from __future__ import annotations

import shutil
import os
import logging
import re
import subprocess
import zipfile
from pathlib import Path

from pyogrio import list_layers, read_dataframe, read_info

from .detect import normalize_name, safe_output_name
from .merge import write_frame

log = logging.getLogger("shpmerge")


def extract_gdb(input_gdb: Path | str, output_dir: Path, *, overwrite: bool = False,
                dry_run: bool = False, categories: list[str] | None = None,
                chunk_size: int = 50_000) -> list[tuple[str, int, str]]:
    """Write each spatial layer in one FileGDB to its own FileGDB."""
    input_ref = str(input_gdb)
    input_path = Path(input_gdb) if "/vsi" not in input_ref.casefold() else None
    layers = sorted((str(name), geom) for name, geom in list_layers(input_ref)
                    if geom is not None)
    wanted = {normalize_name(name) for name in (categories or [])}
    if wanted:
        layers = [(name, geom) for name, geom in layers if normalize_name(name) in wanted]

    names: dict[str, str] = {}
    for layer, _ in layers:
        filename = safe_output_name(layer)
        folded = filename.casefold()
        if folded in names:
            raise ValueError(f"nama output bentrok: {names[folded]} dan {layer} -> {filename}.gdb")
        names[folded] = layer

    results = []
    for layer, _ in layers:
        filename = safe_output_name(layer)
        dest = output_dir / f"{filename}.gdb"
        # Running recursively on the same tree should leave already-extracted
        # one-layer GDBs alone, including when input and output roots coincide.
        if (input_path is not None and dest.resolve() == input_path.resolve() and len(layers) == 1
                and normalize_name(layer) == normalize_name(input_path.stem)):
            results.append((layer, int(read_info(str(input_gdb), layer=layer)["features"]),
                            str(dest)))
            continue
        if input_path is not None and dest.resolve() == input_path.resolve():
            raise ValueError(f"nama output {dest.name} sama dengan GDB sumber; sumber tidak akan ditimpa")
        info = read_info(str(input_gdb), layer=layer)
        count = int(info["features"])
        if dest.exists() and not overwrite:
            try:
                output_layers = [(str(n), g) for n, g in list_layers(str(dest)) if g is not None]
                if (len(output_layers) == 1 and output_layers[0][0].casefold() == filename.casefold()
                        and int(read_info(str(dest), layer=output_layers[0][0])["features"]) == count):
                    results.append((layer, count, str(dest)))
                    continue
            except Exception:  # incomplete output from a previously interrupted run
                pass
            raise FileExistsError(f"output sudah ada tetapi belum lengkap: {dest} (gunakan --overwrite untuk memperbaiki)")
        results.append((layer, count, str(dest)))
        if dry_run:
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        if count:
            for start in range(0, count, chunk_size):
                frame = read_dataframe(str(input_gdb), layer=layer,
                                       skip_features=start, max_features=chunk_size)
                write_frame(frame, str(dest), append=start > 0,
                            layer=filename, fmt="gdb")
        else:
            frame = read_dataframe(str(input_gdb), layer=layer)
            write_frame(frame, str(dest), layer=filename, fmt="gdb",
                        geometry_type=info.get("geometry_type"))
    return results


def _archive_gdbs(archive: Path) -> list[tuple[str, str]]:
    """Return (GDAL VSI path, internal GDB path) for GDB folders in an archive."""
    ext = archive.suffix.casefold()
    if ext == ".zip":
        with zipfile.ZipFile(archive) as zf:
            names = [n.replace("\\", "/").strip("/") for n in zf.namelist()]
        prefix = "/vsizip/"
    elif ext == ".rar":
        exe = shutil.which("7z") or shutil.which("7za")
        for candidate in (r"C:\Program Files\7-Zip\7z.exe",
                          r"C:\Program Files (x86)\7-Zip\7z.exe"):
            if not exe and Path(candidate).is_file():
                exe = candidate
        if not exe:
            raise RuntimeError("7-Zip diperlukan untuk membaca daftar isi RAR")
        proc = subprocess.run([exe, "l", "-slt", str(archive)], capture_output=True,
                              text=True, errors="replace")
        if proc.returncode >= 2:
            raise RuntimeError(f"gagal membaca RAR: {proc.stderr.strip() or proc.stdout.strip()}")
        names = [m.group(1).replace("\\", "/").strip("/")
                 for line in proc.stdout.splitlines()
                 if (m := re.match(r"^Path = (.+)$", line)) and m.group(1) not in (str(archive), archive.name)]
        prefix = "/vsirar/"
    else:
        return []

    # A FileGDB is a directory, so infer each candidate from its internal files.
    gdb_dirs = set()
    for name in names:
        parts = name.split("/")
        for i, part in enumerate(parts):
            if part.casefold().endswith(".gdb") and i < len(parts) - 1:
                gdb_dirs.add("/".join(parts[:i + 1]))
    archive_abs = archive.resolve().as_posix()
    return [(f"{prefix}{archive_abs}/{inside}", inside) for inside in sorted(gdb_dirs, key=str.casefold)]


