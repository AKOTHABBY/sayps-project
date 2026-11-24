import os
import bcrypt
import joblib
import secrets
import requests
import urllib3
import hashlib
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, timedelta
from backup import make_backup, BACKUP_DIR
from functools import wraps
from dotenv import load_dotenv  
from sqlalchemy import create_engine, text
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, session , flash , Response , got_request_exception, send_from_directory

load_dotenv()

app = Flask(__name__)
app.secret_key = "change-this-to-something-random-later"

db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
db_host = os.getenv("DB_HOST")
db_name = os.getenv("DB_NAME")
engine = create_engine(f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}")


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def log_event(event_type, actor, details):
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO System_Logs (event_type, actor, details) VALUES (:t, :a, :d)"
            ), {"t": event_type, "a": (actor or "")[:100], "d": (details or "")[:300]})
    except Exception as e:
        print("Could not write log:", e)


def _log_exception(sender, exception, **extra):
    log_event("error", session.get("full_name", "anonymous"), f"{request.path}: {exception}")


got_request_exception.connect(_log_exception, app)


def send_sms(phone_number, message):
    to = phone_number if phone_number.startswith("+") else "+" + phone_number
    try:
        r = requests.post(
            "https://api.sandbox.africastalking.com/version1/messaging",
            headers={
                "apiKey": os.getenv("AT_API_KEY"),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"username": os.getenv("AT_USERNAME"), "to": to, "message": message},
            timeout=10, verify=False,
        )
        print("SMS sent (manual request):", r.status_code, r.text)
        return r.status_code in (200, 201)
    except Exception as e:
        print("SMS failed:", e)
        return False

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('must_change'):
            return redirect(url_for('change_password'))
        if session.get('role') not in ('admin', 'officer'):
            return "Access denied: dashboard is for officers and admins only.", 403
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('must_change'):
            return redirect(url_for('change_password'))
        if session.get('role') != 'admin':
            return "Access denied: administrators only.", 403
        return view(*args, **kwargs)
    return wrapper


def read_farm_data(sql, extra=None):
    """Run a query. Officers only get farms in their own county; admins get everything."""
    if session.get('role') == 'officer':
        clause = " AND f.county = :county"
        params = {"county": session.get('county')}
    else:
        clause = ""
        params = {}
    params.update(extra or {})
    return pd.read_sql(text(sql.replace("{region}", clause)), engine, params=params)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT user_id, full_name, role, county, password_hash, status, must_change_password
                FROM Users WHERE email = :email AND role IN ('admin', 'officer')
            """), {"email": email}).fetchone()

        if result and bcrypt.checkpw(password.encode('utf-8'), result.password_hash.encode('utf-8')):
            if result.status == 'inactive':
                log_event('login_blocked', email, 'Inactive account tried to log in')
                return render_template('login.html', error="Account inactive. Contact your administrator.")
            session['user_id'] = result.user_id
            session['full_name'] = result.full_name
            session['role'] = result.role
            session['county'] = result.county
            session['must_change'] = bool(result.must_change_password)
            log_event('login', email, f"{result.role} logged in")
            if session['must_change']:
                return redirect(url_for('change_password'))
            return redirect(url_for('dashboard'))
        log_event('login_failed', email, 'Wrong email or password')
        return render_template('login.html', error="Invalid email or password")

    return render_template('login.html')


@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_event('logout', session.get('full_name'), 'Logged out')
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    df = read_farm_data("""
        SELECT f.farm_name, f.county, f.crop_type, f.acreage, y.actual_yield_kg_ha
        FROM Farms f
        JOIN Yield_Records y ON f.farm_id = y.farm_id
        WHERE 1=1 {region}
    """)

    total_farms = len(df)
    avg_yield = round(df["actual_yield_kg_ha"].mean(), 1)
    total_counties = df["county"].nunique()
    total_crops = df["crop_type"].nunique()

    crop_avg = df.groupby("crop_type")["actual_yield_kg_ha"].mean().round(1)
    max_val = crop_avg.max()
    crop_chart_data = [(label, value, max_val) for label, value in crop_avg.items()]

    return render_template(
        "dashboard.html",
        total_farms=total_farms,
        avg_yield=avg_yield,
        total_counties=total_counties,
        total_crops=total_crops,
        crop_chart_data=crop_chart_data,
        farms=df.to_dict(orient="records"),
        user_name=session.get('full_name')
    )


@app.route('/predictions')
@login_required
def predictions():
    df = read_farm_data("""
        SELECT p.prediction_id, f.farm_name, f.crop_type,
               p.predicted_yield_kg_ha, l.channel, p.created_at
        FROM Predictions p
        JOIN Farms f ON p.farm_id = f.farm_id
        LEFT JOIN Prediction_Logs l ON p.prediction_id = l.prediction_id
        WHERE 1=1 {region}
        ORDER BY p.created_at DESC
    """)
    return render_template("predictions.html", predictions=df.to_dict(orient="records"))


@app.route('/farms')
@login_required
def farms():
    q = request.args.get('q', '').strip()
    crop = request.args.get('crop', '')
    since = request.args.get('since', '')

    extra_sql = ""
    extra = {}
    if q:
        extra_sql += " AND (u.full_name LIKE :q OR u.phone_number LIKE :q OR f.sub_county LIKE :q)"
        extra["q"] = f"%{q}%"
    if crop:
        extra_sql += " AND f.crop_type = :crop"
        extra["crop"] = crop
    if since:
        extra_sql += " AND f.registered_at >= :since"
        extra["since"] = since

    df = read_farm_data(f"""
        SELECT f.farm_id, f.farm_name, f.county, f.sub_county,
               f.crop_type, f.acreage, u.full_name, u.phone_number
        FROM Farms f
        JOIN Users u ON f.user_id = u.user_id
        WHERE 1=1 {{region}} {extra_sql}
        ORDER BY f.farm_id DESC
    """, extra)

    return render_template(
        "farms.html", farms=df.to_dict(orient="records"),
        q=q, crop=crop, since=since, crops=CROPS
    )

def get_threshold():
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT setting_value FROM Settings WHERE setting_key = 'alert_threshold'"
        )).fetchone()
    return float(row[0]) if row else 1500.0


def sync_alerts():
    """Create an open alert for any farm below the threshold that has no alert yet."""
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO Alerts (farm_id, yield_value)
            SELECT f.farm_id, MIN(y.actual_yield_kg_ha)
            FROM Farms f JOIN Yield_Records y ON f.farm_id = y.farm_id
            WHERE NOT EXISTS (SELECT 1 FROM Alerts a WHERE a.farm_id = f.farm_id)
            GROUP BY f.farm_id
            HAVING MIN(y.actual_yield_kg_ha) < :t
        """), {"t": get_threshold()})


