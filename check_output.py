import sys
from pathlib import Path

from pyogrio import list_layers, read_info

d = Path(sys.argv[1])
n = total = 0
for p in sorted(d.glob("*.shp")):
    total += int(read_info(str(p))["features"])
    n += 1
for g in sorted(d.glob("*.gdb")):
    for name, gtype in list_layers(str(g)):
        if gtype is None:
            continue
        total += int(read_info(str(g), layer=str(name))["features"])
        n += 1
print(n, "kumpulan data (.shp dan layer .gdb),", total, "fitur")