import os
import bcrypt
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
db_host = os.getenv("DB_HOST")
db_name = os.getenv("DB_NAME")
engine = create_engine(f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}")

plain_password = "Admin123!"
hashed = bcrypt.hashpw(plain_password.encode('utf-8'), bcrypt.gensalt())

with engine.connect() as conn:
    conn.execute(text("""
        INSERT INTO Users (role, full_name, phone_number, email, password_hash, county)
        VALUES ('admin', 'System Admin', '254700000000', 'admin@sayps.com', :pw, 'Nakuru')
    """), {"pw": hashed.decode('utf-8')})
    conn.commit()

print("Admin user created. Email: admin@sayps.com | Password: Admin123!")