"""NG Trading Intelligence Dashboard — single-file Plotly Dash app.

Run with: python app.py
Then open http://127.0.0.1:8055
"""
from __future__ import annotations

import datetime as dt
import math

import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update
import dash_bootstrap_components as dbc
import pytz

import config
from utils.theme import COLORS
from data import futures as fdata
from data import fundamentals as fdata_fund
from data import sentiment_db, finbert_scorer

from tabs import storage as tab_storage
from tabs import weather as tab_weather
from tabs import news as tab_news
from tabs import positioning as tab_positioning
from tabs import curve as tab_curve
from tabs import fundamentals as tab_fund
from tabs import regional_storage as tab_regional_storage
from utils.snapshot import build_pdf_snapshot
import scheduler as sentiment_scheduler

NY = pytz.timezone("America/New_York")

app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.CYBORG,
        "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap",
    ],
    suppress_callback_exceptions=True,
    title="NG Terminal",
)
server = app.server


def _quote_cell(title, value_id, sub_id=None):
    kids = [html.Div(title, className="tb-label"),
            html.Div("—", id=value_id, className="tb-value")]
    if sub_id:
        kids.append(html.Div("", id=sub_id, className="tb-sub"))
    return html.Div(kids, className="tb-cell")


def build_navbar():
    """Bloomberg-style top bar: security box, dense quote cells, clock."""
    return html.Div([
        html.Div([
            html.Span("NG1", className="tb-sec"),
            html.Span("<CMDTY>", className="tb-yk"),
        ], className="tb-secbox"),
        html.Div([
            html.Div("NATURAL GAS (NYMEX HENRY HUB)", className="tb-name"),
            html.Div("FRONT MONTH · USD/MMBTU", className="tb-sub"),
        ], className="tb-cell tb-namecell"),
        html.Div([
            html.Div("LAST", className="tb-label"),
            html.Div("—", id="nav-price", className="tb-value-big"),
        ], className="tb-cell"),
        html.Div([
            html.Div("CHG", className="tb-label"),
            html.Div("", id="nav-price-change", className="tb-value"),
        ], className="tb-cell"),
        _quote_cell("STOR vs 5Y", "nav-storage"),
        _quote_cell("CFTC MM NET", "nav-mm-net", "nav-mm-net-chg"),
        html.Div([
            html.Div("SENT", className="tb-label"),
            html.Span(id="nav-sentiment-dot", className="sentiment-dot",
                      style={"color": COLORS["MUTED"], "backgroundColor": COLORS["MUTED"]}),
        ], className="tb-cell"),
        html.Div(id="nav-alert", className="tb-alert"),
        html.Div(style={"flex": "1"}),
        html.Div([
            html.Div("NEW YORK", className="tb-label"),
            html.Div("—", id="nav-clock", className="tb-value tb-clock"),
        ], className="tb-cell"),
        html.Button("SETTINGS", id="settings-open", className="term-btn"),
        html.Button("SNAPSHOT", id="snapshot-btn", className="term-btn term-btn-amber"),
    ], className="term-topbar")


# Function-key style tab bar: (tab_id, number, mnemonic, description)
_TABS = [
    ("storage",     "1", "STOR", "EIA Storage"),
    ("weather",     "2", "WTHR", "Weather / HDD"),
    ("news",        "3", "NEWS", "News & Sentiment"),
    ("positioning", "4", "POSN", "CFTC Positioning"),
    ("curve",       "5", "CURV", "Forward Curve"),
    ("fund",        "6", "FUND", "Global Gas & Fundamentals"),
]
_CMD_ALIASES = {
    "storage": {"STORAGE", "EIA", "INV"},
    "weather": {"WEATHER", "WX", "HDD"},
    "news": {"TOP", "N", "SENT", "CN"},
    "positioning": {"COT", "CFTC", "POS"},
    "curve": {"CURVE", "CT", "FWD", "STRIP"},
    "fund": {"FUNDAMENTALS", "TTF", "JKM", "LNG", "ARB", "PROD", "GLCO"},
}


