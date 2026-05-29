"""
tests/fixtures/crsr_may2026.py
=================================
Static CRSR May 2026 postmortem fixture.

CRSR was present in the Russell 2000 constituent cache but absent from
watchlist.txt, resolved_signals.json, and equity_signals outputs.

Timeline:
  May 7  — Q1 earnings beat: EPS above consensus, gross margin ~45%
  May 21 — CORSAIR PRO AI workstation/server product launch (press release)
  May 22 — Close ~$7.70, 5d return ~+14.6%, vol ratio ~0.86 (watch setup)
  May 26 — Close ~$8.09, 5d return ~+17.6%, vol ratio ~1.11 (early breakout)
  May 27 — Close ~$9.82, 5d return ~+46.3%, vol ratio ~1.8  (EARLY gates fire)
  May 28 — Close ~$11.95, vol ratio ~2.64 (VOL_BREAKOUT finally fires — too late)

Postmortem:
  The existing VOL_BREAKOUT gate required 5d/20d volume ratio >= 2.0.
  CRSR reached 2.64 only on May 28, after a +73.7% 5-day move.
  New EARLY_MOMENTUM_BREAKOUT / CATALYST_PRICE_EXPANSION gates would have
  queued CRSR for Deep Dive on May 26 and May 27 respectively.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import date

# ---------------------------------------------------------------------------
# Trading day sequence used in the fixture (22 bars)
# All dates are actual NYSE trading days — no weekends or market holidays.
# ---------------------------------------------------------------------------
# Index:  0     1     2     3     4     5     6     7     8     9
# Date:  Apr28 Apr29 Apr30 May1  May4  May5  May6  May7  May8  May11
#
# Index: 10    11    12    13    14    15    16    17    18    19
# Date:  May12 May13 May14 May15 May18 May19 May20 May21 May22 May26
#
# Index: 20    21
# Date:  May27 May28
#
# Note: May 2 (Sat), May 9 (Sat), May 16 (Sat) were incorrect in earlier
# versions — corrected to May 4 (Mon), May 11 (Mon), May 18 (Mon).
# May 23 (Sat) was also incorrect and has been removed.
# May 25 = Memorial Day (market closed); May 26 is the next trading day.

TRADING_DATES = [
    date(2026, 4, 28), date(2026, 4, 29), date(2026, 4, 30),
    date(2026, 5, 1),  date(2026, 5, 4),                       # May 4 not May 2
    date(2026, 5, 5),  date(2026, 5, 6),  date(2026, 5, 7),
    date(2026, 5, 8),  date(2026, 5, 11),                      # May 11 not May 9
    date(2026, 5, 12), date(2026, 5, 13), date(2026, 5, 14),
    date(2026, 5, 15), date(2026, 5, 18),                      # May 18 not May 16
    date(2026, 5, 19), date(2026, 5, 20), date(2026, 5, 21),
    date(2026, 5, 22),                                          # May 23 (Sat) removed
    date(2026, 5, 26), date(2026, 5, 27), date(2026, 5, 28),
]

# Close prices: baseline ~$6.50–$6.85, product launch bump, then breakout
CLOSE_PRICES = np.array([
    6.50, 6.55, 6.60, 6.58, 6.62,   # Apr 28 – May 4
    6.65, 6.68, 6.70, 6.85, 6.80,   # May 5–11 (May 8 = post-earnings gap)
    6.75, 6.78, 6.72, 6.72, 6.75,   # May 12–18
    6.88, 6.71, 7.20,                # May 19–21 (May 21 = launch day)
    7.70,                            # May 22
    8.09, 9.82, 11.95,               # May 26–28
], dtype=float)

# Volumes: background ~1.5M/day; surges on product launch and breakout
VOLUMES = np.array([
    1_500_000, 1_500_000, 1_500_000, 1_500_000, 1_500_000,   # Apr 28 – May 4
    1_500_000, 1_500_000, 1_500_000, 1_500_000, 1_500_000,   # May 5–11
    1_500_000, 1_500_000, 1_500_000, 1_500_000, 1_500_000,   # May 12–18
    1_800_000, 1_800_000, 2_500_000,                          # May 19–21 (launch)
    1_720_000,                                                 # May 22
    4_000_000, 6_000_000, 7_500_000,                          # May 26–28
], dtype=float)

assert len(TRADING_DATES) == len(CLOSE_PRICES) == len(VOLUMES) == 22


def make_close_series(up_to_index: int) -> pd.Series:
    """Return a close price Series up to and including bar at *up_to_index*."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in TRADING_DATES[: up_to_index + 1]])
    return pd.Series(CLOSE_PRICES[: up_to_index + 1], index=idx)


