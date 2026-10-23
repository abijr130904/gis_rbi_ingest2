from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from pyogrio import read_dataframe, read_info, write_dataframe

from .discovery import TMP_PREFIX
from .models import SOURCE_FIELDS, Group, Options, SourceFile
from .validate import crs_equal

log = logging.getLogger("shpmerge")

SHP_EXTS = {".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx"}


class MergeError(RuntimeError):
    pass


def existing_outputs(out_dir: Path, name: str) -> list[Path]:
    if not out_dir.exists():
        return []
    return [p for p in out_dir.iterdir()
            if p.is_file() and p.stem.lower() == name.lower() and p.suffix.lower() in SHP_EXTS]


def _cast(s: pd.Series, fam: str) -> pd.Series:
    if fam == "int":
        num = pd.to_numeric(s)
        return num.astype("int64") if not num.isna().any() else num.astype("Int64")
    if fam == "float":
        return pd.to_numeric(s).astype("float64")
    return s


def _prepare(gdf, sf: SourceFile, g: Group, o: Options):
    if o.target_crs and sf.crs and not crs_equal(sf.crs, o.target_crs):
        gdf = gdf.to_crs(o.target_crs)
    geom = gdf.geometry.name
    for name, fam in g.schema:
        if name not in gdf.columns:
            gdf[name] = None
        gdf[name] = _cast(gdf[name], fam)
    cols = [n for n, _ in g.schema]
    if o.add_source_fields:
        gdf["SRC_REGION"] = sf.region_hint[:254]
        gdf["SRC_FILE"] = sf.rel[:254]
        cols += list(SOURCE_FIELDS)
    return gdf[cols + [geom]]


def _read_kw(o: Options) -> dict:
    return {"encoding": o.source_encoding} if o.source_encoding else {}


def write_merged(g: Group, o: Options, dest: Path) -> int:
    first, written, invalid = True, 0, 0
    for sf in g.files:
        n = sf.features
        for start in range(0, n, o.chunk_size):
            gdf = read_dataframe(str(sf.path), skip_features=start,
                                 max_features=o.chunk_size, **_read_kw(o))
            gdf = _prepare(gdf, sf, g, o)
            invalid += int((gdf.geometry.notna() & ~gdf.geometry.is_valid).sum())
            write_dataframe(gdf, str(dest), driver="ESRI Shapefile",
                            append=not first, encoding="UTF-8")
            first = False
            written += len(gdf)
            log.info("  %s: %d/%d fitur (total tertulis %d)", sf.rel, min(start + o.chunk_size, n), n, written)
    if invalid:
        g.warnings.append(f"{invalid} geometri invalid ditemukan (TIDAK diperbaiki/dihapus)")
    return written


FLOAT_ATOL = 1e-15  # DBF output menulis 15 desimal -> galat pembulatan <= ~1e-15


def _is_num(a: pd.Series, b: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b)


def _tol(a: pd.Series, b: pd.Series) -> float:
    return FLOAT_ATOL if (a.dtype.kind == "f" or b.dtype.kind == "f") else 0.0


def _same(a: pd.Series, b: pd.Series, stats: dict | None = None, col: str = "") -> bool:
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    if _is_num(a, b):
        af, bf = a.astype(float).to_numpy(), b.astype(float).to_numpy()
        ok = np.isclose(af, bf, rtol=1e-12, atol=_tol(a, b), equal_nan=True)
        if stats is not None and _tol(a, b) > 0:
            diff = np.abs(af - bf)
            diff = diff[~np.isnan(diff)]
            stats[col] = max(stats.get(col, 0.0), float(diff.max()) if diff.size else 0.0)
        return bool(ok.all())
    both_na = a.isna() & b.isna()
    return bool(((a.astype(object) == b.astype(object)) | both_na).all())


def _describe_diff(a: pd.Series, b: pd.Series) -> str:
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    if _is_num(a, b):
        af, bf = a.astype(float).to_numpy(), b.astype(float).to_numpy()
        bad = ~np.isclose(af, bf, rtol=1e-12, atol=_tol(a, b), equal_nan=True)
        i = int(np.argmax(bad))
        return (f"{int(bad.sum())} nilai beda; contoh baris {i}: sumber={af[i]!r}, "
                f"output={bf[i]!r}, selisih={abs(af[i] - bf[i]):.3g}")
    ao, bo = a.astype(object), b.astype(object)
    bad = ~((ao == bo) | (a.isna() & b.isna())).to_numpy()
    i = int(np.argmax(bad))
    return (f"{int(bad.sum())} nilai beda; contoh baris {i}: "
            f"sumber={ao[i]!r} ({type(ao[i]).__name__}), output={bo[i]!r} ({type(bo[i]).__name__})")