@app.route('/alerts')
@login_required
def alerts():
    sync_alerts()
    send_pending_alert_sms()
    df = read_farm_data("""
        SELECT a.alert_id, f.farm_name, f.county, f.crop_type, u.full_name,
               u.phone_number, a.yield_value, a.created_at, a.sms_sent
        FROM Alerts a
        JOIN Farms f ON a.farm_id = f.farm_id
        JOIN Users u ON f.user_id = u.user_id
        WHERE a.status = 'open' {region}
        ORDER BY a.yield_value ASC
    """)
    return render_template("alerts.html", alerts=df.to_dict(orient="records"),
                           total=len(df), threshold=get_threshold())


@app.route('/alerts/history')
@login_required
def alerts_history():
    df = read_farm_data("""
        SELECT a.alert_id, f.farm_name, f.county, f.crop_type, u.full_name,
               a.yield_value, a.created_at, a.resolved_at
        FROM Alerts a
        JOIN Farms f ON a.farm_id = f.farm_id
        JOIN Users u ON f.user_id = u.user_id
        WHERE a.status = 'resolved' {region}
        ORDER BY a.resolved_at DESC
    """)
    return render_template("alerts_history.html", alerts=df.to_dict(orient="records"))


@app.route('/alerts/<int:alert_id>/resolve', methods=['POST'])
@login_required
def resolve_alert(alert_id):
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT f.county FROM Alerts a JOIN Farms f ON a.farm_id = f.farm_id
            WHERE a.alert_id = :id
        """), {"id": alert_id}).fetchone()
        if row is None:
            return "Alert not found.", 404
        if session.get('role') == 'officer' and row.county != session.get('county'):
            return "Access denied: this alert is outside your region.", 403
        conn.execute(text(
            "UPDATE Alerts SET status = 'resolved', resolved_at = NOW() WHERE alert_id = :id"
        ), {"id": alert_id})
    flash("Alert marked as resolved.")
    return redirect(url_for('alerts'))

def report_filters():
    return {k: request.args.get(k, '') for k in ('crop', 'county', 'start', 'end')}


def report_queries(f):
    sql_extra, params = "", {}
    if f['crop']:
        sql_extra += " AND f.crop_type = :crop"
        params['crop'] = f['crop']
    if f['county'] and session.get('role') == 'admin':
        sql_extra += " AND f.county = :cty"
        params['cty'] = f['county']
    date_extra, date_params = "", {}
    if f['start']:
        date_extra += " AND y.record_date >= :start"
        date_params['start'] = f['start']
    if f['end']:
        date_extra += " AND y.record_date <= :end"
        date_params['end'] = f['end']
    return sql_extra, params, date_extra, date_params


def report_yield_df(f):
    sql_extra, params, date_extra, date_params = report_queries(f)
    return read_farm_data(f"""
        SELECT f.farm_name, f.county, f.crop_type, y.season, y.record_date, y.actual_yield_kg_ha
        FROM Farms f JOIN Yield_Records y ON f.farm_id = y.farm_id
        WHERE 1=1 {{region}} {sql_extra} {date_extra}
    """, {**params, **date_params})


@app.route('/reports')
@login_required
def reports():
    f = report_filters()
    df = report_yield_df(f)
    threshold = get_threshold()
    df["is_low"] = df["actual_yield_kg_ha"] < threshold

    def summarise(col):
        return df.groupby(col).agg(
            farms=("actual_yield_kg_ha", "count"),
            avg_yield=("actual_yield_kg_ha", "mean"),
            low_count=("is_low", "sum")
        ).round(1).reset_index()

    by_county = summarise("county")
    by_crop = summarise("crop_type")

    sql_extra, params, _, _ = report_queries(f)
    pred = read_farm_data(f"""
        SELECT f.county, AVG(p.predicted_yield_kg_ha) AS avg_pred
        FROM Predictions p JOIN Farms f ON p.farm_id = f.farm_id
        WHERE 1=1 {{region}} {sql_extra}
        GROUP BY f.county
    """, params)
    pred["avg_pred"] = pred["avg_pred"].astype(float).round(1)
    by_county = by_county.merge(pred, on="county", how="left")
    by_county = by_county.astype(object).where(pd.notnull(by_county), None)

    return render_template(
        "reports.html",
        by_county=by_county.to_dict(orient="records"),
        by_crop=by_crop.to_dict(orient="records"),
        f=f, crops=CROPS, counties=get_counties(),
        is_admin=session.get('role') == 'admin',
        threshold=threshold, total=len(df),
        qs=request.query_string.decode()
    )


@app.route('/reports/export.csv')
@login_required
def reports_export():
    df = report_yield_df(report_filters())
    return Response(
        df.to_csv(index=False), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sayps_yield_report.csv"}
    )


@app.route('/reports/activity')
@login_required
def reports_activity():
    if session.get('role') != 'admin':
        return "Access denied: administrators only.", 403
    df = pd.read_sql(text("""
        SELECT u.full_name, u.role, l.channel, COUNT(*) AS predictions,
               MAX(l.requested_at) AS last_active
        FROM Prediction_Logs l JOIN Users u ON l.user_id = u.user_id
        GROUP BY u.user_id, u.full_name, u.role, l.channel
        ORDER BY predictions DESC
    """), engine)
    return render_template("activity.html", rows=df.to_dict(orient="records"))

def normalize_phone(raw):
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    if digits.startswith("254") and len(digits) == 12:
        return digits
    return None


COUNTIES = ["Nakuru", "Trans-Nzoia", "Uasin Gishu", "Kericho"]
CROPS = ["Maize", "Beans", "Wheat"]
SOILS = ["Loam", "Clay", "Sandy", "Silt"]

model = joblib.load("yield_model.pkl")
feature_columns = joblib.load("feature_columns.pkl")

KG_PER_BAG = 90
ACRES_PER_HECTARE = 2.471

INPUT_FIELDS = [
    ("rainfall_mm", "Rainfall (mm)"), ("temp_avg_c", "Average temperature (°C)"),
    ("humidity_pct", "Humidity (%)"), ("ph_level", "Soil pH"),
    ("moisture_pct", "Soil moisture (%)"), ("fertility_index", "Fertility index"),
    ("ndvi_value", "NDVI"), ("evi_value", "EVI"),
]


def bags_per_acre(kg_ha):
    return round(float(kg_ha) / ACRES_PER_HECTARE / KG_PER_BAG, 1)


def recommendation_for(kg_ha):
    if kg_ha < 1500:
        return "Yield expected below average. Test your soil, consider fertiliser, and talk to your extension officer."
    if kg_ha < 1800:
        return "Moderate yield expected. Apply top-dressing on time and weed early."
    return "Good growing conditions expected. Ensure timely weeding."


def default_inputs(conn, farm_id):
    """Latest records for the farm. If a farm has none yet, use averages from all farms."""
    sources = [
        ("Weather_Records", "record_date", ["rainfall_mm", "temp_avg_c", "humidity_pct"]),
        ("Soil_Records", "record_date", ["ph_level", "moisture_pct", "fertility_index"]),
        ("Satellite_Data", "capture_date", ["ndvi_value", "evi_value"]),
    ]
    values, used_defaults = {}, False
    for table, date_col, cols in sources:
        row = conn.execute(text(
            f"SELECT {', '.join(cols)} FROM {table} WHERE farm_id = :id ORDER BY {date_col} DESC LIMIT 1"
        ), {"id": farm_id}).fetchone()
        if row is None:
            avg_cols = ", ".join(f"AVG({c}) AS {c}" for c in cols)
            row = conn.execute(text(f"SELECT {avg_cols} FROM {table}")).fetchone()
            used_defaults = True
        for c in cols:
            values[c] = round(float(row._mapping[c]), 2)
    return values, used_defaults


def run_prediction(farm, inputs):
    row = {"acreage": float(farm.acreage), **inputs}
    for crop in CROPS:
        row[f"crop_type_{crop}"] = 1 if farm.crop_type == crop else 0
    df = pd.DataFrame([row]).reindex(columns=feature_columns, fill_value=0)
    return round(float(model.predict(df)[0]), 1)

@app.route('/farms/add', methods=['GET', 'POST'])
@login_required
def add_farm():
    error = None
    is_officer = session.get('role') == 'officer'

    if request.method == 'POST':
        f = request.form
        phone = normalize_phone(f['phone'])
        county = session['county'] if is_officer else f['county']

        if phone is None:
            error = "Enter a valid phone number, for example 0712345678."
        else:
            try:
                with engine.begin() as conn:
                    exists = conn.execute(
                        text("SELECT user_id FROM Users WHERE phone_number = :p"),
                        {"p": phone}
                    ).fetchone()
                    if exists:
                        raise ValueError("A farmer with this phone number is already registered.")

                    res = conn.execute(text("""
                        INSERT INTO Users (role, full_name, phone_number, password_hash, county)
                        VALUES ('farmer', :name, :phone, 'no-login', :county)
                    """), {"name": f['farmer_name'], "phone": phone, "county": county})
                    user_id = res.lastrowid

                    conn.execute(text("""
                        INSERT INTO Farms (user_id, farm_name, county, sub_county, latitude,
                                           longitude, crop_type, acreage, soil_type)
                        VALUES (:uid, :fname, :county, :sub, :lat, :lng, :crop, :acres, :soil)
                    """), {
                        "uid": user_id, "fname": f['farm_name'], "county": county,
                        "sub": f['sub_county'], "lat": f['latitude'], "lng": f['longitude'],
                        "crop": f['crop_type'], "acres": f['acreage'], "soil": f['soil_type']
                    })
                flash("Farm added successfully.")
                return redirect(url_for('farms'))
            except ValueError as e:
                error = str(e)

    return render_template(
        "farm_form.html", error=error, counties=COUNTIES, crops=CROPS, soils=SOILS,
        form=request.form, is_officer=is_officer, officer_county=session.get('county')
    )


@app.route('/farms/<int:farm_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_farm(farm_id):
    is_officer = session.get('role') == 'officer'

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT f.farm_id, f.user_id, f.farm_name, f.county, f.sub_county,
                   f.latitude, f.longitude, f.crop_type, f.acreage, f.soil_type,
                   u.full_name, u.phone_number
            FROM Farms f JOIN Users u ON f.user_id = u.user_id
            WHERE f.farm_id = :id
        """), {"id": farm_id}).fetchone()

    if row is None:
        return "Farm not found.", 404
    if is_officer and row.county != session.get('county'):
        return "Access denied: this farm is outside your region.", 403

    error = None
    form = {
        "farmer_name": row.full_name, "phone": row.phone_number,
        "farm_name": row.farm_name, "county": row.county,
        "sub_county": row.sub_county, "crop_type": row.crop_type,
        "latitude": row.latitude, "longitude": row.longitude,
        "soil_type": row.soil_type, "acreage": row.acreage,
    }

    if request.method == 'POST':
        f = request.form
        form = f
        phone = normalize_phone(f['phone'])
        county = row.county if is_officer else f['county']

        if phone is None:
            error = "Enter a valid phone number, for example 0712345678."
        else:
            try:
                with engine.begin() as conn:
                    clash = conn.execute(text(
                        "SELECT user_id FROM Users WHERE phone_number = :p AND user_id <> :uid"
                    ), {"p": phone, "uid": row.user_id}).fetchone()
                    if clash:
                        raise ValueError("Another farmer already uses this phone number.")

                    conn.execute(text("""
                        UPDATE Users SET full_name = :name, phone_number = :phone, county = :county
                        WHERE user_id = :uid
                    """), {"name": f['farmer_name'], "phone": phone, "county": county, "uid": row.user_id})

                    conn.execute(text("""
                        UPDATE Farms SET farm_name = :fname, county = :county, sub_county = :sub,
                               latitude = :lat, longitude = :lng, crop_type = :crop,
                               acreage = :acres, soil_type = :soil
                        WHERE farm_id = :fid
                    """), {
                        "fname": f['farm_name'], "county": county, "sub": f['sub_county'],
                        "lat": f['latitude'], "lng": f['longitude'], "crop": f['crop_type'],
                        "acres": f['acreage'], "soil": f['soil_type'], "fid": farm_id
                    })
                flash("Farm updated successfully.")
                return redirect(url_for('farms'))
            except ValueError as e:
                error = str(e)

    return render_template(
        "farm_form.html", error=error, counties=COUNTIES, crops=CROPS, soils=SOILS,
        form=form, is_officer=is_officer, officer_county=session.get('county'),
        page_title="Edit farm"
    )

