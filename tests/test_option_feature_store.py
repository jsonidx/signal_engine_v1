"""
Tests for TRD-050: Option Feature Store persistence.

Verifies that save_option_candidate_snapshot writes the full structured
feature set: TRD-049 guardrail fields, scenario compact summary, thesis
enrichment, algorithm versioning, and IBKR Greeks.

All DB I/O is mocked — no live Supabase required.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from utils.option_candidates import CandidateResult, OptionCandidate, ThesisContext
from utils.supabase_persist import (
    _ALGO_VERSION,
    _GUARDRAIL_VERSION,
    _RISK_FRAMEWORK_VERSION,
    _SCENARIO_ENGINE_VERSION,
    _TARGET_ENGINE_VERSION,
    save_option_candidate_snapshot,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _make_candidate(**overrides) -> OptionCandidate:
    expiry = _expiry(21)
    from utils.option_candidates import _compute_exit_plan
    thesis = ThesisContext(
        ticker="AAPL", direction="BULL", conviction=4,
        target_1=160.0, target_2=175.0, stop_loss=140.0,
    )
    contract = type("C", (), {"dte": 21, "expiry": expiry, "right": "C", "mid": 2.10})()
    exit_plan = _compute_exit_plan(contract, "long_call", thesis)
    base = dict(
        ticker="AAPL", expiry=expiry, strike=150.0, right="C", dte=21,
        bid=2.00, ask=2.20, mid=2.10, spread_pct=9.5,
        delta=0.40, implied_vol=0.35, open_interest=500, volume=100,
        breakeven=152.10, score=68.0,
        rationale="bullish long call", strategy_preset="long_call",
        source="yfinance",
    )
    base.update(exit_plan)
    base.update(overrides)
    return OptionCandidate(**base)


def _make_result(candidates=None) -> CandidateResult:
    c = candidates if candidates is not None else [_make_candidate()]
    return CandidateResult(
        ticker="AAPL", generated_at="2026-06-06T12:00:00",
        suppressed=False, candidates=c,
        rejection_reasons=[], underlying_price=148.0, chain_source="yfinance",
    )


def _capture_sql_mock(inserted_ids=(42,)):
    """Return (conn_ctx, insert_calls) where insert_calls is a list of (sql, params) tuples."""
    # insert_calls accumulates (sql, params) for each INSERT INTO option_candidate_snapshots
    insert_calls: list[tuple[str, list]] = []
    all_sql: list[str] = []

    fetchone_calls = [{"tbl": "option_candidate_snapshots"}]
    for iid in inserted_ids:
        fetchone_calls.append({"id": iid})

    mock_cur = MagicMock()
    mock_cur.fetchone.side_effect = fetchone_calls

    def _execute(sql, params=None):
        all_sql.append(sql)
        if "INSERT INTO option_candidate_snapshots" in sql:
            insert_calls.append((sql, list(params) if params else []))

    mock_cur.execute.side_effect = _execute

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_cur)
    ctx.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = ctx

    conn_ctx = MagicMock()
    conn_ctx.__enter__ = MagicMock(return_value=conn)
    conn_ctx.__exit__ = MagicMock(return_value=False)

    return conn_ctx, all_sql, insert_calls


def _get_insert_sql_and_params(all_sql, insert_calls, call_index=0):
    """Return (insert_sql, params_list) for the nth INSERT (default: first)."""
    if call_index < len(insert_calls):
        return insert_calls[call_index]
    return None, []


def _fields_from_insert(sql: str) -> list[str]:
    """Extract column list from INSERT INTO table (col1, col2, ...) VALUES (...)."""
    import re
    m = re.search(r"INSERT INTO option_candidate_snapshots \(([^)]+)\)", sql)
    if not m:
        return []
    return [f.strip() for f in m.group(1).split(",")]


# ══════════════════════════════════════════════════════════════════════════════
# TRD-049 Live-entry guardrail fields persisted
# ══════════════════════════════════════════════════════════════════════════════

class TestGuardrailFieldsPersisted:
    """entry_action, quote_freshness_label, and related guardrail fields must be in INSERT."""

    def test_guardrail_fields_present_in_insert(self):
        c = _make_candidate(
            entry_action="enter_if_repriced",
            quote_freshness_label="stale",
            quote_age_seconds=190.0,
            fair_value_entry_low=1.85,
            fair_value_entry_high=2.00,
            entry_overpay_pct=5.0,
            market_quality_label="wide",
            live_guardrail_reason="Mid $2.10 above FV ceiling $2.00.",
        )
        conn_ctx, sql_list, _ = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql = next((s for s in sql_list if "INSERT INTO option_candidate_snapshots" in s), None)
        assert insert_sql is not None
        for col in [
            "entry_action", "quote_freshness_label", "quote_age_seconds",
            "fair_value_entry_low", "fair_value_entry_high",
            "entry_overpay_pct", "market_quality_label", "live_guardrail_reason",
        ]:
            assert col in insert_sql, f"Expected column '{col}' in INSERT SQL"

    def test_guardrail_values_match_candidate(self):
        c = _make_candidate(
            entry_action="reduce_size",
            quote_freshness_label="live",
            entry_overpay_pct=3.5,
            fair_value_entry_low=1.90,
            fair_value_entry_high=2.05,
        )
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        assert insert_sql is not None
        fields = _fields_from_insert(insert_sql)

        idx_action = fields.index("entry_action")
        assert params[idx_action] == "reduce_size"

        idx_freshness = fields.index("quote_freshness_label")
        assert params[idx_freshness] == "live"

        idx_overpay = fields.index("entry_overpay_pct")
        assert params[idx_overpay] == pytest.approx(3.5)

    def test_default_guardrail_values_are_stable(self):
        """Candidate with only defaults: entry_action='enter_now', freshness='unknown'."""
        c = _make_candidate()  # all defaults
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)

        assert params[fields.index("entry_action")] == "enter_now"
        assert params[fields.index("quote_freshness_label")] == "unknown"
        assert params[fields.index("fair_value_entry_low")] is None
        assert params[fields.index("market_quality_label")] == "unknown"

    def test_empty_guardrail_reason_stored_as_none(self):
        """live_guardrail_reason='' should be persisted as None (not empty string)."""
        c = _make_candidate(live_guardrail_reason="")
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        assert params[fields.index("live_guardrail_reason")] is None


# ══════════════════════════════════════════════════════════════════════════════
# TRD-047 Scenario compact summary persisted
# ══════════════════════════════════════════════════════════════════════════════

class TestScenariosPersisted:

    def _scenario(self, sid="fast_target", ret=55.0, days=7,
                  method="delta_approx", price=3.25) -> dict:
        return {
            "scenario_id": sid,
            "scenario_label": sid.replace("_", " ").title(),
            "projected_return_pct": ret,
            "days_to_resolution": days,
            "input_method": method,
            "projected_option_price": price,
            "scenario_weight_label": "medium",
            "exit_guidance": "Take profit.",
            "theta_cost": 0.10,
        }

    def test_scenarios_json_present_in_insert(self):
        c = _make_candidate(scenarios=[
            self._scenario("fast_target", ret=55.0),
            self._scenario("adverse_stop", ret=-50.0, method="delta_approx"),
        ])
        conn_ctx, sql_list, _ = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql = next((s for s in sql_list if "INSERT INTO option_candidate_snapshots" in s), None)
        assert insert_sql is not None
        assert "scenarios_json" in insert_sql

    def test_scenarios_json_compact_format(self):
        """Stored compact format has id, ret, days, method, price keys."""
        c = _make_candidate(scenarios=[
            self._scenario("fast_target", ret=55.0, days=7, price=3.25),
            self._scenario("sideways_decay", ret=-40.0, days=21, price=1.20),
        ])
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        raw = params[fields.index("scenarios_json")]
        assert raw is not None
        data = json.loads(raw)
        assert len(data) == 2
        assert data[0]["id"] == "fast_target"
        assert data[0]["ret"] == pytest.approx(55.0)
        assert data[0]["days"] == 7
        assert data[0]["price"] == pytest.approx(3.25)

    def test_insufficient_inputs_scenarios_excluded(self):
        """insufficient_inputs scenarios must not appear in the compact summary."""
        c = _make_candidate(scenarios=[
            self._scenario("fast_target", method="insufficient_inputs"),
            self._scenario("slow_target", ret=30.0, method="delta_approx"),
        ])
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        raw = params[fields.index("scenarios_json")]
        data = json.loads(raw)
        assert all(s["id"] != "fast_target" for s in data)
        assert any(s["id"] == "slow_target" for s in data)

    def test_empty_scenarios_stored_as_null(self):
        c = _make_candidate(scenarios=[])
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        assert params[fields.index("scenarios_json")] is None

    def test_all_insufficient_stored_as_null(self):
        """If every scenario is insufficient, scenarios_json should be None."""
        c = _make_candidate(scenarios=[
            self._scenario("fast_target", method="insufficient_inputs"),
        ])
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        assert params[fields.index("scenarios_json")] is None


# ══════════════════════════════════════════════════════════════════════════════
# Thesis enrichment fields
# ══════════════════════════════════════════════════════════════════════════════

class TestThesisEnrichmentPersisted:

    def test_thesis_enrichment_fields_in_insert(self):
        conn_ctx, sql_list, _ = _capture_sql_mock((42,))
        tc = {
            "thesis_date": "2026-06-06",
            "time_horizon": "2-4 weeks",
            "signal_agreement": 0.78,
            "entry_low": 145.0,
            "entry_high": 150.0,
            "days_to_earnings": 18,
            "heat_score": 0.82,
            "expected_move_pct": 4.5,
        }
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result(), run_date="2026-06-06",
                                           thesis_context=tc)

        insert_sql = next((s for s in sql_list if "INSERT INTO option_candidate_snapshots" in s), None)
        for col in ["thesis_entry_low", "thesis_entry_high", "days_to_earnings",
                    "heat_score", "expected_move_pct"]:
            assert col in insert_sql, f"Expected '{col}' in INSERT SQL"

    def test_thesis_enrichment_values_persisted_correctly(self):
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        tc = {
            "entry_low": 144.0,
            "entry_high": 149.0,
            "days_to_earnings": 12,
            "heat_score": 0.75,
            "expected_move_pct": 3.2,
        }
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result(), run_date="2026-06-06",
                                           thesis_context=tc)

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        assert params[fields.index("thesis_entry_low")] == pytest.approx(144.0)
        assert params[fields.index("thesis_entry_high")] == pytest.approx(149.0)
        assert params[fields.index("days_to_earnings")] == 12
        assert params[fields.index("heat_score")] == pytest.approx(0.75)
        assert params[fields.index("expected_move_pct")] == pytest.approx(3.2)

    def test_missing_thesis_enrichment_is_none(self):
        """When thesis_context omits enrichment fields, columns must be NULL."""
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        tc = {"thesis_date": "2026-06-06", "signal_agreement": 0.7}
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result(), run_date="2026-06-06",
                                           thesis_context=tc)

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        assert params[fields.index("thesis_entry_low")] is None
        assert params[fields.index("days_to_earnings")] is None
        assert params[fields.index("heat_score")] is None

    def test_no_thesis_context_degrades_safely(self):
        """Calling with thesis_context=None must not raise."""
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            ids = save_option_candidate_snapshot(_make_result(), run_date="2026-06-06",
                                                 thesis_context=None)
        assert len(ids) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# Algorithm versioning metadata
# ══════════════════════════════════════════════════════════════════════════════

class TestVersioningMetadataPersisted:

    def test_version_columns_in_insert(self):
        conn_ctx, sql_list, _ = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result(), run_date="2026-06-06")

        insert_sql = next((s for s in sql_list if "INSERT INTO option_candidate_snapshots" in s), None)
        for col in ["algo_version", "target_engine_version", "scenario_engine_version",
                    "risk_framework_version", "guardrail_version"]:
            assert col in insert_sql, f"Expected '{col}' in INSERT SQL"

    def test_version_values_match_constants(self):
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result(), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)

        assert params[fields.index("algo_version")]            == _ALGO_VERSION
        assert params[fields.index("target_engine_version")]   == _TARGET_ENGINE_VERSION
        assert params[fields.index("scenario_engine_version")] == _SCENARIO_ENGINE_VERSION
        assert params[fields.index("risk_framework_version")]  == _RISK_FRAMEWORK_VERSION
        assert params[fields.index("guardrail_version")]       == _GUARDRAIL_VERSION

    def test_version_values_are_non_empty_strings(self):
        for v in [_ALGO_VERSION, _TARGET_ENGINE_VERSION, _SCENARIO_ENGINE_VERSION,
                  _RISK_FRAMEWORK_VERSION, _GUARDRAIL_VERSION]:
            assert isinstance(v, str) and len(v) > 0

    def test_versioning_in_suppression_row(self):
        """Version columns must also appear in suppression-row INSERTs."""
        suppressed = CandidateResult(
            ticker="MSFT", generated_at="2026-06-06T12:00:00",
            suppressed=True, suppression_reason="No thesis",
            candidates=[], rejection_reasons=[],
            underlying_price=None, chain_source="unknown",
        )
        conn_ctx, sql_list, _ = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(suppressed, run_date="2026-06-06")

        insert_sql = next((s for s in sql_list if "INSERT INTO option_candidate_snapshots" in s), None)
        assert insert_sql is not None
        assert "algo_version" in insert_sql


# ══════════════════════════════════════════════════════════════════════════════
# IBKR Greeks (gamma / theta / vega)
# ══════════════════════════════════════════════════════════════════════════════

class TestGreeksPersisted:

    def test_gamma_theta_vega_present_in_insert(self):
        c = _make_candidate(gamma=0.05, theta=-0.04, vega=0.12)
        conn_ctx, sql_list, _ = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql = next((s for s in sql_list if "INSERT INTO option_candidate_snapshots" in s), None)
        for col in ["gamma", "theta", "vega"]:
            assert col in insert_sql, f"Expected '{col}' in INSERT SQL"

    def test_greek_values_persisted(self):
        c = _make_candidate(gamma=0.05, theta=-0.04, vega=0.12)
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        assert params[fields.index("gamma")] == pytest.approx(0.05)
        assert params[fields.index("theta")] == pytest.approx(-0.04)
        assert params[fields.index("vega")]  == pytest.approx(0.12)

    def test_yfinance_candidate_greeks_are_null(self):
        """yfinance candidates don't have gamma/theta/vega — must persist as None."""
        c = _make_candidate()  # no gamma/theta/vega set, source=yfinance
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        assert params[fields.index("gamma")] is None
        assert params[fields.index("theta")] is None
        assert params[fields.index("vega")]  is None

    def test_greeks_in_features_json(self):
        """features_json must also carry gamma/theta/vega for downstream ML queries."""
        c = _make_candidate(gamma=0.05, theta=-0.04, vega=0.12)
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        fj = json.loads(params[fields.index("features_json")])
        assert fj["gamma"] == pytest.approx(0.05)
        assert fj["theta"] == pytest.approx(-0.04)
        assert fj["vega"]  == pytest.approx(0.12)


