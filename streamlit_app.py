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

from ai import ai_choose
from broker import Broker, BrokerError
from config import Config, ConfigError
from sizing import size_position
from strategy import rank_candidates
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


def run_scan(use_ai: bool, ai_key: str, ai_provider: str) -> None:
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

    ranked = rank_candidates(bars)
    if not ranked:
        st.warning("No candidates passed the strategy filters right now. "
                   "Nothing to trade.")
        return

    # Drop anything we already hold.
    try:
        held = broker.held_symbols()
    except BrokerError as exc:
        st.error(f"Couldn't check positions: {exc}")
        return
    tradeable = [c for c in ranked if c.symbol not in held]
    if not tradeable:
        st.info("The top candidates are all already in your portfolio — "
                "skipping to avoid stacking positions.")
        return

    # Default to the rule-based #1. If AI is on, let Claude pick among the top few.
    candidate = tradeable[0]
    ai_result = None
    if use_ai and ai_key:
        with st.spinner("Asking the AI for a second opinion…"):
            ai_result = ai_choose(tradeable[:6], ai_key, provider=ai_provider)
        if ai_result is not None:
            match = next((c for c in tradeable if c.symbol == ai_result.symbol), None)
            if match is not None:
                candidate = match  # AI's pick (falls back to #1 if it named an odd one)

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
    st.session_state.candidate = {"c": candidate, "s": sizing, "ai": ai_result}


# --- AI toggle (uses a FREE provider key if one is configured) ---
# Groq is the default (free, fast, no credit card); OpenRouter is a free backup.
groq_key = _secret("GROQ_API_KEY").strip()
openrouter_key = _secret("OPENROUTER_API_KEY").strip()
if groq_key:
    ai_provider, ai_key = "groq", groq_key
elif openrouter_key:
    ai_provider, ai_key = "openrouter", openrouter_key
else:
    ai_provider, ai_key = "groq", ""

if ai_key:
    use_ai = st.toggle("🤖 Let the AI pick the trade (free)", value=True,
                       help="The AI reviews the top candidates' real numbers and "
                            "chooses one, with a plain-English rationale.")
else:
    use_ai = False
    st.caption("💡 Add a **free** `GROQ_API_KEY` (from console.groq.com — no "
               "credit card) in Secrets to have the AI pick and explain the "
               "trade. Without it, the rule-based momentum + volume scanner "
               "runs on its own.")

# --- Action buttons ---
b1, b2 = st.columns(2)
if b1.button("🔍 Scan for a trade", use_container_width=True, type="primary"):
    run_scan(use_ai, ai_key, ai_provider)
if b2.button("🔄 Clear", use_container_width=True):
    st.session_state.candidate = None

# --- Candidate card ---
stash = st.session_state.candidate
if stash:
    c = stash["c"]
    s = stash["s"]
    ai = stash.get("ai")
    st.subheader(f"Candidate: {c.symbol}")
    st.write(f"**Current price:** ${c.last_price:,.2f}")
    st.write(f"**Signal:** {c.reason}")
    st.write(f"**Score:** {c.score:.4f}")

    if ai is not None:
        badge = {"GO": "🟢", "CAUTION": "🟡", "NO-GO": "🔴"}.get(ai.recommendation, "🤖")
        st.markdown(
            f"**🤖 AI's take — {badge} {ai.recommendation}** "
            f"(confidence: {ai.confidence})\n\n{ai.rationale}"
        )

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
