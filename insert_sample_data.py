import os
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

# Insert a sample user (farmer)
cursor.execute("""
    INSERT INTO Users (role, full_name, phone_number, email, password_hash, county)
    VALUES ('farmer', 'John Kamau', '254712345678', 'john@example.com', 'hashed_pw_placeholder', 'Nakuru')
""")
user_id = cursor.lastrowid

# Insert a sample farm for that user
cursor.execute("""
    INSERT INTO Farms (user_id, farm_name, county, sub_county, latitude, longitude, crop_type, acreage)
    VALUES (%s, 'Kamau Maize Farm', 'Nakuru', 'Njoro', -0.333, 36.066, 'Maize', 2.5)
""", (user_id,))
farm_id = cursor.lastrowid

# Insert sample weather, soil, satellite, and yield records for that farm
cursor.execute("""
    INSERT INTO Weather_Records (farm_id, rainfall_mm, temp_avg_c, humidity_pct, record_date, source)
    VALUES (%s, 320.5, 22.3, 65.0, '2025-03-01', 'KMD')
""", (farm_id,))

cursor.execute("""
    INSERT INTO Soil_Records (farm_id, ph_level, moisture_pct, fertility_index, record_date)
    VALUES (%s, 6.2, 40.5, 72.0, '2025-03-01')
""", (farm_id,))

cursor.execute("""
    INSERT INTO Satellite_Data (farm_id, ndvi_value, evi_value, capture_date, image_source)
    VALUES (%s, 0.65, 0.42, '2025-03-01', 'Sentinel-2')
""", (farm_id,))

cursor.execute("""
    INSERT INTO Yield_Records (farm_id, season, actual_yield_kg_ha, record_date)
    VALUES (%s, 'Long Rains 2025', 1450.0, '2025-07-15')
""", (farm_id,))

connection.commit()
print(f"Sample data inserted successfully! Farm ID: {farm_id}")

cursor.close()
connection.close()