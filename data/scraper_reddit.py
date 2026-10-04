"""Reddit scraper: PRAW when REDDIT_CLIENT_ID is set, public RSS fallback
otherwise (the anonymous .json endpoints now return 403). Both paths emit the same normalised post dicts.

Subreddits listed in config.NG_NATIVE_SUBREDDITS bypass the NG-relevance
filter (everything in them is on-topic). General-investing subs require the
preprocessor's keyword/ticker check.
"""
from __future__ import annotations

import calendar
import datetime as dt
import logging
import os
import re
import time
from typing import Iterable

import feedparser
import requests
from dotenv import load_dotenv

import config

load_dotenv()
logger = logging.getLogger(__name__)

_RSS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/130.0 Safari/537.36"),
    "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
}
# Reddit 429s RSS requests fired back-to-back.
_RSS_DELAY_S = 6.0
_TAG_RE = re.compile(r"<[^>]+>")

_PRAW = None
_PRAW_TRIED = False


def _get_praw():
    """Return a PRAW Reddit instance if creds are available, else None.
    Caches the result so we only attempt construction once."""
    global _PRAW, _PRAW_TRIED
    if _PRAW_TRIED:
        return _PRAW
    _PRAW_TRIED = True

    cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    sec = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    ua  = os.environ.get("REDDIT_USER_AGENT", "").strip()
    if not (cid and sec and ua):
        logger.warning("Reddit creds missing — falling back to public RSS")
        return None
    try:
        import praw
        _PRAW = praw.Reddit(client_id=cid, client_secret=sec, user_agent=ua,
                            check_for_async=False)
        # Force read-only mode (script app without username/password).
        _PRAW.read_only = True
        logger.info("Reddit PRAW authenticated (read-only)")
        return _PRAW
    except Exception:
        logger.exception("PRAW init failed; using public RSS fallback")
        _PRAW = None
        return None


# ── Public entrypoint ────────────────────────────────────────────────────────

def fetch(subreddits: Iterable[str] | None = None,
          limit_per_sub: int = 30) -> list[dict]:
    """Return normalised post dicts from each subreddit. Skips low-engagement
    posts (score < ENGAGEMENT_FILTER_REDDIT_SCORE OR comments <
    ENGAGEMENT_FILTER_REDDIT_COMMENTS). Engagement filter is applied per the
    spec to reduce noise."""
    subs = list(subreddits or config.SUBREDDITS)
    reddit = _get_praw()
    fetcher = _fetch_via_praw if reddit else _fetch_via_rss
    out: list[dict] = []
    for i, sub in enumerate(subs):
        if not reddit and i:
            time.sleep(_RSS_DELAY_S)
        try:
            posts = fetcher(sub, limit_per_sub)
        except Exception:
            logger.exception("Reddit fetch failed for r/%s", sub)
            continue
        out.extend(posts)
    logger.info("Reddit: %d posts across %d subs", len(out), len(subs))
    return out


# ── PRAW path ────────────────────────────────────────────────────────────────

def _fetch_via_praw(sub: str, limit: int) -> list[dict]:
    reddit = _get_praw()
    if reddit is None:
        return []
    rows: list[dict] = []
    sr = reddit.subreddit(sub)
    # hot + new combined, dedup by id
    seen: set[str] = set()
    for listing in (sr.hot(limit=limit), sr.new(limit=limit // 2)):
        for s in listing:
            if s.id in seen:
                continue
            seen.add(s.id)
            if not _passes_engagement(s.score, s.num_comments):
                continue
            created = dt.datetime.fromtimestamp(getattr(s, "created_utc", 0),
                                                tz=dt.timezone.utc)
            text = (s.title or "") + ("\n" + s.selftext if s.selftext else "")
            rows.append({
                "source": "reddit",
                "source_name": f"r/{sub}",
                "text": text,
                "url": f"https://www.reddit.com{s.permalink}",
                "author": str(s.author) if s.author else None,
                "engagement_score": float(s.score + s.num_comments),
                "created_at": created,
                "is_ng_native": sub in config.NG_NATIVE_SUBREDDITS,
                # extras (not persisted but useful for upstream callers)
                "score": s.score,
                "num_comments": s.num_comments,
                "upvote_ratio": getattr(s, "upvote_ratio", None),
            })
    return rows


# ── Public RSS fallback ──────────────────────────────────────────────────────

def _fetch_via_rss(sub: str, limit: int) -> list[dict]:
    """Newest posts via /r/<sub>/new/.rss. RSS carries no score or comment
    count, so the engagement filter is skipped and engagement_score is 0."""
    url = f"https://www.reddit.com/r/{sub}/new/.rss?limit={limit}"
    r = requests.get(url, headers=_RSS_HEADERS, timeout=15)
    if r.status_code == 429:
        time.sleep(_RSS_DELAY_S * 2)
        r = requests.get(url, headers=_RSS_HEADERS, timeout=15)
    if r.status_code == 429:
        logger.warning("Reddit RSS rate-limited on r/%s; skipping", sub)
        return []
    if r.status_code != 200:
        logger.warning("Reddit RSS %s for r/%s", r.status_code, sub)
        return []
    parsed = feedparser.parse(r.content)
    rows: list[dict] = []
    for e in parsed.entries[:limit]:
        st = getattr(e, "updated_parsed", None) or getattr(e, "published_parsed", None)
        created = (dt.datetime.fromtimestamp(calendar.timegm(st), tz=dt.timezone.utc)
                   if st else dt.datetime.now(dt.timezone.utc))
        body = ""
        if getattr(e, "content", None):
            body = _TAG_RE.sub(" ", e.content[0].get("value", ""))
            body = " ".join(body.replace("&#32;", " ").split())
            # Drop Reddit's "submitted by /u/x [link] [comments]" footer.
            body = body.split(" submitted by ")[0].strip()
        title = (getattr(e, "title", "") or "").strip()
        rows.append({
            "source": "reddit",
            "source_name": f"r/{sub}",
            "text": title + ("\n" + body if body else ""),
            "url": getattr(e, "link", None),
            "author": (getattr(e, "author", "") or "").replace("/u/", "") or None,
            "engagement_score": 0.0,
            "created_at": created,
            "is_ng_native": sub in config.NG_NATIVE_SUBREDDITS,
        })
    return rows


def _passes_engagement(score: int, num_comments: int) -> bool:
    return (score >= config.ENGAGEMENT_FILTER_REDDIT_SCORE
            and num_comments >= config.ENGAGEMENT_FILTER_REDDIT_COMMENTS)
