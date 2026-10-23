# # cara jelanin
# # python ekstrak_arsip.py .\arsip -o .\data

# #!/usr/bin/env python3
# """
# ekstrak_arsip.py - ekstrak banyak file .zip dan .rar sekaligus.

# Contoh (PowerShell):
#     python ekstrak_arsip.py D:\\data\\arsip
#     python ekstrak_arsip.py D:\\data\\arsip -o D:\\data\\hasil -r -w 6
#     python ekstrak_arsip.py D:\\data\\arsip -p rahasia123

# Cara kerja:
#   - .zip  -> modul bawaan Python (zipfile), tanpa instalasi tambahan.
#   - .rar  -> memakai 7-Zip (7z.exe). Pasang dulu: winget install 7zip.7zip
#   - Tiap arsip diekstrak ke subfolder sendiri (nama sama dengan nama arsip).
#   - Ekstraksi dilakukan ke folder sementara (.partial) lalu di-rename,
#     jadi arsip yang gagal/terputus tidak meninggalkan folder setengah jadi.
#   - Arsip yang folder tujuannya sudah ada dilewati (pakai --timpa untuk ulang).
# """
# import argparse
# import os
# import re
# import shutil
# import subprocess
# import sys
# import zipfile
# from concurrent.futures import ThreadPoolExecutor, as_completed
# from pathlib import Path

# EKSTENSI = {".zip", ".rar"}
# POLA_PART_RAR = re.compile(r"\.part0*(\d+)\.rar$", re.IGNORECASE)


# def cari_7zip():
#     """Cari 7z.exe di PATH atau lokasi instalasi standar Windows."""
#     for nama in ("7z", "7za", "7zr"):
#         lokasi = shutil.which(nama)
#         if lokasi:
#             return lokasi
#     for lokasi in (
#         r"C:\Program Files\7-Zip\7z.exe",
#         r"C:\Program Files (x86)\7-Zip\7z.exe",
#     ):
#         if os.path.isfile(lokasi):
#             return lokasi
#     return None


# def nama_dasar(arsip: Path) -> str:
#     """Nama folder tujuan: nama arsip tanpa ekstensi (dan tanpa '.part1')."""
#     return re.sub(r"\.part0*1$", "", arsip.stem, flags=re.IGNORECASE)


# def kumpulkan_arsip(sumber: Path, rekursif: bool, output: Path):
#     kandidat = sumber.rglob("*") if rekursif else sumber.glob("*")
#     hasil = []
#     for p in sorted(kandidat):
#         if not p.is_file() or p.suffix.lower() not in EKSTENSI:
#             continue
#         if output in p.parents:  # jangan pindai folder hasil sendiri
#             continue
#         m = POLA_PART_RAR.search(p.name)
#         if m and int(m.group(1)) != 1:  # part2, part3, ... ikut part1
#             continue
#         hasil.append(p)
#     return hasil


# def ekstrak_zip(arsip: Path, tujuan: Path, password):
#     pwd = password.encode() if password else None
#     with zipfile.ZipFile(arsip) as z:
#         z.extractall(tujuan, pwd=pwd)


# def ekstrak_7z(exe, arsip: Path, tujuan: Path, password):
#     cmd = [exe, "x", str(arsip), f"-o{tujuan}", "-y", "-bso0", "-bsp0"]
#     if password:
#         cmd.append(f"-p{password}")
#     p = subprocess.run(
#         cmd,
#         stdin=subprocess.DEVNULL,  # cegah 7z menunggu input password
#         capture_output=True,
#         text=True,
#         errors="replace",
#     )
#     # 0 = sukses, 1 = sukses dengan peringatan, >=2 = gagal
#     if p.returncode >= 2:
#         pesan = (p.stderr or p.stdout or "").strip().splitlines()
#         raise RuntimeError(pesan[-1] if pesan else f"7z kode {p.returncode}")


# def proses(arsip, sumber, output, exe7z, password, timpa):
#     rel = arsip.relative_to(sumber).parent
#     tujuan = output / rel / nama_dasar(arsip)

#     if tujuan.exists() and any(tujuan.iterdir()) and not timpa:
#         return arsip, "lewati", "folder tujuan sudah ada"

