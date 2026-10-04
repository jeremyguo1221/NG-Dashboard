"""RSS news scraper. Emits normalised post dicts identical in shape to the
other scrapers.

Feeds in config.NG_NATIVE_FEEDS bypass the NG-relevance filter (EIA, NGI,
Rigzone, Google News NG queries — already on-topic). Other feeds (CNBC,
OilPrice, Seeking Alpha) pass through the preprocessor's keyword/ticker filter.
"""
from __future__ import annotations

import calendar
import datetime as dt
import logging
from typing import Iterable

import feedparser
import requests

import config

logger = logging.getLogger(__name__)

# Several publishers 403 feedparser's default / bot-looking user agents.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/130.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
}


def fetch(feeds: Iterable[tuple[str, str]] | None = None) -> list[dict]:
    """Parse each (source_name, url) pair. Tolerant of malformed feeds —
    individual failures log and are skipped."""
    feeds = list(feeds or config.RSS_FEEDS)
    out: list[dict] = []
    for name, url in feeds:
        try:
            r = requests.get(url, headers=_HEADERS, timeout=20)
        except Exception as e:
            logger.warning("feed %s GET failed: %r", name, e)
            continue
        if r.status_code != 200:
            logger.warning("feed %s HTTP %s", name, r.status_code)
            continue
        try:
            parsed = feedparser.parse(r.content)
        except Exception:
            logger.exception("feedparser raised for %s", name)
            continue
        if getattr(parsed, "bozo", 0) and parsed.entries == []:
            logger.warning("feed %s bozo, 0 entries: %r",
                           name, getattr(parsed, "bozo_exception", None))
            continue
        for entry in parsed.entries:
            try:
                out.append(_normalise(entry, name))
            except Exception:
                logger.exception("RSS entry normalise failed for %s", name)
    logger.info("RSS: %d entries across %d feeds", len(out), len(feeds))
    return out


def _normalise(entry, source_name: str) -> dict:
    title = (getattr(entry, "title", "") or "").strip()
    summary = (getattr(entry, "summary", "") or "").strip()
    text = title if not summary else f"{title}\n{summary}"
    url = (getattr(entry, "link", "") or "").strip() or None

    # Try the various date attributes feedparser populates.
    ts = None
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        st = getattr(entry, attr, None)
        if st:
            try:
                # feedparser normalises to a UTC struct_time; timegm (not
                # mktime, which assumes local time) keeps it in UTC.
                ts = dt.datetime.fromtimestamp(calendar.timegm(st), tz=dt.timezone.utc)
                break
            except Exception:
                continue
    if ts is None:
        ts = dt.datetime.now(dt.timezone.utc)

    return {
        "source": "news",
        "source_name": source_name,
        "text": text,
        "url": url,
        "author": getattr(entry, "author", None),
        "engagement_score": 0.0,        # RSS has no engagement signal
        "created_at": ts,
        "is_ng_native": source_name in config.NG_NATIVE_FEEDS,
    }
