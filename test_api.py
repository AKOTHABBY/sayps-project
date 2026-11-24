import requests

response = requests.post("http://127.0.0.1:5000/predict", json={
    "farm_id": 1,
    "user_id": 1,
    "acreage": 2.5,
    "rainfall_mm": 320.5,
    "temp_avg_c": 22.3,
    "humidity_pct": 65.0,
    "ph_level": 6.2,
    "moisture_pct": 40.5,
    "fertility_index": 72.0,
    "ndvi_value": 0.65,
    "evi_value": 0.42,
    "crop_type": "Maize",
    "channel": "web"
})

print("Status code:", response.status_code)
print(response.json())