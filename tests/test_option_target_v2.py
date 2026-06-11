"""
Tests for TRD-043: Option Target Engine v2

Covers:
- compute_target_projections() math: bullish, bearish, short-DTE, missing inputs
- _compute_exit_plan() propagates v2 fields onto candidates
- get_option_candidates() end-to-end: v2 fields present on returned candidates
- Nearer/farther underlying targets produce materially different projected levels
- Legacy flat-multiplier fields still present (backward compat)
"""

from datetime import date, timedelta

import pytest

from utils.option_candidates import (
    ThesisContext,
    compute_target_projections,
    get_option_candidates,
)
from utils.ibkr_options import OptionChainResult, OptionContract


# ── Helpers ────────────────────────────────────────────────────────────────────

def _expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _contract(
    ticker="AAPL",
    strike=150.0,
    right="C",
    dte=45,
    bid=2.0,
    ask=2.20,
    mid=2.10,
    delta=0.40,
    iv=0.30,
    oi=500,
    volume=100,
) -> OptionContract:
    return OptionContract(
        ticker=ticker,
        expiry=_expiry(dte),
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
        underlying_price=148.0,
        source="mock",
    )


def _bull_thesis(**kw) -> ThesisContext:
    defaults = dict(
        ticker="AAPL",
        direction="BULL",
        conviction=3,
        current_price=100.0,
        target_1=110.0,
        target_2=120.0,
        stop_loss=90.0,
    )
    defaults.update(kw)
    return ThesisContext(**defaults)


def _bear_thesis(**kw) -> ThesisContext:
    defaults = dict(
        ticker="AAPL",
        direction="BEAR",
        conviction=3,
        current_price=100.0,
        target_1=90.0,
        target_2=80.0,
        stop_loss=110.0,
    )
    defaults.update(kw)
    return ThesisContext(**defaults)


def _chain(contracts, price=100.0) -> OptionChainResult:
    return OptionChainResult(
        ticker="AAPL",
        underlying_price=price,
        fetch_time="2026-06-06T12:00:00",
        contracts=contracts,
        expiries=[_expiry(45)],
        source="mock",
    )