def build_function_bar():
    return html.Div([
        dbc.Tabs(id="tabs", active_tab="storage", className="term-tabs", children=[
            dbc.Tab(label=f"{num}) {mn}", tab_id=tid,
                    label_class_name="term-tab", active_label_class_name="term-tab-active")
            for tid, num, mn, _ in _TABS
        ]),
        html.Div(id="tab-description", className="fn-desc"),
        html.Div(style={"flex": "1"}),
        html.Div([
            dcc.Input(id="cmd-input", type="text", debounce=True,
                      placeholder="1-5 or STOR NEWS POSN…", className="cmd-input",
                      autoComplete="off"),
            html.Span("<GO>", className="cmd-go"),
        ], className="cmd-box"),
    ], className="fn-bar")


def build_ticker_tape():
    return html.Div([
        html.Div("NG NEWS", className="tape-label"),
        html.Div(html.Div(id="ticker-tape", className="tape-track"),
                 className="tape-viewport"),
    ], className="term-tape")


def build_settings_modal():
    city_options = [{"label": c["name"], "value": c["name"]} for c in config.CITIES]
    default_city_values = [c["name"] for c in config.CITIES]
    return dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Settings")),
        dbc.ModalBody([
            dbc.Label("EIA API Key"),
            dbc.Input(id="settings-eia-key", type="text", placeholder="paste EIA v2 API key",
                      value=config.EIA_API_KEY_FALLBACK),
            html.Br(),
            dbc.Label("Weather refresh interval (minutes)"),
            dbc.Input(id="settings-weather-mins", type="number",
                      value=config.DEFAULT_INTERVALS["weather_ms"] // 60000, min=1),
            html.Br(),
            dbc.Label("Cities to include"),
            dbc.Checklist(id="settings-cities", options=city_options,
                          value=default_city_values, inline=True,
                          style={"maxHeight": "180px", "overflowY": "auto"}),
            html.Br(),
            dbc.Label("Sentiment tickers (one per line — restart to apply)"),
            dbc.Textarea(id="settings-sentiment-tickers",
                         value="\n".join(config.TICKERS_TO_TRACK),
                         style={"height": "100px"}),
        ]),
        dbc.ModalFooter([
            dbc.Button("Save", id="settings-save", color="success"),
            dbc.Button("Close", id="settings-close", color="secondary"),
        ]),
    ], id="settings-modal", is_open=False, size="lg")


def build_stores():
    return html.Div([
        dcc.Store(id="settings-store", storage_type="local"),
        dcc.Store(id="storage-history-store", storage_type="memory"),
        dcc.Store(id="weather-data-store", storage_type="memory"),
        dcc.Store(id="weather-yesterday-store", storage_type="local"),
        dcc.Store(id="weather-3d-ago-store", storage_type="local"),
        dcc.Store(id="cftc-history-store", storage_type="local"),
        # Global so the CFTC fetch (and the top-bar MM net) runs on every tab.
        dcc.Interval(id="cftc-global-interval",
                     interval=config.DEFAULT_INTERVALS["cftc_ms"]),
        dcc.Store(id="curve-history-store", storage_type="local"),
        dcc.Store(id="front-month-store", storage_type="memory"),
        dcc.Store(id="trajectory-store", storage_type="memory"),
        dcc.Store(id="trajectory-last-calculated", storage_type="memory"),
        dcc.Store(id="regional-storage-data", storage_type="memory"),
        # Lives here (not in the Storage tab) because the trajectory callback
        # takes it as an Input and must resolve on every tab.
        dcc.Interval(id="trajectory-interval",
                     interval=config.DEFAULT_INTERVALS["trajectory_ms"]),
        dcc.Interval(id="clock-interval", interval=config.DEFAULT_INTERVALS["clock_ms"]),
        dcc.Interval(id="navbar-price-interval",
                     interval=config.DEFAULT_INTERVALS["navbar_price_ms"]),
        # Nav sentiment dot + nav alert refresh from the sentiment DB on this
        # interval. Decoupled from the in-tab refresh so the navbar stays
        # responsive even when the News tab is closed.
        dcc.Interval(id="nav-sentiment-interval",
                     interval=config.SENTIMENT_DASHBOARD_REFRESH_MS),
        dcc.Download(id="download"),
    ])


