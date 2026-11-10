from __future__ import annotations

import logging
import os
from pathlib import Path

from pyogrio import list_layers

from .models import SourceFile

log = logging.getLogger("shpmerge")

EXCLUDED_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "node_modules",
                 "$recycle.bin", "system volume information", "dissolved"}
TMP_PREFIX = ".shpmerge_tmp_"
REQUIRED_EXTS = (".shx", ".dbf")


def _keep_dir(path: Path, output_dir: Path | None) -> bool:
    name = path.name
    if name.casefold() in EXCLUDED_DIRS or name.startswith(TMP_PREFIX):
        return False
    if output_dir is not None and path.resolve() == output_dir:
        return False
    return True


def _add_gdb(p: Path, root: Path, results: list, seen: set) -> None:
    ident = os.path.normcase(os.path.realpath(p))
    if ident in seen:
        return
    seen.add(ident)
    rel = p.relative_to(root)
    region = rel.parts[0] if len(rel.parts) > 1 else ""
    try:
        layers = sorted(str(name) for name, gtype in list_layers(str(p)) if gtype is not None)
    except Exception as exc:  # noqa: BLE001
        results.append(SourceFile(path=p, rel=rel.as_posix(), stem=p.stem, region_hint=region,
                                  error=f"GDB_TIDAK_BISA_DIBACA: {exc}"))
        return
    for layer in layers:
        label = rel.as_posix() if len(layers) == 1 else f"{rel.as_posix()}#{layer}"
        results.append(SourceFile(path=p, rel=label, stem=layer, region_hint=region, layer=layer))


def find_shapefiles(input_dir: Path, output_dir: Path | None = None) -> list[SourceFile]:
    """Scan rekursif semua .shp dan semua layer spasial di folder .gdb (case-insensitive).
    Folder output/venv/temp/dissolved dilewati; isi folder .gdb tidak dipindai sebagai folder biasa."""
    root = Path(input_dir).resolve()
    out = Path(output_dir).resolve() if output_dir else None
    results: list[SourceFile] = []
    seen: set[str] = set()

    if root.suffix.lower() == ".gdb":  # input langsung berupa satu .gdb
        _add_gdb(root, root.parent, results, seen)
        return results

    for cur, dirs, names in os.walk(root):  # os.walk tidak mengikuti symlink
        cur_p = Path(cur)
        keep = []
        for d in sorted(dirs):
            p = cur_p / d
            if not _keep_dir(p, out):
                continue
            if d.lower().endswith(".gdb"):
                _add_gdb(p, root, results, seen)
            else:
                keep.append(d)
        dirs[:] = keep
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
    log.info("Ditemukan %d sumber (.shp / layer .gdb)", len(results))
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