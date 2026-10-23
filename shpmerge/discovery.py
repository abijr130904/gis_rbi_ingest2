from __future__ import annotations

import logging
import os
from pathlib import Path

from .models import SourceFile

log = logging.getLogger("shpmerge")

EXCLUDED_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "node_modules",
                 "$recycle.bin", "system volume information"}
TMP_PREFIX = ".shpmerge_tmp_"
REQUIRED_EXTS = (".shx", ".dbf")


def _keep_dir(path: Path, output_dir: Path | None) -> bool:
    name = path.name
    if name.casefold() in EXCLUDED_DIRS or name.startswith(TMP_PREFIX):
        return False
    if output_dir is not None and path.resolve() == output_dir:
        return False
    return True


def find_shapefiles(input_dir: Path, output_dir: Path | None = None) -> list[SourceFile]:
    """Scan rekursif semua .shp (case-insensitive), lewati folder output/venv/temp."""
    root = Path(input_dir).resolve()
    out = Path(output_dir).resolve() if output_dir else None
    results: list[SourceFile] = []
    seen: set[str] = set()
    for cur, dirs, names in os.walk(root):  # os.walk tidak mengikuti symlink
        cur_p = Path(cur)
        dirs[:] = sorted(d for d in dirs if _keep_dir(cur_p / d, out))
        for n in sorted(names):
            if not n.lower().endswith(".shp"):
                continue
            p = cur_p / n
            ident = os.path.normcase(os.path.realpath(p))
            if ident in seen:
                continue
            seen.add(ident)
            rel = p.relative_to(root)
            results.append(SourceFile(
                path=p,
                rel=rel.as_posix(),
                stem=p.stem,
                region_hint=rel.parts[0] if len(rel.parts) > 1 else "",
            ))
    log.info("Ditemukan %d file .shp", len(results))
    return results


def missing_components(shp: Path) -> list[str]:
    stem = shp.stem.lower()
    present = {p.suffix.lower() for p in shp.parent.iterdir()
               if p.is_file() and p.stem.lower() == stem}
    return [e for e in REQUIRED_EXTS if e not in present]


def sibling_size(shp: Path, ext: str) -> int:
    stem = shp.stem.lower()
    for p in shp.parent.iterdir():
        if p.is_file() and p.stem.lower() == stem and p.suffix.lower() == ext:
            return p.stat().st_size
    return 0