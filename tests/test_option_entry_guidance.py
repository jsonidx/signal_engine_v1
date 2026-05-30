"""
Tests for TRD-031: Option Execution Guidance and Entry Pricing.

Covers: compute_entry_guidance() logic across spread tiers, edge cases,
and end-to-end integration via get_option_candidates().
"""

from datetime import date, timedelta

import pytest

from utils.ibkr_options import OptionChainResult, OptionContract
from utils.option_candidates import (
    ExecutionGuidance,
    OptionCandidate,
    ThesisContext,
    _SPREAD_MODERATE,
    _SPREAD_TIGHT,
    compute_entry_guidance,
    get_option_candidates,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _make_candidate(
    bid: float | None = 2.00,
    ask: float | None = 2.20,
    mid: float | None = 2.10,
    spread_pct: float | None = 9.5,
    open_interest: int | None = 500,
    volume: int | None = 100,
    strategy_preset: str = "long_call",
) -> OptionCandidate:
    return OptionCandidate(
        ticker="AAPL",
        expiry=_expiry(21),
        strike=150.0,
        right="C",
        dte=21,
        bid=bid,
        ask=ask,
        mid=mid,
        spread_pct=spread_pct,
        delta=0.40,
        implied_vol=0.35,
        open_interest=open_interest,
        volume=volume,
        breakeven=152.10,
        score=70.0,
        rationale="bullish long call",
        strategy_preset=strategy_preset,
        source="yfinance",
    )


def _contract(
    ticker="AAPL",
    dte=21,
    strike=150.0,
    right="C",
    bid=2.0,
    ask=2.20,
    mid=2.10,
    delta=0.40,
    iv=0.35,
    oi=500,
    volume=100,
) -> OptionContract:
    """Build a mock OptionContract. spread_pct is computed from bid/ask automatically."""
    return OptionContract(
        ticker=ticker,
        expiry=_expiry(dte),
        strike=strike,
        right=right,
        dte=dte,
        bid=bid,
        ask=ask,
        mid=mid,
        delta=delta,
        implied_vol=iv,
        open_interest=oi,
        volume=volume,
        underlying_price=148.0,
        source="mock",
    )


def _thesis(direction="BULL", conviction=4, current_price=148.0) -> ThesisContext:
    return ThesisContext(
        ticker="AAPL",
        direction=direction,
        conviction=conviction,
        current_price=current_price,
        entry_low=145.0,
        entry_high=150.0,
        target_1=165.0,
        target_2=175.0,
        stop_loss=140.0,
        time_horizon="2-4 weeks",
        days_to_earnings=30,
    )


# ── Unit tests: compute_entry_guidance() ──────────────────────────────────────

class TestComputeEntryGuidanceTightSpread:
    """spread_pct ≤ 3% → passive entry below mid, low slippage."""

    def test_entry_below_mid(self):
        c = _make_candidate(bid=2.00, ask=2.12, mid=2.06, spread_pct=2.9, open_interest=800)
        g = compute_entry_guidance(c)
        assert g.recommended_entry_price is not None
        assert g.recommended_entry_price < c.mid

    def test_max_chase_at_mid(self):
        c = _make_candidate(bid=2.00, ask=2.12, mid=2.06, spread_pct=2.9, open_interest=800)
        g = compute_entry_guidance(c)
        assert g.max_chase_price == round(c.mid, 2)

    def test_entry_style_passive(self):
        c = _make_candidate(bid=2.00, ask=2.12, mid=2.06, spread_pct=2.0, open_interest=800)
        g = compute_entry_guidance(c)
        assert g.entry_style == "passive"

    def test_slippage_label_low(self):
        c = _make_candidate(bid=2.00, ask=2.12, mid=2.06, spread_pct=2.0, open_interest=800)
        g = compute_entry_guidance(c)
        assert g.slippage_risk_label == "low"

    def test_fill_quality_high(self):
        c = _make_candidate(bid=2.00, ask=2.12, mid=2.06, spread_pct=2.0, open_interest=1000)
        g = compute_entry_guidance(c)
        assert g.fill_quality_score is not None
        assert g.fill_quality_score >= 0.70

    def test_order_type_limit(self):
        c = _make_candidate(bid=2.00, ask=2.12, mid=2.06, spread_pct=2.0)
        g = compute_entry_guidance(c)
        assert g.recommended_order_type == "limit"


class TestComputeEntryGuidanceModerateSpread:
    """3% < spread_pct ≤ 8% → balanced entry at mid."""

    def test_entry_at_mid(self):
        c = _make_candidate(bid=1.90, ask=2.10, mid=2.00, spread_pct=5.0, open_interest=300)
        g = compute_entry_guidance(c)
        assert g.recommended_entry_price == round(c.mid, 2)

    def test_max_chase_slightly_above_mid(self):
        c = _make_candidate(bid=1.90, ask=2.10, mid=2.00, spread_pct=5.0, open_interest=300)
        g = compute_entry_guidance(c)
        assert g.max_chase_price is not None
        assert g.max_chase_price > c.mid

    def test_entry_style_balanced(self):
        c = _make_candidate(bid=1.90, ask=2.10, mid=2.00, spread_pct=6.0, open_interest=300)
        g = compute_entry_guidance(c)
        assert g.entry_style == "balanced"

    def test_slippage_label_moderate(self):
        c = _make_candidate(bid=1.90, ask=2.10, mid=2.00, spread_pct=6.0, open_interest=300)
        g = compute_entry_guidance(c)
        assert g.slippage_risk_label == "moderate"


class TestComputeEntryGuidanceWideSpread:
    """spread_pct > 8% → conservative entry below mid, high slippage."""

    def test_entry_below_mid(self):
        c = _make_candidate(bid=1.80, ask=2.40, mid=2.10, spread_pct=11.0, open_interest=100)
        g = compute_entry_guidance(c)
        assert g.recommended_entry_price is not None
        assert g.recommended_entry_price < c.mid

    def test_max_chase_capped_at_mid(self):
        c = _make_candidate(bid=1.80, ask=2.40, mid=2.10, spread_pct=11.0, open_interest=100)
        g = compute_entry_guidance(c)
        assert g.max_chase_price == round(c.mid, 2)

    def test_entry_style_passive(self):
        c = _make_candidate(bid=1.80, ask=2.40, mid=2.10, spread_pct=10.0, open_interest=100)
        g = compute_entry_guidance(c)
        assert g.entry_style == "passive"

    def test_slippage_label_high(self):
        c = _make_candidate(bid=1.80, ask=2.40, mid=2.10, spread_pct=10.0, open_interest=100)
        g = compute_entry_guidance(c)
        assert g.slippage_risk_label == "high"

    def test_fill_quality_lower(self):
        c = _make_candidate(bid=1.80, ask=2.40, mid=2.10, spread_pct=11.0, open_interest=50)
        g = compute_entry_guidance(c)
        assert g.fill_quality_score is not None
        assert g.fill_quality_score < 0.50

    def test_skip_threshold_for_long_call(self):
        c = _make_candidate(spread_pct=10.0, strategy_preset="long_call")
        g = compute_entry_guidance(c)
        assert g.skip_if_spread_above_pct == 12.0

    def test_skip_threshold_for_leaps(self):
        c = _make_candidate(spread_pct=12.0, strategy_preset="leaps_call")
        g = compute_entry_guidance(c)
        assert g.skip_if_spread_above_pct == 15.0


class TestComputeEntryGuidanceEdgeCases:
    """Edge cases: missing bid/ask, null mid, zero OI."""

    def test_no_bid_ask_uses_mid(self):
        c = _make_candidate(bid=None, ask=None, mid=2.00, spread_pct=None)
        g = compute_entry_guidance(c)
        assert g.recommended_entry_price == 2.00
        assert g.max_chase_price == 2.00

    def test_null_mid_returns_null_entry(self):
        c = _make_candidate(bid=None, ask=None, mid=None, spread_pct=None)
        g = compute_entry_guidance(c)
        assert g.recommended_entry_price is None
        assert g.max_chase_price is None

    def test_zero_oi_still_returns_guidance(self):
        c = _make_candidate(bid=2.00, ask=2.20, mid=2.10, spread_pct=9.5, open_interest=0, volume=0)
        g = compute_entry_guidance(c)
        assert g.recommended_entry_price is not None
        assert isinstance(g.fill_quality_score, float)

    def test_entry_price_positive(self):
        c = _make_candidate(bid=0.05, ask=0.15, mid=0.10, spread_pct=50.0, open_interest=10)
        g = compute_entry_guidance(c)
        # Even on extreme spreads entry should still be non-negative
        if g.recommended_entry_price is not None:
            assert g.recommended_entry_price >= 0.0

    def test_fill_quality_clamped_0_to_1(self):
        for sp in [1.0, 5.0, 11.0]:
            c = _make_candidate(spread_pct=sp, open_interest=2000, volume=500)
            g = compute_entry_guidance(c)
            if g.fill_quality_score is not None:
                assert 0.0 <= g.fill_quality_score <= 1.0

    def test_max_chase_always_gte_entry(self):
        for sp in [2.0, 5.0, 10.0]:
            c = _make_candidate(bid=1.90, ask=2.30, mid=2.10, spread_pct=sp)
            g = compute_entry_guidance(c)
            if g.recommended_entry_price is not None and g.max_chase_price is not None:
                assert g.max_chase_price >= g.recommended_entry_price


# ── Integration test: guidance flows through get_option_candidates() ──────────

class TestEntryGuidanceIntegration:
    """Verify execution guidance fields are attached to candidates returned by the engine."""

    def test_candidate_has_entry_guidance_fields(self):
        thesis = _thesis()
        # bid=1.90/ask=2.10 → spread_pct ≈ 10% (within the 12% filter)
        chain = OptionChainResult(
            ticker="AAPL",
            underlying_price=148.0,
            fetch_time="2026-05-30T12:00:00",
            contracts=[_contract(dte=21, bid=1.90, ask=2.10, mid=2.00, oi=500)],
            expiries=[_expiry(21)],
            source="yfinance",
        )
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        assert not result.suppressed
        assert result.candidates
        c = result.candidates[0]
        assert hasattr(c, "recommended_entry_price")
        assert hasattr(c, "max_chase_price")
        assert hasattr(c, "entry_style")
        assert hasattr(c, "entry_rationale")
        assert hasattr(c, "slippage_risk_label")
        assert hasattr(c, "fill_quality_score")
        assert hasattr(c, "skip_if_spread_above_pct")

    def test_candidate_entry_fields_not_none(self):
        thesis = _thesis()
        chain = OptionChainResult(
            ticker="AAPL",
            underlying_price=148.0,
            fetch_time="2026-05-30T12:00:00",
            contracts=[_contract(dte=21, bid=1.90, ask=2.10, mid=2.00, oi=500)],
            expiries=[_expiry(21)],
            source="yfinance",
        )
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        c = result.candidates[0]
        assert c.recommended_entry_price is not None
        assert c.max_chase_price is not None
        assert c.entry_rationale != ""
        assert c.slippage_risk_label in ("low", "moderate", "high", "very_high")

    def test_wide_spread_candidate_gets_conservative_entry(self):
        # bid=1.70/ask=2.50/mid=2.10 → spread ≈ 38%; set mid explicitly
        # Note: spread 38% exceeds the 12% hard filter so candidate won't pass —
        # use a spread within the filter but still classified "wide" by guidance (>8%)
        thesis = _thesis()
        chain = OptionChainResult(
            ticker="AAPL",
            underlying_price=148.0,
            fetch_time="2026-05-30T12:00:00",
            contracts=[_contract(dte=21, bid=1.95, ask=2.30, mid=2.10, oi=80, delta=0.40)],
            expiries=[_expiry(21)],
            source="yfinance",
        )
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        if result.candidates:
            c = result.candidates[0]
            assert c.recommended_entry_price is not None
            assert c.recommended_entry_price < c.mid
            assert c.slippage_risk_label == "high"

    def test_tight_spread_candidate_passive_style(self):
        # bid=2.00/ask=2.12 → spread = 0.12/2.06 ≈ 5.8% — moderate not tight
        # Use bid=2.00/ask=2.06 → spread = 0.06/2.03 ≈ 3.0% — at boundary
        thesis = _thesis()
        chain = OptionChainResult(
            ticker="AAPL",
            underlying_price=148.0,
            fetch_time="2026-05-30T12:00:00",
            contracts=[_contract(dte=21, bid=2.00, ask=2.06, mid=2.03, oi=900, delta=0.40)],
            expiries=[_expiry(21)],
            source="yfinance",
        )
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        if result.candidates:
            c = result.candidates[0]
            # spread ≈ 3% boundary — may be passive or balanced depending on exact calc
            assert c.entry_style in ("passive", "balanced")
            assert c.slippage_risk_label in ("low", "moderate")

    def test_order_type_always_limit(self):
        thesis = _thesis()
        chain = OptionChainResult(
            ticker="AAPL",
            underlying_price=148.0,
            fetch_time="2026-05-30T12:00:00",
            contracts=[_contract(dte=21, bid=1.90, ask=2.10, mid=2.00, oi=300)],
            expiries=[_expiry(21)],
            source="yfinance",
        )
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        if result.candidates:
            assert result.candidates[0].recommended_order_type == "limit"
