"""Tab 6: Fundamentals — global gas benchmarks, LNG arb, cash vs futures, and
EIA monthly supply/demand (production, LNG exports, power burn, Canada imports).

All data comes from data/fundamentals.py (cached), so the callbacks are cheap
after the first load.
"""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, Input, Output
import dash_bootstrap_components as dbc

from data import fundamentals as fd
from utils.theme import COLORS, plotly_layout

_REFRESH_MS = 15 * 60 * 1000

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_GRAPHS = ["fund-benchmarks", "fund-arb", "fund-cash-fut", "fund-prod",
           "fund-lng", "fund-power", "fund-canada"]


def layout():
    g = lambda i: dcc.Graph(id=i, config={"displayModeBar": False})
    return html.Div([
        dcc.Interval(id="fund-interval", interval=_REFRESH_MS),
        html.Div(id="fund-quotes", className="quote-strip"),
        dbc.Row([dbc.Col(g("fund-benchmarks"), width=8),
                 dbc.Col(g("fund-arb"), width=4)], className="g-1"),
        dbc.Row([dbc.Col(g("fund-cash-fut"), width=6),
                 dbc.Col(g("fund-prod"), width=6)], className="g-1"),
        dbc.Row([dbc.Col(g("fund-lng"), width=4),
                 dbc.Col(g("fund-power"), width=4),
                 dbc.Col(g("fund-canada"), width=4)], className="g-1"),
        html.Div("SOURCES: CME/ICE via yfinance (delayed) · EIA API v2 (monthly series lag ~2 months) · "
                 "TTF converted EUR/MWh → $/MMBtu at spot EUR/USD. Rig counts not available from a free source.",
                 className="fund-footnote"),
    ])


# ── Quote strip ──────────────────────────────────────────────────────────────

def _cell(label, value, sub=None, color=None):
    return html.Div([
        html.Div(label, className="tb-label"),
        html.Div(value, className="tb-value", style={"color": color or COLORS["TEXT"]}),
        html.Div(sub or "", className="tb-sub"),
    ], className="tb-cell")


def _chg_color(v, invert=False):
    if v is None or (isinstance(v, float) and math.isnan(v)) or v == 0:
        return COLORS["MUTED"]
    up = v > 0
    if invert:
        up = not up
    return COLORS["BULL"] if up else COLORS["BEAR"]


def _build_quotes(bench: pd.DataFrame, spot: pd.Series, monthly: pd.DataFrame):
    cells = []
    q = fd.latest_quotes(bench) if bench is not None and not bench.empty else {}
    if "HH" in q:
        last, ch = q["HH"]
        cells.append(_cell("HH FUT", f"{last:.3f}", f"{ch:+.3f}", _chg_color(ch)))
    if spot is not None and not spot.empty:
        ch = float(spot.iloc[-1] - spot.iloc[-2]) if len(spot) > 1 else 0.0
        cells.append(_cell("HH SPOT", f"{spot.iloc[-1]:.2f}",
                           f"{ch:+.2f} · {spot.index[-1]:%b%d}".upper(), _chg_color(ch)))
    if "TTF" in q:
        last, ch = q["TTF"]
        eur = bench["TTF_EUR_MWH"].dropna()
        cells.append(_cell("TTF $/MMBTU", f"{last:.2f}",
                           f"{ch:+.2f} · €{eur.iloc[-1]:.2f}/MWH" if not eur.empty else f"{ch:+.2f}",
                           _chg_color(ch)))
    if "JKM" in q:
        last, ch = q["JKM"]
        cells.append(_cell("JKM $/MMBTU", f"{last:.2f}", f"{ch:+.2f}", _chg_color(ch)))
    if "HH" in q and "TTF" in q:
        cells.append(_cell("TTF − HH", f"{q['TTF'][0] - q['HH'][0]:+.2f}", "EUROPE ARB",
                           COLORS["AMBER"]))
    if "HH" in q and "JKM" in q:
        cells.append(_cell("JKM − HH", f"{q['JKM'][0] - q['HH'][0]:+.2f}", "ASIA ARB",
                           COLORS["AMBER"]))
    labels = {"dry_prod": "DRY PROD", "lng_exports": "LNG EXP",
              "power_burn": "POWER BURN", "canada_imp": "CANADA IMP"}
    # More supply = bearish (red); more demand/exports = bullish (green).
    bearish_when_up = {"dry_prod", "canada_imp"}
    for key, label in labels.items():
        if monthly is None or key not in monthly:
            continue
        r = fd.yoy(monthly[key])
        if not r:
            continue
        last, d, ts = r
        cells.append(_cell(f"{label} BCF/D", f"{last:.1f}",
                           f"YoY {d:+.1f} · {ts:%b%y}".upper(),
                           _chg_color(d, invert=key in bearish_when_up)))
    if not cells:
        return html.Div("LOADING FUNDAMENTALS…", className="term-hint")
    return cells


# ── Figures ──────────────────────────────────────────────────────────────────

def _empty(title, height):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title=title, height=height))
    return fig


