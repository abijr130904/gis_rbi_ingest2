"""Uji apakah pyogrio/GDAL di venv ini bisa menulis File Geodatabase (OpenFileGDB), termasuk append."""
import shutil
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from shapely.geometry import Point

print("pyogrio:", pyogrio.__version__, "| GDAL:", pyogrio.__gdal_version_string__)
mode = str(pyogrio.list_drivers().get("OpenFileGDB", "tidak ada"))
print("driver OpenFileGDB:", mode, "(butuh 'rw')")
if "w" not in mode:
    sys.exit("GAGAL: GDAL ini tidak bisa menulis File Geodatabase. Coba: pip install -U pyogrio")


def frame(start: int) -> gpd.GeoDataFrame:
    n = 3
    return gpd.GeoDataFrame(
        {
            "ID": pd.Series(np.arange(start, start + n), dtype="int32"),
            "NAMA": [f"n{start + i}" for i in range(n)],
            "NILAI": np.array([0.5, 1.5, 2.5]),
            "TGL": pd.to_datetime(["2020-01-01", "2021-05-06", None]).astype("datetime64[ms]"),
        },
        geometry=[Point(i, i) for i in range(n)],
        crs="EPSG:4326",
    )


tmp = Path(tempfile.mkdtemp())
ok = True
try:
    dest = str(tmp / "uji.gdb")
    opts = {"CREATE_SHAPE_AREA_AND_LENGTH_FIELDS": "NO"}
    pyogrio.write_dataframe(frame(0), dest, layer="UJI", driver="OpenFileGDB", layer_options=opts)
    pyogrio.write_dataframe(frame(3), dest, layer="UJI", driver="OpenFileGDB", append=True)
    info = pyogrio.read_info(dest, layer="UJI")
    print("fitur:", info["features"], "(harus 6)")
    print("kolom:", list(info["fields"]), "(harus ID, NAMA, NILAI, TGL)")
    print("tipe :", list(info["dtypes"]))
    df = pyogrio.read_dataframe(dest, layer="UJI")
    print(df.drop(columns="geometry").to_string())
    ok = (info["features"] == 6 and list(info["fields"]) == ["ID", "NAMA", "NILAI", "TGL"]
          and int(df["TGL"].notna().sum()) == 4)
except Exception as exc:  # noqa: BLE001
    ok = False
    print("ERROR:", type(exc).__name__, exc)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("HASIL:", "OK, penulisan .gdb didukung" if ok else "GAGAL, kirimkan seluruh output ini")
sys.exit(0 if ok else 1)