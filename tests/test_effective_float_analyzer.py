"""
Unit tests for CHUNK-06: effective_float_analyzer.py

Tests cover:
  - No-record neutral return
  - pct_class aggregation from records
  - pct_class back-fill from shares_beneficially_owned / shares_outstanding
  - Amendment deduplication (latest filing per holder wins)
  - 10% float floor prevents zero effective float
  - effective_short_float_ratio computation
  - compute_effective_float_score threshold table
  - Derivative exposure flag surfaced but not double-counted
  - Squeeze screener integration: neutral without filings
  - Squeeze screener integration: CAR-like concentration flags
  - Anti-lookahead: fetch_filing_catalysts filters future filings (mocked)

All tests are pure-function — no live DB or EDGAR calls.
"""

import pytest
from unittest.mock import patch
from datetime import date

import pandas as pd

from effective_float_analyzer import (
    analyze_effective_float,
    normalize_large_holder_records,
    compute_large_holder_ownership_pct,
    estimate_effective_float,
    compute_effective_short_float_ratio,
    compute_effective_float_score,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _holder(
    holder_name="Pentwater Capital",
    pct_class=22.2,
    shares_beneficially_owned=None,
    filing_date="2024-06-15",
    filing_type="SC 13G",
    ownership_accumulation_flag=True,
    large_holder_flag=True,
    derivative_exposure_flag=False,
    accession_number="0001234567-24-001234",
):
    return {
        "ticker": "CAR",
        "holder_name": holder_name,
        "pct_class": pct_class,
        "shares_beneficially_owned": shares_beneficially_owned,
        "filing_date": filing_date,
        "filing_type": filing_type,
        "ownership_accumulation_flag": ownership_accumulation_flag,
        "large_holder_flag": large_holder_flag,
        "derivative_exposure_flag": derivative_exposure_flag,
        "accession_number": accession_number,
        "source_url": "https://efts.sec.gov/LATEST/search-index?q=%220001234567-24-001234%22",
    }


# ── Pure analyzer tests ───────────────────────────────────────────────────────

class TestNoHolderRecords:

    def test_returns_neutral_effective_float(self):
        result = analyze_effective_float(
            ticker="CAR",
            reported_float=10_000_000,
            shares_outstanding=15_000_000,
            large_holder_records=[],
        )
        assert result["effective_float_estimate"] == pytest.approx(10_000_000)
        assert result["extreme_float_lock_flag"] is False
        assert result["large_holder_concentration_flag"] is False
        assert result["large_holder_ownership_pct"] == pytest.approx(0.0)
        assert result["effective_float_confidence"] in ("unknown", "low")

    def test_no_crash_on_none_float(self):
        result = analyze_effective_float(
            ticker="CAR",
            reported_float=None,
            shares_outstanding=None,
            large_holder_records=[],
        )
        assert result["extreme_float_lock_flag"] is False
        assert result["effective_float_estimate"] == pytest.approx(0.0)


class TestLargeHolderOwnershipFromPctClass:

    def test_computes_ownership_from_two_holders(self):
        records = [
            _holder("Holder A", pct_class=22.2),
            _holder("Holder B", pct_class=30.0, accession_number="0001234567-24-002222"),
        ]
        result = analyze_effective_float(
            ticker="CAR",
            reported_float=10_000_000,
            shares_outstanding=15_000_000,
            large_holder_records=records,
        )
        assert result["large_holder_ownership_pct"] == pytest.approx(52.2)
        assert result["extreme_float_lock_flag"] is True  # >= 50%

    def test_concentration_flag_at_30pct(self):
        records = [_holder("Holder A", pct_class=30.0)]
        result = analyze_effective_float(
            ticker="TEST",
            reported_float=10_000_000,
            shares_outstanding=15_000_000,
            large_holder_records=records,
        )
        assert result["large_holder_concentration_flag"] is True
        assert result["extreme_float_lock_flag"] is False  # 30% < 50%


class TestPctClassBackfillFromShares:

    def test_computes_pct_class_from_shares_when_missing(self):
        records = [_holder(
            holder_name="Holder X",
            pct_class=None,
            shares_beneficially_owned=5_000_000,
        )]
        normalised = normalize_large_holder_records(records, shares_outstanding=25_000_000)
        assert len(normalised) == 1
        assert normalised[0]["pct_class"] == pytest.approx(20.0)

    def test_backfill_pct_used_in_aggregation(self):
        records = [_holder(
            holder_name="Holder X",
            pct_class=None,
            shares_beneficially_owned=5_000_000,
        )]
        result = analyze_effective_float(
            ticker="TEST",
            reported_float=20_000_000,
            shares_outstanding=25_000_000,
            large_holder_records=records,
        )
        assert result["large_holder_ownership_pct"] == pytest.approx(20.0)


class TestDeduplicatesHolderAmendments:

    def test_only_latest_filing_counts(self):
        # Same holder, two filings — older reports 10%, newer reports 22.2%
        records = [
            _holder("Pentwater Capital", pct_class=10.0, filing_date="2024-01-15",
                    accession_number="0001234567-24-000001"),
            _holder("Pentwater Capital", pct_class=22.2, filing_date="2024-06-15",
                    accession_number="0001234567-24-001234"),
        ]
        result = analyze_effective_float(
            ticker="CAR",
            reported_float=100_000_000,
            shares_outstanding=150_000_000,
            large_holder_records=records,
        )
        # Only the 22.2% (latest) should count — not 32.2%
        assert result["large_holder_ownership_pct"] == pytest.approx(22.2)
        assert result["record_count"] == 1

    def test_different_holders_both_count(self):
        records = [
            _holder("Holder A", pct_class=10.0, accession_number="AAAA"),
            _holder("Holder B", pct_class=15.0, accession_number="BBBB"),
        ]
        result = analyze_effective_float(
            ticker="TEST",
            reported_float=100_000_000,
            shares_outstanding=150_000_000,
            large_holder_records=records,
        )
        assert result["large_holder_ownership_pct"] == pytest.approx(25.0)
        assert result["record_count"] == 2


class TestEffectiveFloatFloor:

    def test_floor_prevents_zero(self):
        # large_holder_shares > reported_float → locked = reported_float → raw effective = 0 → floor applied
        result = estimate_effective_float(
            reported_float=10_000_000,
            large_holder_shares=20_000_000,    # > float → capped at 10M
            large_holder_ownership_pct=200.0,
        )
        assert result["effective_float_estimate"] == pytest.approx(1_000_000)   # 10% floor
        assert result["float_floor_applied"] is True
        assert result["extreme_float_lock_flag"] is True

    def test_normal_case_no_floor(self):
        result = estimate_effective_float(
            reported_float=10_000_000,
            large_holder_shares=2_000_000,
            large_holder_ownership_pct=20.0,
        )
        assert result["effective_float_estimate"] == pytest.approx(8_000_000)
        assert result["float_floor_applied"] is False


class TestEffectiveShortFloatRatio:

    def test_ratio_computed_correctly(self):
        ratio = compute_effective_short_float_ratio(
            shares_short=8_000_000,
            effective_float_estimate=2_000_000,
        )
        assert ratio == pytest.approx(4.0)

    def test_returns_zero_on_missing_short(self):
        assert compute_effective_short_float_ratio(None, 2_000_000) == pytest.approx(0.0)

    def test_returns_zero_on_zero_float(self):
        assert compute_effective_short_float_ratio(8_000_000, 0.0) == pytest.approx(0.0)


class TestEffectiveFloatScoreThresholds:

    @pytest.mark.parametrize("ratio,expected", [
        (1.00, 10.0),
        (1.50, 10.0),
        (0.75, 9.0),
        (0.99, 9.0),
        (0.50, 8.0),
        (0.74, 8.0),
        (0.35, 6.0),
        (0.49, 6.0),
        (0.20, 4.0),
        (0.34, 4.0),
        (0.10, 2.0),
        (0.19, 2.0),
        (0.05, 0.0),
        (0.00, 0.0),
    ])
    def test_score_threshold(self, ratio, expected):
        assert compute_effective_float_score(ratio) == pytest.approx(expected)


class TestDerivativeExposureNotDoubleCount:

    def test_derivative_flag_surfaced(self):
        records = [_holder(
            "Derivative Holder",
            pct_class=15.0,
            derivative_exposure_flag=True,
        )]
        result = analyze_effective_float(
            ticker="TEST",
            reported_float=100_000_000,
            shares_outstanding=150_000_000,
            large_holder_records=records,
        )
        # Flag is surfaced
        assert result["derivative_exposure_present"] is True

    def test_derivative_shares_not_double_counted(self):
        # One holder with derivative_exposure_flag and explicit shares_beneficially_owned.
        # The shares_beneficially_owned should be used as-is; derivative flag should
        # not add additional shares on top.
        records = [_holder(
            "Derivative Holder",
            pct_class=None,
            shares_beneficially_owned=5_000_000,
            derivative_exposure_flag=True,
        )]
        normalised = normalize_large_holder_records(records, shares_outstanding=50_000_000)
        ownership = compute_large_holder_ownership_pct(normalised, reported_float=50_000_000)
        # Should be 5M/50M = 10% — not doubled
        assert ownership["large_holder_ownership_pct"] == pytest.approx(10.0)
        assert ownership["large_holder_shares"] == pytest.approx(5_000_000)


# ── Squeeze screener integration tests ───────────────────────────────────────

def _make_hist(price, n=60):
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Close": [float(price)] * n, "Volume": [2_000_000] * n},
        index=dates,
    )


