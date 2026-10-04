"""News tab — NG-focused FinBERT sentiment dashboard.

Layout:
    ┌─────────┬──────────────────────────┬──────────────────────────┐
    │ Filters │  TOP NEWS headline feed  │  Sentiment monitor table │
    │         │  (news + reddit + bsky)  │  (sortable, click a row) │
    ├─────────┴──────────────────────────┴──────────────────────────┤
    │  Detail panel for selected ticker: score / sources / volume / │
    │  top bull + top bear posts                                    │
    └───────────────────────────────────────────────────────────────┘

All data is read from the SQLAlchemy DB written by scheduler.py. The tab does
no scraping or scoring itself.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections import Counter

import plotly.graph_objects as go
import pytz
from dash import dcc, html, Input, Output, State, dash_table, no_update
import dash_bootstrap_components as dbc

import config
from data import sentiment_db
from utils.theme import COLORS, FONT_MONO, plotly_layout, term_panel

logger = logging.getLogger(__name__)

_WINDOW_HOURS = {"1h": 1, "4h": 4, "24h": 24}
_NY = pytz.timezone("America/New_York")

_TABLE_COLUMNS = [
    {"name": "Ticker",     "id": "ticker"},
    {"name": "Composite",  "id": "composite_signal", "type": "numeric",
     "format": {"specifier": "+.1f"}},
    {"name": "Sentiment",  "id": "sentiment_score", "type": "numeric",
     "format": {"specifier": "+.1f"}},
    {"name": "Mentions",   "id": "mention_volume", "type": "numeric"},
    {"name": "Bull/Bear",  "id": "bull_bear_ratio_pct", "type": "numeric",
     "format": {"specifier": ".0%"}},
    {"name": "Velocity",   "id": "sentiment_velocity", "type": "numeric",
     "format": {"specifier": "+.1f"}},
]


# ── Layout ───────────────────────────────────────────────────────────────────

def layout():
    return html.Div([
        dcc.Interval(id="sentiment-refresh",
                     interval=config.SENTIMENT_DASHBOARD_REFRESH_MS),
        dcc.Store(id="sentiment-selected-ticker", storage_type="memory"),

        dbc.Row([
            dbc.Col(term_panel("Filters", _sidebar()), width=2),
            dbc.Col(term_panel(
                "Top News — Natural Gas",
                html.Div(id="news-headlines", className="headline-list"),
                right=html.Span(id="news-headline-count"),
            ), width=5),
            dbc.Col(term_panel(
                "Sentiment Monitor",
                html.Div(id="sentiment-header", className="sent-header"),
                dash_table.DataTable(
                    id="sentiment-table",
                    columns=_TABLE_COLUMNS,
                    sort_action="native",
                    row_selectable="single",
                    page_action="none",
                    cell_selectable=True,
                    fixed_rows={"headers": True},
                    style_table={"height": "590px", "overflowY": "auto"},
                    style_cell={"backgroundColor": COLORS["PANEL"],
                                "color": COLORS["TEXT"],
                                "border": "none",
                                "borderBottom": f"1px solid {COLORS['GRID']}",
                                "fontFamily": FONT_MONO,
                                "fontSize": "12px", "padding": "3px 6px",
                                "textAlign": "right"},
                    style_cell_conditional=[
                        {"if": {"column_id": "ticker"}, "textAlign": "left",
                         "fontWeight": "700", "color": COLORS["AMBER"]},
                    ],
                    style_header={"backgroundColor": "#141414",
                                  "color": COLORS["AMBER"],
                                  "fontWeight": "700",
                                  "textTransform": "uppercase",
                                  "border": "none",
                                  "borderBottom": f"1px solid {COLORS['AMBER']}"},
                ),
            ), width=5),
        ], className="g-1"),
        html.Div(id="sentiment-detail-panel", style={"marginTop": "4px"}),
    ])


def _sidebar():
    label = {"className": "side-label"}
    return html.Div([
        html.Div("WINDOW", **label),
        dcc.RadioItems(id="sentiment-window",
                       options=[{"label": w.upper(), "value": w}
                                for w in ("1h", "4h", "24h")],
                       value="24h", className="term-radio",
                       inputStyle={"marginRight": "4px"},
                       labelStyle={"display": "inline-block", "marginRight": "10px"}),
        html.Div("MIN MENTIONS", **label),
        dcc.Slider(id="sentiment-min-mentions", min=0, max=50, step=1,
                   value=0, marks={0: "0", 25: "25", 50: "50"}),
        html.Div("SOURCES", **label),
        dcc.Checklist(id="sentiment-sources",
                      options=[{"label": s.upper(), "value": s} for s in
                               ("reddit", "news", "stocktwits", "bluesky")],
                      value=["reddit", "news", "stocktwits", "bluesky"],
                      className="term-check",
                      inputStyle={"marginRight": "6px"},
                      labelStyle={"display": "block"}),
        html.Div([
            html.Div("LEGEND", **label),
            html.Div([html.Span("▲ ", style={"color": COLORS["BULL"]}), "FinBERT bullish"]),
            html.Div([html.Span("▼ ", style={"color": COLORS["BEAR"]}), "FinBERT bearish"]),
            html.Div([html.Span("■ ", style={"color": COLORS["MUTED"]}), "neutral"]),
        ], className="side-legend"),
    ])


# ── Snapshot hook (called by utils/snapshot.py) ──────────────────────────────

def figures_for_snapshot(stores: dict):
    """Build a static set of figures for the PDF snapshot. Reads directly from
    the DB; ignores `stores` (the new pipeline persists everything itself)."""
    figures = []
    try:
        latest = sentiment_db.get_latest_signals("1h")
    except Exception:
        return figures
    if not latest:
        return figures
    figures.append(("Sentiment — Composite by ticker (1h)",
                    _build_table_figure(latest)))
    # Top mover detail
    movers = sorted(latest, key=lambda r: abs(r["composite_signal"] or 0),
                    reverse=True)[:3]
    for m in movers:
        history = sentiment_db.get_history(m["ticker"], "1h", hours=24)
        figures.append((f"Sentiment — {m['ticker']} (24h)",
                        _build_score_chart(history, m["ticker"])))
    return figures


def _build_table_figure(rows: list[dict]) -> go.Figure:
    """A bar chart of composite signal per ticker — used for PDF snapshots."""
    rows_sorted = sorted(rows, key=lambda r: r["composite_signal"] or 0)
    tickers = [r["ticker"] for r in rows_sorted]
    composites = [r["composite_signal"] or 0 for r in rows_sorted]
    colors = [_signal_color(c) for c in composites]
    fig = go.Figure(go.Bar(x=composites, y=tickers, orientation="h",
                           marker_color=colors))
    fig.update_layout(**plotly_layout(title=None, height=400, showlegend=False))
    fig.update_xaxes(range=[-100, 100], title="Composite signal")
    return fig


# ── Callbacks ────────────────────────────────────────────────────────────────

def register_callbacks(app):

    @app.callback(
        Output("news-headlines", "children"),
        Output("news-headline-count", "children"),
        Input("sentiment-refresh", "n_intervals"),
        Input("sentiment-sources", "value"),
    )
    def update_headlines(_n, sources):
        # StockTwits / Bluesky are chatter, not headlines — keep them out.
        wanted = [x for x in (sources or []) if x in ("news", "reddit")]
        if not wanted:
            return _muted("(no headline sources selected)"), "0"
        try:
            heads = sentiment_db.get_recent_headlines(limit=80, sources=wanted)
        except Exception:
            logger.exception("headline load failed")
            heads = []
        if not heads:
            return _muted("Waiting for the first scrape (runs ~20s after start, then every 15 min)…"), "0"
        return [_headline_row(h) for h in heads], f"{len(heads)} ITEMS"

    @app.callback(
        Output("sentiment-header", "children"),
        Output("sentiment-table", "data"),
        Output("sentiment-table", "style_data_conditional"),
        Input("sentiment-refresh", "n_intervals"),
        Input("sentiment-window", "value"),
        Input("sentiment-min-mentions", "value"),
    )
    def update_table(_n, window, min_mentions):
        try:
            rows = sentiment_db.get_latest_signals(window)
            last_updated = sentiment_db.get_last_updated()
        except Exception:
            logger.exception("update_table DB read failed")
            rows, last_updated = [], None

        # Order: tracked watchlist first (so missing tickers still show as 0),
        # then any extra tickers discovered in the wild.
        by_ticker = {r["ticker"]: r for r in rows}
        ordered = []
        for t in config.TICKERS_TO_TRACK:
            ordered.append(by_ticker.get(t) or _empty_row(t, window))
        for t, r in by_ticker.items():
            if t not in config.TICKERS_TO_TRACK:
                ordered.append(r)
        ordered = [r for r in ordered
                   if (r["mention_volume"] or 0) >= (min_mentions or 0)]
        # Most-discussed first, like a terminal "most active" monitor.
        ordered.sort(key=lambda r: (-(r["mention_volume"] or 0),
                                    -abs(r["composite_signal"] or 0)))

        table_data = [_to_table_row(r) for r in ordered]
        style = _row_style_for(ordered)
        header = _build_header(last_updated, window, len(ordered))
        return header, table_data, style

    @app.callback(
        Output("sentiment-selected-ticker", "data"),
        Input("sentiment-table", "selected_rows"),
        Input("sentiment-table", "active_cell"),
        State("sentiment-table", "data"),
    )
    def store_selection(selected_rows, active_cell, data):
        if not data:
            return no_update
        idx = None
        if selected_rows:
            idx = selected_rows[0]
        elif active_cell and "row" in active_cell:
            idx = active_cell["row"]
        if idx is None or idx >= len(data):
            return no_update
        return data[idx]["ticker"]

    @app.callback(
        Output("sentiment-detail-panel", "children"),
        Input("sentiment-selected-ticker", "data"),
        Input("sentiment-window", "value"),
        Input("sentiment-sources", "value"),
        Input("sentiment-refresh", "n_intervals"),
    )
    def update_detail(ticker, window, sources, _n):
        if not ticker:
            return html.Div("SELECT A TICKER IN THE SENTIMENT MONITOR TO DRILL IN",
                            className="term-hint")
        try:
            history = sentiment_db.get_history(ticker, window, hours=24)
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
                hours=_WINDOW_HOURS.get(window, 24))
            posts = sentiment_db.get_posts_for_ticker(ticker, since, limit=200)
        except Exception:
            logger.exception("detail load failed for %s", ticker)
            history, posts = [], []

        if sources:
            posts = [p for p in posts if p["source"] in set(sources)]

        return _build_detail_panel(ticker, history, posts)


# ── Detail panel builders ────────────────────────────────────────────────────

def _build_detail_panel(ticker, history, posts):
    return term_panel(
        f"{ticker} — Sentiment Detail",
        dbc.Row([
            dbc.Col(dcc.Graph(figure=_build_score_chart(history, ticker),
                              config={"displayModeBar": False}), width=5),
            dbc.Col(dcc.Graph(figure=_build_volume_chart(history, ticker),
                              config={"displayModeBar": False}), width=4),
            dbc.Col(dcc.Graph(figure=_build_source_pie(posts),
                              config={"displayModeBar": False}), width=3),
        ], className="g-1"),
        dbc.Row([
            dbc.Col(_build_post_list(posts, kind="bull"), width=6),
            dbc.Col(_build_post_list(posts, kind="bear"), width=6),
        ], className="g-1", style={"marginTop": "4px"}),
        right=f"{len(posts)} POSTS",
    )


def _build_score_chart(history, ticker) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**plotly_layout(
        title=f"Sentiment score — {ticker} (24h history)",
        height=260, showlegend=False))
    fig.update_yaxes(range=[-100, 100])
    if not history:
        return fig
    xs = [h["computed_at"] for h in history]
    ys = [h["sentiment_score"] for h in history]
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers",
                             line={"color": COLORS["BLUE"], "width": 2},
                             marker={"size": 5}))
    # 1h rolling mean overlay
    if len(ys) >= 3:
        rolling = _rolling_mean(ys, window=3)
        fig.add_trace(go.Scatter(x=xs, y=rolling, mode="lines",
                                 line={"color": COLORS["WARN"], "width": 1,
                                       "dash": "dot"}, name="1h roll"))
    return fig


def _build_volume_chart(history, ticker) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title=f"Mention volume — {ticker}",
                                       height=260, showlegend=False))
    if not history:
        return fig
    xs = [h["computed_at"] for h in history]
    ys = [h["mention_volume"] for h in history]
    fig.add_trace(go.Bar(x=xs, y=ys, marker_color=COLORS["BLUE"]))
    return fig


def _build_source_pie(posts) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Sources", height=260,
                                       showlegend=True))
    if not posts:
        return fig
    counts = Counter(p["source"] for p in posts)
    labels = list(counts.keys())
    values = [counts[k] for k in labels]
    palette = {"reddit": COLORS["ORANGE"], "news": COLORS["BLUE"],
               "stocktwits": COLORS["BULL"], "bluesky": COLORS["WARN"]}
    fig.add_trace(go.Pie(labels=labels, values=values, hole=0.45,
                         marker_colors=[palette.get(l, COLORS["MUTED"])
                                        for l in labels]))
    return fig


def _build_post_list(posts, kind: str):
    """kind = 'bull' shows top-5 most positive; 'bear' shows top-5 most
    negative."""
    title = "▲ TOP BULLISH" if kind == "bull" else "▼ TOP BEARISH"
    color = COLORS["BULL"] if kind == "bull" else COLORS["BEAR"]
    if not posts:
        body = html.Div("(no posts in window)",
                        style={"color": COLORS["MUTED"],
                               "fontStyle": "italic", "fontSize": "12px"})
        return html.Div([html.Div(title, style={"fontWeight": "700",
                                                "color": color,
                                                "marginBottom": "4px"}), body])

    def _score(p):
        pos = p.get("positive") or 0.0
        neg = p.get("negative") or 0.0
        return (pos - neg) if kind == "bull" else (neg - pos)

    candidates = [p for p in posts if p.get("positive") is not None]
    ranked = sorted(candidates, key=_score, reverse=True)[:5]
    items = []
    for p in ranked:
        snippet = (p["text"] or "")[:140].replace("\n", " ")
        if p.get("url"):
            link = html.A(snippet, href=p["url"], target="_blank",
                          style={"color": COLORS["TEXT"],
                                 "textDecoration": "none"})
        else:
            link = html.Span(snippet)
        meta = f"  {p['source_name'] or p['source']}"
        items.append(html.Div([
            html.Span((p['source_name'] or p['source'] or "").upper()[:12],
                      className="hl-src"),
            link,
        ], className="hl-row"))
    return html.Div([
        html.Div(title, className="post-list-title", style={"color": color}),
        html.Div(items),
    ])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_header(last_updated, window, n_rows):
    ts_text = "—"
    if last_updated:
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=dt.timezone.utc)
        ts_text = last_updated.astimezone().strftime("%H:%M:%S")
    return [
        html.Span([
            html.Span("UPD ", className="k"), html.Span(ts_text),
            html.Span("  WIN ", className="k"), html.Span(window.upper()),
            html.Span("  TKRS ", className="k"), html.Span(str(n_rows)),
        ]),
        html.Span("AUTO 60S", className="k"),
    ]


def _to_table_row(r) -> dict:
    bb = r.get("bull_bear_ratio")
    return {
        "ticker": r["ticker"],
        "composite_signal": _round_or_none(r["composite_signal"], 1),
        "sentiment_score":  _round_or_none(r["sentiment_score"], 1),
        "mention_volume":   int(r["mention_volume"] or 0),
        "bull_bear_ratio_pct": (None if bb is None else round(bb, 3)),
        "sentiment_velocity": _round_or_none(r.get("sentiment_velocity"), 1),
    }


def _row_style_for(rows: list[dict]) -> list[dict]:
    styles: list[dict] = []
    for col in ("composite_signal", "sentiment_score", "sentiment_velocity"):
        styles.append({"if": {"filter_query": f"{{{col}}} > 0", "column_id": col},
                       "color": COLORS["BULL"]})
        styles.append({"if": {"filter_query": f"{{{col}}} < 0", "column_id": col},
                       "color": COLORS["BEAR"]})
    styles.append({"if": {"filter_query": "{mention_volume} = 0"},
                   "color": COLORS["MUTED"]})
    styles.append({"if": {"state": "selected"},
                   "backgroundColor": "#1d1406", "border": f"1px solid {COLORS['AMBER']}"})
    return styles


def _headline_row(h: dict):
    ts = h.get("created_at")
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    if ts:
        local = ts.astimezone(_NY)
        today = dt.datetime.now(_NY).date()
        stamp = local.strftime("%H:%M") if local.date() == today else local.strftime("%b%d").upper()
    else:
        stamp = "--:--"
    label = h.get("label")
    mark, color = {"positive": ("▲", COLORS["BULL"]),
                   "negative": ("▼", COLORS["BEAR"])}.get(label, ("■", COLORS["MUTED"]))
    title = h.get("title") or ""
    if len(title) > 160:
        title = title[:157] + "…"
    text = (html.A(title, href=h["url"], target="_blank", className="hl-title")
            if h.get("url") else html.Span(title, className="hl-title"))
    return html.Div([
        html.Span(stamp, className="hl-time"),
        html.Span(mark, className="hl-mark", style={"color": color}),
        html.Span(short_source(h.get("source_name") or h.get("source")), className="hl-src"),
        text,
    ], className="hl-row")


def short_source(name: str) -> str:
    name = (name or "").strip()
    abbrev = {"Google News NG": "GNEWS", "Google News LNG/HH": "GNEWS",
              "EIA Today in Energy": "EIA", "Seeking Alpha": "SA",
              "CNBC Energy": "CNBC", "OilPrice": "OILPX", "Rigzone": "RIGZN"}
    return abbrev.get(name, name.upper())[:12]


def _muted(text: str):
    return html.Div(text, className="term-hint")


def _empty_row(ticker, window) -> dict:
    return {
        "ticker": ticker, "window": window,
        "composite_signal": 0.0, "sentiment_score": 0.0,
        "mention_volume": 0, "bull_bear_ratio": None,
        "sentiment_velocity": None, "computed_at": None,
    }


def _round_or_none(v, ndigits: int):
    if v is None:
        return None
    return round(float(v), ndigits)


def _signal_color(value: float) -> str:
    if value is None:
        return COLORS["MUTED"]
    if value > 10:
        return COLORS["BULL"]
    if value < -10:
        return COLORS["BEAR"]
    return COLORS["MUTED"]


def _rolling_mean(xs: list[float], window: int) -> list[float]:
    if window <= 1 or not xs:
        return list(xs)
    out = []
    for i in range(len(xs)):
        lo = max(0, i - window + 1)
        chunk = xs[lo: i + 1]
        out.append(sum(chunk) / len(chunk))
    return out
