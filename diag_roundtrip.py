import struct
import sys
import tempfile
from pathlib import Path

import numpy as np
from pyogrio import read_dataframe, write_dataframe


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


src, col = sys.argv[1], sys.argv[2]
df = read_dataframe(src)
tmp = Path(tempfile.mkdtemp()) / "t.shp"
write_dataframe(df, str(tmp), driver="ESRI Shapefile", encoding="UTF-8")
out = read_dataframe(str(tmp))

a = df[col].astype(float).to_numpy()
b = out[col].astype(float).to_numpy()
bad = ~np.isclose(a, b, rtol=1e-12, atol=0, equal_nan=True)
print("dtype sumber:", df[col].dtype, "| dtype output:", out[col].dtype)
print("nilai beda:", int(bad.sum()), "dari", len(a))
for i in np.flatnonzero(bad)[:5]:
    print(" baris", i, "sumber=", repr(a[i]), "output=", repr(b[i]))
print("spesifikasi DBF output:", [f for f in dbf_fields(tmp.with_suffix(".dbf")) if f[0] == col])