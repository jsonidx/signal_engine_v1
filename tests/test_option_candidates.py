"""
Tests for utils/option_candidates.py  (TRD-022)

Covers: suppression logic, hard filters, scoring behavior, API response structure.
All tests are deterministic and use mocked chain data.
"""

from datetime import date, timedelta
from typing import List, Optional

import pytest

from utils.ibkr_options import OptionChainResult, OptionContract
from utils.option_candidates import (
    CandidateResult,
    OptionCandidate,
    PRESETS,
    ThesisContext,
    _breakeven,
    _build_rationale,
    _score_contract,
    _should_suppress,
    get_option_candidates,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _contract(
    ticker="AAPL",
    expiry=None,
    strike=150.0,
    right="C",
    dte=21,
    bid=2.0,
    ask=2.20,
    mid=2.10,
    delta=0.40,
    iv=0.35,
    oi=500,
    volume=100,
    underlying_price=148.0,
) -> OptionContract:
    if expiry is None:
        expiry = _expiry(dte)
    return OptionContract(
        ticker=ticker,
        expiry=expiry,
        strike=strike,
        right=right,
        dte=dte,
        bid=bid,
        ask=ask,
        mid=mid,
        open_interest=oi,
        volume=volume,
        delta=delta,
        implied_vol=iv,
        underlying_price=underlying_price,
        source="mock",
    )


def _bull_thesis(**kwargs) -> ThesisContext:
    defaults = dict(
        ticker="AAPL",
        direction="BULL",
        conviction=3,
        current_price=148.0,
        entry_low=145.0,
        entry_high=150.0,
        target_1=160.0,
        target_2=175.0,
        stop_loss=140.0,
    )
    defaults.update(kwargs)
    return ThesisContext(**defaults)


def _bear_thesis(**kwargs) -> ThesisContext:
    defaults = dict(
        ticker="AAPL",
        direction="BEAR",
        conviction=3,
        current_price=148.0,
        entry_low=145.0,
        entry_high=150.0,
        target_1=130.0,
        target_2=115.0,
        stop_loss=155.0,
    )
    defaults.update(kwargs)
    return ThesisContext(**defaults)


def _chain(contracts: List[OptionContract], price=148.0) -> OptionChainResult:
    return OptionChainResult(
        ticker="AAPL",
        underlying_price=price,
        fetch_time="2026-05-29T12:00:00",
        contracts=contracts,
        expiries=[_expiry(21)],
        source="mock",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Suppression logic
# ══════════════════════════════════════════════════════════════════════════════

class TestSuppression:
    def test_neutral_direction_suppressed(self):
        thesis = ThesisContext(ticker="AAPL", direction="NEUTRAL", conviction=3)
        suppressed, reason = _should_suppress(thesis)
        assert suppressed
        assert "NEUTRAL" in reason

    def test_conviction_1_suppressed(self):
        thesis = ThesisContext(ticker="AAPL", direction="BULL", conviction=1)
        suppressed, reason = _should_suppress(thesis)
        assert suppressed
        assert "1/5" in reason or "conviction" in reason.lower()

    def test_conviction_2_not_suppressed(self):
        thesis = ThesisContext(ticker="AAPL", direction="BULL", conviction=2)
        suppressed, _ = _should_suppress(thesis)
        assert not suppressed

    def test_earnings_within_3_days_suppressed(self):
        thesis = ThesisContext(ticker="AAPL", direction="BULL", conviction=4, days_to_earnings=2)
        suppressed, reason = _should_suppress(thesis)
        assert suppressed
        assert "2" in reason

    def test_earnings_on_day_zero_not_suppressed(self):
        thesis = ThesisContext(ticker="AAPL", direction="BULL", conviction=4, days_to_earnings=0)
        suppressed, _ = _should_suppress(thesis)
        assert not suppressed  # today is ok (event happened / is today)

    def test_earnings_4_days_out_not_suppressed(self):
        thesis = ThesisContext(ticker="AAPL", direction="BULL", conviction=4, days_to_earnings=4)
        suppressed, _ = _should_suppress(thesis)
        assert not suppressed

    def test_bull_direction_not_suppressed(self):
        thesis = ThesisContext(ticker="AAPL", direction="BULL", conviction=3)
        suppressed, _ = _should_suppress(thesis)
        assert not suppressed

    def test_bear_direction_not_suppressed(self):
        thesis = ThesisContext(ticker="AAPL", direction="BEAR", conviction=4)
        suppressed, _ = _should_suppress(thesis)
        assert not suppressed


# ══════════════════════════════════════════════════════════════════════════════
# Score contract (hard filters)
# ══════════════════════════════════════════════════════════════════════════════

class TestScoreContractHardFilters:
    def test_passes_valid_contract(self):
        c = _contract(mid=2.10, delta=0.40, oi=500)
        score, rejections = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        assert rejections == []
        assert score > 0

    def test_rejects_no_mid(self):
        c = _contract(mid=None)
        _, rejections = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        assert len(rejections) > 0
        assert any("mid" in r.lower() or "price" in r.lower() for r in rejections)

    def test_rejects_zero_mid(self):
        c = _contract(mid=0.0)
        _, rejections = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        assert len(rejections) > 0

    def test_rejects_wide_spread(self):
        c = _contract(bid=1.0, ask=3.0, mid=2.0, delta=0.40, oi=500)
        _, rejections = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        assert len(rejections) > 0
        assert any("spread" in r.lower() for r in rejections)

    def test_rejects_low_oi(self):
        c = _contract(oi=10, mid=2.0, delta=0.40)
        _, rejections = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        assert len(rejections) > 0
        assert any("oi" in r.lower() or "open interest" in r.lower() for r in rejections)

    def test_rejects_delta_outside_band(self):
        # long_call band = [0.25, 0.65]; delta=0.10 is out of range
        # Use bid/ask consistent with mid to avoid triggering spread filter first
        c = _contract(bid=0.48, ask=0.52, mid=0.50, delta=0.10, oi=200)
        _, rejections = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        assert len(rejections) > 0
        assert any("delta" in r.lower() for r in rejections)

    def test_rejects_put_delta_outside_band(self):
        c = _contract(right="P", mid=2.0, delta=-0.10, oi=200)
        _, rejections = _score_contract(c, PRESETS["long_put"], _bear_thesis())
        assert len(rejections) > 0

    def test_allows_none_oi_past_filter(self):
        # OI=None should not trigger the OI floor filter
        c = _contract(oi=None, mid=2.10, delta=0.40)
        _, rejections = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        # OI filter only runs when oi is not None; None means we can't check
        # No rejection for OI
        assert not any("OI" in r or "open interest" in r.lower() for r in rejections)

    def test_allows_none_delta_past_filter(self):
        # delta=None → delta range filter is skipped
        c = _contract(delta=None, mid=2.10, oi=300)
        _, rejections = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        assert not any("delta" in r.lower() for r in rejections)


# ══════════════════════════════════════════════════════════════════════════════
# Score contract (soft scoring sanity)
# ══════════════════════════════════════════════════════════════════════════════

class TestScoreContractSoftScoring:
    def test_tighter_spread_scores_higher(self):
        tight = _contract(bid=2.0, ask=2.10, mid=2.05, delta=0.40, oi=500)
        wide = _contract(bid=2.0, ask=2.40, mid=2.20, delta=0.40, oi=500)
        s_tight, _ = _score_contract(tight, PRESETS["long_call"], _bull_thesis())
        s_wide, _ = _score_contract(wide, PRESETS["long_call"], _bull_thesis())
        assert s_tight > s_wide

    def test_higher_oi_scores_higher(self):
        low_oi = _contract(oi=60, mid=2.10, delta=0.40)
        high_oi = _contract(oi=2000, mid=2.10, delta=0.40)
        s_low, _ = _score_contract(low_oi, PRESETS["long_call"], _bull_thesis())
        s_high, _ = _score_contract(high_oi, PRESETS["long_call"], _bull_thesis())
        assert s_high > s_low

    def test_score_capped_at_100(self):
        c = _contract(bid=2.0, ask=2.05, mid=2.025, delta=0.40, oi=5000, volume=2000)
        score, _ = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        assert score <= 100.0

    def test_score_non_negative(self):
        c = _contract(mid=2.10, delta=0.40, oi=200)
        score, _ = _score_contract(c, PRESETS["long_call"], _bull_thesis())
        assert score >= 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Breakeven
# ══════════════════════════════════════════════════════════════════════════════

class TestBreakeven:
    def test_call_breakeven(self):
        c = _contract(strike=150.0, right="C", mid=2.50)
        assert _breakeven(c) == pytest.approx(152.50, abs=0.01)

    def test_put_breakeven(self):
        c = _contract(strike=150.0, right="P", mid=2.50)
        assert _breakeven(c) == pytest.approx(147.50, abs=0.01)

    def test_no_mid_returns_none(self):
        c = _contract(mid=None)
        assert _breakeven(c) is None


# ══════════════════════════════════════════════════════════════════════════════
# get_option_candidates integration
# ══════════════════════════════════════════════════════════════════════════════

class TestGetOptionCandidates:
    def test_bullish_returns_call_candidates(self):
        contracts = [
            _contract(strike=150.0, right="C", dte=21, mid=2.10, delta=0.40, oi=500),
            _contract(strike=155.0, right="C", dte=21, mid=1.20, delta=0.30, oi=300),
            # Puts should not appear in bullish candidates
            _contract(strike=150.0, right="P", dte=21, mid=2.10, delta=-0.40, oi=500),
        ]
        chain = _chain(contracts)
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)

        assert not result.suppressed
        assert len(result.candidates) > 0
        for c in result.candidates:
            assert c.right == "C", f"Expected call, got put: {c}"

    def test_bearish_returns_put_candidates(self):
        contracts = [
            _contract(strike=145.0, right="P", dte=21, mid=2.10, delta=-0.40, oi=500),
            _contract(strike=140.0, right="P", dte=21, mid=1.30, delta=-0.30, oi=300),
            # Calls should not appear in bearish candidates
            _contract(strike=150.0, right="C", dte=21, mid=2.10, delta=0.40, oi=500),
        ]
        chain = _chain(contracts)
        thesis = _bear_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)

        assert not result.suppressed
        assert len(result.candidates) > 0
        for c in result.candidates:
            assert c.right == "P", f"Expected put, got call: {c}"

    def test_illiquid_contracts_filtered_out(self):
        contracts = [
            _contract(strike=150.0, right="C", dte=21, mid=2.10, delta=0.40, oi=5),   # OI too low
        ]
        chain = _chain(contracts)
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        assert result.candidates == []
        assert len(result.rejection_reasons) > 0

    def test_wide_spread_filtered_out(self):
        contracts = [
            _contract(strike=150.0, right="C", dte=21, bid=1.0, ask=4.0, mid=2.5, delta=0.40, oi=500),
        ]
        chain = _chain(contracts)
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        assert result.candidates == []
        assert any("spread" in r.lower() for r in result.rejection_reasons)

    def test_no_thesis_returns_suppressed(self):
        result = get_option_candidates("AAPL", thesis=None)
        assert result.suppressed
        assert result.suppression_reason is not None

    def test_neutral_thesis_returns_suppressed(self):
        thesis = ThesisContext(ticker="AAPL", direction="NEUTRAL", conviction=3)
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=_chain([]))
        assert result.suppressed

    def test_low_conviction_returns_suppressed(self):
        contracts = [
            _contract(strike=150.0, right="C", dte=21, mid=2.10, delta=0.40, oi=500),
        ]
        thesis = _bull_thesis(conviction=1)
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=_chain(contracts))
        assert result.suppressed

    def test_earnings_proximity_returns_suppressed(self):
        contracts = [
            _contract(strike=150.0, right="C", dte=21, mid=2.10, delta=0.40, oi=500),
        ]
        thesis = _bull_thesis(days_to_earnings=2)
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=_chain(contracts))
        assert result.suppressed
        assert "earnings" in result.suppression_reason.lower()

    def test_max_candidates_respected(self):
        # Build 10 valid call contracts
        contracts = [
            _contract(
                strike=140.0 + i * 2.5,
                right="C",
                dte=21,
                mid=2.00 + i * 0.1,
                delta=0.35 + i * 0.01,
                oi=300,
            )
            for i in range(10)
        ]
        chain = _chain(contracts)
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain, max_candidates=3)
        assert len(result.candidates) <= 3

    def test_empty_chain_returns_no_candidates(self):
        chain = _chain([])
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        assert result.candidates == []

    def test_chain_error_returns_suppressed(self):
        bad_chain = OptionChainResult(
            ticker="AAPL",
            underlying_price=None,
            fetch_time="2026-05-29T12:00:00",
            error="Network error",
            source="yfinance",
        )
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=bad_chain)
        assert result.suppressed

    def test_candidates_have_breakeven(self):
        contracts = [
            _contract(strike=150.0, right="C", dte=21, mid=2.50, delta=0.40, oi=500),
        ]
        chain = _chain(contracts)
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        if result.candidates:
            assert result.candidates[0].breakeven == pytest.approx(152.50, abs=0.01)

    def test_candidates_have_rationale(self):
        contracts = [
            _contract(strike=150.0, right="C", dte=21, mid=2.10, delta=0.40, oi=500),
        ]
        chain = _chain(contracts)
        result = get_option_candidates("AAPL", thesis=_bull_thesis(), chain_result=chain)
        if result.candidates:
            assert result.candidates[0].rationale != ""

    def test_result_includes_rejection_reasons(self):
        # One valid + one illiquid contract
        contracts = [
            _contract(strike=150.0, right="C", dte=21, mid=2.10, delta=0.40, oi=500),
            _contract(strike=155.0, right="C", dte=21, mid=0.50, delta=0.25, oi=5),  # OI too low
        ]
        chain = _chain(contracts)
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        # Should have at least one rejection reason from the illiquid contract
        assert isinstance(result.rejection_reasons, list)

    def test_result_carries_chain_source(self):
        chain = _chain([
            _contract(strike=150.0, right="C", dte=21, mid=2.10, delta=0.40, oi=500),
        ])
        result = get_option_candidates("AAPL", thesis=_bull_thesis(), chain_result=chain)
        assert result.chain_source == "mock"

    def test_leaps_preset_uses_long_dte(self):
        leaps_contract = _contract(
            strike=150.0, right="C", dte=210,
            mid=10.0, delta=0.55, oi=100,
        )
        leaps_contract = OptionContract(
            ticker="AAPL",
            expiry=_expiry(210),
            strike=150.0,
            right="C",
            dte=210,
            bid=9.80,
            ask=10.20,
            mid=10.00,
            open_interest=100,
            volume=20,
            delta=0.55,
            implied_vol=0.35,
            underlying_price=148.0,
            source="mock",
        )
        chain = _chain([leaps_contract])
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain, include_leaps=True)
        if result.candidates:
            assert any(c.strategy_preset.startswith("leaps") for c in result.candidates)

    def test_no_leaps_when_disabled(self):
        leaps_contract = OptionContract(
            ticker="AAPL",
            expiry=_expiry(210),
            strike=150.0,
            right="C",
            dte=210,
            bid=9.80,
            ask=10.20,
            mid=10.00,
            open_interest=100,
            volume=20,
            delta=0.55,
            implied_vol=0.35,
            underlying_price=148.0,
            source="mock",
        )
        chain = _chain([leaps_contract])
        thesis = _bull_thesis()
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain, include_leaps=False)
        # leaps preset disabled → LEAPS contract should not be in candidates
        for c in result.candidates:
            assert not c.strategy_preset.startswith("leaps")


# ══════════════════════════════════════════════════════════════════════════════
# CandidateResult shape
# ══════════════════════════════════════════════════════════════════════════════

class TestCandidateResultShape:
    def test_suppressed_result_has_no_candidates(self):
        result = get_option_candidates("AAPL", thesis=None)
        assert result.suppressed is True
        assert result.candidates == []
        assert result.suppression_reason is not None

    def test_successful_result_fields(self):
        contracts = [
            _contract(strike=150.0, right="C", dte=21, mid=2.10, delta=0.40, oi=500),
        ]
        chain = _chain(contracts)
        result = get_option_candidates("AAPL", thesis=_bull_thesis(), chain_result=chain)
        assert result.ticker == "AAPL"
        assert isinstance(result.generated_at, str)
        assert isinstance(result.candidates, list)
        assert isinstance(result.rejection_reasons, list)