@app.route('/farms/<int:farm_id>', methods=['GET', 'POST'])
@login_required
def farm_detail(farm_id):
    with engine.connect() as conn:
        farm = conn.execute(text("""
            SELECT f.farm_id, f.user_id, f.farm_name, f.county, f.sub_county, f.crop_type,
                   f.acreage, f.soil_type, u.full_name, u.phone_number
            FROM Farms f JOIN Users u ON f.user_id = u.user_id
            WHERE f.farm_id = :id
        """), {"id": farm_id}).fetchone()
        if farm is None:
            return "Farm not found.", 404
        if session.get('role') == 'officer' and farm.county != session.get('county'):
            return "Access denied: this farm is outside your region.", 403
        defaults, used_defaults = default_inputs(conn, farm_id)

    if request.method == 'POST':
        try:
            inputs = {key: float(request.form[key]) for key, _ in INPUT_FIELDS}
        except (ValueError, KeyError):
            flash("Please fill every input with a number.")
            return redirect(url_for('farm_detail', farm_id=farm_id))

        prediction = run_prediction(farm, inputs)
        with engine.begin() as conn:
            mv = conn.execute(text(
                "SELECT model_version_id FROM Model_Versions WHERE is_active = TRUE LIMIT 1"
            )).fetchone()
            if mv:
                model_version_id = mv.model_version_id
            else:
                model_version_id = conn.execute(text("""
                    INSERT INTO Model_Versions (algorithm_name, trained_on_date, is_active)
                    VALUES ('RandomForest', CURDATE(), TRUE)
                """)).lastrowid

            pid = conn.execute(text("""
                INSERT INTO Predictions (farm_id, model_version_id, predicted_yield_kg_ha, recommendation_text)
                VALUES (:fid, :mv, :y, :rec)
            """), {"fid": farm_id, "mv": model_version_id, "y": prediction,
                   "rec": recommendation_for(prediction)}).lastrowid

            conn.execute(text("""
                INSERT INTO Prediction_Logs (prediction_id, user_id, channel, status)
                VALUES (:pid, :uid, 'web', 'success')
            """), {"pid": pid, "uid": session['user_id']})
        return redirect(url_for('farm_detail', farm_id=farm_id, new=pid))

    history = pd.read_sql(text("""
        SELECT p.prediction_id, p.predicted_yield_kg_ha, p.recommendation_text,
               p.created_at, p.suspect, l.channel
        FROM Predictions p
        LEFT JOIN Prediction_Logs l ON p.prediction_id = l.prediction_id
        WHERE p.farm_id = :fid
        ORDER BY p.created_at DESC
    """), engine, params={"fid": farm_id}).to_dict(orient="records")
    for h in history:
        h["bags"] = bags_per_acre(h["predicted_yield_kg_ha"])

    new_id = request.args.get('new', type=int)
    latest = next((h for h in history if h["prediction_id"] == new_id), None)

    return render_template(
        "farm_detail.html", farm=farm, defaults=defaults, used_defaults=used_defaults,
        fields=INPUT_FIELDS, history=history, latest=latest
    )


