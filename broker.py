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

try:
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest
    _NEWS_AVAILABLE = True
except Exception:  # pragma: no cover - older SDKs
    _NEWS_AVAILABLE = False
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
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
        self.news = None
        if _NEWS_AVAILABLE:
            try:
                self.news = NewsClient(api_key=config.api_key,
                                       secret_key=config.api_secret)
            except Exception:  # pragma: no cover
                self.news = None

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

    def today_pl(self) -> tuple[float, float]:
        """Today's profit/loss in (dollars, percent), from equity vs last_equity.
        Reliable and account-level — what your account is up/down since yesterday."""
        a = self.get_account()
        equity = float(getattr(a, "equity", 0) or 0)
        last = float(getattr(a, "last_equity", 0) or 0)
        dollars = equity - last
        pct = (dollars / last * 100) if last else 0.0
        return round(dollars, 2), round(pct, 2)

    def close_position(self, symbol: str):
        """Flatten a single position at market (sells a long / covers a short)."""
        try:
            return self.trading.close_position(symbol)
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"Could not close {symbol}: {exc}") from exc

    # ------------------------------------------------------------- exit management
    def get_open_orders(self) -> list:
        """All currently-open orders (used to find & move protective stops).
        Best-effort — returns [] if the SDK call isn't available."""
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            return list(self.trading.get_orders(filter=req) or [])
        except Exception:  # noqa: BLE001 - never break the app over this
            try:
                return list(self.trading.get_orders() or [])
            except Exception:
                return []

    def find_stop_order(self, symbol: str, position_side: str):
        """Find the open protective STOP order for a held position, or None.
        For a long position the stop is a SELL; for a short it's a BUY."""
        want_side = "sell" if position_side == "long" else "buy"
        for o in self.get_open_orders():
            if getattr(o, "symbol", None) != symbol:
                continue
            otype = str(getattr(o, "order_type", "") or getattr(o, "type", "")).lower()
            oside = str(getattr(o, "side", "")).lower()
            if "stop" in otype and want_side in oside:
                return o
        return None

    def replace_stop(self, order_id: str, new_stop: float):
        """Move an existing stop order to a new stop price (trail / breakeven)."""
        try:
            from alpaca.trading.requests import ReplaceOrderRequest
            return self.trading.replace_order_by_id(
                order_id, ReplaceOrderRequest(stop_price=round(new_stop, 2)))
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"Could not move stop: {exc}") from exc

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

    def get_news(self, symbol: str, limit: int = 4):
        """Recent news headlines for a symbol: list of (headline, source).
        Best-effort — returns [] if news isn't available."""
        if self.news is None:
            return []
        try:
            result = self.news.get_news(NewsRequest(symbols=symbol, limit=limit))
        except Exception:
            return []
        # Extract the list of news items across possible SDK response shapes.
        items = None
        for attr in ("news",):
            items = getattr(result, attr, None)
            if items:
                break
        if items is None:
            data = getattr(result, "data", None)
            if isinstance(data, dict):
                items = data.get("news") or []
        out = []
        for it in (items or [])[:limit]:
            headline = getattr(it, "headline", None) or (
                it.get("headline") if isinstance(it, dict) else None)
            source = getattr(it, "source", None) or (
                it.get("source") if isinstance(it, dict) else "") or ""
            if headline:
                out.append((headline, source))
        return out

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
        self, symbol: str, qty: int, take_profit: float, stop_loss: float,
        side: str = "buy", limit_price: Optional[float] = None,
    ) -> object:
        """
        Submit a BRACKET order that attaches a take-profit and a stop-loss.
        If `limit_price` is given, the entry is a LIMIT order (fills only at that
        price or better — e.g. "buy on a dip to $190"); otherwise a MARKET entry
        (fills now). Once the entry fills, Alpaca manages both exits.
        """
        order_side = OrderSide.SELL if side == "sell" else OrderSide.BUY
        common = dict(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.GTC if limit_price else TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
        )
        if limit_price:
            order_data = LimitOrderRequest(limit_price=round(limit_price, 2), **common)
        else:
            order_data = MarketOrderRequest(**common)
        try:
            return self.trading.submit_order(order_data=order_data)
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"Order rejected by Alpaca: {exc}") from exc