def _build_benchmarks(bench):
    fig = _empty("Global Gas Benchmarks ($/MMBtu, 1Y)", 340)
    if bench is None or bench.empty:
        return fig
    for col, name, color in (("JKM", "JKM (Asia LNG)", COLORS["PURPLE"]),
                             ("TTF", "TTF (Europe)", COLORS["BLUE"]),
                             ("HH", "Henry Hub", COLORS["AMBER"])):
        s = bench[col].dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines", name=name,
                                 line=dict(color=color, width=2.5 if col == "HH" else 1.6)))
    fig.update_yaxes(title_text="$/MMBtu")
    return fig


def _build_arb(bench):
    fig = _empty("LNG Arb: Intl − Henry Hub", 340)
    if bench is None or bench.empty:
        return fig
    b = bench.copy()
    b["HH"] = b["HH"].ffill()
    for col, name, color in (("TTF", "TTF − HH", COLORS["BLUE"]),
                             ("JKM", "JKM − HH", COLORS["PURPLE"])):
        s = (b[col] - b["HH"]).dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines", name=name,
                                 line=dict(color=color, width=1.6)))
    fig.add_hline(y=0, line_color=COLORS["BORDER"])
    fig.update_yaxes(title_text="$/MMBtu")
    return fig


def _build_cash_fut(bench, spot):
    fig = _empty("Henry Hub Cash vs Front-Month Futures (6M)", 320)
    if spot is None or spot.empty:
        return fig
    cutoff = spot.index.max() - pd.Timedelta(days=183)
    s = spot[spot.index >= cutoff]
    fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines", name="HH spot",
                             line=dict(color=COLORS["CYAN"], width=1.6)))
    if bench is not None and not bench.empty:
        f = bench["HH"].dropna()
        f = f[f.index >= cutoff]
        fig.add_trace(go.Scatter(x=f.index, y=f.values, mode="lines", name="NG1 futures",
                                 line=dict(color=COLORS["AMBER"], width=2)))
        basis = (s - f.reindex(s.index)).dropna()
        fig.add_trace(go.Bar(x=basis.index, y=basis.values, name="Cash − futures",
                             yaxis="y2", opacity=0.6,
                             marker_color=[COLORS["BULL"] if v >= 0 else COLORS["BEAR"]
                                           for v in basis.values]))
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                      color=COLORS["MUTED"], zerolinecolor=COLORS["BORDER"],
                                      title_text="Basis"))
    fig.update_layout(yaxis_title_text="$/MMBtu")
    return fig


def _build_seasonal(series: pd.Series, title: str, height=320, years=5):
    """Monthly series overlaid by calendar year; current year in amber."""
    fig = _empty(title, height)
    s = series.dropna() if series is not None else pd.Series(dtype=float)
    if s.empty:
        return fig
    yrs = sorted(set(s.index.year))[-years:]
    shades = [COLORS["BORDER"], "#4a4a4a", "#6a6a6a", COLORS["BLUE"], COLORS["AMBER"]]
    shades = shades[-len(yrs):]
    for yr, color in zip(yrs, shades):
        d = s[s.index.year == yr]
        current = yr == yrs[-1]
        fig.add_trace(go.Scatter(x=[_MONTHS[m - 1] for m in d.index.month], y=d.values,
                                 mode="lines+markers" if current else "lines", name=str(yr),
                                 line=dict(color=color, width=2.6 if current else 1.4),
                                 marker=dict(size=5)))
    fig.update_xaxes(categoryorder="array", categoryarray=_MONTHS)
    fig.update_yaxes(title_text="Bcf/d")
    return fig


def build_all(bench, spot, monthly):
    col = lambda k: monthly[k] if monthly is not None and k in monthly else None
    return (
        _build_benchmarks(bench),
        _build_arb(bench),
        _build_cash_fut(bench, spot),
        _build_seasonal(col("dry_prod"), "Dry Gas Production (Bcf/d, EIA monthly)"),
        _build_seasonal(col("lng_exports"), "LNG Exports (Bcf/d)"),
        _build_seasonal(col("power_burn"), "Power Burn (Bcf/d)"),
        _build_seasonal(col("canada_imp"), "Pipeline Imports from Canada (Bcf/d)"),
    )


def _load(settings=None):
    key = (settings or {}).get("eia_key") or None
    bench = fd.fetch_global_benchmarks()
    spot = fd.fetch_hh_spot(api_key=key)
    monthly = fd.fetch_monthly_fundamentals(api_key=key)
    return bench, spot, monthly


def figures_for_snapshot(stores):
    try:
        figs = build_all(*_load())
    except Exception:
        return []
    names = ["Global benchmarks", "LNG arb", "HH cash vs futures", "Dry production",
             "LNG exports", "Power burn", "Canada imports"]
    return [(f"Fundamentals — {n}", f) for n, f in zip(names, figs)]


def register_callbacks(app):

    @app.callback(
        Output("fund-quotes", "children"),
        *[Output(g, "figure") for g in _GRAPHS],
        Input("fund-interval", "n_intervals"),
        Input("settings-store", "data"),
    )
    def refresh(_n, settings):
        bench, spot, monthly = _load(settings)
        return (_build_quotes(bench, spot, monthly), *build_all(bench, spot, monthly))
