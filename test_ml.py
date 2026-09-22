import sklearn
import pandas as pd
import numpy as np

print("scikit-learn version:", sklearn.__version__)
print("pandas version:", pd.__version__)
print("numpy version:", np.__version__)

# quick sanity check - create a tiny dataframe and array
df = pd.DataFrame({"yield_kg_ha": [1200, 1450, 980], "rainfall_mm": [300, 420, 180]})
print("\nSample dataframe:")
print(df)

arr = np.array([1, 2, 3, 4, 5])
print("\nSample numpy array mean:", arr.mean())