import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
import pandas as pd
import numpy as np
import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

load_dotenv()

# Build a SQLAlchemy engine instead of a raw mysql.connector connection
db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
db_host = os.getenv("DB_HOST")
db_name = os.getenv("DB_NAME")

engine = create_engine(f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}")

query = """
    SELECT 
        f.farm_id, f.crop_type, f.acreage,
        w.rainfall_mm, w.temp_avg_c, w.humidity_pct,
        s.ph_level, s.moisture_pct, s.fertility_index,
        sat.ndvi_value, sat.evi_value,
        y.actual_yield_kg_ha
    FROM Farms f
    JOIN Weather_Records w ON f.farm_id = w.farm_id
    JOIN Soil_Records s ON f.farm_id = s.farm_id
    JOIN Satellite_Data sat ON f.farm_id = sat.farm_id
    JOIN Yield_Records y ON f.farm_id = y.farm_id
"""
df = pd.read_sql(query, engine)

print(f"Loaded {len(df)} rows.\n")
print(df.head())

# --- Feature engineering ---
# crop_type is categorical - one-hot encode it
df_encoded = pd.get_dummies(df, columns=["crop_type"])

feature_cols = [c for c in df_encoded.columns if c not in ("farm_id", "actual_yield_kg_ha")]
X = df_encoded[feature_cols]
joblib.dump(feature_cols, "feature_columns.pkl")
y = df_encoded["actual_yield_kg_ha"]

# --- Train/test split ---
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

# --- Train Random Forest ---
rf_model = RandomForestRegressor(n_estimators=200, random_state=42)
rf_model.fit(X_train, y_train)
rf_preds = rf_model.predict(X_test)

# --- Train baseline Linear Regression for comparison ---
lr_model = LinearRegression()
lr_model.fit(X_train, y_train)
lr_preds = lr_model.predict(X_test)

def evaluate(name, y_true, y_pred):
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mae = mean_absolute_error(y_true, y_pred)
    r2 = r2_score(y_true, y_pred)
    print(f"\n{name}:")
    print(f"  RMSE: {rmse:.2f}")
    print(f"  MAE:  {mae:.2f}")
    print(f"  R²:   {r2:.4f}")
    return rmse, mae, r2

print("\n--- Model Evaluation (Test Set) ---")
evaluate("Random Forest", y_test, rf_preds)
evaluate("Linear Regression", y_test, lr_preds)

# --- 5-fold cross-validation on Random Forest (matches Test Plan Section 3.2.3.1) ---
print("\n--- 5-Fold Cross-Validation (Random Forest) ---")
cv_scores = cross_val_score(rf_model, X, y, cv=5, scoring="r2")
print(f"R² scores per fold: {cv_scores}")
print(f"Mean R²: {cv_scores.mean():.4f}")
joblib.dump(rf_model, "yield_model.pkl")
print("\nModel saved to yield_model.pkl")