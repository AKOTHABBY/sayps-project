import os
import random
from dotenv import load_dotenv
import mysql.connector

load_dotenv()

connection = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME")
)
cursor = connection.cursor()

counties = ["Nakuru", "Trans-Nzoia", "Uasin Gishu", "Kericho"]
crops = ["Maize", "Wheat", "Beans"]

for i in range(300):
    county = random.choice(counties)

    # Create a farmer
    phone_number = f"2547{random.randint(10000000, 99999999)}"
    email = f"farmer{i+1}_{random.randint(1000,9999)}@example.com"

    cursor.execute("""
        INSERT INTO Users (role, full_name, phone_number, email, password_hash, county)
        VALUES ('farmer', %s, %s, %s, 'hashed_pw_placeholder', %s)
    """, (f"Farmer {i+1}", phone_number, email, county))
    user_id = cursor.lastrowid

    # Create their farm
    crop = random.choice(crops)
    acreage = round(random.uniform(0.5, 5.0), 2)
    cursor.execute("""
        INSERT INTO Farms (user_id, farm_name, county, sub_county, latitude, longitude, crop_type, acreage)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (user_id, f"Farm {i+1}", county, "SubCounty", round(random.uniform(-1.5, 1.5), 3),
          round(random.uniform(34.5, 37.5), 3), crop, acreage))
    farm_id = cursor.lastrowid

    # Weather
    rainfall = round(random.uniform(150, 450), 1)
    temp = round(random.uniform(18, 28), 1)
    humidity = round(random.uniform(40, 80), 1)
    cursor.execute("""
        INSERT INTO Weather_Records (farm_id, rainfall_mm, temp_avg_c, humidity_pct, record_date, source)
        VALUES (%s, %s, %s, %s, '2025-03-01', 'KMD')
    """, (farm_id, rainfall, temp, humidity))

    # Soil
    ph = round(random.uniform(5.0, 7.5), 2)
    moisture = round(random.uniform(20, 60), 1)
    fertility = round(random.uniform(40, 90), 1)
    cursor.execute("""
        INSERT INTO Soil_Records (farm_id, ph_level, moisture_pct, fertility_index, record_date)
        VALUES (%s, %s, %s, %s, '2025-03-01')
    """, (farm_id, ph, moisture, fertility))

    # Satellite (NDVI)
    ndvi = round(random.uniform(0.3, 0.8), 3)
    evi = round(random.uniform(0.2, 0.6), 3)
    cursor.execute("""
        INSERT INTO Satellite_Data (farm_id, ndvi_value, evi_value, capture_date, image_source)
        VALUES (%s, %s, %s, '2025-03-01', 'Sentinel-2')
    """, (farm_id, ndvi, evi))

    # Yield - loosely correlated with rainfall + fertility + ndvi so the model has a real pattern to learn
    base_yield = (rainfall * 2.5) + (fertility * 8) + (ndvi * 900)
    actual_yield = round(base_yield + random.uniform(-150, 150), 1)
    cursor.execute("""
        INSERT INTO Yield_Records (farm_id, season, actual_yield_kg_ha, record_date)
        VALUES (%s, 'Long Rains 2025', %s, '2025-07-15')
    """, (farm_id, actual_yield))

connection.commit()
print("Inserted 50 farms with full seasonal records successfully!")

cursor.close()
connection.close()