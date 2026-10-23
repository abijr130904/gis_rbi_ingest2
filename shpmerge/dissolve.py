from __future__ import annotations

import logging
import shutil
from pathlib import Path

import geopandas as gpd
import shapely
from pyogrio import read_dataframe, read_info, write_dataframe

from .merge import _new_tmp, existing_outputs, finalize
from .models import Group, Options

log = logging.getLogger("shpmerge")

DISSOLVE_DIR = "dissolved"


def is_polygon_group(g: Group) -> bool:
    return bool(g.files) and "Polygon" in g.files[0].geometry_type


def _polygons_only(geom):
    """Ambil bagian poligon saja (make_valid dapat menghasilkan GeometryCollection)."""
    if geom is None or geom.is_empty or geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    polys = []
    for part in shapely.get_parts(geom):
        if part.geom_type == "Polygon":
            polys.append(part)
        elif part.geom_type == "MultiPolygon":
            polys.extend(part.geoms)
    return shapely.MultiPolygon(polys) if polys else None


def dissolve_group(g: Group, o: Options, out_dir: Path) -> None:
    """Dissolve hasil gabungan kategori poligon ke <output>\\dissolved\\NAMA.shp.
    Kegagalan hanya menjadi peringatan; status MERGED tidak diubah."""
    if not (o.dissolve and g.output and is_polygon_group(g)):
        return
    name = g.out_name
    ddir = out_dir / DISSOLVE_DIR
    ddir.mkdir(parents=True, exist_ok=True)
    if existing_outputs(ddir, name) and not o.overwrite:
        g.warnings.append("DISSOLVE_DILEWATI: hasil dissolve sudah ada (gunakan --overwrite)")
        return
    tmp = _new_tmp(ddir)
    try:
        gdf = read_dataframe(g.output)
        n_in = len(gdf)
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
        if gdf.empty:
            raise ValueError("tidak ada geometri yang bisa di-dissolve")
        if len(gdf) < n_in:
            g.warnings.append(f"DISSOLVE: {n_in - len(gdf)} fitur tanpa geometri tidak ikut")
        if gdf.geometry.has_z.any():
            g.warnings.append("DISSOLVE: koordinat Z dibuang pada hasil dissolve (2D)")
        geom = gdf.geometry.force_2d().copy()
        bad = ~geom.is_valid
        if bad.any():
            g.warnings.append(f"DISSOLVE: {int(bad.sum())} geometri invalid diperbaiki (make_valid) "
                              "hanya pada file dissolve; file gabungan tidak diubah")
            geom.loc[bad] = geom.loc[bad].make_valid()
        merged = _polygons_only(geom.union_all())
        if merged is None or merged.is_empty:
            raise ValueError("hasil dissolve kosong")
        res = gpd.GeoDataFrame({"JML_FITUR": [n_in]}, geometry=[merged], crs=gdf.crs)
        tmp_shp = tmp / f"{name}.shp"
        write_dataframe(res, str(tmp_shp), driver="ESRI Shapefile", encoding="UTF-8")
        if int(read_info(str(tmp_shp))["features"]) != 1:
            raise ValueError("verifikasi hasil dissolve gagal")
        finalize(tmp, ddir, name, o.overwrite)
        g.dissolved_output = str(ddir / f"{name}.shp")
        g.dissolved_features = 1
        log.info("Dissolve %s: %d fitur -> 1 (%s)", g.key, n_in, g.dissolved_output)
    except Exception as exc:  # noqa: BLE001
        g.warnings.append(f"DISSOLVE_GAGAL: {type(exc).__name__}: {exc}")
        log.error("Dissolve gagal %s: %s", g.key, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)