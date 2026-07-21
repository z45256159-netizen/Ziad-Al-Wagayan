"""
Alpaca trading bot — chat-style website.

Flow:
  1. Paste your Alpaca keys (and optional free Groq key). The app checks them.
  2. Chat: type "find" and it finds one trade, shows the reason, and asks you.
  3. Tap Yes to place it, No to skip.

Run locally:  streamlit run streamlit_app.py
Deploy free:  see README (Streamlit Community Cloud).
"""

from __future__ import annotations

import os

import streamlit as st
from alpaca.trading.enums import OrderSide

from ai import ai_choose, verify_key
from broker import Broker, BrokerError
from config import Config, ConfigError
from sizing import size_position
from strategy import rank_candidates
from universe import UNIVERSE

st.set_page_config(page_title="Alpaca Trading Bot", page_icon="📈", layout="centered")

ss = st.session_state
ss.setdefault("connected", False)
ss.setdefault("messages", [])      # chat history: [{"role","content"}]
ss.setdefault("pending", None)     # trade awaiting yes/no: {"c","s","ai"}
ss.setdefault("broker", None)
ss.setdefault("cfg", None)
ss.setdefault("groq_key", "")


def _secret(name: str, default: str = "") -> str:
    """Prefill from Streamlit secrets / env if available (optional)."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


def say(role: str, content: str) -> None:
    ss.messages.append({"role": role, "content": content})


# ===========================================================================
# 1) CONNECT SCREEN — paste keys, verify they work
# ===========================================================================
if not ss.connected:
    st.title("📈 Alpaca Trading Bot")
    st.caption("Paste your keys to start. They stay in this browser session only.")

    with st.form("connect"):
        st.markdown("**Alpaca keys** (from app.alpaca.markets → Paper Trading)")
        alp_key = st.text_input("Alpaca API key", value=_secret("ALPACA_API_KEY"),
                                type="password")
        alp_sec = st.text_input("Alpaca API secret", value=_secret("ALPACA_API_SECRET"),
                                type="password")

        st.markdown("**Groq key** — free AI (from console.groq.com). Optional.")
        groq_key = st.text_input("Groq API key", value=_secret("GROQ_API_KEY"),
                                 type="password")

        live = st.checkbox("⚠️ Live trading (REAL money)", value=False)
        submitted = st.form_submit_button("Connect", use_container_width=True,
                                          type="primary")

    if submitted:
        try:
            cfg = Config(
                api_key=alp_key.strip(),
                api_secret=alp_sec.strip(),
                live=live,
                position_size_pct=float(_secret("POSITION_SIZE_PCT", "0.05")),
                max_order_dollars=float(_secret("MAX_ORDER_DOLLARS", "1000")),
                lookback_days=int(_secret("LOOKBACK_DAYS", "20")),
            )
        except (ConfigError, ValueError) as exc:
            st.error(f"Check your entries: {exc}")
            st.stop()

        # Verify Alpaca keys by fetching the account.
        with st.spinner("Checking your Alpaca keys…"):
            try:
                broker = Broker(cfg)
                acct = broker.get_account()
            except BrokerError as exc:
                st.error(f"❌ Alpaca keys didn't work: {exc}")
                st.info("Make sure they're **paper** keys and 'Live trading' is "
                        "unchecked (or use live keys with it checked).")
                st.stop()

        # Verify Groq key if one was entered.
        groq_msg = "No AI key — the built-in scanner will decide trades."
        gk = groq_key.strip()
        if gk:
            with st.spinner("Checking your Groq AI key…"):
                if verify_key(gk):
                    groq_msg = "🤖 Groq AI connected — it will pick and explain trades."
                else:
                    gk = ""
                    groq_msg = "⚠️ That Groq key didn't work, so the built-in " \
                               "scanner will decide trades. (You can reconnect later.)"

        ss.broker = broker
        ss.cfg = cfg
        ss.groq_key = gk
        ss.connected = True
        ss.messages = []
        ss.pending = None
        say("assistant",
            f"✅ Connected to Alpaca ({cfg.mode_name}). {groq_msg}\n\n"
            f"Type **find** to find a trade. You can also type **balance** or "
            f"**positions**.")
        st.rerun()

    st.stop()


# ===========================================================================
# Connected — set up helpers
# ===========================================================================
broker: Broker = ss.broker
cfg: Config = ss.cfg


def build_trade():
    """Scan and pick one trade. Returns (assistant_text, pending_dict_or_None)."""
    try:
        market_open = broker.is_market_open()
    except BrokerError:
        market_open = None
    try:
        bars = broker.fetch_bars(UNIVERSE)
    except BrokerError as exc:
        return f"⚠️ Couldn't get market data: {exc}", None

    if not bars:
        return "No market data came back. Try again in a moment.", None

    ranked = rank_candidates(bars)
    if not ranked:
        return ("No stock passed the momentum + volume filters right now — "
                "nothing worth trading. Try again later."), None

    try:
        held = broker.held_symbols()
    except BrokerError as exc:
        return f"⚠️ Couldn't check your positions: {exc}", None
    tradeable = [c for c in ranked if c.symbol not in held]
    if not tradeable:
        return ("The best candidates are all already in your portfolio — "
                "skipping to avoid doubling up."), None

    candidate = tradeable[0]
    ai = None
    if ss.groq_key:
        ai = ai_choose(tradeable[:6], ss.groq_key)
        if ai is not None:
            match = next((c for c in tradeable if c.symbol == ai.symbol), None)
            if match is not None:
                candidate = match

    try:
        buying_power = broker.get_buying_power()
    except BrokerError as exc:
        return f"⚠️ Couldn't read your buying power: {exc}", None

    sizing = size_position(
        price=candidate.last_price,
        buying_power=buying_power,
        position_size_pct=cfg.position_size_pct,
        max_order_dollars=cfg.max_order_dollars,
    )

    # Build the message.
    parts = [f"**Found: {candidate.symbol}** at ${candidate.last_price:,.2f}",
             f"_{candidate.reason}_"]
    if ai is not None:
        badge = {"GO": "🟢", "CAUTION": "🟡", "NO-GO": "🔴"}.get(ai.recommendation, "🤖")
        parts.append(f"🤖 AI: {badge} **{ai.recommendation}** "
                     f"({ai.confidence} confidence) — {ai.rationale}")
    if market_open is False:
        parts.append("_Market is closed — the order will queue until it opens._")

    if not sizing.ok:
        parts.append(f"But I can't size an order: {sizing.skipped_reason}")
        return "\n\n".join(parts), None

    parts.append(f"**Proposed: BUY {sizing.qty} share(s) (~${sizing.estimated_cost:,.2f}).**")
    parts.append("Place it? Tap **Yes** or **No** below (or type yes / no).")
    pending = {"c": candidate, "s": sizing, "ai": ai}
    return "\n\n".join(parts), pending


def place_pending() -> None:
    p = ss.pending
    ss.pending = None
    c, s = p["c"], p["s"]
    try:
        order = broker.submit_market_order(symbol=c.symbol, qty=s.qty, side=OrderSide.BUY)
        status = getattr(order.status, "value", order.status)
        say("assistant", f"✅ Order placed! **{order.symbol} ×{order.qty}** — "
                         f"status **{status}** (id `{order.id}`).")
    except BrokerError as exc:
        say("assistant", f"❌ Order failed: {exc}")


def handle_command(text: str) -> None:
    t = text.strip().lower()
    say("user", text)

    # Yes/No while a trade is pending.
    if ss.pending is not None:
        if t in ("yes", "y", "place", "buy", "ok", "yeah"):
            place_pending()
            return
        if t in ("no", "n", "cancel", "skip", "stop"):
            ss.pending = None
            say("assistant", "👍 Skipped. Type **find** whenever you want another.")
            return

    if any(w in t for w in ("find", "scan", "trade", "buy something")):
        msg, pending = build_trade()
        ss.pending = pending
        say("assistant", msg)
    elif "balance" in t or "money" in t or "account" in t:
        try:
            a = broker.get_account()
            say("assistant",
                f"💰 Buying power **${float(a.buying_power):,.2f}** · "
                f"cash ${float(a.cash):,.2f} · "
                f"portfolio ${float(a.portfolio_value):,.2f}.")
        except BrokerError as exc:
            say("assistant", f"⚠️ {exc}")
    elif "position" in t or "holding" in t or "portfolio" in t:
        try:
            ps = broker.get_positions()
        except BrokerError as exc:
            say("assistant", f"⚠️ {exc}")
            return
        if not ps:
            say("assistant", "You have no open positions.")
        else:
            rows = "\n".join(
                f"- **{p.symbol}** ×{float(p.qty):g} · now ${float(p.current_price):,.2f} "
                f"· P/L ${float(p.unrealized_pl):,.2f}" for p in ps)
            say("assistant", "📊 Your positions:\n" + rows)
    else:
        say("assistant", "Type **find** to find a trade, or **balance** / "
                         "**positions**.")


# ===========================================================================
# 2) CHAT SCREEN
# ===========================================================================
if cfg.live:
    st.error("⚠️ LIVE trading — orders use REAL money.")
else:
    st.title("📈 Alpaca Trading Bot")
    st.caption("🟢 Paper mode — fake money, safe to experiment.")

top = st.columns([3, 1])
with top[0]:
    try:
        bp = float(broker.get_account().buying_power)
        st.caption(f"Buying power: ${bp:,.0f}")
    except BrokerError:
        st.caption("Buying power: —")
with top[1]:
    if st.button("Disconnect"):
        for k in ("connected", "broker", "cfg", "groq_key", "pending"):
            ss[k] = False if k == "connected" else (None if k != "groq_key" else "")
        ss.messages = []
        st.rerun()

# Render chat history.
for m in ss.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# Yes/No buttons when a trade is waiting.
if ss.pending is not None:
    yn = st.columns(2)
    if yn[0].button("✅ Yes, place it", use_container_width=True, type="primary"):
        place_pending()
        st.rerun()
    if yn[1].button("❌ No, skip", use_container_width=True):
        ss.pending = None
        say("assistant", "👍 Skipped. Type **find** whenever you want another.")
        st.rerun()

# Chat input.
prompt = st.chat_input("Type 'find' to find a trade…")
if prompt:
    handle_command(prompt)
    st.rerun()
