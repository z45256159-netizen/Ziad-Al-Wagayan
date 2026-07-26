# Alpaca CLI Trading Bot

An interactive command-line trading bot for US stocks, built on the
[Alpaca](https://alpaca.markets/) API using the official `alpaca-py` SDK.

You type `scan` (or **find** in the web chat), it finds one candidate using a
documented **multi-indicator momentum** strategy (moving-average crossover +
RSI + MACD + volume), then hands you a full **trade plan** — how many shares, a
volatility-based **stop-loss** and a **take-profit** at a 1:2 reward:risk, the
dollar cost, the dollars at risk, the potential profit, why it picked the stock,
and which strategy — and asks you to confirm. On **Yes** it places a **bracket
order** so the stop-loss and take-profit are attached automatically.

**It defaults to paper trading (fake money). You have to go out of your way to
enable live trading.**

---

## Features

- Interactive REPL: `scan`, `positions`, `balance`, `help`, `quit`.
- Human-in-the-loop: every order requires an explicit `yes` before it's sent.
- Swappable strategy in its own module (`strategy.py`).
- Position sizing as a configurable % of buying power, with a hard per-order
  dollar cap.
- Skips tickers you already hold.
- Paper trading by default; loud warning when live mode is on.
- Graceful handling of market-closed, no-candidates, and API errors.

---

## Project structure

```
.
├── streamlit_app.py  # ★ The phone-friendly chat website (recommended)
├── engine.py         # ★ Professional trade core: regime, structure stops, R:R gate
├── bot.py            # Interactive loop / entry point (the CLI)
├── strategy.py       # ★ Candidate scanner (SMA crossover + RSI + MACD + volume)
├── analysis.py       # Long-term investing read + news sentiment + explanations
├── broker.py         # All Alpaca API calls (data + trading) live here
├── sizing.py         # Position sizing & risk rules (the trade-plan dataclass)
├── backtest.py       # Walk-forward backtester over historical bars
├── chart.py          # Candlestick chart (Plotly) + chart image for AI vision
├── ai.py             # Optional free Groq AI (text pick + chart vision)
├── universe.py       # ★ The editable list of tickers to scan
├── config.py         # Loads/validates settings from .env
├── test_strategy.py  # Unit tests for the engine, scoring & sizing (no API needed)
├── requirements.txt
├── .env.example      # Copy to .env and fill in your keys
└── .gitignore        # Excludes your real .env
```

Files marked ★ are the ones you'll most likely want to edit.

---

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Get your Alpaca keys (paper first!)

1. Sign up at [alpaca.markets](https://alpaca.markets/) (free).
2. In the dashboard, switch to **Paper Trading**.
3. Generate an API key + secret from the paper account.

### 3. Configure

```bash
cp .env.example .env
```

Open `.env` and paste your paper keys into `ALPACA_API_KEY` and
`ALPACA_API_SECRET`. Leave `LIVE=false`.

Your `.env` is git-ignored, so your keys won't be committed.

---

## 🌐 The website (phone-friendly) — recommended

The web version (`streamlit_app.py`) is a polished, phone-friendly app that
**anyone can sign in to with their own Alpaca keys** — deploy it once and share
the link. On the sign-in screen each visitor enters *their own* keys (stored
only in their own browser via **Remember me**), accepts the **Terms of Service /
"not financial advice"** disclaimer, and lands in the app. Nobody ever sees or
uses anyone else's account. Then they tap the big **🔎 Find me a trade** button:
it finds one trade, explains it, and they tap **Yes** or **No** to place it.

> **Multi-user by design.** The app never reads keys from the server's Secrets,
> so it can't leak the operator's account to visitors — every user brings their
> own keys, kept in their own browser. If you host it for others, set only the
> non-personal defaults (order caps, etc.) in Secrets, never `ALPACA_API_KEY`.

> Why does it need hosting instead of being a plain link? Because it talks to
> your real brokerage account. Your secret key must live on a server (never in a
> public page), and the server is what reaches Alpaca. Hosting is free.

### Deploy free on Streamlit Community Cloud (~3 minutes)

1. Push this repo to your own GitHub (this branch already is).
2. Go to **[share.streamlit.io](https://share.streamlit.io)** and sign in with
   GitHub.
3. Click **Create app** → pick this repo, your branch, and
   `streamlit_app.py` as the main file.
4. Click **Deploy**. You'll get a URL like `https://your-app.streamlit.app` —
   bookmark it on your phone.
5. Open the link and **paste your keys in the app** — just the Alpaca key +
   secret. The app checks them, then you're in the chat. Nothing else to set up.

### Never type your keys again — "Remember me"

Tick **Remember me** when you sign in. Your keys are saved **in your own
browser** (localStorage) and the app signs you in automatically next time — no
Secrets, no "Manage app," nothing to configure. This works the same for every
visitor, each with their own keys. Tap **Disconnect / change keys** to forget
them on that device.

> Don't put `ALPACA_API_KEY` / `ALPACA_API_SECRET` in the deployment Secrets for
> a shared app — the app intentionally ignores them so it can never sign a
> visitor into someone else's account. Secrets are only for non-personal
> defaults like `MAX_ORDER_DOLLARS`.

### 🧠 The engine is the brain (no AI key, no third-party sign-up)

The **momentum + volume scanner** shortlists movers, then `engine.py` — the
professional core — decides whether any of them is actually worth trading. It's
completely self-contained: no OpenAI, no Groq, no extra API keys to chase down.

**What the engine does (Technical mode):**

- **Market-regime detection** — reads trend strength (ADX), volatility (ATR%),
  and bias (moving-average structure) so the logic adapts instead of using one
  fixed rule.
- **Realistic stop-loss** — placed at real market structure (the recent swing
  low for a long, swing high for a short) plus an ATR buffer, clamped to sane
  bounds. A **BUY stop is never above entry**; a **SELL stop is never below
  entry** — this is validated before anything is ever recommended.
- **Structure-based take-profit** — aimed at the nearest real swing level in the
  trade's direction, not a made-up number.
- **Hard reward:risk gate** — if the best honest target isn't far enough away to
  clear your **minimum reward:risk** (slider in Settings), the trade is
  **rejected, not forced**. When nothing on the watchlist qualifies, the app
  says *"No trade right now — quality over quantity"* rather than inventing one.
- **Confidence score (0–100)** with plain-English reasons for *why*, blending
  trend alignment, R:R, volume, momentum (RSI), volatility, and news sentiment.

**Best-performers (investing) mode** is a different read entirely: long-term
trend structure, 3-month and 1-year performance, **relative strength vs the S&P
500 (SPY)**, drawdown, and news sentiment → a STRONG BUY / BUY / HOLD / AVOID
verdict with a quality score and reasons. (These are price-based estimates for
learning, not a substitute for company fundamentals.)

**Backtesting:** `python backtest.py` walks historical bars and simulates the
engine's trades, reporting win rate, average R multiple, expectancy, and profit
factor — an honest what-if on daily data, not a promise of future results.

### 📊 "Does it actually work?" — the built-in backtest

Open the **"Does it actually work? (backtest on real history)"** panel and tap
**Run backtest**. The app replays months of real prices across the watchlist and
simulates every trade the engine would have taken, then shows the **win rate,
average R, and profit factor** using your current risk settings. It's an honest
what-if on daily data — not a promise — but it lets you see the edge before you
trust it with real money.

### 🛡️ Safety, exits & your track record

Three tools to keep you in control (all in the app — no config files):

- **Safety guardrails** (⚙️ Settings → Safety) — hard limits the bot cannot
  cross: a **daily loss limit** (stop opening trades once you're down $X today),
  **max open positions**, and **max % of your portfolio in one trade**. They
  apply to both manual and hands-free trades.
- **Protect winners** (⚙️ Settings → Protect winners) — once a trade is up
  enough, the bot moves its stop-loss to **breakeven** and then **trails** it
  behind the price, so a winner can't turn back into a loss. It runs while the
  page is open, or on demand with **🛡️ Protect my winners now**.
- **"How am I doing?"** panel — your **real** results: today's account P/L,
  portfolio value, and every open position with live profit/loss and a one-tap
  **Close** button.

### 🔎 When nothing qualifies

The engine won't invent a bad trade. When you tap **Find** manually and nothing
clears your minimum reward:risk, it shows the **best-available** setup clearly
flagged *"below your quality bar"* so you always have something to look at (you
decide whether to take it). The **auto-trader stays strict** — it places only
trades that fully pass the gate, and otherwise places nothing.

> No third-party AI keys are required or used by the web app anymore — the
> engine is fully self-contained. (`ai.py` remains in the repo for anyone who
> wants to wire an optional model back in, but it's off by default.)

To go live later, change `LIVE` to `"true"` in the Secrets box (the app shows a
loud red warning in live mode).

### Run the website locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then edit in your keys
streamlit run streamlit_app.py
```

---

## Running the CLI (paper mode)

```bash
python bot.py
```

You'll see a `[PAPER MODE]` banner and a prompt:

```
[PAPER] > scan
  Scanning 30 tickers...
  ==========================================================
  CANDIDATE: NVDA
  ----------------------------------------------------------
  Current price : $120.34
  Signal        : Price $120.34 is 4.2% above its 20-day SMA ($115.50);
                  today's volume is 1.73x the 20-day average.
  Score         : 0.0726
  ----------------------------------------------------------
  Proposed order: BUY 8 share(s) of NVDA
  Estimated cost: $962.72
  Sizing         : 5.0% of $100,000.00 buying power = $5,000.00 target,
                   capped at $1,000.00 max order -> budget $1,000.00. ...
  ==========================================================
  Place this order? (yes/no): yes
  ✅ Order submitted!
     Order ID : 61e...
     Status   : accepted
```

### Commands

| Command     | What it does                                              |
|-------------|-----------------------------------------------------------|
| `scan`      | Find one candidate, show the signal + sizing, confirm.    |
| `positions` | List your open positions with P/L.                        |
| `balance`   | Show buying power, cash, and portfolio value.             |
| `help`      | Show the command list.                                    |
| `quit`      | Exit.                                                     |

> **Tip:** The stock market is open 9:30am–4:00pm ET on weekdays. Outside those
> hours `scan` still works, but any order you place queues until the next open.

---

## Configuration reference (`.env`)

| Variable            | Default  | Meaning                                             |
|---------------------|----------|-----------------------------------------------------|
| `ALPACA_API_KEY`    | —        | Your Alpaca API key ID.                             |
| `ALPACA_API_SECRET` | —        | Your Alpaca API secret.                             |
| `LIVE`              | `false`  | `false` = paper, `true` = **real money**.           |
| `POSITION_SIZE_PCT` | `0.05`   | Fraction of buying power per position (0.05 = 5%).  |
| `MAX_ORDER_DOLLARS` | `1000`   | Hard ceiling on any single order.                   |
| `LOOKBACK_DAYS`     | `20`     | Days used for the moving average / volume baseline. |

---

## Changing the strategy

The scanning logic is isolated in **`strategy.py`**. There are two ways to
change it:

**A. Tune the default.** Edit `score_symbol()`. It receives a list of `Bar`
objects (close + volume) for one ticker and returns a `SymbolScore` if the
ticker qualifies, or `None` if it doesn't. Change the filters (e.g. require the
price to be 2% above the SMA), change the score formula, or change what counts
as "high volume."

**B. Write a new strategy.** Implement your own function with the same
signature:

```python
def my_strategy(symbol: str, bars: list[Bar]) -> Optional[SymbolScore]:
    ...
```

then point the module-level `SCORING_FN` at it:

```python
SCORING_FN = my_strategy
```

`find_candidate()` uses whatever `SCORING_FN` references, so nothing else needs
to change.

### Changing the universe

Edit the `UNIVERSE` list in **`universe.py`** — add or remove tickers freely.
Keep them liquid (large/mid caps) so fills are clean and the volume signal is
meaningful.

---

## Going from paper to live (⚠ real money)

Only do this once you've tested thoroughly in paper mode and understand the
strategy's behavior.

1. Fund a **live** Alpaca account and generate **live** API keys.
2. In `.env`, replace the keys with your live keys and set:
   ```
   LIVE=true
   ```
3. Consider lowering `POSITION_SIZE_PCT` and `MAX_ORDER_DOLLARS` for your first
   live runs.
4. Run `python bot.py`. You'll see a loud `LIVE TRADING IS ON` warning. Every
   order still requires your explicit `yes`.

To go back to paper, set `LIVE=false`.

---

## Running the tests

The scoring and sizing logic is pure and can be tested without any API keys or
network access:

```bash
python -m unittest test_strategy -v
```

---

## Disclaimer

This is educational software, not financial advice. Trading involves risk of
loss. The default strategy is intentionally simple. Test in paper mode, and
never trade money you can't afford to lose.
