from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from pyogrio import read_dataframe, read_info, write_dataframe
from pyogrio.raw import write as raw_write
from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon

from .discovery import TMP_PREFIX
from .models import SOURCE_FIELDS, Group, Options, SourceFile
from .validate import crs_equal, family, src_kw

log = logging.getLogger("shpmerge")

SHP_EXTS = {".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx"}
GDB_RESERVED = {"OBJECTID", "SHAPE", "SHAPE_AREA", "SHAPE_LENGTH"}  # nama sistem File Geodatabase


class MergeError(RuntimeError):
    pass


# ---------------------------------------------------------------- format keluaran
def out_ext(o: Options) -> str:
    return ".gdb" if o.is_gdb else ".shp"


def layer_of(g: Group, o: Options) -> str | None:
    return g.out_name if o.is_gdb else None


def _layer_kw(layer: str | None) -> dict:
    return {"layer": layer} if layer else {}


def out_col(name: str, o: Options) -> str:
    """Nama kolom di output: kolom bernama cadangan sistem GDB diberi akhiran _SRC."""
    if o.is_gdb and name.upper() in GDB_RESERVED:
        return name + "_SRC"
    return name


def expected_columns(g: Group, o: Options) -> list[str]:
    cols = list(output_column_map(g, o).values())
    if o.add_source_fields:
        cols += list(SOURCE_FIELDS)
    return cols


