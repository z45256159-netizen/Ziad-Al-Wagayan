"""
The trading universe: the set of tickers the scanner considers.

Keep this list SMALL and LIQUID. These are large/mid-cap US stocks that trade
heavy volume, which makes fills clean and the momentum/volume signal meaningful.

Edit this list freely — add or remove tickers as you like. The scanner will
automatically pick up whatever is here.
"""

# ~30 large/mid-cap, highly-liquid S&P 500 names across sectors.
UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO",
    # Semis / hardware
    "AMD", "INTC", "QCOM", "MU",
    # Financials
    "JPM", "BAC", "GS", "V", "MA",
    # Healthcare
    "JNJ", "UNH", "PFE", "LLY",
    # Consumer
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX",
    # Industrials / energy
    "CAT", "BA", "XOM", "CVX",
]
