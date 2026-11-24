import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import joblib
import pandas as pd
from flask import Flask, request, jsonify

load_dotenv()

app = Flask(__name__)

model = joblib.load("yield_model.pkl")
feature_columns = joblib.load("feature_columns.pkl")

db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
db_host = os.getenv("DB_HOST")
db_name = os.getenv("DB_NAME")
engine = create_engine(f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}")


@app.route('/')
def home():
    return "SAYPS Prediction API is running"


@app.route('/predict', methods=['POST'])
def predict():
    data = request.get_json()

    required_fields = [
        "farm_id", "acreage", "rainfall_mm", "temp_avg_c", "humidity_pct",
        "ph_level", "moisture_pct", "fertility_index",
        "ndvi_value", "evi_value", "crop_type"
    ]
    missing = [f for f in required_fields if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {missing}"}), 400

    row = {
        "acreage": data["acreage"],
        "rainfall_mm": data["rainfall_mm"],
        "temp_avg_c": data["temp_avg_c"],
        "humidity_pct": data["humidity_pct"],
        "ph_level": data["ph_level"],
        "moisture_pct": data["moisture_pct"],
        "fertility_index": data["fertility_index"],
        "ndvi_value": data["ndvi_value"],
        "evi_value": data["evi_value"],
        "crop_type_Maize": 1 if data["crop_type"] == "Maize" else 0,
        "crop_type_Beans": 1 if data["crop_type"] == "Beans" else 0,
        "crop_type_Wheat": 1 if data["crop_type"] == "Wheat" else 0,
    }

    input_df = pd.DataFrame([row])
    input_df = input_df.reindex(columns=feature_columns, fill_value=0)

    prediction = round(float(model.predict(input_df)[0]), 2)
    channel = data.get("channel", "web")  # defaults to 'web' if not specified

    with engine.connect() as conn:
        # Get (or create) the active model version record
        model_row = conn.execute(text(
            "SELECT model_version_id FROM Model_Versions WHERE is_active = TRUE LIMIT 1"
        )).fetchone()

        if model_row:
            model_version_id = model_row.model_version_id
        else:
            result = conn.execute(text("""
                INSERT INTO Model_Versions (algorithm_name, trained_on_date, is_active)
                VALUES ('RandomForest', CURDATE(), TRUE)
            """))
            conn.commit()
            model_version_id = result.lastrowid

        # Save the prediction itself
        pred_result = conn.execute(text("""
            INSERT INTO Predictions (farm_id, model_version_id, predicted_yield_kg_ha, recommendation_text)
            VALUES (:farm_id, :model_version_id, :predicted_yield, :recommendation)
        """), {
            "farm_id": data["farm_id"],
            "model_version_id": model_version_id,
            "predicted_yield": prediction,
            "recommendation": "Maintain current soil fertility practices for optimal yield."
        })
        conn.commit()
        prediction_id = pred_result.lastrowid

        # Log the request for auditability
        conn.execute(text("""
            INSERT INTO Prediction_Logs (prediction_id, user_id, channel, status)
            VALUES (:prediction_id, :user_id, :channel, 'success')
        """), {
            "prediction_id": prediction_id,
            "user_id": data.get("user_id"),
            "channel": channel
        })
        conn.commit()

    return jsonify({
        "predicted_yield_kg_ha": prediction,
        "prediction_id": prediction_id
    })


if __name__ == '__main__':
    app.run(debug=True)