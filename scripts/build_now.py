"""Build the Now lens: fetch -> cluster -> (Grok) rank and write -> echoes -> now.json.

Runs every 6 hours from .github/workflows/refresh-now.yml. Writes only into LIVE_DIR
(the `live` branch checkout). Never touches main.

  python scripts/build_now.py            # normal run
  python scripts/build_now.py --no-grok  # deterministic fallback, no API call
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict

from common import (BUDGET_GZ, LIVE, MAX_EVENTS_30D, MAX_EVENTS_7D, ROOT, UA, grok_available,
                    grok_brief, grok_json, haversine_km, headline, http_get, http_json, log, read_json,
                    slug_words, write_json)

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
ISO = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")

# Country-level GDELT points for these states use the capital, not the national centroid.
# FIPS -> ISO-3, capital, capital lat/lon, centroid lat/lon. Centroid is only for
# relocating events already published before this rule existed.
CAPITALS = {
    "US": ("USA", "Washington", 38.91, -77.04, 39.83, -98.58),
    "CH": ("CHN", "Beijing", 39.90, 116.41, 35.86, 104.20),
    "RS": ("RUS", "Moscow", 55.76, 37.62, 61.52, 105.32),
    "IN": ("IND", "New Delhi", 28.61, 77.21, 20.59, 78.96),
    "JA": ("JPN", "Tokyo", 35.68, 139.65, 36.20, 138.25),
    "GM": ("DEU", "Berlin", 52.52, 13.41, 51.17, 10.45),
    "UK": ("GBR", "London", 51.51, -0.13, 54.00, -2.50),
    "FR": ("FRA", "Paris", 48.86, 2.35, 46.23, 2.21),
    "BR": ("BRA", "Brasília", -15.79, -47.88, -14.24, -51.93),
    "IT": ("ITA", "Rome", 41.90, 12.50, 41.87, 12.57),
}

# CAMEO root code -> Chronosphere kind
KIND = {"18": "conflict", "19": "conflict", "20": "conflict", "14": "unrest",
        "13": "coercion", "15": "coercion", "16": "coercion", "17": "coercion",
        "03": "diplomacy", "04": "diplomacy", "05": "diplomacy", "06": "diplomacy",
        "07": "diplomacy", "08": "diplomacy", "10": "diplomacy", "11": "diplomacy", "12": "diplomacy"}

# GDELT actor-type codes for people and institutions that hold power or capital.
POWER_TYPES = {"GOV", "ELI", "BUS", "LEG", "JUD", "IGO", "OPP"}
POWER_WORDS = ("PRESIDENT", "PRIME MINISTER", "CHANCELLOR", "PARLIAMENT", "CONGRESS", "SENATE",
               "CABINET", "ELECTION", "REGIME", "MONARCH", "KING ", "QUEEN ", "SULTAN", "EMIR",
               "BANK", "TREASURY", "FEDERAL RESERVE", "CENTRAL BANK", "IMF", "ECB", "WORLD BANK",
               "TARIFF", "SANCTION", "BILLIONAIRE", "OLIGARCH", "CEO", "CHAIRMAN", "HEDGE FUND")


# ------------------------------------------------------------------ country codes

def fips_to_iso3() -> dict:
    """GeoNames countryInfo maps FIPS (used by GDELT) to ISO-3 (used by Chronosphere)."""
    cache = ROOT / "data" / "country_codes.json"
    table = read_json(cache)
    if table:
        return table
    try:
        text = http_get("https://download.geonames.org/export/dump/countryInfo.txt").decode("utf8")
    except Exception as e:  # keep running without ISO-3; echoes fall back to distance
        log("country code table unavailable:", e)
        return {}
    table = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) > 3 and cols[3]:
            table[cols[3]] = cols[1]
    write_json(cache, table, compact=False)
    return table


# ------------------------------------------------------------------ GDELT

def gdelt_files(hours: int) -> list[str]:
    last = http_get("http://data.gdeltproject.org/gdeltv2/lastupdate.txt").decode()
    url = next(l.split()[-1] for l in last.splitlines() if l.endswith(".export.CSV.zip"))
    stamp = dt.datetime.strptime(url.rsplit("/", 1)[1][:14], "%Y%m%d%H%M%S")
    return [f"http://data.gdeltproject.org/gdeltv2/{(stamp - dt.timedelta(minutes=15 * i)):%Y%m%d%H%M%S}.export.CSV.zip"
            for i in range(hours * 4)]


LOCAL_NOISE = ("FIRE DEPARTMENT", "FIREFIGHTER", "POLICE", "SHERIFF", "DOCUSERIE", "DOCUMENTARY")


def actor_bits(row) -> tuple[set, str]:
    types = {row[i] for i in (12, 13, 14, 22, 23, 24) if i < len(row) and row[i]}
    names = " ".join(a for a in (row[6], row[16]) if a).upper()
    return types, names


def state_level(row, types, names) -> bool:
    """A regime, elite, legislature, court, market, or national government, not a city department."""
    if any(w in names for w in LOCAL_NOISE):
        return False
    if types & {"ELI", "BUS", "LEG", "JUD", "IGO", "OPP"}:
        return True
    if any(w in names for w in POWER_WORDS):
        return True
    codes = [row[i] for i in (5, 15) if i < len(row) and row[i]]
    return "GOV" in types and any(c[:3].isalpha() and c[:3].isupper() for c in codes)


def power_of(row) -> tuple[bool, float]:
    """True when a government, elite, legislature, court, business, or market actor is involved."""
    types, names = actor_bits(row)
    named = any(w in names for w in POWER_WORDS)
    if not (types & POWER_TYPES) and not named:
        return False, 1.0
    weight = 1.0
    if "ELI" in types or any(w in names for w in ("BILLIONAIRE", "OLIGARCH", "CEO", "CHAIRMAN")):
        weight = max(weight, 2.2)
    if "BUS" in types or any(w in names for w in ("BANK", "TREASURY", "FEDERAL RESERVE", "CENTRAL BANK", "IMF", "ECB", "TARIFF", "HEDGE FUND")):
        weight = max(weight, 2.0)
    if types & {"GOV", "LEG", "JUD", "IGO", "OPP"}:
        weight = max(weight, 1.5)
    return True, weight


def gdelt_clusters(hours: int, fips: dict) -> list[dict]:
    clusters: dict[tuple, dict] = {}
    for url in gdelt_files(hours):
        try:
            raw = http_get(url)
        except Exception as e:
            log("skip", url, e)
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            rows = csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), "utf8", errors="replace"), delimiter="\t")
            for r in rows:
                if len(r) < 61 or r[25] != "1":
                    continue
                kind = KIND.get(r[28])
                code = r[26]
                if code.startswith(("061", "084", "103")):
                    kind = "economic"
                power, weight = power_of(r)
                types, names = actor_bits(r)
                if r[28] == "01" and power:
                    kind = "political"
                elif r[28] == "09" and power:
                    kind = "political"
                if not kind or not r[56] or not r[57]:
                    continue
                if kind in ("conflict", "unrest"):
                    if not state_level(r, types, names):
                        continue
                elif not power and kind != "economic":
                    continue
                lat, lon = float(r[56]), float(r[57])
                place = r[52]
                # ActionGeo_Type 1 is the country. GDELT then stores the centroid.
                cap = CAPITALS.get(r[53])
                if cap and r[51] == "1":
                    place, lat, lon = cap[1], cap[2], cap[3]
                arts, gold = int(r[33] or 0), float(r[30] or 0)
                if kind in ("diplomacy", "political") and arts < 12:
                    continue
                if kind == "economic" and arts < 8:
                    continue
                key = (kind, round(lat, 1), round(lon, 1))
                c = clusters.setdefault(key, {"kind": kind, "lat": round(lat, 2), "lon": round(lon, 2),
                                              "place": place, "fips": r[53], "articles": 0, "sources": 0,
                                              "gold": 0.0, "power": False, "weight": 1.0,
                                              "actors": defaultdict(int), "urls": defaultdict(int)})
                c["power"] = c["power"] or power
                c["weight"] = max(c["weight"], weight)
                c["articles"] += arts
                c["sources"] += int(r[32] or 0)
                c["gold"] = min(c["gold"], gold)
                for a in (r[6], r[16]):
                    if a:
                        c["actors"][a] += arts
                if r[60]:
                    c["urls"][r[60]] += arts
    out = []
    for c in clusters.values():
        theme = 1.3 if c["kind"] in ("economic", "political") else 1.0
        c["score"] = round(c["articles"] * (1 + abs(c["gold"]) / 10) * c.pop("weight") * theme, 1)
        c["iso3"] = fips.get(c.pop("fips"), None)
        c["actors"] = [a for a, _ in sorted(c["actors"].items(), key=lambda x: -x[1])[:4]]
        c["urls"] = [u for u, _ in sorted(c["urls"].items(), key=lambda x: -x[1])[:5]]
        out.append(c)
    out.sort(key=lambda c: -c["score"])
    for i, c in enumerate(out):
        c["cid"] = f"c{i}"
    log(f"gdelt: {len(out)} clusters")
    return out[:300]


# ------------------------------------------------------------------ hazards

def hazards() -> list[dict]:
    out = []
    try:
        feed = http_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson")
        for f in feed["features"]:
            p, (lon, lat, *_ ) = f["properties"], f["geometry"]["coordinates"]
            if (p.get("mag") or 0) < 5:
                continue
            out.append({"id": "usgs-" + f["id"], "kind": "quake", "t": ISO(dt.datetime.fromtimestamp(p["time"] / 1000, dt.timezone.utc)),
                        "title": f"M{p['mag']:.1f} earthquake, {p.get('place') or 'unknown location'}",
                        "lat": round(lat, 2), "lon": round(lon, 2), "sev": min(5, max(1, int(p["mag"]) - 3)),
                        "src": [p["url"]]})
    except Exception as e:
        log("usgs failed:", e)
    try:
        feed = http_json("https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=30")
        for ev in feed.get("events", []):
            cat = ev["categories"][0]["id"] if ev.get("categories") else ""
            if cat not in ("wildfires", "severeStorms", "volcanoes", "floods"):
                continue
            g = ev["geometry"][-1]
            if g.get("type") != "Point":
                continue
            lon, lat = g["coordinates"][:2]
            out.append({"id": "eonet-" + ev["id"], "kind": {"wildfires": "fire", "severeStorms": "storm",
                        "volcanoes": "volcano", "floods": "flood"}[cat], "t": g["date"],
                        "title": ev["title"], "lat": round(lat, 2), "lon": round(lon, 2), "sev": 2,
                        "src": [s["url"] for s in ev.get("sources", [])[:2]]})
    except Exception as e:
        log("eonet failed:", e)
    log(f"hazards: {len(out)}")
    return out


# ------------------------------------------------------------------ Grok editorial step

GROK_TASK = """You are running the Chronosphere Now refresh. Below are candidate news clusters from GDELT
for the last {hours} hours, and the events already live this week.

