"""Check the history lenses for drift and broken references.

  python scripts/validate_lenses.py               # errors exit 1, warnings don't
  python scripts/validate_lenses.py --strict      # warnings fail too
  python scripts/validate_lenses.py --check-wiki  # also HEAD-check Wikipedia/Grokipedia links (network)

Errors: duplicate events, daily pool ids that match no event, history index drift,
embed attach keys that match no event. Warnings: events outside their era window,
same wiki page and year on two events.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_history_index import ROOT, LENSES, check as index_check  # noqa: E402
from lens_slug import event_slug, name_slug  # noqa: E402


def load(rel: str, default=None):
    p = ROOT / rel
    return json.loads(p.read_text()) if p.exists() else default


def run(check_wiki: bool = False) -> tuple[list[str], list[str]]:
    errs: list[str] = []
    warns: list[str] = []
    keys: set[str] = set()  # ids and page slugs that resolve to an event
    events: list[tuple[str, dict, dict]] = []
    for lens in LENSES:
        eras = load(f"lenses/{lens}/eras.json", [])
        years = [e["year"] for e in eras]
        for i, era in enumerate(eras):
            lo = years[i - 1] if i else None
            for ev in era.get("events", []):
                events.append((lens, era, ev))
                keys.update({ev["id"], event_slug(lens, ev)})
                y = ev["year"]
                if (lo is not None and y <= lo) or y > era["year"] + 15:
                    warns.append(f"era window: {ev['id']} ({y}) sits in era {era['year']}")
    ids = Counter(ev["id"] for _, _, ev in events)
    errs += [f"duplicate id: {i} x{n}" for i, n in ids.items() if n > 1]
    named = Counter((lens, name_slug(ev["name"]), ev["year"]) for lens, _, ev in events)
    errs += [f"duplicate event: {l}/{n} {y}" for (l, n, y), c in named.items() if c > 1]
    wy = Counter((lens, ev.get("wiki"), ev["year"]) for lens, _, ev in events if ev.get("wiki"))
    warns += [f"same wiki and year: {l} {w} {y}" for (l, w, y), c in wy.items() if c > 1]

    pool = load("games/daily_pool.json", {}).get("events", [])
    errs += [f"daily_pool: duplicate {i}" for i, n in Counter(pool).items() if n > 1]
    errs += [f"daily_pool: no event for {i}" for i in pool if i not in keys]

    errs += index_check()

    for lens in LENSES:
        for x in load(f"lenses/{lens}/embeds.json", []) or []:
            a = x.get("attach")
            if a and a not in keys:
                errs.append(f"embeds/{lens}: {x.get('id')} attaches to unknown event {a}")

    if check_wiki:
        seen = set()
        for _, _, ev in events:
            for base, page in (("https://en.wikipedia.org/wiki/", ev.get("wiki")),
                               ("https://grokipedia.com/page/", ev.get("grokipedia", ev.get("wiki")))):
                if not page:
                    continue
                url = base + urllib.parse.quote(page.split("#")[0], safe="%()'_,-.")
                if url in seen:
                    continue
                seen.add(url)
                try:
                    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "chronosphere-data validator"})
                    code = urllib.request.urlopen(req, timeout=15).status
                except urllib.error.HTTPError as e:
                    code = e.code
                except Exception as e:  # network trouble is a warning, not a data error
                    warns.append(f"wiki: {url} unreachable ({e.__class__.__name__})")
                    continue
                if code == 404:
                    warns.append(f"wiki 404: {ev['id']} {url}")
    return errs, warns


def main() -> int:
    args = sys.argv[1:]
    errs, warns = run("--check-wiki" in args)
    for w in warns:
        print("WARN", w)
    for e in errs:
        print("ERROR", e)
    print(f"lenses: {len(errs)} errors, {len(warns)} warnings")
    return 1 if errs or ("--strict" in args and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
