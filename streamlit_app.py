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
from ai import ai_choose, verify_key
from broker import Broker, BrokerError
from config import Config, ConfigError
from sizing import build_trade_plan
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
ss.setdefault("recent", [])        # last few tickers suggested, for variety


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
# 1) CONNECT — auto-connect from saved Secrets, else show a one-time form
# ===========================================================================
def _connect(alp_key, alp_sec, groq_key, live, verify_groq=True):
    """Returns (ok, error_message). On success, stores everything in session."""
    try:
        cfg = Config(
            api_key=alp_key.strip(),
            api_secret=alp_sec.strip(),
            live=live,
            position_size_pct=float(_secret("POSITION_SIZE_PCT", "0.05")),
            max_order_dollars=float(_secret("MAX_ORDER_DOLLARS", "2000")),
            lookback_days=int(_secret("LOOKBACK_DAYS", "60")),
            risk_pct=float(_secret("RISK_PCT", "0.01")),
        )
    except (ConfigError, ValueError) as exc:
        return False, f"Check your entries: {exc}"

    try:
        broker = Broker(cfg)
        broker.get_account()
    except BrokerError as exc:
        return False, f"Alpaca keys didn't work: {exc}"

    gk = groq_key.strip()
    groq_msg = "No AI key — the built-in scanner will decide trades."
    if gk:
        if not verify_groq or verify_key(gk):
            groq_msg = "🤖 Groq AI connected — it will pick and explain trades."
        else:
            gk = ""
            groq_msg = "⚠️ That Groq key didn't work, so the built-in scanner will decide."

    ss.broker, ss.cfg, ss.groq_key = broker, cfg, gk
    ss.connected, ss.messages, ss.pending = True, [], None
    say("assistant",
        f"✅ Connected to Alpaca ({cfg.mode_name}). {groq_msg}\n\n"
        f"Type **find** to find a trade. You can also type **balance** or "
        f"**positions**.")
    return True, None


def _live_flag() -> bool:
    return _secret("LIVE", "false").strip().lower() in ("1", "true", "yes", "on")


if not ss.connected:
    # Auto-connect if keys are saved in Secrets — so you never retype them.
    saved_key = _secret("ALPACA_API_KEY").strip()
    saved_sec = _secret("ALPACA_API_SECRET").strip()
    if saved_key and saved_sec and not ss.get("auto_tried"):
        ss.auto_tried = True
        with st.spinner("Connecting…"):
            ok, _err = _connect(saved_key, saved_sec, _secret("GROQ_API_KEY"),
                                _live_flag())
        if ok:
            st.rerun()

