"""
Tests for the TRD-054 pre-entry buy rule engine.

Covers:
- compute_buy_decision: full truth table of (risk_allowed × entry_action) combinations
- edge cases: missing / None / unexpected entry_action values
- OptionCandidate field defaults
- _serialize_candidate integration (fields present in serialized dict)
"""

import pytest
from utils.option_candidates import compute_buy_decision, OptionCandidate


# ── Truth table ────────────────────────────────────────────────────────────────

class TestComputeBuyDecision:
    def test_both_true_enter_now_returns_buy_now(self):
        result = compute_buy_decision(risk_allowed=True, entry_action="enter_now")
        assert result["buy_decision"] == "buy_now"
        assert result["buy_decision_blocker"] is None
        assert "buy allowed" in result["buy_decision_reason"].lower()

    def test_risk_false_enter_now_returns_do_not_buy_risk_policy(self):
        result = compute_buy_decision(risk_allowed=False, entry_action="enter_now")
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "risk_policy"
        assert "risk policy" in result["buy_decision_reason"].lower()

    def test_risk_true_enter_if_repriced_returns_do_not_buy_entry_quality(self):
        result = compute_buy_decision(risk_allowed=True, entry_action="enter_if_repriced")
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "entry_quality"
        assert "better entry" in result["buy_decision_reason"].lower()

    def test_risk_true_reduce_size_returns_do_not_buy_entry_quality(self):
        result = compute_buy_decision(risk_allowed=True, entry_action="reduce_size")
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "entry_quality"

    def test_risk_true_skip_for_now_returns_do_not_buy_entry_quality(self):
        result = compute_buy_decision(risk_allowed=True, entry_action="skip_for_now")
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "entry_quality"

    def test_risk_false_enter_if_repriced_returns_do_not_buy_both(self):
        result = compute_buy_decision(risk_allowed=False, entry_action="enter_if_repriced")
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "both"
        assert "both" in result["buy_decision_reason"].lower()

    def test_risk_false_reduce_size_returns_do_not_buy_both(self):
        result = compute_buy_decision(risk_allowed=False, entry_action="reduce_size")
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "both"

    def test_risk_false_skip_for_now_returns_do_not_buy_both(self):
        result = compute_buy_decision(risk_allowed=False, entry_action="skip_for_now")
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "both"


# ── Edge cases ─────────────────────────────────────────────────────────────────

class TestComputeBuyDecisionEdgeCases:
    def test_missing_entry_action_empty_string_is_do_not_buy(self):
        result = compute_buy_decision(risk_allowed=True, entry_action="")
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "entry_quality"

    def test_none_entry_action_is_do_not_buy(self):
        result = compute_buy_decision(risk_allowed=True, entry_action=None)  # type: ignore[arg-type]
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "entry_quality"

    def test_unknown_entry_action_is_do_not_buy(self):
        result = compute_buy_decision(risk_allowed=True, entry_action="unknown_value")
        assert result["buy_decision"] == "do_not_buy"

    def test_both_false_none_returns_do_not_buy_both(self):
        result = compute_buy_decision(risk_allowed=False, entry_action=None)  # type: ignore[arg-type]
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "both"

    def test_reason_is_always_non_empty_string(self):
        for ra in (True, False):
            for ea in ("enter_now", "skip_for_now", "", None):
                result = compute_buy_decision(risk_allowed=ra, entry_action=ea)  # type: ignore[arg-type]
                assert isinstance(result["buy_decision_reason"], str)
                assert len(result["buy_decision_reason"]) > 0

    def test_buy_decision_is_always_valid_enum(self):
        valid = {"buy_now", "do_not_buy"}
        for ra in (True, False):
            for ea in ("enter_now", "enter_if_repriced", "reduce_size", "skip_for_now", ""):
                result = compute_buy_decision(risk_allowed=ra, entry_action=ea)
                assert result["buy_decision"] in valid

    def test_blocker_is_none_only_for_buy_now(self):
        result_buy = compute_buy_decision(risk_allowed=True, entry_action="enter_now")
        assert result_buy["buy_decision_blocker"] is None

        for ea in ("enter_if_repriced", "reduce_size", "skip_for_now"):
            r = compute_buy_decision(risk_allowed=True, entry_action=ea)
            assert r["buy_decision_blocker"] is not None