#     sementara = tujuan.with_name(tujuan.name + ".partial")
#     shutil.rmtree(sementara, ignore_errors=True)
#     sementara.mkdir(parents=True)

#     try:
#         if arsip.suffix.lower() == ".zip":
#             try:
#                 ekstrak_zip(arsip, sementara, password)
#             except NotImplementedError:  # mis. kompresi Deflate64
#                 if not exe7z:
#                     raise
#                 ekstrak_7z(exe7z, arsip, sementara, password)
#         else:
#             if not exe7z:
#                 raise RuntimeError("7-Zip tidak ditemukan (dibutuhkan untuk .rar)")
#             ekstrak_7z(exe7z, arsip, sementara, password)

#         if tujuan.exists():
#             shutil.rmtree(tujuan)
#         sementara.rename(tujuan)
#         return arsip, "ok", ""
#     except Exception as e:  # noqa: BLE001
#         shutil.rmtree(sementara, ignore_errors=True)
#         return arsip, "gagal", str(e)


# def main():
#     if hasattr(sys.stdout, "reconfigure"):
#         sys.stdout.reconfigure(errors="replace")

#     ap = argparse.ArgumentParser(description="Ekstrak banyak file .zip / .rar sekaligus.")
#     ap.add_argument("sumber", type=Path, help="folder berisi arsip")
#     ap.add_argument("-o", "--output", type=Path, help="folder hasil (default: <sumber>\\hasil_ekstrak)")
#     ap.add_argument("-r", "--rekursif", action="store_true", help="cari arsip sampai ke subfolder")
#     ap.add_argument("-w", "--worker", type=int, default=4, help="jumlah proses paralel (default 4)")
#     ap.add_argument("-p", "--password", help="password arsip (jika ada)")
#     ap.add_argument("--timpa", action="store_true", help="ekstrak ulang walau folder tujuan sudah ada")
#     args = ap.parse_args()

#     sumber = args.sumber.resolve()
#     if not sumber.is_dir():
#         sys.exit(f"Folder sumber tidak ditemukan: {sumber}")
#     output = (args.output or sumber / "hasil_ekstrak").resolve()
#     output.mkdir(parents=True, exist_ok=True)

#     daftar = kumpulkan_arsip(sumber, args.rekursif, output)
#     if not daftar:
#         sys.exit("Tidak ada file .zip / .rar yang ditemukan.")

#     exe7z = cari_7zip()
#     if not exe7z and any(a.suffix.lower() == ".rar" for a in daftar):
#         print("PERINGATAN: 7-Zip tidak ditemukan, file .rar akan gagal.")
#         print("            Pasang dengan: winget install 7zip.7zip\n")

#     print(f"{len(daftar)} arsip ditemukan -> {output}\n")
#     hitung = {"ok": 0, "lewati": 0, "gagal": 0}
#     gagal = []

#     with ThreadPoolExecutor(max_workers=max(1, args.worker)) as pool:
#         tugas = [
#             pool.submit(proses, a, sumber, output, exe7z, args.password, args.timpa)
#             for a in daftar
#         ]
#         for i, f in enumerate(as_completed(tugas), 1):
#             arsip, status, pesan = f.result()
#             hitung[status] += 1
#             label = {"ok": "OK    ", "lewati": "LEWATI", "gagal": "GAGAL "}[status]
#             print(f"[{i}/{len(daftar)}] {label} {arsip.name}" + (f"  ({pesan})" if pesan else ""))
#             if status == "gagal":
#                 gagal.append((arsip, pesan))

#     print(f"\nSelesai: {hitung['ok']} berhasil, {hitung['lewati']} dilewati, {hitung['gagal']} gagal.")
#     if gagal:
#         print("\nYang gagal:")
#         for arsip, pesan in gagal:
#             print(f"  - {arsip}: {pesan}")
#         sys.exit(1)


# if __name__ == "__main__":
#     main()



