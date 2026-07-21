#!/usr/bin/env python3
"""
Interactive command-line trading bot built on the Alpaca API.

Run it:
    python bot.py

Commands (type at the prompt):
    scan       Scan the universe for one candidate trade, then confirm to place it.
    positions  Show your open positions.
    balance    Show buying power and account value.
    help       Show the command list.
    quit       Exit.

SAFETY: Defaults to PAPER trading. Live trading requires LIVE=true in .env and
prints a loud warning on startup.
"""

from __future__ import annotations

import sys

from alpaca.trading.enums import OrderSide

from broker import Broker, BrokerError
from config import CONFIG, ConfigError
from sizing import size_position
from strategy import find_candidate
from universe import UNIVERSE


# --------------------------------------------------------------------------- UI
BANNER = r"""
  ___  _                       ___      _
 / _ \| |                     | _ ) ___| |_
| |_| | | Alpaca              | _ \/ _ \  _|
 \___/|_| momentum + volume   |___/\___/\__|
"""


def print_mode_banner() -> None:
    print(BANNER)
    if CONFIG.live:
        # LOUD warning for live mode.
        print("!" * 64)
        print("!!  ⚠  LIVE TRADING IS ON — ORDERS USE REAL MONEY  ⚠")
        print(f"!!  Endpoint: {CONFIG.trading_url}")
        print("!!  Set LIVE=false in your .env to return to paper trading.")
        print("!" * 64)
    else:
        print(f"[PAPER MODE] Safe sandbox — no real money. ({CONFIG.trading_url})")
    print()


HELP_TEXT = """
Commands:
  scan       Find one candidate trade and confirm before placing it.
  positions  Show open positions.
  balance    Show buying power and account value.
  help       Show this help.
  quit       Exit the bot.
"""


def prompt(msg: str) -> str:
    """input() that treats EOF / Ctrl-D as 'quit'."""
    try:
        return input(msg)
    except EOFError:
        print()
        return "quit"


# ---------------------------------------------------------------------- actions
def cmd_balance(broker: Broker) -> None:
    try:
        acct = broker.get_account()
    except BrokerError as exc:
        print(f"  Error: {exc}")
        return
    print(f"  Account status : {acct.status}")
    print(f"  Buying power   : ${float(acct.buying_power):,.2f}")
    print(f"  Cash           : ${float(acct.cash):,.2f}")
    print(f"  Portfolio value: ${float(acct.portfolio_value):,.2f}")


def cmd_positions(broker: Broker) -> None:
    try:
        positions = broker.get_positions()
    except BrokerError as exc:
        print(f"  Error: {exc}")
        return
    if not positions:
        print("  No open positions.")
        return
    print(f"  {'SYM':<6}{'QTY':>8}{'AVG':>12}{'PRICE':>12}{'MKT VAL':>14}{'P/L':>14}")
    for p in positions:
        print(
            f"  {p.symbol:<6}"
            f"{float(p.qty):>8.2f}"
            f"{float(p.avg_entry_price):>12.2f}"
            f"{float(p.current_price):>12.2f}"
            f"{float(p.market_value):>14.2f}"
            f"{float(p.unrealized_pl):>14.2f}"
        )


def cmd_scan(broker: Broker) -> None:
    """Scan -> present candidate -> confirm -> place order."""
    # 1. Warn (don't block) if the market is closed. A market order placed while
    #    closed will queue until the next open.
    try:
        if not broker.is_market_open():
            print("  Note: the market is currently CLOSED. Any order you place "
                  "will queue until the next session.")
    except BrokerError as exc:
        print(f"  Warning: could not check market hours ({exc}).")

    # 2. Pull data for the whole universe.
    print(f"  Scanning {len(UNIVERSE)} tickers...")
    try:
        bars_by_symbol = broker.fetch_bars(UNIVERSE)
    except BrokerError as exc:
        print(f"  Error: {exc}")
        return

    if not bars_by_symbol:
        print("  No market data returned. Try again later.")
        return

    # 3. Run the (swappable) strategy.
    candidate = find_candidate(bars_by_symbol)
    if candidate is None:
        print("  No candidates passed the strategy filters right now. "
              "Nothing to trade.")
        return

    # 4. Skip if we already hold it.
    try:
        held = broker.held_symbols()
    except BrokerError as exc:
        print(f"  Error checking existing positions: {exc}")
        return
    if candidate.symbol in held:
        print(f"  Top candidate is {candidate.symbol}, but you already hold it. "
              "Skipping to avoid stacking a position.")
        return

    # 5. Size the position.
    try:
        buying_power = broker.get_buying_power()
    except BrokerError as exc:
        print(f"  Error: {exc}")
        return

    sizing = size_position(
        price=candidate.last_price,
        buying_power=buying_power,
        position_size_pct=CONFIG.position_size_pct,
        max_order_dollars=CONFIG.max_order_dollars,
    )

    # 6. Present the candidate clearly.
    print()
    print("  " + "=" * 58)
    print(f"  CANDIDATE: {candidate.symbol}")
    print("  " + "-" * 58)
    print(f"  Current price : ${candidate.last_price:,.2f}")
    print(f"  Signal        : {candidate.reason}")
    print(f"  Score         : {candidate.score:.4f}")
    print("  " + "-" * 58)

    if not sizing.ok:
        print(f"  Proposed order: NONE — {sizing.skipped_reason}")
        print("  " + "=" * 58)
        return

    print(f"  Proposed order: BUY {sizing.qty} share(s) of {candidate.symbol}")
    print(f"  Estimated cost: ${sizing.estimated_cost:,.2f}")
    print(f"  Sizing         : {sizing.explanation}")
    print("  " + "=" * 58)

    # 7. Confirm.
    answer = prompt("  Place this order? (yes/no): ").strip().lower()
    if answer not in ("yes", "y"):
        print("  Cancelled. Back to the main loop.")
        return

    # 8. Submit.
    try:
        order = broker.submit_market_order(
            symbol=candidate.symbol,
            qty=sizing.qty,
            side=OrderSide.BUY,
        )
    except BrokerError as exc:
        print(f"  Order failed: {exc}")
        return

    print(f"  ✅ Order submitted!")
    print(f"     Order ID : {order.id}")
    print(f"     Symbol   : {order.symbol}")
    print(f"     Side     : {order.side.value if hasattr(order.side, 'value') else order.side}")
    print(f"     Qty      : {order.qty}")
    print(f"     Status   : {order.status.value if hasattr(order.status, 'value') else order.status}")


# ------------------------------------------------------------------------- loop
def main() -> int:
    print_mode_banner()

    try:
        broker = Broker(CONFIG)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to connect to Alpaca: {exc}")
        return 1

    # Verify credentials early with a friendly message.
    try:
        acct = broker.get_account()
        print(f"Connected. Account {acct.account_number} — "
              f"buying power ${float(acct.buying_power):,.2f}.")
    except BrokerError as exc:
        print(f"Could not connect to your Alpaca account: {exc}")
        print("Check your keys in .env and whether LIVE matches the key type.")
        return 1

    print(HELP_TEXT)

    while True:
        raw = prompt(f"[{CONFIG.mode_name}] > ").strip().lower()
        if raw == "":
            continue
        elif raw in ("quit", "exit", "q"):
            print("Goodbye.")
            return 0
        elif raw == "scan":
            cmd_scan(broker)
        elif raw == "positions":
            cmd_positions(broker)
        elif raw == "balance":
            cmd_balance(broker)
        elif raw in ("help", "?"):
            print(HELP_TEXT)
        else:
            print(f"  Unknown command: {raw!r}. Type 'help' for options.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye.")
        sys.exit(0)
