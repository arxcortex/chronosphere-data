"""Event slug shared by the lens scripts. Mirrors eventSlug in chronosphere.html and tools/extract_eras.mjs."""
from __future__ import annotations

import re
import unicodedata

COMBINING = re.compile(r"[\u0300-\u036f]")


def name_slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.lower())
    s = COMBINING.sub("", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"^-|-$", "", s)[:60]


def event_slug(lens: str, ev: dict) -> str:
    return f"{lens}-{name_slug(ev['name'])}-{ev['year']}"