def output_column_map(g: Group, o: Options) -> dict[str, str]:
    """Map source schema names to unique output names (GDB names are case-insensitive)."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for name, _ in g.schema:
        base = out_col(name, o)
        candidate, suffix = base, 2
        while candidate.casefold() in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        mapping[name] = candidate
        used.add(candidate.casefold())
    return mapping


def _features(path: Path, layer: str | None) -> int:
    return int(read_info(str(path), **_layer_kw(layer))["features"])


def existing_outputs(out_dir: Path, name: str, fmt: str = "shp") -> list[Path]:
    if not out_dir.exists():
        return []
    if fmt == "gdb":
        return [p for p in out_dir.iterdir() if p.is_dir() and p.name.lower() == f"{name}.gdb".lower()]
    return [p for p in out_dir.iterdir()
            if p.is_file() and p.stem.lower() == name.lower() and p.suffix.lower() in SHP_EXTS]


# ---------------------------------------------------------------- persiapan data
def _cast(s: pd.Series, fam: str, width: int = 64) -> pd.Series:
    if fam == "int":
        num = pd.to_numeric(s)
        has_na = bool(num.isna().any())
        if width <= 32:
            return num.astype("Int32") if has_na else num.astype("int32")
        return num.astype("Int64") if has_na else num.astype("int64")
    if fam == "float":
        return pd.to_numeric(s).astype("float64")
    if fam == "date":
        return pd.to_datetime(s)
    if fam == "text":
        out = s.astype(object).copy()
        valid = s.notna()
        out.loc[valid] = s.loc[valid].map(
            lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value))
        out.loc[~valid] = None
        return out
    return s


def _prepare(gdf, sf: SourceFile, g: Group, o: Options):
    if o.target_crs and sf.crs and not crs_equal(sf.crs, o.target_crs):
        gdf = gdf.to_crs(o.target_crs)
    if g.drop_z:
        gdf = gdf.set_geometry(gdf.geometry.force_2d())
    geom = gdf.geometry.name
    aliases = g.field_aliases.get(sf.rel, {})
    source_names = {n.casefold(): n for n in gdf.columns if n != geom}
    for name, fam in g.schema:
        source_name = aliases.get(name, source_names.get(name.casefold(), name))
        if source_name not in gdf.columns:
            gdf[name] = None
        elif source_name != name:
            gdf[name] = gdf[source_name]
        gdf[name] = _cast(gdf[name], fam, g.int_width.get(name, 64) if o.is_gdb else 64)
    cols = [n for n, _ in g.schema]
    if o.add_source_fields:
        gdf["SRC_REGION"] = sf.region_hint[:254]
        gdf["SRC_FILE"] = sf.rel[:254]
        cols += list(SOURCE_FIELDS)
    gdf = gdf[cols + [geom]]
    if o.is_gdb:
        colmap = output_column_map(g, o)
        ren = {c: colmap[c] for c, _ in g.schema if colmap[c] != c}
        if ren:
            gdf = gdf.rename(columns=ren)
    return gdf


def _scan_int_widths(g: Group, o: Options) -> dict[str, int]:
    """Kolom integer ditulis 32 bit jika SEMUA nilai sumber muat (kompatibel ArcGIS lama);
    selain itu 64 bit (butuh GDAL >= 3.9 untuk keluaran .gdb)."""
    int_cols = [n for n, fam in g.schema if fam == "int"]
    widths = {n: 32 for n in int_cols}
    lo, hi = -(2 ** 31), 2 ** 31 - 1
    for sf in g.files:
        have = dict(sf.schema)
        cols = [n for n in int_cols if n in have]
        if not cols:
            continue
        for start in range(0, sf.features, o.chunk_size):
            df = read_dataframe(str(sf.path), columns=cols, read_geometry=False,
                                skip_features=start, max_features=o.chunk_size,
                                **src_kw(sf, o.source_encoding))
            for c in cols:
                if widths[c] == 64:
                    continue
                s = pd.to_numeric(df[c], errors="coerce")
                if s.notna().any() and (s.min() < lo or s.max() > hi):
                    widths[c] = 64
    return widths


# ---------------------------------------------------------------- penulisan
def _infer_gtype(gdf) -> str:
    types = set(gdf.geometry.geom_type.dropna().unique())
    if not types:
        return "Unknown"
    if types <= {"Polygon", "MultiPolygon"}:
        base = "MultiPolygon" if "MultiPolygon" in types else "Polygon"
    elif types <= {"LineString", "MultiLineString"}:
        base = "MultiLineString" if "MultiLineString" in types else "LineString"
    elif types <= {"Point", "MultiPoint"}:
        base = "MultiPoint" if "MultiPoint" in types else "Point"
    else:
        return "Unknown"
    return base + (" Z" if bool(gdf.geometry.has_z.any()) else "")


def _write_chunk_raw(gdf, dest: str, append: bool) -> None:
    """Shapefile: tulis lewat API raw pyogrio agar kolom tanggal tersimpan sebagai Date (DBF 'D')."""
    geom = gdf.geometry.name
    fields = [c for c in gdf.columns if c != geom]
    n = len(gdf)
    data, masks, any_mask = [], [], False
    for c in fields:
        s = gdf[c]
        mask = np.zeros(n, dtype=bool)
        if pd.api.types.is_datetime64_any_dtype(s):
            arr = pd.to_datetime(s).to_numpy().astype("datetime64[D]")  # NaT -> NULL
        elif pd.api.types.is_bool_dtype(s):
            mask = s.isna().to_numpy(dtype=bool)
            arr = s.fillna(False).to_numpy(dtype=bool)
        elif pd.api.types.is_integer_dtype(s):
            mask = s.isna().to_numpy(dtype=bool)
            arr = s.fillna(0).to_numpy(dtype="int64")
        elif pd.api.types.is_float_dtype(s):
            arr = s.to_numpy(dtype="float64", na_value=np.nan)
        else:
            arr = s.to_numpy(dtype=object, na_value=None)
        any_mask = any_mask or bool(mask.any())
        data.append(arr)
        masks.append(mask)
    raw_write(
        dest,
        gdf.geometry.to_wkb().to_numpy(),
        data,
        fields,
        field_mask=masks if any_mask else None,
        driver="ESRI Shapefile",
        geometry_type=_infer_gtype(gdf),
        crs=gdf.crs.to_wkt("WKT1_GDAL") if gdf.crs else None,
        encoding="UTF-8",
        append=append,
    )


def write_frame(gdf, dest: str, append: bool = False, layer: str | None = None,
                fmt: str = "shp", geometry_type: str | None = None) -> None:
    """Penulis tunggal. gdb: OpenFileGDB (tanggal, string, angka ditulis native).
    shp: jalur raw jika ada kolom tanggal, selain itu write_dataframe."""
    geom = gdf.geometry.name
    if fmt == "gdb":
        # OpenFileGDB tidak menerima satu frame yang mencampur geometri tunggal
        # dan multipart. Gunakan tipe multipart secara konsisten pada setiap chunk.
        to_multi = {
            "Polygon": MultiPolygon,
            "LineString": MultiLineString,
            "Point": MultiPoint,
        }
        gdf = gdf.copy()
        gdf[geom] = gdf.geometry.map(
            lambda value: to_multi[value.geom_type]([value])
            if value is not None and value.geom_type in to_multi else value
        )
        kw = {}
        if not append:
            opts = {"CREATE_SHAPE_AREA_AND_LENGTH_FIELDS": "NO"}
            if any(str(gdf[c].dtype) in ("int64", "Int64") for c in gdf.columns if c != geom):
                opts["TARGET_ARCGIS_VERSION"] = "ARCGIS_PRO_3_2_OR_LATER"  # kolom BigInteger
            kw["layer_options"] = opts
        write_dataframe(gdf, dest, layer=layer, driver="OpenFileGDB", append=append,
                        geometry_type=geometry_type, **kw)
        return
    if any(pd.api.types.is_datetime64_any_dtype(gdf[c]) for c in gdf.columns if c != geom):
        _write_chunk_raw(gdf, dest, append)
    else:
        write_dataframe(gdf, dest, driver="ESRI Shapefile", append=append, encoding="UTF-8")


def write_merged(g: Group, o: Options, dest: Path) -> int:
    first, written, invalid = True, 0, 0
    layer = layer_of(g, o)
    if o.is_gdb:
        g.int_width = _scan_int_widths(g, o)
        colmap = output_column_map(g, o)
        renamed = [f"{n}->{colmap[n]}" for n, _ in g.schema if colmap[n] != n]
        if renamed:
            g.warnings.append("RENAME_KOLOM_GDB: nama cadangan sistem GDB diganti: " + ", ".join(renamed))
    elif any(fam == "date" for _, fam in g.schema):
        log.info("  kategori memiliki kolom tanggal: jalur tulis raw (tipe Date dipertahankan)")
    for sf in g.files:
        n = sf.features
        for start in range(0, n, o.chunk_size):
            gdf = read_dataframe(str(sf.path), skip_features=start, max_features=o.chunk_size,
                                 **src_kw(sf, o.source_encoding))
            gdf = _prepare(gdf, sf, g, o)
            invalid += int((gdf.geometry.notna() & ~gdf.geometry.is_valid).sum())
            write_frame(gdf, str(dest), append=not first, layer=layer, fmt=o.output_format)
            first = False
            written += len(gdf)
            log.info("  %s: %d/%d fitur (total tertulis %d)", sf.rel, min(start + o.chunk_size, n), n, written)
    if invalid:
        g.warnings.append(f"{invalid} geometri invalid ditemukan (TIDAK diperbaiki/dihapus)")
    return written


# ---------------------------------------------------------------- verifikasi
FLOAT_ATOL = 1e-15  # DBF output menulis 15 desimal -> galat pembulatan <= ~1e-15


def _is_num(a: pd.Series, b: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b)


def _tol(a: pd.Series, b: pd.Series) -> float:
    return FLOAT_ATOL if (a.dtype.kind == "f" or b.dtype.kind == "f") else 0.0


def _same(a: pd.Series, b: pd.Series, stats: dict | None = None, col: str = "") -> bool:
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    if (pd.api.types.is_datetime64_any_dtype(a)
            and pd.api.types.is_datetime64_any_dtype(b)):
        try:
            return bool(pd.to_datetime(a, utc=True).equals(pd.to_datetime(b, utc=True)))
        except (TypeError, ValueError):
            pass
    if _is_num(a, b):
        af, bf = a.astype(float).to_numpy(), b.astype(float).to_numpy()
        ok = np.isclose(af, bf, rtol=1e-12, atol=_tol(a, b), equal_nan=True)
        if stats is not None and _tol(a, b) > 0:
            diff = np.abs(af - bf)
            diff = diff[~np.isnan(diff)]
            stats[col] = max(stats.get(col, 0.0), float(diff.max()) if diff.size else 0.0)
        return bool(ok.all())
    ao, bo = a.astype(object), b.astype(object)
    a_empty = a.isna() | (ao == "")   # DBF tidak membedakan teks kosong dan NULL
    b_empty = b.isna() | (bo == "")
    return bool(((ao == bo) | (a_empty & b_empty)).all())


def _describe_diff(a: pd.Series, b: pd.Series) -> str:
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    if _is_num(a, b):
        af, bf = a.astype(float).to_numpy(), b.astype(float).to_numpy()
        bad = ~np.isclose(af, bf, rtol=1e-12, atol=_tol(a, b), equal_nan=True)
        i = int(np.argmax(bad))
        return (f"{int(bad.sum())} nilai beda; contoh baris {i}: sumber={af[i]!r}, "
                f"output={bf[i]!r}, selisih={abs(af[i] - bf[i]):.3g}")
    ao, bo = a.astype(object), b.astype(object)
    a_empty = a.isna() | (ao == "")
    b_empty = b.isna() | (bo == "")
    bad = ~(((ao == bo) | (a_empty & b_empty)).to_numpy())
    i = int(np.argmax(bad))
    return (f"{int(bad.sum())} nilai beda; contoh baris {i}: "
            f"sumber={ao[i]!r} ({type(ao[i]).__name__}), output={bo[i]!r} ({type(bo[i]).__name__})")


def verify_output(g: Group, o: Options, dest: Path) -> dict[str, float]:
    """Baca ulang output & bandingkan jumlah fitur, kolom, tipe, dan nilai atribut dengan sumber
    (urutan fitur dipertahankan). Mengembalikan selisih numerik maksimum per kolom desimal."""
    lk = _layer_kw(layer_of(g, o))
    info = read_info(str(dest), **lk)
    if int(info["features"]) != g.total_features:
        raise MergeError(f"jumlah fitur output {info['features']} != {g.total_features}")
    if [str(x) for x in info["fields"]] != expected_columns(g, o):
        raise MergeError("daftar kolom output tidak sesuai rencana")
    out_fam = {str(n): family(d) for n, d in zip(info["fields"], info["dtypes"])}
    colmap = output_column_map(g, o)
    changed = [f"{n}: {fam}->{out_fam.get(colmap[n])}" for n, fam in g.schema
               if out_fam.get(colmap[n]) != fam]
    if changed:
        raise MergeError("tipe kolom berubah: " + ", ".join(changed))
    stats: dict[str, float] = {}
    pos = 0
    schema_families = dict(g.schema)
    for sf in g.files:
        aliases = g.field_aliases.get(sf.rel, {})
        output_names = {actual: canonical for canonical, actual in aliases.items()}
        for start in range(0, sf.features, o.chunk_size):
            src = read_dataframe(str(sf.path), skip_features=start, max_features=o.chunk_size,
                                 read_geometry=False, **src_kw(sf, o.source_encoding))
            out = read_dataframe(str(dest), skip_features=pos + start, max_features=len(src),
                                 read_geometry=False, **lk)
            for col in src.columns:
                canonical = output_names.get(col, col)
                oc = colmap.get(canonical, out_col(canonical, o))
                if oc not in out.columns:
                    continue
                source_values = _cast(src[col], "text") if schema_families.get(canonical) == "text" else src[col]
                if not _same(source_values, out[oc], stats, canonical):
                    raise MergeError(
                        f"verifikasi gagal: kolom {col} pada {sf.rel} (mulai fitur {start}): "
                        + _describe_diff(source_values, out[oc]))
        pos += sf.features
    return stats


# ---------------------------------------------------------------- finalisasi
def finalize(tmp: Path, out_dir: Path, name: str, overwrite: bool) -> None:
    """Shapefile: pindahkan semua komponen NAMA.* dari folder sementara ke tujuan."""
    new = [p for p in tmp.iterdir() if p.is_file() and p.stem.lower() == name.lower()]
    old = existing_outputs(out_dir, name, "shp")
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


def finalize_dir(tmp: Path, out_dir: Path, name: str, overwrite: bool) -> None:
    """GDB: pindahkan folder NAMA.gdb dari folder sementara ke tujuan (atomik di drive yang sama)."""
    new = tmp / f"{name}.gdb"
    if not new.is_dir():
        raise FileNotFoundError(f"hasil sementara tidak ditemukan: {new}")
    old = existing_outputs(out_dir, name, "gdb")
    if old and not overwrite:
        raise FileExistsError(f"output sudah ada: {name}.gdb (gunakan --overwrite)")
    backup = tmp / "_backup"
    backup.mkdir()
    moved = None
    try:
        for p in old:
            os.replace(p, backup / p.name)
            moved = p
        os.replace(new, out_dir / new.name)
    except Exception:
        if moved is not None:
            placed = out_dir / new.name
            if placed.exists():
                shutil.rmtree(placed, ignore_errors=True)
            os.replace(backup / moved.name, moved)
        raise


def finalize_output(tmp: Path, out_dir: Path, name: str, o: Options) -> None:
    if o.is_gdb:
        finalize_dir(tmp, out_dir, name, o.overwrite)
    else:
        finalize(tmp, out_dir, name, o.overwrite)


def _new_tmp(out_dir: Path) -> Path:
    tmp = out_dir / f"{TMP_PREFIX}{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True)
    return tmp


def merge_group(g: Group, o: Options, out_dir: Path) -> None:
    tmp = _new_tmp(out_dir)
    try:
        tmp_out = tmp / f"{g.out_name}{out_ext(o)}"
        written = write_merged(g, o, tmp_out)
        if written != g.total_features:
            raise MergeError(f"fitur tertulis {written} != {g.total_features}")
        if o.verify:
            stats = verify_output(g, o, tmp_out)
            for col, d in sorted(stats.items()):
                if d > 0:
                    g.warnings.append(
                        f"PRESISI: kolom {col} selisih numerik maks {d:.3g} terhadap sumber")
        finalize_output(tmp, out_dir, g.out_name, o)
        final = out_dir / f"{g.out_name}{out_ext(o)}"
        if not final.exists() or _features(final, layer_of(g, o)) != g.total_features:
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
    """Kategori satu-sumber. Shapefile -> shapefile: salin komponen apa adanya.
    Selain itu (keluaran .gdb atau sumber .gdb): tulis ulang lewat jalur yang sama dengan merge."""
    sf = g.files[0]
    tmp = _new_tmp(out_dir)
    try:
        if not o.is_gdb and sf.layer is None:
            for p in sf.path.parent.iterdir():
                if p.is_file() and p.stem.lower() == sf.stem.lower() and p.suffix.lower() in SHP_EXTS:
                    shutil.copy2(p, tmp / f"{g.out_name}{p.suffix.lower()}")
            finalize(tmp, out_dir, g.out_name, o.overwrite)
        else:
            o2 = dataclasses.replace(o, add_source_fields=False)
            dest = tmp / f"{g.out_name}{out_ext(o)}"
            written = write_merged(g, o2, dest)
            if written != sf.features:
                raise MergeError(f"fitur tertulis {written} != {sf.features}")
            if o.verify:
                verify_output(g, o2, dest)
            finalize_output(tmp, out_dir, g.out_name, o)
        final = out_dir / f"{g.out_name}{out_ext(o)}"
        if not final.exists() or _features(final, layer_of(g, o)) != sf.features:
            raise MergeError(f"salinan akhir tidak ditemukan atau jumlah fitur tidak sesuai: {final}")
        g.output = str(final)
        g.output_features = sf.features
        g.warnings.append("SINGLE_SOURCE ditulis apa adanya (tanpa field sumber)")
    except Exception as exc:  # noqa: BLE001
        g.status = "FAILED"
        g.reasons.append(f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