def _base_data(**overrides):
    base = {
        "ticker": "CAR",
        "price": 40.0,
        "market_cap": 3_000_000_000,
        "float_shares": 10_100_000,
        "shares_outstanding": 15_000_000,
        "short_pct_float": 0.30,
        "short_ratio_dtc": 3.0,
        "volume_avg_30d": 500_000,
        "volume_avg_5d": 500_000,
        "avg_price_60d": 40.0,
        "history": _make_hist(40.0),
        "info": {},
    }
    base.update(overrides)
    return base


class TestEffectiveFloatIntegration:

    def test_neutral_without_filings(self):
        """Screener must not crash and effective_float_score stays neutral/low."""
        from squeeze_screener import compute_squeeze_score

        data = _base_data()
        with patch("squeeze_screener._load_filing_catalysts", return_value=[]):
            with patch("squeeze_screener._load_si_history", return_value=[]):
                sq = compute_squeeze_score("CAR", data, {}, pd.DataFrame(),
                                           si_history=[], filing_catalysts=[])

        assert sq.final_score >= 0.0
        # With no holder records, effective_float = reported_float → ratio = SI% → low/neutral score
        assert sq.effective_float_score <= 5.0
        assert sq.extreme_float_lock_flag is False
        assert sq.effective_float_confidence == "unknown"

    def test_flags_car_like_concentration(self):
        """High large-holder ownership + 80% SI → extreme flag and elevated score."""
        from squeeze_screener import compute_squeeze_score

        records = [
            _holder("Holder A", pct_class=30.0, accession_number="AAAA"),
            _holder("Holder B", pct_class=25.0, accession_number="BBBB"),
        ]  # 55% total → extreme_float_lock_flag

        data = _base_data(
            float_shares=10_100_000,
            shares_outstanding=15_000_000,
            short_pct_float=0.80,
        )

        sq = compute_squeeze_score("CAR", data, {}, pd.DataFrame(),
                                   si_history=[], filing_catalysts=records)

        assert sq.extreme_float_lock_flag is True
        assert sq.effective_short_float_ratio > 1.0   # shorts exceed effective float
        assert sq.effective_float_score >= 8.0         # high score