@app.route('/predictions/<int:prediction_id>/suspect', methods=['POST'])
@login_required
def report_suspect(prediction_id):
    note = request.form.get('note', '').strip()
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT p.farm_id, f.county
            FROM Predictions p JOIN Farms f ON p.farm_id = f.farm_id
            WHERE p.prediction_id = :id
        """), {"id": prediction_id}).fetchone()
        if row is None:
            return "Prediction not found.", 404
        if session.get('role') == 'officer' and row.county != session.get('county'):
            return "Access denied: this prediction is outside your region.", 403
        conn.execute(text(
            "UPDATE Predictions SET suspect = TRUE, suspect_note = :note WHERE prediction_id = :id"
        ), {"note": note, "id": prediction_id})
    flash("Thank you. The prediction has been reported for model review.")
    return redirect(url_for('farm_detail', farm_id=row.farm_id))

@app.route('/farms/<int:farm_id>/link-phone', methods=['POST'])
@login_required
def link_phone(farm_id):
    phone = normalize_phone(request.form.get('phone', ''))
    with engine.begin() as conn:
        farm = conn.execute(text(
            "SELECT user_id, county FROM Farms WHERE farm_id = :id"
        ), {"id": farm_id}).fetchone()
        if farm is None:
            return "Farm not found.", 404
        if session.get('role') == 'officer' and farm.county != session.get('county'):
            return "Access denied: this farm is outside your region.", 403

        if phone is None:
            flash("Enter a valid phone number, for example 0712345678.")
        else:
            clash = conn.execute(text(
                "SELECT user_id FROM Users WHERE phone_number = :p AND user_id <> :uid"
            ), {"p": phone, "uid": farm.user_id}).fetchone()
            if clash:
                flash("Another farmer already uses this phone number.")
            else:
                conn.execute(text(
                    "UPDATE Users SET phone_number = :p WHERE user_id = :uid"
                ), {"p": phone, "uid": farm.user_id})
                flash("Phone number linked. The farmer can now use the USSD channel.")
    return redirect(url_for('farm_detail', farm_id=farm_id))

def make_temp_password():
    return secrets.token_urlsafe(8)


def get_setting(key, default):
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT setting_value FROM Settings WHERE setting_key = :k"
        ), {"k": key}).fetchone()
    return row[0] if row else default


def set_setting(conn, key, value):
    conn.execute(text("""
        INSERT INTO Settings (setting_key, setting_value) VALUES (:k, :v)
        ON DUPLICATE KEY UPDATE setting_value = :v
    """), {"k": key, "v": value})


def get_counties():
    items = [c.strip() for c in get_setting('regions', '').split(',') if c.strip()]
    return items or COUNTIES


@app.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    error = None
    if request.method == 'POST':
        new, confirm = request.form['new_password'], request.form['confirm_password']
        if len(new) < 8:
            error = "Password must be at least 8 characters."
        elif new != confirm:
            error = "The two passwords do not match."
        else:
            hashed = bcrypt.hashpw(new.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE Users SET password_hash = :h, must_change_password = FALSE WHERE user_id = :id"
                ), {"h": hashed, "id": session['user_id']})
            session['must_change'] = False
            flash("Password updated.")
            return redirect(url_for('dashboard'))
    return render_template('change_password.html', error=error, forced=session.get('must_change'))


@app.route('/users')
@admin_required
def users():
    q = request.args.get('q', '').strip()
    extra, params = "", {}
    if q:
        extra = " AND (full_name LIKE :q OR county LIKE :q OR email LIKE :q)"
        params["q"] = f"%{q}%"
    df = pd.read_sql(text(f"""
        SELECT user_id, full_name, email, phone_number, role, county, status
        FROM Users WHERE role IN ('admin', 'officer') {extra} ORDER BY user_id
    """), engine, params=params)
    return render_template("users.html", users=df.to_dict(orient="records"), q=q,
                           me=session['user_id'])


@app.route('/users/add', methods=['GET', 'POST'])
@admin_required
def add_user():
    error = None
    if request.method == 'POST':
        f = request.form
        phone = normalize_phone(f['phone'])
        if phone is None:
            error = "Enter a valid phone number, for example 0712345678."
        else:
            temp = make_temp_password()
            hashed = bcrypt.hashpw(temp.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            try:
                with engine.begin() as conn:
                    dup = conn.execute(text(
                        "SELECT user_id FROM Users WHERE email = :e OR phone_number = :p"
                    ), {"e": f['email'], "p": phone}).fetchone()
                    if dup:
                        raise ValueError("A user with this email or phone number already exists.")
                    conn.execute(text("""
                        INSERT INTO Users (role, full_name, phone_number, email, password_hash,
                                           county, status, must_change_password)
                        VALUES (:role, :name, :phone, :email, :pw, :county, 'active', TRUE)
                 """), {"role": f['role'], "name": f['full_name'], "phone": phone,
                           "email": f['email'], "pw": hashed, "county": f['county']})
                    mail_body = (f"Hello {f['full_name']},\n\nYour SAYPS account has been created.\n"
                             f"Email: {f['email']}\nTemporary password: {temp}\n\n"
                             f"Log in at {BASE_URL} and you will be asked to choose your own password.")
                if send_email(f['email'], "Your SAYPS account", mail_body):
                    flash(f"User created. A temporary password was emailed to {f['email']}.")
                else:
                    flash(f"User created, but the email could not be sent. Temporary password: {temp}  (shown once, give it to the user)")

                return redirect(url_for('users'))
            except ValueError as e:
                error = str(e)
    return render_template("user_form.html", error=error, form=request.form,
                           counties=get_counties(), editing=False)


@app.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    with engine.connect() as conn:
        u = conn.execute(text("""
            SELECT user_id, full_name, email, phone_number, role, county, status
            FROM Users WHERE user_id = :id AND role IN ('admin', 'officer')
        """), {"id": user_id}).fetchone()
    if u is None:
        return "User not found.", 404

    error = None
    form = {"full_name": u.full_name, "email": u.email, "phone": u.phone_number,
            "role": u.role, "county": u.county, "status": u.status}

    if request.method == 'POST':
        f = request.form
        form = {**form, **f}
        if user_id == session['user_id'] and f['status'] == 'inactive':
            error = "You cannot deactivate your own account."
        elif user_id == session['user_id'] and f['role'] != 'admin':
            error = "You cannot remove your own administrator role."
        else:
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE Users SET full_name = :n, county = :c, role = :r, status = :s
                    WHERE user_id = :id
                """), {"n": f['full_name'], "c": f['county'], "r": f['role'],
                       "s": f['status'], "id": user_id})
            flash("User updated.")
            return redirect(url_for('users'))

    return render_template("user_form.html", error=error, form=form,
                           counties=get_counties(), editing=True, user_id=user_id)


