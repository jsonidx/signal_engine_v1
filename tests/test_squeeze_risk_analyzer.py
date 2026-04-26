"""
Unit tests for CHUNK-16: squeeze_risk_analyzer.py

Tests cover:
  1.  Low risk with no flags → LOW, risk_score < 25
  2.  Dilution risk flag → at least MEDIUM, DILUTION_RISK flag
  3.  Large offering (shares_offered_pct_float >= 0.20) → LARGE_OFFERING_RISK
  4.  Recent dilution filing within 14 days → RECENT_DILUTION_FILING
  5.  Completed squeeze → COMPLETED_SQUEEZE_RISK
  6.  ACTIVE state + low SI (< 20%) → LOW_REMAINING_SHORT_PRESSURE
  7.  ACTIVE state + low DTC (< 2) → LOW_DTC_AFTER_MOVE
  8.  Unknown effective_float_confidence → LOW_EFFECTIVE_FLOAT_CONFIDENCE
  9.  Risk score clamped to 100
  10. Risk level thresholds: LOW/MEDIUM/HIGH/EXTREME boundaries
  11. extract_dilution_info: no records → neutral result
  12. extract_dilution_info: dilution record → correct fields
  13. extract_dilution_info: shares_offered_pct_float computation
  14. extract_dilution_info: days_since_filing computation
  15. Price extension risk thresholds

All tests are pure-function — no live DB or network calls.
"""

from datetime import date, timedelta

import pytest

