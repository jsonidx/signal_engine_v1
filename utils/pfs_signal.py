"""
utils/pfs_signal.py — Peer-First-Mover Sympathy (PFS) Signal  (TRD-035)

Deterministic signal only. No LLM, no options, no dark-pool data.

Algorithm
---------
1. Group tickers by sector.
2. For each group, detect "first movers": tickers that moved ≥ TRIGGER_PCT in
   the last TRIGGER_WINDOW trading days with above-average volume.
3. For each first-mover event, score non-participating peers that are still
   within the signal-validity window (DECAY_WINDOW trading days from trigger).
4. Apply anti-laggard guard: discard peers that have already moved ≥ LAGGARD_PCT.
5. Apply mass-rally suppression: skip events where ≥ MASS_RALLY_FRACTION of the
   sector moved on the same trigger day (likely ETF rotation, not sympathy).
6. Return a per-ticker PFS score in [0, 1], clipped at 0 when no valid trigger.

All windows use trading days (business-day calendar, no holiday correction).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import NamedTuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Tuning constants ───────────────────────────────────────────────────────────
TRIGGER_PCT = 8.0        # % move in TRIGGER_WINDOW days to classify a first mover
TRIGGER_WINDOW = 5       # trading days to measure trigger move
DECAY_WINDOW = 5         # trading days after trigger during which PFS is valid
LAGGARD_PCT = 4.0        # % already moved by non-participant → discard (laggard chase)
MASS_RALLY_FRACTION = 0.6  # fraction of sector peers moving → suppress (ETF day)
VOLUME_MULTIPLIER = 1.3  # first mover must have volume ≥ multiplier × 20d avg
MIN_PEER_GROUP = 3       # minimum peers to form a valid group
LOOKBACK_DAYS = 70       # trading days of price history to fetch


class PFSResult(NamedTuple):
    ticker: str
    pfs_score: float          # 0-1
    trigger_ticker: str | None
    trigger_date: date | None
    trigger_move_pct: float | None
    days_since_trigger: int | None
    peer_group_size: int


def _bdate_range(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.bdate_range(start=start, end=end)]


def _nth_trading_day_back(ref: date, n: int, tdays: list[date]) -> date | None:
    try:
        pos = tdays.index(ref)
    except ValueError:
        return None
    back = pos - n
    return tdays[back] if back >= 0 else None


def score_pfs(
    universe: list[str],
    sector_map: dict[str, str],    # ticker → sector
    prices: pd.DataFrame,          # indexed by date, columns = tickers (close prices)
    volumes: pd.DataFrame,         # indexed by date, columns = tickers (volumes)
    as_of_date: date | None = None,
) -> list[PFSResult]:
    """
    Score all tickers in *universe* on the PFS signal.

    Parameters
    ----------
    universe    : list of ticker symbols to score
    sector_map  : ticker → sector string (GICS sector level)
    prices      : pd.DataFrame of adjusted closes; date-indexed, ticker columns
    volumes     : pd.DataFrame of daily volumes; same shape as prices
    as_of_date  : evaluation date (defaults to latest date in prices)

    Returns list of PFSResult, one per ticker in universe.
    """
    if prices.empty or volumes.empty:
        return [PFSResult(t, 0.0, None, None, None, None, 0) for t in universe]

    tdays = sorted(prices.index.tolist())
    if not tdays:
        return [PFSResult(t, 0.0, None, None, None, None, 0) for t in universe]

    as_of = as_of_date or tdays[-1]
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)

    # Trim prices to as_of to enforce point-in-time safety
    prices = prices[prices.index <= as_of].copy()
    volumes = volumes[volumes.index <= as_of].copy()
    tdays = sorted(prices.index.tolist())
    if len(tdays) < TRIGGER_WINDOW + 1:
        return [PFSResult(t, 0.0, None, None, None, None, 0) for t in universe]

    # ── Group by sector ────────────────────────────────────────────────────────
    sector_groups: dict[str, list[str]] = {}
    for ticker in universe:
        if ticker not in prices.columns:
            continue
        sector = sector_map.get(ticker, "Unknown")
        sector_groups.setdefault(sector, []).append(ticker)

    # ── Detect first-mover events ──────────────────────────────────────────────
    # For each sector, for each of the last DECAY_WINDOW trigger-check days,
    # find tickers that moved ≥ TRIGGER_PCT.

    # Window to scan for triggers: last (TRIGGER_WINDOW + DECAY_WINDOW) trading days
    scan_start_idx = max(0, len(tdays) - TRIGGER_WINDOW - DECAY_WINDOW - 1)
    scan_days = tdays[scan_start_idx:]

    # Precompute volume 20d avg for volume-confirmation filter
    vol_avg_20 = volumes.rolling(20, min_periods=5).mean()

    # Build first-mover events: list of (sector, trigger_ticker, trigger_date, move_pct)
    events: list[tuple[str, str, date, float]] = []

    for sector, peers in sector_groups.items():
        if len(peers) < MIN_PEER_GROUP:
            continue

        peer_prices = prices[[p for p in peers if p in prices.columns]]
        peer_volumes = volumes[[p for p in peers if p in volumes.columns]]

        # For each day in scan window, check each peer for a trigger
        for day in scan_days:
            try:
                day_idx = tdays.index(day)
            except ValueError:
                continue
            start_idx = day_idx - TRIGGER_WINDOW
            if start_idx < 0:
                continue
            start_day = tdays[start_idx]

            movers_today: list[str] = []
            for ticker in peers:
                if ticker not in peer_prices.columns:
                    continue
                p_start = peer_prices[ticker].get(start_day)
                p_end = peer_prices[ticker].get(day)
                if p_start is None or p_end is None or p_start <= 0:
                    continue
                move = (p_end / p_start - 1) * 100.0

                # Volume confirmation
                vol_today = peer_volumes[ticker].get(day)
                avg_vol = vol_avg_20[ticker].get(day) if ticker in vol_avg_20.columns else None
                vol_confirmed = (
                    vol_today is not None
                    and avg_vol is not None
                    and avg_vol > 0
                    and vol_today >= VOLUME_MULTIPLIER * avg_vol
                )

                if move >= TRIGGER_PCT and vol_confirmed:
                    movers_today.append((ticker, move))

            # Mass-rally suppression: too many peers moved → ETF day, suppress
            if len(movers_today) >= MASS_RALLY_FRACTION * len(peers):
                continue

            for trigger_ticker, move_pct in movers_today:
                events.append((sector, trigger_ticker, day, move_pct))

    # ── Score non-participating peers ──────────────────────────────────────────
    # For each ticker, find the best (most recent, strongest) valid trigger event
    # from a different ticker in the same sector.

    results: list[PFSResult] = []

    for ticker in universe:
        sector = sector_map.get(ticker, "Unknown")
        peers = sector_groups.get(sector, [])

        # Only events from OTHER tickers in same sector, within DECAY_WINDOW
        valid_events = [
            e for e in events
            if e[0] == sector
            and e[1] != ticker
            and (as_of - e[2]).days >= 0
        ]

        # Filter to within DECAY_WINDOW trading days of as_of
        def _tdays_between(d1: date, d2: date) -> int:
            try:
                i1 = tdays.index(d1) if d1 in tdays else 0
                i2 = tdays.index(d2) if d2 in tdays else len(tdays) - 1
                return abs(i2 - i1)
            except Exception:
                return 999

        valid_events = [
            e for e in valid_events
            if _tdays_between(e[2], as_of) <= DECAY_WINDOW
        ]

        if not valid_events or ticker not in prices.columns:
            results.append(PFSResult(ticker, 0.0, None, None, None, None, len(peers)))
            continue

        # Anti-laggard: has the target already moved?
        # Compute target move over same TRIGGER_WINDOW
        try:
            day_idx = tdays.index(as_of) if as_of in tdays else len(tdays) - 1
            start_idx = max(0, day_idx - TRIGGER_WINDOW)
            p_start = prices[ticker].iloc[start_idx]
            p_end = prices[ticker].iloc[day_idx]
            target_move = abs((p_end / p_start - 1) * 100.0) if p_start > 0 else 0.0
        except Exception:
            target_move = 0.0

        if target_move >= LAGGARD_PCT:
            # Target has already moved — laggard-chasing guard fires
            results.append(PFSResult(ticker, 0.0, None, None, None, None, len(peers)))
            continue

        # Pick best event: most recent, then largest move
        best = max(valid_events, key=lambda e: (e[2], e[3]))
        sector_e, trigger_t, trigger_d, move_pct = best
        days_since = _tdays_between(trigger_d, as_of)

        # Score: start at 1.0, decay linearly with time since trigger
        decay = 1.0 - (days_since / (DECAY_WINDOW + 1))
        # Boost for larger first-mover move (capped at 2× trigger)
        move_factor = min(move_pct / TRIGGER_PCT, 2.0) / 2.0
        pfs_score = float(np.clip(decay * (0.7 + 0.3 * move_factor), 0.0, 1.0))

        results.append(PFSResult(
            ticker=ticker,
            pfs_score=pfs_score,
            trigger_ticker=trigger_t,
            trigger_date=trigger_d,
            trigger_move_pct=move_pct,
            days_since_trigger=days_since,
            peer_group_size=len(peers),
        ))

    return results
