"""
Web version of the Alpaca trading bot — a phone-friendly website.

Run locally:
    streamlit run streamlit_app.py

Or deploy free to Streamlit Community Cloud (see README) to get a permanent URL
you can open on your phone. Your API keys go in the host's "Secrets" box, never
in the code.

It reuses the exact same engine as the CLI: strategy.py (the swappable scan),
sizing.py (risk), broker.py (Alpaca), universe.py (tickers).
"""

from __future__ import annotations

import os

import streamlit as st
from alpaca.trading.enums import OrderSide

from broker import Broker, BrokerError
from config import Config, ConfigError
from sizing import size_position
from strategy import find_candidate
from universe import UNIVERSE


# --------------------------------------------------------------------- settings
def _secret(name: str, default: str = "") -> str:
    """Read a setting from Streamlit secrets first, then the environment."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


def build_config() -> Config:
    """Assemble a validated Config from the host's secrets/env."""
    live = _secret("LIVE", "false").strip().lower() in ("1", "true", "yes", "on")
    return Config(
        api_key=_secret("ALPACA_API_KEY").strip(),
        api_secret=_secret("ALPACA_API_SECRET").strip(),
        live=live,
        position_size_pct=float(_secret("POSITION_SIZE_PCT", "0.05")),
        max_order_dollars=float(_secret("MAX_ORDER_DOLLARS", "1000")),
        lookback_days=int(_secret("LOOKBACK_DAYS", "20")),
    )


@st.cache_resource(show_spinner=False)
def get_broker(_cfg_key: str, cfg: Config) -> Broker:
    """Cache one Broker per unique key so we don't reconnect on every rerun."""
    return Broker(cfg)


# --------------------------------------------------------------------------- UI
st.set_page_config(page_title="Alpaca Trading Bot", page_icon="📈", layout="centered")
st.title("📈 Alpaca Trading Bot")
st.caption("Momentum + volume scanner · confirm before every order")

# Build config; if keys are missing, guide the user instead of crashing.
try:
    cfg = build_config()
except (ConfigError, ValueError) as exc:
    st.warning("⚙️ **Not configured yet.**")
    st.write(
        "Add your Alpaca **paper** API key and secret in the host's *Secrets* "
        "settings (or a local `.streamlit/secrets.toml`), then reload:"
    )
    st.code(
        'ALPACA_API_KEY="PK...your key..."\n'
        'ALPACA_API_SECRET="...your secret..."\n'
        'LIVE="false"',
        language="toml",
    )
    st.caption(f"Details: {exc}")
    st.stop()

# Loud mode banner.
if cfg.live:
    st.error("⚠️ **LIVE TRADING IS ON — orders use REAL money.** "
             "Set `LIVE=\"false\"` in secrets to return to the paper sandbox.")
else:
    st.success("🟢 **Paper mode** — safe sandbox, fake money.")

# Connect.
try:
    broker = get_broker(f"{cfg.api_key}:{cfg.live}", cfg)
    account = broker.get_account()
except BrokerError as exc:
    st.error(f"Couldn't connect to Alpaca: {exc}")
    st.info("Double-check your key/secret, and that LIVE matches the key type "
            "(paper keys need LIVE=false).")
    st.stop()

# Account strip.
c1, c2, c3 = st.columns(3)
c1.metric("Buying power", f"${float(account.buying_power):,.0f}")
c2.metric("Cash", f"${float(account.cash):,.0f}")
c3.metric("Portfolio", f"${float(account.portfolio_value):,.0f}")

st.divider()

# Session state to carry the scanned candidate across button clicks.
if "candidate" not in st.session_state:
    st.session_state.candidate = None


def run_scan() -> None:
    """Scan the universe and stash the top tradeable candidate in session state."""
    st.session_state.candidate = None
    with st.spinner(f"Scanning {len(UNIVERSE)} tickers…"):
        try:
            market_open = broker.is_market_open()
        except BrokerError:
            market_open = None
        try:
            bars = broker.fetch_bars(UNIVERSE)
        except BrokerError as exc:
            st.error(f"Market data error: {exc}")
            return

    if market_open is False:
        st.info("Market is currently **closed** — an order will queue until the "
                "next open.")

    if not bars:
        st.warning("No market data returned. Try again shortly.")
        return

    candidate = find_candidate(bars)
    if candidate is None:
        st.warning("No candidates passed the strategy filters right now. "
                   "Nothing to trade.")
        return

    # Skip anything we already hold.
    try:
        held = broker.held_symbols()
    except BrokerError as exc:
        st.error(f"Couldn't check positions: {exc}")
        return
    if candidate.symbol in held:
        st.info(f"Top pick **{candidate.symbol}** is already in your portfolio — "
                "skipping to avoid stacking a position.")
        return

    # Size it.
    try:
        buying_power = broker.get_buying_power()
    except BrokerError as exc:
        st.error(f"Error: {exc}")
        return
    sizing = size_position(
        price=candidate.last_price,
        buying_power=buying_power,
        position_size_pct=cfg.position_size_pct,
        max_order_dollars=cfg.max_order_dollars,
    )
    st.session_state.candidate = {"c": candidate, "s": sizing}


# --- Action buttons ---
b1, b2 = st.columns(2)
if b1.button("🔍 Scan for a trade", use_container_width=True, type="primary"):
    run_scan()
if b2.button("🔄 Clear", use_container_width=True):
    st.session_state.candidate = None

# --- Candidate card ---
stash = st.session_state.candidate
if stash:
    c = stash["c"]
    s = stash["s"]
    st.subheader(f"Candidate: {c.symbol}")
    st.write(f"**Current price:** ${c.last_price:,.2f}")
    st.write(f"**Signal:** {c.reason}")
    st.write(f"**Score:** {c.score:.4f}")

    if not s.ok:
        st.warning(f"No order proposed — {s.skipped_reason}")
    else:
        st.info(
            f"**Proposed order:** BUY {s.qty} share(s) of {c.symbol} "
            f"(~${s.estimated_cost:,.2f})\n\n**How sized:** {s.explanation}"
        )
        # Explicit confirm step, mirroring the CLI's yes/no.
        confirm = st.checkbox("I confirm I want to place this order")
        if st.button("✅ Place order", disabled=not confirm,
                     use_container_width=True):
            try:
                order = broker.submit_market_order(
                    symbol=c.symbol, qty=s.qty, side=OrderSide.BUY
                )
                status = getattr(order.status, "value", order.status)
                st.success(
                    f"Order submitted! ID `{order.id}` — {order.symbol} "
                    f"×{order.qty} — status **{status}**"
                )
                st.session_state.candidate = None  # avoid double-buy
            except BrokerError as exc:
                st.error(f"Order failed: {exc}")

st.divider()

# --- Positions expander ---
with st.expander("📊 My positions"):
    try:
        positions = broker.get_positions()
    except BrokerError as exc:
        st.error(f"Error: {exc}")
        positions = []
    if not positions:
        st.write("No open positions.")
    else:
        st.table([
            {
                "Symbol": p.symbol,
                "Qty": float(p.qty),
                "Avg": round(float(p.avg_entry_price), 2),
                "Price": round(float(p.current_price), 2),
                "Mkt value": round(float(p.market_value), 2),
                "P/L": round(float(p.unrealized_pl), 2),
            }
            for p in positions
        ])

st.caption("Educational tool, not financial advice. Test in paper mode first.")
