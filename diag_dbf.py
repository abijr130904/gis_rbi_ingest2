import struct
import sys
from pathlib import Path


def fields(dbf):
    b = Path(dbf).read_bytes()
    header_len = struct.unpack("<H", b[8:10])[0]
    out = []
    for off in range(32, header_len - 1, 32):
        d = b[off:off + 32]
        if d[0] == 0x0D:
            break
        out.append((d[:11].split(b"\0")[0].decode("latin1"), chr(d[11]), d[16], d[17]))
    return out


for p in sys.argv[1:]:
    print(p)
    for f in fields(p):
        print("  ", f)  # (nama, tipe, lebar, desimal)