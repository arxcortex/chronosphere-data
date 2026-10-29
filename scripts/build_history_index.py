"""Build data/history_index.json from lenses/*/eras.json.

  python scripts/build_history_index.py          # rewrite the index
  python scripts/build_history_index.py --check  # exit 1 if the index has drifted
Same fields and order as tools/extract_eras.mjs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LENSES = ("geo", "religion")
OUT = ROOT / "data" / "history_index.json"


def build() -> list[dict]:
    index = []
    for lens in LENSES:
        eras = json.loads((ROOT / "lenses" / lens / "eras.json").read_text())
        for era in eras:
            countries = list(dict.fromkeys(c for p in era.get("polities", []) for c in (p.get("countries") or [])))
            for e in era.get("events", []):
                lat = e.get("lat")
                if isinstance(lat, bool) or not isinstance(lat, (int, float)):
                    continue
                index.append({"id": e["id"], "lens": lens, "name": e["name"], "year": e["year"],
                              "lat": e["lat"], "lon": e["lon"], "era": era["name"], "era_year": era["year"],
                              "wiki": e.get("wiki") or None, "era_countries": countries})
    return index


def render(index: list[dict]) -> str:
    return json.dumps(index, ensure_ascii=False, separators=(",", ":"))


def check() -> list[str]:
    want = build()
    have = json.loads(OUT.read_text()) if OUT.exists() else []
    if have == want:
        return []
    hi, wi = {h["id"]: h for h in have}, {w["id"]: w for w in want}
    errs = [f"history_index: {len(have)} entries, eras.json gives {len(want)}; run scripts/build_history_index.py"]
    errs += [f"history_index: missing {i}" for i in sorted(wi.keys() - hi.keys())[:20]]
    errs += [f"history_index: stale {i}" for i in sorted(hi.keys() - wi.keys())[:20]]
    errs += [f"history_index: differs {i}" for i in sorted(wi.keys() & hi.keys()) if wi[i] != hi[i]][:20]
    return errs


def main() -> int:
    if "--check" in sys.argv[1:]:
        errs = check()
        for e in errs:
            print(e)
        return 1 if errs else 0
    index = build()
    OUT.write_text(render(index))
    print(f"history_index: {len(index)} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
