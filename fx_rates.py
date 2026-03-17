#!/usr/bin/env python3
"""
================================================================================
FX RATES MODULE v1.0
================================================================================
Real-time currency conversion with multiple data sources and local caching.

SOURCES (tried in order):
    1. Yahoo Finance intraday (EURUSD=X, 1-minute interval) — near real-time
    2. ECB (European Central Bank) daily reference rates — official, free
    3. Frankfurter API (backed by ECB) — free, no API key
    4. Local cache — fallback if all sources fail

CACHING:
    Rates are cached locally in fx_cache.json with timestamps.
    Cache expires after 30 minutes during market hours, 24h on weekends.

USAGE:
    # As a module (import in other scripts):
    from fx_rates import get_eur_rate, convert_to_eur

    rate = get_eur_rate("USD")         # Returns EUR/USD rate
    eur_value = convert_to_eur(23.53, "USD")  # $23.53 → €XX.XX

    # As standalone:
    python3 fx_rates.py                # Show all rates
    python3 fx_rates.py --pair EURUSD  # Specific pair
================================================================================
"""

import json
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from typing import Optional

warnings.filterwarnings("ignore")

CACHE_FILE = "fx_cache.json"
CACHE_MAX_AGE_MINUTES = 30      # During market hours
CACHE_MAX_AGE_WEEKEND = 1440    # 24 hours on weekends

# Currencies we care about
PAIRS = {
    "USD": "EURUSD=X",   # Most of our tickers
    "GBP": "EURGBP=X",
    "CHF": "EURCHF=X",   # Swiss tickers (NOVN.SW, ROG.SW, UBS)
    "SEK": "EURSEK=X",
    "JPY": "EURJPY=X",
}

# Tickers and their trading currencies
TICKER_CURRENCY = {
    # EUR-denominated (no conversion)
    "SAP": "EUR", "AIR.PA": "EUR", "SIE.DE": "EUR", "ALV.DE": "EUR",
    "BAS.DE": "EUR", "BMW.DE": "EUR", "VOW.DE": "EUR", "DTE.DE": "EUR",
    "MC.PA": "EUR", "OR.PA": "EUR", "SAN.PA": "EUR", "BNP.PA": "EUR",
    "DBK.DE": "EUR", "NOKIA.HE": "EUR",
    # CHF-denominated
    "NOVN.SW": "CHF", "ROG.SW": "CHF", "NESN.SW": "CHF", "UBS": "CHF",
    # Everything else is USD
}


