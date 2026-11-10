## Merge Shapefile RBI

```powershell
cd D:\Developer\python_project\gis_rbi_ingest

pyhton --version

winges install pyhton.python.3.12

python -m venv .venv

.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip

pip install -r requirements-merge.txt

# 1) selalu dry-run dulu
python merge_shp.py --input "D:\Developer\python_project\gis_rbi_ingest\data" `
  --output "D:\Developer\python_project\gis_rbi_ingest\merged_output" --dry-run `
  --suggest-similar --report ".\dryrun_report.json"

# 2) merge sungguhan
  python merge_shp.py --input "D:\Developer\python_project\gis_rbi_ingest2\data" --output "D:\Developer\python_project\gis_rbi_ingest2\merged_output"

# Opsi umum merge:
#   --target-crs EPSG:4326
#       Transformasikan semua output ke CRS EPSG:4326. Tanpa opsi ini CRS dipertahankan.
#   --allow-schema-union
#       Izinkan kolom berbeda antar sumber; kolom yang tidak ada diisi NULL.
#   --skip-bad-files
#       Lewati file sumber yang rusak/tidak lengkap; hasil bisa parsial.
#   --include-single
#       Sertakan kategori yang hanya ditemukan pada satu sumber.
#   --overwrite
#       Izinkan output yang sudah ada diganti.
#   --category ADMINISTRASI_LN_50K
#       Proses hanya kategori tersebut; opsi ini bisa diulang untuk kategori lain.

# Ekstrak setiap layer/kategori dari satu FileGDB menjadi satu file .gdb
python merge_shp.py --input "D:\data\gabungan.gdb" --output "D:\data\hasil_per_kategori" --extract-gdb
# Opsional: --dry-run untuk melihat rencana, --category NAMA_LAYER untuk memilih kategori,
# --overwrite untuk mengganti file keluaran yang sudah ada

# test
python -m pytest tests -q
```

## Panduan CLI

### Merge Shapefile dan GDB

`merge_shp.py` mencari file `.shp` dan layer spasial di dalam `.gdb`. Sumber dengan nama kategori sama dikelompokkan untuk digabung.

```powershell
python merge_shp.py --input "D:\data\sumber" --output "D:\data\hasil" --dry-run
python merge_shp.py --input "D:\data\sumber" --output "D:\data\hasil"
```

Jalankan `python merge_shp.py --help` untuk melihat opsi dari terminal.

| Opsi | Kegunaan |
|---|---|
| `--input PATH` | Wajib. Folder sumber berisi Shapefile dan/atau folder `.gdb`. |
| `--output PATH` | Wajib. Folder tujuan; setiap kategori menjadi file output tersendiri. |
| `--output-format gdb` | Keluaran FileGDB. Ini format bawaan. |
| `--output-format shp` | Keluaran Shapefile. |
| `--dry-run` | Tampilkan rencana tanpa menulis output. |
| `--overwrite` | Izinkan mengganti output kategori yang sudah ada. |
| `--category NAMA` | Proses kategori tertentu. Ulangi opsi ini untuk beberapa kategori. |
| `--include-single` | Sertakan kategori yang hanya memiliki satu sumber. |
| `--target-crs CRS` | Ubah CRS output, misalnya `EPSG:4326`. Tanpa opsi ini CRS tidak diubah. |
| `--add-source-fields` | Tambahkan kolom `SRC_REGION` dan `SRC_FILE`. Ini bawaan. |
| `--no-add-source-fields` | Jangan tambahkan kolom informasi sumber. |
| `--strict-schema` | Wajibkan skema sumber sama persis (nama, tipe, dan urutan kolom). |
| `--allow-schema-union` | Izinkan kolom yang tidak ada di sebagian sumber; nilai yang hilang diisi `NULL` selama tipe tidak konflik. |
| `--force-merge` | Izinkan merge dengan skema berbeda; konflik tipe menjadi teks, kolom hilang diisi `NULL`, dan Z dibuang pada geometri campuran 2D/Z. |
| `--allow-unknown-crs` | Izinkan merge jika semua sumber tidak memiliki CRS. |
| `--skip-bad-files` | Lewati sumber rusak/tidak lengkap; hasil dapat menjadi parsial. |
| `--suggest-similar` | Laporkan nama kategori yang mirip untuk diperiksa. Nama mirip tidak otomatis digabung. |
| `--chunk-size ANGKA` | Jumlah fitur per batch. Bawaan `50000`. |
| `--source-encoding NAMA` | Paksa encoding DBF sumber jika file `.cpg` tidak tersedia. |
| `--no-verify` | Lewati verifikasi baca ulang output. |
| `--report PATH` | Simpan laporan JSON di lokasi yang ditentukan; CSV dibuat di sebelahnya. Tanpa opsi ini, laporan dibuat otomatis di folder output setelah proses merge. |
| `--verbose` | Tampilkan log lebih terperinci. |

Contoh memilih dua kategori:

```powershell
python merge_shp.py --input "D:\data\sumber" --output "D:\data\hasil" `
  --category ADMINISTRASI_LN_50K --category JALAN_LN_25K
