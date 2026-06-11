"""
Option Recommendation Outcome Tracking  (TRD-027)
================================================================================
Deterministic resolution of historical option_candidate_snapshots into
measurable outcome records.

Resolution uses yfinance for underlying price history.  Option-price movement
is approximated via delta (where stored) rather than requiring a live chain:

    approx_option_return ≈ Δ × (underlying_move / underlying_price)

This is linear delta approximation — accurate for small moves, conservative
for large ones.  It is reproducible and does not require a market-data
subscription.

Resolution windows: '1d', '5d', '10d', 'expiry'
Each window produces one row in option_candidate_outcomes.
================================================================================
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional

import yfinance as yf

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal price-history cache to avoid repeated yfinance calls
# ---------------------------------------------------------------------------
_price_cache: dict[str, Any] = {}   # ticker → DataFrame


def _fetch_history(ticker: str, start: str, end: str) -> Any:
    """Fetch OHLCV history; cached by ticker for the session."""
    import pandas as pd
    key = f"{ticker}:{start}:{end}"
    if key not in _price_cache:
        try:
            df = yf.download(
                ticker, start=start, end=end,
                auto_adjust=True, progress=False, threads=False,
            )
            _price_cache[key] = df
        except Exception as exc:
            log.warning("yfinance history failed for %s: %s", ticker, exc)
            _price_cache[key] = pd.DataFrame()
    return _price_cache[key]


def _close_on_date(hist: Any, target_date: date) -> Optional[float]:
    """Return the Close price on or just after target_date; None if unavailable."""
    if hist is None or hist.empty:
        return None
    import pandas as pd
    target_ts = pd.Timestamp(target_date)
    # Find the first available close at or after the target date
    future = hist[hist.index >= target_ts]
    if future.empty:
        return None
    try:
        return float(future["Close"].iloc[0])
    except Exception:
        return None


def _pct(new_price: Optional[float], base_price: Optional[float]) -> Optional[float]:
    if new_price is None or base_price is None or base_price == 0:
        return None
    return round((new_price - base_price) / base_price * 100, 4)


def _approx_option_price(
    underlying_return_pct: Optional[float],
    delta: Optional[float],
    original_mid: Optional[float],
) -> Optional[float]:
    """
    Approximate option mid price after an underlying move using delta.
    new_option_mid ≈ original_mid × (1 + delta_ratio × underlying_return_pct)
    where delta_ratio = delta / (call/put direction)

    This is a first-order approximation only.
    """
    if underlying_return_pct is None or delta is None or original_mid is None:
        return None
    if original_mid <= 0:
        return None
    # Option dollar move ≈ delta × (underlying_return_pct / 100 × underlying_price)
    # But we don't have underlying_price here; use:
    #   option_return_pct ≈ delta × underlying_return_pct × (1 / intrinsic_leverage)
    # Simplified: option_return_pct ≈ delta × underlying_return_pct (% of underlying)
    # This overstates for cheap OTM options; acceptable for analytics
    raw_option_return = delta * underlying_return_pct
    new_mid = round(original_mid * (1 + raw_option_return / 100), 4)
    return max(0.01, new_mid)  # options can't go below zero


def resolve_snapshot(
    snapshot: dict,
    run_resolution_types: tuple[str, ...] = ("1d", "5d", "10d"),
) -> list[dict]:
    """
    Compute outcome records for a single snapshot row.

    Args:
        snapshot: dict from option_candidate_snapshots (all columns)
        run_resolution_types: which windows to resolve

    Returns list of outcome dicts (one per resolution_type), ready for
    save_option_candidate_outcome().

    TRD-044: each outcome now carries target_projection_method and v2 hit
    markers (hit_v2_tp1/tp2/stop) so legacy and v2 accuracy can be compared.
    """
    ticker = snapshot.get("ticker", "")
    run_date_val = snapshot.get("run_date") or snapshot.get("created_at")
    mid = snapshot.get("mid")
    delta = snapshot.get("delta")
    opt_tp1 = snapshot.get("option_take_profit_1")
    opt_tp2 = snapshot.get("option_take_profit_2")
    opt_sl = snapshot.get("option_stop_loss")
    # V2 projected targets (from TRD-043)
    v2_tp1 = snapshot.get("projected_option_tp1")
    v2_tp2 = snapshot.get("projected_option_tp2")
    v2_stop = snapshot.get("projected_option_stop")
    target_projection_method = snapshot.get("target_projection_method")
    und_t1 = snapshot.get("underlying_target_1")
    und_t2 = snapshot.get("underlying_target_2")
    und_stop = snapshot.get("underlying_stop")
    holding_days = snapshot.get("holding_window_days") or 21

    if not ticker or not run_date_val:
        return []

    # Parse run_date as a date object
    try:
        if hasattr(run_date_val, "date"):
            rec_date = run_date_val.date()
        elif isinstance(run_date_val, date):
            rec_date = run_date_val
        else:
            rec_date = date.fromisoformat(str(run_date_val)[:10])
    except Exception:
        return []

    # Fetch enough price history to cover all windows
    start_str = rec_date.isoformat()
    end_date = rec_date + timedelta(days=max(holding_days + 14, 25))
    end_str = end_date.isoformat()

    hist = _fetch_history(ticker, start_str, end_str)

    # Get underlying price at rec_date (entry reference)
    entry_price = _close_on_date(hist, rec_date)
    if entry_price is None:
        log.debug("No entry price for %s on %s — skipping resolution", ticker, rec_date)
        return []

    # Compute prices at each window
    dates = {
        "1d":  rec_date + timedelta(days=1),
        "5d":  rec_date + timedelta(days=5),
        "10d": rec_date + timedelta(days=10),
    }
    prices = {k: _close_on_date(hist, v) for k, v in dates.items()}
    returns = {k: _pct(prices[k], entry_price) for k in prices}

    outcomes = []
    for rt in run_resolution_types:
        und_ret = returns.get(rt)
        und_px = prices.get(rt)

        # Approximate option price at this window
        new_opt_mid = _approx_option_price(und_ret, delta, mid)
        opt_ret = _pct(new_opt_mid, mid)

        # Hit markers based on underlying movement
        hit_t1 = None
        hit_t2 = None
        hit_stop = None
        if und_px is not None:
            direction = snapshot.get("direction", "BULL")
            if direction == "BULL":
                hit_t1 = (und_t1 is not None and und_px >= und_t1)
                hit_t2 = (und_t2 is not None and und_px >= und_t2)
                hit_stop = (und_stop is not None and und_px <= und_stop)
            elif direction == "BEAR":
                hit_t1 = (und_t1 is not None and und_px <= und_t1)
                hit_t2 = (und_t2 is not None and und_px <= und_t2)
                hit_stop = (und_stop is not None and und_px >= und_stop)

        # Legacy option target/stop hit markers (flat multiplier targets)
        hit_opt_tp1 = (new_opt_mid is not None and opt_tp1 is not None and new_opt_mid >= opt_tp1)
        hit_opt_tp2 = (new_opt_mid is not None and opt_tp2 is not None and new_opt_mid >= opt_tp2)
        hit_opt_stop = (new_opt_mid is not None and opt_sl is not None and new_opt_mid <= opt_sl)

        # V2 hit markers (projected delta-based targets — TRD-044)
        hit_v2_tp1: Optional[bool] = None
        hit_v2_tp2: Optional[bool] = None
        hit_v2_stop: Optional[bool] = None
        if new_opt_mid is not None:
            if v2_tp1 is not None:
                hit_v2_tp1 = new_opt_mid >= v2_tp1
            if v2_tp2 is not None:
                hit_v2_tp2 = new_opt_mid >= v2_tp2
            if v2_stop is not None:
                hit_v2_stop = new_opt_mid <= v2_stop

        hit_target = hit_t1 or hit_opt_tp1 or False

        # Exit reason (first triggered; legacy targets drive live exit decisions)
        exit_reason = None
        if hit_opt_tp2:
            exit_reason = "tp2"
        elif hit_opt_tp1:
            exit_reason = "tp1"
        elif hit_opt_stop:
            exit_reason = "stop"
        elif hit_stop:
            exit_reason = "underlying_stop"

        window_days = {"1d": 1, "5d": 5, "10d": 10}.get(rt, 1)
        outcome = {
            "underlying_close_1d":     prices.get("1d"),
            "underlying_close_5d":     prices.get("5d"),
            "underlying_close_10d":    prices.get("10d"),
            "underlying_return_1d_pct":  returns.get("1d"),
            "underlying_return_5d_pct":  returns.get("5d"),
            "underlying_return_10d_pct": returns.get("10d"),
        }
        # Add window-specific option fields
        opt_key = f"option_mid_{rt}"
        opt_ret_key = f"option_return_{rt}_pct"
        outcome[opt_key] = new_opt_mid
        outcome[opt_ret_key] = opt_ret
        outcome["days_held_to_exit"] = window_days
        outcome["exit_reason"] = exit_reason
        outcome["hit_option_tp1"] = hit_opt_tp1 if mid else None
        outcome["hit_option_tp2"] = hit_opt_tp2 if mid else None
        outcome["hit_option_stop"] = hit_opt_stop if mid else None
        outcome["hit_underlying_t1"] = hit_t1
        outcome["hit_underlying_t2"] = hit_t2
        outcome["hit_underlying_stop"] = hit_stop
        outcome["hit_target"] = hit_target
        # TRD-044: v2 comparator fields
        outcome["target_projection_method"] = target_projection_method
        outcome["hit_v2_tp1"]  = hit_v2_tp1
        outcome["hit_v2_tp2"]  = hit_v2_tp2
        outcome["hit_v2_stop"] = hit_v2_stop
        outcome["expired_itm"] = None   # Populated only at expiry resolution
        outcome["notes"] = f"Delta-approx resolution at {rt} window"

        outcomes.append({"resolution_type": rt, "outcome": outcome})

    return outcomes


def resolve_batch(
    snapshots: list[dict],
    resolution_types: tuple[str, ...] = ("1d", "5d", "10d"),
    persist: bool = True,
) -> dict[str, int]:
    """
    Resolve a batch of unresolved snapshots and optionally persist outcomes.

    Returns counts: {'resolved': N, 'failed': M, 'persisted': K}
    """
    from utils.supabase_persist import save_option_candidate_outcome

    counts = {"resolved": 0, "failed": 0, "persisted": 0}

    for snap in snapshots:
        snap_id = snap.get("id")
        try:
            outcomes = resolve_snapshot(snap, run_resolution_types=resolution_types)
            for item in outcomes:
                counts["resolved"] += 1
                if persist and snap_id:
                    ok = save_option_candidate_outcome(
                        snap_id,
                        item["resolution_type"],
                        item["outcome"],
                    )
                    if ok:
                        counts["persisted"] += 1
        except Exception as exc:
            log.warning("resolve_snapshot failed for id=%s: %s", snap_id, exc)
            counts["failed"] += 1

    return counts