def verify_output(g: Group, o: Options, dest: Path) -> dict[str, float]:
    """Baca ulang output & bandingkan atribut dengan sumber (urutan fitur dipertahankan).
    Mengembalikan selisih numerik maksimum per kolom desimal."""
    info = read_info(str(dest))
    if int(info["features"]) != g.total_features:
        raise MergeError(f"jumlah fitur output {info['features']} != {g.total_features}")
    expected = [n for n, _ in g.schema] + (list(SOURCE_FIELDS) if o.add_source_fields else [])
    if [str(x) for x in info["fields"]] != expected:
        raise MergeError("daftar kolom output tidak sesuai rencana")
    stats: dict[str, float] = {}
    pos = 0
    for sf in g.files:
        for start in range(0, sf.features, o.chunk_size):
            src = read_dataframe(str(sf.path), skip_features=start, max_features=o.chunk_size,
                                 read_geometry=False, **_read_kw(o))
            out = read_dataframe(str(dest), skip_features=pos + start,
                                 max_features=len(src), read_geometry=False)
            for col in src.columns:
                if not _same(src[col], out[col], stats, col):
                    raise MergeError(
                        f"verifikasi gagal: kolom {col} pada {sf.rel} (mulai fitur {start}): "
                        + _describe_diff(src[col], out[col]))
        pos += sf.features
    return stats


def finalize(tmp: Path, out_dir: Path, name: str, overwrite: bool) -> None:
    new = [p for p in tmp.iterdir() if p.is_file() and p.stem.lower() == name.lower()]
    old = existing_outputs(out_dir, name)
    if old and not overwrite:
        raise FileExistsError(f"output sudah ada: {name} (gunakan --overwrite)")
    backup = tmp / "_backup"
    backup.mkdir()
    moved, placed = [], []
    try:
        for p in old:
            os.replace(p, backup / p.name)
            moved.append(p)
        for p in new:
            os.replace(p, out_dir / p.name)
            placed.append(out_dir / p.name)
    except Exception:
        for p in placed:
            p.unlink(missing_ok=True)
        for p in moved:
            os.replace(backup / p.name, p)
        raise


def _new_tmp(out_dir: Path) -> Path:
    tmp = out_dir / f"{TMP_PREFIX}{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True)
    return tmp


def merge_group(g: Group, o: Options, out_dir: Path) -> None:
    tmp = _new_tmp(out_dir)
    try:
        tmp_shp = tmp / f"{g.out_name}.shp"
        written = write_merged(g, o, tmp_shp)
        if written != g.total_features:
            raise MergeError(f"fitur tertulis {written} != {g.total_features}")
        if o.verify:
            stats = verify_output(g, o, tmp_shp)
            for col, d in sorted(stats.items()):
                if d > 0:
                    g.warnings.append(
                        f"PRESISI: kolom {col} selisih numerik maks {d:.3g} terhadap sumber "
                        "(DBF output menyimpan 15 desimal)")
        finalize(tmp, out_dir, g.out_name, o.overwrite)
        final = out_dir / f"{g.out_name}.shp"
        if not final.exists() or int(read_info(str(final))["features"]) != g.total_features:
            raise MergeError(f"output akhir tidak ditemukan atau jumlah fitur tidak sesuai: {final}")
        g.status = "MERGED"
        g.output = str(final)
        g.output_features = written
        log.info("Output akhir terverifikasi: %s", final)
    except Exception as exc:  # noqa: BLE001
        g.status = "FAILED"
        g.reasons.append(f"{type(exc).__name__}: {exc}")
        log.error("Gagal merge %s: %s", g.key, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def copy_single(g: Group, o: Options, out_dir: Path) -> None:
    sf = g.files[0]
    tmp = _new_tmp(out_dir)
    try:
        for p in sf.path.parent.iterdir():
            if p.is_file() and p.stem.lower() == sf.stem.lower() and p.suffix.lower() in SHP_EXTS:
                shutil.copy2(p, tmp / f"{g.out_name}{p.suffix.lower()}")
        finalize(tmp, out_dir, g.out_name, o.overwrite)
        final = out_dir / f"{g.out_name}.shp"
        if not final.exists() or int(read_info(str(final))["features"]) != sf.features:
            raise MergeError(f"salinan akhir tidak ditemukan atau jumlah fitur tidak sesuai: {final}")
        g.output = str(final)
        g.output_features = sf.features
        g.warnings.append("SINGLE_SOURCE disalin apa adanya (tanpa field sumber)")
    except Exception as exc:  # noqa: BLE001
        g.status = "FAILED"
        g.reasons.append(f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)