# ── OptionCandidate field defaults ─────────────────────────────────────────────

class TestOptionCandidateFields:
    def _minimal_candidate(self, **overrides) -> OptionCandidate:
        defaults = dict(
            ticker="AAPL", expiry="2026-08-15", strike=150.0, right="C", dte=21,
            bid=2.0, ask=2.2, mid=2.1, spread_pct=9.5,
            delta=0.40, implied_vol=0.35, open_interest=500, volume=100, breakeven=152.1,
        )
        defaults.update(overrides)
        return OptionCandidate(**defaults)

    def test_default_buy_decision_is_do_not_buy(self):
        c = self._minimal_candidate()
        assert c.buy_decision == "do_not_buy"

    def test_default_buy_decision_reason_is_string(self):
        c = self._minimal_candidate()
        assert isinstance(c.buy_decision_reason, str)

    def test_default_buy_decision_blocker_is_none(self):
        c = self._minimal_candidate()
        assert c.buy_decision_blocker is None

    def test_fields_are_assignable(self):
        c = self._minimal_candidate()
        c.buy_decision = "buy_now"
        c.buy_decision_reason = "Buy allowed: both gates passed."
        c.buy_decision_blocker = None
        assert c.buy_decision == "buy_now"


# ── Serialization (fields appear in _serialize_candidate output) ───────────────

class TestSerializeCandidate:
    def test_serialize_candidate_includes_buy_decision_fields(self):
        from dashboard.api.main import _serialize_candidate
        from utils.option_candidates import OptionCandidate

        c = OptionCandidate(
            ticker="AAPL", expiry="2026-08-15", strike=150.0, right="C", dte=21,
            bid=2.0, ask=2.2, mid=2.1, spread_pct=9.5,
            delta=0.40, implied_vol=0.35, open_interest=500, volume=100, breakeven=152.1,
            buy_decision="buy_now",
            buy_decision_reason="Buy allowed: portfolio risk policy passed and entry quality is actionable now.",
            buy_decision_blocker=None,
        )
        result = _serialize_candidate(c)
        assert result["buy_decision"] == "buy_now"
        assert result["buy_decision_reason"].startswith("Buy allowed")
        assert result["buy_decision_blocker"] is None

    def test_serialize_candidate_do_not_buy_with_blocker(self):
        from dashboard.api.main import _serialize_candidate
        from utils.option_candidates import OptionCandidate

        c = OptionCandidate(
            ticker="AAPL", expiry="2026-08-15", strike=150.0, right="C", dte=21,
            bid=2.0, ask=2.2, mid=2.1, spread_pct=9.5,
            delta=0.40, implied_vol=0.35, open_interest=500, volume=100, breakeven=152.1,
            buy_decision="do_not_buy",
            buy_decision_reason="Do not buy: blocked by portfolio risk policy.",
            buy_decision_blocker="risk_policy",
        )
        result = _serialize_candidate(c)
        assert result["buy_decision"] == "do_not_buy"
        assert result["buy_decision_blocker"] == "risk_policy"

    def test_serialize_candidate_safe_for_legacy_candidates_missing_fields(self):
        """getattr fallback means old candidates without the new attrs serialize safely."""
        from dashboard.api.main import _serialize_candidate
        from utils.option_candidates import OptionCandidate
        import dataclasses

        c = OptionCandidate(
            ticker="AAPL", expiry="2026-08-15", strike=150.0, right="C", dte=21,
            bid=2.0, ask=2.2, mid=2.1, spread_pct=9.5,
            delta=0.40, implied_vol=0.35, open_interest=500, volume=100, breakeven=152.1,
        )
        # Simulate a legacy object that somehow lacks the new fields
        d = dataclasses.asdict(c)
        del d["buy_decision"]
        del d["buy_decision_reason"]
        del d["buy_decision_blocker"]

        class _Legacy:
            pass
        obj = _Legacy()
        for k, v in d.items():
            setattr(obj, k, v)

        result = _serialize_candidate(obj)  # type: ignore[arg-type]
        assert result["buy_decision"] == "do_not_buy"      # getattr fallback
        assert result["buy_decision_reason"] == ""
        assert result["buy_decision_blocker"] is None