Return JSON: {{"events": [ ... ]}} with at most {limit} items, the most significant first. Each item:
  "cids": candidate ids this item covers (merge clusters that are the same story),
  "update_of": id of an already-live event this continues, or null,
  "title": headline, at most 90 characters, plain and factual,
  "summary": one sentence, at most 200 characters, only facts supported by the URLs, actors and place given,
  "kind": one of conflict, unrest, coercion, diplomacy, economic, political,
  "sev": 1 to 5 (5 = mass-casualty war event or state collapse; 1 = notable but minor).
Prefer stories about elites, regimes, elections, legislatures, courts, sanctions, tariffs, central banks,
markets, and major firms. Drop street crime, sports, entertainment, weather, and local unrest that does not
involve a government, an elite, or a financial institution. Never invent casualties, names or numbers.

LIVE THIS WEEK:
{live}

CANDIDATES:
{cands}
"""


def editorial(cands: list[dict], live: list[dict], hours: int, use_grok: bool) -> list[dict]:
    by_cid = {c["cid"]: c for c in cands}
    picked = []
    if use_grok and grok_available():
        try:
            compact = [{k: c[k] for k in ("cid", "kind", "place", "iso3", "actors", "articles", "gold", "urls")} for c in cands]
            live_c = [{"id": e["id"], "title": e["title"], "iso3": e.get("iso3")} for e in live[:150]]
            reply = grok_json(grok_brief(), GROK_TASK.format(hours=hours, limit=60,
                              live=json.dumps(live_c, ensure_ascii=False), cands=json.dumps(compact, ensure_ascii=False)))
            for item in reply.get("events", [])[:60]:
                members = [by_cid[c] for c in item.get("cids", []) if c in by_cid]
                if not members:
                    continue
                lead = max(members, key=lambda c: c["score"])
                picked.append({**_base(lead, members), "title": str(item["title"])[:90],
                               "summary": str(item.get("summary", ""))[:200], "kind": item.get("kind", lead["kind"]),
                               "sev": max(1, min(5, int(item.get("sev", 2)))), "update_of": item.get("update_of")})
            log(f"grok picked {len(picked)}")
            return picked
        except Exception as e:
            log("grok step failed, using fallback:", e)
    for c in cands[:40]:  # deterministic fallback keeps the layer alive
        title = slug_words(c["urls"][0]) if c["urls"] else c["place"]
        picked.append({**_base(c, [c]), "title": title[:90] or c["place"], "summary": "",
                       "kind": c["kind"], "sev": 3 if c["kind"] == "conflict" else 2, "update_of": None})
    return picked


def _base(lead: dict, members: list[dict]) -> dict:
    urls = []
    for m in members:
        urls += [u for u in m["urls"] if u not in urls]
    return {"key": f"{lead['kind']}:{round(lead['lat'], 1)}:{round(lead['lon'], 1)}",
            "lat": lead["lat"], "lon": lead["lon"], "place": lead["place"], "iso3": lead["iso3"],
            "power": any(m.get("power") for m in members),
            "score": round(sum(m["score"] for m in members), 1), "src": urls[:3]}


# ------------------------------------------------------------------ echoes

def echoes(ev: dict, history: list[dict], n: int = 3) -> list[dict]:
    scored = []
    for h in history:
        d = haversine_km(ev["lat"], ev["lon"], h["lat"], h["lon"])
        # A shared polity only counts when the event is also reasonably near: empires spanned
        # continents, so a country match alone links Bamako to Iberia.
        same_ground = bool(ev.get("iso3")) and ev["iso3"] in h["era_countries"] and d <= 2000
        if d > 900 and not same_ground:
            continue
        scored.append((d - (400 if same_ground else 0), h))
    scored.sort(key=lambda x: x[0])
    out, eras, names = [], set(), set()
    for _, h in scored:  # one echo per era and per name, so the strip spans time
        if h["era"] in eras or h["name"] in names:
            continue
        eras.add(h["era"])
        names.add(h["name"])
        out.append({"id": h["id"], "name": h["name"], "year": h["year"], "lens": h["lens"]})
        if len(out) == n:
            break
    return out


# ------------------------------------------------------------------ assemble

def merge(archive: list[dict], fresh: list[dict]) -> list[dict]:
    by_id = {e["id"]: e for e in archive}
    recent = ISO(NOW - dt.timedelta(hours=48))
    by_key = {e.get("key"): e for e in archive if e.get("u", e["t"]) >= recent}
    for f in fresh:
        # Grok names the story it continues; otherwise the same kind at the same spot within 48 h is the same story
        target = by_id.get(f.pop("update_of") or "") or by_key.get(f["key"])
        if target:
            target.update({k: f[k] for k in ("title", "summary", "sev") if f.get(k)})
            target["u"] = ISO(NOW)
            target["score"] = round(target.get("score", 0) + f["score"], 1)
            target["src"] = (f["src"] + [u for u in target.get("src", []) if u not in f["src"]])[:3]
            continue
        eid = f"n{NOW:%y%m%d%H}-{len(by_id)}"
        by_id[eid] = by_key[f["key"]] = {"id": eid, "t": ISO(NOW), "u": ISO(NOW), **f}
    cutoff = ISO(NOW - dt.timedelta(days=30))
    return sorted([e for e in by_id.values() if e["t"] >= cutoff], key=lambda e: e["t"], reverse=True)


def on_theme(e: dict) -> bool:
    """Published news stays on power, politics, and finance. Hazards are not passed through here."""
    if e.get("kind") in ("diplomacy", "coercion", "economic", "political"):
        return True
    blob = f"{e.get('title', '')} {e.get('summary', '')}".lower()
    if any(w in blob for w in ("fire", "police", "documentary", "auction")):
        return False
    return any(w in blob for w in ("president", "minister", "parliament", "congress", "election", "sanction",
                                   "tariff", "bank", "market", "regime", "billion", "imf", "treasury", "war", "strike"))


def window(events, days, limit):
    cutoff = ISO(NOW - dt.timedelta(days=days))
    keep = [e for e in events if e.get("u", e["t"]) >= cutoff]
    keep.sort(key=lambda e: (-e.get("sev", 1), -e.get("score", 0)))
    return keep[:limit]


def pin_published_capitals(events: list[dict]) -> None:
    """Move events already saved on a top-10 country centroid onto that capital."""
    by_iso = {iso: (city, lat, lon, clat, clon)
              for iso, city, lat, lon, clat, clon in CAPITALS.values()}
    for e in events:
        spec = by_iso.get(e.get("iso3"))
        if not spec:
            continue
        city, lat, lon, clat, clon = spec
        if haversine_km(e["lat"], e["lon"], lat, lon) <= 80:
            continue
        if haversine_km(e["lat"], e["lon"], clat, clon) > 250:
            continue
        e["lat"], e["lon"], e["place"] = lat, lon, city


def strip(e: dict) -> dict:
    return {k: v for k, v in e.items() if k not in ("score", "key") and v not in (None, "", [])}


# ------------------------------------------------------------------ story lede
# The Now panel no longer lists historical echoes. When a source page gives up a
# real first sentence, that sentence fills the same slot.

_LEDE_JUNK = ("subscribe", "cookie", "sign in", "javascript", "enable javascript", "all rights reserved",
              "advertisement", "click here", "newsletter", "privacy policy", "accept cookies")
_LEDE_KEYS = ("og:description", "twitter:description", "description")


def _first_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    while len(parts) > 1 and (len(parts[0]) < 25 or parts[0].rstrip(".").isupper()):
        parts.pop(0)
    sentence = parts[0] if parts else ""
    if len(sentence) > 240:
        sentence = sentence[:237].rsplit(" ", 1)[0].rstrip(",;:—- ") + "…"
    return sentence


def _lede_ok(sentence: str, title: str) -> bool:
    if not sentence or len(sentence) < 40:
        return False
    low = sentence.lower()
    if any(j in low for j in _LEDE_JUNK):
        return False
    title_words = set(re.findall(r"[a-z0-9']+", (title or "").lower()))
    sent_words = set(re.findall(r"[a-z0-9']+", low))
    if title_words and sent_words and len(title_words & sent_words) / len(title_words) > 0.9 and len(sentence) < len(title) + 20:
        return False
    return True


def _page_lede(url: str, title: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=8) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype and "html" not in ctype and "text" not in ctype and "xml" not in ctype:
            return ""
        raw = r.read(350_000)
        charset = r.headers.get_content_charset() or "utf-8"
    page = raw.decode(charset, "replace")
    found = {}
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        attrs = {k.lower(): html.unescape(v) for k, v in re.findall(r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']", tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in _LEDE_KEYS and attrs.get("content"):
            found.setdefault(key, attrs["content"].strip())
    candidates = [_first_sentence(found.get(k, "")) for k in _LEDE_KEYS]
    for m in re.finditer(r"<p\b[^>]*>(.*?)</p>", page, re.I | re.S):
        plain = re.sub(r"<[^>]+>", " ", m.group(1))
        plain = re.sub(r"\s+", " ", html.unescape(plain)).strip()
        if len(plain) >= 80:
            candidates.append(_first_sentence(plain))
            if len(candidates) > 6:
                break
    for sentence in candidates:
        if _lede_ok(sentence, title):
            return sentence
    return ""


def attach_ledes(events: list[dict], cap: int = 100) -> None:
    """Fetch a first sentence for events that do not have one yet. Failures stay blank."""
    cache: dict[str, str] = {}
    tried = 0
    for e in events:
        if e.get("lede") or tried >= cap:
            continue
        for url in (e.get("src") or [])[:3]:
            if url not in cache:
                tried += 1
                try:
                    cache[url] = _page_lede(url, e.get("title", ""))
                except Exception:
                    cache[url] = ""
                if tried >= cap:
                    break
            if cache[url]:
                e["lede"] = cache[url]
                break
    filled = sum(1 for e in events if e.get("lede"))
    log(f"ledes: {filled}/{len(events)} events, {tried} pages fetched")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=6)
    ap.add_argument("--no-grok", action="store_true")
    args = ap.parse_args()

    history = read_json(ROOT / "data" / "history_index.json", [])
    chokepoints = read_json(ROOT / "data" / "chokepoints.json", [])
    archive = read_json(LIVE / "state" / "archive.json", [])
    dispatches = read_json(ROOT / "lenses" / "now" / "dispatches.json", [])
    pin_published_capitals(archive)
    for e in archive:
        if e.get("title"):
            e["title"] = headline(e["title"])

    fips = fips_to_iso3()
    cands = gdelt_clusters(args.hours, fips)
    fresh = editorial(cands, archive, args.hours, use_grok=not args.no_grok)
    archive = merge(archive, fresh)
    for e in archive:
        e["echoes"] = echoes(e, history)
    write_json(LIVE / "state" / "archive.json", archive)

    haz = hazards()
    publishing, seen_ids = [], set()
    for days, limit in ((7, MAX_EVENTS_7D), (30, MAX_EVENTS_30D)):
        for e in window([e for e in archive if on_theme(e)], days, limit):
            if e.get("id") not in seen_ids:
                seen_ids.add(e.get("id"))
                publishing.append(e)
    attach_ledes(publishing)
    for cp in chokepoints:
        cp["active"] = any(haversine_km(cp["lat"], cp["lon"], e["lat"], e["lon"]) <= cp["radius_km"]
                           for e in window(archive, 7, 999))

    meta = {"generated": ISO(NOW), "attribution": ["GDELT Project (gdeltproject.org)", "USGS", "NASA EONET"],
            "editor": "grok" if (grok_available() and not args.no_grok) else "fallback"}
    for name, days, limit, hlim in (("now.json", 7, MAX_EVENTS_7D, 60), ("now_30d.json", 30, MAX_EVENTS_30D, 120)):
        cutoff = ISO(NOW - dt.timedelta(days=days))
        h = sorted([x for x in haz if x["t"] >= cutoff], key=lambda x: -x["sev"])[:hlim]
        doc = {"meta": {**meta, "window_days": days},
               "events": [strip(e) for e in window([e for e in archive if on_theme(e)], days, limit)],
               "hazards": h, "chokepoints": chokepoints, "dispatches": dispatches}
        size = write_json(LIVE / "now" / name, doc)
        log(f"{name}: {len(doc['events'])} events, {len(h)} hazards, {size} B gz (budget {BUDGET_GZ[name]})")


if __name__ == "__main__":
    main()