@app.route('/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def reset_user_password(user_id):
    with engine.connect() as conn:
        u = conn.execute(text(
            "SELECT email, full_name FROM Users WHERE user_id = :id AND role IN ('admin', 'officer')"
        ), {"id": user_id}).fetchone()
    if u is None:
        return "User not found.", 404
    temp = make_temp_password()
    hashed = bcrypt.hashpw(temp.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE Users SET password_hash = :h, must_change_password = TRUE WHERE user_id = :id"
        ), {"h": hashed, "id": user_id})
    mail_body = (f"Hello {u.full_name},\n\nYour SAYPS password was reset by an administrator.\n"
                 f"Temporary password: {temp}\n\nYou will be asked to choose a new one when you log in.")
    if send_email(u.email, "Your SAYPS password was reset", mail_body):
        flash(f"Password reset. A temporary password was emailed to {u.email}.")
    else:
        flash(f"Password reset. The email could not be sent. New temporary password: {temp}  (shown once, give it to the user)")
    return redirect(url_for('users'))


@app.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    if request.method == 'POST':
        try:
            threshold = float(request.form['alert_threshold'])
            if threshold <= 0:
                raise ValueError
        except ValueError:
            flash("Alert threshold must be a positive number.")
            return redirect(url_for('settings'))
        regions = ",".join(c.strip() for c in request.form['regions'].split(',') if c.strip())
        with engine.begin() as conn:
            set_setting(conn, 'alert_threshold', str(int(threshold)) if threshold == int(threshold) else str(threshold))
            if regions:
                set_setting(conn, 'regions', regions)
        flash("Settings saved.")
        return redirect(url_for('settings'))
    return render_template("settings.html", threshold=int(get_threshold()),
                           regions=", ".join(get_counties()), crops=CROPS)


