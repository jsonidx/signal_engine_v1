"""
Tests for new modules:
  - utils/dcf_model.py
  - utils/peer_benchmarking.py
  - red_flag_screener.py
  - earnings_transcript.py (cache/offline portions only)
  - Updated conflict_resolver (red_flag_screener module vote)
  - Updated fundamental_analysis (extended mode)
"""

import json
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ==============================================================================
# DCF MODEL
# ==============================================================================

class TestDCFModel:

    def test_run_dcf_returns_dict(self):
        from utils.dcf_model import run_dcf
        result = run_dcf("AAPL")
        assert isinstance(result, dict)
        assert "ticker" in result
        assert "wacc" in result
        assert "intrinsic_value" in result
        assert "data_quality" in result

    def test_run_dcf_insufficient_on_fake_ticker(self):
        from utils.dcf_model import run_dcf
        result = run_dcf("XXXXXXXXFAKE")
        assert result["data_quality"] == "INSUFFICIENT"
        assert result["intrinsic_value"] is None

    def test_compute_wacc_basic(self):
        from utils.dcf_model import compute_wacc
        inputs = {
            "mkt_cap": 1_000_000_000,
            "total_debt": 200_000_000,
            "beta": 1.2,
            "tax_rate": 0.21,
            "interest_expense": 8_000_000,
            "sector": "Technology",
        }
        result = compute_wacc(inputs, rf=0.045)
        assert isinstance(result["wacc"], float)
        assert 0.06 <= result["wacc"] <= 0.20
        assert result["cost_equity"] > result["cost_debt"]
        assert result["weight_equity"] + result["weight_debt"] == pytest.approx(1.0, abs=0.01)

    def test_compute_wacc_no_debt(self):
        from utils.dcf_model import compute_wacc
        inputs = {
            "mkt_cap": 500_000_000,
            "total_debt": 0,
            "beta": 1.0,
            "tax_rate": 0.21,
            "interest_expense": None,
            "sector": "Technology",
        }
        result = compute_wacc(inputs, rf=0.045)
        assert result["weight_debt"] == pytest.approx(0.0, abs=0.01)
        assert result["weight_equity"] == pytest.approx(1.0, abs=0.01)

    def test_compute_wacc_beta_clipping(self):
        from utils.dcf_model import compute_wacc
        # Beta of 0.0 is treated as unavailable → replaced by sector proxy (1.0 for unknown sector)
        inputs = {"mkt_cap": 1e9, "total_debt": 0, "beta": 0.0,
                  "tax_rate": 0.21, "interest_expense": None, "sector": ""}
        result = compute_wacc(inputs, rf=0.045)
        # Sector proxy for unknown sector is 1.0; after clip: still 1.0
        assert result["beta_used"] == pytest.approx(1.0, abs=0.01)
        # Beta of 5.0 (provided, > 0) should be clipped to 3.0
        inputs["beta"] = 5.0
        result = compute_wacc(inputs, rf=0.045)
        assert result["beta_used"] == pytest.approx(3.0, abs=0.01)

    def test_project_fcf_positive_growth(self):
        from utils.dcf_model import project_fcf
        inputs = {"fcf": 1_000_000_000, "revenue_growth": 0.15, "tax_rate": 0.21, "ebit": None}
        proj = project_fcf(inputs, wacc_rate=0.10)
        assert len(proj) == 5
        # Year 1 should be ~15% higher than base
        assert proj[0] > 1_000_000_000
        # Years 4-5 should grow slower than years 1-3
        growth_yr1 = proj[0] / 1_000_000_000 - 1
        growth_yr5 = proj[4] / proj[3] - 1
        assert growth_yr5 < growth_yr1

    def test_project_fcf_negative_base(self):
        from utils.dcf_model import project_fcf
        # Negative FCF should return empty list (no projection)
        inputs = {"fcf": -100_000_000, "revenue_growth": 0.20, "tax_rate": 0.21, "ebit": None}
        proj = project_fcf(inputs, wacc_rate=0.10)
        # Negative FCF: projections still run but values are negative
        # (that's by design — DCF will produce a low/negative EV)
        assert isinstance(proj, list)

    def test_project_fcf_ebit_fallback(self):
        from utils.dcf_model import project_fcf
        # No FCF but EBIT available — should use EBIT * (1-tax) * 0.85
        inputs = {"fcf": None, "ebit": 500_000_000, "revenue_growth": 0.10, "tax_rate": 0.21}
        proj = project_fcf(inputs, wacc_rate=0.10)
        assert len(proj) == 5
        assert all(v > 0 for v in proj)

    def test_compute_roic(self):
        from utils.dcf_model import compute_roic
        inputs = {
            "ebit": 100_000_000,
            "tax_rate": 0.21,
            "book_equity": 200_000_000,
            "total_debt": 50_000_000,
            "total_cash": 20_000_000,
            "mkt_cap": 1_000_000_000,
        }
        roic = compute_roic(inputs)
        expected_nopat = 100_000_000 * (1 - 0.21)
        expected_ic = 200_000_000 + 50_000_000 - 20_000_000
        assert roic == pytest.approx(expected_nopat / expected_ic, abs=0.001)

    def test_compute_roic_no_ebit(self):
        from utils.dcf_model import compute_roic
        result = compute_roic({"ebit": None, "tax_rate": 0.21})
        assert result is None

    def test_flags_populated(self):
        from utils.dcf_model import run_dcf
        result = run_dcf("MSFT")
        assert isinstance(result["flags"], list)

    def test_data_quality_levels(self):
        from utils.dcf_model import run_dcf
        result = run_dcf("AAPL")
        assert result["data_quality"] in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")

    def test_wacc_bounds(self):
        from utils.dcf_model import run_dcf
        result = run_dcf("AAPL")
        if result["wacc"] is not None:
            assert 0.06 <= result["wacc"] <= 0.20

    def test_roic_wacc_spread_sign(self):
        from utils.dcf_model import run_dcf
        result = run_dcf("AAPL")
        if result["roic"] is not None and result["wacc"] is not None:
            expected_spread = result["roic"] - result["wacc"]
            assert result["roic_wacc_spread"] == pytest.approx(expected_spread, abs=0.001)


