import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

from shpmerge import merge as merge_mod
from shpmerge.models import Options
from shpmerge.pipeline import run_pipeline

N = "ADMINISTRASI_LN_50K"


def make(tmp, region, name=N, n=3, crs="EPSG:4326", geom="line", cols=None, depth=None, offset=0):
    d = tmp.joinpath("data", region, *(depth or [region, region]))
    d.mkdir(parents=True, exist_ok=True)
    cols = cols or {"NAMA": [f"{region}_{i}" for i in range(n)], "NILAI": list(range(offset, offset + n))}
    if geom == "line":
        geoms = [LineString([(i, 0), (i + 1, 1)]) for i in range(n)]
    elif geom == "poly":
        geoms = [Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]) for i in range(n)]
    else:
        geoms = [Point(i, i) for i in range(n)]
    gpd.GeoDataFrame(cols, geometry=geoms, crs=crs).to_file(d / f"{name}.shp")
    return d / f"{name}.shp"

def run(tmp, **kw):
    kw.setdefault("output_dir", tmp / "out")
    return run_pipeline(Options(input_dir=tmp / "data", **kw))


def cat(res, name=N):
    return next(c for c in res["categories"] if c["name"] == name.lower())


def out_path(tmp, name=N):
    return tmp / "out" / f"{name}.shp"


def test_two_regions_nested_values_and_readback(tmp_path):  # 1,6,16,17,18
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B", depth=["x"], offset=10)
    c = cat(run(tmp_path))
    assert c["status"] == "MERGED" and c["output_features"] == 6
    df = gpd.read_file(out_path(tmp_path))
    assert len(df) == 6
    assert sorted(df["NILAI"]) == [0, 1, 2, 10, 11, 12]
    assert set(df["SRC_REGION"]) == {"KAB_A", "KAB_B"}
    assert set(df["NAMA"]) == {f"KAB_A_{i}" for i in range(3)} | {f"KAB_B_{i}" for i in range(3)}


def test_many_categories_scales_ln_ar_separate(tmp_path):  # 2,3,4
    for name in ["ADMINISTRASI_LN_25K", "ADMINISTRASI_LN_50K", "ADMINISTRASI_AR_50K", "JALAN_LN_50K"]:
        make(tmp_path, "KAB_A", name)
        make(tmp_path, "KAB_B", name)
    res = run(tmp_path)
    assert res["totals"]["categories"] == 4
    assert all(c["status"] == "MERGED" and c["output_features"] == 6 for c in res["categories"])


def test_case_insensitive_names_same_group(tmp_path):  # 5
    make(tmp_path, "KAB_A", "Jalan_ln_50k")
    make(tmp_path, "KAB_B", "JALAN_LN_50K")
    res = run(tmp_path)
    assert res["totals"]["categories"] == 1 and res["categories"][0]["status"] == "MERGED"


def test_different_crs_review_then_target(tmp_path):  # 7
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B", crs="EPSG:3857")
    assert cat(run(tmp_path))["status"] == "REVIEW_REQUIRED"
    c = cat(run(tmp_path, target_crs="EPSG:4326"))
    assert c["status"] == "MERGED"
    assert gpd.read_file(out_path(tmp_path)).crs.to_epsg() == 4326


def test_unknown_crs(tmp_path):  # 8
    make(tmp_path, "KAB_A", crs=None)
    make(tmp_path, "KAB_B", crs="EPSG:4326")
    assert cat(run(tmp_path))["status"] == "REVIEW_REQUIRED"


def test_all_unknown_crs_needs_flag(tmp_path):  # 8
    make(tmp_path, "KAB_A", crs=None)
    make(tmp_path, "KAB_B", crs=None)
    assert cat(run(tmp_path))["status"] == "REVIEW_REQUIRED"
    assert cat(run(tmp_path, allow_unknown_crs=True))["status"] == "MERGED"


def test_schema_differs(tmp_path):  # 9
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B", cols={"NAMA": ["a", "b", "c"], "KODE": [1, 2, 3]})
    assert cat(run(tmp_path))["status"] == "REVIEW_REQUIRED"
    c = cat(run(tmp_path, allow_schema_union=True))
    assert c["status"] == "MERGED"
    df = gpd.read_file(out_path(tmp_path))
    assert len(df) == 6 and df["KODE"].isna().sum() == 3 and df["NILAI"].isna().sum() == 3


def test_geometry_differs(tmp_path):  # 10
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B", geom="point")
    assert cat(run(tmp_path))["status"] == "REVIEW_REQUIRED"


def _write_corrupt(tmp, region):
    d = tmp / "data" / region
    d.mkdir(parents=True)
    for ext in ("shp", "shx", "dbf"):
        (d / f"{N}.{ext}").write_bytes(b"garbage")


def test_corrupt_shp(tmp_path):  # 11
    make(tmp_path, "KAB_A")
    _write_corrupt(tmp_path, "KAB_B")
    c = cat(run(tmp_path))
    assert c["status"] == "REVIEW_REQUIRED"
    assert any("FILE_TIDAK_VALID" in r for r in c["reasons"])
    assert not out_path(tmp_path).exists()


