import pandas as pd
import os

df = pd.read_csv("ps4/results_1-300k_ps4.csv")
chunk_size = 100
output_dir = "ps4_results_100row_each"

for i, start in enumerate(range(0, len(df), chunk_size)):
    chunk = df.iloc[start:start + chunk_size]
    chunk.to_csv(os.path.join(output_dir, f"ps4_results_{i}.csv"), index=False)

print(f"Total rows: {len(df)}")
print(f"Files created: {i + 1}")