if not ss.connected:
    st.title("📈 Alpaca Trading Bot")
    st.caption("Enter your keys once. Tip: to skip this screen forever, save them "
               "in the app's **Settings → Secrets** (see the README) and it will "
               "auto-connect every time.")

    with st.form("connect"):
        st.markdown("**Alpaca keys** (from app.alpaca.markets → Paper Trading)")
        alp_key = st.text_input("Alpaca API key", value=_secret("ALPACA_API_KEY"),
                                type="password")
        alp_sec = st.text_input("Alpaca API secret", value=_secret("ALPACA_API_SECRET"),
                                type="password")
        st.markdown("**Groq key** — free AI that runs Llama (from "
                    "console.groq.com). Optional.")
        groq_key = st.text_input("Groq API key (free Llama AI)",
                                 value=_secret("GROQ_API_KEY"), type="password")
        live = st.checkbox("⚠️ Live trading (REAL money)", value=_live_flag())
        submitted = st.form_submit_button("Connect", use_container_width=True,
                                          type="primary")

    if submitted:
        with st.spinner("Checking your keys…"):
            ok, err = _connect(alp_key, alp_sec, groq_key, live)
        if ok:
            st.rerun()
        else:
            st.error(f"❌ {err}")
            st.info("Make sure they're **paper** keys and 'Live trading' is "
                    "unchecked (or use live keys with it checked).")

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
        return ("No stock passed the trend + RSI + MACD + volume filters right "
                "now — nothing worth trading. Try again later."), None

    try:
        held = broker.held_symbols()
    except BrokerError as exc:
        return f"⚠️ Couldn't check your positions: {exc}", None
    tradeable = [c for c in ranked if c.symbol not in held]
    if not tradeable:
        return ("The best candidates are all already in your portfolio — "
                "skipping to avoid doubling up."), None

    # Variety: prefer candidates we haven't just suggested. If that empties the
    # list, fall back to the full set.
    fresh = [c for c in tradeable if c.symbol not in ss.recent]
    pool = fresh if fresh else tradeable

    candidate = pool[0]
    ai = None
    if ss.groq_key:
        ai = ai_choose(pool[:6], ss.groq_key)
        if ai is not None:
            match = next((c for c in pool if c.symbol == ai.symbol), None)
            if match is not None:
                candidate = match

    try:
        buying_power = broker.get_buying_power()
    except BrokerError as exc:
        return f"⚠️ Couldn't read your buying power: {exc}", None

    plan = build_trade_plan(
        score=candidate,
        buying_power=buying_power,
        risk_pct=ss.risk_pct,
        max_order_dollars=ss.max_order,
        stop_atr_mult=ss.stop_mult,
        reward_risk=ss.reward_risk,
    )

    # Remember this ticker so the next scan tends to pick something different.
    ss.recent = ([candidate.symbol] + ss.recent)[:3]

    # ---- Build the full, trader-style message ----
    parts = [f"### 📊 {candidate.symbol} @ ${plan.entry:,.2f}"]
    parts.append(f"**Why this stock:** {candidate.reason}")
    parts.append("**Strategy:** moving-average crossover (20 vs 50) + RSI + MACD "
                 "+ volume — a momentum setup that only fires when trend, "
                 "momentum, confirmation and participation all agree.")
    if ai is not None:
        badge = {"GO": "🟢", "CAUTION": "🟡", "NO-GO": "🔴"}.get(ai.recommendation, "🤖")
        parts.append(f"🤖 **AI ({ai.confidence} confidence): {badge} "
                     f"{ai.recommendation}** — {ai.rationale}")
    if market_open is False:
        parts.append("_Market is closed — the order will queue until it opens._")

    if not plan.ok:
        parts.append(f"⚠️ Can't build an order: {plan.skipped_reason}")
        return "\n\n".join(parts), None

    parts.append(
        "**📋 Trade plan**\n"
        f"- **Buy {plan.qty} share(s)** of {plan.symbol} at ~${plan.entry:,.2f}\n"
        f"- 💵 Cost: **${plan.cost:,.2f}**\n"
        f"- 🛑 Stop-loss: **${plan.stop:,.2f}** (−{plan.stop_pct:.1f}%) → "
        f"risk **${plan.risk_total:,.2f}** if it hits\n"
        f"- 🎯 Take-profit: **${plan.take_profit:,.2f}** (+{plan.tp_pct:.1f}%) → "
        f"profit **${plan.reward_total:,.2f}** if it hits\n"
        f"- ⚖️ Risk/reward: **1 : {plan.rr_ratio:g}**"
    )
    parts.append("Place it? Tap **✅ Yes** or **❌ No** below (or type yes / no). "
                 "The stop-loss and take-profit are placed automatically with it.")
    return "\n\n".join(parts), {"plan": plan, "ai": ai}


def place_pending() -> None:
    plan = ss.pending["plan"]
    ss.pending = None
    try:
        order = broker.submit_bracket_order(
            symbol=plan.symbol,
            qty=plan.qty,
            take_profit=plan.take_profit,
            stop_loss=plan.stop,
        )
        status = getattr(order.status, "value", order.status)
        say("assistant",
            f"✅ Order placed! **{order.symbol} ×{order.qty}** at market — "
            f"status **{status}**.\n\n"
            f"🛑 Stop-loss ${plan.stop:,.2f} and 🎯 take-profit "
            f"${plan.take_profit:,.2f} are attached automatically "
            f"(id `{order.id}`).")
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
        if pending is not None and ss.auto_mode:
            ai = pending.get("ai")
            if ai is not None and ai.recommendation == "NO-GO":
                ss.pending = None
                say("assistant", msg + "\n\n🤖 **Auto mode:** the AI flagged this "
                                        "**NO-GO**, so I skipped it. Type **find** "
                                        "for another.")
            else:
                say("assistant", msg)   # show the plan
                ss.pending = pending
                place_pending()         # ...then place it automatically
        else:
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
# Slider-controlled settings default from config on first load.
ss.setdefault("risk_pct", cfg.risk_pct)
ss.setdefault("max_order", cfg.max_order_dollars)
ss.setdefault("stop_mult", 1.5)
ss.setdefault("reward_risk", 2.0)
ss.setdefault("auto_mode", False)