def alert_sms_text(farm_name, crop, kg_ha):
    return (f"SAYPS Alert - {farm_name}: a low yield is expected for your {crop} crop "
            f"(about {bags_per_acre(kg_ha)} bags/acre). Please contact your extension officer.")


def send_pending_alert_sms(limit=10):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT a.alert_id, a.yield_value, f.farm_name, f.crop_type, u.phone_number
            FROM Alerts a
            JOIN Farms f ON a.farm_id = f.farm_id
            JOIN Users u ON f.user_id = u.user_id
            WHERE a.sms_sent = FALSE AND a.status = 'open'
            LIMIT :n
        """), {"n": limit}).fetchall()
    for r in rows:
        if send_sms(r.phone_number, alert_sms_text(r.farm_name, r.crop_type, r.yield_value)):
            with engine.begin() as conn:
                conn.execute(text("UPDATE Alerts SET sms_sent = TRUE WHERE alert_id = :id"),
                             {"id": r.alert_id})


@app.route('/alerts/<int:alert_id>/sms', methods=['POST'])
@login_required
def alert_sms(alert_id):
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT a.yield_value, f.farm_name, f.crop_type, f.county, u.phone_number
            FROM Alerts a
            JOIN Farms f ON a.farm_id = f.farm_id
            JOIN Users u ON f.user_id = u.user_id
            WHERE a.alert_id = :id
        """), {"id": alert_id}).fetchone()
    if r is None:
        return "Alert not found.", 404
    if session.get('role') == 'officer' and r.county != session.get('county'):
        return "Access denied: this alert is outside your region.", 403
    if send_sms(r.phone_number, alert_sms_text(r.farm_name, r.crop_type, r.yield_value)):
        with engine.begin() as conn:
            conn.execute(text("UPDATE Alerts SET sms_sent = TRUE WHERE alert_id = :id"), {"id": alert_id})
        flash("SMS sent to the farmer.")
    else:
        flash("The SMS could not be sent. Check the app terminal for the reason.")
    return redirect(url_for('alerts'))