# ══════════════════════════════════════════════════════════════════════════════
# Quote time persisted
# ══════════════════════════════════════════════════════════════════════════════

class TestQuoteTimePersisted:

    def test_quote_time_present_in_insert(self):
        c = _make_candidate(quote_time="2026-06-06T09:31:00Z")
        conn_ctx, sql_list, _ = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql = next((s for s in sql_list if "INSERT INTO option_candidate_snapshots" in s), None)
        assert "quote_time" in insert_sql

    def test_quote_time_value_persisted(self):
        c = _make_candidate(quote_time="2026-06-06T09:31:00Z")
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        assert params[fields.index("quote_time")] == "2026-06-06T09:31:00Z"

    def test_yfinance_quote_time_is_null(self):
        c = _make_candidate()  # quote_time defaults to None
        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")

        insert_sql, params = _get_insert_sql_and_params(sql_list, params_list)
        fields = _fields_from_insert(insert_sql)
        assert params[fields.index("quote_time")] is None


# ══════════════════════════════════════════════════════════════════════════════
# Feature completeness: all TRD-026 through TRD-050 groups present together
# ══════════════════════════════════════════════════════════════════════════════

class TestFeatureCompleteness:
    """Single consolidated test: a fully-populated candidate covers all feature groups."""

    REQUIRED_COLUMNS = [
        # Thesis context
        "direction", "conviction", "thesis_date", "time_horizon", "signal_agreement",
        "thesis_entry_low", "thesis_entry_high", "days_to_earnings",
        # Contract
        "strike", "expiry", "dte", "contract_right",
        "bid", "ask", "mid", "spread_pct", "delta", "iv",
        "gamma", "theta", "vega",
        "open_interest", "volume", "breakeven",
        "quote_time",
        # Exit plan
        "holding_window_days", "exit_by_date",
        "underlying_target_1", "underlying_target_2", "underlying_stop",
        "option_take_profit_1", "option_take_profit_2", "option_stop_loss",
        # Execution guidance
        "recommended_entry_price", "recommended_order_type", "max_chase_price",
        "fill_quality_score", "slippage_risk_label",
        # V2 target engine
        "projected_option_tp1", "target_projection_method",
        # PM/risk
        "risk_allowed", "position_size_tier", "iv_regime_label",
        # Structure
        "structure_archetype", "structure_policy_reason",
        # TRD-049 guardrails
        "entry_action", "quote_freshness_label", "fair_value_entry_low",
        "fair_value_entry_high", "entry_overpay_pct", "market_quality_label",
        # TRD-047 scenarios
        "scenarios_json",
        # Versioning
        "algo_version", "target_engine_version", "scenario_engine_version",
        "risk_framework_version", "guardrail_version",
        # Chain
        "chain_source", "underlying_price",
        # Score
        "score", "rationale", "features_json",
    ]

    def test_all_feature_groups_in_insert(self):
        c = _make_candidate(
            gamma=0.05, theta=-0.04, vega=0.12,
            quote_time="2026-06-06T09:31:00Z",
            entry_action="enter_now",
            quote_freshness_label="live",
            fair_value_entry_low=1.90,
            fair_value_entry_high=2.05,
            entry_overpay_pct=None,
            market_quality_label="acceptable",
            live_guardrail_reason="",
            projected_option_tp1=3.20,
            target_projection_method="delta_only",
            structure_archetype="medium_swing",
            structure_policy_reason="Medium tempo thesis",
            scenarios=[{
                "scenario_id": "fast_target",
                "projected_return_pct": 55.0,
                "days_to_resolution": 7,
                "input_method": "delta_approx",
                "projected_option_price": 3.25,
            }],
        )
        tc = {
            "thesis_date": "2026-06-06",
            "time_horizon": "2-4 weeks",
            "signal_agreement": 0.78,
            "entry_low": 145.0,
            "entry_high": 150.0,
            "days_to_earnings": 20,
            "heat_score": 0.80,
            "expected_move_pct": 4.0,
        }

        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            save_option_candidate_snapshot(
                _make_result([c]),
                run_date="2026-06-06",
                thesis_context=tc,
            )

        insert_sql = next((s for s in sql_list if "INSERT INTO option_candidate_snapshots" in s), None)
        assert insert_sql is not None, "No INSERT found"

        missing = [col for col in self.REQUIRED_COLUMNS if col not in insert_sql]
        assert missing == [], f"Missing columns from INSERT: {missing}"


