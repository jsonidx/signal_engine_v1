"""
utils/setup_outcome_resolver.py — Pre-Breakout Setup Outcome Resolver  (TRD-040)

Converts setup_watchlist entries into standardized forward-outcome labels.
Point-in-time safe: only reads price data available AFTER the setup_date.
No ML, no future-data leakage, no production-scoring changes.

Outcome fields
--------------
- raw returns at 5 / 10 / 20 / 40 trading days
- sector-adjusted returns (vs SPY as default benchmark)
- max adverse excursion (MAE) over 20d and 40d
- max favorable excursion (MFE) over 20d and 40d
- binary labels: success_20d, success_40d, failed_20d
- confirmation-pipeline overlap: confirmed_later, days_to_confirmation
- market_regime (from regime_snapshots if available)

Label definitions
-----------------
success_20d  : sector-adj 20d return > +10%
success_40d  : sector-adj 40d return > +5%
failed_20d   : sector-adj 20d return < 0%
confirmed_later : ticker appeared in daily_rankings after setup_date
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Label thresholds ───────────────────────────────────────────────────────────
SUCCESS_20D_THRESHOLD = 10.0   # sector-adj % return
SUCCESS_40D_THRESHOLD = 5.0
HORIZONS = [5, 10, 20, 40]    # trading-day forward windows
BENCHMARK_TICKER = "SPY"

SECTOR_ETF = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Financial Services": "XLF",
    "Communication Services": "XLC",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}


def _bdate_index(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.bdate_range(start=start, end=end)]


def _nth_tday(setup_date: date, n: int, tdays: list[date]) -> date | None:
    try:
        pos = tdays.index(setup_date)
    except ValueError:
        # Find closest date on or after setup_date
        for i, d in enumerate(tdays):
            if d >= setup_date:
                pos = i
                break
        else:
            return None
    target = pos + n
    return tdays[target] if target < len(tdays) else None


def _fetch_confirmation_dates(ticker: str, after_date: date) -> list[date]:
    """Return dates where ticker appeared in daily_rankings after after_date."""
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT run_date FROM daily_rankings WHERE ticker=%s AND run_date>%s ORDER BY run_date",
            (ticker.upper(), str(after_date)),
        )
        rows = cur.fetchall()
        conn.close()
        return [r["run_date"] for r in rows]
    except Exception as exc:
        logger.debug("_fetch_confirmation_dates(%s) failed: %s", ticker, exc)
        return []


def _fetch_regime(run_date: date) -> str | None:
    """Return market regime for a given date from regime_snapshots."""
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT regime FROM regime_snapshots WHERE date=%s LIMIT 1",
            (str(run_date),),
        )
        row = cur.fetchone()
        conn.close()
        return row["regime"] if row else None
    except Exception:
        return None


def resolve_outcome(
    setup_date: date,
    ticker: str,
    sector: str | None,
    prices: pd.DataFrame,          # daily closes: date-indexed, ticker+benchmark columns
    today: date | None = None,
    composite_score: float | None = None,
    pfs_score: float | None = None,
    psc_score: float | None = None,
    erm_score: float | None = None,
    archetype: str | None = None,
) -> dict:
    """
    Compute forward outcome for a single (setup_date, ticker) pair.

    Parameters
    ----------
    setup_date   : date of the setup alert
    ticker       : equity ticker
    sector       : GICS sector string (used to select sector ETF benchmark)
    prices       : DataFrame of close prices (must include ticker and benchmark columns)
    today        : as-of date for maturity check (defaults to date.today())
    *_score      : component scores from setup_watchlist (for storage)
    archetype    : Stage 3 archetype if available

    Returns a dict compatible with save_setup_outcome().
    """
    today = today or date.today()
    tdays = sorted(prices.index.tolist())

    bench_etf = SECTOR_ETF.get(sector or "", BENCHMARK_TICKER)
    if bench_etf not in prices.columns:
        bench_etf = BENCHMARK_TICKER

    rec: dict[str, Any] = {
        "setup_date": setup_date,
        "ticker": ticker.upper(),
        "composite_score": composite_score,
        "pfs_score": pfs_score,
        "psc_score": psc_score,
        "erm_score": erm_score,
        "archetype": archetype,
        "mature_20d": False,
        "mature_40d": False,
        "resolved_at": today,
    }

    if ticker not in prices.columns:
        return rec

    # Entry price
    try:
        entry_idx = next(i for i, d in enumerate(tdays) if d >= setup_date)
        entry_date = tdays[entry_idx]
    except StopIteration:
        return rec

    entry_price = prices[ticker].get(entry_date)
    bench_entry = prices[bench_etf].get(entry_date) if bench_etf in prices.columns else None
    if not entry_price or entry_price <= 0:
        return rec

    # ── Forward returns ────────────────────────────────────────────────────────
    for h in HORIZONS:
        fwd_date = _nth_tday(entry_date, h, tdays)
        mature = fwd_date is not None and fwd_date <= today

        if h == 20:
            rec["mature_20d"] = mature
        if h == 40:
            rec["mature_40d"] = mature

        if not mature or fwd_date is None:
            continue

        fwd_price = prices[ticker].get(fwd_date)
        bench_fwd = prices[bench_etf].get(fwd_date) if bench_etf in prices.columns else None

        if fwd_price and fwd_price > 0:
            raw_ret = (fwd_price / entry_price - 1) * 100.0
            rec[f"ret_{h}d"] = raw_ret

            if bench_entry and bench_fwd and bench_entry > 0 and bench_fwd > 0:
                bench_ret = (bench_fwd / bench_entry - 1) * 100.0
                rec[f"ret_{h}d_excess"] = raw_ret - bench_ret

    # ── MAE / MFE over 20d and 40d ────────────────────────────────────────────
    for h, label in [(20, "20d"), (40, "40d")]:
        fwd_date = _nth_tday(entry_date, h, tdays)
        if fwd_date is None or fwd_date > today:
            continue
        try:
            start_i = tdays.index(entry_date)
            end_i = tdays.index(fwd_date)
            window_prices = prices[ticker].iloc[start_i:end_i + 1].dropna()
            if len(window_prices) < 2:
                continue
            excursions = ((window_prices / entry_price) - 1) * 100.0
            rec[f"mae_{label}"] = float(excursions.min())
            rec[f"mfe_{label}"] = float(excursions.max())
        except Exception:
            pass

    # ── Binary labels (from sector-adjusted returns) ───────────────────────────
    excess_20 = rec.get("ret_20d_excess")
    excess_40 = rec.get("ret_40d_excess")

    if excess_20 is not None and rec.get("mature_20d"):
        rec["success_20d"] = excess_20 > SUCCESS_20D_THRESHOLD
        rec["failed_20d"] = excess_20 < 0.0

    if excess_40 is not None and rec.get("mature_40d"):
        rec["success_40d"] = excess_40 > SUCCESS_40D_THRESHOLD

    # ── Confirmation pipeline overlap ──────────────────────────────────────────
    conf_dates = _fetch_confirmation_dates(ticker, setup_date)
    if conf_dates:
        rec["confirmed_later"] = True
        first_conf = min(conf_dates)
        try:
            setup_i = next(i for i, d in enumerate(tdays) if d >= setup_date)
            conf_i  = next(i for i, d in enumerate(tdays) if d >= first_conf)
            rec["days_to_confirmation"] = conf_i - setup_i
        except StopIteration:
            pass
    else:
        rec["confirmed_later"] = False

    # ── Market regime ──────────────────────────────────────────────────────────
    rec["market_regime"] = _fetch_regime(setup_date)

    return rec


def run_resolution_batch(
    setups: list[dict],
    today: date | None = None,
    sector_map: dict[str, str] | None = None,
) -> list[dict]:
    """
    Resolve a batch of setup_watchlist dicts into outcome dicts.

    setups must be dicts with at least: run_date, ticker.
    Prices are fetched from yfinance for all tickers in one batch call.

    Returns list of outcome dicts suitable for save_setup_outcome().
    """
    import yfinance as yf
    from datetime import timedelta

    today = today or date.today()
    sector_map = sector_map or {}

    if not setups:
        return []

    all_tickers = list({s["ticker"].upper() for s in setups})
    # Compute earliest setup date
    dates = [s["run_date"] if isinstance(s["run_date"], date) else date.fromisoformat(str(s["run_date"]))
             for s in setups]
    earliest = min(dates)
    fetch_start = earliest - timedelta(days=5)

    all_symbols = list(set(all_tickers + list(SECTOR_ETF.values()) + [BENCHMARK_TICKER]))

    try:
        raw = yf.download(
            " ".join(all_symbols),
            start=fetch_start,
            end=today + timedelta(days=1),
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            prices = raw["Close"]
        else:
            prices = raw[["Close"]].rename(columns={"Close": all_symbols[0]})
        prices.index = pd.to_datetime(prices.index).date
    except Exception as exc:
        logger.warning("run_resolution_batch: price fetch failed: %s", exc)
        return []

    tdays = sorted(prices.index.tolist())
    outcomes = []
    for setup in setups:
        ticker = str(setup["ticker"]).strip().upper()
        setup_date = setup["run_date"]
        if isinstance(setup_date, str):
            setup_date = date.fromisoformat(setup_date)

        outcome = resolve_outcome(
            setup_date=setup_date,
            ticker=ticker,
            sector=sector_map.get(ticker) or setup.get("sector"),
            prices=prices,
            today=today,
            composite_score=setup.get("composite_score"),
            pfs_score=setup.get("pfs_score"),
            psc_score=setup.get("psc_score"),
            erm_score=setup.get("erm_score"),
            archetype=setup.get("archetype"),
        )
        outcomes.append(outcome)

    return outcomes