app.layout = html.Div([
    build_navbar(),
    build_function_bar(),
    build_settings_modal(),
    build_stores(),
    html.Div(html.Div(id="tab-content"), className="dashboard-container"),
    build_ticker_tape(),
])


@app.callback(
    Output("tabs", "active_tab"),
    Output("cmd-input", "value"),
    Input("cmd-input", "value"),
    prevent_initial_call=True,
)
def run_command(cmd):
    """Terminal-style command line: `4`, `POSN`, `COT <GO>` → switch tab."""
    c = (cmd or "").strip().upper().replace("<GO>", "").strip()
    if not c:
        return no_update, no_update
    for tid, num, mn, _ in _TABS:
        if c in {num, mn, f"{num})"} or c in _CMD_ALIASES.get(tid, set()):
            return tid, ""
    return no_update, ""


@app.callback(Output("tab-description", "children"), Input("tabs", "active_tab"))
def tab_description(active):
    for tid, num, mn, desc in _TABS:
        if tid == active:
            return f"{mn} — {desc.upper()}"
    return ""


@app.callback(Output("tab-content", "children"), Input("tabs", "active_tab"))
def render_tab(active):
    if   active == "storage":     return tab_storage.layout()
    elif active == "weather":     return tab_weather.layout()
    elif active == "news":        return tab_news.layout()
    elif active == "positioning": return tab_positioning.layout()
    elif active == "curve":       return tab_curve.layout()
    elif active == "fund":        return tab_fund.layout()
    return html.Div("Unknown tab")


# ── Navbar callbacks ────────────────────────────────────────────────────────

@app.callback(Output("nav-clock", "children"), Input("clock-interval", "n_intervals"))
def update_clock(_):
    return dt.datetime.now(NY).strftime("%H:%M:%S ET")


@app.callback(
    Output("nav-price", "children"),
    Output("nav-price-change", "children"),
    Output("nav-price-change", "style"),
    Output("front-month-store", "data"),
    Input("navbar-price-interval", "n_intervals"),
)
def update_navbar_price(_):
    price, change, pct = fdata.fetch_front_month_price()
    if price is None or (isinstance(price, float) and math.isnan(price)):
        return "—", "", {"color": COLORS["MUTED"]}, no_update
    color = COLORS["BULL"] if change >= 0 else COLORS["BEAR"]
    arrow = "▲" if change >= 0 else "▼"
    change_text = f"{arrow}{change:+.3f} {pct:+.2f}%"
    return (f"{price:.3f}", change_text, {"color": color},
            {"price": price, "change": change, "pct": pct})


@app.callback(
    Output("nav-storage", "children"),
    Output("nav-storage", "style"),
    Input("storage-history-store", "data"),
)
def update_navbar_storage(data):
    if not data or "deviation" not in data:
        return "—", {"color": COLORS["MUTED"]}
    deviation = data["deviation"]
    color = COLORS["BULL"] if deviation > 0 else (COLORS["BEAR"] if deviation < 0 else COLORS["MUTED"])
    return f"{deviation:+,.0f} Bcf", {"color": color, "fontWeight": "700"}


@app.callback(
    Output("nav-sentiment-dot", "style"),
    Input("nav-sentiment-interval", "n_intervals"),
)
def update_sentiment_dot(_):
    """Show the NG_FUTURES composite signal as a colored dot.

    Positive composite → green, negative → red, low-magnitude → amber, no
    data → muted grey.
    """
    try:
        rows = sentiment_db.get_latest_signals("1h")
    except Exception:
        return {"color": COLORS["MUTED"], "backgroundColor": COLORS["MUTED"]}
    target = next((r for r in rows if r["ticker"] == config.NG_MACRO_BUCKET), None)
    if not target:
        # Fall back to mean composite across watchlist if NG_FUTURES not populated.
        tracked = [r for r in rows if r["ticker"] in set(config.TICKERS_TO_TRACK)]
        if not tracked:
            return {"color": COLORS["MUTED"], "backgroundColor": COLORS["MUTED"]}
        score = sum(r["composite_signal"] or 0 for r in tracked) / len(tracked)
    else:
        score = target["composite_signal"] or 0
    if score >  20: color = COLORS["BULL"]
    elif score < -20: color = COLORS["BEAR"]
    elif abs(score) > 0.5: color = COLORS["WARN"]
    else: color = COLORS["MUTED"]
    return {"color": color, "backgroundColor": color}


