"""Tab 5: NYMEX natural gas forward curve, spreads, roll yield, regime."""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, Input, Output, State, no_update
import dash_bootstrap_components as dbc

import config
from data import futures
from data import fundamentals as fd
from utils.theme import COLORS, DIVERGING, plotly_layout


def layout():
    return html.Div([
        dcc.Interval(id="curve-interval", interval=config.DEFAULT_INTERVALS["futures_ms"]),
        dbc.Row([
            dbc.Col(html.Div(id="curve-regime-banner", className="term-strip"),
                    width=12),
        ]),
        html.Div(id="curve-strips", className="quote-strip"),
        dcc.Graph(id="curve-main"),
        dcc.Slider(id="curve-history-slider", min=0, max=0, step=1, value=0,
                   marks={}, tooltip={"placement": "bottom", "always_visible": False}),
        dbc.Row([
            dbc.Col(dcc.Graph(id="curve-spread-heatmap"), width=7),
            dbc.Col(dcc.Graph(id="curve-roll-yield"), width=5),
        ], style={"marginTop": "8px"}),
        dcc.Graph(id="curve-winter-summer", style={"marginTop": "8px"}),
    ])


def _curve_from_records(records):
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df["expiry"] = pd.to_datetime(df["expiry"])
    return df


def _build_main_curve(today_df, week_ago, month_ago, scrub=None):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="NYMEX NG Forward Curve (24 months)", height=420))
    if today_df.empty:
        return fig
    labels = today_df["label"].tolist()
    today_prices = today_df["price"].tolist()

    if week_ago is not None and len(week_ago) == len(today_prices):
        above = [t if (t is not None and w is not None and not math.isnan(t) and not math.isnan(w) and t >= w) else None
                 for t, w in zip(today_prices, week_ago)]
        below = [t if (t is not None and w is not None and not math.isnan(t) and not math.isnan(w) and t <  w) else None
                 for t, w in zip(today_prices, week_ago)]
        fig.add_trace(go.Scatter(x=labels, y=week_ago, mode="lines",
                                  line=dict(color=COLORS["WARN"], dash="dash", width=1.5),
                                  name="Week ago"))
        fig.add_trace(go.Scatter(x=labels, y=above, mode="lines", fill="tonexty",
                                  line=dict(color="rgba(0,0,0,0)"),
                                  fillcolor="rgba(0,255,136,0.25)", showlegend=False))
        fig.add_trace(go.Scatter(x=labels, y=below, mode="lines", fill="tonexty",
                                  line=dict(color="rgba(0,0,0,0)"),
                                  fillcolor="rgba(255,51,51,0.25)", showlegend=False))
    if month_ago is not None and len(month_ago) == len(today_prices):
        fig.add_trace(go.Scatter(x=labels, y=month_ago, mode="lines",
                                  line=dict(color=COLORS["ORANGE"], dash="dot", width=1.5),
                                  name="Month ago"))
    if scrub is not None and len(scrub) == len(today_prices):
        fig.add_trace(go.Scatter(x=labels, y=scrub, mode="lines",
                                  line=dict(color=COLORS["BLUE"], dash="dashdot", width=2),
                                  name="Historical (slider)"))
    fig.add_trace(go.Scatter(x=labels, y=today_prices, mode="lines+markers",
                              line=dict(color=COLORS["TEXT"], width=3),
                              marker=dict(size=7), name="Today"))
    fig.update_yaxes(title_text="$/MMBtu")
    return fig


def _build_spread_heatmap(today_df):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Calendar Spread Heatmap (far − near, $/MMBtu)", height=440))
    if today_df.empty:
        return fig
    labels = today_df["label"].tolist()
    prices = today_df["price"].tolist()
    n = len(prices)
    Z = np.full((n, n), np.nan)
    for i in range(n):
        for j in range(n):
            if j > i and not math.isnan(prices[i]) and not math.isnan(prices[j]):
                Z[i, j] = prices[j] - prices[i]
    # Pre-formatted labels so the empty lower triangle stays blank (not "NaN").
    text = [["" if np.isnan(v) else f"{v:.2f}" for v in row] for row in Z]
    # Positive spread (contango) = bear/red, negative (backwardation) = bull/green.
    scale = [[1 - stop, color] for stop, color in reversed(DIVERGING)]
    fig.add_trace(go.Heatmap(z=Z, x=labels, y=labels, colorscale=scale,
                              zmid=0, text=text, texttemplate="%{text}",
                              textfont={"size": 9},
                              hovertemplate="Near %{y}<br>Far %{x}<br>Spread $%{z:.2f}<extra></extra>",
                              colorbar=dict(tickfont=dict(color=COLORS["TEXT"]))))
    fig.update_xaxes(title_text="Far")
    fig.update_yaxes(title_text="Near", autorange="reversed")
    return fig