def test_corrupt_shp_skipped_partial_merge(tmp_path):  # 11
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_C", offset=10)
    _write_corrupt(tmp_path, "KAB_B")
    c = cat(run(tmp_path, skip_bad_files=True))
    assert c["status"] == "MERGED" and c["output_features"] == 6
    assert len(c["skipped_files"]) == 1
    assert any("PARSIAL" in w for w in c["warnings"])
    assert len(gpd.read_file(out_path(tmp_path))) == 6


def test_corrupt_leaves_single_source_when_only_one_good(tmp_path):  # 11, 20
    make(tmp_path, "KAB_A")
    _write_corrupt(tmp_path, "KAB_B")
    c = cat(run(tmp_path, skip_bad_files=True))
    assert c["status"] == "SINGLE_SOURCE"
    assert len(c["skipped_files"]) == 1
    assert not out_path(tmp_path).exists()


def test_missing_component(tmp_path):  # 12
    make(tmp_path, "KAB_A")
    p = make(tmp_path, "KAB_B")
    p.with_suffix(".shx").unlink()
    c = cat(run(tmp_path))
    assert c["status"] == "REVIEW_REQUIRED" and "KOMPONEN_HILANG" in " ".join(c["reasons"])


def test_duplicate_input_warned_not_deduped(tmp_path):  # 13
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B", cols={"NAMA": [f"KAB_A_{i}" for i in range(3)], "NILAI": [0, 1, 2]})
    c = cat(run(tmp_path))
    assert c["status"] == "MERGED" and c["output_features"] == 6
    assert any("KEMUNGKINAN_DUPLIKAT" in w for w in c["warnings"])


def test_output_exists_and_overwrite(tmp_path):  # 14
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B")
    assert cat(run(tmp_path))["status"] == "MERGED"
    assert cat(run(tmp_path))["status"] == "SKIPPED"
    assert cat(run(tmp_path, overwrite=True))["status"] == "MERGED"
    assert len(gpd.read_file(out_path(tmp_path))) == 6


def test_write_failure_keeps_old_output(tmp_path, monkeypatch):  # 15
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B")
    run(tmp_path)
    real, calls = merge_mod.write_dataframe, {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("disk penuh")
        return real(*a, **k)

    monkeypatch.setattr(merge_mod, "write_dataframe", boom)
    assert cat(run(tmp_path, overwrite=True))["status"] == "FAILED"
    assert len(gpd.read_file(out_path(tmp_path))) == 6
    assert not [p for p in (tmp_path / "out").iterdir() if p.name.startswith(".shpmerge_tmp_")]


def test_output_inside_input_not_rescanned(tmp_path):  # 19
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B")
    out = tmp_path / "data" / "merged_output"
    assert run(tmp_path, output_dir=out)["totals"]["shp_found"] == 2
    res = run(tmp_path, output_dir=out, overwrite=True)
    assert res["totals"]["shp_found"] == 2 and cat(res)["output_features"] == 6


def test_single_source(tmp_path):  # 20
    make(tmp_path, "KAB_A")
    assert cat(run(tmp_path))["status"] == "SINGLE_SOURCE"
    assert not out_path(tmp_path).exists()
    assert cat(run(tmp_path, include_single=True))["output"]
    assert out_path(tmp_path).exists()


def test_dry_run_writes_nothing(tmp_path):
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B")
    res = run(tmp_path, dry_run=True)
    assert cat(res)["status"] == "READY_TO_MERGE" and not (tmp_path / "out").exists()

def test_tiny_float_precision_within_dbf_limit(tmp_path):
    vals = [3.25010344845e-05, 1.0, 123.456789012345]
    make(tmp_path, "KAB_A", cols={"NAMA": ["a", "b", "c"], "PANJANG": vals})
    make(tmp_path, "KAB_B", cols={"NAMA": ["d", "e", "f"], "PANJANG": vals})
    c = cat(run(tmp_path))
    assert c["status"] == "MERGED" and c["output_features"] == 6

def test_dissolve_polygon_by_default(tmp_path):
    make(tmp_path, "KAB_A", geom="poly")
    make(tmp_path, "KAB_B", geom="poly", offset=10)
    c = cat(run(tmp_path))
    assert c["status"] == "MERGED" and c["output_features"] == 6
    d = gpd.read_file(tmp_path / "out" / "dissolved" / f"{N}.shp")
    assert len(d) == 1 and d["JML_FITUR"].iloc[0] == 6
    assert abs(d.geometry.area.iloc[0] - 3.0) < 1e-9
    assert len(gpd.read_file(out_path(tmp_path))) == 6  # file gabungan tetap utuh


def test_dissolve_skipped_for_lines_and_when_disabled(tmp_path):
    make(tmp_path, "KAB_A")
    make(tmp_path, "KAB_B")
    cat(run(tmp_path))
    assert not (tmp_path / "out" / "dissolved").exists()
    make(tmp_path, "KAB_C", name="POLY_AR_50K", geom="poly")
    make(tmp_path, "KAB_D", name="POLY_AR_50K", geom="poly")
    run(tmp_path, dissolve=False)
    assert not (tmp_path / "out" / "dissolved" / "POLY_AR_50K.shp").exists()