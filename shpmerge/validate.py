from __future__ import annotations

from pyogrio import read_dataframe, read_info
from pyproj import CRS

from .discovery import missing_components, sibling_size
from .models import SOURCE_FIELDS, Group, Options, SourceFile

SHP_LIMIT = 2_000_000_000  # batas praktis .shp / .dbf (~2 GB); tidak berlaku untuk keluaran .gdb
INT32_DTYPES = {"int8", "int16", "int32", "uint8", "uint16"}


def src_kw(sf: SourceFile, encoding: str | None = None) -> dict:
    """Argumen baca pyogrio untuk satu sumber: layer untuk .gdb, encoding untuk .shp."""
    if sf.layer:
        return {"layer": sf.layer}
    return {"encoding": encoding} if encoding else {}


def crs_equal(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        ca, cb = CRS.from_user_input(a), CRS.from_user_input(b)
    except Exception:
        return False
    if ca.equals(cb, ignore_axis_order=True):
        return True
    ea, eb = ca.to_epsg(min_confidence=70), cb.to_epsg(min_confidence=70)
    return ea is not None and ea == eb


def family(dtype: str) -> str:
    d = str(dtype)
    if d.startswith(("int", "uint")):
        return "int"
    if d.startswith("float"):
        return "float"
    if d.startswith("datetime"):
        return "date"
    return "text"


def dtype_bits(dtype) -> int:
    return 32 if str(dtype) in INT32_DTYPES else 64


def _gnorm(t: str) -> str:
    """Layer .gdb melaporkan Multi*, Shapefile tidak: samakan Polygon dan LineString."""
    return t.replace("MultiPolygon", "Polygon").replace("MultiLineString", "LineString")


def inspect_file(sf: SourceFile, encoding: str | None = None) -> None:
    if sf.error:
        return
    if sf.layer is None:
        miss = missing_components(sf.path)
        if miss:
            sf.error = "KOMPONEN_HILANG: " + ",".join(miss)
            return
    elif not sf.path.is_dir():
        sf.error = "GDB_TIDAK_DITEMUKAN"
        return
    try:
        sf.info = read_info(str(sf.path), **src_kw(sf, encoding))
        if int(sf.info["features"]) < 0:
            raise ValueError("jumlah fitur tidak dapat dihitung")
    except Exception as exc:  # noqa: BLE001
        sf.info = None
        sf.error = f"TIDAK_BISA_DIBACA: {exc}"
        return
    if sf.layer is None:
        sf.size_shp = sf.path.stat().st_size
        sf.size_dbf = sibling_size(sf.path, ".dbf")


def _fingerprint(sf: SourceFile):
    b = sf.info.get("total_bounds")
    bounds = tuple(round(float(x), 6) for x in b) if b is not None else ()
    return (sf.features, bounds, sf.size_shp, sf.size_dbf)


def _col_all_null(sf: SourceFile, col: str, encoding: str | None) -> bool:
    try:
        df = read_dataframe(str(sf.path), columns=[col], read_geometry=False, **src_kw(sf, encoding))
    except Exception:  # noqa: BLE001
        return False
    s = df[col]
    return bool((s.isna() | s.astype(str).str.strip().eq("")).all())


def reconcile_empty_columns(files, union: dict, encoding: str | None) -> dict:
    """Kolom bertipe beda antar-file yang KOSONG pada file bertipe 'minoritas' dianggap kompatibel:
    tipe mengikuti file yang kolomnya benar-benar terisi."""
    fixed: dict[str, str] = {}
    for col in list(union):
        groups: dict[str, list] = {}
        for f in files:
            d = dict(f.schema).get(col)
            if d is not None:
                groups.setdefault(family(d), []).append(f)
        if len(groups) <= 1:
            continue
        filled = [fam for fam, fl in groups.items()
                  if not all(_col_all_null(f, col, encoding) for f in fl)]
        if len(filled) == 1:
            union[col] = filled[0]
            fixed[col] = filled[0]
        elif not filled:
            union[col] = "text" if "text" in groups else next(iter(groups))
            fixed[col] = union[col]
    return fixed


def _union_schema(files) -> dict[str, str]:
    union: dict[str, str] = {}
    for f in files:
        for n, d in f.schema:
            union.setdefault(n, family(d))
    return union


def validate_group(g: Group, o: Options) -> Group:
    bad = [f for f in g.files if f.error]
    if bad:
        if o.skip_bad_files:
            for f in bad:
                g.skipped.append((f.rel, f.error))
            g.warnings.append(f"PARSIAL: {len(bad)} file dilewati, hasil TIDAK mencakup seluruh sumber")
            g.files = [f for f in g.files if not f.error]
        else:
            g.status = "REVIEW_REQUIRED"
            g.reasons += [f"FILE_TIDAK_VALID: {f.rel} -> {f.error}" for f in bad]
            return g
    if not g.files:
        g.status = "SKIPPED"
        g.reasons.append("TIDAK_ADA_FILE_VALID")
        return g
    if g.total_features == 0:
        g.status = "SKIPPED"
        g.reasons.append("SEMUA_FILE_KOSONG")
        return g
    if len(g.files) == 1:
        g.schema = list(_union_schema(g.files).items())
        g.status = "SINGLE_SOURCE"
        return g

    problems: list[str] = []

    # --- CRS (tidak pernah menebak) ---
    known = [f for f in g.files if f.crs]
    unknown = [f for f in g.files if not f.crs]
    if unknown and known:
        problems.append("CRS_CAMPURAN: sebagian file tanpa CRS: "
                        + ", ".join(f.rel for f in unknown[:5]))
    elif unknown:
        if o.target_crs:
            problems.append("CRS_TIDAK_DIKETAHUI: tidak bisa ditransformasi ke --target-crs")
        elif not o.allow_unknown_crs:
            problems.append("CRS_TIDAK_DIKETAHUI: semua file tanpa CRS (gunakan --allow-unknown-crs jika disengaja)")
        else:
            g.warnings.append("CRS tidak diketahui pada semua sumber; output tanpa informasi CRS")
    else:
        distinct: list[str] = []
        for f in known:
            if not any(crs_equal(f.crs, d) for d in distinct):
                distinct.append(f.crs)
        if len(distinct) > 1 and not o.target_crs:
            problems.append(f"CRS_BERBEDA: {len(distinct)} CRS berbeda (gunakan --target-crs)")
        g.crs_out = o.target_crs or known[0].crs

    # --- Geometri ---
    gtypes = sorted({_gnorm(f.geometry_type) for f in g.files})
    if len(gtypes) > 1:
        bases = {t.removesuffix(" Z") for t in gtypes}
        if o.force_merge and len(bases) == 1:
            g.drop_z = True
            g.warnings.append(
                "FORCE_MERGE_GEOMETRI: Z dibuang karena sumber mencampur geometri 2D dan Z")
        else:
            problems.append(f"GEOMETRI_BERBEDA: {gtypes}")

    # --- Skema ---
    if o.force_merge:
        names: dict[str, str] = {}
        fam_files: dict[str, dict[str, list[tuple[SourceFile, str]]]] = {}
        missing_count = 0
        g.field_aliases.clear()
        for f in g.files:
            aliases: dict[str, str] = {}
            for n, dtype in f.schema:
                folded = n.casefold()
                canonical = names.setdefault(folded, n)
                aliases[canonical] = n
                fam_files.setdefault(canonical, {}).setdefault(family(dtype), []).append((f, n))
            g.field_aliases[f.rel] = aliases

        missing_count = sum(len(names) - len(g.field_aliases[f.rel]) for f in g.files)
        union: dict[str, str] = {}
        conflicting_cols: list[str] = []
        for col, by_family in fam_files.items():
            if len(by_family) == 1:
                union[col] = next(iter(by_family))
                continue
            filled = [fam for fam, sources in by_family.items()
                      if any(not _col_all_null(sf, actual, o.source_encoding)
                             for sf, actual in sources)]
            if len(filled) == 1:
                union[col] = filled[0]
            elif len(filled) > 1:
                union[col] = "text"
                conflicting_cols.append(col)
            else:
                union[col] = "text" if "text" in by_family else next(iter(by_family))

        details = []
        if missing_count:
            details.append(f"{missing_count} kolom sumber yang tidak tersedia diisi NULL")
        if conflicting_cols:
            details.append("konflik tipe disimpan sebagai teks: " + ", ".join(sorted(conflicting_cols)))
        if details:
            g.warnings.append("FORCE_MERGE_SCHEMA: " + "; ".join(details))
    else:
        union = _union_schema(g.files)
        fixed: dict[str, str] = {}
        if not o.strict_schema:
            fixed = reconcile_empty_columns(g.files, union, o.source_encoding)
            if fixed:
                g.warnings.append("TIPE_KOLOM_KOSONG: tipe kolom berbeda antar-file tetapi kosong pada file "
                                  "yang berbeda tipe; diseragamkan -> "
                                  + ", ".join(f"{c}={t}" for c, t in fixed.items()))
        ref = g.files[0]
        diffs, hard = [], False
        for f in g.files:
            if o.strict_schema and f.schema != ref.schema:
                diffs.append(f"{f.rel}: skema tidak identik dengan {ref.rel}")
                hard = True
                continue
            fam = {n: family(d) for n, d in f.schema}
            missing = [n for n in union if n not in fam]
            conflict = [n for n in fam if fam[n] != union[n] and n not in fixed]
            if missing or conflict:
                diffs.append(f"{f.rel}: kolom hilang={missing} konflik tipe={conflict}")
                hard = hard or bool(conflict)
        if diffs:
            if o.allow_schema_union and not hard:
                g.warnings.append("SKEMA_UNION: kolom hilang diisi NULL -> " + " | ".join(diffs))
            else:
                problems.append("SKEMA_BERBEDA: " + " | ".join(diffs))
    g.schema = list(union.items())

    # --- Field sumber ---
    if o.add_source_fields:
        clash = [n for n in SOURCE_FIELDS if n in {x.upper() for x in union}]
        if clash:
            problems.append(f"BENTROK_FIELD_SUMBER: kolom {clash} sudah ada (gunakan --no-add-source-fields)")

    # --- Duplikat (hanya peringatan, tidak dihapus) ---
    seen: dict = {}
    for f in g.files:
        if f.features == 0:
            continue
        fp = _fingerprint(f)
        if fp in seen:
            g.warnings.append(f"KEMUNGKINAN_DUPLIKAT: {f.rel} mirip {seen[fp]} (tetap digabung)")
        else:
            seen[fp] = f.rel

    # --- Batas ukuran Shapefile (tidak berlaku untuk keluaran .gdb) ---
    if not o.is_gdb:
        margin = 0.95 if o.add_source_fields else 1.0
        if (sum(f.size_shp for f in g.files) >= SHP_LIMIT * margin
                or sum(f.size_dbf for f in g.files) >= SHP_LIMIT * margin):
            problems.append("MELEBIHI_BATAS_SHAPEFILE: perkiraan >2 GB; gunakan --output-format gdb")

    if problems:
        g.status = "REVIEW_REQUIRED"
        g.reasons += problems
    return g
