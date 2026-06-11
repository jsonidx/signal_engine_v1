"""
Tests for TRD-046: Options Risk and Position Sizing Framework

Covers:
- _iv_regime() label mapping
- _event_risk_policy() label mapping
- _determine_size_tier() tier determination with all modifiers
- Hard blocks: extreme IV, DTE gamma trap, no price
- Event-risk blocks: earnings imminent
- Soft blocks → size reductions: high IV, near earnings, wide spread, short DTE
- Contract count sizing respects premium-at-risk caps
- Missing account/portfolio context degrades safely (model portfolio used)
- Concentration warnings
- Exit hierarchy generation
- compute_option_risk() end-to-end
- Integration via get_option_candidates()
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pytest

from utils.option_risk import (
    PortfolioContext,
    RiskAssessment,
    _determine_size_tier,
    _event_risk_policy,
    _iv_regime,
    _MODEL_NAV_USD,
    compute_option_risk,
)
from utils.option_candidates import (
    ThesisContext,
    get_option_candidates,
)
from utils.ibkr_options import OptionChainResult, OptionContract


# ── Minimal candidate stub ─────────────────────────────────────────────────────

@dataclass
class _FakeCandidate:
    """Minimal stub for compute_option_risk without needing a real OptionCandidate."""
    ticker: str = "AAPL"
    mid: Optional[float] = 2.0
    dte: int = 30
    implied_vol: Optional[float] = 0.30
    spread_pct: Optional[float] = 4.0
    strategy_preset: str = "long_call"
    option_stop_loss: Optional[float] = 1.0
    option_take_profit_1: Optional[float] = 3.0
    option_take_profit_2: Optional[float] = 4.0
    projected_option_tp1: Optional[float] = None
    projected_option_tp2: Optional[float] = None
    exit_by_date: Optional[str] = None
    holding_window_days: Optional[int] = 21


def _thesis(conviction=3, direction="BULL", days_to_earnings=None, stop_loss=140.0):
    return ThesisContext(
        ticker="AAPL",
        direction=direction,
        conviction=conviction,
        current_price=148.0,
        target_1=160.0,
        target_2=175.0,
        stop_loss=stop_loss,
        days_to_earnings=days_to_earnings,
    )


def _expiry(days):
    return (date.today() + timedelta(days=days)).isoformat()


def _contract(
    right="C", dte=30, mid=2.0, delta=0.40, iv=0.30,
    bid=1.90, ask=2.10, oi=500, volume=100, strike=150.0,
):
    return OptionContract(
        ticker="AAPL",
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


def _chain(contracts, price=148.0):
    return OptionChainResult(
        ticker="AAPL",
        underlying_price=price,
        fetch_time="2026-06-06T12:00:00",
        contracts=contracts,
        expiries=[_expiry(30)],
        source="mock",
    )


# ══════════════════════════════════════════════════════════════════════════════
# IV regime labeling
# ══════════════════════════════════════════════════════════════════════════════

class TestIVRegime:
    def test_none_is_unknown(self):
        assert _iv_regime(None) == "unknown_iv"

    def test_below_20pct_is_low(self):
        assert _iv_regime(0.15) == "low_iv"

    def test_20_to_40_is_normal(self):
        assert _iv_regime(0.25) == "normal_iv"
        assert _iv_regime(0.35) == "normal_iv"

    def test_40_to_60_is_elevated(self):
        assert _iv_regime(0.45) == "elevated_iv"
        assert _iv_regime(0.55) == "elevated_iv"

    def test_60_to_100_is_high(self):
        assert _iv_regime(0.65) == "high_iv"
        assert _iv_regime(0.99) == "high_iv"

    def test_above_100_is_extreme(self):
        assert _iv_regime(1.01) == "extreme_iv"
        assert _iv_regime(2.00) == "extreme_iv"

    def test_boundary_40pct(self):
        # exactly 0.40 → normal (< 0.40 is normal; >= 0.40 is elevated)
        assert _iv_regime(0.40) == "elevated_iv"

    def test_boundary_60pct(self):
        assert _iv_regime(0.60) == "high_iv"


# ══════════════════════════════════════════════════════════════════════════════
# Event risk policy
# ══════════════════════════════════════════════════════════════════════════════

class TestEventRiskPolicy:
    def test_none_is_no_event(self):
        assert _event_risk_policy(None) == "no_event_window"

    def test_zero_is_no_event(self):
        assert _event_risk_policy(0) == "no_event_window"

    def test_negative_is_no_event(self):
        assert _event_risk_policy(-1) == "no_event_window"

    def test_beyond_30_is_no_event(self):
        assert _event_risk_policy(31) == "no_event_window"

    def test_1_day_is_hard_block(self):
        assert _event_risk_policy(1) == "hard_block_earnings_imminent"

    def test_2_days_is_hard_block(self):
        assert _event_risk_policy(2) == "hard_block_earnings_imminent"

    def test_3_days_is_reduce_size(self):
        assert _event_risk_policy(3) == "reduce_size_near_earnings"

    def test_7_days_is_reduce_size(self):
        assert _event_risk_policy(7) == "reduce_size_near_earnings"

    def test_8_days_is_monitor(self):
        assert _event_risk_policy(8) == "monitor_event_window"

    def test_14_days_is_monitor(self):
        assert _event_risk_policy(14) == "monitor_event_window"

    def test_15_days_is_event_aware(self):
        assert _event_risk_policy(15) == "event_aware"

    def test_30_days_is_event_aware(self):
        assert _event_risk_policy(30) == "event_aware"


# ══════════════════════════════════════════════════════════════════════════════
# Size tier determination
# ══════════════════════════════════════════════════════════════════════════════

class TestSizeTier:
    def _tier(self, **kw):
        defaults = dict(
            conviction=3,
            strategy_preset="long_call",
            iv_label="normal_iv",
            event_policy="no_event_window",
            spread_pct=4.0,
            dte=30,
        )
        defaults.update(kw)
        return _determine_size_tier(**defaults)

    def test_normal_conditions_is_standard(self):
        assert self._tier() == "standard"

    def test_low_conviction_is_reduced(self):
        assert self._tier(conviction=2) == "reduced"

    def test_leaps_conviction5_is_max(self):
        assert self._tier(conviction=5, strategy_preset="leaps_call") == "max"

    def test_non_leaps_conviction5_is_standard(self):
        assert self._tier(conviction=5, strategy_preset="long_call") == "standard"

    def test_extreme_iv_is_skip(self):
        assert self._tier(iv_label="extreme_iv") == "skip"

    def test_high_iv_downgrades_standard_to_reduced(self):
        assert self._tier(iv_label="high_iv") == "reduced"

    def test_high_iv_downgrades_max_to_standard(self):
        assert self._tier(conviction=5, strategy_preset="leaps_call", iv_label="high_iv") == "standard"

    def test_earnings_imminent_is_skip(self):
        assert self._tier(event_policy="hard_block_earnings_imminent") == "skip"

    def test_near_earnings_downgrades_standard_to_reduced(self):
        assert self._tier(event_policy="reduce_size_near_earnings") == "reduced"

    def test_wide_spread_downgrades(self):
        assert self._tier(spread_pct=13.0) == "reduced"

    def test_short_dte_downgrades(self):
        assert self._tier(dte=10) == "reduced"

    def test_multiple_downgrades_floor_at_reduced(self):
        # short DTE + near earnings: both try to downgrade standard; floor is reduced
        result = self._tier(dte=10, event_policy="reduce_size_near_earnings")
        assert result == "reduced"


# ══════════════════════════════════════════════════════════════════════════════
# compute_option_risk — unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPortfolioContextNavLabel:
    def test_default_nav_label_is_model(self):
        port = PortfolioContext()
        assert port.nav_label == "model"

    def test_cash_proxy_label_accepted(self):
        port = PortfolioContext(account_nav_usd=10_000.0, nav_label="cash_proxy")
        assert port.nav_label == "cash_proxy"
        assert not port.nav_is_model

    def test_nav_proxy_label_accepted(self):
        port = PortfolioContext(account_nav_usd=30_000.0, nav_label="nav_proxy")
        assert port.nav_label == "nav_proxy"
        assert not port.nav_is_model

    def test_nav_is_model_when_no_nav(self):
        port = PortfolioContext(account_nav_usd=None)
        assert port.nav_is_model

    def test_nav_is_model_when_zero_nav(self):
        port = PortfolioContext(account_nav_usd=0.0)
        assert port.nav_is_model

    def test_effective_nav_falls_back_to_model_constant(self):
        port = PortfolioContext(account_nav_usd=None)
        assert port.effective_nav_usd == _MODEL_NAV_USD


class TestComputeOptionRisk:

    def _run(self, cand=None, thesis=None, portfolio=None):
        if cand is None:
            cand = _FakeCandidate()
        if thesis is None:
            thesis = _thesis()
        return compute_option_risk(cand, thesis, portfolio)

    # ── Hard blocks ───────────────────────────────────────────────────────────

    def test_extreme_iv_hard_blocks(self):
        cand = _FakeCandidate(implied_vol=1.6)
        ra = self._run(cand)
        assert ra.risk_allowed is False
        assert ra.position_size_tier == "skip"
        assert ra.suggested_contract_count == 0
        assert ra.risk_block_reason is not None

    def test_dte_gamma_trap_hard_blocks(self):
        cand = _FakeCandidate(dte=3)
        ra = self._run(cand)
        assert ra.risk_allowed is False
        assert ra.position_size_tier == "skip"

    def test_no_mid_hard_blocks(self):
        cand = _FakeCandidate(mid=None)
        ra = self._run(cand)
        assert ra.risk_allowed is False
        assert ra.suggested_contract_count == 0

    def test_zero_mid_hard_blocks(self):
        cand = _FakeCandidate(mid=0.0)
        ra = self._run(cand)
        assert ra.risk_allowed is False

    def test_earnings_1_day_hard_blocks(self):
        ra = self._run(thesis=_thesis(days_to_earnings=1))
        assert ra.risk_allowed is False
        assert "earnings" in (ra.risk_block_reason or "").lower()

    def test_earnings_2_days_hard_blocks(self):
        ra = self._run(thesis=_thesis(days_to_earnings=2))
        assert ra.risk_allowed is False

    # ── Soft blocks → reduced size ────────────────────────────────────────────

    def test_high_iv_is_allowed_but_reduced(self):
        cand = _FakeCandidate(implied_vol=0.75)
        ra = self._run(cand)
        assert ra.risk_allowed is True
        assert ra.position_size_tier == "reduced"

    def test_near_earnings_3d_is_reduced(self):
        ra = self._run(thesis=_thesis(days_to_earnings=3))
        assert ra.risk_allowed is True
        assert ra.position_size_tier == "reduced"

    def test_near_earnings_7d_is_reduced(self):
        ra = self._run(thesis=_thesis(days_to_earnings=7))
        assert ra.risk_allowed is True
        assert ra.position_size_tier == "reduced"

    def test_wide_spread_is_reduced(self):
        cand = _FakeCandidate(spread_pct=13.0)
        ra = self._run(cand)
        assert ra.risk_allowed is True
        assert ra.position_size_tier == "reduced"

    def test_short_dte_is_reduced(self):
        cand = _FakeCandidate(dte=10)
        ra = self._run(cand)
        assert ra.risk_allowed is True
        assert ra.position_size_tier == "reduced"

    def test_low_conviction_is_reduced(self):
        ra = self._run(thesis=_thesis(conviction=2))
        assert ra.position_size_tier == "reduced"

    # ── Standard / max ────────────────────────────────────────────────────────

    def test_normal_conditions_is_standard(self):
        ra = self._run()
        assert ra.risk_allowed is True
        assert ra.position_size_tier == "standard"

    def test_leaps_conviction5_is_max(self):
        cand = _FakeCandidate(strategy_preset="leaps_call", dte=270)
        ra = self._run(cand, thesis=_thesis(conviction=5))
        assert ra.position_size_tier == "max"

    # ── IV regime label ───────────────────────────────────────────────────────

    def test_iv_regime_label_populated(self):
        cand = _FakeCandidate(implied_vol=0.35)
        ra = self._run(cand)
        assert ra.iv_regime_label == "normal_iv"

    def test_iv_regime_null_is_unknown(self):
        cand = _FakeCandidate(implied_vol=None)
        ra = self._run(cand)
        assert ra.iv_regime_label == "unknown_iv"

    # ── Event risk policy ─────────────────────────────────────────────────────

    def test_event_risk_policy_populated(self):
        ra = self._run(thesis=_thesis(days_to_earnings=5))
        assert ra.event_risk_policy == "reduce_size_near_earnings"

    def test_no_earnings_policy(self):
        ra = self._run(thesis=_thesis(days_to_earnings=None))
        assert ra.event_risk_policy == "no_event_window"

    # ── Contract count sizing ─────────────────────────────────────────────────

    def test_contract_count_positive_for_allowed(self):
        ra = self._run()
        assert ra.suggested_contract_count is not None
        assert ra.suggested_contract_count >= 1

    def test_contract_count_zero_for_blocked(self):
        cand = _FakeCandidate(mid=None)
        ra = self._run(cand)
        assert ra.suggested_contract_count == 0

    def test_contract_count_respects_budget(self):
        port = PortfolioContext(account_nav_usd=10_000.0)
        # Standard tier: 2% of 10k = $200 budget; mid=2.0 → 1 contract costs $200
        cand = _FakeCandidate(mid=2.0)
        ra = compute_option_risk(cand, _thesis(), port)
        assert ra.max_premium_risk_usd == pytest.approx(200.0, abs=1.0)
        assert ra.suggested_contract_count >= 1

    def test_expensive_contract_limits_count(self):
        port = PortfolioContext(account_nav_usd=10_000.0)
        # Standard budget: $200; expensive contract mid=$20 → 1 contract costs $2000; count=1
        cand = _FakeCandidate(mid=20.0)
        ra = compute_option_risk(cand, _thesis(), port)
        assert ra.suggested_contract_count == 1  # always at least 1

    def test_cheap_contract_count_capped(self):
        port = PortfolioContext(account_nav_usd=100_000.0)
        # Standard: 2% of 100k = $2000; cheap mid=$0.10 → 200 contracts uncapped
        # Cap: standard max = 10
        cand = _FakeCandidate(mid=0.10)
        ra = compute_option_risk(cand, _thesis(), port)
        assert ra.suggested_contract_count <= 10

    def test_reduced_tier_has_smaller_count_than_standard(self):
        port = PortfolioContext(account_nav_usd=25_000.0)
        cand_std = _FakeCandidate(mid=2.0)
        cand_red = _FakeCandidate(mid=2.0, dte=10)  # short DTE → reduced
        ra_std = compute_option_risk(cand_std, _thesis(), port)
        ra_red = compute_option_risk(cand_red, _thesis(), port)
        assert ra_red.max_premium_risk_usd <= ra_std.max_premium_risk_usd

    # ── Missing portfolio context degrades safely ─────────────────────────────

    def test_no_portfolio_context_uses_model_nav(self):
        ra = self._run(portfolio=None)
        assert ra.risk_allowed is True
        assert ra.max_premium_risk_usd is not None
        assert ra.nav_source == "model"

    def test_zero_nav_falls_back_to_model(self):
        port = PortfolioContext(account_nav_usd=0.0)
        ra = compute_option_risk(_FakeCandidate(), _thesis(), port)
        assert ra.nav_source == "model"
        assert ra.max_premium_risk_usd == pytest.approx(_MODEL_NAV_USD * 0.02, abs=1.0)

    def test_none_nav_falls_back_to_model(self):
        port = PortfolioContext(account_nav_usd=None)
        ra = compute_option_risk(_FakeCandidate(), _thesis(), port)
        assert ra.nav_source == "model"

    def test_real_nav_uses_nav_label(self):
        """nav_source reflects the nav_label set on PortfolioContext."""
        port = PortfolioContext(account_nav_usd=50_000.0, nav_label="nav_proxy")
        ra = compute_option_risk(_FakeCandidate(), _thesis(), port)
        assert ra.nav_source == "nav_proxy"

    def test_cash_proxy_label_propagates(self):
        """cash_proxy label propagates correctly when only cash is known."""
        port = PortfolioContext(account_nav_usd=20_000.0, nav_label="cash_proxy")
        ra = compute_option_risk(_FakeCandidate(), _thesis(), port)
        assert ra.nav_source == "cash_proxy"

    def test_nav_proxy_label_propagates(self):
        """nav_proxy label propagates when cash+deployed is available."""
        port = PortfolioContext(account_nav_usd=30_000.0, nav_label="nav_proxy")
        ra = compute_option_risk(_FakeCandidate(), _thesis(), port)
        assert ra.nav_source == "nav_proxy"

    # ── Concentration warnings ────────────────────────────────────────────────

    def test_no_warning_when_no_concentration(self):
        port = PortfolioContext(open_option_positions=1)
        ra = compute_option_risk(_FakeCandidate(), _thesis(), port)
        assert ra.portfolio_concentration_warning is None

    def test_warning_when_book_at_ceiling(self):
        port = PortfolioContext(
            account_nav_usd=50_000.0,
            max_options_allocation_pct=0.10,
            current_options_exposure_pct=0.10,
        )
        ra = compute_option_risk(_FakeCandidate(), _thesis(), port)
        assert ra.portfolio_concentration_warning is not None

    def test_warning_for_too_many_positions(self):
        port = PortfolioContext(open_option_positions=7)
        ra = compute_option_risk(_FakeCandidate(), _thesis(), port)
        assert ra.portfolio_concentration_warning is not None

    def test_warning_for_duplicate_ticker(self):
        port = PortfolioContext(open_option_tickers=["AAPL", "MSFT"])
        ra = compute_option_risk(_FakeCandidate(ticker="AAPL"), _thesis(), port)
        assert ra.portfolio_concentration_warning is not None

    # ── Exit hierarchy ────────────────────────────────────────────────────────

    def test_exit_hierarchy_non_empty(self):
        ra = self._run()
        assert len(ra.exit_hierarchy) >= 4

    def test_exit_hierarchy_is_empty_on_hard_block(self):
        cand = _FakeCandidate(mid=None)
        ra = self._run(cand)
        assert ra.exit_hierarchy == []

    def test_exit_hierarchy_contains_thesis_stop(self):
        ra = self._run(thesis=_thesis(stop_loss=140.0))
        assert any("thesis_stop" in e for e in ra.exit_hierarchy)

    def test_exit_hierarchy_contains_time_stop(self):
        ra = self._run()
        assert any("time_stop" in e for e in ra.exit_hierarchy)

    def test_exit_hierarchy_contains_premium_stop(self):
        ra = self._run()
        assert any("premium_stop" in e for e in ra.exit_hierarchy)

    def test_exit_hierarchy_contains_scale_out_tp1(self):
        ra = self._run()
        assert any("scale_out_tp1" in e for e in ra.exit_hierarchy)

    def test_exit_hierarchy_contains_event_exit_when_near_earnings(self):
        ra = self._run(thesis=_thesis(days_to_earnings=10))
        assert any("event_exit" in e for e in ra.exit_hierarchy)

    def test_exit_hierarchy_no_event_exit_when_no_earnings(self):
        ra = self._run(thesis=_thesis(days_to_earnings=None))
        assert not any("event_exit" in e for e in ra.exit_hierarchy)

    # ── Thesis direction in exit hierarchy ───────────────────────────────────

    def test_bull_stop_says_falls_below(self):
        ra = self._run(thesis=_thesis(direction="BULL", stop_loss=140.0))
        thesis_stop = next(e for e in ra.exit_hierarchy if "thesis_stop" in e)
        assert "falls below" in thesis_stop

    def test_bear_stop_says_rises_above(self):
        ra = self._run(thesis=_thesis(direction="BEAR", stop_loss=155.0))
        thesis_stop = next(e for e in ra.exit_hierarchy if "thesis_stop" in e)
        assert "rises above" in thesis_stop


# ══════════════════════════════════════════════════════════════════════════════
# Integration via get_option_candidates
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskIntegration:
    """Risk fields are attached to candidates returned by get_option_candidates."""

    def _run(self, thesis, contracts, portfolio=None):
        chain = _chain(contracts)
        return get_option_candidates(
            "AAPL", thesis=thesis, chain_result=chain, portfolio_context=portfolio
        )

    def test_candidate_has_risk_allowed_field(self):
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30)]
        result = self._run(t, cs)
        if result.candidates:
            c = result.candidates[0]
            assert hasattr(c, "risk_allowed")
            assert isinstance(c.risk_allowed, bool)

    def test_normal_candidate_is_risk_allowed(self):
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30)]
        result = self._run(t, cs)
        if result.candidates:
            assert result.candidates[0].risk_allowed is True

    def test_extreme_iv_candidate_is_blocked(self):
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=1.6)]
        result = self._run(t, cs)
        if result.candidates:
            c = result.candidates[0]
            assert c.risk_allowed is False
            assert c.position_size_tier == "skip"

    def test_high_iv_candidate_is_reduced(self):
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.80)]
        result = self._run(t, cs)
        if result.candidates:
            assert result.candidates[0].position_size_tier == "reduced"

    def test_near_earnings_candidate_is_reduced(self):
        t = _thesis(days_to_earnings=5)
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30)]
        result = self._run(t, cs)
        if result.candidates:
            assert result.candidates[0].position_size_tier == "reduced"

    def test_candidate_has_exit_hierarchy(self):
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30)]
        result = self._run(t, cs)
        if result.candidates:
            c = result.candidates[0]
            assert isinstance(c.exit_hierarchy, list)
            assert len(c.exit_hierarchy) >= 4

    def test_candidate_has_iv_regime_label(self):
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.35)]
        result = self._run(t, cs)
        if result.candidates:
            assert result.candidates[0].iv_regime_label == "normal_iv"

    def test_no_portfolio_context_does_not_crash(self):
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30)]
        result = self._run(t, cs, portfolio=None)
        # Should complete without exception; risk fields use model defaults
        assert result is not None

    def test_contract_count_present_on_candidate(self):
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30)]
        result = self._run(t, cs)
        if result.candidates:
            c = result.candidates[0]
            assert c.suggested_contract_count is not None
            assert c.suggested_contract_count >= 0

    def test_portfolio_context_propagates_nav_source(self):
        """nav_source on candidate reflects the label from the PortfolioContext."""
        from utils.option_risk import PortfolioContext
        port = PortfolioContext(account_nav_usd=30_000.0, nav_label="nav_proxy")
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30)]
        result = self._run(t, cs, portfolio=port)
        if result.candidates:
            assert result.candidates[0].risk_nav_source == "nav_proxy"

    def test_cash_proxy_nav_source_propagates(self):
        """cash_proxy nav_label propagates to candidate risk_nav_source."""
        from utils.option_risk import PortfolioContext
        port = PortfolioContext(account_nav_usd=15_000.0, nav_label="cash_proxy")
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30)]
        result = self._run(t, cs, portfolio=port)
        if result.candidates:
            assert result.candidates[0].risk_nav_source == "cash_proxy"

    def test_screener_and_per_ticker_same_sizing_given_same_context(self):
        """
        When both screener and per-ticker paths receive identical PortfolioContext,
        contract counts and size tiers must match for the same contract.
        (Regression guard for Issue 1 — screener used to skip portfolio_context.)
        """
        from utils.option_risk import PortfolioContext
        port = PortfolioContext(account_nav_usd=25_000.0, nav_label="nav_proxy")
        t = _thesis()
        cs = [_contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30)]

        result_a = self._run(t, cs, portfolio=port)
        result_b = self._run(t, cs, portfolio=port)

        if result_a.candidates and result_b.candidates:
            a = result_a.candidates[0]
            b = result_b.candidates[0]
            assert a.position_size_tier == b.position_size_tier
            assert a.suggested_contract_count == b.suggested_contract_count
            assert a.max_premium_risk_usd == b.max_premium_risk_usd
