from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from .models import Options
from .pipeline import run_pipeline
from .report import write_reports


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Auto-detect & merge Shapefile RBI per kategori (nama layer).")
    p.add_argument("--input", required=True, help="folder sumber")
    p.add_argument("--output", required=True, help="folder hasil")
    p.add_argument("--dry-run", action="store_true", help="hanya deteksi & tampilkan rencana")
    p.add_argument("--overwrite", action="store_true", help="izinkan mengganti output yang sesuai")
    p.add_argument("--target-crs", help="CRS target (mis. EPSG:4326); tanpa ini tidak ada transformasi")
    p.add_argument("--include-single", action="store_true", help="salin kategori dengan 1 sumber")
    p.add_argument("--add-source-fields", action=argparse.BooleanOptionalAction, default=True,
                   help="tambah SRC_REGION & SRC_FILE (nonaktifkan: --no-add-source-fields)")
    p.add_argument("--report", help="path laporan JSON (CSV disimpan di sebelahnya)")
    p.add_argument("--category", action="append", default=[], help="filter kategori (boleh berulang)")
    p.add_argument("--strict-schema", action="store_true", help="skema harus identik (nama, tipe, urutan)")
    p.add_argument("--allow-schema-union", action="store_true",
                   help="izinkan kolom hilang (diisi NULL) selama tipe tidak konflik")
    p.add_argument("--allow-unknown-crs", action="store_true",
                   help="izinkan merge jika SEMUA sumber tanpa CRS")
    p.add_argument("--skip-bad-files", action="store_true",
                   help="lewati file rusak/tidak lengkap (hasil ditandai PARSIAL)")
    p.add_argument("--suggest-similar", action="store_true", help="laporkan nama layer yang mirip")
    p.add_argument("--chunk-size", type=int, default=50_000)
    p.add_argument("--source-encoding", help="paksa encoding DBF sumber bila tidak ada .cpg")
    p.add_argument("--no-verify", dest="verify", action="store_false", help="lewati verifikasi baca-ulang")
    p.add_argument("--dissolve", action=argparse.BooleanOptionalAction, default=True,
                   help="dissolve kategori poligon ke <output>\\dissolved (nonaktifkan: --no-dissolve)")
    p.add_argument("--verbose", action="store_true")
    return p


def print_summary(r: dict) -> None:
    t = r["totals"]
    print(f"\nSHP ditemukan: {t['shp_found']} | kategori: {t['categories']} | "
          f"fitur input: {t['total_input_features']} | durasi: {r['duration_s']}s")
    for c in r["categories"]:
        print(f"[{c['status']:<15}] {c['out_name']:<40} file={len(c['files']):<4} fitur={c['total_features']}")
        for x in c["reasons"] + c["warnings"]:
            print(f"      - {x}")
    for s in r["similar_names"]:
        print(f"  mirip: {s['a']} ~ {s['b']} ({s['ratio']}) - tidak digabung")
    counts = t["status_counts"]
    if r["dry_run"]:
        print("\nDRY-RUN: tidak ada file yang ditulis.")
    else:
        print(f"\nMERGED {counts.get('MERGED', 0)} dari {t['categories']} kategori. "
              "Kategori lain BELUM tergabung; periksa alasan di atas / laporan.")
        n_d = sum(1 for c in r["categories"] if c.get("dissolved_output"))
        print(f"Dissolve: {n_d} kategori poligon -> {r['output_dir']}\\dissolved")


def main(argv=None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    inp, out = Path(a.input), Path(a.output)
    if not inp.is_dir():
        parser.error(f"folder input tidak ditemukan: {inp}")
    o = Options(input_dir=inp, output_dir=out, dry_run=a.dry_run, overwrite=a.overwrite,
                target_crs=a.target_crs, include_single=a.include_single,
                add_source_fields=a.add_source_fields,
                report=Path(a.report) if a.report else None, categories=a.category,
                strict_schema=a.strict_schema, allow_schema_union=a.allow_schema_union,
                allow_unknown_crs=a.allow_unknown_crs, skip_bad_files=a.skip_bad_files,
                suggest_similar=a.suggest_similar, chunk_size=a.chunk_size,
                source_encoding=a.source_encoding, verify=a.verify)
    result = run_pipeline(o)
    print_summary(result)
    report = o.report
    if report is None and not o.dry_run:
        report = out / f"merge_report_{datetime.now():%Y%m%d_%H%M%S}.json"
    if report:
        j, c = write_reports(result, report)
        print(f"Laporan: {j}\n         {c}")
    return 1 if result["totals"]["status_counts"].get("FAILED") else 0