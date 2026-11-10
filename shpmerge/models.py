from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCE_FIELDS = ("SRC_REGION", "SRC_FILE")


@dataclass
class Options:
    input_dir: Path
    output_dir: Path
    dry_run: bool = False
    overwrite: bool = False
    target_crs: str | None = None
    include_single: bool = False
    add_source_fields: bool = True
    report: Path | None = None
    categories: list[str] = field(default_factory=list)
    strict_schema: bool = False
    allow_schema_union: bool = False
    force_merge: bool = False
    allow_unknown_crs: bool = False
    skip_bad_files: bool = False
    suggest_similar: bool = False
    chunk_size: int = 50_000
    source_encoding: str | None = None
    verify: bool = True
    dissolve: bool = True
    dissolve_ignore: list[str] = field(default_factory=list)
    dissolve_overlap: bool = True
    dissolve_overlap_min: float = 0.01
    dissolve_seams: bool = True
    only_dissolved: bool = False
    output_format: str = "shp"  # "shp" atau "gdb" (CLI memakai "gdb" sebagai bawaan)

    @property
    def is_gdb(self) -> bool:
        return self.output_format == "gdb"


@dataclass
class SourceFile:
    path: Path               # file .shp, atau folder .gdb
    rel: str                 # path relatif terhadap folder input (posix)
    stem: str                # nama file .shp, atau nama layer .gdb
    region_hint: str         # HANYA petunjuk dari nama folder, bukan wilayah administrasi valid
    key: str = ""
    layer: str | None = None  # nama layer jika sumber berupa .gdb
    info: dict[str, Any] | None = None
    error: str | None = None
    size_shp: int = 0
    size_dbf: int = 0

    @property
    def features(self) -> int:
        return int(self.info["features"]) if self.info else 0

    @property
    def crs(self) -> str | None:
        return (self.info.get("crs") or None) if self.info else None

    @property
    def geometry_type(self) -> str:
        return str(self.info.get("geometry_type")) if self.info else ""

    @property
    def schema(self) -> list[tuple[str, str]]:
        if not self.info:
            return []
        return [(str(n), str(d)) for n, d in zip(self.info["fields"], self.info["dtypes"])]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.rel,
            "layer": self.layer,
            "region_hint": self.region_hint,
            "features": self.features,
            "crs": self.crs,
            "geometry": self.geometry_type,
            "fields": [list(x) for x in self.schema],
            "size_shp_bytes": self.size_shp,
            "size_dbf_bytes": self.size_dbf,
            "error": self.error,
        }


@dataclass
class Group:
    key: str
    files: list[SourceFile]
    out_name: str = ""
    status: str = "READY_TO_MERGE"
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    schema: list[tuple[str, str]] = field(default_factory=list)  # (nama, family int/float/date/text)
    int_width: dict[str, int] = field(default_factory=dict)       # lebar bit kolom integer (khusus GDB)
    crs_out: str | None = None
    drop_z: bool = False
    field_aliases: dict[str, dict[str, str]] = field(default_factory=dict)
    output: str | None = None
    output_features: int | None = None
    dissolved_output: str | None = None
    dissolved_features: int | None = None

    @property
    def total_features(self) -> int:
        return sum(f.features for f in self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.key,
            "out_name": self.out_name,
            "status": self.status,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "output": self.output,
            "total_features": self.total_features,
            "output_features": self.output_features,
            "dissolved_output": self.dissolved_output,
            "dissolved_features": self.dissolved_features,
            "crs_out": self.crs_out,
            "files": [f.to_dict() for f in self.files],
            "skipped_files": [{"file": r, "reason": w} for r, w in self.skipped],
        }
