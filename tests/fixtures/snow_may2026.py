"""
tests/fixtures/snow_may2026.py
================================
Static postmortem fixture for SNOW (Snowflake) — May 2026 earnings gap.

## What happened
SNOW reported earnings on May 28 2026 and gapped up significantly.  The engine
had it at rank 3 during May 25-28 and had partial catalyst evidence, but never
surfaced a clear pre-breakout watch/catalyst alert.  The system mostly treated
the upcoming earnings as risk (pre_earnings_hold override) rather than as the
thesis itself.

## What the engine missed
1. Serial earnings beats (SNOW beat estimates 4 consecutive quarters) → strong
   re-rating probability.
2. Improving relative strength through May 2026 (price drifting from $153 zone
   toward $170s while the thesis entry zone was stale at $147-$153).
3. Unusual call activity in the 10-14 days before earnings, but no alert fired.
4. Dark-pool ACCUMULATION signal on Apr 13 (z-score -1.60) was never refreshed
   or connected to the May earnings setup.

## Why short-squeeze logic was correctly low-confidence
- Short interest: ~5-6% of float  (squeeze threshold: typically ≥15%)
- Days-to-cover: ~2.3-2.5         (squeeze threshold: typically ≥5)
- Squeeze score: ~11.5 / 100
Any detector that relies on squeeze mechanics would not and should not fire here.
The new pre_earnings_breakout detector is intentionally independent of squeeze
thresholds.

## New rules this case should motivate
- pre_earnings_breakout flag in catalyst_screener when multiple independent
  conditions align (TRD-001).
- Thesis refresh when price moves above entry_high (TRD-002).
- WatchSetup / CatalystSetup alert combining dark-pool + options + earnings
  proximity, separate from Hot Entry (TRD-004).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Apr 13 2026 — first dark-pool accumulation signal
# ---------------------------------------------------------------------------
APR13_THESIS = {
    "ticker": "SNOW",
    "date": "2026-04-13",
    "direction": "BULL",
    "conviction": 2,
    "entry_low": 140.0,
    "entry_high": 146.0,
    "target_1": 158.0,
    "target_2": 170.0,
    "stop_loss": 133.0,
    "summary": "Dark-pool accumulation signal; conviction low pending earnings clarity.",
}

APR13_CATALYST_SCORES = {
    "ticker": "SNOW",
    "date": "2026-04-13",
    # component scores — all nonzero
    "squeeze_score": 1.0,       # low — not a squeeze candidate
    "volume_score": 3.0,        # moderate volume expansion
    "vol_compress": 2.0,        # Bollinger squeeze forming
    "options_score": 2.0,       # mild call activity
    "technical_score": 3.0,     # positive momentum
    "dark_pool_score": 2.0,     # ACCUMULATION signal (z-score ~ -1.60)
    "dark_pool_signal": "ACCUMULATION",
    "earnings_score": 1.0,      # earnings ~45d out
    "analyst_score": 1.0,
    "n_flags": 5,
    "price": 143.5,
    "short_pct": 5.8,
    # composite correctly nonzero on this date
    "composite": 35.2,
    "post_squeeze_guard": False,
    "raw_composite": 35.2,
}

APR13_DARK_POOL = {
    "ticker": "SNOW",
    "date": "2026-04-13",
    "signal": "ACCUMULATION",
    "short_ratio_zscore": -1.60,
    "detail": "FINRA short ratio z-score -1.60 — institutional accumulation signal",
}

# ---------------------------------------------------------------------------
# May 15 2026 — stale NEUTRAL thesis, entry zone about to be invalidated
# ---------------------------------------------------------------------------
MAY15_THESIS = {
    "ticker": "SNOW",
    "date": "2026-05-15",
    "direction": "NEUTRAL",
    "conviction": 2,
    "entry_low": 147.0,
    "entry_high": 153.0,
    "target_1": 165.0,
    "target_2": 178.0,
    "stop_loss": 140.0,
    "summary": (
        "Neutral stance; earnings risk noted for May 27. "
        "Entry zone $147-$153 may already be stale if price continues drifting up."
    ),
}

MAY15_CATALYST_SCORES = {
    "ticker": "SNOW",
    "date": "2026-05-15",
    "squeeze_score": 1.0,
    "volume_score": 3.0,
    "vol_compress": 1.0,
    "options_score": 2.0,
    "technical_score": 3.0,
    "dark_pool_score": 1.0,
    "dark_pool_signal": "NEUTRAL",
    "earnings_score": 3.0,      # earnings now 12d out
    "analyst_score": 2.0,
    "n_flags": 6,
    "price": 158.0,             # already above entry_high of $153
    "short_pct": 5.4,
    "composite": 34.8,
    "post_squeeze_guard": False,
    "raw_composite": 34.8,
}

# ---------------------------------------------------------------------------
# May 25-28 2026 — pre-earnings window, composite incorrectly 0.0
# ---------------------------------------------------------------------------
MAY25_RANKING = {
    "ticker": "SNOW",
    "date": "2026-05-25",
    "rank": 3,
    "prob_t1": 0.52,
    "prob_t2": 0.28,
    "t1_price": 175.0,
    "t2_price": 190.0,
    "weight": 0.07,
    "direction": "BULL",
}

MAY25_CATALYST_SCORES = {
    "ticker": "SNOW",
    "date": "2026-05-25",
    # All component scores are NONZERO — clear setup evidence
    "squeeze_score": 1.0,       # intentionally low — not a squeeze
    "volume_score": 4.0,        # volume expansion ahead of earnings
    "vol_compress": 2.0,
    "options_score": 4.0,       # unusual call activity
    "technical_score": 4.0,     # strong momentum, above all MAs
    "dark_pool_score": 1.0,
    "dark_pool_signal": "NEUTRAL",
    "earnings_score": 5.0,      # earnings in 2d — imminent
    "analyst_score": 2.0,       # 1 upgrade in 7d
    "n_flags": 9,
    "price": 168.5,
    "short_pct": 5.1,
    # BUG: composite persisted as 0.0 despite nonzero components
    # This was the core TRD-003 issue.
    "composite": 0.0,
    "post_squeeze_guard": False,  # post_squeeze_guard was NOT the cause
    "raw_composite": 51.4,        # what it should have been
}

# The May 27-28 rows follow the same pattern (rank 3, stale thesis, composite=0.0)
MAY27_CATALYST_SCORES = {**MAY25_CATALYST_SCORES, "date": "2026-05-27", "price": 171.0}
MAY28_CATALYST_SCORES = {**MAY25_CATALYST_SCORES, "date": "2026-05-28", "price": 174.5}

# ---------------------------------------------------------------------------
# Pre-earnings breakout detector inputs (as the engine would have seen them)
# ---------------------------------------------------------------------------
SNOW_PRE_EARNINGS_INPUTS = {
    "ticker": "SNOW",
    "days_to_earnings": 2,
    "earnings_beat_streak": 4,          # beat 4 consecutive quarters
    "earnings_surprise_avg_pct": 8.5,   # avg beat magnitude
    "momentum_1m_pct": 9.2,             # +9.2% in last month
    "volume_score": 4.0,                # from score_volume_breakout
    "options_score": 4.0,               # from score_options_activity
    "dark_pool_signal": "NEUTRAL",      # weakened since Apr 13
    "short_pct_float": 5.1,             # low — NOT a squeeze
    "days_to_cover": 2.4,               # low — NOT a squeeze
    "squeeze_score_100": 11.5,          # well below squeeze threshold
}

# Expected output from the new detector for this input:
SNOW_EXPECTED_BREAKOUT_FLAG = {
    "pre_earnings_breakout": True,
    "confidence": "medium",             # not "high" — one condition missing
    "reasons": [
        "earnings_beat_streak_4",
        "momentum_1m_above_threshold",
        "volume_expansion_confirmed",
        "call_demand_elevated",
    ],
    "not_a_squeeze": True,
}

# ---------------------------------------------------------------------------
# Refresh trigger: what should have queued SNOW for re-analysis after May 15
# ---------------------------------------------------------------------------
SNOW_REFRESH_TRIGGER_CONTEXT = {
    "ticker": "SNOW",
    "thesis_date": "2026-05-15",
    "entry_high": 153.0,
    "current_price": 168.5,             # > entry_high * 1.05
    "current_rank": 3,                  # top-5 — qualifies for ranking trigger too
    "days_to_earnings": 12,             # within the near-earnings window
    "thesis_direction": "NEUTRAL",      # stale neutral thesis at rank 3
    # Expected: should_refresh returns True with reason
    "expected_reason": "price_above_entry_zone",  # primary trigger
}