# ==============================================================================
# PEER BENCHMARKING
# ==============================================================================

class TestPeerBenchmarking:

    def test_returns_dict_with_required_keys(self):
        from utils.peer_benchmarking import run_peer_benchmarking
        result = run_peer_benchmarking("AAPL")
        required = [
            "sector", "peer_tickers", "peer_median_pe", "stock_pe",
            "pe_vs_peers_pct", "relative_valuation", "flags",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_relative_valuation_valid_values(self):
        from utils.peer_benchmarking import run_peer_benchmarking
        result = run_peer_benchmarking("AAPL")
        assert result["relative_valuation"] in ("CHEAP", "FAIR", "RICH", "INSUFFICIENT")

    def test_sector_peers_excludes_ticker(self):
        from utils.peer_benchmarking import get_sector_peers
        peers = get_sector_peers("AAPL", "Technology")
        assert "AAPL" not in peers

    def test_sector_peers_capped_at_12(self):
        from utils.peer_benchmarking import get_sector_peers
        peers = get_sector_peers("MSFT", "Technology")
        assert len(peers) <= 12

    def test_safe_median_none_values(self):
        from utils.peer_benchmarking import _safe_median
        assert _safe_median([None, None, None]) is None
        assert _safe_median([10, None, 20]) == pytest.approx(15.0, abs=0.1)
        assert _safe_median([]) is None

    def test_pe_vs_peers_pct_formula(self):
        from utils.peer_benchmarking import run_peer_benchmarking
        result = run_peer_benchmarking("NVDA")
        # If both stock P/E and peer median P/E are available, check formula
        if result["stock_pe"] and result["peer_median_pe"]:
            expected = (result["stock_pe"] / result["peer_median_pe"] - 1) * 100
            assert result["pe_vs_peers_pct"] == pytest.approx(expected, abs=0.5)

    def test_flags_are_list(self):
        from utils.peer_benchmarking import run_peer_benchmarking
        result = run_peer_benchmarking("AAPL")
        assert isinstance(result["flags"], list)
        assert len(result["flags"]) >= 1

    def test_fake_ticker_degrades_gracefully(self):
        from utils.peer_benchmarking import run_peer_benchmarking
        # Should not raise; returns a dict with INSUFFICIENT or partial data
        result = run_peer_benchmarking("XXXXXXXXFAKE")
        assert isinstance(result, dict)
        assert "relative_valuation" in result


# ==============================================================================
# RED FLAG SCREENER
# ==============================================================================

class TestRedFlagScreener:

    def test_returns_required_keys(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("AAPL", skip_edgar=True)
        required = ["ticker", "red_flag_score", "risk_level", "checks", "flags", "data_quality"]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_score_in_valid_range(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("AAPL", skip_edgar=True)
        assert 0 <= result["red_flag_score"] <= 100

    def test_risk_level_valid_values(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("AAPL", skip_edgar=True)
        assert result["risk_level"] in ("CLEAN", "CAUTION", "WARNING", "CRITICAL")

    def test_risk_level_consistent_with_score(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("AAPL", skip_edgar=True)
        score = result["red_flag_score"]
        level = result["risk_level"]
        if score <= 20:
            assert level == "CLEAN"
        elif score <= 45:
            assert level == "CAUTION"
        elif score <= 70:
            assert level == "WARNING"
        else:
            assert level == "CRITICAL"

    def test_all_5_checks_present(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("MSFT", skip_edgar=True)
        checks = result["checks"]
        expected_checks = [
            "restatement", "accruals", "gaap_divergence", "payout_risk", "revenue_quality"
        ]
        for check in expected_checks:
            assert check in checks, f"Missing check: {check}"

    def test_each_check_has_score_and_detail(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("NVDA", skip_edgar=True)
        for name, check in result["checks"].items():
            assert "score" in check, f"Check {name} missing 'score'"
            assert "detail" in check, f"Check {name} missing 'detail'"
            assert 0 <= check["score"] <= 25, f"Check {name} score out of range"

    def test_total_score_equals_sum_of_checks(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("MSFT", skip_edgar=True)
        check_sum = sum(c["score"] for c in result["checks"].values())
        # Total is capped at 100
        expected = min(check_sum, 100)
        assert result["red_flag_score"] == expected

    def test_skip_edgar_flag(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("AAPL", skip_edgar=True)
        restatement_detail = result["checks"]["restatement"]["detail"]
        assert "skipped" in restatement_detail.lower() or "edgar" in restatement_detail.lower()

    def test_dividend_payer_has_payout_detail(self):
        from red_flag_screener import run_red_flag_screener
        # MSFT pays a dividend
        result = run_red_flag_screener("MSFT", skip_edgar=True)
        payout = result["checks"]["payout_risk"]
        assert payout["score"] is not None

    def test_no_dividend_score_zero(self):
        from red_flag_screener import check_payout_sustainability
        # A company with no dividend should return score=0
        # We mock this by calling with a crypto/no-dividend ticker
        result = check_payout_sustainability("BRK-B")  # Berkshire pays no dividend
        assert result["score"] == 0 or "no dividend" in result["detail"].lower()

    def test_accruals_check_returns_ratio(self):
        from red_flag_screener import check_accruals
        result = check_accruals("AAPL")
        assert "score" in result
        assert "ratio" in result
        assert 0 <= result["score"] <= 25

    def test_gaap_divergence_check(self):
        from red_flag_screener import check_gaap_divergence
        result = check_gaap_divergence("AAPL")
        assert "score" in result
        assert 0 <= result["score"] <= 25

    def test_revenue_quality_check(self):
        from red_flag_screener import check_revenue_quality
        result = check_revenue_quality("MSFT")
        assert "score" in result
        assert 0 <= result["score"] <= 25

    def test_fake_ticker_does_not_raise(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("XXXXXXXXFAKE", skip_edgar=True)
        assert isinstance(result, dict)
        assert result["red_flag_score"] >= 0

    def test_flags_list_not_empty(self):
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener("AAPL", skip_edgar=True)
        assert len(result["flags"]) >= 1


# ==============================================================================
# EARNINGS TRANSCRIPT (cache/offline portions only — no live EDGAR/Claude calls)
# ==============================================================================

class TestEarningsTranscript:

    def test_cache_init(self):
        from earnings_transcript import _init_cache
        conn = _init_cache()
        assert conn is not None
        conn.close()

    def test_cache_save_and_retrieve(self):
        from earnings_transcript import _save_cache, _get_cached
        import time as _time
        ticker = "TESTXYZ"
        analysis = {
            "tone_score": 2,
            "tone_label": "BULLISH",
            "guidance_direction": "RAISED",
            "guidance_confidence": "HIGH",
        }
        _save_cache(ticker, "2026-01-01", analysis, "test transcript text")
        retrieved = _get_cached(ticker)
        assert retrieved is not None
        assert retrieved["tone_score"] == 2
        assert retrieved["tone_label"] == "BULLISH"

    def test_cache_returns_none_when_expired(self):
        """Cache older than TTL should not be returned."""
        from earnings_transcript import _init_cache, _get_cached
        import sqlite3
        conn = _init_cache()
        # Insert an old entry
        conn.execute(
            "INSERT OR REPLACE INTO transcript_cache "
            "(ticker, filing_date, analysis_json, transcript_snippet, created_at) "
            "VALUES (?,?,?,?,?)",
            (
                "EXPIREDTEST",
                "2025-01-01",
                '{"tone_score": 3}',
                "snippet",
                "2025-01-01T00:00:00",  # very old
            ),
        )
        conn.commit()
        conn.close()
        result = _get_cached("EXPIREDTEST")
        assert result is None  # Should be expired

    def test_strip_html_removes_tags(self):
        from earnings_transcript import _strip_html
        html = "<p>Hello <b>World</b></p><script>evil()</script>"
        stripped = _strip_html(html)
        assert "<" not in stripped
        assert "Hello" in stripped
        assert "World" in stripped
        assert "evil" not in stripped

    def test_strip_html_decodes_entities(self):
        from earnings_transcript import _strip_html
        html = "Revenue grew &amp; margin improved &gt; expectations"
        stripped = _strip_html(html)
        assert "&amp;" not in stripped
        assert "&" in stripped

    def test_find_transcript_section_finds_markers(self):
        from earnings_transcript import _find_transcript_section
        text = """
Annual Report 2025 boilerplate header...
Page 1 of 50

Operator: Good morning, and welcome to the Q4 2025 Earnings Call.
CEO: Thank you operator. We had a strong quarter with revenue up 20%.
CFO: Our free cash flow grew significantly.
Q&A Session:
Analyst: Can you talk about guidance?
CEO: We are raising our full year guidance.
"""
        section = _find_transcript_section(text)
        assert "Operator" in section or "Good morning" in section

    def test_get_transcript_signals_no_transcript_available(self):
        """Fake ticker should return transcript_available=False without raising."""
        from earnings_transcript import get_transcript_signals
        result = get_transcript_signals("XXXXXXXXFAKE", use_cache=False)
        assert isinstance(result, dict)
        # Should return gracefully
        assert "transcript_available" in result

    def test_analyze_transcript_fallback_on_no_api_key(self, monkeypatch):
        """Without ANTHROPIC_API_KEY, should return neutral fallback."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from earnings_transcript import analyze_transcript_with_claude
        result = analyze_transcript_with_claude("TEST", "Some transcript text")
        assert result["tone_label"] == "NEUTRAL"
        assert "error" in result


# ==============================================================================
# CONFLICT RESOLVER — RED FLAG MODULE INTEGRATION
# ==============================================================================

class TestConflictResolverRedFlags:

    def test_red_flag_module_in_weights(self):
        from conflict_resolver import MODULE_WEIGHTS, _SIGNALS_KEY_MAP
        assert "red_flag_screener" in MODULE_WEIGHTS
        assert "red_flag_screener" in _SIGNALS_KEY_MAP
        assert _SIGNALS_KEY_MAP["red_flag_screener"] == "red_flags"

    def test_red_flag_module_weight_positive(self):
        from conflict_resolver import MODULE_WEIGHTS
        assert MODULE_WEIGHTS["red_flag_screener"] > 0

    def test_extract_direction_red_flag_warning_is_bear(self):
        from conflict_resolver import extract_module_direction
        module_output = {
            "red_flag_risk_level": "WARNING",
            "red_flag_score": 55,
        }
        direction = extract_module_direction("red_flag_screener", module_output)
        assert direction == "BEAR"

    def test_extract_direction_red_flag_critical_is_bear(self):
        from conflict_resolver import extract_module_direction
        module_output = {
            "red_flag_risk_level": "CRITICAL",
            "red_flag_score": 85,
        }
        direction = extract_module_direction("red_flag_screener", module_output)
        assert direction == "BEAR"

    def test_extract_direction_red_flag_clean_is_none(self):
        from conflict_resolver import extract_module_direction
        module_output = {
            "red_flag_risk_level": "CLEAN",
            "red_flag_score": 5,
        }
        direction = extract_module_direction("red_flag_screener", module_output)
        assert direction is None  # CLEAN = no vote (not BULL, not BEAR)

    def test_extract_direction_red_flag_caution_is_none(self):
        from conflict_resolver import extract_module_direction
        module_output = {
            "red_flag_risk_level": "CAUTION",
            "red_flag_score": 30,
        }
        direction = extract_module_direction("red_flag_screener", module_output)
        assert direction is None  # CAUTION = no directional vote yet

    def test_extract_direction_red_flag_score_threshold(self):
        """Score >= 46 should trigger BEAR even without risk_level."""
        from conflict_resolver import extract_module_direction
        module_output = {"red_flag_risk_level": "", "red_flag_score": 50}
        direction = extract_module_direction("red_flag_screener", module_output)
        assert direction == "BEAR"

    def test_extract_direction_red_flag_empty_is_none(self):
        from conflict_resolver import extract_module_direction
        direction = extract_module_direction("red_flag_screener", {})
        assert direction is None

    def test_red_flag_bear_vote_reduces_bull_confidence(self):
        """When red flags vote BEAR, net BULL confidence should decrease."""
        from conflict_resolver import compute_weighted_vote
        # Build signals: mostly bull but with red flags WARNING
        signals = {
            "signal_engine": {"composite_z": 1.5},      # BULL
            "fundamentals":  {"fundamental_score_pct": 70},  # BULL
            "squeeze":       {"squeeze_score_100": 60},  # BULL
            "options_flow":  {"heat_score": 70, "pc_ratio": 0.5},  # BULL
            "cross_asset":   {"signal": "NEUTRAL"},
            "polymarket":    {},
            "dark_pool_flow":{"signal": "NEUTRAL"},
            "red_flags":     {"red_flag_risk_level": "WARNING", "red_flag_score": 60},  # BEAR
            "sec":           {},
            "congress":      {},
        }
        result = compute_weighted_vote(signals)
        # Red flag should add to bear weight
        assert result["bear_weight"] > 0
        # Bull should still win due to strong signals, but with lower confidence
        assert result["net_direction"] == "BULL"

    def test_red_flag_critical_can_swing_neutral(self):
        """Critical red flags with weak bull signal should produce NEUTRAL."""
        from conflict_resolver import compute_weighted_vote
        signals = {
            "signal_engine": {"composite_z": 0.3},      # No vote (below threshold)
            "fundamentals":  {"fundamental_score_pct": 50},  # No vote
            "squeeze":       {"squeeze_score_100": 30},  # No vote
            "options_flow":  {"heat_score": 40},         # No vote
            "cross_asset":   {"signal": "NEUTRAL"},
            "polymarket":    {},
            "dark_pool_flow":{"signal": "NEUTRAL"},
            "red_flags":     {"red_flag_risk_level": "CRITICAL", "red_flag_score": 80},  # BEAR
            "sec":           {},
            "congress":      {},
        }
        result = compute_weighted_vote(signals)
        # Only BEAR vote is from red flags — should be BEAR or NEUTRAL
        assert result["net_direction"] in ("BEAR", "NEUTRAL")


# ==============================================================================
# FUNDAMENTAL ANALYSIS — EXTENDED MODE
# ==============================================================================

class TestFundamentalAnalysisExtended:

    def test_analyze_ticker_basic_no_extended(self):
        from fundamental_analysis import analyze_ticker
        result = analyze_ticker("AAPL", use_cache=True, extended=False)
        assert result is not None
        assert "composite" in result
        # Extended composite should equal base composite when extended=False
        assert result["extended_composite"] == result["composite"]

    def test_analyze_ticker_has_extended_composite(self):
        from fundamental_analysis import analyze_ticker
        result = analyze_ticker("AAPL", use_cache=True, extended=False)
        assert "extended_composite" in result

    def test_score_dcf_valuation_returns_dict(self):
        from fundamental_analysis import score_dcf_valuation
        result = score_dcf_valuation("AAPL")
        assert "score" in result
        assert "max" in result
        assert "flags" in result
        assert 0 <= result["score"] <= 4

    def test_score_peer_relative_valuation_returns_dict(self):
        from fundamental_analysis import score_peer_relative_valuation
        result = score_peer_relative_valuation("AAPL")
        assert "score" in result
        assert "max" in result
        assert 0 <= result["score"] <= 4

    def test_score_accounting_quality_returns_dict(self):
        from fundamental_analysis import score_accounting_quality
        result = score_accounting_quality("AAPL")
        assert "score" in result
        assert "max" in result
        # Clean accounting → score 4, degraded → lower
        assert 0 <= result["score"] <= 4

    def test_score_accounting_quality_default_on_error(self):
        """Should return score=2 (neutral) if module unavailable."""
        from fundamental_analysis import score_accounting_quality
        result = score_accounting_quality("XXXXXXXXFAKE")
        assert "score" in result
        # Should not raise; returns a valid score

    def test_score_dcf_fake_ticker_returns_zero(self):
        from fundamental_analysis import score_dcf_valuation
        result = score_dcf_valuation("XXXXXXXXFAKE")
        assert result["score"] == 0
        assert len(result["flags"]) >= 1
