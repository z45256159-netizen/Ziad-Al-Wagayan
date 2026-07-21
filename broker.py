"""
Alpaca broker wrapper.

This module is the ONLY place that talks to the Alpaca SDK. It handles:
  * authenticating the trading + market-data clients,
  * fetching recent daily bars for the universe,
  * reading account info and positions,
  * submitting market orders.

Everything is wrapped so callers get plain Python types and friendly errors
instead of raw SDK exceptions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import (
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from config import Config
from strategy import Bar


class BrokerError(Exception):
    """Friendly, caller-facing error for anything that goes wrong with Alpaca."""


class Broker:
    def __init__(self, config: Config) -> None:
        self.config = config
        # `paper=True` routes trading to paper-api.alpaca.markets. This is the
        # single most important safety switch in the app.
        self.trading = TradingClient(
            api_key=config.api_key,
            secret_key=config.api_secret,
            paper=not config.live,
        )
        # Historical market data. The free IEX feed works for paper accounts;
        # switch to DataFeed.SIP if your subscription allows it.
        self.data = StockHistoricalDataClient(
            api_key=config.api_key,
            secret_key=config.api_secret,
        )

    # ------------------------------------------------------------------ account
    def get_account(self):
        """Return the raw Alpaca account object (buying_power, cash, etc.)."""
        try:
            return self.trading.get_account()
        except Exception as exc:  # noqa: BLE001 - present a clean message
            raise BrokerError(f"Could not fetch account: {exc}") from exc

    def get_buying_power(self) -> float:
        return float(self.get_account().buying_power)

    def is_market_open(self) -> bool:
        try:
            return bool(self.trading.get_clock().is_open)
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"Could not fetch market clock: {exc}") from exc

    def get_positions(self) -> list:
        """Return the list of open positions (raw Alpaca Position objects)."""
        try:
            return self.trading.get_all_positions()
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"Could not fetch positions: {exc}") from exc

    def held_symbols(self) -> set[str]:
        """Set of ticker symbols we currently hold — used to skip duplicates."""
        return {p.symbol for p in self.get_positions()}

    # --------------------------------------------------------------- market data
    def fetch_bars(self, symbols: List[str]) -> Dict[str, List[Bar]]:
        """
        Fetch recent DAILY bars for each symbol and convert them into the plain
        `Bar` objects the strategy understands.

        We request a generous calendar window (lookback_days plus padding for
        weekends/holidays) and then keep the most recent `lookback_days` bars.
        """
        lookback = self.config.lookback_days
        # ~1.6 calendar days per trading day is plenty of padding, + a week buffer.
        calendar_days = int(lookback * 1.6) + 7
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=calendar_days)

        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )

        try:
            barset = self.data.get_stock_bars(request)
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"Could not fetch market data: {exc}") from exc

        # barset.data is a dict: symbol -> list of Bar SDK objects.
        result: Dict[str, List[Bar]] = {}
        raw = getattr(barset, "data", {}) or {}
        for symbol, sdk_bars in raw.items():
            if not sdk_bars:
                continue
            # Keep only the most recent `lookback` bars.
            recent = sdk_bars[-lookback:]
            result[symbol] = [
                Bar(
                    close=float(b.close),
                    volume=float(b.volume),
                    high=float(b.high),
                    low=float(b.low),
                    open=float(b.open),
                )
                for b in recent
            ]
        return result

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Best-effort latest price from the most recent daily bar."""
        bars = self.fetch_bars([symbol]).get(symbol)
        if not bars:
            return None
        return bars[-1].close

    # -------------------------------------------------------------------- orders
    def submit_market_order(
        self, symbol: str, qty: float, side: OrderSide
    ) -> object:
        """Submit a market order and return the raw Alpaca Order object."""
        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        try:
            return self.trading.submit_order(order_data=order_data)
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"Order rejected by Alpaca: {exc}") from exc

    def submit_bracket_order(
        self, symbol: str, qty: int, take_profit: float, stop_loss: float
    ) -> object:
        """
        Submit a BRACKET order: a market BUY entry that automatically attaches a
        take-profit limit and a stop-loss. Once the entry fills, Alpaca manages
        both exits for you (whichever hits first cancels the other).
        """
        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
        )
        try:
            return self.trading.submit_order(order_data=order_data)
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"Order rejected by Alpaca: {exc}") from exc
