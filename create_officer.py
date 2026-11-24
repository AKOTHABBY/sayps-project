import os
import bcrypt
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

engine = create_engine(
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
)

hashed = bcrypt.hashpw("Officer123!".encode("utf-8"), bcrypt.gensalt())

with engine.connect() as conn:
    conn.execute(text("""
        INSERT INTO Users (role, full_name, phone_number, email, password_hash, county)
        VALUES ('officer', 'Nakuru Officer', '254700000001', 'officer@sayps.com', :pw, 'Nakuru')
    """), {"pw": hashed.decode("utf-8")})
    conn.commit()

print("Officer created: officer@sayps.com / Officer123!  (region: Nakuru)")