# ══════════════════════════════════════════════════════════════════════════════
# compute_target_projections — unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeTargetProjections:

    def test_bullish_call_tp1(self):
        """delta=0.40, move=+10 → projected gain 4.0 → tp1 = 2.0 + 4.0 = 6.0"""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=120.0, stop_loss=90.0, dte=45,
        )
        assert result["target_projection_method"] == "delta_only"
        assert result["projected_option_tp1"] == pytest.approx(6.0, abs=0.01)

    def test_bullish_call_tp2(self):
        """delta=0.40, move=+20 → projected gain 8.0 → tp2 = 2.0 + 8.0 = 10.0"""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=120.0, stop_loss=90.0, dte=45,
        )
        assert result["projected_option_tp2"] == pytest.approx(10.0, abs=0.01)

    def test_bullish_call_stop_projects_loss(self):
        """delta=0.40, move=−10 → gain −4.0 → stop = max(0.01, 2.0 − 4.0) = 0.01"""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=120.0, stop_loss=90.0, dte=45,
        )
        assert result["projected_option_stop"] == pytest.approx(0.01, abs=0.001)
        assert result["projected_stop_return_pct"] < 0

    def test_bearish_put_tp1(self):
        """delta=−0.40, move=−10 → gain = −0.40 × −10 = +4.0 → tp1 = 6.0"""
        result = compute_target_projections(
            mid=2.0, delta=-0.40, current_price=100.0,
            target_1=90.0, target_2=80.0, stop_loss=110.0, dte=45,
        )
        assert result["target_projection_method"] == "delta_only"
        assert result["projected_option_tp1"] == pytest.approx(6.0, abs=0.01)

    def test_bearish_put_stop_projects_loss(self):
        """delta=−0.40, move=+10 (adverse for bear) → gain −4.0 → stop floors at 0.01"""
        result = compute_target_projections(
            mid=2.0, delta=-0.40, current_price=100.0,
            target_1=90.0, target_2=80.0, stop_loss=110.0, dte=45,
        )
        assert result["projected_option_stop"] == pytest.approx(0.01, abs=0.001)
        assert result["projected_stop_return_pct"] < 0

    def test_return_pct_bullish_tp1(self):
        """Return % at tp1: (6 − 2) / 2 × 100 = 200%"""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=120.0, stop_loss=90.0, dte=45,
        )
        assert result["projected_tp1_return_pct"] == pytest.approx(200.0, abs=0.1)

    def test_tp2_larger_than_tp1_for_bull(self):
        """Farther target → larger projected option price."""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=120.0, stop_loss=90.0, dte=45,
        )
        assert result["projected_option_tp2"] > result["projected_option_tp1"]

    def test_farther_target_produces_larger_projection(self):
        """Closer target vs farther target: projected levels differ materially."""
        near = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=105.0, target_2=None, stop_loss=None, dte=45,
        )
        far = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=115.0, target_2=None, stop_loss=None, dte=45,
        )
        assert far["projected_option_tp1"] > near["projected_option_tp1"]

    def test_short_dte_uses_adjusted_method(self):
        """DTE=14 → method should be delta_dte_adjusted."""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=120.0, stop_loss=90.0, dte=14,
        )
        assert result["target_projection_method"] == "delta_dte_adjusted"

    def test_short_dte_projection_is_less_than_long_dte(self):
        """DTE haircut reduces projected gains vs no-haircut baseline."""
        long_dte = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=None, stop_loss=None, dte=45,
        )
        short_dte = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=None, stop_loss=None, dte=14,
        )
        assert short_dte["projected_option_tp1"] < long_dte["projected_option_tp1"]

    def test_dte_30_boundary_no_haircut(self):
        """Exactly DTE=30 → no haircut → delta_only."""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=None, stop_loss=None, dte=30,
        )
        assert result["target_projection_method"] == "delta_only"

    def test_dte_29_applies_haircut(self):
        """DTE=29 → haircut applies → delta_dte_adjusted."""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=None, stop_loss=None, dte=29,
        )
        assert result["target_projection_method"] == "delta_dte_adjusted"

    def test_missing_delta_returns_insufficient(self):
        result = compute_target_projections(
            mid=2.0, delta=None, current_price=100.0,
            target_1=110.0, target_2=120.0, stop_loss=90.0, dte=45,
        )
        assert result["target_projection_method"] == "insufficient_inputs"
        assert result["projected_option_tp1"] is None
        assert result["projected_option_tp2"] is None
        assert result["projected_option_stop"] is None

    def test_missing_current_price_returns_insufficient(self):
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=None,
            target_1=110.0, target_2=120.0, stop_loss=90.0, dte=45,
        )
        assert result["target_projection_method"] == "insufficient_inputs"

    def test_missing_mid_returns_insufficient(self):
        result = compute_target_projections(
            mid=None, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=120.0, stop_loss=90.0, dte=45,
        )
        assert result["target_projection_method"] == "insufficient_inputs"

    def test_zero_mid_returns_insufficient(self):
        result = compute_target_projections(
            mid=0.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=None, stop_loss=None, dte=45,
        )
        assert result["target_projection_method"] == "insufficient_inputs"

    def test_partial_targets_ok(self):
        """Only target_1 provided; tp2 and stop should be None but tp1 populated."""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=110.0, target_2=None, stop_loss=None, dte=45,
        )
        assert result["projected_option_tp1"] is not None
        assert result["projected_option_tp2"] is None
        assert result["projected_option_stop"] is None

    def test_projected_price_floored_at_one_cent(self):
        """Large adverse move → projected price floors at 0.01, never goes negative."""
        result = compute_target_projections(
            mid=0.50, delta=0.50, current_price=100.0,
            target_1=None, target_2=None, stop_loss=50.0, dte=45,
        )
        # stop move = 50 − 100 = −50; gain = 0.50 × −50 = −25; 0.50 − 25 < 0
        assert result["projected_option_stop"] == pytest.approx(0.01, abs=0.001)

    def test_target_already_near_current_price(self):
        """Very small move → small projected gain."""
        result = compute_target_projections(
            mid=2.0, delta=0.40, current_price=100.0,
            target_1=101.0, target_2=None, stop_loss=None, dte=45,
        )
        # gain = 0.40 × 1 = 0.40
        assert result["projected_option_tp1"] == pytest.approx(2.40, abs=0.01)


