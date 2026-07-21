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
├── bot.py            # Interactive loop / entry point (the CLI)
├── strategy.py       # ★ Swappable strategy (SMA crossover + RSI + MACD + volume)
├── broker.py         # All Alpaca API calls (data + trading) live here
├── sizing.py         # Position sizing & risk rules
├── universe.py       # ★ The editable list of tickers to scan
├── config.py         # Loads/validates settings from .env
├── test_strategy.py  # Unit tests for scoring + sizing (no API needed)
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

The web version (`streamlit_app.py`) is a **chat** that works great on a phone.
You **paste your keys right in the app** (it checks they work), then just type
**find** — it finds one trade, explains it, and you tap **Yes** or **No** to
place it. Deploy it free once and you get a permanent URL you open in your
browser; after that there's no more setup — open the link, paste keys, chat.

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
5. Open the link and **paste your keys in the app** — Alpaca key + secret, and
   (optionally) your free Groq key. The app checks them, then you chat.

> You do **not** need to put keys in Streamlit's Secrets box — the app asks for
> them on screen. (If you'd rather pre-fill them so you don't retype each visit,
> you *can* add `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `LIVE`, and `GROQ_API_KEY`
> under **Advanced settings → Secrets** — they'll auto-fill the form.)

### 🧠 The bot already has a brain (no AI needed)

The **momentum + volume scanner is the decision-maker** — it picks the trade,
sizes it, and skips tickers you already hold, entirely on its own. You can trade
with it and never turn on any AI. The AI below is an optional *second opinion*.

### 🤖 Optional: let a FREE AI pick the trade

If you enter a **Groq** key when connecting, the AI reviews the qualifying
candidates' real numbers, chooses one, and shows a plain-English
GO / CAUTION / NO-GO rationale in the chat. Leave it blank and the rule-based
scanner decides on its own — the app works fully either way.

> **It's free, and it's Groq (not OpenAI).** Get a free API key (no credit card)
> from **[console.groq.com](https://console.groq.com)** → API Keys. Groq runs
> fast open models (Llama 3.3 70B) at no cost. The AI only ever sees the
> price/volume data the app already fetched; it never invents numbers, and if
> the AI call fails the app falls back to the rule-based pick.

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
