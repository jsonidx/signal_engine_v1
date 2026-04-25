"""
Unit tests for CHUNK-07: SEC filing catalyst parsing extensions in sec_module.py.

Tests cover:
  - Dilution risk detection (424B5, S-3, ATM language)
  - shares_offered parsing
  - Ownership accumulation detection (13G/13D)
  - pct_class and shares_beneficially_owned parsing
  - large_holder_flag threshold
  - derivative_exposure_flag
  - Filing catalyst record building and persistence stub

All tests are pure-function tests — no live EDGAR HTTP calls.
"""

import pytest
from unittest.mock import patch, MagicMock

from sec_module import (
    classify_filing,
    _parse_pct_class,
    _parse_beneficial_shares,
    _parse_shares_offered,
    _detect_derivative_exposure,
    _detect_atm_language,
    build_filing_catalyst_records,
    get_activist_filings,
    get_dilution_filings,
    _LARGE_HOLDER_PCT_THRESHOLD,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _edgar_hit(form_type, file_date="2024-06-15", filer="Test Corp",
               accession_no="0001234567-24-001234"):
    """Build a minimal mock EDGAR search hit _source dict."""
    return {
        "_id": accession_no,
        "_source": {
            "file_date": file_date,
            "form_type": form_type,
            "display_names": [filer],
            "accession_no": accession_no,
            "entity_name": filer,
            "period_of_report": file_date,
        },
    }


def _mock_edgar_response(hits):
    return {"hits": {"hits": hits, "total": {"value": len(hits)}}}


# ── Dilution detection ────────────────────────────────────────────────────────

class TestDilutionDetection:

    def test_detects_424b5_dilution_risk(self):
        result = classify_filing("424B5")
        assert result["dilution_risk_flag"] is True

    def test_detects_s3_dilution_risk(self):
        result = classify_filing("S-3")
        assert result["dilution_risk_flag"] is True

    def test_detects_s3asr_dilution_risk(self):
        result = classify_filing("S-3ASR")
        assert result["dilution_risk_flag"] is True

    def test_detects_f3_dilution_risk(self):
        result = classify_filing("F-3")
        assert result["dilution_risk_flag"] is True

    def test_no_dilution_flag_for_13g(self):
        result = classify_filing("SC 13G")
        assert result["dilution_risk_flag"] is False

    def test_detects_atm_language_in_title(self):
        result = classify_filing("S-3", title="Prospectus for at-the-market offering program")
        assert result["dilution_risk_flag"] is True

    def test_detects_sales_agreement_language(self):
        result = classify_filing("8-K", text="entered into an equity distribution agreement")
        assert result["dilution_risk_flag"] is True

    def test_detects_may_offer_and_sell_language(self):
        result = classify_filing("8-K", text="The company may offer and sell shares of common stock")
        assert result["dilution_risk_flag"] is True

    def test_no_dilution_flag_for_plain_10k(self):
        result = classify_filing("10-K")
        assert result["dilution_risk_flag"] is False


class TestSharesOfferedParsing:

    def test_parses_numeric_shares_offered(self):
        text = "offering of up to 5,000,000 shares of common stock"
        result = _parse_shares_offered(text)
        assert result == 5_000_000

    def test_parses_shares_offered_million_word(self):
        text = "up to 10 million shares"
        result = _parse_shares_offered(text)
        assert result == 10_000_000

    def test_parses_aggregate_shares(self):
        text = "an aggregate of 3,500,000 shares of common stock"
        result = _parse_shares_offered(text)
        assert result == 3_500_000

    def test_returns_none_when_no_match(self):
        text = "This is a quarterly earnings report."
        result = _parse_shares_offered(text)
        assert result is None

    def test_classify_filing_propagates_shares_offered(self):
        result = classify_filing("424B5", text="up to 5,000,000 shares of common stock")
        assert result["shares_offered"] == 5_000_000


# ── Ownership accumulation ────────────────────────────────────────────────────

class TestOwnershipAccumulationDetection:

    def test_detects_13g_ownership_accumulation(self):
        result = classify_filing("SC 13G")
        assert result["ownership_accumulation_flag"] is True

    def test_detects_13d_ownership_accumulation(self):
        result = classify_filing("SC 13D")
        assert result["ownership_accumulation_flag"] is True

    def test_detects_13g_amendment(self):
        result = classify_filing("SC 13G/A")
        assert result["ownership_accumulation_flag"] is True

    def test_detects_13d_amendment(self):
        result = classify_filing("SC 13D/A")
        assert result["ownership_accumulation_flag"] is True

    def test_no_ownership_flag_for_424b5(self):
        result = classify_filing("424B5")
        assert result["ownership_accumulation_flag"] is False


class TestPctClassParsing:

    def test_parses_pct_class_colon_format(self):
        text = "Percent of class: 22.2%"
        assert _parse_pct_class(text) == pytest.approx(22.2)

    def test_parses_pct_class_no_percent_sign(self):
        text = "Percent of Class Represented by Amount in Row (11): 22.2"
        assert _parse_pct_class(text) == pytest.approx(22.2)

    def test_parses_aggregate_percent_of_class(self):
        text = "Aggregate percent of class of securities: 15.3%"
        assert _parse_pct_class(text) == pytest.approx(15.3)

    def test_returns_none_when_no_match(self):
        assert _parse_pct_class("No percentage here.") is None

    def test_rejects_values_over_100(self):
        # 105% would be invalid
        assert _parse_pct_class("Percent of class: 105.0%") is None

    def test_classify_filing_propagates_pct_class(self):
        text = "SC 13G filing — percent of class: 22.2%"
        result = classify_filing("SC 13G", text=text)
        assert result["pct_class"] == pytest.approx(22.2)


class TestBeneficialSharesParsing:

    def test_parses_amount_beneficially_owned(self):
        text = "Amount beneficially owned: 7,824,100"
        assert _parse_beneficial_shares(text) == 7_824_100

    def test_parses_aggregate_amount(self):
        text = "Aggregate amount beneficially owned: 12,500,000"
        assert _parse_beneficial_shares(text) == 12_500_000

    def test_parses_shares_beneficially_owned(self):
        text = "Total shares beneficially owned: 5,000,000"
        assert _parse_beneficial_shares(text) == 5_000_000

    def test_returns_none_when_no_match(self):
        assert _parse_beneficial_shares("No share count here.") is None


# ── Large holder flag ─────────────────────────────────────────────────────────

class TestLargeHolderFlag:

    def test_large_holder_flag_set_for_pct_above_threshold(self):
        text = f"Percent of class: {_LARGE_HOLDER_PCT_THRESHOLD + 5:.1f}%"
        result = classify_filing("SC 13G", text=text)
        assert result["large_holder_flag"] is True

    def test_large_holder_flag_set_at_exact_threshold(self):
        text = f"Percent of class: {_LARGE_HOLDER_PCT_THRESHOLD:.1f}%"
        result = classify_filing("SC 13G", text=text)
        assert result["large_holder_flag"] is True

    def test_large_holder_flag_false_below_threshold(self):
        text = "Percent of class: 7.5%"
        result = classify_filing("SC 13G", text=text)
        assert result["large_holder_flag"] is False

    def test_large_holder_flag_false_when_pct_not_parsed(self):
        result = classify_filing("SC 13G")
        assert result["large_holder_flag"] is False

    def test_specific_22pct_triggers_large_holder(self):
        text = "Percent of class: 22.2%"
        result = classify_filing("SC 13G", text=text)
        assert result["large_holder_flag"] is True


# ── Derivative exposure flag ──────────────────────────────────────────────────

class TestDerivativeExposureFlag:

    def test_call_options_triggers_flag(self):
        assert _detect_derivative_exposure("shares issuable upon exercise of call options") is True

    def test_warrants_triggers_flag(self):
        assert _detect_derivative_exposure("includes 500,000 shares issuable upon exercise of warrants") is True

    def test_swaps_triggers_flag(self):
        assert _detect_derivative_exposure("The reporting person holds equity swaps") is True

    def test_derivatives_word_triggers_flag(self):
        assert _detect_derivative_exposure("Economic exposure via derivatives") is True

    def test_convertible_notes_triggers_flag(self):
        assert _detect_derivative_exposure("convertible notes at 3.5% due 2027") is True

    def test_no_derivative_language_returns_false(self):
        assert _detect_derivative_exposure("Aggregate amount beneficially owned: 7,824,100") is False

    def test_classify_filing_propagates_derivative_flag(self):
        text = "The holder has call options on 1,000,000 shares."
        result = classify_filing("SC 13D", text=text)
        assert result["derivative_exposure_flag"] is True


# ── Build filing catalyst records ─────────────────────────────────────────────

class TestBuildFilingCatalystRecords:

    def _activist(self, form="SC 13G", pct=22.2):
        return {
            "date": "2024-06-15",
            "filer": "Pentwater Capital",
            "form": form,
            "description": f"5%+ ownership stake ({form})",
            "accession_number": "0001234567-24-001234",
            "holder_name": "Pentwater Capital Management LP",
            "source_url": "https://efts.sec.gov/LATEST/search-index?q=%220001234567-24-001234%22",
            "ownership_accumulation_flag": True,
            "large_holder_flag": pct >= _LARGE_HOLDER_PCT_THRESHOLD,
            "derivative_exposure_flag": False,
            "pct_class": pct,
            "shares_beneficially_owned": 7_824_100,
        }

    def _dilution(self, form="424B5"):
        return {
            "date": "2024-07-01",
            "filer": "AVIS Budget Group",
            "form": form,
            "description": f"Potential dilution filing ({form})",
            "accession_number": "0001234567-24-009999",
            "issuer": "AVIS Budget Group",
            "source_url": "https://efts.sec.gov/LATEST/search-index?q=%220001234567-24-009999%22",
            "dilution_risk_flag": True,
            "shares_offered": 5_000_000,
            "derivative_exposure_flag": False,
        }

    def test_builds_record_from_activist_filing(self):
        records = build_filing_catalyst_records("CAR", [self._activist()], [])
        assert len(records) == 1
        r = records[0]
        assert r["ticker"] == "CAR"
        assert r["filing_type"] == "SC 13G"
        assert r["ownership_accumulation_flag"] is True
        assert r["dilution_risk_flag"] is False
        assert r["pct_class"] == pytest.approx(22.2)
        assert r["shares_beneficially_owned"] == 7_824_100
        assert r["accession_number"] == "0001234567-24-001234"
        assert r["source_url"] is not None

    def test_builds_record_from_dilution_filing(self):
        records = build_filing_catalyst_records("CAR", [], [self._dilution()])
        assert len(records) == 1
        r = records[0]
        assert r["ticker"] == "CAR"
        assert r["filing_type"] == "424B5"
        assert r["dilution_risk_flag"] is True
        assert r["ownership_accumulation_flag"] is False
        assert r["shares_offered"] == 5_000_000

    def test_builds_combined_records(self):
        records = build_filing_catalyst_records(
            "CAR", [self._activist()], [self._dilution()]
        )
        assert len(records) == 2
        types = {r["filing_type"] for r in records}
        assert "SC 13G" in types
        assert "424B5" in types

    def test_empty_inputs_return_empty_list(self):
        assert build_filing_catalyst_records("CAR", [], []) == []

    def test_source_field_is_edgar_search(self):
        records = build_filing_catalyst_records("CAR", [self._activist()], [])
        assert records[0]["source"] == "edgar_search"


# ── Persistence stub ──────────────────────────────────────────────────────────

class TestSaveFilingCatalysts:

    def test_empty_records_no_crash(self):
        """save_filing_catalysts with empty list must not raise."""
        from utils.supabase_persist import save_filing_catalysts
        save_filing_catalysts([])  # should return silently

    def test_db_unavailable_no_crash(self):
        """If DB is down, save_filing_catalysts must not raise."""
        from utils.supabase_persist import save_filing_catalysts
        bad_record = {
            "ticker": "CAR",
            "filing_date": "2024-06-15",
            "filing_type": "SC 13G",
        }
        with patch("utils.supabase_persist._conn", side_effect=Exception("DB down")):
            save_filing_catalysts([bad_record])  # must not raise


# ── get_activist_filings with mocked EDGAR ────────────────────────────────────

class TestGetActivistFilingsMocked:

    def test_returns_extended_fields(self):
        mock_hit = _edgar_hit("SC 13G", filer="Pentwater Capital")
        mock_resp = _mock_edgar_response([mock_hit])

        with patch("sec_module._sec_request", return_value=mock_resp):
            result = get_activist_filings("CAR", days_back=365)

        assert len(result) > 0
        r = result[0]
        assert "ownership_accumulation_flag" in r
        assert r["ownership_accumulation_flag"] is True
        assert "holder_name" in r
        assert "source_url" in r
        assert "accession_number" in r

    def test_returns_empty_on_edgar_failure(self):
        with patch("sec_module._sec_request", return_value=None):
            result = get_activist_filings("CAR", days_back=365)
        assert result == []


# ── get_dilution_filings with mocked EDGAR ────────────────────────────────────

class TestGetDilutionFilingsMocked:

    def test_returns_dilution_risk_flag(self):
        mock_hit = _edgar_hit("424B5", filer="AVIS Budget Group")
        mock_resp = _mock_edgar_response([mock_hit])

        with patch("sec_module._sec_request", return_value=mock_resp):
            result = get_dilution_filings("CAR", days_back=365)

        assert len(result) > 0
        assert result[0]["dilution_risk_flag"] is True

    def test_returns_empty_on_edgar_failure(self):
        with patch("sec_module._sec_request", return_value=None):
            result = get_dilution_filings("CAR", days_back=365)
        assert result == []