```

### Dissolve

Secara bawaan, kategori poligon di-dissolve dan hasilnya disimpan di subfolder `dissolved`.

| Opsi | Kegunaan |
|---|---|
| `--dissolve` / `--no-dissolve` | Aktifkan atau matikan dissolve. Aktif secara bawaan. |
| `--dissolve-ignore KOLOM1,KOLOM2` | Abaikan kolom tertentu saat dissolve. Pisahkan nama kolom dengan koma. |
| `--dissolve-overlap` / `--no-dissolve-overlap` | Aktifkan atau matikan penggabungan fitur yang tumpang tindih. Aktif secara bawaan. |
| `--dissolve-overlap-min ANGKA` | Ambang irisan sebagai pecahan luas bentuk terkecil. Bawaan `0.01` (1%); `0` berarti semua irisan berluas positif. |
| `--dissolve-seams` / `--no-dissolve-seams` | Aktifkan atau matikan penggabungan potongan di garis antarlembar jika atribut identik. Aktif secara bawaan. |
| `--only-dissolved` | Simpan hasil dissolve saja jika ada geometri yang disatukan; jika tidak, simpan hasil merge biasa. Garis dan titik tetap ditulis biasa. Tidak dapat digunakan dengan `--no-dissolve`. |

Contoh menonaktifkan dissolve:

```powershell
python merge_shp.py --input "D:\data\sumber" --output "D:\data\hasil" --no-dissolve
```

### Ekstrak GDB menjadi GDB per kategori

`--extract-gdb` menerima satu folder `.gdb`, satu file ZIP/RAR, atau folder induk. Arsip dipindai untuk menemukan folder `.gdb` dan layer dibaca langsung tanpa mengekstrak isi arsip ke disk. Hasil GDB ditulis di folder tujuan; secara bawaan hasil dari arsip berada di subfolder bernama arsip, lalu mengikuti struktur folder GDB di dalamnya. Folder induk dipindai rekursif dan isi setiap folder `.gdb` tidak dipindai sebagai folder biasa.

```powershell
# Dari folder proyek: cari semua GDB di .\data25k dan simpan hasil di folder sumbernya
python merge_shp.py --extract-gdb --dry-run
python merge_shp.py --extract-gdb

# Bisa juga dari dalam folder data25k
cd .\data25k
python ..\merge_shp.py --extract-gdb --dry-run

# Atau ekstrak satu GDB saja
python merge_shp.py --input "D:\data\gabungan.gdb" `
  --output "D:\data\hasil_per_kategori" --extract-gdb --dry-run

python merge_shp.py --input "D:\data\gabungan.gdb" `
  --output "D:\data\hasil_per_kategori" --extract-gdb

# Langsung baca GDB di dalam ZIP/RAR tanpa membuka arsip terlebih dahulu
python merge_shp.py --input "D:\data\rbi.zip" --extract-gdb --dry-run
python merge_shp.py --input "D:\data\rbi.rar" --extract-gdb
```

Saat `--extract-gdb` digunakan tanpa `--input`, program mencari `data25k` di folder saat ini; jika folder tersebut tidak ada, folder saat ini yang dipindai. Tanpa `--output`, hasil dari GDB biasa ditaruh di folder sumber; hasil dari arsip ditaruh di folder induk arsip. ZIP dibaca melalui virtual filesystem GDAL. RAR memerlukan dukungan `/vsirar/` pada GDAL (GDAL dibangun dengan libarchive); 7-Zip dibutuhkan untuk membaca daftar isi RAR. Arsip terenkripsi tidak didukung untuk pembacaan langsung. Jalankan `--dry-run` terlebih dahulu untuk meninjau rencana. Output GDB satu layer yang sudah bernama sama dengan layer-nya dikenali sebagai hasil ekstrak sebelumnya sehingga aman saat perintah dijalankan ulang. `--overwrite` tetap tersedia untuk mengganti output kategori yang sudah ada.

Layer tanpa fitur tetap diekstrak beserta skema dan tipe geometrinya. Pyogrio saat ini memberi peringatan bahwa dimensi ukur M pada geometri measured tidak didukung dan dikonversi menjadi Z; nilai M tidak dipertahankan pada hasil.

Gunakan `--category NAMA_LAYER` untuk memilih layer, atau `--chunk-size ANGKA` untuk mengatur jumlah fitur per batch. Contoh:

```powershell
python merge_shp.py --input "D:\data\gabungan.gdb" `
  --output "D:\data\hasil_per_kategori" --extract-gdb `
  --category ADMINISTRASI_LN --overwrite
```

Mode ini hanya mendukung keluaran GDB.

### Ekstrak arsip ZIP/RAR

`ekstrak_arsip.py` mencari arsip di folder sumber dan subfoldernya. Secara bawaan hasil masuk ke folder `hasil_ekstrak` di dalam folder sumber.

```powershell
python ekstrak_arsip.py "D:\data\arsip"
```

| Argumen/Opsi | Kegunaan |
|---|---|
| `SUMBER` | Wajib. Folder yang berisi arsip ZIP/RAR. |
| `-o PATH`, `--output PATH` | Folder tujuan ekstraksi. |
| `-w ANGKA`, `--worker ANGKA` | Jumlah worker paralel. Bawaan `4`, minimal `1`. |
| `-p PASSWORD`, `--password PASSWORD` | Password arsip jika diperlukan. |
| `--timpa` | Ekstrak ulang meskipun folder tujuan arsip sudah ada. |

Contoh menentukan folder output dan jumlah worker:

```powershell
python ekstrak_arsip.py "D:\data\arsip" `
  --output "D:\data\hasil_ekstrak" --worker 4
```

Untuk bantuan opsi masing-masing perintah, jalankan:

```powershell
python merge_shp.py --help
python ekstrak_arsip.py --help
```