@app.route('/system/logs')
@admin_required
def system_logs():
    kind = request.args.get('type', '')
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM System_Logs WHERE created_at < NOW() - INTERVAL 90 DAY"))
    df = pd.read_sql(text("""
        SELECT created_at, event_type, actor, details FROM (
            SELECT created_at, event_type, actor, details FROM System_Logs
            UNION ALL
            SELECT l.requested_at AS created_at, 'prediction' AS event_type,
                   COALESCE(u.full_name, 'unknown') AS actor,
                   CONCAT('Channel: ', l.channel, ' | Prediction #', l.prediction_id) AS details
            FROM Prediction_Logs l LEFT JOIN Users u ON l.user_id = u.user_id
        ) AS all_logs
        WHERE created_at >= NOW() - INTERVAL 90 DAY
          AND (:kind = '' OR event_type = :kind)
        ORDER BY created_at DESC LIMIT 300
    """), engine, params={"kind": kind})
    return render_template("logs.html", rows=df.to_dict(orient="records"), kind=kind)


def list_backups():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    items = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if name.endswith(".sql"):
            full = os.path.join(BACKUP_DIR, name)
            items.append({
                "name": name,
                "size_kb": round(os.path.getsize(full) / 1024, 1),
                "when": datetime.fromtimestamp(os.path.getmtime(full)),
            })
    return items


