"""
Chart helpers: a TradingView deep link + an in-app candlestick chart that draws
the entry / stop-loss / take-profit like a broker's "position" tool.
"""

from __future__ import annotations

from typing import List

from strategy import Bar


def tradingview_url(symbol: str) -> str:
    """Deep link to the live TradingView chart for a US ticker."""
    return f"https://www.tradingview.com/chart/?symbol={symbol.upper()}"


def make_position_chart(bars: List[Bar], plan, support=None, resistance=None,
                        marks=None):
    """
    Build a Plotly candlestick figure with the trade drawn on it:
      * green zone from entry up to the take-profit,
      * red zone from the stop-loss up to entry,
      * dashed entry / stop / target lines with labels.
    Returns a plotly Figure, or None if plotly isn't available.
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None

    xs = list(range(len(bars)))
    fig = go.Figure(go.Candlestick(
        x=xs,
        open=[(b.open if b.open is not None else b.close) for b in bars],
        high=[(b.high if b.high is not None else b.close) for b in bars],
        low=[(b.low if b.low is not None else b.close) for b in bars],
        close=[b.close for b in bars],
        increasing_line_color="#22c55e",
        decreasing_line_color="#ef4444",
        name=plan.symbol,
    ))

    x0, x1 = -0.5, len(bars) - 0.5
    # Target zone (green) and risk zone (red), like the photo.
    fig.add_shape(type="rect", x0=x0, x1=x1, y0=plan.entry, y1=plan.take_profit,
                  fillcolor="rgba(34,197,94,0.15)", line_width=0, layer="below")
    fig.add_shape(type="rect", x0=x0, x1=x1, y0=plan.stop, y1=plan.entry,
                  fillcolor="rgba(239,68,68,0.15)", line_width=0, layer="below")

    # Support (floor) and resistance (ceiling) — the S/R "boxes" traders watch.
    if resistance:
        fig.add_hline(y=resistance, line_dash="dot", line_color="#f59e0b",
                      line_width=1)
        fig.add_annotation(x=x0, y=resistance, text=f"Resistance {resistance:.2f}",
                           showarrow=False, font=dict(color="#f59e0b", size=10),
                           xanchor="left", yanchor="bottom")
    if support:
        fig.add_hline(y=support, line_dash="dot", line_color="#38bdf8",
                      line_width=1)
        fig.add_annotation(x=x0, y=support, text=f"Support {support:.2f}",
                           showarrow=False, font=dict(color="#38bdf8", size=10),
                           xanchor="left", yanchor="top")

    for price, color, label in [
        (plan.take_profit, "#22c55e",
         f"🎯 Target {plan.take_profit:.2f}  (+{plan.tp_pct:.1f}%)  ${plan.reward_total:,.0f}"),
        (plan.entry, "#9ca3af", f"Entry {plan.entry:.2f}"),
        (plan.stop, "#ef4444",
         f"🛑 Stop {plan.stop:.2f}  (-{plan.stop_pct:.1f}%)  ${plan.risk_total:,.0f}"),
    ]:
        fig.add_hline(y=price, line_dash="dash", line_color=color, line_width=1.5)
        fig.add_annotation(x=x1, y=price, text=label, showarrow=False,
                           font=dict(color="#e5e7eb", size=11),
                           xanchor="right", yanchor="bottom",
                           bgcolor="rgba(17,24,39,0.75)")

    # Pattern markers (e.g. the two lows of a double bottom, or H&S peaks).
    if marks:
        mx = [m[0] for m in marks]
        my = [m[1] for m in marks]
        fig.add_trace(go.Scatter(
            x=mx, y=my, mode="markers+text",
            marker=dict(size=11, color="#eab308", symbol="circle-open",
                        line=dict(width=2)),
            text=[m[2] for m in marks], textposition="bottom center",
            textfont=dict(color="#eab308", size=10), name="pattern",
        ))

    fig.update_layout(
        title=f"{plan.symbol} — {plan.qty} shares · Risk/Reward 1:{plan.rr_ratio:g}",
        xaxis_rangeslider_visible=False,
        height=420,
        margin=dict(l=8, r=8, t=42, b=8),
        showlegend=False,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showticklabels=False)
    return fig
