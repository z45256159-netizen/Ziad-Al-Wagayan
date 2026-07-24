"""
Alpaca trading bot — chat-style website.

Flow:
  1. Paste your Alpaca keys once. The app checks them. That's all it needs.
  2. Tap "🔎 Find me a trade" (or type "find"): the built-in engine scans, and
     if a setup clears the reward:risk bar it shows the reason and asks you.
  3. Tap Yes to place it (bracket: entry + stop-loss + take-profit), No to skip.

Run locally:  streamlit run streamlit_app.py
Deploy free:  see README (Streamlit Community Cloud).
"""

from __future__ import annotations

import os
import random

import streamlit as st
from analysis import explain_setup, invest_analysis, news_sentiment
from broker import Broker, BrokerError
from chart import make_position_chart, tradingview_url
from config import Config, ConfigError
from engine import MIN_RR, build_setup
from sizing import TradePlan
from strategy import (detect_pattern, direction_of, rank_candidates,
                      rank_relaxed)
from universe import UNIVERSE, describe

st.set_page_config(page_title="Alpaca Trading Bot", page_icon="📈", layout="centered")

ss = st.session_state
ss.setdefault("connected", False)
ss.setdefault("messages", [])      # chat history: [{"role","content"}]
ss.setdefault("pending", None)     # trade awaiting yes/no: {"c","s","ai"}
ss.setdefault("broker", None)
ss.setdefault("cfg", None)
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
def _connect(alp_key, alp_sec, live):
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

    ss.broker, ss.cfg = broker, cfg
    ss.connected, ss.messages, ss.pending = True, [], None
    say("assistant",
        f"✅ Connected to Alpaca ({cfg.mode_name}).\n\n"
        f"Tap **🔎 Find me a trade** (or type **find**). You can also type "
        f"**balance** or **positions**.")
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


def ls_save(ls, alp_key: str, alp_sec: str, live: bool) -> None:
    if ls is None:
        return
    try:
        ls.setItem("ALPACA_API_KEY", alp_key, key="ls_ak")
        ls.setItem("ALPACA_API_SECRET", alp_sec, key="ls_as")
        ls.setItem("LIVE", "true" if live else "false", key="ls_lv")
    except Exception:
        pass


def ls_clear(ls) -> None:
    if ls is None:
        return
    for name, wk in (("ALPACA_API_KEY", "d_ak"), ("ALPACA_API_SECRET", "d_as"),
                     ("LIVE", "d_lv")):
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
            ok, _err = _connect(saved_key, saved_sec, _live_flag())
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
                ok, _err = _connect(rk, rs, ls_get(_ls, "LIVE") == "true")
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
        live = st.checkbox("⚠️ Live trading (REAL money)", value=_live_flag())
        remember = st.checkbox("💾 Remember me on this device", value=True)
        submitted = st.form_submit_button("Connect", use_container_width=True,
                                          type="primary")

    st.caption("That's all you need — no other keys, no sign-ups. The trade "
               "engine is built in.")

    if submitted:
        with st.spinner("Checking your keys…"):
            ok, err = _connect(alp_key, alp_sec, live)
        if ok:
            if remember:
                # Reuse the same handle created above (don't build a second one).
                ls_save(_ls, alp_key.strip(), alp_sec.strip(), live)
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


def _setup_to_plan(setup) -> TradePlan:
    """Convert an engine Setup into the TradePlan the placing/UI code expects."""
    return TradePlan(
        symbol=setup.symbol, qty=setup.qty, entry=setup.entry, stop=setup.stop,
        take_profit=setup.target, cost=round(setup.qty * setup.entry, 2),
        risk_total=setup.risk_total, reward_total=setup.reward_total,
        rr_ratio=setup.rr, stop_pct=setup.stop_pct, tp_pct=setup.tp_pct,
        side=setup.side, direction=setup.direction, limit_price=setup.limit_price)


def build_trade(allow_fallback=True):
    """Scan and pick one trade. Returns (assistant_text, pending_dict_or_None).

    allow_fallback: when True (manual find), if nothing clears the reward:risk
    bar we surface the best-available setup clearly flagged. When False (the
    auto-trader), we stay strict and place nothing rather than a sub-par trade.
    """
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
    # nothing is rejected up front. The engine then validates the best setup.
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

    try:
        buying_power = broker.get_buying_power()
    except BrokerError as exc:
        return f"⚠️ Couldn't read your buying power: {exc}", None

    dmode = ss.direction_mode         # "Both" / "Long only" / "Short only"
    forced = ("short" if dmode == "Short only"
              else "long" if dmode == "Long only" else None)

    def allowed(d: str) -> bool:
        return d in ("long", "short") if forced is None else d == forced

    if ss.scan_mode == "Best performers":
        return _build_investing(bars, tradeable, buying_power, market_open)
    return _build_technical(bars, tradeable, buying_power, market_open,
                            forced, allowed, allow_fallback)


