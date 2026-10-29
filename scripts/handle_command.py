"""Act on your reply to a proposal issue: /approve, /reject, /revise <note>.

The workflow only calls this when the comment author is the repo owner.
/approve builds the change on a branch and opens a pull request. It never merges.

Env: ISSUE_NUMBER, ISSUE_TITLE, ISSUE_BODY, COMMENT_BODY, GITHUB_REPOSITORY,
     SITE_DIR (checkout of the site repo, for target `site`), SITE_REPO (owner/name)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from common import ROOT, grok_brief, grok_json, log

N = os.environ["ISSUE_NUMBER"]
TITLE = os.environ.get("ISSUE_TITLE", "")
BODY = os.environ.get("ISSUE_BODY", "")
CMD = os.environ.get("COMMENT_BODY", "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "")

# What the bot may touch, per target. Everything else is refused.
ALLOWED = {
    "data": [r"^lenses/[\w\-/]+\.json$", r"^data/[\w\-/]+\.(json|md)$", r"^proposals/BACKLOG\.md$"],
    "site": [r"^chronosphere\.html$", r"^chronosphere/[\w\-/]+\.(js|css|json)$", r"^assets/[\w\-/]+\.(webp|svg)$"],
}
MAX_FILE = 1_000_000
MAX_TOTAL_CHANGE = 200_000


def sh(*args, cwd=None) -> str:
    return subprocess.run(list(args), check=True, capture_output=True, text=True, cwd=cwd).stdout.strip()


def comment(text: str):
    sh("gh", "issue", "comment", N, "--repo", REPO, "--body", text)


def target() -> str:
    m = re.search(r"Target repo: `(\w+)`", BODY)
    return m.group(1) if m and m.group(1) in ALLOWED else "data"


def repo_dir(t: str) -> Path:
    return ROOT if t == "data" else Path(os.environ["SITE_DIR"])


def listing(base: Path, t: str) -> list[dict]:
    out = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base).as_posix()
        if p.is_file() and not rel.startswith(".") and any(re.match(rx, rel) for rx in ALLOWED[t]):
            out.append({"path": rel, "bytes": p.stat().st_size})
    return out


PICK = """Proposal #{n} was approved. Which existing files must you read to build it? Choose at most 3
from the list. Return JSON {{"read": ["path", ...]}}.

PROPOSAL:
{title}
{body}

FILES:
{files}
"""

BUILD = """Build approved proposal #{n} exactly as written, no more.

Return JSON:
{{"summary": "2-4 sentences for the pull request",
  "files": [{{"path": "...", "content": "full text of a NEW or fully rewritten small file"}}],
  "edits": [{{"path": "...", "find": "exact text that occurs once in the file", "replace": "new text"}}]}}

Use "edits" for changes to large existing files; copy "find" exactly from the file. Keep JSON files valid.
Only these paths are allowed: {allowed}

PROPOSAL:
{title}
{body}

FILES YOU ASKED FOR:
{files}
"""


def approve():
    t = target()
    base = repo_dir(t)
    sh("gh", "issue", "edit", N, "--repo", REPO, "--add-label", "approved")
    files = listing(base, t)
    pick = grok_json(grok_brief(), PICK.format(n=N, title=TITLE, body=BODY, files=json.dumps(files)))
    wanted = [f["path"] for f in files if f["path"] in pick.get("read", [])][:3]
    texts = {p: (base / p).read_text() for p in wanted}
    plan = grok_json(grok_brief(), BUILD.format(n=N, title=TITLE, body=BODY, allowed=ALLOWED[t],
                     files=json.dumps(texts, ensure_ascii=False)), max_tokens=32000)

    changed, total = [], 0
    for f in plan.get("files", []):
        _guard(f["path"], t)
        data = f["content"]
        if len(data.encode()) > MAX_FILE:
            raise SystemExit(f"{f['path']} over 1 MB")
        _write(base / f["path"], data)
        total += len(data)
        changed.append(f["path"])
    for e in plan.get("edits", []):
        _guard(e["path"], t)
        p = base / e["path"]
        text = p.read_text()
        if text.count(e["find"]) != 1:
            raise SystemExit(f"edit target not unique in {e['path']}")
        p.write_text(text.replace(e["find"], e["replace"]))
        total += len(e["replace"])
        changed.append(e["path"])
    if not changed:
        comment("Grok returned no changes for this proposal. Try `/revise` with more detail.")
        return
    if total > MAX_TOTAL_CHANGE:
        raise SystemExit(f"change of {total} B is over the {MAX_TOTAL_CHANGE} B cap; split the proposal")
    for path in changed:
        if path.endswith(".json"):
            json.loads((base / path).read_text())  # refuse invalid JSON before it reaches a PR

    branch = f"proposal-{N}"
    sh("git", "checkout", "-B", branch, cwd=base)
    sh("git", "add", *sorted(set(changed)), cwd=base)
    sh("git", "commit", "-m", f"Proposal #{N}: {TITLE}", cwd=base)
    sh("git", "push", "-f", "origin", branch, cwd=base)
    pr_repo = REPO if t == "data" else os.environ["SITE_REPO"]
    url = sh("gh", "pr", "create", "--repo", pr_repo, "--head", branch, "--title", f"Proposal #{N}: {TITLE}",
             "--body", f"{plan.get('summary', '')}\n\nFrom {REPO}#{N}. Review the deploy preview, then merge to ship.")
    comment(f"Built. Pull request: {url}\n\nThe Netlify deploy preview link appears on the pull request. "
            f"Merging it is the only thing that ships this.")


def _guard(path: str, t: str):
    if ".." in path or not any(re.match(rx, path) for rx in ALLOWED[t]):
        raise SystemExit(f"refused path: {path}")


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def reject():
    sh("gh", "issue", "edit", N, "--repo", REPO, "--add-label", "rejected")
    sh("gh", "issue", "close", N, "--repo", REPO, "--reason", "not planned")


REVISE = """Revise proposal #{n} following the owner's note. Keep the same template.
Return JSON {{"title": "...", "idea": "...", "frame": "...", "why": "...", "cost": "...", "risk": "...", "target": "data or site"}}.

NOTE: {note}

CURRENT:
{title}
{body}
"""


def revise(note: str):
    from propose import BODY as TEMPLATE
    p = grok_json(grok_brief(), REVISE.format(n=N, note=note, title=TITLE, body=BODY))
    body = TEMPLATE.format(**{k: p.get(k, "") for k in ("idea", "frame", "why", "cost", "risk")},
                           target=p.get("target", target()))
    sh("gh", "issue", "edit", N, "--repo", REPO, "--title", p.get("title", TITLE)[:120], "--body", body)
    comment("Revised. Reply `/approve`, `/reject`, or `/revise <note>` again.")


def main():
    try:
        if CMD.startswith("/approve"):
            approve()
        elif CMD.startswith("/reject"):
            reject()
        elif CMD.startswith("/revise"):
            revise(CMD[len("/revise"):].strip() or "Make it smaller and more concrete.")
    except SystemExit as e:
        comment(f"Could not build: {e}. Nothing was changed.")
        raise
    except Exception as e:
        log("failed:", e)
        comment(f"The bot hit an error ({type(e).__name__}). Nothing was changed; see the Actions log.")
        raise


if __name__ == "__main__":
    main()
