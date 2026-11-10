from __future__ import annotations

import logging
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from pyproj import CRS

from .detect import group_files, normalize_name, safe_output_name, suggest_similar
from .discovery import find_shapefiles
from .merge import copy_single, existing_outputs, finalize_output, merge_group, out_ext
from .models import Group, Options
from .validate import inspect_file, validate_group

from .dissolve import dissolve_group, is_polygon_group

log = logging.getLogger("shpmerge")


def _only_dissolved(g: Group, o: Options, out_dir: Path, writer) -> None:
    """Stage polygon merge, then publish only dissolve or raw fallback output."""
    with tempfile.TemporaryDirectory(prefix=".shpmerge_only_", dir=out_dir) as tmp_name:
        stage = Path(tmp_name)
        writer(g, o, stage)
        if not g.output:
            return

        dissolve_group(g, o, stage, target_dir=out_dir)
        if g.dissolved_output:
            g.output = g.dissolved_output
            g.output_features = g.dissolved_features
            return

        # No geometries were joined, or dissolve failed: keep the verified raw merge.
        try:
            finalize_output(stage, out_dir, g.out_name, o)
            g.output = str(out_dir / f"{g.out_name}{out_ext(o)}")
            g.warnings.append("ONLY_DISSOLVED: hasil gabungan disimpan apa adanya karena dissolve tidak menghasilkan output")
        except Exception as exc:  # noqa: BLE001
            g.status = "FAILED"
            g.reasons.append(f"{type(exc).__name__}: {exc}")
            g.output = None
            log.error("Gagal memfinalisasi output %s: %s", g.key, exc)


def run_pipeline(o: Options) -> dict:
    t0 = time.perf_counter()
    started = datetime.now().isoformat(timespec="seconds")
    if o.target_crs:
        CRS.from_user_input(o.target_crs)  # gagal cepat jika CRS target tidak valid

    files = find_shapefiles(o.input_dir, o.output_dir)
    by_key = group_files(files)
    groups = [Group(key=k, files=v, out_name=safe_output_name(k)) for k, v in sorted(by_key.items())]

    wanted = {normalize_name(c) for c in o.categories}
    for g in groups:
        if wanted and g.key not in wanted:
            g.status = "SKIPPED"
            g.reasons.append("DI_LUAR_FILTER_KATEGORI")

    names = defaultdict(list)
    for g in groups:
        if g.status != "SKIPPED":
            names[g.out_name.casefold()].append(g)
    for lst in names.values():
        if len(lst) > 1:
            for g in lst:
                g.status = "FAILED"
                g.reasons.append("BENTROK_NAMA_OUTPUT: " + ", ".join(x.key for x in lst))

    active = [g for g in groups if g.status == "READY_TO_MERGE"]
    for i, g in enumerate(active, 1):
        log.info("[%d/%d] inspeksi kategori %s (%d file)", i, len(active), g.key, len(g.files))
        for f in g.files:
            inspect_file(f, o.source_encoding)
        validate_group(g, o)

    out_dir = o.output_dir.resolve()
    if not o.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    for g in groups:
        has_out = bool(existing_outputs(out_dir, g.out_name))
        if g.status == "READY_TO_MERGE":
            if has_out and not o.overwrite:
                if o.dry_run:
                    g.warnings.append("OUTPUT_SUDAH_ADA: butuh --overwrite")
                else:
                    g.status = "SKIPPED"
                    g.reasons.append("OUTPUT_SUDAH_ADA: gunakan --overwrite")
                    continue
            if not o.dry_run:
                log.info("Merge %s -> %s%s", g.key, g.out_name, out_ext(o))
                if o.only_dissolved and o.dissolve and is_polygon_group(g):
                    _only_dissolved(g, o, out_dir, merge_group)
                else:
                    merge_group(g, o, out_dir)
                    if g.status == "MERGED":
                        dissolve_group(g, o, out_dir)
            elif o.dissolve and is_polygon_group(g):
                target = g.out_name if o.only_dissolved else f"dissolved\\{g.out_name}"
                g.warnings.append(f"RENCANA: dissolve ke {target}{out_ext(o)}")
        elif g.status == "SINGLE_SOURCE" and o.include_single and not o.dry_run:
            if has_out and not o.overwrite:
                g.warnings.append("OUTPUT_SUDAH_ADA: tidak disalin (butuh --overwrite)")
            elif o.only_dissolved and o.dissolve and is_polygon_group(g):
                _only_dissolved(g, o, out_dir, copy_single)
            else:
                copy_single(g, o, out_dir)
                if g.output:
                    dissolve_group(g, o, out_dir)

    similar = suggest_similar([g.key for g in groups]) if o.suggest_similar else []
    cats = [g.to_dict() for g in groups]
    skipped = [{"category": g.key, **s} for g in groups for s in g.to_dict()["skipped_files"]]
    skipped += [{"category": g.key, "file": f.rel, "reason": f.error}
                for g in groups for f in g.files if f.error and g.status != "SKIPPED"]
    return {
        "started_at": started,
        "duration_s": round(time.perf_counter() - t0, 2),
        "input_dir": str(o.input_dir),
        "output_dir": str(o.output_dir),
        "dry_run": o.dry_run,
        "options": {k: str(v) for k, v in vars(o).items()},
        "totals": {
            "shp_found": len(files),
            "categories": len(groups),
            "total_input_features": sum(g.total_features for g in groups),
            "status_counts": dict(Counter(g.status for g in groups)),
        },
        "categories": cats,
        "similar_names": similar,
        "skipped_files": skipped,
        "errors": [{"category": g.key, "reasons": g.reasons} for g in groups if g.status == "FAILED"],
    }
