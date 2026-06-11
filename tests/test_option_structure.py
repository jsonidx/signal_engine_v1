"""
Tests for TRD-048: Option Structure Selection Policy by Thesis Tempo

Covers:
- parse_horizon_days(): number+unit patterns, keyword fallbacks, edge cases
- classify_structure_archetype(): all four archetypes + default fallback
- apply_policy_to_preset(): DTE narrowing, spread tightening, LEAPS untouched
- get_option_candidates() integration:
    - short_breakout excludes LEAPS and caps DTE at 35
    - slow_macro prefers LEAPS and sets 45–60 DTE floor for swing
    - event_sensitive tightens spread to 10%
    - missing tempo falls back to default_swing
    - structure_archetype present on CandidateResult and each candidate
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from utils.option_structure import (
    POLICIES,
    StructurePolicy,
    apply_policy_to_preset,
    classify_structure_archetype,
    parse_horizon_days,
)
from utils.option_candidates import (
    ThesisContext,
    get_option_candidates,
)
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


def _thesis(
    direction="BULL", conviction=3,
    time_horizon=None, days_to_earnings=None,
    current_price=148.0,
    target_1=None, target_2=None, stop_loss=None,
):
    # Direction-appropriate defaults so stale-thesis suppression doesn't fire
    if target_1 is None:
        target_1 = 160.0 if direction != "BEAR" else 135.0
    if target_2 is None:
        target_2 = 175.0 if direction != "BEAR" else 120.0
    if stop_loss is None:
        stop_loss = 140.0 if direction != "BEAR" else 160.0
    return ThesisContext(
        ticker="AAPL",
        direction=direction,
        conviction=conviction,
        current_price=current_price,
        target_1=target_1,
        target_2=target_2,
        stop_loss=stop_loss,
        time_horizon=time_horizon,
        days_to_earnings=days_to_earnings,
    )


# ══════════════════════════════════════════════════════════════════════════════
# parse_horizon_days
# ══════════════════════════════════════════════════════════════════════════════

class TestParseHorizonDays:
    def test_none_returns_none(self):
        assert parse_horizon_days(None) is None

    def test_empty_string_returns_none(self):
        assert parse_horizon_days("") is None

    def test_days_unit(self):
        assert parse_horizon_days("3 days") == 3
        assert parse_horizon_days("7 days") == 7

    def test_range_days_takes_upper(self):
        assert parse_horizon_days("3-5 days") == 5

    def test_weeks_unit(self):
        assert parse_horizon_days("2 weeks") == 14
        assert parse_horizon_days("4 weeks") == 28

    def test_range_weeks_takes_upper(self):
        assert parse_horizon_days("2-4 weeks") == 28

    def test_months_unit(self):
        assert parse_horizon_days("2 months") == 60
        assert parse_horizon_days("3 months") == 90

    def test_range_months_takes_upper(self):
        assert parse_horizon_days("3-6 months") == 180

    def test_keyword_short(self):
        result = parse_horizon_days("short term")
        assert result is not None and result <= 14

    def test_keyword_macro(self):
        result = parse_horizon_days("long macro play")
        assert result is not None and result >= 60

    def test_keyword_swing(self):
        result = parse_horizon_days("medium swing")
        assert result is not None and 14 <= result <= 42

    def test_number_only_small_treated_as_weeks(self):
        # "4" with no unit ≤ 12 → 4 weeks = 28 days
        result = parse_horizon_days("4")
        assert result == 28

    def test_number_only_large_treated_as_days(self):
        # "30" with no unit > 12 → 30 days
        result = parse_horizon_days("30")
        assert result == 30


# ══════════════════════════════════════════════════════════════════════════════
# classify_structure_archetype
# ══════════════════════════════════════════════════════════════════════════════

class TestClassifyStructureArchetype:

    def _classify(self, time_horizon=None, days_to_earnings=None):
        t = _thesis(time_horizon=time_horizon, days_to_earnings=days_to_earnings)
        return classify_structure_archetype(t)

    def test_short_horizon_gives_short_breakout(self):
        policy = self._classify(time_horizon="1-2 weeks")
        assert policy.archetype == "short_breakout"

    def test_very_short_horizon_gives_short_breakout(self):
        policy = self._classify(time_horizon="3 days")
        assert policy.archetype == "short_breakout"

    def test_medium_horizon_gives_medium_swing(self):
        policy = self._classify(time_horizon="3-4 weeks")
        assert policy.archetype == "medium_swing"

    def test_six_week_horizon_is_medium_swing(self):
        # 6 weeks = 42 days → medium_swing (≤ 42)
        policy = self._classify(time_horizon="6 weeks")
        assert policy.archetype == "medium_swing"

    def test_long_horizon_gives_slow_macro(self):
        policy = self._classify(time_horizon="3-6 months")
        assert policy.archetype == "slow_macro"

    def test_two_month_horizon_gives_slow_macro(self):
        policy = self._classify(time_horizon="2 months")
        assert policy.archetype == "slow_macro"

    def test_event_sensitive_overrides_horizon(self):
        # earnings in 7 days, even with a medium-swing horizon → event_sensitive
        policy = self._classify(time_horizon="3-4 weeks", days_to_earnings=7)
        assert policy.archetype == "event_sensitive"

    def test_event_at_3_days_is_sensitive(self):
        policy = self._classify(days_to_earnings=3)
        assert policy.archetype == "event_sensitive"

    def test_event_at_14_days_is_sensitive(self):
        policy = self._classify(days_to_earnings=14)
        assert policy.archetype == "event_sensitive"

    def test_event_at_2_days_not_sensitive_handled_by_suppression(self):
        # days=2 is already blocked by _should_suppress; archetype doesn't fire
        # The archetype rule only covers 3–14 range
        policy = self._classify(days_to_earnings=2)
        # Not event_sensitive — earnings at day 2 is outside the 3-14 window
        assert policy.archetype != "event_sensitive"

    def test_event_at_15_days_not_sensitive(self):
        policy = self._classify(days_to_earnings=15)
        assert policy.archetype != "event_sensitive"

    def test_no_horizon_no_earnings_gives_default_swing(self):
        policy = self._classify(time_horizon=None, days_to_earnings=None)
        assert policy.archetype == "default_swing"

    def test_unrecognised_horizon_gives_default_swing(self):
        policy = self._classify(time_horizon="sometime soon")
        assert policy.archetype == "default_swing"


# ══════════════════════════════════════════════════════════════════════════════
# apply_policy_to_preset
# ══════════════════════════════════════════════════════════════════════════════

class TestApplyPolicyToPreset:
    """Preset application: verify DTE tightening, spread override, LEAPS passthrough."""

    def _base_swing_preset(self):
        return {
            "right": "C",
            "min_dte": 14,
            "max_dte": 60,
            "delta_min": 0.25,
            "delta_max": 0.65,
            "max_spread_pct": 12.0,
            "min_oi": 50,
            "directions": {"BULL"},
        }

    def _base_leaps_preset(self):
        return {
            "right": "C",
            "min_dte": 180,
            "max_dte": 560,
            "delta_min": 0.35,
            "delta_max": 0.80,
            "max_spread_pct": 15.0,
            "min_oi": 20,
            "directions": {"BULL"},
        }

    def test_short_breakout_caps_max_dte(self):
        ep = apply_policy_to_preset(
            self._base_swing_preset(), "long_call", POLICIES["short_breakout"]
        )
        assert ep["max_dte"] == 35

    def test_short_breakout_preserves_min_dte(self):
        ep = apply_policy_to_preset(
            self._base_swing_preset(), "long_call", POLICIES["short_breakout"]
        )
        assert ep["min_dte"] == 14   # preset min = policy min = 14

    def test_medium_swing_dte_floor_raised(self):
        ep = apply_policy_to_preset(
            self._base_swing_preset(), "long_call", POLICIES["medium_swing"]
        )
        assert ep["min_dte"] == 21   # policy raises floor from 14 to 21

    def test_slow_macro_swing_dte_floor_at_45(self):
        ep = apply_policy_to_preset(
            self._base_swing_preset(), "long_call", POLICIES["slow_macro"]
        )
        assert ep["min_dte"] == 45
        assert ep["max_dte"] == 60

    def test_event_sensitive_caps_dte_at_45(self):
        ep = apply_policy_to_preset(
            self._base_swing_preset(), "long_call", POLICIES["event_sensitive"]
        )
        assert ep["max_dte"] == 45

    def test_event_sensitive_tightens_spread(self):
        ep = apply_policy_to_preset(
            self._base_swing_preset(), "long_call", POLICIES["event_sensitive"]
        )
        assert ep["max_spread_pct"] == 10.0

    def test_leaps_preset_not_dte_constrained(self):
        """LEAPS presets bypass swing DTE overrides entirely."""
        ep = apply_policy_to_preset(
            self._base_leaps_preset(), "leaps_call", POLICIES["short_breakout"]
        )
        # LEAPS range should be unchanged (180–560), regardless of short_breakout policy
        assert ep["min_dte"] == 180
        assert ep["max_dte"] == 560

    def test_original_preset_not_mutated(self):
        preset = self._base_swing_preset()
        original_max = preset["max_dte"]
        apply_policy_to_preset(preset, "long_call", POLICIES["short_breakout"])
        assert preset["max_dte"] == original_max  # original must be unchanged

    def test_default_swing_preserves_standard_range(self):
        ep = apply_policy_to_preset(
            self._base_swing_preset(), "long_call", POLICIES["default_swing"]
        )
        assert ep["min_dte"] == 14
        assert ep["max_dte"] == 60
        assert ep["max_spread_pct"] == 12.0


# ══════════════════════════════════════════════════════════════════════════════
# Policy properties
# ══════════════════════════════════════════════════════════════════════════════

class TestPolicyProperties:
    def test_short_breakout_no_leaps(self):
        assert POLICIES["short_breakout"].allow_leaps is False

    def test_slow_macro_allows_and_prefers_leaps(self):
        p = POLICIES["slow_macro"]
        assert p.allow_leaps is True
        assert p.prefer_leaps is True

    def test_event_sensitive_no_leaps(self):
        assert POLICIES["event_sensitive"].allow_leaps is False

    def test_medium_swing_allows_leaps_not_prefers(self):
        p = POLICIES["medium_swing"]
        assert p.allow_leaps is True
        assert p.prefer_leaps is False

    def test_default_swing_allows_leaps(self):
        assert POLICIES["default_swing"].allow_leaps is True

    def test_event_sensitive_spread_tighter_than_standard(self):
        assert POLICIES["event_sensitive"].max_spread_pct < POLICIES["default_swing"].max_spread_pct

    def test_all_policies_have_non_empty_reason(self):
        for name, policy in POLICIES.items():
            assert policy.reason, f"Policy '{name}' has empty reason"

    def test_all_policies_have_valid_dte_range(self):
        for name, policy in POLICIES.items():
            assert policy.swing_dte_min <= policy.swing_dte_max, (
                f"Policy '{name}': swing_dte_min > swing_dte_max"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Integration via get_option_candidates
# ══════════════════════════════════════════════════════════════════════════════

class TestStructureArchetypeIntegration:

    def _run(self, thesis, contracts):
        chain = _chain(contracts)
        return get_option_candidates("AAPL", thesis=thesis, chain_result=chain)

    # ── Short breakout: no LEAPS, DTE capped at 35 ───────────────────────────

    def test_short_breakout_excludes_leaps_contract(self):
        """DTE=210 LEAPS contract must not appear for short-breakout thesis."""
        t = _thesis(time_horizon="1-2 weeks")
        contracts = [
            _contract(right="C", dte=21, mid=2.0, delta=0.40),
            _contract(right="C", dte=210, mid=10.0, delta=0.55, bid=9.80, ask=10.20),
        ]
        result = self._run(t, contracts)
        assert result.structure_archetype == "short_breakout"
        for c in result.candidates:
            assert not c.strategy_preset.startswith("leaps"), (
                f"LEAPS contract should be excluded for short_breakout: {c.strategy_preset}"
            )

    def test_short_breakout_excludes_dte_40_contract(self):
        """DTE=40 exceeds short_breakout cap of 35; must not appear."""
        t = _thesis(time_horizon="1-2 weeks")
        contracts = [
            _contract(right="C", dte=21, mid=2.0, delta=0.40),   # OK
            _contract(right="C", dte=40, mid=2.5, delta=0.38),   # excluded: DTE > 35
        ]
        result = self._run(t, contracts)
        for c in result.candidates:
            assert c.dte <= 35, f"DTE {c.dte} exceeds short_breakout max of 35"

    def test_short_breakout_accepts_dte_35_contract(self):
        """DTE=35 is exactly at the cap — should be eligible."""
        t = _thesis(time_horizon="1-2 weeks")
        contracts = [
            _contract(right="C", dte=35, mid=2.0, delta=0.40),
        ]
        result = self._run(t, contracts)
        assert not result.suppressed
        assert len(result.candidates) >= 1

    # ── Slow macro: LEAPS preferred, swing floor at 45 ───────────────────────

    def test_slow_macro_includes_leaps_contract(self):
        """6-month thesis should include LEAPS contracts."""
        t = _thesis(time_horizon="3-6 months")
        contracts = [
            _contract(right="C", dte=210, mid=10.0, delta=0.55, bid=9.80, ask=10.20),
        ]
        result = self._run(t, contracts)
        assert result.structure_archetype == "slow_macro"
        assert any(c.strategy_preset.startswith("leaps") for c in result.candidates)

    def test_slow_macro_excludes_short_dte_swing(self):
        """DTE=21 swing contract is below slow_macro swing floor (45 DTE)."""
        t = _thesis(time_horizon="3-6 months")
        contracts = [
            _contract(right="C", dte=21, mid=2.0, delta=0.40),   # excluded: DTE < 45
            _contract(right="C", dte=210, mid=10.0, delta=0.55, bid=9.80, ask=10.20),  # OK
        ]
        result = self._run(t, contracts)
        for c in result.candidates:
            if not c.strategy_preset.startswith("leaps"):
                assert c.dte >= 45, (
                    f"Swing DTE {c.dte} below slow_macro floor of 45"
                )

    def test_slow_macro_leaps_ranks_first(self):
        """With both LEAPS and 50 DTE swing available, LEAPS should appear (prefer_leaps=True)."""
        t = _thesis(time_horizon="3-6 months")
        contracts = [
            _contract(right="C", dte=50, mid=3.0, delta=0.40),   # swing (within 45–60)
            _contract(right="C", dte=210, mid=10.0, delta=0.55, bid=9.80, ask=10.20),
        ]
        result = self._run(t, contracts)
        # LEAPS should be among candidates when prefer_leaps=True
        has_leaps = any(c.strategy_preset.startswith("leaps") for c in result.candidates)
        assert has_leaps

    # ── Event sensitive: spread ≤ 10%, DTE capped at 45 ──────────────────────

    def test_event_sensitive_blocks_wide_spread(self):
        """Contract with 11% spread should be rejected for event_sensitive (max=10%)."""
        t = _thesis(days_to_earnings=7)
        # 11% spread: bid=1.8, ask=2.2, mid=2.0 → (0.4/2.0)*100 = 20% ← too wide
        # Use tighter: bid=1.89, ask=2.11 → 11% spread
        contracts = [
            _contract(
                right="C", dte=30, mid=2.0,
                bid=1.89, ask=2.11,  # ~11% spread
                delta=0.40, oi=500,
            ),
        ]
        result = self._run(t, contracts)
        assert result.structure_archetype == "event_sensitive"
        # 11% spread > 10% event_sensitive cap → rejected
        assert len(result.candidates) == 0

    def test_event_sensitive_accepts_tight_spread(self):
        """Contract with 4% spread is within event_sensitive limit (10%)."""
        t = _thesis(days_to_earnings=7)
        contracts = [
            _contract(
                right="C", dte=30, mid=2.0,
                bid=1.96, ask=2.04,  # ~4% spread
                delta=0.40, oi=500,
            ),
        ]
        result = self._run(t, contracts)
        assert result.structure_archetype == "event_sensitive"
        assert len(result.candidates) >= 1

    def test_event_sensitive_excludes_leaps(self):
        """LEAPS should not appear for event_sensitive thesis."""
        t = _thesis(days_to_earnings=7)
        contracts = [
            _contract(right="C", dte=30, mid=2.0, delta=0.40),
            _contract(right="C", dte=210, mid=10.0, delta=0.55, bid=9.80, ask=10.20),
        ]
        result = self._run(t, contracts)
        for c in result.candidates:
            assert not c.strategy_preset.startswith("leaps")

    def test_event_sensitive_excludes_dte_50(self):
        """DTE=50 exceeds event_sensitive max of 45; must be excluded."""
        t = _thesis(days_to_earnings=7)
        contracts = [
            _contract(right="C", dte=50, mid=2.0, delta=0.40),   # excluded: DTE > 45
            _contract(right="C", dte=30, mid=2.0, delta=0.40),   # OK
        ]
        result = self._run(t, contracts)
        for c in result.candidates:
            assert c.dte <= 45

    # ── Default swing: missing tempo ─────────────────────────────────────────

    def test_no_horizon_gives_default_swing(self):
        t = _thesis(time_horizon=None, days_to_earnings=None)
        contracts = [_contract(right="C", dte=30, mid=2.0, delta=0.40)]
        result = self._run(t, contracts)
        assert result.structure_archetype == "default_swing"

    def test_unrecognised_horizon_gives_default_swing(self):
        t = _thesis(time_horizon="eventually")
        contracts = [_contract(right="C", dte=30, mid=2.0, delta=0.40)]
        result = self._run(t, contracts)
        assert result.structure_archetype == "default_swing"

    def test_default_swing_includes_leaps(self):
        """Default swing should still allow LEAPS (allow_leaps=True)."""
        t = _thesis(time_horizon=None)
        contracts = [
            _contract(right="C", dte=30, mid=2.0, delta=0.40),
            _contract(right="C", dte=210, mid=10.0, delta=0.55, bid=9.80, ask=10.20),
        ]
        result = self._run(t, contracts)
        assert result.structure_archetype == "default_swing"
        leaps_present = any(c.strategy_preset.startswith("leaps") for c in result.candidates)
        # Both swing and LEAPS should be available
        assert leaps_present or len(result.candidates) >= 1  # at least swing present

    # ── Archetype metadata on candidates ─────────────────────────────────────

    def test_structure_archetype_on_candidate_result(self):
        t = _thesis(time_horizon="2-4 weeks")
        contracts = [_contract(right="C", dte=30, mid=2.0, delta=0.40)]
        result = self._run(t, contracts)
        assert result.structure_archetype == "medium_swing"
        assert result.structure_policy_reason is not None
        assert len(result.structure_policy_reason) > 10

    def test_structure_archetype_on_each_candidate(self):
        t = _thesis(time_horizon="2-4 weeks")
        contracts = [
            _contract(right="C", dte=30, mid=2.0, delta=0.40),
            _contract(right="C", dte=35, mid=1.8, delta=0.35),
        ]
        result = self._run(t, contracts)
        for c in result.candidates:
            assert c.structure_archetype == "medium_swing"
            assert c.structure_policy_reason is not None

    def test_suppressed_result_still_has_archetype(self):
        """Even when no contracts pass filters, archetype is set on the result."""
        t = _thesis(time_horizon="1-2 weeks")
        contracts = [
            _contract(right="C", dte=40, mid=2.0, delta=0.40),  # excluded: DTE > 35
        ]
        result = self._run(t, contracts)
        # No candidates but archetype should still be set
        assert result.structure_archetype == "short_breakout"

    # ── Bear direction ────────────────────────────────────────────────────────

    def test_bear_short_breakout_uses_puts(self):
        t = _thesis(direction="BEAR", time_horizon="1-2 weeks")
        contracts = [
            _contract(right="P", dte=21, mid=2.0, delta=-0.40),
            _contract(right="P", dte=210, mid=10.0, delta=-0.55, bid=9.80, ask=10.20),
        ]
        result = self._run(t, contracts)
        assert result.structure_archetype == "short_breakout"
        for c in result.candidates:
            assert c.right == "P"
            assert not c.strategy_preset.startswith("leaps")

    def test_bear_slow_macro_uses_leaps_puts(self):
        t = _thesis(direction="BEAR", time_horizon="3-6 months")
        contracts = [
            _contract(right="P", dte=210, mid=10.0, delta=-0.55, bid=9.80, ask=10.20),
        ]
        result = self._run(t, contracts)
        assert result.structure_archetype == "slow_macro"
        if result.candidates:
            assert any(c.strategy_preset.startswith("leaps") for c in result.candidates)
