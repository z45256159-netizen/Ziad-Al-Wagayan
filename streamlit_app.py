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
import random

import streamlit as st
from ai import ai_choose, verify_key
from analysis import explain_pattern, invest_analysis
from broker import Broker, BrokerError
from chart import make_position_chart, tradingview_url
from config import Config, ConfigError
from sizing import TradePlan, build_trade_plan
from strategy import detect_pattern, direction_of, rank_relaxed
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
ss.setdefault("view", None)        # {plan, bars} for the chart of the latest pick


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


# --- "Remember me on this device" via browser localStorage (best-effort) ---
def _local_storage():
    """Return a LocalStorage handle, or None if the component isn't available.
    Everything here is wrapped so a failure never breaks the app."""
    try:
        from streamlit_local_storage import LocalStorage
        return LocalStorage()
    except Exception:
        return None


def ls_get(ls, name: str) -> str:
    if ls is None:
        return ""
    try:
        return (ls.getItem(name) or "").strip()
    except Exception:
        return ""


def ls_save(ls, alp_key: str, alp_sec: str, groq_key: str, live: bool) -> None:
    if ls is None:
        return
    try:
        ls.setItem("ALPACA_API_KEY", alp_key, key="ls_ak")
        ls.setItem("ALPACA_API_SECRET", alp_sec, key="ls_as")
        ls.setItem("GROQ_API_KEY", groq_key, key="ls_gk")
        ls.setItem("LIVE", "true" if live else "false", key="ls_lv")
    except Exception:
        pass


def ls_clear(ls) -> None:
    if ls is None:
        return
    for name, wk in (("ALPACA_API_KEY", "d_ak"), ("ALPACA_API_SECRET", "d_as"),
                     ("GROQ_API_KEY", "d_gk"), ("LIVE", "d_lv")):
        try:
            ls.deleteItem(name, key=wk)
        except Exception:
            pass


if not ss.connected:
    # 1) Auto-connect from Secrets (instant, reliable) if present.
    saved_key = _secret("ALPACA_API_KEY").strip()
    saved_sec = _secret("ALPACA_API_SECRET").strip()
    if saved_key and saved_sec and not ss.get("auto_tried"):
        ss.auto_tried = True
        with st.spinner("Connecting…"):
            ok, _err = _connect(saved_key, saved_sec, _secret("GROQ_API_KEY"),
                                _live_flag())
        if ok:
            st.rerun()

    # 2) Otherwise auto-connect from this device's saved keys (Remember me).
    # One LocalStorage handle per run (its constructor uses a fixed widget key).
    _ls = _local_storage()
    if not ss.connected and not ss.get("ls_tried"):
        rk, rs = ls_get(_ls, "ALPACA_API_KEY"), ls_get(_ls, "ALPACA_API_SECRET")
        # Only mark "tried" once the browser has actually returned the keys —
        # on the first render storage may still be loading.
        if rk and rs:
            ss.ls_tried = True
            with st.spinner("Connecting…"):
                ok, _err = _connect(rk, rs, ls_get(_ls, "GROQ_API_KEY"),
                                    ls_get(_ls, "LIVE") == "true")
            if ok:
                st.rerun()

