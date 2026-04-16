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
PORTFOLIO_NAV = 0               # Fallback only — live value read from Supabase portfolio_settings (key='cash_eur')
EQUITY_ALLOCATION = 0.65        # 65% to equities
CRYPTO_ALLOCATION = 0.25        # 25% to crypto
CASH_BUFFER = 0.10              # 10% cash reserve (dry powder)

# ============================================================
# EQUITY UNIVERSE
# ============================================================
# No hardcoded tickers.  Universe is 100% dynamic:
#   Primary  : watchlist.txt (auto-updated by universe_builder.py)
#   Favorites: Supabase user_favorites table (via favorites.py)
#   Positions: Supabase trades table (status='open')
#
# To seed your favorites run:
#   python3 -c "from favorites import add_favorite; add_favorite('AAPL')"
# Or use the Favorites panel in the dashboard.

# Kept for CLI --watchlist arg compatibility; populated at runtime only.
EQUITY_WATCHLIST: list = []
CUSTOM_WATCHLIST: list = []

# ============================================================
# CRYPTO UNIVERSE
# ============================================================
# Loaded dynamically from user_watchlists (category='crypto') in Supabase.
# If that table is empty the crypto module is skipped with a warning.
# Seed via: INSERT INTO user_watchlists (ticker, category) VALUES ('BTC-USD','crypto');
CRYPTO_TICKERS: list = []

