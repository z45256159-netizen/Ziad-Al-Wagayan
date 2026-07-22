"""
The trading universe: the set of tickers the scanner considers.

Keep this list SMALL and LIQUID. These are large/mid-cap US stocks that trade
heavy volume, which makes fills clean and the momentum/volume signal meaningful.

Edit this list freely — add or remove tickers as you like. The scanner will
automatically pick up whatever is here.
"""

# Plain-English "what this company actually does" — so you know what you're
# buying, not just a ticker. Add a line here when you add a ticker.
DESCRIPTIONS = {
    "AAPL": "Apple — makes the iPhone, Mac, iPad, and services (App Store, iCloud).",
    "MSFT": "Microsoft — Windows, Office, Azure cloud, and Xbox.",
    "GOOGL": "Google (Alphabet) — search, YouTube, ads, and Google Cloud.",
    "AMZN": "Amazon — online shopping and AWS cloud computing.",
    "META": "Meta — Facebook, Instagram, WhatsApp, and ads.",
    "NVDA": "Nvidia — chips (GPUs) that power gaming and AI.",
    "TSLA": "Tesla — electric cars, batteries, and solar.",
    "AVGO": "Broadcom — semiconductors and networking/software.",
    "AMD": "AMD — computer and server processors and graphics chips.",
    "INTC": "Intel — computer processors and chip manufacturing.",
    "QCOM": "Qualcomm — mobile phone chips and wireless tech.",
    "MU": "Micron — memory and storage chips.",
    "JPM": "JPMorgan Chase — the largest US bank.",
    "BAC": "Bank of America — a big US consumer and commercial bank.",
    "GS": "Goldman Sachs — investment banking and trading.",
    "V": "Visa — runs the card payment network.",
    "MA": "Mastercard — runs the card payment network.",
    "JNJ": "Johnson & Johnson — pharmaceuticals and medical devices.",
    "UNH": "UnitedHealth — health insurance and healthcare services.",
    "PFE": "Pfizer — a big pharmaceutical (drugs and vaccines) company.",
    "LLY": "Eli Lilly — drugmaker (diabetes and weight-loss drugs).",
    "WMT": "Walmart — the largest retailer; groceries and general goods.",
    "COST": "Costco — membership warehouse stores (bulk groceries & goods).",
    "HD": "Home Depot — home improvement and hardware stores.",
    "MCD": "McDonald's — the global fast-food burger chain.",
    "NKE": "Nike — athletic shoes and apparel.",
    "SBUX": "Starbucks — the global coffee chain.",
    "CAT": "Caterpillar — construction and mining machinery.",
    "BA": "Boeing — makes commercial jets and defense aircraft.",
    "XOM": "ExxonMobil — oil and gas (energy).",
    "CVX": "Chevron — oil and gas (energy).",
}


def describe(symbol: str) -> str:
    """One-line 'what it does' for a ticker (or a safe default)."""
    return DESCRIPTIONS.get(symbol.upper(),
                            f"{symbol.upper()} — a publicly traded US company.")


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
