"""
Tests for scripts/options_rollout_monitor.py

Covers:
- compute_warnings with various mock health/distribution states
- format_text_report produces the expected sections
- build_json_report produces the expected shape and values
- _pct / _distrib_lines formatting helpers
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.options_rollout_monitor import (
    NULL_RATE_WARN,
    INSUF_INPUTS_WARN,
    GUARDRAIL_STRICT_WARN,
    MIN_ROWS_DISTRIB,
    COMPARATOR_MIN_ROWS,
    compute_warnings,
    format_text_report,
    build_json_report,
    _pct,
    _distrib_lines,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _healthy_health(n: int = 30) -> dict:
    return {
        "fresh_rows": n,
        "rows_24h": 10,
        "v2_rows": n,
        "null_algo_version": 0,
        "suppressed_rows": 5,
        "no_candidate_rows": 5,
        "candidate_rows": n,
        "null_risk_allowed": 0,
        "null_structure_archetype": 0,
        "null_target_projection_method": 0,
        "null_entry_action": 0,
        "null_scenarios_unexpected": 0,
        "v2_candidate_rows": n,
    }


def _healthy_distributions(n: int = 30) -> dict:
    return {
        "entry_action": [
            {"value": "enter_now", "n": n // 2},
            {"value": "enter_if_repriced", "n": n // 4},
            {"value": "skip_for_now", "n": n // 4},
        ],
        "position_size_tier": [
            {"value": "standard", "n": n // 2},
            {"value": "reduced", "n": n // 2},
        ],
        "structure_archetype": [
            {"value": "medium_swing", "n": n // 2},
            {"value": "short_breakout", "n": n // 2},
        ],
        "target_projection_method": [
            {"value": "delta_dte_adjusted", "n": n // 2},
            {"value": "delta_only", "n": n // 4},
            {"value": "insufficient_inputs", "n": n // 4},
        ],
        "risk_nav_source": [
            {"value": "nav_proxy", "n": n},
        ],
    }


def _healthy_outcomes() -> dict:
    return {
        "total_outcomes": 5,
        "outcomes_with_method": 5,
        "outcomes_with_v2_hits": 3,
        "all_time_outcomes": 10,
    }


def _healthy_comparator(resolved: int = 5) -> dict:
    return {
        "resolved_snapshots": resolved,
        "tickers_covered": 3,
        "delta_dte_rows": resolved // 2,
        "delta_only_rows": resolved // 2,
    }


# ── compute_warnings ──────────────────────────────────────────────────────────

class TestComputeWarnings:

    def test_no_warnings_when_healthy(self):
        w = compute_warnings(
            _healthy_health(),
            _healthy_distributions(),
            _healthy_outcomes(),
            _healthy_comparator(),
        )
        assert w == []

    def test_null_rate_risk_allowed(self):
        h = _healthy_health(n=20)
        h["null_risk_allowed"] = 3  # 15% > 10% threshold
        w = compute_warnings(h, _healthy_distributions(20), _healthy_outcomes(), _healthy_comparator())
        assert any("risk_allowed" in x and "NULL_RATE" in x for x in w)

    def test_null_rate_structure_archetype(self):
        h = _healthy_health(n=20)
        h["null_structure_archetype"] = 3  # 15%
        w = compute_warnings(h, _healthy_distributions(20), _healthy_outcomes(), _healthy_comparator())
        assert any("structure_archetype" in x for x in w)

    def test_null_rate_target_projection_method(self):
        h = _healthy_health(n=20)
        h["null_target_projection_method"] = 3
        w = compute_warnings(h, _healthy_distributions(20), _healthy_outcomes(), _healthy_comparator())
        assert any("target_projection_method" in x for x in w)

    def test_null_rate_entry_action(self):
        h = _healthy_health(n=20)
        h["null_entry_action"] = 3
        w = compute_warnings(h, _healthy_distributions(20), _healthy_outcomes(), _healthy_comparator())
        assert any("entry_action" in x for x in w)

    def test_null_scenarios_unexpected(self):
        h = _healthy_health(n=20)
        h["null_scenarios_unexpected"] = 3
        w = compute_warnings(h, _healthy_distributions(20), _healthy_outcomes(), _healthy_comparator())
        assert any("scenarios_json" in x for x in w)

    def test_null_rate_exactly_at_threshold_no_warning(self):
        # At exactly 10% (threshold is STRICTLY greater than)
        h = _healthy_health(n=10)
        h["null_risk_allowed"] = 1  # exactly 10%
        w = compute_warnings(h, _healthy_distributions(10), _healthy_outcomes(), _healthy_comparator())
        assert not any("risk_allowed" in x for x in w)

    def test_all_insufficient_inputs_warning(self):
        n = 20
        dist = _healthy_distributions(n)
        dist["target_projection_method"] = [{"value": "insufficient_inputs", "n": n}]
        w = compute_warnings(_healthy_health(n), dist, _healthy_outcomes(), _healthy_comparator())
        assert any("insufficient_inputs" in x for x in w)

    def test_insufficient_inputs_below_threshold_no_warning(self):
        n = 20
        dist = _healthy_distributions(n)
        dist["target_projection_method"] = [
            {"value": "delta_dte_adjusted", "n": 12},
            {"value": "insufficient_inputs", "n": 8},  # 40% < 50%
        ]
        w = compute_warnings(_healthy_health(n), dist, _healthy_outcomes(), _healthy_comparator())
        assert not any("insufficient_inputs" in x for x in w)

    def test_guardrail_too_strict_warning(self):
        n = 20
        dist = _healthy_distributions(n)
        dist["entry_action"] = [
            {"value": "enter_if_repriced", "n": 12},
            {"value": "skip_for_now", "n": 6},
            {"value": "enter_now", "n": 2},
        ]  # 90% blocked > 75% threshold
        w = compute_warnings(_healthy_health(n), dist, _healthy_outcomes(), _healthy_comparator())
        assert any("GUARDRAIL" in x for x in w)

    def test_guardrail_under_threshold_no_warning(self):
        n = 20
        dist = _healthy_distributions(n)
        dist["entry_action"] = [
            {"value": "enter_now", "n": 10},
            {"value": "enter_if_repriced", "n": 8},
            {"value": "skip_for_now", "n": 2},
        ]  # 50% blocked < 75%
        w = compute_warnings(_healthy_health(n), dist, _healthy_outcomes(), _healthy_comparator())
        assert not any("GUARDRAIL" in x for x in w)

    def test_size_tier_uniformity_warning(self):
        n = 20
        dist = _healthy_distributions(n)
        dist["position_size_tier"] = [{"value": "skip", "n": n}]  # 100% > 95%
        w = compute_warnings(_healthy_health(n), dist, _healthy_outcomes(), _healthy_comparator())
        assert any("position_size_tier" in x for x in w)

    def test_no_fresh_data_warning(self):
        h = _healthy_health()
        h["rows_24h"] = 0
        w = compute_warnings(h, _healthy_distributions(), _healthy_outcomes(), _healthy_comparator())
        assert any("STALENESS" in x for x in w)

    def test_skip_distrib_when_too_few_rows(self):
        # With only MIN_ROWS_DISTRIB - 1 rows, skip distribution warnings
        n = MIN_ROWS_DISTRIB - 1
        h = _healthy_health(n=n)
        dist = _healthy_distributions(n)
        # Even with 100% insufficient_inputs, no distrib warning when n is too small
        dist["target_projection_method"] = [{"value": "insufficient_inputs", "n": n}]
        w = compute_warnings(h, dist, _healthy_outcomes(), _healthy_comparator())
        assert not any("insufficient_inputs" in x for x in w)

    def test_multiple_warnings_collected(self):
        h = _healthy_health(n=20)
        h["null_risk_allowed"] = 5
        h["null_structure_archetype"] = 5
        h["rows_24h"] = 0
        w = compute_warnings(h, _healthy_distributions(20), _healthy_outcomes(), _healthy_comparator())
        assert len(w) >= 3

    def test_zero_rows_no_null_rate_warnings(self):
        # With 0 candidate rows, null rates should not fire
        h = _healthy_health(n=0)
        w = compute_warnings(h, {}, _healthy_outcomes(), _healthy_comparator())
        null_warns = [x for x in w if "NULL_RATE" in x]
        assert null_warns == []


# ── format_text_report ────────────────────────────────────────────────────────

class TestFormatTextReport:

    def _make_report(self, warnings=None):
        return format_text_report(
            days=7,
            health=_healthy_health(),
            distributions=_healthy_distributions(),
            outcomes=_healthy_outcomes(),
            comparator=_healthy_comparator(),
            warnings=warnings or [],
            generated_at="2026-06-07T10:00:00Z",
        )

    def test_contains_required_sections(self):
        r = self._make_report()
        assert "SNAPSHOT HEALTH" in r
        assert "DISTRIBUTIONS" in r
        assert "OUTCOMES & COMPARATOR" in r
        assert "WARNINGS" in r

    def test_no_warnings_shows_ok(self):
        r = self._make_report(warnings=[])
        assert "No data-quality warnings" in r

    def test_warnings_shown_in_output(self):
        r = self._make_report(warnings=["NULL_RATE: risk_allowed is 15% null"])
        assert "NULL_RATE: risk_allowed" in r

    def test_comparator_warmup_notice(self):
        r = self._make_report()
        # healthy_comparator has 5 resolved < COMPARATOR_MIN_ROWS (20)
        assert "warming up" in r

    def test_comparator_ready_notice(self):
        report = format_text_report(
            days=7,
            health=_healthy_health(),
            distributions=_healthy_distributions(),
            outcomes=_healthy_outcomes(),
            comparator=_healthy_comparator(resolved=COMPARATOR_MIN_ROWS + 5),
            warnings=[],
            generated_at="2026-06-07T10:00:00Z",
        )
        assert "sufficient data" in report

    def test_window_appears_in_header(self):
        r = self._make_report()
        assert "window: 7d" in r

    def test_distribution_fields_present(self):
        r = self._make_report()
        for field in ("entry_action", "structure_archetype",
                      "target_projection_method", "position_size_tier"):
            assert field in r


# ── build_json_report ─────────────────────────────────────────────────────────

class TestBuildJsonReport:

    def _make(self, warnings=None):
        return build_json_report(
            days=7,
            health=_healthy_health(),
            distributions=_healthy_distributions(),
            outcomes=_healthy_outcomes(),
            comparator=_healthy_comparator(),
            warnings=warnings or [],
            generated_at="2026-06-07T10:00:00Z",
        )

    def test_top_level_keys(self):
        r = self._make()
        for key in ("generated_at", "window_days", "snapshot_health", "distributions",
                    "outcomes", "comparator", "warnings", "status"):
            assert key in r

    def test_status_ok_when_no_warnings(self):
        r = self._make(warnings=[])
        assert r["status"] == "ok"

    def test_status_warning_when_warnings(self):
        r = self._make(warnings=["NULL_RATE: something"])
        assert r["status"] == "warning"

    def test_null_rates_shape(self):
        r = self._make()
        nr = r["snapshot_health"]["null_rates"]
        for key in ("risk_allowed", "structure_archetype", "target_projection_method",
                    "entry_action", "scenarios_json_unexpected"):
            assert key in nr

    def test_null_rates_zero_when_healthy(self):
        r = self._make()
        nr = r["snapshot_health"]["null_rates"]
        assert nr["risk_allowed"] == 0.0
        assert nr["structure_archetype"] == 0.0

    def test_null_rates_none_when_no_rows(self):
        h = _healthy_health(n=0)
        r = build_json_report(7, h, {}, _healthy_outcomes(), _healthy_comparator(), [], "now")
        nr = r["snapshot_health"]["null_rates"]
        assert nr["risk_allowed"] is None

    def test_json_serializable(self):
        r = self._make()
        # Must not raise
        json.dumps(r, default=str)

    def test_window_days_stored(self):
        r = self._make()
        assert r["window_days"] == 7


# ── Formatting helpers ────────────────────────────────────────────────────────

class TestHelpers:

    def test_pct_normal(self):
        assert _pct(1, 10) == "10%"
        assert _pct(3, 4) == "75%"

    def test_pct_zero_denom(self):
        assert _pct(5, 0) == "—"

    def test_pct_zero_num(self):
        assert _pct(0, 10) == "0%"

    def test_pct_full(self):
        assert _pct(10, 10) == "100%"

    def test_distrib_lines_normal(self):
        rows = [{"value": "enter_now", "n": 10}, {"value": "skip_for_now", "n": 5}]
        lines = _distrib_lines(rows, 15)
        assert any("enter_now" in l for l in lines)
        assert any("67%" in l for l in lines)

    def test_distrib_lines_empty(self):
        lines = _distrib_lines([], 0)
        assert any("no data" in l for l in lines)

    def test_distrib_lines_null_value(self):
        rows = [{"value": None, "n": 3}]
        lines = _distrib_lines(rows, 3)
        assert any("(null)" in l for l in lines)
