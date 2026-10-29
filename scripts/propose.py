"""Weekly: Grok reviews Chronosphere and opens up to 3 proposal issues.

Each issue follows the fixed template: Idea, Frame, Why add, Cost, Risk, Preview.
Nothing changes in either repo here; this only writes issues.
  python scripts/propose.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess

from common import ROOT, grok_brief, grok_json, log, read_json

MAX_OPEN = 3


def gh(*args) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def issues(state: str) -> list[dict]:
    out = gh("issue", "list", "--label", "proposal", "--state", state, "--limit", "200",
             "--json", "number,title,labels,body")
    return json.loads(out or "[]")


def context() -> dict:
    geo = read_json(ROOT / "lenses" / "geo" / "eras.json", [])
    rel = read_json(ROOT / "lenses" / "religion" / "eras.json", [])
    lens_dirs = sorted(p.name for p in (ROOT / "lenses").iterdir() if p.is_dir())
    summary = lambda eras: [{"year": e["year"], "name": e["name"], "events": len(e.get("events", [])),
                             "polities": len(e.get("polities", []))} for e in eras]
    return {
        "lenses": lens_dirs,
        "geo_eras": summary(geo),
        "religion_eras": summary(rel),
        "embeds": len(read_json(ROOT / "lenses" / "now" / "embeds.json", [])),
        "backlog": (ROOT / "proposals" / "BACKLOG.md").read_text(),
    }


TASK = """Weekly Chronosphere review. Propose at most {n} improvements, best first.

Good proposals: fill a thin era (few events), add a missing polity, add primary-source or video embeds to
existing events, add hand-picked precedent echoes, items from the backlog, fixes for broken or weak entries,
a new lens pack. Respect the bandwidth rules and the Arx look described in your brief.

Never re-propose anything in REJECTED or OPEN. Prefer small, shippable changes over grand ones.

Return JSON: {{"proposals": [{{
  "title": "short imperative title",
  "idea": "one sentence",
  "frame": "which lens / mode / panel it belongs to and how it fits the look",
  "why": "what a reader gains; which Arx essay or beat it serves",
  "cost": "added KB gzipped, new sources, effort small|medium|large",
  "risk": "licence, accuracy or performance concerns, or none",
  "target": "data or site"
}}]}}

STATE OF CHRONOSPHERE:
{ctx}

OPEN:
{open}

REJECTED:
{rejected}
"""

BODY = """### Idea
{idea}

### Frame
{frame}

### Why add
{why}

### Cost
{cost}

### Risk
{risk}

### Preview
_Filled in when built._

---
Target repo: `{target}`

Reply with `/approve`, `/reject`, or `/revise <note>`. Nothing ships until you merge the pull request.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    open_ = issues("open")
    room = MAX_OPEN - len(open_)
    if room <= 0:
        log(f"{len(open_)} proposals already open; waiting on you.")
        return
    closed = issues("closed")
    rejected = [i["title"] for i in closed if any(l["name"] == "rejected" for l in i["labels"])]
    reply = grok_json(grok_brief(), TASK.format(n=room, ctx=json.dumps(context(), ensure_ascii=False),
                      open=json.dumps([i["title"] for i in open_]), rejected=json.dumps(rejected)),
                      temperature=0.6)
    for p in reply.get("proposals", [])[:room]:
        body = BODY.format(**{k: p.get(k, "") for k in ("idea", "frame", "why", "cost", "risk")},
                           target=p.get("target", "data"))
        if args.dry_run:
            print(f"# {p['title']}\n{body}")
            continue
        gh("issue", "create", "--title", p["title"][:120], "--body", body, "--label", "proposal")
        log("opened:", p["title"])


if __name__ == "__main__":
    main()