if not ss.connected:
    st.title("📈 Alpaca Trading Bot")
    st.caption("Enter your keys once. Tick **Remember me** and this device won't "
               "ask again.")

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
        remember = st.checkbox("💾 Remember me on this device", value=True)
        submitted = st.form_submit_button("Connect", use_container_width=True,
                                          type="primary")

    if submitted:
        with st.spinner("Checking your keys…"):
            ok, err = _connect(alp_key, alp_sec, groq_key, live)
        if ok:
            if remember:
                # Reuse the same handle created above (don't build a second one).
                ls_save(_ls, alp_key.strip(), alp_sec.strip(),
                        groq_key.strip(), live)
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
    ss.view = None  # clear any previous chart until we have a fresh plan
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

    # ONE broad filter: rank all stocks as "movers" (by momentum + volume);
    # nothing is rejected up front. The AI then looks for the best setup.
    movers = rank_relaxed(bars)
    if not movers:
        return ("Couldn't score any stock (not enough price history). "
                "Try again in a moment."), None

    try:
        held = broker.held_symbols()
    except BrokerError as exc:
        return f"⚠️ Couldn't check your positions: {exc}", None
    tradeable = [c for c in movers if c.symbol not in held]
    if not tradeable:
        return ("Every mover is already in your portfolio — skipping to avoid "
                "doubling up."), None

    dmode = ss.direction_mode         # "Both" / "Long only" / "Short only"
    forced = ("short" if dmode == "Short only"
              else "long" if dmode == "Long only" else None)

    def allowed(d: str) -> bool:
        return d in ("long", "short") if forced is None else d == forced

    investing = (ss.scan_mode == "Best performers")

    if investing:
        # Long-term: rank by ~3-month performance, buy the leaders.
        def perf(c):
            b = bars[c.symbol]
            base = b[-60].close if len(b) >= 60 else b[0].close
            return (b[-1].close - base) / base if base > 0 else 0.0
        ordered = sorted(tradeable, key=perf, reverse=True)
        pool = [c for c in ordered if c.symbol not in ss.recent] or ordered
        pool_top = pool[:8]
        direction = "long"
    else:
        fresh = [c for c in tradeable if c.symbol not in ss.recent]
        pool_top = (fresh if fresh else tradeable)[:10]
        direction = None  # decided per-candidate below

    # Detect chart pattern for each candidate in the working pool (for the chart).
    pattern_marks = {}
    for c in pool_top:
        label, marks = detect_pattern(bars[c.symbol][-40:])
        c.pattern = label
        pattern_marks[c.symbol] = marks

    # Technical: prefer setups matching the allowed direction.
    if not investing:
        setups = [c for c in pool_top if allowed(direction_of(c.pattern))]
        if setups:
            pool_top = setups

    weights = [max(abs(c.score), 1e-4) for c in pool_top]
    candidate = random.choices(pool_top, weights=weights, k=1)[0]

    ai = None
    if ss.groq_key:
        subset = random.sample(pool_top, min(6, len(pool_top)))
        ai = ai_choose(subset, ss.groq_key)
        if ai is not None:
            match = next((c for c in subset if c.symbol == ai.symbol), None)
            if match is not None:
                candidate = match

    try:
        buying_power = broker.get_buying_power()
    except BrokerError as exc:
        return f"⚠️ Couldn't read your buying power: {exc}", None

    ss.recent = ([candidate.symbol] + ss.recent)[:3]
    sym = candidate.symbol
    chart_bars = bars[sym][-40:]

    # =====================================================================
    # BEST PERFORMERS  →  long-term investing view (verdict, horizon, news)
    # =====================================================================
    if investing:
        a = invest_analysis(bars[sym], candidate.atr)
        entry = round(candidate.last_price, 2)
        budget = min(0.10 * buying_power, ss.max_order * 3, buying_power)
        qty = int(budget // entry) or (1 if entry <= buying_power else 0)
        if qty < 1:
            return (f"One share of {sym} (${entry:,.2f}) is more than your "
                    "buying power."), None
        exp_pct = a.exp_return_pct if a.exp_return_pct > 0 else a.downside_pct * 1.2
        stop = round(entry * (1 - a.downside_pct / 100), 2)
        target = round(entry * (1 + exp_pct / 100), 2)
        risk_total = round(qty * (entry - stop), 2)
        reward_total = round(qty * (target - entry), 2)
        rr = round(reward_total / risk_total, 2) if risk_total else 0.0
        plan = TradePlan(sym, qty, entry, stop, target, round(qty * entry, 2),
                         risk_total, reward_total, rr, a.downside_pct, exp_pct,
                         side="buy", direction="long")

        emoji = {"GOOD": "🟢", "OKAY": "🟡", "AVOID": "🔴"}.get(a.verdict, "🟡")
        parts = [f"### 📈 {sym} — long-term pick @ ${entry:,.2f}"]
        parts.append(f"**Verdict: {emoji} {a.verdict}** · {a.perf_pct:+.0f}% "
                     "over ~3 months")
        parts.append(f"**Why:** {a.reason}")
        parts.append(
            "**📊 Plan (buy & hold)**\n"
            f"- 🟢 **Buy {qty} share(s)** of {sym} (~${plan.cost:,.2f}) — "
            f"{plan.cost / buying_power * 100:.0f}% of buying power\n"
            f"- ⏳ Suggested hold: **{a.horizon}**\n"
            f"- 📈 If it keeps its pace: **~+{exp_pct:.0f}%** (≈ +${reward_total:,.0f}) "
            f"→ target ${target:,.2f}\n"
            f"- 📉 Downside if wrong: **~-{a.downside_pct:.0f}%** (≈ -${risk_total:,.0f}) "
            f"→ protective stop ${stop:,.2f}\n"
            f"- ⚖️ Reward:risk ≈ **1 : {rr:g}**")
        news = broker.get_news(sym)
        if news:
            parts.append("**📰 Recent news:**\n" +
                         "\n".join(f"- {h}" + (f" ({s})" if s else "")
                                   for h, s in news))
        else:
            parts.append("_No recent news found for this ticker._")
        parts.append("_These are rough estimates for learning, not guarantees._")
        if market_open is False:
            parts.append("_Market is closed — the order will queue until it opens._")
        parts.append("Place it? Tap **✅ Yes** or **❌ No** (buy + protective stop "
                     "+ target, placed as one bracket).")
        ss.view = {"plan": plan, "bars": chart_bars,
                   "support": candidate.support, "resistance": candidate.resistance,
                   "marks": []}
        return "\n\n".join(parts), {"plan": plan, "ai": ai}

    # =====================================================================
    # TECHNICAL PATTERNS  →  short-term day-trade view (why & how)
    # =====================================================================
    if direction is None:
        direction = direction_of(candidate.pattern)
        if not allowed(direction):
            direction = forced or "long"

    plan = build_trade_plan(
        score=candidate, buying_power=buying_power, risk_pct=ss.risk_pct,
        max_order_dollars=ss.max_order, stop_atr_mult=ss.stop_mult,
        reward_risk=ss.reward_risk, direction=direction)

    pattern = (ai.pattern if (ai is not None and ai.pattern) else candidate.pattern)
    parts = [f"### 📊 {sym} @ ${plan.entry:,.2f}"]
    parts.append(f"📐 **Setup found: {pattern}**")
    info = explain_pattern(candidate.pattern)
    if info:
        what, why, how = info
        parts.append(f"**How I found it:** {sym} is showing {what}.\n\n"
                     f"**Why it's a signal:** {why}.\n\n"
                     f"**How to trade it:** {how}.")
    parts.append(f"**The numbers:** {candidate.reason}")
    if ai is not None:
        badge = {"GO": "🟢", "CAUTION": "🟡", "NO-GO": "🔴"}.get(ai.recommendation, "🤖")
        parts.append(f"🤖 **AI ({ai.confidence} confidence): {badge} "
                     f"{ai.recommendation}** — {ai.rationale}")
    if market_open is False:
        parts.append("_Market is closed — the order will queue until it opens._")

    if not plan.ok:
        parts.append(f"⚠️ Can't build an order: {plan.skipped_reason}")
        return "\n\n".join(parts), None

    if plan.direction == "short":
        action = f"🔻 **SHORT-SELL {plan.qty} share(s)**"
        dir_note = "_Short = you profit if the price **falls**._"
        stop_side, tp_side = "above", "below"
    else:
        action = f"🟢 **BUY {plan.qty} share(s)**"
        dir_note = "_Long = you profit if the price **rises**._"
        stop_side, tp_side = "below", "above"

    parts.append(
        "**📋 Trade plan**\n"
        f"- {action} of {plan.symbol} at ~${plan.entry:,.2f}  {dir_note}\n"
        f"- 💵 Cost: **${plan.cost:,.2f}**\n"
        f"- 🛑 Stop-loss: **${plan.stop:,.2f}** ({stop_side}, {plan.stop_pct:.1f}%) "
        f"→ risk **${plan.risk_total:,.2f}** if it hits\n"
        f"- 🎯 Take-profit: **${plan.take_profit:,.2f}** ({tp_side}, "
        f"{plan.tp_pct:.1f}%) → profit **${plan.reward_total:,.2f}** if it hits\n"
        f"- ⚖️ Risk/reward: **1 : {plan.rr_ratio:g}**")
    parts.append(
        f"📈 **[Open {plan.symbol} on TradingView]({tradingview_url(plan.symbol)})** "
        "— chart with your levels below.\n\n"
        "_On TradingView: Long Position tool → "
        f"Entry ${plan.entry:,.2f} · Stop ${plan.stop:,.2f} · "
        f"Target ${plan.take_profit:,.2f}._")
    parts.append("Place it? Tap **✅ Yes** or **❌ No** (buy + stop-loss + "
                 "take-profit, placed as one bracket).")

    ss.view = {"plan": plan, "bars": chart_bars,
               "support": candidate.support, "resistance": candidate.resistance,
               "marks": pattern_marks.get(sym, [])}
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
            side=plan.side,
        )
        status = getattr(order.status, "value", order.status)
        entry_word = "SHORT-SELL" if plan.side == "sell" else "BUY"
        exit_word = "BUY-to-cover" if plan.side == "sell" else "SELL"
        say("assistant",
            f"✅ **3 orders placed** for **{order.symbol}** (bracket):\n"
            f"1. **{entry_word} {order.qty}** @ market — status *{status}*\n"
            f"2. 🛑 **{exit_word} stop-loss** @ ${plan.stop:,.2f}\n"
            f"3. 🎯 **{exit_word} take-profit** @ ${plan.take_profit:,.2f}\n\n"
            f"The stop and target trigger automatically — whichever hits first "
            f"cancels the other. (Order id `{order.id}`.)\n\n"
            f"_If the market is closed the entry queues until 9:30am ET, and the "
            f"stop/target activate once it fills._")
    except BrokerError as exc:
        say("assistant", f"❌ Order failed: {exc}\n\n_(If it mentions the market "
                         "being closed or a bracket rule, try during market "
                         "hours.)_")


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
ss.setdefault("scan_mode", "Technical patterns")
ss.setdefault("direction_mode", "Both")

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
    ss.scan_mode = st.radio(
        "How to find trades",
        ["Technical patterns", "Best performers"],
        index=["Technical patterns", "Best performers"].index(ss.scan_mode),
        help="Technical patterns = find chart setups (head & shoulders, double "
             "bottom, breakout…). Best performers = just take the strongest "
             "movers.")
    ss.direction_mode = st.radio(
        "Trade direction",
        ["Both", "Long only", "Short only"],
        index=["Both", "Long only", "Short only"].index(ss.direction_mode),
        help="Long = buy (profit if it rises). Short = sell (profit if it falls).")
    st.markdown("---")
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
        ls_clear(_local_storage())   # forget saved keys on this device
        for k in ("connected", "broker", "cfg", "groq_key", "pending"):
            ss[k] = False if k == "connected" else (None if k != "groq_key" else "")
        ss.messages = []
        ss.auto_tried = False
        ss.ls_tried = False
        st.rerun()

# Current-mode badge so you always know what 'find' will do.
_mode_icon = "📈 Investing (best performers)" if ss.scan_mode == "Best performers" \
    else "📐 Technical (patterns)"
_dir_icon = {"Both": "↔ Long & Short", "Long only": "🟢 Long only",
             "Short only": "🔻 Short only"}.get(ss.direction_mode, ss.direction_mode)
st.caption(f"🔎 Mode: **{_mode_icon}**  ·  {_dir_icon}  "
           f"·  {'🤖 Auto' if ss.auto_mode else '✋ Manual'}")

st.divider()

# --- Chat history ---
for msg in ss.messages:
    with st.chat_message(msg["role"], avatar="📈" if msg["role"] == "assistant" else None):
        st.markdown(msg["content"])

# --- Chart of the latest pick (entry / stop / target drawn on candles) ---
view = ss.get("view")
if view:
    fig = make_position_chart(view["bars"], view["plan"],
                              support=view.get("support"),
                              resistance=view.get("resistance"),
                              marks=view.get("marks"))
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
        st.link_button("📈 Open on TradingView",
                       tradingview_url(view["plan"].symbol),
                       use_container_width=True)

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