# ============================================================
# EQUITY FACTOR PARAMETERS
# ============================================================
EQUITY_FACTORS = {
    "momentum_12_1": {
        "weight": 0.28,         # 12-month return minus last month (Jegadeesh-Titman)
        "lookback_long": 252,   # ~12 months trading days
        "lookback_skip": 21,    # Skip last 21 trading days (mean-reversion contamination)
    },
    "momentum_6_1": {
        "weight": 0.16,         # 6-month momentum
        "lookback_long": 126,
        "lookback_skip": 21,
    },
    "earnings_revision": {
        "weight": 0.18,         # Sell-side FY1 EPS revision momentum (well-documented alpha)
    },
    "ivol": {
        "weight": 0.12,         # Negative idiosyncratic vol — low IVOL = quality premium
        "lookback": 63,         # 3-month regression window
    },
    "52wk_high_proximity": {
        "weight": 0.10,         # George-Hwang (2004): price / 52wk high
    },
    "mean_reversion_5d": {
        "weight": 0.08,         # 5-day mean reversion (contrarian at short horizon)
        "lookback": 5,
        "invert": True,         # Negative return = positive signal
    },
    "volatility_quality": {
        "weight": 0.08,         # Low-vol factor (quality proxy)
        "lookback": 63,         # 3-month realized vol
        "invert": True,         # Lower vol = higher score
    },
    # NOTE: regime_filter.py's get_factor_weights() overrides these in RISK_OFF.
    # Update regime_filter to include: earnings_revision, ivol, 52wk_high_proximity.
    # Weights above sum to 1.0.  Removed: risk_adjusted_momentum (0.15 → redistributed).
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
TRANSACTION_COST_BPS = 5        # One-way transaction cost (bps) for paper trader + backtest
YAHOO_FINANCE_TIMEOUT = 30      # Seconds before timeout per ticker

# ============================================================
# POLYMARKET PREDICTION MARKET PARAMETERS
# ============================================================
# ============================================================
# UNIVERSE BUILDER PARAMETERS
# ============================================================
UNIVERSE_INDICES = [
    "russell1000", "russell2000", "sp500", "sp400",   # US large/mid/small
    # "iefa", "iemg", "acwi",                          # international — slow (~5500 tickers), covered by LIQUID_ADRS
]
UNIVERSE_PRESCREEN_TOP_N    = 200
UNIVERSE_MIN_DOLLAR_VOLUME  = 3_000_000   # 30-day avg dollar volume ($)
UNIVERSE_MIN_PRICE          = 1.5          # Minimum share price ($) — global-compatible
UNIVERSE_CACHE_TTL_HOURS    = 24           # Cache TTL for index constituents (hours)
UNIVERSE_ATR_PCT_MAX        = 6.0          # Drop if 20-day ATR% > this (quality gate)
UNIVERSE_BETA_MAX           = 2.0          # Drop if 60-day beta vs SPY > this

# ============================================================
# REGIME FILTER PARAMETERS
# ============================================================
REGIME_CACHE_TTL_HOURS    = 24           # Cache TTL for regime + sector data (hours)
FRED_YIELD_CURVE_SERIES   = "T10Y2Y"     # FRED series: 10-year minus 2-year Treasury spread
FRED_USER_AGENT           = "SignalEngine/1.0 (research)"
REGIME_RISK_ON_THRESHOLD  = 3            # Total score >= this → RISK_ON
REGIME_RISK_OFF_THRESHOLD = 0            # Total score <= this → RISK_OFF

# ============================================================
# POLYMARKET PREDICTION MARKET PARAMETERS
# ============================================================
# ============================================================
# DARK POOL FLOW PARAMETERS
# ============================================================
DARK_POOL_ACCUMULATION_THRESHOLD = 65   # score above this = ACCUMULATION
DARK_POOL_DISTRIBUTION_THRESHOLD = 35   # score below this = DISTRIBUTION
DARK_POOL_INTENSITY_HIGH = 0.45         # above this = heavy institutional off-exchange routing

# ============================================================
# POLYMARKET PREDICTION MARKET PARAMETERS
# ============================================================
# ============================================================
# IMPLIED VOLATILITY (IV) PARAMETERS
# ============================================================
IV_RISK_FREE_RATE    = 0.05              # Fed funds rate approximation (update as rates change)
IV_TARGET_DTE        = 30               # Target days-to-expiry for ATM IV interpolation
IV_MIN_HISTORY_DAYS  = 60               # Min stored rows before iv_rank/percentile returns a value
IV_HISTORY_DB        = "data/iv_history.db"  # Legacy constant — iv_history is now in Supabase (iv_history table)

# ============================================================
# SOCIAL SENTIMENT PARAMETERS
# ============================================================
SOCIAL_TRENDS_LOOKBACK_DAYS      = 30   # Google Trends timeframe: f'now {N}-d' (max 90)
SOCIAL_TRENDS_CACHE_TTL_HOURS    = 24   # Re-fetch Trends data at most once per day
SOCIAL_STOCKTWITS_CACHE_TTL_HOURS = 4  # StockTwits updates frequently; 4hr TTL is safe
SOCIAL_BULLISH_THRESHOLD         = 0.65 # bull_ratio above this → BULLISH sentiment signal
SOCIAL_BEARISH_THRESHOLD         = 0.35 # bull_ratio below this → BEARISH sentiment signal

# ============================================================
# POLYMARKET PREDICTION MARKET PARAMETERS
# ============================================================
# ============================================================
# AI MODEL CONFIGURATION
# ============================================================
AI_MODEL_DEFAULT   = "grok-4-1-fast-reasoning"   # Daily driver — fast + light reasoning
AI_MODEL_PREMIUM   = "grok-4.20-0309-reasoning"  # High-conviction setups only (~3× cost)
AI_MODEL_FALLBACK  = "grok-4-1-fast-reasoning"   # Retry model if premium call fails
AI_PREMIUM_THRESHOLD = 0.85                       # signal_agreement_score ≥ this → premium

# ============================================================
# AI QUANT API CALL LIMITS
# ============================================================
AI_QUANT_MAX_TICKERS = 5            # Hard cap on Grok API calls per run
AI_QUANT_MIN_AGREEMENT = 0.60       # Minimum signal_agreement_score to qualify
AI_QUANT_MIN_CONVICTION_SCORE = 13  # Minimum composite catalyst score to qualify
AI_QUANT_ALWAYS_INCLUDE: list = []  # Populated at runtime from trade_journal open positions

# Paths
import pathlib
BASE_DIR = pathlib.Path(__file__).parent

POLYMARKET_PARAMS = {
    # API
    "api_base_url": "https://gamma-api.polymarket.com",
    "cache_file": "polymarket_cache.json",
    "cache_ttl_hours": 1,           # Cache market listings for 1 hour
    "max_markets_fetch": 500,       # Max markets per refresh (5 pages × 100)
    "page_size": 100,               # Markets per API page
    "request_timeout": 15,          # Seconds per API call
    "request_delay": 0.5,           # Seconds between paginated calls

    # Signal inclusion thresholds
    "min_volume_24h": 500,          # Min 24h USD volume to include
    "min_liquidity": 500,           # Min liquidity (USD) to include
    "min_days_to_resolution": 1,    # Skip markets resolving today
    "max_days_to_resolution": 180,  # Skip markets >6 months out

    # Signal scoring thresholds
    "strong_consensus_high": 0.70,  # Probability >= this → strong Yes signal
    "strong_consensus_low": 0.30,   # Probability <= this → strong No signal
    "moderate_consensus_high": 0.60,
    "moderate_consensus_low": 0.40,

    # Volume tiers for confidence
    "volume_high": 50_000,          # ≥ $50k 24h volume = high confidence
    "volume_medium": 10_000,        # ≥ $10k
    "volume_low": 1_000,            # ≥ $1k

    # Liquidity tiers
    "liquidity_high": 100_000,      # ≥ $100k liquidity
    "liquidity_medium": 10_000,
    "liquidity_low": 1_000,
}
