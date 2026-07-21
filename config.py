"""
Configuration loader for the Alpaca trading bot.

All runtime settings come from environment variables (loaded from a `.env`
file via python-dotenv). This keeps secrets out of the code and makes it easy
to flip between paper and live trading without editing source.

Import `CONFIG` (a singleton) anywhere you need settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load variables from a local .env file into the process environment.
# Values already present in the real environment take precedence.
load_dotenv()

# Alpaca API endpoints. The SDK's TradingClient uses the `paper` flag rather
# than a raw URL, but we keep the URLs here for clarity and for the data feed.
PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
LIVE_TRADING_URL = "https://api.alpaca.markets"


def _get_bool(name: str, default: bool) -> bool:
    """Parse a truthy/falsy environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got: {raw!r}")


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got: {raw!r}")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class Config:
    """Typed, validated view of the bot's configuration."""

    api_key: str
    api_secret: str
    live: bool
    position_size_pct: float
    max_order_dollars: float
    lookback_days: int

    # Derived / convenience fields.
    trading_url: str = field(init=False)

    def __post_init__(self) -> None:
        self.trading_url = LIVE_TRADING_URL if self.live else PAPER_TRADING_URL

        # Basic sanity checks so we fail loudly at startup rather than mid-trade.
        if not self.api_key or not self.api_secret:
            raise ConfigError(
                "ALPACA_API_KEY and ALPACA_API_SECRET must be set. "
                "Copy .env.example to .env and fill in your keys."
            )
        if not (0 < self.position_size_pct <= 1):
            raise ConfigError(
                f"POSITION_SIZE_PCT must be between 0 and 1 "
                f"(got {self.position_size_pct})."
            )
        if self.max_order_dollars <= 0:
            raise ConfigError("MAX_ORDER_DOLLARS must be positive.")
        if self.lookback_days < 2:
            raise ConfigError("LOOKBACK_DAYS must be at least 2.")

    @property
    def mode_name(self) -> str:
        return "LIVE" if self.live else "PAPER"


def load_config() -> Config:
    """Build a Config from the current environment."""
    return Config(
        api_key=os.getenv("ALPACA_API_KEY", "").strip(),
        api_secret=os.getenv("ALPACA_API_SECRET", "").strip(),
        live=_get_bool("LIVE", default=False),
        position_size_pct=_get_float("POSITION_SIZE_PCT", default=0.05),
        max_order_dollars=_get_float("MAX_ORDER_DOLLARS", default=1000.0),
        # Enough daily bars for the 50-day SMA + MACD/RSI warmup.
        lookback_days=_get_int("LOOKBACK_DAYS", default=60),
    )


# Note: we intentionally do NOT build a singleton at import time. The CLI
# (bot.py) calls load_config() at startup, and the web app (streamlit_app.py)
# builds a Config from its own secrets store. This keeps `import config` free of
# side effects so either front-end can use it.