@app.callback(
    Output("nav-alert", "children"),
    Output("nav-alert", "className"),
    Input("nav-sentiment-interval", "n_intervals"),
)
def update_nav_alert(_):
    """Surface the most recent sentiment alert in the navbar pill."""
    try:
        alert = sentiment_db.get_recent_alert()
    except Exception:
        return "", "tb-alert"
    if not alert:
        return "", "tb-alert"
    # Only show alerts fired in the last 2 hours; older ones aren't fresh.
    fired = alert["fired_at"]
    if fired and fired.tzinfo is None:
        fired = fired.replace(tzinfo=dt.timezone.utc)
    if fired and (dt.datetime.now(dt.timezone.utc) - fired) > dt.timedelta(hours=2):
        return "", "tb-alert"
    text = (alert["message"] or "")[:120]
    value = alert.get("value") or 0
    css = "tb-alert " + ("flash-green" if value > 0 else "flash-red")
    return text, css


@app.callback(
    Output("nav-mm-net", "children"),
    Output("nav-mm-net", "style"),
    Output("nav-mm-net-chg", "children"),
    Input("cftc-history-store", "data"),
)
def update_navbar_mm(data):
    recs = (data or {}).get("records") or []
    if not recs:
        return "—", {"color": COLORS["MUTED"]}, ""
    last = recs[-1]["mm_net"]
    prev = recs[-2]["mm_net"] if len(recs) > 1 else last
    color = COLORS["BULL"] if last > 0 else COLORS["BEAR"]
    return (f"{last:+,.0f}", {"color": color},
            f"WoW {last - prev:+,.0f} · {recs[-1]['report_date'][:10]}")


@app.callback(
    Output("ticker-tape", "children"),
    Input("nav-sentiment-interval", "n_intervals"),
    Input("front-month-store", "data"),
    Input("storage-history-store", "data"),
    Input("cftc-history-store", "data"),
)
def update_ticker_tape(_n, front, storage, cftc_data):
    """Scrolling bottom tape: key market stats followed by latest headlines."""
    items = []

    def stat(label, value, color):
        items.append(html.Span([html.Span(label + " ", className="tape-k"),
                                html.Span(value, style={"color": color})],
                               className="tape-item"))

    if front and front.get("price") is not None:
        ch = front.get("change") or 0
        stat("NG1", f"{front['price']:.3f} {ch:+.3f} ({front.get('pct') or 0:+.2f}%)",
             COLORS["BULL"] if ch >= 0 else COLORS["BEAR"])
    if storage and storage.get("weekly_change") is not None:
        wc = storage["weekly_change"]
        stat("EIA STOR", f"{wc:+.0f} BCF WoW", COLORS["BEAR"] if wc > 0 else COLORS["BULL"])
    if storage and storage.get("deviation") is not None:
        dv = storage["deviation"]
        stat("vs 5Y", f"{dv:+,.0f} BCF", COLORS["BEAR"] if dv > 0 else COLORS["BULL"])
    # International benchmarks — only if the FUND tab already warmed the cache
    # (never block the tape on a network fetch).
    bench = fdata_fund.cached_global()
    if bench is not None and not bench.empty:
        q = fdata_fund.latest_quotes(bench)
        for col, name in (("TTF", "TTF $/MMBTU"), ("JKM", "JKM")):
            if col in q:
                last, ch = q[col]
                stat(name, f"{last:.2f} {ch:+.2f}",
                     COLORS["BULL"] if ch >= 0 else COLORS["BEAR"])
    recs = (cftc_data or {}).get("records") or []
    if recs:
        mm = recs[-1]["mm_net"]
        stat("CFTC MM NET", f"{mm:+,.0f}", COLORS["BULL"] if mm > 0 else COLORS["BEAR"])

    try:
        heads = sentiment_db.get_recent_headlines(limit=20)
    except Exception:
        heads = []
    for h in heads:
        ts = h["created_at"]
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        hhmm = ts.astimezone(NY).strftime("%H:%M") if ts else "--:--"
        color = {"positive": COLORS["BULL"], "negative": COLORS["BEAR"]}.get(
            h["label"], COLORS["TEXT"])
        items.append(html.Span([
            html.Span(hhmm + " ", className="tape-k"),
            html.Span(tab_news.short_source(h["source_name"]) + " ", className="tape-src"),
            html.Span(h["title"][:140], style={"color": color}),
        ], className="tape-item"))
    if not items:
        return html.Span("AWAITING DATA…", className="tape-item")
    # Two copies of the run so the CSS marquee loops seamlessly.
    return [html.Span(items, className="tape-run"),
            html.Span(items, className="tape-run")]