# ══════════════════════════════════════════════════════════════════════════════
# Backward compatibility: old-style rows (minimal fields)
# ══════════════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """Rows persisted before TRD-050 have NULL for new columns — this must not break reads."""

    def test_candidate_without_new_trd050_fields_persists_safely(self):
        """A candidate with only the original TRD-026 fields must persist without error."""
        c = _make_candidate()  # no TRD-049/050 overrides; all default to None/empty
        conn_ctx, sql_list, _ = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            ids = save_option_candidate_snapshot(
                _make_result([c]), run_date="2026-06-06"
            )
        assert len(ids) >= 1, "Persistence should succeed even with minimal fields"

    def test_missing_optional_fields_produce_none_not_keyerror(self):
        """getattr(..., None) pattern must produce None, not raise AttributeError."""
        c = _make_candidate()
        # Deliberately strip out TRD-050 attributes to simulate an old-style candidate
        for attr in ("gamma", "theta", "vega", "quote_time", "entry_action",
                     "quote_freshness_label", "scenarios"):
            if hasattr(c, attr):
                object.__setattr__(c, attr, None)

        conn_ctx, sql_list, params_list = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            ids = save_option_candidate_snapshot(_make_result([c]), run_date="2026-06-06")
        assert len(ids) >= 1

    def test_suppression_row_with_no_candidate_data_persists_safely(self):
        """Suppression rows have no per-candidate fields; must still include versioning."""
        suppressed = CandidateResult(
            ticker="NVDA", generated_at="2026-06-06T12:00:00",
            suppressed=True, suppression_reason="Low IV rank",
            candidates=[], rejection_reasons=[],
            underlying_price=None, chain_source="yfinance",
        )
        conn_ctx, sql_list, _ = _capture_sql_mock((42,))
        with patch("utils.db.managed_connection", return_value=conn_ctx):
            ids = save_option_candidate_snapshot(suppressed, run_date="2026-06-06")

        assert len(ids) >= 1
        insert_sql = next((s for s in sql_list if "INSERT INTO option_candidate_snapshots" in s), None)
        assert "algo_version" in insert_sql
        # Guardrail/scenario/greek columns must NOT be in the suppression INSERT
        # (they're only in per-candidate rows)
        assert "entry_action" not in insert_sql
        assert "scenarios_json" not in insert_sql


# ══════════════════════════════════════════════════════════════════════════════
# OptionCandidate dataclass: Greeks fields exist
# ══════════════════════════════════════════════════════════════════════════════

class TestOptionCandidateGreeksFields:

    def test_gamma_theta_vega_default_none(self):
        c = _make_candidate()
        assert c.gamma is None
        assert c.theta is None
        assert c.vega  is None

    def test_gamma_theta_vega_settable(self):
        c = _make_candidate(gamma=0.03, theta=-0.02, vega=0.09)
        assert c.gamma == pytest.approx(0.03)
        assert c.theta == pytest.approx(-0.02)
        assert c.vega  == pytest.approx(0.09)