# ---------------------------------------------------------------------------
# BEST PERFORMERS  →  long-term investing view (quality score, horizon, news)
# ---------------------------------------------------------------------------
def _build_investing(bars, tradeable, buying_power, market_open):
    benchmark = bars.get("SPY")

    # Rank the field by ~3-month performance, then keep a fresh pool.
    def perf(c):
        b = bars[c.symbol]
        base = b[-60].close if len(b) >= 60 else b[0].close
        return (b[-1].close - base) / base if base > 0 else 0.0
    ordered = sorted(tradeable, key=perf, reverse=True)
    pool = [c for c in ordered if c.symbol not in ss.recent] or ordered
    pool_top = pool[:8]

    # Analyse each candidate for QUALITY (trend, relative strength, drawdown,
    # 1-year return) and pick among the best few — not just the top mover.
    scored = []
    for c in pool_top:
        a = invest_analysis(bars[c.symbol], c.atr, benchmark_bars=benchmark)
        scored.append((a.score, c, a))
    scored.sort(key=lambda t: t[0], reverse=True)
    _, candidate, a = random.choice(scored[:3])
    sym = candidate.symbol
    ss.recent = ([sym] + ss.recent)[:3]
    price = round(candidate.last_price, 2)
    chart_bars = bars[sym][-40:]

    # News sentiment can nudge the verdict up or down one notch.
    news = broker.get_news(sym)
    sent_label, sent_emoji, _ = news_sentiment(news)
    ladder = ["AVOID", "HOLD", "BUY", "STRONG BUY"]
    verdict = a.verdict
    if verdict in ladder:
        i = ladder.index(verdict)
        if sent_label == "BEARISH" and i > 0:
            verdict = ladder[i - 1]
        elif sent_label == "BULLISH" and i < len(ladder) - 1:
            verdict = ladder[i + 1]

    # Entry: buy now (market) or a limit on a dip.
    if ss.invest_entry == "Limit on a dip":
        entry = round(price * (1 - ss.invest_dip_pct / 100), 2)
        limit_price = entry
    else:
        entry = price
        limit_price = None

    budget = min(0.10 * buying_power, ss.max_order * 3, buying_power)
    qty = int(budget // entry) or (1 if entry <= buying_power else 0)
    if qty < 1:
        return (f"One share of {sym} (${entry:,.2f}) is more than your "
                "buying power."), None

    exp_pct, dn_pct = a.exp_return_pct, a.downside_pct
    stop = round(entry * (1 - dn_pct / 100), 2)
    target = round(entry * (1 + exp_pct / 100), 2)
    risk_total = round(qty * (entry - stop), 2)
    reward_total = round(qty * (target - entry), 2)
    rr = round(reward_total / risk_total, 2) if risk_total else 0.0
    plan = TradePlan(sym, qty, entry, stop, target, round(qty * entry, 2),
                     risk_total, reward_total, rr, dn_pct, exp_pct,
                     side="buy", direction="long", limit_price=limit_price)

    emoji = {"STRONG BUY": "🟢", "BUY": "🟢", "HOLD": "🟡",
             "AVOID": "🔴"}.get(verdict, "🟡")
    parts = [f"### 📈 {sym} — long-term pick @ ${price:,.2f}"]
    parts.append(f"**What it is:** {describe(sym)}")
    parts.append(f"**Verdict: {emoji} {verdict}** · quality **{a.score}/100** · "
                 f"3-month {a.perf_pct:+.0f}% · 1-year {a.perf_1y_pct:+.0f}% · "
                 f"rel. strength {a.rel_strength:+.0f}% · news {sent_emoji} "
                 f"{sent_label.title()}")
    if a.reasons:
        parts.append("**Why:**\n" + "\n".join(f"- {r}" for r in a.reasons))
    parts.append(f"_{a.reason}_")
    if verdict == "AVOID":
        parts.append("⚠️ _This one doesn't meet the quality bar right now — "
                     "shown for transparency. Consider skipping._")

    if limit_price:
        entry_line = (f"- 🟢 **Buy {qty} share(s)** with a **LIMIT at "
                      f"${entry:,.2f}** (−{ss.invest_dip_pct:.0f}% — fills only "
                      f"if it dips there)")
    else:
        entry_line = (f"- 🟢 **Buy {qty} share(s) now** (~${plan.cost:,.2f}) — "
                      f"{plan.cost / buying_power * 100:.0f}% of buying power")
    parts.append(
        "**📊 Plan (buy & hold)**\n"
        f"{entry_line}\n"
        f"- ⏳ Suggested hold: **{a.horizon}**\n"
        f"- 📈 If it keeps its pace: **~+{exp_pct:.0f}%** (≈ +${reward_total:,.0f}) "
        f"→ target ${target:,.2f}\n"
        f"- 📉 Downside if wrong: **~-{dn_pct:.0f}%** (≈ -${risk_total:,.0f}) "
        f"→ protective stop ${stop:,.2f}\n"
        f"- ⚖️ Reward:risk ≈ **1 : {rr:g}**")
    if news:
        parts.append("**📰 Recent news:**\n" +
                     "\n".join(f"- {h}" + (f" ({s})" if s else "")
                               for h, s in news))
    else:
        parts.append("_No recent news found for this ticker._")
    parts.append("_Price-based estimates for learning, not guarantees or a "
                 "substitute for company fundamentals._")
    if market_open is False:
        parts.append("_Market is closed — the order will queue until it opens._")
    parts.append("Place it? Tap **✅ Yes** or **❌ No** (entry + protective "
                 "stop + target, placed as one bracket).")
    ss.view = {"plan": plan, "bars": chart_bars,
               "support": candidate.support, "resistance": candidate.resistance,
               "marks": []}
    return "\n\n".join(parts), {"plan": plan, "below_bar": False}


# ---------------------------------------------------------------------------
# TECHNICAL  →  engine scan: only take a VALIDATED, risk-gated setup, or none.
# ---------------------------------------------------------------------------
def _build_technical(bars, tradeable, buying_power, market_open, forced, allowed,
                     allow_fallback=True):
    fresh = [c for c in tradeable if c.symbol not in ss.recent]
    pool_top = (fresh if fresh else tradeable)[:12]

    # Detect chart patterns (for the chart + the pattern-implied direction).
    pattern_marks = {}
    for c in pool_top:
        label, marks = detect_pattern(bars[c.symbol][-40:])
        c.pattern = label
        pattern_marks[c.symbol] = marks

    # Build a validated setup for each name. Only geometry-valid, reward:risk-
    # passing setups survive. Nothing is forced.
    def scan(min_rr):
        out = []
        for c in pool_top:
            pd = direction_of(c.pattern)
            dirs = [pd] + [d for d in ("long", "short") if d != pd] \
                if (forced is None and pd in ("long", "short")) \
                else ([forced] if forced else ["long", "short"])
            for d in dirs:
                if not allowed(d):
                    continue
                s = build_setup(c.symbol, bars[c.symbol], d, buying_power,
                                ss.risk_pct, ss.max_order,
                                vol_ratio=getattr(c, "volume_ratio", 1.0),
                                news_score=0, min_rr=min_rr)
                if s.ok:
                    out.append((c, s))
                    break  # one direction per symbol
        return out

    valid = scan(ss.min_rr)
    below_bar = False
    if not valid:
        # Nothing cleared your bar. Rather than always saying "no trade", find
        # the closest COHERENT setup (still a real stop/target, just a lower
        # reward:risk) so you have something to look at — clearly flagged, and
        # never auto-placed.
        if not allow_fallback:
            return (f"🚫 **No trade right now.** Nothing on the watchlist offered "
                    f"a clean setup at your minimum reward:risk (1:{ss.min_rr:g}) "
                    f"in the allowed direction. Auto-trader stays strict and "
                    f"skips."), None
        near = scan(0.0)
        if not near:
            return ("🚫 **No trade right now.** I couldn't build a single "
                    "coherent setup on the watchlist in your allowed direction "
                    "(often the case when the market's been flat/closed). Try "
                    "**find** again later, or switch direction to **Both** in "
                    "settings."), None
        near.sort(key=lambda t: t[1].rr, reverse=True)  # best available R:R
        candidate, setup = near[0]
        below_bar = True
    else:
        # Pick among the top-confidence setups for a little variety.
        valid.sort(key=lambda t: t[1].confidence, reverse=True)
        candidate, setup = random.choice(valid[:3])
    sym = candidate.symbol
    ss.recent = ([sym] + ss.recent)[:3]
    chart_bars = bars[sym][-40:]

    # News for the winner → fold its sentiment into the confidence.
    news = broker.get_news(sym)
    sent_label, sent_emoji, _ = news_sentiment(news)
    news_score = 1 if sent_label == "BULLISH" else -1 if sent_label == "BEARISH" else 0

    # Rebuild with the news score folded in (keep the same direction).
    rebuilt = build_setup(sym, bars[sym], setup.direction, buying_power, ss.risk_pct,
                          ss.max_order, vol_ratio=getattr(candidate, "volume_ratio", 1.0),
                          news_score=news_score, min_rr=0.0 if below_bar else ss.min_rr)
    if rebuilt.ok:
        setup = rebuilt

    plan = _setup_to_plan(setup)

    parts = [f"### 📊 {sym} @ ${plan.entry:,.2f}"]
    if below_bar:
        parts.append(f"⚠️ **Below your quality bar** — nothing hit your minimum "
                     f"reward:risk (1:{ss.min_rr:g}) today, so this is just the "
                     f"**best available** setup (1:{plan.rr_ratio:g}). Consider "
                     f"skipping, or lower the bar in Settings.")
    parts.append(f"**Market regime:** {setup.regime}")
    conf_blocks = "█" * (setup.confidence // 10) + "░" * (10 - setup.confidence // 10)
    parts.append(f"**Confidence: {setup.confidence}/100**  `{conf_blocks}`")
    parts.append(f"📐 **Setup found: {candidate.pattern}**")
    info = explain_setup(candidate.pattern, candidate.support,
                         candidate.resistance, plan.entry,
                         pattern_marks.get(sym, []))
    if info:
        what, why, how = info
        parts.append(f"**How I found it:** {sym} shows {what}.\n\n"
                     f"**Why it's a signal:** {why}.\n\n"
                     f"**How to trade it:** {how}.")
    if setup.reasons:
        parts.append("**Why this trade:**\n" +
                     "\n".join(f"- {r}" for r in setup.reasons))
    parts.append(f"**News:** {sent_emoji} {sent_label.title()}")
    if news:
        parts.append("**📰 Recent headlines:**\n" +
                     "\n".join(f"- {h}" + (f" ({s})" if s else "")
                               for h, s in news[:3]))
    if market_open is False:
        parts.append("_Market is closed — the order will queue until it opens._")

    if plan.direction == "short":
        action = f"🔻 **SHORT-SELL {plan.qty} share(s)**"
        dir_note = "_Short = you profit if the price **falls**._"
        stop_side, tp_side = "above", "below"
    else:
        action = f"🟢 **BUY {plan.qty} share(s)**"
        dir_note = "_Long = you profit if the price **rises**._"
        stop_side, tp_side = "below", "above"

    entry_word = (f"LIMIT ${plan.limit_price:,.2f}" if plan.limit_price
                  else f"~${plan.entry:,.2f}")
    parts.append(
        "**📋 Trade plan**\n"
        f"- {action} of {plan.symbol} at {entry_word}  {dir_note}\n"
        f"- 💵 Cost: **${plan.cost:,.2f}**\n"
        f"- 🛑 Stop-loss: **${plan.stop:,.2f}** ({stop_side} entry, {plan.stop_pct:.1f}%) "
        f"→ risk **${plan.risk_total:,.2f}** if it hits\n"
        f"- 🎯 Take-profit: **${plan.take_profit:,.2f}** ({tp_side} entry, "
        f"{plan.tp_pct:.1f}%) → profit **${plan.reward_total:,.2f}** if it hits\n"
        f"- ⚖️ Risk/reward: **1 : {plan.rr_ratio:g}**  _(min 1:{ss.min_rr:g})_")
    parts.append(
        f"📈 **[Open {plan.symbol} on TradingView]({tradingview_url(plan.symbol)})** "
        "— chart with your levels below.")
    parts.append("Place it? Tap **✅ Yes** or **❌ No** (entry + stop-loss + "
                 "take-profit, placed as one bracket).")

    ss.view = {"plan": plan, "bars": chart_bars,
               "support": candidate.support, "resistance": candidate.resistance,
               "marks": pattern_marks.get(sym, [])}
    return "\n\n".join(parts), {"plan": plan, "below_bar": below_bar}


def guardrail_block(cost: float):
    """Return a reason string if a SAFETY rule should block a new trade, else
    None. Applies to both manual (Yes) and the hands-free auto-trader."""
    try:
        a = broker.get_account()
    except BrokerError:
        return None  # can't check → don't block (fail open, but rare)

    limit = float(ss.get("daily_loss_limit", 0) or 0)
    if limit > 0:
        try:
            dollars, _ = broker.today_pl()
        except BrokerError:
            dollars = 0.0
        if dollars <= -abs(limit):
            return (f"🛑 **Daily loss limit hit.** You're down ${-dollars:,.0f} "
                    f"today (your limit is ${limit:,.0f}). No new trades for now — "
                    f"come back tomorrow, or change the limit in ⚙️ Settings → "
                    f"Safety.")

    try:
        n = len(broker.get_positions())
    except BrokerError:
        n = 0
    if n >= int(ss.get("max_positions", 10)):
        return (f"🛑 **Max open positions reached** ({n}). Close one first, or "
                f"raise the limit in ⚙️ Settings → Safety.")

    pv = float(getattr(a, "portfolio_value", 0) or 0)
    max_pct = float(ss.get("max_pos_pct", 25))
    if pv > 0 and cost > pv * max_pct / 100:
        return (f"🛑 **That trade is too big** — ${cost:,.0f} is over {max_pct:.0f}% "
                f"of your ${pv:,.0f} portfolio. Lower **Max per order** or raise "
                f"the limit in ⚙️ Settings → Safety.")
    return None


def place_pending() -> bool:
    """Place the pending bracket order. Returns True if it was actually sent."""
    plan = ss.pending["plan"]
    below_bar = ss.pending.get("below_bar", False)
    ss.pending = None

    block = guardrail_block(plan.cost)
    if block:
        say("assistant", block)
        return False

    try:
        order = broker.submit_bracket_order(
            symbol=plan.symbol,
            qty=plan.qty,
            take_profit=plan.take_profit,
            stop_loss=plan.stop,
            side=plan.side,
            limit_price=plan.limit_price,
        )
        status = getattr(order.status, "value", order.status)
        entry_word = "SHORT-SELL" if plan.side == "sell" else "BUY"
        exit_word = "BUY-to-cover" if plan.side == "sell" else "SELL"
        entry_desc = (f"LIMIT @ ${plan.limit_price:,.2f} (fills when price reaches it)"
                      if plan.limit_price else "@ market")
        # Log it to this session's track record.
        ss.trade_log.append({
            "symbol": plan.symbol, "side": plan.side, "qty": plan.qty,
            "entry": plan.entry, "stop": plan.stop, "target": plan.take_profit,
            "below_bar": below_bar,
        })
        say("assistant",
            f"✅ **3 orders placed** for **{order.symbol}** (bracket):\n"
            f"1. **{entry_word} {order.qty}** {entry_desc} — status *{status}*\n"
            f"2. 🛑 **{exit_word} stop-loss** @ ${plan.stop:,.2f}\n"
            f"3. 🎯 **{exit_word} take-profit** @ ${plan.take_profit:,.2f}\n\n"
            f"The stop and target trigger automatically — whichever hits first "
            f"cancels the other. (Order id `{order.id}`.)\n\n"
            f"_If the market is closed the entry queues until 9:30am ET, and the "
            f"stop/target activate once it fills._")
        return True
    except BrokerError as exc:
        say("assistant", f"❌ Order failed: {exc}\n\n_(If it mentions the market "
                         "being closed or a bracket rule, try during market "
                         "hours.)_")
        return False


def manage_exits() -> list:
    """Protect open winners: once a position is up enough, move its stop to
    breakeven, then trail it behind the price so profit is locked in. Returns a
    list of human-readable notes about what changed."""
    notes = []
    try:
        positions = broker.get_positions()
    except BrokerError:
        return notes
    be_trig = float(ss.get("be_trigger_pct", 1.5))
    trail = float(ss.get("trail_pct", 4.0))
    for p in positions:
        try:
            side = str(getattr(p, "side", "long")).lower()   # 'long' / 'short'
            entry = float(p.avg_entry_price)
            cur = float(p.current_price)
        except Exception:  # noqa: BLE001
            continue
        if entry <= 0 or cur <= 0:
            continue
        gain_pct = ((cur - entry) / entry * 100 if side == "long"
                    else (entry - cur) / entry * 100)
        if gain_pct < be_trig:
            continue  # not enough profit yet to bother protecting
        stop_order = broker.find_stop_order(p.symbol, side)
        if stop_order is None:
            continue
        try:
            cur_stop = float(getattr(stop_order, "stop_price", 0) or 0)
        except Exception:  # noqa: BLE001
            cur_stop = 0.0

        if side == "long":
            desired = max(entry, cur * (1 - trail / 100))      # breakeven, then trail
            new_stop = max(cur_stop, desired)
            if new_stop >= cur:                                # never at/above price
                new_stop = round(cur * 0.999, 2)
            improved = new_stop > cur_stop + max(0.01, cur_stop * 0.001)
        else:
            desired = min(entry, cur * (1 + trail / 100))
            new_stop = min(cur_stop, desired) if cur_stop > 0 else desired
            if new_stop <= cur:                                # never at/below price
                new_stop = round(cur * 1.001, 2)
            improved = new_stop < cur_stop - max(0.01, cur_stop * 0.001)

        if not improved:
            continue
        try:
            broker.replace_stop(getattr(stop_order, "id", None), round(new_stop, 2))
            notes.append(f"🛡️ **{p.symbol}**: stop moved up to ${new_stop:,.2f} "
                         f"(now +{gain_pct:.1f}% — profit protected).")
        except BrokerError as exc:
            notes.append(f"⚠️ **{p.symbol}**: couldn't move the stop ({exc}).")
    return notes


def auto_place_one() -> None:
    """One hands-free cycle: find a trade and place it (used by the auto-trader).
    Stays STRICT — only places a trade that fully cleared the quality gate."""
    msg, pending = build_trade(allow_fallback=False)
    say("assistant", "🔁 " + msg)
    if pending is not None:
        ss.pending = pending
        if place_pending():
            ss.auto_count += 1


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
        # Auto mode stays strict (no below-the-bar fallback); manual can see the
        # best-available pick.
        msg, pending = build_trade(allow_fallback=not ss.auto_mode)
        if pending is not None and ss.auto_mode:
            # In auto mode the trade already cleared the quality + reward:risk
            # gate, so we show it and place it automatically.
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
ss.setdefault("min_rr", MIN_RR)
ss.setdefault("auto_mode", False)
ss.setdefault("scan_mode", "Technical patterns")
ss.setdefault("direction_mode", "Both")
ss.setdefault("invest_entry", "Buy now (market)")
ss.setdefault("invest_dip_pct", 5.0)
ss.setdefault("auto_loop", False)      # hands-free auto-trader running?
ss.setdefault("auto_max", 5)           # max trades per run
ss.setdefault("auto_interval", 60)     # seconds between trades
ss.setdefault("auto_count", 0)         # trades placed this run
ss.setdefault("backtest", None)        # cached backtest summary text
ss.setdefault("trade_log", [])         # trades the bot placed this session
# --- Safety guardrails (0 / high default = effectively off until you set them) ---
ss.setdefault("daily_loss_limit", 0.0)  # $ down today that blocks new trades (0=off)
ss.setdefault("max_positions", 10)      # most open positions at once
ss.setdefault("max_pos_pct", 25.0)      # most % of portfolio in one new trade
# --- Smarter exits (protect winners) ---
ss.setdefault("protect_winners", False)
ss.setdefault("be_trigger_pct", 1.5)    # move stop to breakeven once up this %
ss.setdefault("trail_pct", 4.0)         # then trail the stop this far behind price

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
    if ss.scan_mode == "Best performers":
        ss.invest_entry = st.radio(
            "Investing — how to enter",
            ["Buy now (market)", "Limit on a dip"],
            index=["Buy now (market)", "Limit on a dip"].index(ss.invest_entry),
            help="Buy now = fills immediately. Limit on a dip = waits to buy "
                 "lower (e.g. -5%); fills only if the price comes down to it.")
        if ss.invest_entry == "Limit on a dip":
            ss.invest_dip_pct = st.slider("Buy this % below current price",
                                          1.0, 10.0, float(ss.invest_dip_pct), 0.5)
    st.markdown("---")
    ss.risk_pct = st.slider(
        "Risk per trade (% of buying power)", 0.25, 5.0,
        float(ss.risk_pct * 100), 0.25,
        help="How much of your account you're willing to lose if the stop hits. "
             "Drives how many shares.") / 100
    ss.max_order = float(st.slider(
        "Max per order ($)", 500, 20000, int(ss.max_order), 500,
        help="Hard ceiling on any single order."))
    ss.min_rr = st.slider(
        "Minimum reward : risk", 1.2, 4.0, float(ss.min_rr), 0.1,
        help="The engine REJECTS any technical trade whose target isn't at "
             "least this many times the risk away. Higher = pickier, fewer "
             "but better trades (quality over quantity).")
    st.caption(f"Now: risk {ss.risk_pct*100:.2f}% · max ${ss.max_order:,.0f} · "
               f"reject below reward:risk 1:{ss.min_rr:g}  ·  stops from "
               f"ATR + market structure (automatic).")

    st.markdown("---")
    ss.auto_mode = st.checkbox(
        "🤖 Auto mode — let the AI find AND place the trade by itself",
        value=ss.auto_mode,
        help="When on, typing 'find' picks the best validated trade and places "
             "it automatically (with stop-loss + take-profit) using your settings "
             "above — no Yes/No. If nothing passes the quality gate, it places "
             "nothing.")
    if ss.auto_mode:
        if cfg.live:
            st.warning("⚠️ Auto mode with **LIVE** money places REAL orders with "
                       "no confirmation. Use paper mode to practice.")
        else:
            st.info("Auto mode is ON (paper). Type **find** and it trades on its own.")

    st.markdown("---")
    st.markdown("**🛡️ Safety guardrails** — hard limits the bot can't cross.")
    ss.daily_loss_limit = float(st.number_input(
        "Stop trading if I'm down this much today ($) — 0 = off", 0, 100000,
        int(ss.daily_loss_limit), step=50,
        help="If your account is down this many dollars since yesterday's close, "
             "no new trades are opened (manual or auto). 0 turns it off."))
    ss.max_positions = int(st.number_input(
        "Max open positions at once", 1, 50, int(ss.max_positions),
        help="The bot won't open a new trade once you already hold this many."))
    ss.max_pos_pct = float(st.slider(
        "Max % of portfolio in one trade", 5.0, 100.0, float(ss.max_pos_pct), 5.0,
        help="Blocks any single trade bigger than this share of your whole "
             "portfolio, so you're never over-concentrated in one stock."))

    st.markdown("---")
    st.markdown("**🛡️ Protect winners** — trail the stop as a trade goes your way.")
    ss.protect_winners = st.checkbox(
        "Automatically move stops up on winners",
        value=ss.protect_winners,
        help="Once a trade is up enough, move its stop-loss to breakeven, then "
             "trail it behind the price — so a winner can't turn back into a "
             "loss. Runs while this page is open, and you can also do it on "
             "demand with the button below.")
    if ss.protect_winners:
        cpc = st.columns(2)
        ss.be_trigger_pct = float(cpc[0].slider(
            "Protect once up (%)", 0.5, 10.0, float(ss.be_trigger_pct), 0.5,
            help="Start protecting once the trade is up this much."))
        ss.trail_pct = float(cpc[1].slider(
            "Trail distance (%)", 1.0, 15.0, float(ss.trail_pct), 0.5,
            help="Keep the stop this far behind the current price as it climbs."))

    st.markdown("---")
    if st.button("Disconnect / change keys", use_container_width=True):
        ls_clear(_local_storage())   # forget saved keys on this device
        for k in ("connected", "broker", "cfg", "pending"):
            ss[k] = False if k == "connected" else None
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

# One-tap: no need to type. This is the main action.
if st.button("🔎 Find me a trade", type="primary", use_container_width=True):
    handle_command("find")
    st.rerun()

# --- 📈 How am I doing? — your REAL trades, not a backtest ---
with st.expander("📈 How am I doing? (my real trades)"):
    try:
        acct = broker.get_account()
        dpl, dpct = broker.today_pl()
        cc = st.columns(2)
        cc[0].metric("Today's P/L", f"${dpl:,.2f}", f"{dpct:+.2f}%")
        cc[1].metric("Portfolio value", f"${float(acct.portfolio_value):,.0f}")
    except BrokerError:
        st.caption("Couldn't read your account right now.")

    try:
        _positions = broker.get_positions()
    except BrokerError:
        _positions = []
    if _positions:
        greens = sum(1 for p in _positions
                     if float(getattr(p, "unrealized_pl", 0) or 0) >= 0)
        st.caption(f"**Open positions: {len(_positions)}**  ·  {greens} green / "
                   f"{len(_positions) - greens} red")
        for p in _positions:
            pl = float(getattr(p, "unrealized_pl", 0) or 0)
            plpc = float(getattr(p, "unrealized_plpc", 0) or 0) * 100
            side = str(getattr(p, "side", "long")).lower()
            emoji = "🟢" if pl >= 0 else "🔴"
            row = st.columns([3, 1])
            row[0].markdown(
                f"{emoji} **{p.symbol}** ({side}) ×{float(p.qty):g} · now "
                f"${float(p.current_price):,.2f} · P/L **${pl:,.2f}** "
                f"({plpc:+.1f}%)")
            if row[1].button("Close", key=f"close_{p.symbol}",
                             use_container_width=True):
                try:
                    broker.close_position(p.symbol)
                    say("assistant", f"✅ Closing **{p.symbol}** at market.")
                except BrokerError as exc:
                    say("assistant", f"❌ Couldn't close {p.symbol}: {exc}")
                st.rerun()
    else:
        st.caption("No open positions yet — tap **Find me a trade** to start.")

    _tl = ss.get("trade_log", [])
    if _tl:
        st.caption(f"**Placed this session:** {len(_tl)} — " +
                   ", ".join(f"{t['symbol']} ({t['side']})" for t in _tl[-8:]))

    if st.button("🛡️ Protect my winners now", use_container_width=True):
        _notes = manage_exits()
        if _notes:
            for _n in _notes:
                say("assistant", _n)
        else:
            say("assistant", "🛡️ No stops needed moving yet — no open position is "
                             "far enough in profit. I'll keep watching.")
        st.rerun()
    st.caption("_'Today's P/L' is your account since yesterday's close — the most "
               "honest read. Green/red counts your open trades right now._")

# --- 🔁 Hands-free auto-trader (paper practice; runs only while this page is open) ---
with st.expander("🔁 Auto-trader (hands-free)", expanded=ss.auto_loop):
    st.caption("Finds and places trades for you on a timer — **only while this "
               "page stays open** (it stops if you close/lock the phone). Paper "
               "practice only; it does not guarantee profit.")
    if not ss.auto_loop:
        cc = st.columns(2)
        ss.auto_max = int(cc[0].number_input("Max trades this run", 1, 50,
                                             int(ss.auto_max)))
        ss.auto_interval = int(cc[1].number_input("Seconds between trades", 30, 600,
                                                 int(ss.auto_interval), step=30))
        if st.button("▶️ Start auto-trading", type="primary",
                     use_container_width=True):
            ss.auto_loop = True
            ss.auto_count = 0
            say("assistant", f"🔁 **Auto-trader started** — up to {ss.auto_max} "
                             f"trades, one every {ss.auto_interval}s, using your "
                             f"current mode/settings. Keep this page open.")
            st.rerun()
    else:
        st.success(f"🔁 Running — **{ss.auto_count}/{ss.auto_max}** trades placed.")
        if st.button("⏹ Stop auto-trading", use_container_width=True):
            ss.auto_loop = False
            say("assistant", f"⏹ Auto-trader stopped after {ss.auto_count} trade(s).")
            st.rerun()

# The timer tick: drives the auto-trader AND/OR winner-protection while the page
# is open. Winner-protection alone uses a gentler 60s beat.
if ss.auto_loop or ss.protect_winners:
    _interval = int(ss.auto_interval) * 1000 if ss.auto_loop else 60000
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=_interval, key="loop_timer")
    except Exception:
        if ss.auto_loop:
            st.warning("Auto-refresh unavailable — auto-trader can't run on this "
                       "build.")
        ss.auto_loop = False

# Winner-protection: trail stops on any open winners each tick (no chat spam
# unless something actually moved).
if ss.protect_winners:
    try:
        if broker.is_market_open():
            for _n in manage_exits():
                say("assistant", _n)
    except BrokerError:
        pass

if ss.auto_loop:
    if ss.auto_count >= ss.auto_max:
        ss.auto_loop = False
        say("assistant", f"✅ Auto-trader finished — placed {ss.auto_count} trade(s).")
    else:
        try:
            _open = broker.is_market_open()
        except BrokerError:
            _open = None
        if _open is False:
            if not ss.get("auto_closed_said"):
                say("assistant", "🔁 Market is closed — auto-trader is waiting for "
                                 "the open (keep this page on).")
                ss.auto_closed_said = True
        else:
            ss.auto_closed_said = False
            auto_place_one()

# --- 📊 Backtest: proof on real history (does the engine actually work?) ---
with st.expander("📊 Does it actually work? (backtest on real history)"):
    st.caption("Replays months of real prices and simulates every trade the "
               "engine WOULD have taken — win rate, average R (profit measured "
               "in units of risk), and profit factor. It uses your current risk "
               "settings. Past results never guarantee the future.")
    if st.button("▶️ Run backtest on the watchlist", use_container_width=True):
        with st.spinner("Replaying history across the watchlist…"):
            try:
                from backtest import backtest
                book = broker.fetch_bars(UNIVERSE)
                res = backtest(book, risk_pct=ss.risk_pct,
                               max_order_dollars=ss.max_order)
                lines = [res.summary()]
                by_sym = {}
                for tr in res.trades:
                    by_sym.setdefault(tr.symbol, []).append(tr)
                tops = sorted(by_sym.items(),
                              key=lambda kv: sum(t.r_multiple for t in kv[1]),
                              reverse=True)[:6]
                for s, ts in tops:
                    wr = sum(1 for t in ts if t.r_multiple > 0) / len(ts) * 100
                    avg = sum(t.r_multiple for t in ts) / len(ts)
                    lines.append(f"- **{s}** · {len(ts)} trades · {wr:.0f}% win · "
                                 f"{avg:+.2f}R avg")
                ss.backtest = "\n".join(lines)
            except Exception as exc:  # noqa: BLE001
                ss.backtest = f"Couldn't run the backtest: {exc}"
    if ss.get("backtest"):
        st.markdown(ss.backtest)
        st.caption("R = reward-to-risk. +1R means it made exactly what it risked. "
                   "Profit factor > 1 means winners outweighed losers.")

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
