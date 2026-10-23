from __future__ import annotations

import difflib
import re
import unicodedata
from collections import defaultdict

from .models import SourceFile

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def normalize_name(stem: str) -> str:
    """Hanya: NFKC, strip, hapus '.shp' jika ada, casefold.
    Token LN/AR/25K/50K dsb sengaja TIDAK disentuh."""
    s = unicodedata.normalize("NFKC", stem).strip()
    if s.lower().endswith(".shp"):
        s = s[:-4]
    return s.casefold()


def group_files(files: list[SourceFile]) -> dict[str, list[SourceFile]]:
    groups: dict[str, list[SourceFile]] = defaultdict(list)
    for f in files:
        f.key = normalize_name(f.stem)
        groups[f.key].append(f)
    return {k: sorted(v, key=lambda x: x.rel.casefold()) for k, v in groups.items()}


def safe_output_name(key: str) -> str:
    name = _INVALID.sub("_", key.upper()).strip(" .")
    if not name or name.lower() in _RESERVED:
        name = "_" + name
    return name


def suggest_similar(keys, threshold: float = 0.85) -> list[dict]:
    """Hanya rekomendasi untuk dibaca manusia. TIDAK pernah memicu merge."""
    ks = sorted(keys)
    out = []
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            r = difflib.SequenceMatcher(None, a, b).ratio()
            if r >= threshold:
                out.append({"a": a, "b": b, "ratio": round(r, 3),
                            "note": "mirip, TIDAK digabung otomatis"})
    return out