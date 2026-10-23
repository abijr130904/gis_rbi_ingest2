import sys
from pathlib import Path

from pyogrio import read_info

d = Path(sys.argv[1])
files = sorted(d.glob("*.shp"))
total = sum(int(read_info(str(p))["features"]) for p in files)
print(len(files), "file .shp,", total, "fitur")    