# --- A little CSS polish (theme-aware) ---
st.markdown("""
<style>
#MainMenu, footer, [data-testid="stToolbar"] {visibility:hidden;}
.block-container {padding-top:1.2rem; max-width:760px;}
.hdr {display:flex; align-items:center; justify-content:space-between;
      background:linear-gradient(135deg,#0f766e,#0891b2);
      color:#fff; padding:14px 18px; border-radius:16px; margin-bottom:.6rem;
      box-shadow:0 4px 16px rgba(8,145,178,.25);}
.hdr .t {font-size:1.15rem; font-weight:800; letter-spacing:.2px;}
.pill {padding:4px 13px; border-radius:999px; font-weight:800; font-size:.72rem;
       letter-spacing:.4px;}
.pill.paper {background:#dcfce7; color:#166534;}
.pill.live  {background:#fee2e2; color:#991b1b;}
.stButton>button {border-radius:12px; font-weight:700; padding:.55rem 1rem;}
[data-testid="stMetric"] {background:rgba(128,128,128,.08); padding:10px 12px;
       border-radius:12px;}
</style>
""", unsafe_allow_html=True)

# --- Header ---
mode_cls = "live" if cfg.live else "paper"
st.markdown(
    f'<div class="hdr"><span class="t">📈 Alpaca Trading Bot</span>'
    f'<span class="pill {mode_cls}">{cfg.mode_name}</span></div>',
    unsafe_allow_html=True,
)
if cfg.live:
    st.error("⚠️ LIVE trading — orders use REAL money.")

# --- Account metrics ---
try:
    a = broker.get_account()
    m = st.columns(3)
    m[0].metric("Buying power", f"${float(a.buying_power):,.0f}")
    m[1].metric("Cash", f"${float(a.cash):,.0f}")
    m[2].metric("Portfolio", f"${float(a.portfolio_value):,.0f}")
except BrokerError:
    st.caption("Account: —")

# --- Settings + disconnect ---
with st.expander("⚙️ Trading settings"):
    ss.risk_pct = st.slider(
        "Risk per trade (% of buying power)", 0.25, 5.0,
        float(ss.risk_pct * 100), 0.25,
        help="How much of your account you're willing to lose if the stop hits. "
             "Drives how many shares.") / 100
    ss.max_order = float(st.slider(
        "Max per order ($)", 500, 20000, int(ss.max_order), 500,
        help="Hard ceiling on any single order."))
    ss.stop_mult = st.slider(
        "Stop distance (× ATR)", 1.0, 3.0, float(ss.stop_mult), 0.5,
        help="Wider = more room, fewer shares. Tighter = less risk per share.")
    ss.reward_risk = st.slider(
        "Reward : Risk", 1.0, 4.0, float(ss.reward_risk), 0.5,
        help="Take-profit distance as a multiple of the stop distance.")
    st.caption(f"Now: risk {ss.risk_pct*100:.2f}% · max ${ss.max_order:,.0f} · "
               f"stop {ss.stop_mult:g}×ATR · reward:risk 1:{ss.reward_risk:g}")

    st.markdown("---")
    ss.auto_mode = st.checkbox(
        "🤖 Auto mode — let the AI find AND place the trade by itself",
        value=ss.auto_mode,
        help="When on, typing 'find' picks the best trade and places it "
             "automatically (with stop-loss + take-profit) using your settings "
             "above — no Yes/No. It skips a trade only if the AI says NO-GO.")
    if ss.auto_mode:
        if cfg.live:
            st.warning("⚠️ Auto mode with **LIVE** money places REAL orders with "
                       "no confirmation. Use paper mode to practice.")
        else:
            st.info("Auto mode is ON (paper). Type **find** and it trades on its own.")
    if st.button("Disconnect / change keys", use_container_width=True):
        for k in ("connected", "broker", "cfg", "groq_key", "pending"):
            ss[k] = False if k == "connected" else (None if k != "groq_key" else "")
        ss.messages = []
        st.rerun()

st.divider()

# --- Chat history ---
for msg in ss.messages:
    with st.chat_message(msg["role"], avatar="📈" if msg["role"] == "assistant" else None):
        st.markdown(msg["content"])

# --- Yes/No buttons when a trade is waiting ---
if ss.pending is not None:
    yn = st.columns(2)
    if yn[0].button("✅ Yes, place it", use_container_width=True, type="primary"):
        place_pending()
        st.rerun()
    if yn[1].button("❌ No, skip", use_container_width=True):
        ss.pending = None
        say("assistant", "👍 Skipped. Type **find** whenever you want another.")
        st.rerun()

# --- Chat input ---
prompt = st.chat_input("Type 'find' to find a trade…")
if prompt:
    handle_command(prompt)
    st.rerun()
