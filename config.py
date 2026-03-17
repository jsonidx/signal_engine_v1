"""
Signal Engine Configuration
===========================
Edit this file to customize your screening universe, factor weights,
risk parameters, and output preferences.

IMPORTANT: This is YOUR config. Tune it to your conviction levels and
risk tolerance. The defaults are conservative — deliberately so.
"""

from datetime import datetime

# ============================================================
# PORTFOLIO PARAMETERS
# ============================================================
PORTFOLIO_NAV = 50_000          # Total portfolio value in EUR
EQUITY_ALLOCATION = 0.65        # 65% to equities
CRYPTO_ALLOCATION = 0.25        # 25% to crypto
CASH_BUFFER = 0.10              # 10% cash reserve (dry powder)

# ============================================================
# EQUITY UNIVERSE
# ============================================================
# Core universe — S&P 500 and STOXX 600 proxies via sector ETFs
# We pull constituents' top holdings. For a proper screener you'd
# use a full constituent list — see note below.
#
# In practice: provide your own ticker list or use the SP500/STOXX
# scraper function included in the engine.

# Fallback: curated large-cap universe if scraping fails
EQUITY_WATCHLIST = [
    # US Mega/Large Cap
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "JNJ", "V", "UNH", "XOM", "PG", "MA", "HD", "CVX", "MRK",
    "ABBV", "PEP", "KO", "COST", "LLY", "AVGO", "WMT", "MCD", "CSCO",
    "TMO", "ACN", "ABT", "CRM", "DHR", "NEE", "LIN", "TXN", "PM",
    "UPS", "MS", "RTX", "HON", "INTC", "QCOM", "AMGN", "CAT", "BA",
    "GE", "DE", "IBM", "GS", "BLK",
    # EU Large Cap (traded as ADRs or directly)
    "ASML", "NVO", "SAP", "AZN", "SHEL", "TTE", "NESN.SW", "ROG.SW",
    "NOVN.SW", "SIE.DE", "AIR.PA", "MC.PA", "OR.PA", "SAN.PA", "BNP.PA",
    "ALV.DE", "DTE.DE", "BAS.DE", "BMW.DE", "VOW3.DE",
]

# Your custom watchlist — add any tickers you're tracking
CUSTOM_WATCHLIST = [
    # Add your own tickers here, e.g.:
    # "PLTR", "SOFI", "RIVN",
]

# ============================================================
# CRYPTO UNIVERSE
# ============================================================
CRYPTO_TICKERS = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "DOT-USD", "MATIC-USD", "LINK-USD",
    "ATOM-USD", "UNI-USD", "LTC-USD", "NEAR-USD", "APT-USD",
    "ARB-USD", "OP-USD", "FIL-USD", "INJ-USD", "SUI-USD",
]

# ============================================================
# EQUITY FACTOR PARAMETERS
# ============================================================
EQUITY_FACTORS = {
    "momentum_12_1": {
        "weight": 0.35,         # 12-month return minus last month (Jegadeesh-Titman)
        "lookback_long": 252,   # ~12 months trading days
        "lookback_skip": 21,    # Skip last month (mean-reversion contamination)
    },
    "momentum_6_1": {
        "weight": 0.20,         # 6-month momentum
        "lookback_long": 126,
        "lookback_skip": 21,
    },
    "mean_reversion_5d": {
        "weight": 0.15,         # 5-day mean reversion (contrarian at short horizon)
        "lookback": 5,
        "invert": True,         # Negative return = positive signal
    },
    "volatility_quality": {
        "weight": 0.15,         # Low-vol factor (quality proxy)
        "lookback": 63,         # 3-month realized vol
        "invert": True,         # Lower vol = higher score
    },
    "risk_adjusted_momentum": {
        "weight": 0.15,         # Momentum / volatility (Sharpe-like)
        "mom_lookback": 126,
        "vol_lookback": 63,
    },
}

# ============================================================
# CRYPTO SIGNAL PARAMETERS
# ============================================================
CRYPTO_PARAMS = {
    # Trend signals
    "ema_fast": 21,             # Fast EMA period
    "ema_slow": 50,             # Slow EMA period
    "ema_trend": 200,           # Trend filter EMA

    # Momentum
    "roc_periods": [7, 14, 30, 60],  # Rate of change lookbacks
    "roc_weights": [0.4, 0.3, 0.2, 0.1],  # Recent momentum weighted higher

    # Vol regime filter
    "vol_lookback": 30,         # Days for realized vol calc
    "vol_threshold_high": 0.80, # 80% annualized = reduce size
    "vol_threshold_extreme": 1.20,  # 120% = go to cash
    "vol_scale_factor": 0.5,    # Scale position by this in high-vol

    # RSI for timing
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
}

# ============================================================
# RISK MANAGEMENT
# ============================================================
RISK_PARAMS = {
    # Position sizing
    "kelly_fraction": 0.25,     # Quarter-Kelly (conservative)
    "max_position_equity_pct": 0.08,    # 8% max single equity position
    "max_position_crypto_pct": 0.10,    # 10% max single crypto position
    "max_equity_positions": 15,         # Concentrated portfolio
    "max_crypto_positions": 5,          # Even more concentrated

    # Transaction costs (bps per side)
    "equity_cost_bps": 15,      # ~15 bps for retail on large-cap
    "crypto_cost_bps": 30,      # ~30 bps on major exchanges

    # Drawdown limits
    "weekly_dd_warning": -0.03,     # -3% weekly = warning
    "monthly_dd_stop": -0.08,      # -8% monthly = flatten

    # Minimum position size (EUR) — below this, don't bother
    "min_position_eur": 500,
}

# ============================================================
# OUTPUT SETTINGS
# ============================================================
OUTPUT_DIR = "./signals_output"
REPORT_DATE_FMT = "%Y-%m-%d"
CSV_EXPORT = True
CONSOLE_PRINT = True

# ============================================================
# DATA SETTINGS
# ============================================================
DATA_LOOKBACK_DAYS = 400        # ~18 months of history for signal calc
YAHOO_FINANCE_TIMEOUT = 30      # Seconds before timeout per ticker