def _build_roll_yield(today_df):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Annualized Roll Yield (front 12)", height=440,
                                     showlegend=False))
    if today_df.empty or len(today_df) < 2:
        return fig
    front12 = today_df.head(12)
    prices = front12["price"].tolist()
    expiries = pd.to_datetime(front12["expiry"]).dt.date.tolist()
    yields = futures.roll_yield(prices, expiries)
    labels = front12["label"].tolist()[:len(yields)]
    colors_arr = [COLORS["BULL"] if (y is not None and not math.isnan(y) and y > 0)
                  else (COLORS["BEAR"] if y is not None and not math.isnan(y) and y < 0 else COLORS["MUTED"])
                  for y in yields]
    fig.add_trace(go.Bar(x=labels, y=yields, marker_color=colors_arr))
    fig.add_hline(y=0, line_color=COLORS["GRID"])
    fig.update_yaxes(title_text="Annualized %")
    return fig


def _build_winter_summer(today_df):
    """Seasonal strip averages side by side, front month for reference."""
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Seasonal Strips vs Front Month ($/MMBtu)",
                                      height=320, showlegend=False))
    if today_df.empty:
        return fig
    strips = fd.curve_strips(today_df[["label", "price"]].to_dict("records"))
    names = [k for k in strips if not ("−" in k or "(" in k)]
    front = today_df["price"].dropna()
    xs = (["FRONT " + today_df["label"].iloc[0].upper()] if not front.empty else []) + names
    ys = ([float(front.iloc[0])] if not front.empty else []) + [strips[k] for k in names]
    colors = [COLORS["AMBER"]] * (1 if not front.empty else 0) +              [COLORS["BLUE"] if k.startswith("WINTER") else
              COLORS["BULL"] if k.startswith("SUMMER") else COLORS["PURPLE"] for k in names]
    fig.add_trace(go.Bar(x=xs, y=ys, marker_color=colors,
                         text=[f"{v:.3f}" for v in ys], textposition="outside",
                         textfont={"color": COLORS["TEXT"]}))
    fig.update_yaxes(title_text="$/MMBtu", range=[0, max(ys) * 1.2] if ys else None)
    return fig


def _curve_history_dict(curve_history_store):
    """Returns {date_iso: [prices]} keyed by date."""
    if not curve_history_store:
        return {}
    return curve_history_store.get("history", {})


def figures_for_snapshot(stores):
    snap = (stores or {}).get("curve") or {}
    today_records = snap.get("today")
    if not today_records:
        return []
    today_df = _curve_from_records(today_records)
    week_ago = snap.get("week_ago")
    month_ago = snap.get("month_ago")
    return [
        ("Curve — Forward Curve", _build_main_curve(today_df, week_ago, month_ago)),
        ("Curve — Spread Heatmap", _build_spread_heatmap(today_df)),
        ("Curve — Roll Yield", _build_roll_yield(today_df)),
        ("Curve — Winter/Summer Spread", _build_winter_summer(today_df)),
    ]