def _load_cache() -> dict:
    """Load cached FX rates from disk."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    """Save FX rates to disk."""
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


def _is_cache_fresh(cache: dict, currency: str) -> bool:
    """Check if cached rate is still fresh."""
    if currency not in cache:
        return False

    cached_time = datetime.fromisoformat(cache[currency]["timestamp"])
    age_minutes = (datetime.now() - cached_time).total_seconds() / 60

    # Weekends: cache valid for 24h (markets closed)
    now = datetime.now()
    is_weekend = now.weekday() >= 5
    max_age = CACHE_MAX_AGE_WEEKEND if is_weekend else CACHE_MAX_AGE_MINUTES

    return age_minutes < max_age


def _fetch_yahoo_realtime(currency: str) -> Optional[float]:
    """
    Source 1: Yahoo Finance intraday data.
    Uses 1-minute interval for near real-time rates.
    """
    try:
        import yfinance as yf
        pair = PAIRS.get(currency)
        if not pair:
            return None

        # Try intraday first (1-minute interval, last 1 day)
        data = yf.download(pair, period="1d", interval="1m",
                           auto_adjust=True, progress=False)
        if not data.empty:
            close = data["Close"]
            if hasattr(close, 'iloc'):
                if isinstance(close, type(data)):
                    close = close.iloc[:, 0]
                rate = float(close.iloc[-1])
                if rate > 0:
                    return rate

        # Fallback to daily
        data = yf.download(pair, period="2d", auto_adjust=True, progress=False)
        if not data.empty:
            close = data["Close"]
            if hasattr(close, 'iloc'):
                if isinstance(close, type(data)):
                    close = close.iloc[:, 0]
                rate = float(close.iloc[-1])
                if rate > 0:
                    return rate
    except Exception:
        pass
    return None


def _fetch_ecb(currency: str) -> Optional[float]:
    """
    Source 2: ECB daily reference rates via Frankfurter API.
    Free, no API key, backed by European Central Bank.
    Updates daily at ~16:00 CET.
    """
    try:
        import urllib.request
        url = f"https://api.frankfurter.app/latest?from=EUR&to={currency}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "SignalEngine/1.0"
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            rate = data.get("rates", {}).get(currency)
            if rate and rate > 0:
                return float(rate)
    except Exception:
        pass
    return None


def _fetch_frankfurter_latest() -> dict:
    """
    Fetch all EUR rates from Frankfurter API in one call.
    Returns dict of {currency: rate}.
    """
    try:
        import urllib.request
        currencies = ",".join(PAIRS.keys())
        url = f"https://api.frankfurter.app/latest?from=EUR&to={currencies}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "SignalEngine/1.0"
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data.get("rates", {})
    except Exception:
        return {}


def get_eur_rate(currency: str, verbose: bool = False) -> float:
    """
    Get the EUR/XXX exchange rate (how many XXX per 1 EUR).

    For USD: returns ~1.09 meaning 1 EUR = 1.09 USD
    To convert USD to EUR: eur_value = usd_value / rate

    Returns rate, falls back to cache, then to hardcoded estimates.
    """
    if currency == "EUR":
        return 1.0

    cache = _load_cache()

    # Check cache first
    if _is_cache_fresh(cache, currency):
        rate = cache[currency]["rate"]
        if verbose:
            age = (datetime.now() - datetime.fromisoformat(cache[currency]["timestamp"])).total_seconds() / 60
            print(f"    FX {currency}: {rate:.4f} (cached, {age:.0f}m ago)")
        return rate

    # Try sources in order
    rate = None
    source = None

    # Source 1: Yahoo Finance real-time
    rate = _fetch_yahoo_realtime(currency)
    if rate:
        source = "Yahoo Finance (intraday)"

    # Source 2: ECB / Frankfurter
    if not rate:
        rate = _fetch_ecb(currency)
        if rate:
            source = "ECB (Frankfurter)"

    # Source 3: Stale cache
    if not rate and currency in cache:
        rate = cache[currency]["rate"]
        source = "cache (stale)"

    # Source 4: Hardcoded fallback
    if not rate:
        fallback = {"USD": 1.09, "GBP": 0.86, "CHF": 0.97, "SEK": 11.20, "JPY": 163.0}
        rate = fallback.get(currency, 1.0)
        source = "hardcoded fallback"

    # Update cache
    cache[currency] = {
        "rate": rate,
        "source": source,
        "timestamp": datetime.now().isoformat(),
    }
    _save_cache(cache)

    if verbose:
        print(f"    FX {currency}: {rate:.4f} ({source})")

    return rate


def get_all_rates(verbose: bool = False) -> dict:
    """Fetch all configured FX rates at once."""
    rates = {}

    # Try batch fetch from Frankfurter first
    batch = _fetch_frankfurter_latest()
    if batch:
        cache = _load_cache()
        for currency, rate in batch.items():
            rates[currency] = rate
            cache[currency] = {
                "rate": rate,
                "source": "ECB (Frankfurter batch)",
                "timestamp": datetime.now().isoformat(),
            }
        _save_cache(cache)

        if verbose:
            for c, r in rates.items():
                print(f"    EUR/{c}: {r:.4f}")

    # Fill any missing with Yahoo
    for currency in PAIRS:
        if currency not in rates:
            rates[currency] = get_eur_rate(currency, verbose)

    rates["EUR"] = 1.0
    return rates


def convert_to_eur(amount: float, from_currency: str, verbose: bool = False) -> float:
    """
    Convert an amount from any currency to EUR.

    convert_to_eur(23.53, "USD") → ~21.59 EUR
    convert_to_eur(189.94, "EUR") → 189.94 EUR
    """
    if from_currency == "EUR":
        return amount

    rate = get_eur_rate(from_currency, verbose)
    return amount / rate if rate > 0 else amount


def get_ticker_currency(ticker: str) -> str:
    """Get the trading currency for a ticker."""
    if ticker in TICKER_CURRENCY:
        return TICKER_CURRENCY[ticker]
    # Default: if it has a country suffix, try to determine
    if ticker.endswith(".DE") or ticker.endswith(".PA") or ticker.endswith(".HE"):
        return "EUR"
    if ticker.endswith(".SW"):
        return "CHF"
    if ticker.endswith(".L"):
        return "GBP"
    if ticker.endswith(".T"):
        return "JPY"
    # Default to USD for US-listed stocks
    return "USD"


def convert_ticker_price_to_eur(price: float, ticker: str, verbose: bool = False) -> float:
    """Convert a ticker's price to EUR based on its trading currency."""
    currency = get_ticker_currency(ticker)
    return convert_to_eur(price, currency, verbose)


# ==============================================================================
# STANDALONE USAGE
# ==============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="FX Rates Module")
    parser.add_argument("--pair", type=str, help="Specific pair, e.g. USD, GBP, CHF")
    parser.add_argument("--refresh", action="store_true", help="Force refresh all rates")
    args = parser.parse_args()

    if args.refresh:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
            print("  Cache cleared.")

    print(f"\n{'═' * 50}")
    print(f"  FX RATES — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'═' * 50}")

    if args.pair:
        currency = args.pair.upper()
        rate = get_eur_rate(currency, verbose=True)
        print(f"\n  EUR/{currency}: {rate:.4f}")
        print(f"  1 {currency} = €{1/rate:.4f}")
        print(f"  1 EUR = {rate:.4f} {currency}")
    else:
        rates = get_all_rates(verbose=True)
        print(f"\n  {'Pair':<12}{'Rate':>10}{'1 unit → EUR':>15}")
        print(f"  {'─' * 37}")
        for currency in sorted(rates.keys()):
            if currency == "EUR":
                continue
            rate = rates[currency]
            print(f"  EUR/{currency:<6}  {rate:>10.4f}  €{1/rate:>12.4f}")

    # Show cache info
    cache = _load_cache()
    print(f"\n  Cache file: {CACHE_FILE}")
    for currency, data in cache.items():
        ts = data.get("timestamp", "unknown")
        src = data.get("source", "unknown")
        print(f"    {currency}: {data['rate']:.4f} via {src} at {ts[:19]}")

    print()


if __name__ == "__main__":
    main()
