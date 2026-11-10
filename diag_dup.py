import sys

import geopandas as gpd

d = gpd.read_file(sys.argv[1])
d["_k"] = d.geometry.to_wkt()
skip = {"SRC_REGION", "SRC_FILE", "_k", "geometry"}
n = 0
for k, grp in d[d.duplicated("_k", keep=False)].groupby("_k"):
    n += 1
    cols = [c for c in grp.columns if c not in skip and grp[c].astype(str).nunique() > 1]
    print(f"{k} -> {len(grp)} fitur dari: {', '.join(grp['SRC_FILE'])}")
    print("   kolom yang berbeda:", cols if cols else "TIDAK ADA (identik)")
print("titik berkoordinat kembar:", n, "dari", len(d), "fitur")

# //python diag_dup.py "D:\Developer\python_project\gis_rbi_ingest2\merged_output\RBI25K_STASIUNCORS_PT_25K.shp"