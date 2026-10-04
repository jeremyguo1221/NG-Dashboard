"""Shared visual theme for the dashboard — Bloomberg-terminal styling.

Every Plotly figure should apply ``plotly_layout()`` so all charts share the
same black background, amber panel titles, grid color, and monospace font.
Colors are referenced by name from ``COLORS`` rather than hard-coded in tab
modules. CSS counterparts of these tokens live in ``assets/custom.css``.
"""
from dash import html

COLORS = {
    "BG":      "#000000",
    "PANEL":   "#050505",
    "GRID":    "#1f1f1f",
    "BORDER":  "#2b2b2b",
    "TEXT":    "#e8e8e8",
    "MUTED":   "#8c8c8c",
    "AMBER":   "#ffa028",
    "BULL":    "#00d26a",
    "BEAR":    "#ff433d",
    "WARN":    "#ffd400",
    "ORANGE":  "#ff8a1f",
    "BLUE":    "#4aa8ff",
    "CYAN":    "#3fd7e6",
    "PURPLE":  "#b48cff",
}

FONT_MONO = '"IBM Plex Mono", "Consolas", "Lucida Console", monospace'

# Diverging bear→neutral→bull scale for heatmaps (replaces RdYlGn).
DIVERGING = [
    [0.0, "#7a0f0c"], [0.25, COLORS["BEAR"]], [0.5, "#141414"],
    [0.75, COLORS["BULL"]], [1.0, "#00703a"],
]


def plotly_layout(title=None, height=None, showlegend=True):
    layout = {
        "paper_bgcolor": COLORS["PANEL"],
        "plot_bgcolor":  COLORS["PANEL"],
        "font": {"color": COLORS["TEXT"], "family": FONT_MONO, "size": 11},
        "xaxis": {"gridcolor": COLORS["GRID"], "zerolinecolor": COLORS["BORDER"],
                  "color": COLORS["MUTED"], "linecolor": COLORS["BORDER"],
                  "tickfont": {"color": COLORS["MUTED"]}},
        "yaxis": {"gridcolor": COLORS["GRID"], "zerolinecolor": COLORS["BORDER"],
                  "color": COLORS["MUTED"], "linecolor": COLORS["BORDER"],
                  "tickfont": {"color": COLORS["MUTED"]}},
        "margin": {"l": 52, "r": 16, "t": 44 if title else 16, "b": 36},
        "showlegend": showlegend,
        "legend": {"bgcolor": "rgba(0,0,0,0)", "font": {"color": COLORS["TEXT"], "size": 10},
                   "orientation": "h", "yanchor": "bottom", "y": 1.0,
                   "xanchor": "right", "x": 1.0},
        "hoverlabel": {"bgcolor": "#111111", "bordercolor": COLORS["AMBER"],
                       "font": {"family": FONT_MONO, "color": COLORS["TEXT"]}},
        "colorway": [COLORS["AMBER"], COLORS["BLUE"], COLORS["BULL"], COLORS["BEAR"],
                     COLORS["CYAN"], COLORS["PURPLE"], COLORS["WARN"]],
    }
    if title:
        # Amber, uppercase, left-aligned — reads like a terminal panel header.
        layout["title"] = {"text": str(title).upper(),
                           "font": {"color": COLORS["AMBER"], "size": 12,
                                    "family": FONT_MONO},
                           "x": 0.0, "xanchor": "left", "xref": "paper",
                           "y": 0.98, "yanchor": "top", "yref": "container",
                           "pad": {"l": 4}}
    if height:
        layout["height"] = height
    return layout


def term_panel(title, *children, right=None, className="", **kwargs):
    """Bordered terminal panel with an amber header strip."""
    header = html.Div([
        html.Span(str(title).upper(), className="term-panel-title"),
        html.Span(right, className="term-panel-right") if right is not None else None,
    ], className="term-panel-hdr")
    return html.Div([header, html.Div(list(children), className="term-panel-body")],
                    className=f"term-panel {className}".strip(), **kwargs)


def bull_or_bear(value, neutral_threshold=0.0):
    if value is None:
        return COLORS["MUTED"]
    if value > neutral_threshold:
        return COLORS["BULL"]
    if value < -neutral_threshold:
        return COLORS["BEAR"]
    return COLORS["MUTED"]


def colored_change(value, pct=None, prefix=""):
    """Return an HTML-safe string and color for a change indicator."""
    color = bull_or_bear(value)
    arrow = "▲" if (value or 0) > 0 else ("▼" if (value or 0) < 0 else "■")
    text = f"{prefix}{arrow} {value:+.2f}"
    if pct is not None:
        text += f" ({pct:+.2f}%)"
    return text, color