#!/usr/bin/env python3
"""
ekstrak_arsip.py - ekstrak banyak file .zip dan .rar sekaligus.

Contoh PowerShell:
    python ekstrak_arsip.py .\arsip -o .\data
    python ekstrak_arsip.py D:\data\arsip -o D:\data\hasil -w 6
    python ekstrak_arsip.py .\arsip -o .\data -p rahasia123
    python ekstrak_arsip.py .\arsip -o .\data --timpa

Cara kerja:
  - Mencari ZIP dan RAR sampai seluruh kedalaman subfolder otomatis.
  - ZIP menggunakan modul bawaan Python.
  - RAR menggunakan 7-Zip (7z.exe).
  - Setiap arsip diekstrak ke folder tersendiri.
  - Mempertahankan struktur folder relatif sumber.
  - Menggunakan folder sementara (.partial).
  - Folder tujuan yang sudah ada dilewati secara default.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


EKSTENSI = {".zip", ".rar"}

# Contoh:
# arsip.part1.rar -> diproses
# arsip.part2.rar -> dilewati
# arsip.part3.rar -> dilewati
POLA_PART_RAR = re.compile(
    r"\.part(\d+)\.rar$",
    re.IGNORECASE
)


def cari_7zip():
    """Cari 7z.exe di PATH atau lokasi instalasi standar Windows."""

    for nama in ("7z", "7za", "7zr"):
        lokasi = shutil.which(nama)
        if lokasi:
            return lokasi

    for lokasi in (
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ):
        if os.path.isfile(lokasi):
            return lokasi

    return None


def nama_dasar(arsip: Path) -> str:
    """Nama folder tujuan tanpa ekstensi arsip."""

    nama = arsip.stem

    # Contoh: data.part1.rar -> data
    nama = re.sub(
        r"\.part1$",
        "",
        nama,
        flags=re.IGNORECASE
    )

    return nama


def kumpulkan_arsip(sumber: Path, output: Path):
    """
    Cari semua file ZIP dan RAR secara rekursif,
    tanpa batas kedalaman folder.
    """

    hasil = []
    output = output.resolve()

    for p in sumber.rglob("*"):

        if not p.is_file():
            continue

        if p.suffix.lower() not in EKSTENSI:
            continue

        # Jangan ikut memindai folder output sendiri.
        try:
            p.resolve().relative_to(output)
            continue
        except ValueError:
            pass

        # Abaikan RAR part2, part3, dst.
        m = POLA_PART_RAR.search(p.name)

        if m and int(m.group(1)) != 1:
            continue

        hasil.append(p)

    return sorted(hasil)


def ekstrak_zip(arsip: Path, tujuan: Path, password):
    """Ekstrak file ZIP menggunakan zipfile."""

    pwd = password.encode("utf-8") if password else None

    with zipfile.ZipFile(arsip) as z:
        z.extractall(tujuan, pwd=pwd)


def ekstrak_7z(exe, arsip: Path, tujuan: Path, password):
    """Ekstrak arsip menggunakan 7-Zip."""

    cmd = [
        exe,
        "x",
        str(arsip),
        f"-o{tujuan}",
        "-y",
        "-bso0",
        "-bsp0",
    ]

    if password:
        cmd.append(f"-p{password}")

    p = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
    )

    # Kode 0 = sukses
    # Kode 1 = peringatan
    # Kode >= 2 = gagal
    if p.returncode >= 2:
        pesan = (
            p.stderr or p.stdout or ""
        ).strip().splitlines()

        raise RuntimeError(
            pesan[-1] if pesan
            else f"7-Zip kode {p.returncode}"
        )



def proses(arsip, sumber, output, exe7z, password, timpa):
    rel = arsip.relative_to(sumber).parent
    tujuan = output / rel / nama_dasar(arsip)

    # Lewati jika hasil sudah tersedia.
    if (
        tujuan.is_dir()
        and any(tujuan.iterdir())
        and not timpa
    ):
        return arsip, "lewati", "folder tujuan sudah ada"

    # Nama temporary dibuat unik untuk setiap arsip.
    import uuid

    sementara = tujuan.with_name(
        tujuan.name + f".{uuid.uuid4().hex}.partial"
    )

    try:
        sementara.mkdir(parents=True, exist_ok=False)

        if arsip.suffix.lower() == ".zip":
            try:
                ekstrak_zip(arsip, sementara, password)
            except NotImplementedError:
                if not exe7z:
                    raise
                ekstrak_7z(exe7z, arsip, sementara, password)

        elif arsip.suffix.lower() == ".rar":
            if not exe7z:
                raise RuntimeError(
                    "7-Zip tidak ditemukan untuk ekstrak RAR"
                )
            ekstrak_7z(exe7z, arsip, sementara, password)

        # Jangan hapus hasil lama secara otomatis.
        # Jika tujuan sudah ada, hentikan dengan pesan jelas.
        if tujuan.exists():
            if not timpa:
                raise RuntimeError(
                    "Folder tujuan sudah ada. "
                    "Periksa hasil ekstraksi sebelumnya."
                )

            raise RuntimeError(
                "Mode timpa memerlukan penggantian folder. "
                "Folder lama dipertahankan demi keamanan. "
                "Pindahkan atau hapus folder tujuan secara "
                "manual setelah memastikan datanya aman."
            )

        # Pastikan parent folder tersedia.
        tujuan.parent.mkdir(parents=True, exist_ok=True)

        sementara.rename(tujuan)

        return arsip, "ok", ""

    except Exception as e:
        # Hapus hanya folder sementara milik tugas ini.
        if sementara.exists():
            shutil.rmtree(sementara, ignore_errors=True)

        return arsip, "gagal", str(e)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(
        description=(
            "Ekstrak banyak file ZIP dan RAR "
            "secara rekursif."
        )
    )

    ap.add_argument(
        "sumber",
        type=Path,
        help="folder sumber yang berisi arsip"
    )

    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        help="folder hasil ekstraksi"
    )

    ap.add_argument(
        "-w",
        "--worker",
        type=int,
        default=4,
        help="jumlah worker paralel (default: 4)"
    )

    ap.add_argument(
        "-p",
        "--password",
        help="password arsip jika ada"
    )

    ap.add_argument(
        "--timpa",
        action="store_true",
        help="ekstrak ulang meskipun folder tujuan sudah ada"
    )

    args = ap.parse_args()

    sumber = args.sumber.resolve()

    if not sumber.is_dir():
        sys.exit(
            f"Folder sumber tidak ditemukan: {sumber}"
        )

    if args.worker < 1:
        sys.exit("Worker minimal 1.")

    output = (
        args.output.resolve()
        if args.output
        else sumber / "hasil_ekstrak"
    )

    # Buat folder output.
    output.mkdir(
        parents=True,
        exist_ok=True
    )

    # Pencarian otomatis hingga semua subfolder.
    daftar = kumpulkan_arsip(
        sumber,
        output
    )

    if not daftar:
        sys.exit(
            "Tidak ada file .zip / .rar yang ditemukan."
        )

    exe7z = cari_7zip()

    if not exe7z and any(
        a.suffix.lower() == ".rar"
        for a in daftar
    ):
        print(
            "PERINGATAN: 7-Zip tidak ditemukan."
        )
        print(
            "File RAR akan gagal diekstrak."
        )
        print(
            "Instal dengan: winget install 7zip.7zip\n"
        )

    print(
        f"{len(daftar)} arsip ditemukan."
    )
    print(
        f"Output: {output}\n"
    )

    hitung = {
        "ok": 0,
        "lewati": 0,
        "gagal": 0,
    }

    gagal = []

    with ThreadPoolExecutor(
        max_workers=args.worker
    ) as pool:

        tugas = [
            pool.submit(
                proses,
                a,
                sumber,
                output,
                exe7z,
                args.password,
                args.timpa
            )
            for a in daftar
        ]

        for i, f in enumerate(
            as_completed(tugas),
            1
        ):
            arsip, status, pesan = f.result()

            hitung[status] += 1

            label = {
                "ok": "OK",
                "lewati": "LEWATI",
                "gagal": "GAGAL",
            }[status]

            print(
                f"[{i}/{len(daftar)}] "
                f"{label:6} {arsip}"
                + (f" ({pesan})" if pesan else "")
            )

            if status == "gagal":
                gagal.append(
                    (arsip, pesan)
                )

    print("\n========== SELESAI ==========")

    print(
        f"Berhasil : {hitung['ok']}"
    )
    print(
        f"Dilewati : {hitung['lewati']}"
    )
    print(
        f"Gagal    : {hitung['gagal']}"
    )

    if gagal:
        print("\nArsip yang gagal:")

        for arsip, pesan in gagal:
            print(
                f"- {arsip}\n  {pesan}"
            )

        sys.exit(1)


if __name__ == "__main__":
    main()
