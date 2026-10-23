import struct
import sys
import tempfile
from pathlib import Path

import numpy as np
from pyogrio import read_dataframe, read_info
from pyogrio.raw import write


def dbf_fields(dbf):
    b = Path(dbf).read_bytes()
    header_len = struct.unpack("<H", b[8:10])[0]
    out = []
    for off in range(32, header_len - 1, 32):
        d = b[off:off + 32]
        if d[0] == 0x0D:
            break
        out.append((d[:11].split(b"\0")[0].decode("latin1"), chr(d[11]), d[16], d[17]))
    return out


src, out = sys.argv[1], sys.argv[2]
a = read_dataframe(src)
b = read_dataframe(out, read_geometry=False)

for col in ("AQDATE", "PUDATE"):
    print(col, "| dtype sumber:", a[col].dtype, "| terisi di sumber:", int(a[col].notna().sum()), "dari", len(a))
    print("   contoh sumber:", a[col].dropna().head(2).tolist())
    print("   contoh output:", b[col].dropna().head(2).tolist())

# percobaan: tulis ulang sebagai tipe Date lewat API tingkat rendah
tmp = Path(tempfile.mkdtemp()) / "t.shp"
info = read_info(src)
gname = a.geometry.name
fields = [c for c in a.columns if c != gname]
data = []
for c in fields:
    s = a[c]
    data.append(s.to_numpy().astype("datetime64[D]") if np.issubdtype(s.dtype, np.datetime64) else s.to_numpy())
try:
    write(str(tmp), a.geometry.to_wkb().to_numpy(), data, fields, driver="ESRI Shapefile",
          geometry_type=info["geometry_type"], crs=info["crs"], encoding="UTF-8")
    print("tipe kolom hasil raw.write:", [f for f in dbf_fields(tmp.with_suffix(".dbf")) if f[0] in ("AQDATE", "PUDATE")])
    print("   contoh dibaca ulang:", read_dataframe(str(tmp), read_geometry=False)["AQDATE"].dropna().head(2).tolist())
except Exception as e:
    print("raw.write gagal:", type(e).__name__, e)
    