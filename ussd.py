import os
from datetime import datetime
from types import SimpleNamespace

import requests
import urllib3
from dotenv import load_dotenv
from flask import Flask, request
from sqlalchemy import text

from app import engine, CROPS, bags_per_acre, recommendation_for, default_inputs, run_prediction

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ussd_app = Flask(__name__)

SUPPORT_PHONE = "0700 XXX XXX"   # replace with the real support number

MAIN_MENU = (
    "Welcome to SAYPS\n"
    "1. Get Yield Estimate\n"
    "2. Farming Recommendations\n"
    "3. My Farm Details\n"
    "4. Help\n"
    "0. Exit"
)
HELP_TEXT = f"SAYPS estimates your crop yield. Dial, press 1, confirm your crop and enter your planting date. Help: {SUPPORT_PHONE}"


def send_sms(phone_number, message):
    try:
        response = requests.post(
            "https://api.sandbox.africastalking.com/version1/messaging",
            headers={
                "apiKey": os.getenv("AT_API_KEY"),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"username": os.getenv("AT_USERNAME"), "to": phone_number, "message": message},
            timeout=10,
            verify=False,
        )
        print("SMS sent (manual request):", response.status_code, response.text)
    except Exception as e:
        print("SMS failed:", e)


def find_farm(phone):
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT f.farm_id, f.user_id, f.farm_name, f.county, f.crop_type, f.acreage
            FROM Farms f JOIN Users u ON f.user_id = u.user_id
            WHERE REPLACE(REPLACE(u.phone_number, '+', ''), ' ', '') = REPLACE(REPLACE(:phone, '+', ''), ' ', '')
            ORDER BY f.farm_id DESC LIMIT 1
        """), {"phone": phone}).fetchone()


def last_prediction(farm_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT predicted_yield_kg_ha FROM Predictions WHERE farm_id = :id ORDER BY created_at DESC LIMIT 1"
        ), {"id": farm_id}).fetchone()


def save_prediction(farm, prediction):
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
        """), {"fid": farm.farm_id, "mv": model_version_id, "y": prediction,
               "rec": recommendation_for(prediction)}).lastrowid

        conn.execute(text("""
            INSERT INTO Prediction_Logs (prediction_id, user_id, channel, status)
            VALUES (:pid, :uid, 'ussd', 'success')
        """), {"pid": pid, "uid": farm.user_id})


def date_ok(value):
    if len(value) != 8 or not value.isdigit():
        return False
    try:
        datetime.strptime(value, "%d%m%Y")
        return True
    except ValueError:
        return False


@ussd_app.route('/ussd', methods=['POST'])
def ussd():
    phone = request.values.get("phoneNumber", "")
    raw = request.values.get("text", "")
    parts = raw.split("*") if raw else []

    # Find the first valid main-menu choice; ignore any wrong entries before it
    choice, rest = None, []
    for i, part in enumerate(parts):
        if part in ("0", "1", "2", "3", "4"):
            choice, rest = part, parts[i + 1:]
            break

    if choice is None:
        prefix = "Invalid option.\n" if parts else ""
        return "CON " + prefix + MAIN_MENU
    if choice == "0":
        return "END Thank you for using SAYPS."
    if choice == "4":
        return "END " + HELP_TEXT

    farm = find_farm(phone)
    if farm is None:
        return "END No registered farm found for this number. Ask your extension officer to register you."

    if choice == "3":
        last = last_prediction(farm.farm_id)
        last_line = f"\nLast estimate: {bags_per_acre(last[0])} bags/acre" if last else "\nNo estimate yet."
        return (f"END Farm: {farm.farm_name}\nCrop: {farm.crop_type}\n"
                f"County: {farm.county}\nAcres: {farm.acreage}{last_line}")

    if choice == "2":
        last = last_prediction(farm.farm_id)
        if last is None:
            return "END Get a yield estimate first (option 1), then check here for advice."
        return "END " + recommendation_for(float(last[0]))

    # choice == "1": confirm crop -> planting date -> result
    state, crop, error = "confirm", farm.crop_type, False
    for part in rest:
        error = False
        if state == "confirm":
            if part == "1":
                state = "date"
            elif part == "2":
                state = "choose"
            else:
                error = True
        elif state == "choose":
            if part in ("1", "2", "3"):
                crop, state = CROPS[int(part) - 1], "date"
            else:
                error = True
        elif state == "date":
            if date_ok(part):
                state = "done"
                break
            error = True

    err = "Invalid entry. " if error else ""
    if state == "confirm":
        return f"CON {err}Is your crop {farm.crop_type}?\n1. Yes\n2. Change crop"
    if state == "choose":
        return f"CON {err}Choose crop:\n1. Maize\n2. Beans\n3. Wheat"
    if state == "date":
        return f"CON {err}Enter planting date as DDMMYYYY (e.g. 15032026)"

    with engine.connect() as conn:
        inputs, _ = default_inputs(conn, farm.farm_id)
    prediction = run_prediction(SimpleNamespace(acreage=farm.acreage, crop_type=crop), inputs)
    save_prediction(farm, prediction)

    bags = bags_per_acre(prediction)
    rec = recommendation_for(prediction)
    sms = (f"SAYPS Result - {farm.farm_name}\nCrop: {crop}\nEstimated yield: {bags} bags/acre\n"
           f"Note: {rec}\nFor help call: {SUPPORT_PHONE}")
    send_sms(phone, sms)
    return f"END Estimated yield: {bags} bags/acre.\n{rec}\nAn SMS has been sent."


if __name__ == '__main__':
    ussd_app.run(debug=True, port=5002)