# ── Anti-lookahead: fetch_filing_catalysts filters future filings ─────────────

class TestFetchFilingCatalystsFutureFilter:

    def test_filters_future_filings(self):
        """
        fetch_filing_catalysts must exclude rows with filing_date > as_of_date.
        We test via the supabase_persist helper mocking the DB cursor.
        """
        from unittest.mock import MagicMock, patch

        past_row = {
            "ticker": "CAR", "filing_date": date(2024, 3, 1),
            "filing_type": "SC 13G", "ownership_accumulation_flag": True,
            "large_holder_flag": True, "pct_class": 22.2,
            "shares_beneficially_owned": 7_824_100, "derivative_exposure_flag": False,
            "holder_name": "Pentwater Capital", "accession_number": "0001234567-24-001234",
            "source_url": None, "source": "edgar_search",
            "event_date": None, "issuer": None, "summary": None, "shares_offered": None,
        }

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [past_row]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("utils.supabase_persist._conn", return_value=mock_conn):
            from utils.supabase_persist import fetch_filing_catalysts
            results = fetch_filing_catalysts("CAR", as_of_date=date(2024, 6, 1))

        # The SQL query is parameterised; verify as_of_date was passed correctly
        call_args = mock_cur.execute.call_args
        query_params = call_args[0][1]  # positional args tuple: (query, params)
        assert query_params[1] == "2024-06-01"   # cutoff passed to SQL

        # Results are the rows returned by the mock cursor
        assert len(results) == 1
        assert results[0]["ticker"] == "CAR"
