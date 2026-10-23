from __future__ import annotations

import csv
import json
from pathlib import Path


def write_reports(result: dict, json_path: Path) -> tuple[Path, Path]:
    json_path = Path(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    csv_path = json_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["category", "category_status", "file", "features", "crs",
                    "geometry", "n_fields", "file_error", "output"])
        for c in result["categories"]:
            for f in c["files"]:
                crs = (f["crs"] or "")[:60].replace("\n", " ")
                w.writerow([c["name"], c["status"], f["file"], f["features"], crs,
                            f["geometry"], len(f["fields"]), f["error"] or "", c["output"] or ""])
    return json_path, csv_path