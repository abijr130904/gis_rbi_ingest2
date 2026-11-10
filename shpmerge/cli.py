from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from .models import Options
from .pipeline import run_pipeline
from .report import write_reports
from .extract import extract_gdb_tree


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Auto-detect & merge Shapefile/GDB RBI per kategori (nama layer).")
    p.add_argument("--input", help="folder sumber (.shp/.gdb/ZIP/RAR) atau satu GDB/arsip")
    p.add_argument("--output",
                   help="folder hasil; tiap kategori menjadi NAMA.gdb (atau NAMA.shp) di dalamnya")
    p.add_argument("--output-format", choices=["gdb", "shp"], default="gdb",
                   help="format keluaran: gdb (File Geodatabase, tanpa batas 2 GB) atau shp (bawaan lama)")
    p.add_argument("--extract-gdb", action="store_true",
                   help="ekstrak setiap layer GDB; input boleh satu .gdb atau folder yang dipindai rekursif")
    p.add_argument("--dry-run", action="store_true", help="hanya deteksi & tampilkan rencana")
    p.add_argument("--overwrite", action="store_true", help="izinkan mengganti output yang sesuai")
    p.add_argument("--target-crs", help="CRS target (mis. EPSG:4326); tanpa ini tidak ada transformasi")
    p.add_argument("--include-single", action="store_true", help="tulis kategori dengan 1 sumber")
    p.add_argument("--add-source-fields", action=argparse.BooleanOptionalAction, default=True,
                   help="tambah SRC_REGION & SRC_FILE (nonaktifkan: --no-add-source-fields)")
    p.add_argument("--report", help="path laporan JSON (CSV disimpan di sebelahnya)")
    p.add_argument("--category", action="append", default=[], help="filter kategori (boleh berulang)")
    p.add_argument("--strict-schema", action="store_true", help="skema harus identik (nama, tipe, urutan)")
    p.add_argument("--allow-schema-union", action="store_true",
                   help="izinkan kolom hilang (diisi NULL) selama tipe tidak konflik")
    p.add_argument("--force-merge", action="store_true",
                   help="gabungkan juga kategori dengan skema berbeda; konflik tipe jadi teks, kolom hilang NULL, Z dibuang pada geometri campuran 2D/Z")
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
    p.add_argument("--dissolve-ignore", default="",
                   help="kolom yang diabaikan saat dissolve, pisahkan koma (mis. NOLEMBAR,SHEET)")
    p.add_argument("--dissolve-overlap", action=argparse.BooleanOptionalAction, default=True,
                   help="satukan fitur yang saling tumpang tindih (nonaktifkan: --no-dissolve-overlap)")
    p.add_argument("--dissolve-overlap-min", type=float, default=0.01,
                   help="irisan minimum sebagai pecahan luas bentuk terkecil agar dianggap tumpang tindih "
                        "(bawaan 0.01 = 1%%; 0 = semua irisan berluas positif)")
    p.add_argument("--dissolve-seams", action=argparse.BooleanOptionalAction, default=True,
                   help="satukan potongan di garis lembar antar-file jika atribut identik "
                        "(nonaktifkan: --no-dissolve-seams)")
    p.add_argument("--only-dissolved", action="store_true",
                   help="poligon disimpan sebagai hasil dissolve bila ada yang disatukan, jika tidak "
                        "file gabungan disimpan apa adanya; file gabungan sebelum dissolve tidak "
                        "disimpan ganda. Garis dan titik tetap ditulis biasa")
    p.add_argument("--verbose", action="store_true")
    return p