# ── Settings modal ──────────────────────────────────────────────────────────

@app.callback(
    Output("settings-modal", "is_open"),
    Input("settings-open", "n_clicks"),
    Input("settings-close", "n_clicks"),
    Input("settings-save", "n_clicks"),
    State("settings-modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_settings(open_click, close_click, save_click, is_open):
    return not is_open


@app.callback(
    Output("settings-store", "data"),
    Input("settings-save", "n_clicks"),
    State("settings-eia-key", "value"),
    State("settings-weather-mins", "value"),
    State("settings-cities", "value"),
    State("settings-sentiment-tickers", "value"),
    State("settings-store", "data"),
    prevent_initial_call=True,
)
def save_settings(_, eia_key, weather_mins, cities, sentiment_tickers, existing):
    tickers_list = [t.strip().upper() for t in
                    (sentiment_tickers or "").replace(",", "\n").split("\n")
                    if t.strip()]
    return {
        "eia_key": (eia_key or "").strip(),
        "weather_mins": int(weather_mins or 10),
        "cities": cities or [c["name"] for c in config.CITIES],
        "sentiment_tickers": tickers_list or list(config.TICKERS_TO_TRACK),
    }


# ── Snapshot PDF download ───────────────────────────────────────────────────

@app.callback(
    Output("download", "data"),
    Input("snapshot-btn", "n_clicks"),
    State("storage-history-store", "data"),
    State("weather-data-store", "data"),
    State("cftc-history-store", "data"),
    State("curve-history-store", "data"),
    State("front-month-store", "data"),
    prevent_initial_call=True,
)
def snapshot(n_clicks, storage_data, weather_data,
             cftc_data, curve_data, front_data):
    if not n_clicks:
        return no_update
    # News-tab figures now read directly from the sentiment DB inside
    # tabs/news.py::figures_for_snapshot, so no news payload is passed.
    stores = {
        "storage": storage_data, "weather": weather_data, "news": None,
        "cftc": cftc_data, "curve": curve_data, "front": front_data,
    }
    pdf_bytes = build_pdf_snapshot(stores)
    filename = f"ng-snapshot-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    return dcc.send_bytes(lambda buf: buf.write(pdf_bytes), filename)


# ── Register per-tab callbacks ──────────────────────────────────────────────

tab_storage.register_callbacks(app)
tab_weather.register_callbacks(app)
tab_news.register_callbacks(app)
tab_positioning.register_callbacks(app)
tab_curve.register_callbacks(app)
tab_fund.register_callbacks(app)
tab_regional_storage.register_callbacks(app)


# ── Sentiment pipeline startup ──────────────────────────────────────────────
# Initialise the sentiment DB, preload FinBERT, and start the background
# scheduler. Done at import time (rather than inside __main__) so it also
# fires under `gunicorn app:server` deployments.
sentiment_db.init_db()
finbert_scorer.preload()           # blocks while ~440MB downloads on first run
sentiment_scheduler.start_scheduler()


if __name__ == "__main__":
    # dev_tools_ui off: the debug badge sits on top of the ticker tape.
    app.run(debug=True, dev_tools_ui=False, host="0.0.0.0", port=8055, use_reloader=False)
