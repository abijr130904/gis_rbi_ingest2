import sys
from pathlib import Path

import pandas as pd
from pyogrio import read_dataframe

rows = []
for shp in sorted(Path(sys.argv[1]).rglob("*.shp")):
    df = read_dataframe(str(shp), read_geometry=False)
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            rows.append((shp.stem, c, int(df[c].notna().sum()), len(df)))

filled = [r for r in rows if r[2] > 0]
print("kolom tanggal ditemukan:", len(rows), "| yang terisi (>0):", len(filled))
for r in filled[:20]:
    print(r)