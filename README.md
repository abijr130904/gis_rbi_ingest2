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

# opsi umum
#   --target-crs EPSG:4326   --allow-schema-union   --skip-bad-files
#   --include-single         --overwrite            --category ADMINISTRASI_LN_50K

# test
python -m pytest tests -q
```