# ══════════════════════════════════════════════════════════════════════════════
# _compute_exit_plan propagation — via get_option_candidates end-to-end
# ══════════════════════════════════════════════════════════════════════════════

class TestExitPlanV2Propagation:
    """V2 fields are present on candidates returned by get_option_candidates."""

    def _run(self, thesis, contract):
        chain = _chain([contract], price=thesis.current_price or 100.0)
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        assert not result.suppressed, result.suppression_reason
        assert len(result.candidates) == 1
        return result.candidates[0]

    def test_bullish_candidate_has_projected_tp1(self):
        thesis = _bull_thesis(current_price=100.0, target_1=110.0)
        c = _contract(right="C", dte=45, mid=2.0, delta=0.40, strike=100.0)
        cand = self._run(thesis, c)
        assert cand.projected_option_tp1 is not None
        assert cand.projected_option_tp1 > cand.mid

    def test_bullish_candidate_stop_projects_below_mid(self):
        thesis = _bull_thesis(current_price=100.0, stop_loss=90.0)
        c = _contract(right="C", dte=45, mid=2.0, delta=0.40, strike=100.0)
        cand = self._run(thesis, c)
        # Stop is adverse; projected_stop should be < mid (or floor at 0.01)
        if cand.projected_option_stop is not None:
            assert cand.projected_option_stop <= cand.mid

    def test_bearish_candidate_has_projected_tp1(self):
        thesis = _bear_thesis(current_price=100.0, target_1=90.0)
        c = _contract(right="P", dte=45, mid=2.0, delta=-0.40, strike=100.0,
                      bid=1.90, ask=2.10)
        chain = _chain([c], price=100.0)
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        assert not result.suppressed
        if result.candidates:
            cand = result.candidates[0]
            assert cand.projected_option_tp1 is not None
            assert cand.projected_option_tp1 > cand.mid

    def test_projection_method_is_set(self):
        thesis = _bull_thesis(current_price=100.0)
        c = _contract(right="C", dte=45, mid=2.0, delta=0.40)
        cand = self._run(thesis, c)
        assert cand.target_projection_method in ("delta_only", "delta_dte_adjusted")

    def test_short_dte_uses_adjusted_method(self):
        thesis = _bull_thesis(current_price=100.0)
        c = _contract(right="C", dte=14, bid=1.90, ask=2.10, mid=2.0, delta=0.35)
        cand = self._run(thesis, c)
        assert cand.target_projection_method == "delta_dte_adjusted"

    def test_missing_delta_falls_back_to_insufficient(self):
        thesis = _bull_thesis(current_price=100.0)
        c = _contract(right="C", dte=45, mid=2.0, delta=None)
        # delta=None → hard filter skips delta check, contract passes
        chain = _chain([c], price=100.0)
        result = get_option_candidates("AAPL", thesis=thesis, chain_result=chain)
        if result.candidates:
            cand = result.candidates[0]
            assert cand.target_projection_method == "insufficient_inputs"
            assert cand.projected_option_tp1 is None

    def test_legacy_flat_fields_still_present(self):
        """Backward compat: old option_take_profit_1/2 and option_stop_loss still set."""
        thesis = _bull_thesis(current_price=100.0)
        c = _contract(right="C", dte=45, mid=2.0, delta=0.40)
        cand = self._run(thesis, c)
        assert cand.option_take_profit_1 == pytest.approx(2.0 * 1.50, abs=0.01)
        assert cand.option_take_profit_2 == pytest.approx(2.0 * 2.00, abs=0.01)
        assert cand.option_stop_loss == pytest.approx(2.0 * 0.50, abs=0.01)

    def test_v2_tp1_differs_from_legacy_tp1(self):
        """V2 thesis-linked projection should differ from the flat 1.5× multiplier."""
        thesis = _bull_thesis(current_price=100.0, target_1=110.0)
        c = _contract(right="C", dte=45, mid=2.0, delta=0.40)
        cand = self._run(thesis, c)
        # Legacy: 2.0 × 1.5 = 3.0; V2: 2.0 + 0.40×10 = 6.0
        assert cand.projected_option_tp1 != cand.option_take_profit_1