def print_summary(r: dict) -> None:
    t = r["totals"]
    print(f"\nSumber ditemukan: {t['shp_found']} | kategori: {t['categories']} | "
          f"fitur input: {t['total_input_features']} | durasi: {r['duration_s']}s")
    for c in r["categories"]:
        print(f"[{c['status']:<15}] {c['out_name']:<40} sumber={len(c['files']):<4} fitur={c['total_features']}")
        for x in c["reasons"] + c["warnings"]:
            print(f"      - {x}")
    for s in r["similar_names"]:
        print(f"  mirip: {s['a']} ~ {s['b']} ({s['ratio']}) - tidak digabung")
    counts = t["status_counts"]
    if r["dry_run"]:
        print("\nDRY-RUN: tidak ada file yang ditulis.")
    else:
        n_ok = counts.get("MERGED", 0)
        pending = t["categories"] - n_ok - counts.get("SINGLE_SOURCE", 0) - counts.get("SKIPPED", 0)
        msg = f"\nMERGED {n_ok} dari {t['categories']} kategori."
        if pending > 0:
            msg += f" {pending} kategori belum tergabung (FAILED/REVIEW_REQUIRED); periksa alasan di atas."
        print(msg)
        n_d = sum(1 for c in r["categories"] if c.get("dissolved_output"))
        loc = r["output_dir"] if r["options"].get("only_dissolved") == "True" else r["output_dir"] + "\\dissolved"
        print(f"Dissolve: {n_d} kategori poligon -> {loc}")


def main(argv=None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    if a.only_dissolved and not a.dissolve:
        parser.error("--only-dissolved tidak bisa dipakai bersama --no-dissolve")
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if a.extract_gdb:
        if a.input:
            inp = Path(a.input)
        else:
            data25k = Path("data25k")
            inp = data25k if data25k.is_dir() else Path.cwd()
        out = Path(a.output) if a.output else (
            inp.parent if inp.suffix.casefold() in {".zip", ".rar"} else inp
        )
    else:
        if not a.input or not a.output:
            parser.error("--input dan --output wajib diisi kecuali saat memakai --extract-gdb")
        inp, out = Path(a.input), Path(a.output)
    if not inp.exists():
        parser.error(f"input tidak ditemukan: {inp}")
    if out.suffix.lower() in (".gdb", ".shp"):
        parser.error("--output harus berupa FOLDER; tiap kategori menjadi NAMA.gdb di dalamnya")
    if a.extract_gdb:
        if not inp.is_dir() and inp.suffix.casefold() not in {".gdb", ".zip", ".rar"}:
            parser.error("--extract-gdb membutuhkan folder, GDB, ZIP, atau RAR")
        if a.output_format != "gdb":
            parser.error("--extract-gdb hanya mendukung keluaran .gdb")
        results = extract_gdb_tree(inp, out, overwrite=a.overwrite, dry_run=a.dry_run,
                                   categories=a.category, chunk_size=a.chunk_size)
        for name, count, path in results:
            print(f"{name}: {count} fitur -> {path}")
        if a.dry_run:
            print("DRY-RUN: tidak ada file yang ditulis.")
        else:
            print(f"Selesai: {len(results)} kategori diekstrak.")
        return 0
    o = Options(input_dir=inp, output_dir=out, dry_run=a.dry_run, overwrite=a.overwrite,
                target_crs=a.target_crs, include_single=a.include_single,
                add_source_fields=a.add_source_fields,
                report=Path(a.report) if a.report else None, categories=a.category,
                strict_schema=a.strict_schema, allow_schema_union=a.allow_schema_union,
                force_merge=a.force_merge,
                allow_unknown_crs=a.allow_unknown_crs, skip_bad_files=a.skip_bad_files,
                suggest_similar=a.suggest_similar, chunk_size=a.chunk_size,
                source_encoding=a.source_encoding, verify=a.verify,
                dissolve=a.dissolve,
                dissolve_ignore=[c.strip() for c in a.dissolve_ignore.split(",") if c.strip()],
                dissolve_overlap=a.dissolve_overlap,
                dissolve_overlap_min=a.dissolve_overlap_min,
                dissolve_seams=a.dissolve_seams,
                only_dissolved=a.only_dissolved,
                output_format=a.output_format)
    result = run_pipeline(o)
    print_summary(result)
    report = o.report
    if report is None and not o.dry_run:
        report = out / f"merge_report_{datetime.now():%Y%m%d_%H%M%S}.json"
    if report:
        j, c = write_reports(result, report)
        print(f"Laporan: {j}\n         {c}")
    return 1 if result["totals"]["status_counts"].get("FAILED") else 0
