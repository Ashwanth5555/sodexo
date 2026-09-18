import pandas as pd
import numpy as np
import sys

PATH = "data/labor_forecasting_poc_synthetic_dataset.xlsx"
pd.set_option("display.max_columns", 50)
pd.set_option("display.width", 200)

xl = pd.ExcelFile(PATH)
sheets = xl.sheet_names

out = []
out.append(f"SHEETS ({len(sheets)}): {sheets}\n")

dfs = {}
for name in sheets:
    df = xl.parse(name)
    dfs[name] = df

for name in sheets:
    df = dfs[name]
    out.append("=" * 100)
    out.append(f"SHEET: {name}  shape={df.shape}")
    out.append("-" * 100)
    out.append("COLUMNS & DTYPES:")
    for c in df.columns:
        out.append(f"  - {c}: {df[c].dtype}")
    out.append("\nSAMPLE (first 5 rows):")
    out.append(df.head(5).to_string())
    out.append("\nNULL COUNTS:")
    nulls = df.isnull().sum()
    out.append(nulls[nulls > 0].to_string() if nulls.sum() > 0 else "  none")
    out.append("\nUNIQUE COUNTS (per column, capped display):")
    for c in df.columns:
        nun = df[c].nunique(dropna=True)
        out.append(f"  - {c}: {nun} unique / {len(df)} rows")
    out.append("")

text = "\n".join(out)
with open("scripts/inspect_output.txt", "w", encoding="utf-8") as f:
    f.write(text)

print("Wrote scripts/inspect_output.txt, length=", len(text))
