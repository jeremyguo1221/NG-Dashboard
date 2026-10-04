"""Global gas benchmarks + EIA monthly fundamentals for the FUND tab.

Sources (all free):
    yfinance  — NG=F (Henry Hub front month), TTF=F (Dutch TTF, EUR/MWh),
                JKM=F (Platts JKM LNG, $/MMBtu), EURUSD=X
    EIA API v2 — daily Henry Hub spot (RNGWHHD) and monthly series:
                dry production, LNG exports, electric-power consumption
                ("power burn"), pipeline imports from Canada

Everything is cached in-process (prices 15 min, EIA 6 h) so tab remounts and
the ticker tape don't re-hit the APIs. `cached_global()` returns whatever is
already cached without fetching, for callers that must never block.

Not covered: Baker Hughes rig counts — the publisher blocks scripted access
and no free mirror publishes them in a parsable form.
"""
from __future__ import annotations

import calendar
import logging
import threading
import time

import pandas as pd
import requests

import config

try:
    import yfinance as yf
except Exception:
    yf = None

logger = logging.getLogger(__name__)

# 1 MWh = 3.412142 MMBtu
MMBTU_PER_MWH = 3.412142

_PRICE_TTL_S = 15 * 60
_EIA_TTL_S = 6 * 3600
_EIA_V2 = "https://api.eia.gov/v2/{route}/data/"

# key → (route, series id, label)
EIA_MONTHLY = {
    "dry_prod":    ("natural-gas/prod/sum",  "N9070US2", "Dry gas production"),
    "lng_exports": ("natural-gas/move/expc", "N9133US2", "LNG exports"),
    "power_burn":  ("natural-gas/cons/sum",  "N3045US2", "Power burn"),
    "canada_imp":  ("natural-gas/move/impc", "N9102CN2", "Pipeline imports from Canada"),
}

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: str, ttl: float, fn):
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    if val is not None and not (isinstance(val, (pd.DataFrame, pd.Series, dict)) and len(val) == 0):
        with _lock:
            _cache[key] = (time.time(), val)
    return val


# ── Global benchmarks ────────────────────────────────────────────────────────

def _closes(ticker: str, period: str) -> pd.Series:
    if yf is None:
        return pd.Series(dtype=float)
    try:
        h = yf.Ticker(ticker).history(period=period, interval="1d")
    except Exception:
        logger.exception("yfinance history failed for %s", ticker)
        return pd.Series(dtype=float)
    if h is None or h.empty:
        return pd.Series(dtype=float)
    s = h["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s


def fetch_global_benchmarks(period: str = "1y") -> pd.DataFrame:
    """Daily closes in $/MMBtu: columns HH, TTF, JKM (+ TTF_EUR_MWH, EURUSD)."""
    def build():
        hh = _closes("NG=F", period)
        ttf = _closes("TTF=F", period)
        jkm = _closes("JKM=F", period)
        fx = _closes("EURUSD=X", period)
        df = pd.DataFrame({"HH": hh, "TTF_EUR_MWH": ttf, "JKM": jkm, "EURUSD": fx})
        if df.empty:
            return df
        df = df.sort_index()
        df["EURUSD"] = df["EURUSD"].ffill()
        df["TTF"] = df["TTF_EUR_MWH"] * df["EURUSD"] / MMBTU_PER_MWH
        return df.dropna(how="all", subset=["HH", "TTF", "JKM"])
    return _cached(f"global:{period}", _PRICE_TTL_S, build)


def cached_global() -> pd.DataFrame | None:
    """Non-blocking: the last fetched 1y benchmark frame, or None."""
    with _lock:
        hit = _cache.get("global:1y")
    return hit[1] if hit else None


def latest_quotes(df: pd.DataFrame) -> dict:
    """{col: (last, change_vs_prev)} for HH/TTF/JKM, using each series' own
    last two valid prints (the markets trade on different calendars)."""
    out = {}
    for col in ("HH", "TTF", "JKM"):
        s = df[col].dropna() if col in df else pd.Series(dtype=float)
        if len(s) >= 2:
            out[col] = (float(s.iloc[-1]), float(s.iloc[-1] - s.iloc[-2]))
        elif len(s) == 1:
            out[col] = (float(s.iloc[-1]), 0.0)
    return out


# ── EIA ──────────────────────────────────────────────────────────────────────

def _eia_series(route: str, series: str, frequency: str, start: str,
                api_key: str | None = None) -> pd.Series:
    key = api_key or config.EIA_API_KEY_FALLBACK
    params = {
        "api_key": key, "frequency": frequency, "data[0]": "value",
        "facets[series][]": series, "start": start,
        "sort[0][column]": "period", "sort[0][direction]": "asc",
        "length": 5000,
    }
    try:
        r = requests.get(_EIA_V2.format(route=route), params=params, timeout=30)
        r.raise_for_status()
        rows = r.json()["response"]["data"]
    except Exception:
        logger.exception("EIA fetch failed for %s/%s", route, series)
        return pd.Series(dtype=float)
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(r["period"]): float(r["value"])
                   for r in rows if r.get("value") is not None})
    return s.sort_index()