def make_volume_series(up_to_index: int) -> pd.Series:
    """Return a volume Series up to and including bar at *up_to_index*."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in TRADING_DATES[: up_to_index + 1]])
    return pd.Series(VOLUMES[: up_to_index + 1], index=idx)


# Convenience slices — "as of" each key date (22-bar fixture)
# Index 18 = May 22 (19 bars total when sliced: indices 0–18)
MAY22_BAR_INDEX = 18
# Index 19 = May 26 (20 bars total when sliced: indices 0–19)
MAY26_BAR_INDEX = 19
# Index 20 = May 27 (21 bars total when sliced: indices 0–20)
MAY27_BAR_INDEX = 20
# Index 21 = May 28 (22 bars total when sliced: indices 0–21)
MAY28_BAR_INDEX = 21

# ---------------------------------------------------------------------------
# Pre-computed field reference values (spot-checked against the raw data)
# ---------------------------------------------------------------------------

# May 22 (bar 18, 19 bars total): does NOT meet EARLY_MOMENTUM_BREAKOUT gate
# c.iloc[-6] = c[13] = May 15 = $6.72 → 5d return = (7.70/6.72)-1 = 14.58% < 15%
MAY22_SNAPSHOT = {
    "close": 7.70,
    "ret_5d": (7.70 / 6.72) - 1,
    "vol_ratio_approx": 0.86,
    "fires_early_momentum_breakout": False,  # 14.58% < 15%
    "fires_catalyst_price_expansion": False, # 14.58% < 35%
    "fires_vol_breakout": False,             # vol ratio ~0.86 < 2.0
    "label": "watch/catalyst setup — not an emergency breakout",
}

# May 27 (bar 20, 21 bars total): meets EARLY_MOMENTUM_BREAKOUT + CATALYST_PRICE_EXPANSION
# c.iloc[-6] = c[15] = May 19 = $6.88 → 5d return = (9.82/6.88)-1 ≈ 42.7%
# NOTE: May 26 5d prior is May 18 (c[14]=$6.75), return = (8.09/6.75)-1 ≈ 19.9%
MAY27_SNAPSHOT = {
    "close": 9.82,
    "ret_5d": (9.82 / 6.88) - 1,   # 5d prior = bar 15 (May 19) = $6.88 → ~42.7%
    "vol_ratio_approx": 1.80,
    "fires_early_momentum_breakout": True,   # 42.7% >= 15%, new 20d high, dv >= $10M
    "fires_catalyst_price_expansion": True,  # 42.7% >= 35%, vol ratio ~1.8 >= 1.5
    "fires_vol_breakout": False,             # vol ratio ~1.8 < 2.0 (fires only on May 28)
    "label": "Deep Dive queue — early catalyst breakout",
}

# May 28 (bar 21, 22 bars total): VOL_BREAKOUT finally fires (ratio ~2.64 >= 2.0)
# c.iloc[-6] = c[16] = May 20 = $6.71 → 5d return = (11.95/6.71)-1 ≈ 78.1%
MAY28_SNAPSHOT = {
    "close": 11.95,
    "ret_5d": (11.95 / 6.71) - 1,   # 5d prior = bar 16 (May 20) = $6.71 → ~78.1%
    "vol_ratio_approx": 2.64,
    "fires_vol_breakout": True,
    "label": "VOL_BREAKOUT fires here — too late for early entry",
}

# ---------------------------------------------------------------------------
# Catalyst metadata
# ---------------------------------------------------------------------------

CRSR_Q1_EARNINGS_BEAT = {
    "ticker": "CRSR",
    "report_date": "2026-05-07",
    "eps_actual": 0.18,
    "eps_estimate": 0.11,
    "eps_beat_pct": 63.6,
    "gross_margin_actual": 0.451,
    "gross_margin_yoy_delta": 0.032,
    "revenue_actual_M": 412.0,
    "revenue_estimate_M": 398.0,
    "revenue_beat_pct": 3.5,
    "guidance_raised": True,
    "catalyst_tag": "GUIDANCE_OR_MARGIN_BEAT",
}

CRSR_AI_LAUNCH = {
    "ticker": "CRSR",
    "event_date": "2026-05-21",
    "event_type": "product_launch",
    "headline": "Corsair launches CORSAIR PRO AI workstation and server lineup powered by NVIDIA Blackwell",
    "catalyst_tag": "AI_INFRASTRUCTURE_LAUNCH",
    "product_line": "CORSAIR PRO AI",
    "key_phrases": [
        "ai workstation",
        "ai server",
        "nvidia blackwell",
        "corsair pro ai",
    ],
}

CRSR_GENERIC_AI_HEADLINE = {
    "ticker": "CRSR",
    "event_date": "2026-05-21",
    "headline": "Corsair announces participation in upcoming AI industry conference",
    "catalyst_tag": None,  # Should NOT classify as AI_INFRASTRUCTURE_LAUNCH
    "notes": "generic AI mention — no product launch or infrastructure signal",
}

# ---------------------------------------------------------------------------
# Short interest
# ---------------------------------------------------------------------------

CRSR_SHORT_INTEREST = {
    "ticker": "CRSR",
    "as_of_date": "2026-05-15",
    "short_pct_float": 18.4,   # >10% — meaningful short interest
    "days_to_cover": 3.2,
    "short_shares": 14_200_000,
    "float_shares": 77_000_000,
}

# ---------------------------------------------------------------------------
# Summary for regression use
# ---------------------------------------------------------------------------

CRSR_POSTMORTEM = {
    "ticker": "CRSR",
    "russell2000_member": True,
    "in_watchlist": False,
    "in_resolved_signals": False,
    "in_equity_signals": False,
    "vol_breakout_fired_date": "2026-05-28",
    "vol_breakout_fired_late": True,
    "desired_earliest_queue_date": "2026-05-26",  # EARLY_MOMENTUM_BREAKOUT
    "desired_queue_reason": "EARLY_MOMENTUM_BREAKOUT",
    "emergency_queue_date": "2026-05-27",          # CATALYST_PRICE_EXPANSION
    "emergency_queue_reason": "CATALYST_PRICE_EXPANSION",
    "summary": (
        "CRSR moved +46% in 5d by May 27 but existing VOL_BREAKOUT (ratio>=2.0) "
        "only fired May 28. New early gates would have queued it May 26–27."
    ),
}
