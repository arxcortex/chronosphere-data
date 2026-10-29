"""Refuse any now/*.json that breaks the contract or the size budget.

Exit code 1 means: do not publish. The workflow then restores the last good files.
  python scripts/validate.py live/now/now.json live/now/now_30d.json
"""
from __future__ import annotations

import sys
from pathlib import Path

from common import BUDGET_GZ, MAX_EVENTS_30D, MAX_EVENTS_7D, gz_size, read_json

KINDS = {"conflict", "unrest", "coercion", "diplomacy", "economic", "political"}
HAZARDS = {"quake", "fire", "storm", "volcano", "flood"}


def check(path: Path) -> list[str]:
    errs = []
    doc = read_json(path)
    if not isinstance(doc, dict):
        return [f"{path}: not a JSON object"]
    budget = BUDGET_GZ.get(path.name)
    size = gz_size(path)
    if budget and size > budget:
        errs.append(f"{path.name}: {size} B gzipped, over the {budget} B budget")
    meta = doc.get("meta", {})
    if not meta.get("generated"):
        errs.append("meta.generated missing")
    events = doc.get("events", [])
    limit = MAX_EVENTS_7D if path.name == "now.json" else MAX_EVENTS_30D
    if len(events) > limit:
        errs.append(f"{len(events)} events, limit {limit}")
    if path.name == "now.json" and len(events) < 5:
        errs.append(f"only {len(events)} events: the fetch probably failed")
    seen = set()
    for i, e in enumerate(events):
        where = f"events[{i}]"
        for k in ("id", "t", "title", "lat", "lon", "kind", "sev"):
            if k not in e:
                errs.append(f"{where}: missing {k}")
        if e.get("id") in seen:
            errs.append(f"{where}: duplicate id {e.get('id')}")
        seen.add(e.get("id"))
        if not (-90 <= e.get("lat", 99) <= 90 and -180 <= e.get("lon", 999) <= 180):
            errs.append(f"{where}: bad coordinates")
        if e.get("kind") not in KINDS:
            errs.append(f"{where}: unknown kind {e.get('kind')}")
        if not 1 <= int(e.get("sev", 0)) <= 5:
            errs.append(f"{where}: sev out of range")
        if len(e.get("title", "")) > 90 or len(e.get("summary", "")) > 200:
            errs.append(f"{where}: title or summary too long")
        if e.get("lede") and len(e["lede"]) > 280:
            errs.append(f"{where}: lede too long")
        if not e.get("src"):
            errs.append(f"{where}: no source link")
    for i, h in enumerate(doc.get("hazards", [])):
        if h.get("kind") not in HAZARDS:
            errs.append(f"hazards[{i}]: unknown kind {h.get('kind')}")
    return errs


def main():
    problems = []
    for p in sys.argv[1:]:
        problems += check(Path(p))
    if problems:
        print("VALIDATION FAILED", *problems[:40], sep="\n  ")
        sys.exit(1)
    print("ok:", ", ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