@app.route('/system/backups')
@admin_required
def system_backups():
    files = list_backups()
    last = files[0]["when"] if files else None
    stale = last is None or datetime.now() - last > timedelta(hours=24)
    return render_template("backups.html", files=files, last=last, stale=stale)


@app.route('/system/backups/run', methods=['POST'])
@admin_required
def run_backup():
    try:
        name = make_backup()
        log_event('backup', session.get('full_name'), f"Manual backup {name}")
        flash(f"Backup created: {name}")
    except Exception as e:
        flash(f"Backup failed: {e}")
    return redirect(url_for('system_backups'))


@app.route('/system/backups/<name>')
@admin_required
def download_backup(name):
    if not name.endswith(".sql") or "/" in name or "\\" in name:
        return "Not found.", 404
    return send_from_directory(BACKUP_DIR, name, as_attachment=True)



BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5001")


def send_email(to_addr, subject, body):
    host, user, password = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    if not (host and user and password):
        print("EMAIL NOT CONFIGURED. Would have sent to", to_addr, "->", subject)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.getenv("MAIL_FROM", user)
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, password)
            s.send_message(msg)
        return True
    except Exception as e:
        print("Email failed:", e)
        log_event('email_failed', to_addr, str(e))
        return False


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    sent = False
    if request.method == 'POST':
        email = request.form['email'].strip()
        with engine.connect() as conn:
            u = conn.execute(text("""
                SELECT user_id, full_name FROM Users
                WHERE email = :e AND role IN ('admin', 'officer') AND status = 'active'
            """), {"e": email}).fetchone()
        if u:
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO Password_Resets (user_id, token_hash, expires_at)
                    VALUES (:u, :h, NOW() + INTERVAL 1 HOUR)
                """), {"u": u.user_id, "h": token_hash})
            link = f"{BASE_URL}/reset/{token}"
            body = (f"Hello {u.full_name},\n\nUse this link to set a new SAYPS password "
                    f"(valid for 1 hour):\n{link}\n\nIf you did not ask for this, ignore this email.")
            if not send_email(email, "SAYPS password reset", body):
                print("RESET LINK (email not sent):", link)
            log_event('password_reset_requested', email, 'Reset link requested')
        sent = True   # same message whether or not the email exists
    return render_template('forgot_password.html', sent=sent)


@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT reset_id, user_id FROM Password_Resets
            WHERE token_hash = :h AND used = FALSE AND expires_at > NOW()
        """), {"h": token_hash}).fetchone()
    if row is None:
        return render_template('reset_password.html', invalid=True, error=None)

    error = None
    if request.method == 'POST':
        new, confirm = request.form['new_password'], request.form['confirm_password']
        if len(new) < 8:
            error = "Password must be at least 8 characters."
        elif new != confirm:
            error = "The two passwords do not match."
        else:
            hashed = bcrypt.hashpw(new.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE Users SET password_hash = :h, must_change_password = FALSE WHERE user_id = :id"
                ), {"h": hashed, "id": row.user_id})
                conn.execute(text("UPDATE Password_Resets SET used = TRUE WHERE reset_id = :id"),
                             {"id": row.reset_id})
            log_event('password_reset', str(row.user_id), 'Password changed with reset link')
            return render_template('login.html', notice="Password updated. Please log in.")
    return render_template('reset_password.html', invalid=False, error=error)


if __name__ == '__main__':
    app.run(debug=True, port=5001)