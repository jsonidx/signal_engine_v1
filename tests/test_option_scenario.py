"""
Tests for TRD-047: Option Scenario Engine and Path Analysis

Covers:
- _theta_cost(): square-root decay math, boundary cases
- _underlying_move_for_scenario(): each scenario type, missing inputs
- compute_scenario_set(): five scenarios produced, path-dependent ordering,
    short-DTE penalty, adverse/gap safety, missing inputs, all input combos
- scenario_set_to_dicts(): serialization roundtrip
- Integration via get_option_candidates(): scenarios field on candidates

Key behavioral requirements verified:
- Fast target hit outperforms slow target hit (same underlying, less theta)
- Sideways penalizes short-DTE contracts more than long-DTE
- Adverse and gap scenarios degrade safely with missing inputs
- Missing delta / mid / current_price produce "insufficient_inputs" method
- All five scenario IDs always present in output
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from utils.option_scenario import (
    ScenarioProjection,
    ScenarioSet,
    _TV_FRACTION,
    _SCENARIO_VERSION,
    _theta_cost,
    _underlying_move_for_scenario,
    compute_scenario_set,
    scenario_set_to_dicts,
)
from utils.option_candidates import ThesisContext, get_option_candidates
from utils.ibkr_options import OptionChainResult, OptionContract


# ── Helpers ────────────────────────────────────────────────────────────────────

def _expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _contract(right="C", dte=30, mid=2.0, delta=0.40, iv=0.30,
              bid=1.90, ask=2.10, oi=500, volume=100, strike=150.0):
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


def _thesis(**kw):
    defaults = dict(
        ticker="AAPL",
        direction="BULL",
        conviction=3,
        current_price=148.0,
        target_1=160.0,
        target_2=172.0,
        stop_loss=140.0,
        time_horizon="2-4 weeks",
    )
    defaults.update(kw)
    return ThesisContext(**defaults)


def _ss(mid=2.0, delta=0.40, current=148.0, t1=160.0, t2=172.0, stop=140.0,
        dte=30, holding=15) -> ScenarioSet:
    return compute_scenario_set(mid, delta, current, t1, t2, stop, dte, holding)


def _scenario(ss: ScenarioSet, sid: str) -> ScenarioProjection:
    for s in ss.scenarios:
        if s.scenario_id == sid:
            return s
    raise KeyError(f"Scenario '{sid}' not found in set")


# ══════════════════════════════════════════════════════════════════════════════
# _theta_cost
# ══════════════════════════════════════════════════════════════════════════════

class TestThetaCost:

    def test_no_days_elapsed_zero_cost(self):
        assert _theta_cost(2.0, 30, 0) == 0.0

    def test_zero_dte_zero_cost(self):
        assert _theta_cost(2.0, 0, 10) == 0.0

    def test_negative_days_zero_cost(self):
        assert _theta_cost(2.0, 30, -5) == 0.0

    def test_full_dte_elapsed_consumes_tv_fraction(self):
        mid = 2.0
        tc = _theta_cost(mid, 30, 30)
        assert tc == pytest.approx(mid * _TV_FRACTION, rel=0.01)

    def test_half_dte_less_than_full(self):
        tc_full = _theta_cost(2.0, 30, 30)
        tc_half = _theta_cost(2.0, 30, 15)
        assert tc_half < tc_full

    def test_square_root_convexity(self):
        """Theta decay is convex — second half costs more than first half."""
        tc_first_half = _theta_cost(2.0, 30, 15)
        tc_full = _theta_cost(2.0, 30, 30)
        tc_second_half = tc_full - tc_first_half
        assert tc_second_half > tc_first_half

    def test_short_dte_higher_cost_per_day(self):
        """Short-DTE contract pays more theta per day for the same elapsed fraction."""
        # 10-DTE: 5 days elapsed = 50% through
        tc_short = _theta_cost(2.0, 10, 5)
        # 45-DTE: 22.5 days elapsed = 50% through
        tc_long = _theta_cost(2.0, 45, 22)
        # Both 50% through, but theta costs should be identical (same fraction)
        # The path-dependence shows when we use the same absolute days
        # 5 days on 10-DTE vs 5 days on 45-DTE
        tc_short_abs = _theta_cost(2.0, 10, 5)   # 50% of short DTE
        tc_long_abs = _theta_cost(2.0, 45, 5)    # 11% of long DTE
        assert tc_short_abs > tc_long_abs

    def test_theta_cost_never_exceeds_tv(self):
        """Theta cost cannot exceed TV_FRACTION × mid."""
        tc = _theta_cost(2.0, 30, 100)   # days >> dte
        assert tc <= 2.0 * _TV_FRACTION + 0.001

    def test_proportional_to_mid(self):
        """Doubling mid doubles theta cost."""
        tc1 = _theta_cost(2.0, 30, 15)
        tc2 = _theta_cost(4.0, 30, 15)
        assert tc2 == pytest.approx(2.0 * tc1, rel=0.01)


# ══════════════════════════════════════════════════════════════════════════════
# _underlying_move_for_scenario
# ══════════════════════════════════════════════════════════════════════════════

class TestUnderlyingMove:

    def test_fast_target_uses_target_1(self):
        move = _underlying_move_for_scenario("fast_target", 148.0, 160.0, 172.0, 140.0)
        assert move == pytest.approx(12.0)

    def test_slow_target_uses_target_1(self):
        move = _underlying_move_for_scenario("slow_target", 148.0, 160.0, 172.0, 140.0)
        assert move == pytest.approx(12.0)

    def test_sideways_is_zero(self):
        move = _underlying_move_for_scenario("sideways_decay", 148.0, 160.0, 172.0, 140.0)
        assert move == pytest.approx(0.0)

    def test_adverse_uses_stop_loss(self):
        move = _underlying_move_for_scenario("adverse_stop", 148.0, 160.0, 172.0, 140.0)
        assert move == pytest.approx(-8.0)   # 140 - 148 = -8

    def test_gap_uses_target_2_when_available(self):
        move = _underlying_move_for_scenario("gap_overshoot", 148.0, 160.0, 172.0, 140.0)
        assert move == pytest.approx(24.0)   # 172 - 148

    def test_gap_falls_back_to_150pct_of_t1_move(self):
        move = _underlying_move_for_scenario("gap_overshoot", 148.0, 160.0, None, 140.0)
        # 1.5 × (160 - 148) = 18
        assert move == pytest.approx(18.0)

    def test_fast_target_no_target_1_returns_none(self):
        assert _underlying_move_for_scenario("fast_target", 148.0, None, 172.0, 140.0) is None

    def test_adverse_no_stop_returns_none(self):
        assert _underlying_move_for_scenario("adverse_stop", 148.0, 160.0, 172.0, None) is None

    def test_gap_no_t1_no_t2_returns_none(self):
        assert _underlying_move_for_scenario("gap_overshoot", 148.0, None, None, 140.0) is None

    def test_no_current_price_returns_none(self):
        assert _underlying_move_for_scenario("fast_target", None, 160.0, 172.0, 140.0) is None

    def test_zero_current_price_returns_none(self):
        assert _underlying_move_for_scenario("fast_target", 0.0, 160.0, 172.0, 140.0) is None

    def test_bear_adverse_is_positive_move(self):
        """For a bear thesis, stop is above current price → positive move."""
        move = _underlying_move_for_scenario("adverse_stop", 148.0, 135.0, 120.0, 158.0)
        assert move == pytest.approx(10.0)   # 158 - 148 = +10 (adverse for bear)


# ══════════════════════════════════════════════════════════════════════════════
# compute_scenario_set — structural
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeScenarioSetStructure:

    def test_always_five_scenarios(self):
        ss = _ss()
        assert len(ss.scenarios) == 5

    def test_all_five_ids_present(self):
        ss = _ss()
        ids = {s.scenario_id for s in ss.scenarios}
        assert ids == {"fast_target", "slow_target", "sideways_decay", "adverse_stop", "gap_overshoot"}

    def test_scenario_set_version(self):
        ss = _ss()
        assert ss.scenario_engine_version == _SCENARIO_VERSION

    def test_entry_mid_stored(self):
        ss = _ss(mid=2.5)
        assert ss.entry_mid == pytest.approx(2.5)

    def test_base_dte_stored(self):
        ss = _ss(dte=45)
        assert ss.base_dte == 45

    def test_holding_days_stored(self):
        ss = _ss(holding=21)
        assert ss.holding_days == 21

    def test_order_matches_spec(self):
        """Scenarios should appear in the canonical order defined in _SCENARIO_SPECS."""
        ss = _ss()
        expected = ["fast_target", "slow_target", "sideways_decay", "adverse_stop", "gap_overshoot"]
        assert [s.scenario_id for s in ss.scenarios] == expected


# ══════════════════════════════════════════════════════════════════════════════
# compute_scenario_set — path-dependence requirements
# ══════════════════════════════════════════════════════════════════════════════

class TestPathDependence:

    def test_fast_target_outperforms_slow_target(self):
        """Same underlying move, less time → better option return."""
        ss = _ss(mid=2.0, delta=0.40, current=148.0, t1=160.0, dte=30, holding=15)
        fast = _scenario(ss, "fast_target")
        slow = _scenario(ss, "slow_target")
        assert fast.projected_return_pct is not None
        assert slow.projected_return_pct is not None
        assert fast.projected_return_pct > slow.projected_return_pct

    def test_fast_target_has_lower_theta_cost(self):
        ss = _ss(dte=30, holding=15)
        fast = _scenario(ss, "fast_target")
        slow = _scenario(ss, "slow_target")
        assert fast.theta_cost < slow.theta_cost

    def test_gap_overshoot_has_best_return(self):
        """Gap (large move, short time) should yield the highest projected return."""
        ss = _ss(mid=2.0, delta=0.40, current=148.0, t1=160.0, t2=172.0, dte=30, holding=15)
        gap = _scenario(ss, "gap_overshoot")
        fast = _scenario(ss, "fast_target")
        assert gap.projected_return_pct > fast.projected_return_pct

    def test_adverse_stop_produces_loss(self):
        """Stop-loss scenario should show negative return for a bull call."""
        ss = _ss(mid=2.0, delta=0.40, current=148.0, stop=140.0)
        adv = _scenario(ss, "adverse_stop")
        assert adv.projected_return_pct is not None
        assert adv.projected_return_pct < 0.0

    def test_sideways_produces_loss(self):
        ss = _ss(mid=2.0, delta=0.40)
        side = _scenario(ss, "sideways_decay")
        assert side.projected_return_pct is not None
        assert side.projected_return_pct < 0.0

    def test_sideways_return_only_theta_driven(self):
        """Sideways: underlying move = 0, so projected_option = mid − theta_cost."""
        ss = _ss(mid=2.0, delta=0.40)
        side = _scenario(ss, "sideways_decay")
        assert side.underlying_move == pytest.approx(0.0)
        if side.theta_cost is not None and side.projected_option_price is not None:
            expected = max(0.01, 2.0 - side.theta_cost)
            assert side.projected_option_price == pytest.approx(expected, abs=0.01)

    def test_fast_target_fewer_days_than_slow(self):
        ss = _ss(holding=15)
        fast = _scenario(ss, "fast_target")
        slow = _scenario(ss, "slow_target")
        assert fast.days_to_resolution < slow.days_to_resolution

    def test_adverse_mid_point_timing(self):
        ss = _ss(holding=20)
        adv = _scenario(ss, "adverse_stop")
        # 50% of 20 days = 10 days
        assert adv.days_to_resolution == pytest.approx(10, abs=1)

    def test_gap_fastest_resolution(self):
        ss = _ss(holding=20)
        gap = _scenario(ss, "gap_overshoot")
        fast = _scenario(ss, "fast_target")
        assert gap.days_to_resolution < fast.days_to_resolution

    # ── Short-DTE penalty in sideways ────────────────────────────────────────

    def test_short_dte_sideways_worse_than_long_dte(self):
        """
        Short-DTE contract should lose more (%) in sideways decay than long-DTE
        for the same underlying parameters, because theta accelerates near expiry.
        """
        ss_short = _ss(mid=2.0, delta=0.40, dte=14, holding=7)
        ss_long  = _ss(mid=2.0, delta=0.40, dte=45, holding=22)

        side_short = _scenario(ss_short, "sideways_decay")
        side_long  = _scenario(ss_long,  "sideways_decay")

        assert side_short.projected_return_pct is not None
        assert side_long.projected_return_pct is not None
        # Short-DTE loses a higher % (larger negative return)
        assert side_short.projected_return_pct < side_long.projected_return_pct

    def test_very_short_dte_sideways_near_total_loss(self):
        """7-DTE option held for 5 days sideways should be near-worthless."""
        ss = _ss(mid=2.0, delta=0.40, dte=7, holding=5)
        side = _scenario(ss, "sideways_decay")
        # Should lose a large fraction of value
        assert side.projected_return_pct < -30.0

    def test_leaps_dte_sideways_minimal_loss(self):
        """210-DTE option over 21-day holding: theta cost should be small."""
        ss = _ss(mid=10.0, delta=0.50, dte=210, holding=21)
        side = _scenario(ss, "sideways_decay")
        # 21 days / 210 DTE = 10% through lifetime; sqrt-decay → moderate theta
        assert side.projected_return_pct is not None
        # Should not lose more than 50% in sideways over 21 days for LEAPS
        assert side.projected_return_pct > -50.0

    # ── Bear (put) scenarios ──────────────────────────────────────────────────

    def test_bear_fast_target_hit_gains(self):
        """For bear puts, target_1 < current_price; fast hit should be profitable."""
        ss = compute_scenario_set(
            mid=2.0, delta=-0.40, current_price=148.0,
            target_1=136.0, target_2=124.0, stop_loss=156.0,
            dte=30, holding_days=15,
        )
        fast = _scenario(ss, "fast_target")
        assert fast.projected_return_pct is not None
        assert fast.projected_return_pct > 0.0

    def test_bear_adverse_stop_produces_loss(self):
        """For bear puts, stop is above current; adverse stop is a loss."""
        ss = compute_scenario_set(
            mid=2.0, delta=-0.40, current_price=148.0,
            target_1=136.0, target_2=124.0, stop_loss=156.0,
            dte=30, holding_days=15,
        )
        adv = _scenario(ss, "adverse_stop")
        assert adv.projected_return_pct < 0.0


# ══════════════════════════════════════════════════════════════════════════════
# compute_scenario_set — missing inputs / safety
# ══════════════════════════════════════════════════════════════════════════════

class TestMissingInputs:

    def test_none_mid_all_insufficient(self):
        ss = compute_scenario_set(None, 0.40, 148.0, 160.0, 172.0, 140.0, 30, 15)
        for s in ss.scenarios:
            assert s.input_method == "insufficient_inputs"
            assert s.projected_option_price is None
            assert s.projected_return_pct is None

    def test_zero_mid_all_insufficient(self):
        ss = compute_scenario_set(0.0, 0.40, 148.0, 160.0, 172.0, 140.0, 30, 15)
        for s in ss.scenarios:
            assert s.input_method == "insufficient_inputs"

    def test_none_delta_all_insufficient(self):
        ss = compute_scenario_set(2.0, None, 148.0, 160.0, 172.0, 140.0, 30, 15)
        for s in ss.scenarios:
            assert s.input_method == "insufficient_inputs"

    def test_none_current_price_all_insufficient(self):
        ss = compute_scenario_set(2.0, 0.40, None, 160.0, 172.0, 140.0, 30, 15)
        for s in ss.scenarios:
            assert s.input_method == "insufficient_inputs"

    def test_none_target_1_fast_slow_insufficient(self):
        """fast_target and slow_target need target_1; others should still compute."""
        ss = compute_scenario_set(2.0, 0.40, 148.0, None, 172.0, 140.0, 30, 15)
        fast = _scenario(ss, "fast_target")
        slow = _scenario(ss, "slow_target")
        side = _scenario(ss, "sideways_decay")
        adv  = _scenario(ss, "adverse_stop")
        gap  = _scenario(ss, "gap_overshoot")

        assert fast.input_method == "insufficient_inputs"
        assert slow.input_method == "insufficient_inputs"
        assert side.input_method == "delta_theta"       # no move needed
        assert adv.input_method == "delta_theta"        # uses stop_loss
        assert gap.input_method == "delta_theta"        # falls back to 1.5×t1 move — but t1 None
        # gap without t1 and with t2 should compute from t2
        assert gap.projected_option_price is not None

    def test_none_stop_adverse_insufficient(self):
        ss = compute_scenario_set(2.0, 0.40, 148.0, 160.0, 172.0, None, 30, 15)
        adv = _scenario(ss, "adverse_stop")
        assert adv.input_method == "insufficient_inputs"

    def test_none_t2_gap_falls_back_to_t1(self):
        ss = compute_scenario_set(2.0, 0.40, 148.0, 160.0, None, 140.0, 30, 15)
        gap = _scenario(ss, "gap_overshoot")
        assert gap.input_method == "delta_theta"
        # move = 1.5 × (160 - 148) = 18
        assert gap.underlying_move == pytest.approx(18.0, abs=0.01)

    def test_always_returns_five_scenarios_even_with_all_none(self):
        ss = compute_scenario_set(None, None, None, None, None, None, 0, 1)
        assert len(ss.scenarios) == 5
        for s in ss.scenarios:
            assert s.input_method == "insufficient_inputs"

    def test_projected_price_always_positive(self):
        """No projected price should go to 0 or negative."""
        ss = _ss()
        for s in ss.scenarios:
            if s.projected_option_price is not None:
                assert s.projected_option_price > 0.0

    def test_dte_at_resolution_non_negative(self):
        ss = _ss(dte=7, holding=14)  # holding > dte
        for s in ss.scenarios:
            assert s.dte_at_resolution >= 0

    def test_holding_days_zero_clamped_to_one(self):
        """holding_days=0 must be clamped; must not raise or divide-by-zero."""
        ss = compute_scenario_set(2.0, 0.40, 148.0, 160.0, 172.0, 140.0, 30, 0)
        assert len(ss.scenarios) == 5
        for s in ss.scenarios:
            assert s.days_to_resolution >= 1


# ══════════════════════════════════════════════════════════════════════════════
# scenario_set_to_dicts — serialization
# ══════════════════════════════════════════════════════════════════════════════

class TestSerialisation:
    EXPECTED_KEYS = {
        "scenario_id", "scenario_label", "underlying_move", "underlying_move_pct",
        "days_to_resolution", "dte_at_resolution", "projected_option_price",
        "projected_return_pct", "theta_cost", "scenario_weight_label",
        "exit_guidance", "input_method",
    }

    def test_returns_list_of_dicts(self):
        ss = _ss()
        result = scenario_set_to_dicts(ss)
        assert isinstance(result, list)
        assert len(result) == 5
        for d in result:
            assert isinstance(d, dict)

    def test_all_expected_keys_present(self):
        ss = _ss()
        for d in scenario_set_to_dicts(ss):
            assert self.EXPECTED_KEYS <= set(d.keys()), (
                f"Missing keys in {d['scenario_id']}: {self.EXPECTED_KEYS - set(d.keys())}"
            )

    def test_ids_round_trip(self):
        ss = _ss()
        dicts = scenario_set_to_dicts(ss)
        ids = [d["scenario_id"] for d in dicts]
        assert ids == ["fast_target", "slow_target", "sideways_decay", "adverse_stop", "gap_overshoot"]

    def test_no_dataclass_instances_in_output(self):
        """Output must be plain dicts — no ScenarioProjection objects."""
        ss = _ss()
        for d in scenario_set_to_dicts(ss):
            assert not isinstance(d, ScenarioProjection)

    def test_insufficient_inputs_serialise_with_none_values(self):
        ss = compute_scenario_set(None, None, None, None, None, None, 30, 15)
        for d in scenario_set_to_dicts(ss):
            assert d["projected_option_price"] is None
            assert d["projected_return_pct"] is None


# ══════════════════════════════════════════════════════════════════════════════
# Integration via get_option_candidates
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration:

    def _run(self, thesis, contracts):
        chain = _chain(contracts)
        return get_option_candidates("AAPL", thesis=thesis, chain_result=chain)

    def test_scenarios_present_on_candidates(self):
        t = _thesis()
        contracts = [_contract(right="C", dte=30, mid=2.0, delta=0.40)]
        result = self._run(t, contracts)
        assert len(result.candidates) >= 1
        c = result.candidates[0]
        assert hasattr(c, "scenarios")
        assert isinstance(c.scenarios, list)
        assert len(c.scenarios) == 5

    def test_scenario_ids_on_candidate(self):
        t = _thesis()
        contracts = [_contract(right="C", dte=30, mid=2.0, delta=0.40)]
        result = self._run(t, contracts)
        c = result.candidates[0]
        ids = [s["scenario_id"] for s in c.scenarios]
        assert set(ids) == {
            "fast_target", "slow_target", "sideways_decay",
            "adverse_stop", "gap_overshoot",
        }

    def test_fast_outperforms_slow_on_candidate(self):
        t = _thesis(current_price=148.0, target_1=160.0)
        contracts = [_contract(right="C", dte=30, mid=2.0, delta=0.40)]
        result = self._run(t, contracts)
        c = result.candidates[0]
        sc_map = {s["scenario_id"]: s for s in c.scenarios}
        fast_ret = sc_map["fast_target"]["projected_return_pct"]
        slow_ret = sc_map["slow_target"]["projected_return_pct"]
        assert fast_ret is not None and slow_ret is not None
        assert fast_ret > slow_ret

    def test_scenarios_empty_list_when_suppressed(self):
        """Suppressed result has no candidates; nothing to test for scenarios."""
        t = ThesisContext(ticker="AAPL", direction="NEUTRAL", conviction=3)
        contracts = [_contract()]
        result = self._run(t, contracts)
        assert result.suppressed is True
        assert result.candidates == []

    def test_scenario_dicts_are_serialisable(self):
        """All scenario values must be JSON-safe (no custom objects)."""
        import json
        t = _thesis()
        contracts = [_contract(right="C", dte=30, mid=2.0, delta=0.40)]
        result = self._run(t, contracts)
        c = result.candidates[0]
        # Should not raise
        json.dumps(c.scenarios)

    def test_no_current_price_scenarios_degrade_gracefully(self):
        """Thesis with no current_price → scenarios present but insufficient_inputs."""
        t = _thesis(current_price=None)
        contracts = [_contract(right="C", dte=30, mid=2.0, delta=0.40)]
        result = self._run(t, contracts)
        if result.candidates:
            c = result.candidates[0]
            for s in c.scenarios:
                assert s["input_method"] == "insufficient_inputs"