def fetch_hh_spot(days: int = 400, api_key: str | None = None) -> pd.Series:
    """Daily Henry Hub spot, $/MMBtu."""
    start = (pd.Timestamp.today() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    return _cached("hh_spot", _EIA_TTL_S,
                   lambda: _eia_series("natural-gas/pri/fut", "RNGWHHD", "daily",
                                       start, api_key))


def _mmcf_month_to_bcfd(s: pd.Series) -> pd.Series:
    days = [calendar.monthrange(ts.year, ts.month)[1] for ts in s.index]
    return s / 1000.0 / pd.Series(days, index=s.index)


def fetch_monthly_fundamentals(years: int = 6, api_key: str | None = None) -> pd.DataFrame:
    """Monthly Bcf/d for each EIA_MONTHLY series, indexed by month start."""
    def build():
        start = f"{pd.Timestamp.today().year - years}-01"
        cols = {}
        for key, (route, series, _label) in EIA_MONTHLY.items():
            s = _eia_series(route, series, "monthly", start, api_key)
            if not s.empty:
                cols[key] = _mmcf_month_to_bcfd(s)
        return pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()
    return _cached("eia_monthly", _EIA_TTL_S, build)


def yoy(s: pd.Series) -> tuple[float, float, pd.Timestamp] | None:
    """(latest, change vs same month last year, latest month) for a monthly series."""
    s = s.dropna()
    if s.empty:
        return None
    last_ts = s.index[-1]
    prior = s.get(last_ts - pd.DateOffset(years=1))
    return float(s.iloc[-1]), (float(s.iloc[-1] - prior) if prior is not None else float("nan")), last_ts


# ── Curve strips ─────────────────────────────────────────────────────────────

def curve_strips(records: list[dict], today: pd.Timestamp | None = None) -> dict:
    """Strip averages and key spreads from the Curve tab's contract records
    (each {label: 'Nov26', price}). Returns {name: value} in $/MMBtu; a strip
    is omitted if any of its months is missing."""
    today = today or pd.Timestamp.today()
    px: dict[tuple[int, int], float] = {}
    for r in records or []:
        try:
            m = pd.to_datetime(r["label"], format="%b%y")
        except Exception:
            continue
        if r.get("price") is not None and r["price"] == r["price"]:
            px[(m.year, m.month)] = float(r["price"])
    if not px:
        return {}

    def avg(months):
        vals = [px.get(ym) for ym in months]
        return None if any(v is None for v in vals) else sum(vals) / len(vals)

    first = min(px)
    # Next winter = the first Nov–Mar window that starts at or after the front month.
    wy = first[0] if first[1] <= 11 else first[0] + 1
    if first[1] <= 3:      # already inside a winter → that winter started last year
        wy = first[0] - 1
    winter1 = [(wy, 11), (wy, 12)] + [(wy + 1, m) for m in (1, 2, 3)]
    summer1 = [(wy + 1, m) for m in range(4, 11)]
    winter2 = [(wy + 1, 11), (wy + 1, 12)] + [(wy + 2, m) for m in (1, 2, 3)]
    cal = [(today.year + 1, m) for m in range(1, 13)]

    out = {}
    w1, s1, w2, c1 = avg(winter1), avg(summer1), avg(winter2), avg(cal)
    if w1 is not None:
        out[f"WINTER {wy % 100:02d}/{(wy + 1) % 100:02d}"] = w1
    if s1 is not None:
        out[f"SUMMER {(wy + 1) % 100:02d}"] = s1
    if w2 is not None:
        out[f"WINTER {(wy + 1) % 100:02d}/{(wy + 2) % 100:02d}"] = w2
    if c1 is not None:
        out[f"CAL {(today.year + 1) % 100:02d}"] = c1
    if w1 is not None and s1 is not None:
        out["WINTER − SUMMER"] = w1 - s1
    h, j = px.get((wy + 1, 3)), px.get((wy + 1, 4))
    if h is not None and j is not None:
        out[f"MAR/APR {(wy + 1) % 100:02d} (H/J)"] = h - j
    v, f = px.get((wy + 1, 10)), px.get((wy + 2, 1))
    if v is not None and f is not None:
        out[f"OCT {(wy + 1) % 100:02d}/JAN {(wy + 2) % 100:02d} (V/F)"] = v - f
    return out
