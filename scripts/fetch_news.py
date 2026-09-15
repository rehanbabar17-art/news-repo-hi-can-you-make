#!/usr/bin/env python3
"""
Fetch half-hourly news from Pakistani English-language RSS feeds,
filter for important national & international stories, and deliver
them to a self-hosted or public ntfy.sh topic.

Usage:
    NTFY_TOPIC=NewsPakistan_xxxxx  python3 scripts/fetch_news.py

Environment variables:
    NTFY_TOPIC   - ntfy topic to post to  (required)
    NTFY_SERVER  - ntfy server URL         (default: https://ntfy.sh)
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

# ─── Configuration ────────────────────────────────────────────────────────────

NTFY_TOPIC  = os.getenv("NTFY_TOPIC")
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")
STATE_FILE  = Path(__file__).resolve().parent.parent / "seen_articles.json"

if not NTFY_TOPIC:
    print("ERROR: NTFY_TOPIC environment variable is not set.", file=sys.stderr)
    print("Set it before running:", file=sys.stderr)
    print("  NTFY_TOPIC=your_topic_name python3 scripts/fetch_news.py", file=sys.stderr)
    sys.exit(1)

# Pakistan Standard Time = UTC+5
PKT = timezone(timedelta(hours=5))

WINDOW_MINUTES = 6 * 60   # 360 minutes (6 hours)
MAX_PER_RUN    = 12       # hard cap per cron run

# ─── RSS feeds ────────────────────────────────────────────────────────────────

RSS_FEEDS = [
    {"name": "Dawn",               "url": "https://www.dawn.com/feeds/home",     "scope": "national"},
    {"name": "ARY News",           "url": "https://arynews.tv/feed",            "scope": "national"},
    {"name": "Business Recorder",
     "url": "https://news.google.com/rss/search?q=site:brecorder.com+Pakistan&hl=en-PK&gl=PK&ceid=PK:en",
     "scope": "national"},
    {"name": "GNews Pakistan",
     "url": "https://news.google.com/rss/search?q=Pakistan+news+today&hl=en-PK&gl=PK&ceid=PK:en",
     "scope": "national"},
    {"name": "GNews Pakistan Intl",
     "url": "https://news.google.com/rss/search?q=Pakistan+world+news&hl=en-PK&gl=PK&ceid=PK:en",
     "scope": "international"},
]

# ─── Priority / importance keywords ───────────────────────────────────────────

HIGH_PRIORITY_KEYWORDS = [
    "pakistan", "pakistani", "islamabad", "karachi", "lahore", "peshawar",
    "quetta", "gilgit", "muzaffarabad", "kashmir", "balochistan", "sindh",
    "punjab", "kpk", "pak army", "pakistan army", "isi", "supreme court",
    "imran khan", "imran", "shehbaz", "zardari", "bhutto", "nawaz", "cpec",
    "election", "parliament", "senate", "national assembly", "nuclear",
    "telenor", "onic", "ufone", "e&", "ptcl", "etisalat", "jazz", "zong",
    "lahore high court", "lahore h.c.", "lhc",
    "islamabad high court", "islamabad h.c.", "ihc",
    "constitutional court", "constitution court", "federal constitutional court",
    "constitution bench",
    "pti", "pakistan tehreek-e-insaf",
    "india", "afghan", "iran", "china", "united states", "nato", "oil",
    "gaza", "ukraine", "russia", "un security council", "imf", "world bank",
]

def _build_keyword_regex(keywords: list[str]) -> re.Pattern:
    parts = []
    for kw in keywords:
        if re.fullmatch(r"[\w ]+", kw):
            parts.append(r"\b" + re.escape(kw) + r"\b")
        else:
            parts.append(r"(?:^|\W)" + re.escape(kw) + r"(?:\W|$)")
    return re.compile("|".join(parts), re.IGNORECASE)

KEYWORD_RE = _build_keyword_regex(HIGH_PRIORITY_KEYWORDS)

# ─── Helpers ──────────────────────────────────────────────────────────────────

_GNEWS_SUFFIX_RE = re.compile(r"\s*-\s*\S.*$")

def load_seen() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_seen(seen: dict) -> None:
    if len(seen) > 10_000:
        keys = sorted(seen, key=seen.get, reverse=True)[:5_000]
        seen = {k: seen[k] for k in keys}
    STATE_FILE.write_text(json.dumps(seen, indent=2))


def title_key(title: str) -> str:
    t = _GNEWS_SUFFIX_RE.sub("", title or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def is_recent(entry: dict, now: datetime) -> bool:
    published = None
    for key in ("published_parsed", "updated_parsed"):
        tp = entry.get(key)
        if tp:
            try:
                published = datetime(*tp[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    if published is None:
        return True
    return (now - published) <= timedelta(minutes=WINDOW_MINUTES)


def is_important(entry: dict, scope: str) -> bool:
    if scope == "national":
        return True
    text = (entry.get("title") or "") + " " + (entry.get("summary") or "")
    return bool(KEYWORD_RE.search(text))


def format_message(entry: dict, source: str, scope: str) -> str:
    title = (entry.get("title") or "No title").strip()
    link  = entry.get("link", "")
    tag   = "🇳🇵" if scope == "national" else "🌍"
    ts = ""
    for key in ("published_parsed", "updated_parsed"):
        tp = entry.get(key)
        if tp:
            try:
                dt = datetime(*tp[:6], tzinfo=timezone.utc)
                ts = dt.astimezone(PKT).strftime("%b %d, %I:%M %p PKT")
            except Exception:
                pass
            break
    lines = [
        f"{tag} [{source}] {title}",
        "",
        f"📰 {scope.upper()} — {ts}" if ts else f"📰 {scope.upper()}",
        f"🔗 {link}",
    ]
    return "\n".join(lines)


# ─── Collection + selection ───────────────────────────────────────────────────

def _collect_articles() -> list[dict]:
    now  = datetime.now(timezone.utc)
    seen = load_seen()
    raw  = []

    for feed_cfg in RSS_FEEDS:
        name, url, scope = feed_cfg["name"], feed_cfg["url"], feed_cfg["scope"]
        print(f"  ▸ Fetching {name} …", flush=True)
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:40]:
                tk = title_key(entry.get("title", ""))
                if tk in seen:
                    continue
                if not is_recent(entry, now):
                    continue
                if not is_important(entry, scope):
                    continue
                raw.append({
                    "source": name,
                    "scope": scope,
                    "entry":  entry,
                    "id":     tk,
                })
        except Exception as exc:
            print(f"    ⚠  Failed to fetch {name}: {exc}", file=sys.stderr)

    dedup = set()
    articles = []
    for art in raw:
        if art["id"] in dedup:
            continue
        dedup.add(art["id"])
        articles.append(art)

    return articles


def _select_pool(articles: list[dict], max_n: int) -> list[dict]:
    by_source: dict[str, list[dict]] = {}
    for art in articles:
        by_source.setdefault(art["source"], []).append(art)

    pool = []
    while by_source and len(pool) < max_n:
        for src in list(by_source):
            if by_source[src]:
                pool.append(by_source[src].pop(0))
            if len(pool) >= max_n:
                break
        by_source = {k: v for k, v in by_source.items() if v}
    return pool


# ─── Main ─────────────────────────────────────────────────────────────────────

def send_to_ntfy(message: str) -> bool:
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    try:
        r = requests.post(
            url,
            data=message.encode("utf-8"),
            headers={
                "Title":    "Pakistan & World News",
                "Tags":     "newspaper,news,pakistan",
                "Priority": "default",
            },
            timeout=15,
        )
        return r.status_code in (200, 201)
    except Exception as exc:
        print(f"  ⚠  ntfy POST failed: {exc}", file=sys.stderr)
        return False


def main() -> None:
    now_pk = datetime.now(PKT).strftime("%b %d, %I:%M %p PKT")
    print(f"═══ News fetcher  |  {now_pk}  |  topic={NTFY_TOPIC}  |  window={WINDOW_MINUTES}min  |  cap={MAX_PER_RUN} ═══\n")

    articles = _collect_articles()
    total    = len(articles)
    print(f"\n  Found {total} new article(s) across sources.")
    if total > MAX_PER_RUN:
        print(f"  ⚠  Capping at {MAX_PER_RUN} per run.\n")
    pool = _select_pool(articles, MAX_PER_RUN)

    if not pool:
        print("Nothing new — done.")
        return

    from collections import Counter
    src_counts = Counter(a["source"] for a in pool)
    print(f"  Queue: {dict(src_counts)}\n")

    seen = load_seen()
    sent = 0
    for art in pool:
        body = format_message(art["entry"], art["source"], art["scope"])
        ok = send_to_ntfy(body)
        if ok:
            sent += 1
            seen[art["id"]] = int(time.time())
        else:
            print(f"  ⚠  Failed to send: {art['entry'].get('title','?')[:60]}", file=sys.stderr)

    save_seen(seen)
    print(f"  ✔ Sent {sent}/{len(pool)} article(s) to ntfy topic: {NTFY_TOPIC}")


if __name__ == "__main__":
    main()