from squeeze_risk_analyzer import (
    compute_squeeze_risk_score,
    extract_dilution_info,
    risk_level_from_score,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _risk(**kwargs) -> dict:
    """Call compute_squeeze_risk_score with safe defaults + overrides."""
    defaults = dict(
        squeeze_state="ARMED",
        final_score=60.0,
        short_pct_float=0.35,
        computed_dtc_30d=5.0,
        volume_confirmation_flag=False,
        recent_squeeze_state="false",
        dilution_risk_flag=False,
        derivative_exposure_flag=False,
        effective_float_confidence="high",
        shares_offered_pct_float=None,
        latest_dilution_filing_date=None,
        as_of_date=date(2024, 6, 1),
        si_persistence_count=3,
        price_extension_pct=None,
        price_reversal_flag=False,
    )
    defaults.update(kwargs)
    return compute_squeeze_risk_score(**defaults)


# ── Test 1: Low risk with no flags ────────────────────────────────────────────

class TestLowRiskNoFlags:

    def test_no_risk_inputs_gives_low_level(self):
        result = _risk()
        assert result["risk_level"] == "LOW"
        assert result["risk_score"] < 25.0

    def test_no_risk_flags_empty(self):
        result = _risk(effective_float_confidence="high", si_persistence_count=5)
        risk_flags = result["risk_flags"]
        assert "DILUTION_RISK" not in risk_flags
        assert "COMPLETED_SQUEEZE_RISK" not in risk_flags

    def test_low_risk_returns_all_required_keys(self):
        result = _risk()
        assert "risk_score" in result
        assert "risk_level" in result
        assert "risk_flags" in result
        assert "risk_warnings" in result
        assert "risk_components" in result


# ── Test 2: Dilution risk flag → at least MEDIUM ─────────────────────────────

class TestDilutionRiskFlag:

    def test_dilution_flag_true_adds_risk(self):
        result = _risk(dilution_risk_flag=True)
        assert result["risk_score"] >= 25.0
        assert result["risk_level"] in ("MEDIUM", "HIGH", "EXTREME")

    def test_dilution_flag_sets_dilution_risk_flag(self):
        result = _risk(dilution_risk_flag=True)
        assert "DILUTION_RISK" in result["risk_flags"]

    def test_dilution_warning_text_present(self):
        result = _risk(dilution_risk_flag=True)
        assert any("dilution" in w.lower() or "filing" in w.lower()
                   for w in result["risk_warnings"])


# ── Test 3: Large offering → LARGE_OFFERING_RISK ─────────────────────────────

class TestLargeOfferingRisk:

    def test_large_offering_at_20pct(self):
        result = _risk(dilution_risk_flag=True, shares_offered_pct_float=0.20)
        assert "LARGE_OFFERING_RISK" in result["risk_flags"]
        # Base + large offering
        assert result["risk_score"] >= 55.0  # 35 + 20 = 55 → HIGH

    def test_mid_offering_no_large_risk_flag(self):
        result = _risk(dilution_risk_flag=True, shares_offered_pct_float=0.15)
        assert "LARGE_OFFERING_RISK" not in result["risk_flags"]
        # Still elevated risk from base + mid offering
        assert result["risk_score"] >= 35.0

    def test_offering_below_threshold_no_flag(self):
        result = _risk(dilution_risk_flag=True, shares_offered_pct_float=0.05)
        assert "LARGE_OFFERING_RISK" not in result["risk_flags"]


# ── Test 4: Recent dilution filing within 14 days ─────────────────────────────

class TestRecentDilutionFiling:

    def test_filing_within_14_days_adds_flag(self):
        as_of = date(2024, 6, 1)
        filing_date = as_of - timedelta(days=7)
        result = _risk(
            dilution_risk_flag=True,
            latest_dilution_filing_date=filing_date,
            as_of_date=as_of,
        )
        assert "RECENT_DILUTION_FILING" in result["risk_flags"]

    def test_filing_older_than_14_days_no_flag(self):
        as_of = date(2024, 6, 1)
        filing_date = as_of - timedelta(days=20)
        result = _risk(
            dilution_risk_flag=True,
            latest_dilution_filing_date=filing_date,
            as_of_date=as_of,
        )
        assert "RECENT_DILUTION_FILING" not in result["risk_flags"]

    def test_filing_exactly_14_days_adds_flag(self):
        as_of = date(2024, 6, 1)
        filing_date = as_of - timedelta(days=14)
        result = _risk(
            dilution_risk_flag=True,
            latest_dilution_filing_date=filing_date,
            as_of_date=as_of,
        )
        assert "RECENT_DILUTION_FILING" in result["risk_flags"]


# ── Test 5: Completed squeeze → COMPLETED_SQUEEZE_RISK ───────────────────────

class TestCompletedSqueezeRisk:

    def test_completed_squeeze_adds_risk(self):
        result = _risk(recent_squeeze_state="completed")
        assert "COMPLETED_SQUEEZE_RISK" in result["risk_flags"]
        assert result["risk_score"] >= 30.0

    def test_completed_squeeze_warning_present(self):
        result = _risk(recent_squeeze_state="completed")
        assert any("completed" in w.lower() for w in result["risk_warnings"])


# ── Test 6: ACTIVE + low SI → LOW_REMAINING_SHORT_PRESSURE ───────────────────

class TestActiveLowSI:

    def test_active_with_low_si_adds_flag(self):
        result = _risk(squeeze_state="ACTIVE", short_pct_float=0.15)
        assert "LOW_REMAINING_SHORT_PRESSURE" in result["risk_flags"]

    def test_active_with_adequate_si_no_flag(self):
        result = _risk(squeeze_state="ACTIVE", short_pct_float=0.30)
        assert "LOW_REMAINING_SHORT_PRESSURE" not in result["risk_flags"]

    def test_armed_with_low_si_no_exhaustion_flag(self):
        # LOW_REMAINING_SHORT_PRESSURE only triggers when ACTIVE
        result = _risk(squeeze_state="ARMED", short_pct_float=0.10)
        assert "LOW_REMAINING_SHORT_PRESSURE" not in result["risk_flags"]


# ── Test 7: ACTIVE + low DTC → LOW_DTC_AFTER_MOVE ────────────────────────────

class TestActiveLowDTC:

    def test_active_low_dtc_adds_flag(self):
        result = _risk(squeeze_state="ACTIVE", computed_dtc_30d=1.5)
        assert "LOW_DTC_AFTER_MOVE" in result["risk_flags"]

    def test_active_adequate_dtc_no_flag(self):
        result = _risk(squeeze_state="ACTIVE", computed_dtc_30d=5.0)
        assert "LOW_DTC_AFTER_MOVE" not in result["risk_flags"]

    def test_not_setup_low_dtc_no_exhaustion_flag(self):
        result = _risk(squeeze_state="NOT_SETUP", computed_dtc_30d=1.0)
        assert "LOW_DTC_AFTER_MOVE" not in result["risk_flags"]


# ── Test 8: Unknown effective float confidence is data-quality warning ─────────

class TestUnknownEffectiveFloatConfidence:

    def test_unknown_confidence_adds_flag(self):
        result = _risk(effective_float_confidence="unknown")
        assert "LOW_EFFECTIVE_FLOAT_CONFIDENCE" in result["risk_flags"]

    def test_low_confidence_adds_flag(self):
        result = _risk(effective_float_confidence="low")
        assert "LOW_EFFECTIVE_FLOAT_CONFIDENCE" in result["risk_flags"]

    def test_unknown_confidence_is_small_risk(self):
        result = _risk(effective_float_confidence="unknown")
        # Should be a data-quality note, not a major risk penalty
        assert result["risk_components"]["data_quality"] <= 10

    def test_high_confidence_no_flag(self):
        result = _risk(effective_float_confidence="high")
        assert "LOW_EFFECTIVE_FLOAT_CONFIDENCE" not in result["risk_flags"]


# ── Test 9: Risk score clamped to 100 ─────────────────────────────────────────

class TestRiskScoreClamped:

    def test_multiple_large_components_clamped_to_100(self):
        result = _risk(
            dilution_risk_flag=True,
            shares_offered_pct_float=0.25,
            latest_dilution_filing_date=date(2024, 5, 28),
            as_of_date=date(2024, 6, 1),
            recent_squeeze_state="completed",
            squeeze_state="ACTIVE",
            short_pct_float=0.10,
            computed_dtc_30d=1.0,
            effective_float_confidence="unknown",
            si_persistence_count=0,
        )
        assert result["risk_score"] <= 100.0
        assert result["risk_level"] == "EXTREME"


# ── Test 10: Risk level thresholds ────────────────────────────────────────────

class TestRiskLevelThresholds:

    @pytest.mark.parametrize("score,expected", [
        (0.0, "LOW"),
        (24.9, "LOW"),
        (25.0, "MEDIUM"),
        (49.9, "MEDIUM"),
        (50.0, "HIGH"),
        (74.9, "HIGH"),
        (75.0, "EXTREME"),
        (100.0, "EXTREME"),
    ])
    def test_threshold_mapping(self, score, expected):
        assert risk_level_from_score(score) == expected


# ── Test 11: extract_dilution_info — no records ───────────────────────────────

class TestExtractDilutionInfoNoRecords:

    def test_empty_records_returns_neutral(self):
        result = extract_dilution_info([], float_shares=10_000_000)
        assert result["dilution_risk_flag"] is False
        assert result["shares_offered"] is None
        assert result["shares_offered_pct_float"] is None
        assert result["latest_dilution_filing_date"] is None

    def test_records_without_dilution_flag_returns_neutral(self):
        records = [{"filing_type": "SC 13G", "dilution_risk_flag": False}]
        result = extract_dilution_info(records, float_shares=10_000_000)
        assert result["dilution_risk_flag"] is False


# ── Test 12: extract_dilution_info — with dilution record ─────────────────────

class TestExtractDilutionInfoWithRecord:

    def test_dilution_record_sets_flag(self):
        records = [{
            "filing_type": "424B5",
            "dilution_risk_flag": True,
            "filing_date": "2024-05-15",
            "shares_offered": 1_000_000,
        }]
        result = extract_dilution_info(records, float_shares=10_000_000)
        assert result["dilution_risk_flag"] is True

    def test_latest_date_is_most_recent(self):
        records = [
            {"dilution_risk_flag": True, "filing_date": "2024-03-01", "shares_offered": None},
            {"dilution_risk_flag": True, "filing_date": "2024-05-15", "shares_offered": None},
        ]
        result = extract_dilution_info(records)
        from datetime import date as _date
        assert result["latest_dilution_filing_date"] == _date(2024, 5, 15)


# ── Test 13: extract_dilution_info — shares_offered_pct_float ─────────────────

class TestSharesOfferedPctFloat:

    def test_shares_pct_computed_correctly(self):
        records = [{
            "dilution_risk_flag": True,
            "filing_date": "2024-05-15",
            "shares_offered": 2_000_000,
        }]
        result = extract_dilution_info(records, float_shares=10_000_000)
        assert result["shares_offered_pct_float"] == pytest.approx(0.20)

    def test_no_shares_offered_gives_none_pct(self):
        records = [{"dilution_risk_flag": True, "filing_date": "2024-05-15", "shares_offered": None}]
        result = extract_dilution_info(records, float_shares=10_000_000)
        assert result["shares_offered_pct_float"] is None


# ── Test 14: extract_dilution_info — days_since_filing ────────────────────────

class TestDaysSinceFiling:

    def test_days_since_filing_computed(self):
        records = [{"dilution_risk_flag": True, "filing_date": "2024-05-20", "shares_offered": None}]
        result = extract_dilution_info(records, as_of_date=date(2024, 6, 1))
        assert result["days_since_filing"] == 12


# ── Test 15: Price extension risk thresholds ──────────────────────────────────

class TestPriceExtensionRisk:

    def test_price_ext_above_50pct_adds_high_risk_flag(self):
        result = _risk(price_extension_pct=0.55)
        assert "PRICE_EXTENSION_RISK" in result["risk_flags"]
        assert result["risk_components"]["exhaustion"] >= 20

    def test_price_ext_between_30_and_50pct_adds_mid_flag(self):
        result = _risk(price_extension_pct=0.35)
        assert "PRICE_EXTENSION_RISK" in result["risk_flags"]
        assert result["risk_components"]["exhaustion"] == 10

    def test_price_ext_below_30pct_no_flag(self):
        result = _risk(price_extension_pct=0.20)
        assert "PRICE_EXTENSION_RISK" not in result["risk_flags"]