def find_gdbs(root: Path, output_root: Path | None = None) -> list[tuple[Path | str, Path, str]]:
    """Find source GDBs recursively without walking into GDB internals."""
    root = root.resolve()
    if root.suffix.casefold() == ".gdb":
        return [(root, Path(), root.stem)]
    if root.suffix.casefold() in {".zip", ".rar"}:
        return [(ref, Path(root.stem) / Path(internal).parent, Path(internal).stem)
                for ref, internal in _archive_gdbs(root)]
    excluded = output_root.resolve() if output_root and output_root.resolve() != root else None
    found: list[tuple[Path | str, Path, str]] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        kept = []
        for name in dirs:
            child = current_path / name
            if child.suffix.casefold() == ".gdb":
                found.append((child, child.parent.relative_to(root), child.stem))
            elif child.suffix.casefold() in {".zip", ".rar"}:
                try:
                    for ref, internal in _archive_gdbs(child):
                        internal_path = Path(internal)
                        found.append((ref, child.parent.relative_to(root) / child.stem / internal_path.parent,
                                      internal_path.stem))
                except Exception as exc:
                    log.warning("Tidak dapat membaca arsip %s: %s", child, exc)
            elif excluded is None or child.resolve() != excluded:
                kept.append(name)
        dirs[:] = kept
        for name in files:
            archive = current_path / name
            if archive.suffix.casefold() not in {".zip", ".rar"}:
                continue
            try:
                for ref, internal in _archive_gdbs(archive):
                    internal_path = Path(internal)
                    found.append((ref, archive.parent.relative_to(root) / archive.stem / internal_path.parent,
                                  internal_path.stem))
            except Exception as exc:
                log.warning("Tidak dapat membaca arsip %s: %s", archive, exc)
    return sorted(found, key=lambda p: str(p).casefold())


def extract_gdb_tree(input_path: Path, output_root: Path, *, overwrite: bool = False,
                     dry_run: bool = False, categories: list[str] | None = None,
                     chunk_size: int = 50_000) -> list[tuple[str, int, str]]:
    """Extract every GDB found under a directory, preserving its relative folder."""
    sources = find_gdbs(input_path, output_root)
    if not sources:
        raise FileNotFoundError(f"tidak ditemukan file .gdb di: {input_path}")
    root = input_path.resolve()
    results = []
    for source, relative_parent, gdb_name in sources:
        if isinstance(source, Path) and root.suffix.casefold() != ".gdb":
            try:
                list_layers(str(source))
            except Exception as exc:  # skip partial/unreadable outputs found during recursive resume
                log.warning("Lewati GDB yang tidak bisa dibaca %s: %s", source, exc)
                continue
        destination = output_root / relative_parent
        try:
            results.extend(extract_gdb(source, destination, overwrite=overwrite,
                                       dry_run=dry_run, categories=categories,
                                       chunk_size=chunk_size))
        except Exception as exc:
            if isinstance(source, str):
                log.warning("Lewati GDB di arsip %s (%s): %s", gdb_name, source, exc)
            else:
                raise
    if not results and any(isinstance(source, str) for source, _, _ in sources):
        raise RuntimeError(
            "GDB ditemukan di arsip tetapi tidak bisa dibaca oleh GDAL. "
            "Untuk RAR, gunakan build GDAL dengan dukungan libarchive; "
            "periksa juga apakah arsip terenkripsi."
        )
    return results
