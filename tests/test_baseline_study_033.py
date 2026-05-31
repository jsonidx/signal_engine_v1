"""
tests/test_baseline_study_033.py — TRD-033 unit tests

Tests the analysis logic in scripts/baseline_study_033.py
without requiring a live database or network calls.
"""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.baseline_study_033 import (
    direction_adjusted_return,
    is_true_positive,
    nth_trading_day,
    trading_days_calendar,
)


# ── trading_days_calendar ────────────────────────────────────────────────────

def test_trading_days_calendar_excludes_weekends():
    days = trading_days_calendar(date(2026, 4, 6), date(2026, 4, 10))
    # 2026-04-06 is Mon, 2026-04-10 is Fri; no weekend days
    assert date(2026, 4, 7) in days   # Tuesday
    assert date(2026, 4, 11) not in days  # Saturday
    assert date(2026, 4, 12) not in days  # Sunday


def test_trading_days_calendar_count():
    # Mon-Fri week: 5 trading days
    days = trading_days_calendar(date(2026, 4, 6), date(2026, 4, 10))
    assert len(days) == 5


# ── nth_trading_day ───────────────────────────────────────────────────────────

def test_nth_trading_day_basic():
    tdays = [date(2026, 4, 6), date(2026, 4, 7), date(2026, 4, 8),
             date(2026, 4, 9), date(2026, 4, 10), date(2026, 4, 13)]
    assert nth_trading_day(date(2026, 4, 6), 1, tdays) == date(2026, 4, 7)
    assert nth_trading_day(date(2026, 4, 6), 5, tdays) == date(2026, 4, 13)


def test_nth_trading_day_past_end_returns_none():
    tdays = [date(2026, 4, 6), date(2026, 4, 7)]
    assert nth_trading_day(date(2026, 4, 6), 5, tdays) is None


def test_nth_trading_day_date_not_in_calendar_returns_none():
    tdays = [date(2026, 4, 6), date(2026, 4, 7)]
    # 2026-04-05 is a Sunday — not in calendar
    assert nth_trading_day(date(2026, 4, 5), 1, tdays) is None


# ── direction_adjusted_return ─────────────────────────────────────────────────

def test_direction_adjusted_return_bull():
    assert direction_adjusted_return(5.0, "BULL") == pytest.approx(5.0)


def test_direction_adjusted_return_bear():
    # BEAR: a negative raw return is good
    assert direction_adjusted_return(-6.0, "BEAR") == pytest.approx(6.0)


def test_direction_adjusted_return_neutral():
    assert direction_adjusted_return(3.0, "NEUTRAL") == pytest.approx(3.0)


# ── is_true_positive ──────────────────────────────────────────────────────────

def test_is_true_positive_bull_above_threshold():
    assert is_true_positive(6.0, "BULL", threshold=5.0) is True


def test_is_true_positive_bull_below_threshold():
    assert is_true_positive(3.0, "BULL", threshold=5.0) is False


def test_is_true_positive_bear_correct_direction():
    # BEAR alert: raw return -7% → direction_adj = +7% → above 5% threshold
    assert is_true_positive(-7.0, "BEAR", threshold=5.0) is True


def test_is_true_positive_bear_wrong_direction():
    # BEAR alert: raw return +5% → direction_adj = -5% → below threshold
    assert is_true_positive(5.0, "BEAR", threshold=5.0) is False


def test_is_true_positive_neutral_returns_none():
    assert is_true_positive(10.0, "NEUTRAL") is None


def test_is_true_positive_none_ret_returns_none():
    assert is_true_positive(None, "BULL") is None


def test_is_true_positive_nan_ret_returns_none():
    assert is_true_positive(float("nan"), "BULL") is None
