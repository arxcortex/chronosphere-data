"""Shared helpers for the Chronosphere data pipeline. Standard library only."""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = Path(os.environ.get("LIVE_DIR", ROOT / "live"))  # checkout of the `live` branch
UA = "chronosphere-data/1.0 (+https://arxcortex.io/chronosphere.html)"

# Hard budgets. The validator enforces these; nothing ships above them.
BUDGET_GZ = {"now.json": 40_000, "now_30d.json": 80_000, "material.json": 80_000}
MAX_EVENTS_7D = 150
MAX_EVENTS_30D = 300


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def http_get(url: str, timeout: int = 60, retries: int = 2) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except Exception as e:  # network blips
            last = e
        time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


def http_json(url: str, **kw):
    return json.loads(http_get(url, **kw))


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write_json(path: Path, obj, compact: bool = True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":") if compact else None,
                      indent=None if compact else 1)
    path.write_text(text)
    return len(gzip.compress(text.encode(), 9))


def gz_size(path: Path) -> int:
    return len(gzip.compress(Path(path).read_bytes(), 9))


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


_SMALL = {"a", "an", "the", "and", "or", "but", "of", "for", "to", "in", "on", "at", "by", "from", "with", "as", "if", "nor", "over", "into"}
_ACRONYMS = {"us": "US", "uk": "UK", "un": "UN", "eu": "EU", "imf": "IMF", "nato": "NATO", "idf": "IDF",
             "gop": "GOP", "ai": "AI", "uae": "UAE", "ecb": "ECB", "usa": "USA", "pm": "PM", "ceo": "CEO", "fed": "Fed"}


def headline(text: str) -> str:
    """Title case for a slug or a half-capitalized headline. Safe to run more than once."""
    raw = urllib.parse.unquote(text or "")
    raw = raw.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    raw = re.sub(r"\s+", " ", raw).strip()
    out = []
    for i, word in enumerate(raw.split(" ")):
        m = re.match(r"^([^A-Za-z0-9]*)([A-Za-z0-9']+)(.*)$", word)
        if not m:
            out.append(word)
            continue
        core = m.group(2)
        low = core.lower()
        if low in _ACRONYMS:
            core = _ACRONYMS[low]
        elif i and low in _SMALL:
            core = low
        else:
            core = low[:1].upper() + low[1:]
        out.append(m.group(1) + core + m.group(3))
    return " ".join(out)


def slug_words(url: str, limit: int = 14) -> str:
    """Best-effort headline words from a news URL slug, used when Grok is unavailable."""
    path = re.sub(r"https?://[^/]+", "", url or "")
    parts = [p for p in re.split(r"[/?#]", path) if p]
    best = max(parts, key=lambda p: p.count("-") + p.count("_"), default="")
    best = re.sub(r"\.(html?|php|aspx?)$", "", best)
    words = [w for w in re.split(r"[-_]+", best) if w and not w.isdigit() and len(w) < 25]
    return headline(" ".join(words[:limit]))


# ---------------------------------------------------------------- Grok (xAI API)

XAI_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1") + "/chat/completions"


def grok_available() -> bool:
    return bool(os.environ.get("XAI_API_KEY"))


def grok_json(system: str, user: str, max_tokens: int = 8000, temperature: float = 0.2):
    """Call Grok and parse a JSON object from its reply. Raises on failure."""
    key = os.environ["XAI_API_KEY"]
    model = os.environ.get("XAI_MODEL") or "grok-4"
    body = json.dumps({
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(XAI_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    text = data["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Grok reply had no JSON object")
    return json.loads(m.group(0))


def grok_brief() -> str:
    """The standing brief every Grok call starts from (GROK.md in the repo root)."""
    return (ROOT / "GROK.md").read_text()
