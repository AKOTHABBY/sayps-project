import joblib
import pandas as pd
from api import app as api_app
from app import app as dashboard_app


def test_api_home_runs():
    client = api_app.test_client()
    response = client.get("/")
    assert response.status_code == 200


def test_predict_missing_field_returns_400():
    client = api_app.test_client()
    response = client.post("/predict", json={"acreage": 2.5})
    assert response.status_code == 400


def test_model_returns_a_number():
    model = joblib.load("yield_model.pkl")
    columns = joblib.load("feature_columns.pkl")
    row = {c: 0 for c in columns}
    row["acreage"] = 2.5
    row["rainfall_mm"] = 300
    result = model.predict(pd.DataFrame([row]))[0]
    assert isinstance(float(result), float)


def test_dashboard_redirects_to_login_when_not_logged_in():
    client = dashboard_app.test_client()
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    def test_login_with_wrong_password_is_rejected():
    client = dashboard_app.test_client()
    response = client.post("/login", data={
        "email": "admin@sayps.com",
        "password": "wrong-password"
    })
    assert b"Invalid email or password" in response.data


def test_predictions_page_requires_login():
    client = dashboard_app.test_client()
    response = client.get("/predictions")
    assert response.status_code == 302