def register_callbacks(app):

    @app.callback(
        Output("curve-history-store", "data"),
        Input("curve-interval", "n_intervals"),
        State("curve-history-store", "data"),
    )
    def fetch_curve(_, existing):
        today = dt.date.today()
        contracts = futures.next_n_contracts(today, n=24)
        df = futures.fetch_curve(contracts)
        if df.empty:
            return no_update
        records = []
        for _, row in df.iterrows():
            records.append({
                "label": row["label"], "ticker": row["ticker"],
                "price": float(row["price"]) if pd.notna(row["price"]) else None,
                "expiry": row["expiry"].isoformat() if hasattr(row["expiry"], "isoformat") else str(row["expiry"]),
            })
        prices = [r["price"] for r in records]

        history = (existing or {}).get("history", {}) or {}
        history[today.isoformat()] = prices
        cutoff = today - dt.timedelta(days=90)
        history = {k: v for k, v in history.items()
                   if dt.date.fromisoformat(k) >= cutoff}

        def lookup(days_ago):
            target = today - dt.timedelta(days=days_ago)
            for offset in range(0, 7):
                key = (target - dt.timedelta(days=offset)).isoformat()
                if key in history:
                    return history[key]
            return None

        week_ago = lookup(7)
        month_ago = lookup(30)

        regime, slope = futures.regime_label(prices)
        consec = _consecutive_regime_days(history, regime)
        return {
            "today": records, "history": history,
            "week_ago": week_ago, "month_ago": month_ago,
            "regime": regime, "slope": slope, "consecutive_days": consec,
        }

    def _consecutive_regime_days(history, current_regime):
        if not history:
            return 0
        keys = sorted(history.keys(), reverse=True)
        count = 0
        for k in keys:
            r, _ = futures.regime_label(history[k])
            if r == current_regime:
                count += 1
            else:
                break
        return count

    @app.callback(
        Output("curve-regime-banner", "children"),
        Output("curve-regime-banner", "style"),
        Input("curve-history-store", "data"),
    )
    def regime_banner(data):
        base = {"fontSize": "14px", "fontWeight": "700", "letterSpacing": "0.5px",
                "textAlign": "left", "padding": "8px 12px",
                "backgroundColor": COLORS["PANEL"], "marginBottom": "6px"}
        if not data:
            return "Awaiting curve data…", {**base, "color": COLORS["MUTED"]}
        regime = data.get("regime", "UNKNOWN")
        consec = data.get("consecutive_days", 0)
        color_map = {"CONTANGO": COLORS["ORANGE"], "BACKWARDATION": COLORS["BULL"],
                     "MIXED": COLORS["WARN"], "UNKNOWN": COLORS["MUTED"]}
        color = color_map.get(regime, COLORS["TEXT"])
        text = f"{regime}  •  {consec} consecutive trading day{'s' if consec != 1 else ''}"
        return text, {**base, "color": color, "border": f"1px solid {color}",
                      "borderLeft": f"6px solid {color}"}

    @app.callback(
        Output("curve-history-slider", "min"),
        Output("curve-history-slider", "max"),
        Output("curve-history-slider", "marks"),
        Output("curve-history-slider", "value"),
        Input("curve-history-store", "data"),
    )
    def slider_bounds(data):
        history = _curve_history_dict(data)
        keys = sorted(history.keys())
        if not keys:
            return 0, 0, {}, 0
        marks_step = max(len(keys) // 8, 1)
        marks = {i: keys[i] for i in range(0, len(keys), marks_step)}
        marks[len(keys) - 1] = keys[-1]
        return 0, len(keys) - 1, marks, len(keys) - 1

    @app.callback(
        Output("curve-main", "figure"),
        Input("curve-history-store", "data"),
        Input("curve-history-slider", "value"),
    )
    def main_chart(data, slider_val):
        if not data:
            return _build_main_curve(pd.DataFrame(), None, None)
        today_df = _curve_from_records(data.get("today"))
        history = _curve_history_dict(data)
        keys = sorted(history.keys())
        scrub = None
        if keys and slider_val is not None and 0 <= slider_val < len(keys):
            if slider_val != len(keys) - 1:
                scrub = history[keys[slider_val]]
        return _build_main_curve(today_df, data.get("week_ago"),
                                  data.get("month_ago"), scrub=scrub)

    @app.callback(Output("curve-strips", "children"),
                  Input("curve-history-store", "data"),
                  Input("curve-interval", "n_intervals"))
    def strips(data, _n):
        vals = fd.curve_strips((data or {}).get("today") or [])
        if not vals:
            return html.Div("AWAITING CURVE DATA…", className="term-hint")
        cells = []
        for name, v in vals.items():
            is_spread = "−" in name or "(" in name
            color = COLORS["AMBER"] if is_spread else COLORS["TEXT"]
            cells.append(html.Div([
                html.Div(name, className="tb-label"),
                html.Div(f"{v:+.3f}" if is_spread else f"{v:.3f}",
                         className="tb-value", style={"color": color}),
                html.Div("SPREAD $/MMBTU" if is_spread else "STRIP AVG $/MMBTU",
                         className="tb-sub"),
            ], className="tb-cell"))
        return cells

    @app.callback(Output("curve-spread-heatmap", "figure"),
                  Input("curve-history-store", "data"))
    def heatmap(data):
        if not data: return _build_spread_heatmap(pd.DataFrame())
        return _build_spread_heatmap(_curve_from_records(data.get("today")))

    @app.callback(Output("curve-roll-yield", "figure"),
                  Input("curve-history-store", "data"))
    def roll(data):
        if not data: return _build_roll_yield(pd.DataFrame())
        return _build_roll_yield(_curve_from_records(data.get("today")))

    @app.callback(Output("curve-winter-summer", "figure"),
                  Input("curve-history-store", "data"))
    def ws(data):
        if not data: return _build_winter_summer(pd.DataFrame())
        return _build_winter_summer(_curve_from_records(data.get("today")))
