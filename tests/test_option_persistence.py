"""
Tests for option candidate snapshot persistence  (TRD-026).
All DB I/O is mocked — no live Supabase required.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from utils.option_candidates import (
    CandidateResult,
    OptionCandidate,
    ThesisContext,
    _compute_exit_plan,
    get_option_candidates,
)
from utils.supabase_persist import save_option_candidate_snapshot


# ── Helpers ────────────────────────────────────────────────────────────────────

def _expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _candidate(ticker="AAPL", right="C", dte=21, strike=150.0, mid=2.10,
               delta=0.40, iv=0.35, oi=500, preset="long_call",
               score=68.0, **extra) -> OptionCandidate:
    expiry = _expiry(dte)
    from utils.option_candidates import ThesisContext, _compute_exit_plan
    thesis = ThesisContext(
        ticker=ticker, direction="BULL", conviction=3,
        target_1=160.0, target_2=175.0, stop_loss=140.0,
    )
    exit_plan = _compute_exit_plan(
        type('C', (), {'dte': dte, 'expiry': expiry, 'right': right, 'mid': mid})(),
        preset,
        thesis,
    )
    return OptionCandidate(
        ticker=ticker, expiry=expiry, strike=strike, right=right, dte=dte,
        bid=mid - 0.10, ask=mid + 0.10, mid=mid, spread_pct=9.5,
        delta=delta, implied_vol=iv, open_interest=oi, volume=100,
        breakeven=round(strike + mid, 2) if right == "C" else round(strike - mid, 2),
        score=score, rationale=f"bullish long {right} — Δ{delta:+.2f}",
        strategy_preset=preset, source="yfinance",
        **exit_plan,
    )


def _mock_conn(inserted_id: int = 42):
    """Return a mock _conn()-style context manager."""
    mock_cur = MagicMock()
    # regclass check
    mock_cur.fetchone.side_effect = [
        ("option_candidate_snapshots",),   # to_regclass check
        (inserted_id,),                    # first INSERT RETURNING id
        (inserted_id + 1,),                # second INSERT
        (inserted_id + 2,),                # third INSERT
    ]
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_cur)
    ctx.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = ctx
    conn.commit.return_value = None

    conn_ctx = MagicMock()
    conn_ctx.__enter__ = MagicMock(return_value=conn)
    conn_ctx.__exit__ = MagicMock(return_value=False)
    return conn_ctx


# ══════════════════════════════════════════════════════════════════════════════
# Exit plan computation  (part of TRD-026 requirements)
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeExitPlan:
    def _contract(self, dte=21, expiry=None, right="C", mid=2.10):
        expiry = expiry or _expiry(dte)
        return type("C", (), {"dte": dte, "expiry": expiry, "right": right, "mid": mid})()

    def _thesis(self, **kw):
        defaults = dict(ticker="AAPL", direction="BULL", conviction=3,
                        target_1=160.0, target_2=175.0, stop_loss=140.0)
        defaults.update(kw)
        return ThesisContext(**defaults)

    def test_holding_window_swing_half_dte(self):
        plan = _compute_exit_plan(self._contract(dte=30), "long_call", self._thesis())
        assert plan["holding_window_days"] == 15

    def test_holding_window_leaps_fixed_90d(self):
        plan = _compute_exit_plan(self._contract(dte=210), "leaps_call", self._thesis())
        assert plan["holding_window_days"] == 90

    def test_holding_window_min_7(self):
        plan = _compute_exit_plan(self._contract(dte=10), "long_call", self._thesis())
        assert plan["holding_window_days"] >= 7

    def test_exit_by_date_before_expiry(self):
        dte = 30
        plan = _compute_exit_plan(self._contract(dte=dte), "long_call", self._thesis())
        assert plan["exit_by_date"] is not None
        from datetime import date as d, timedelta
        exp = d.fromisoformat(_expiry(dte))
        exit_dt = d.fromisoformat(plan["exit_by_date"])
        assert exit_dt <= exp

    def test_option_take_profit_1_50pct_gain(self):
        plan = _compute_exit_plan(self._contract(mid=2.00), "long_call", self._thesis())
        assert plan["option_take_profit_1"] == pytest.approx(3.00)

    def test_option_take_profit_2_100pct_gain(self):
        plan = _compute_exit_plan(self._contract(mid=2.00), "long_call", self._thesis())
        assert plan["option_take_profit_2"] == pytest.approx(4.00)

    def test_option_stop_loss_50pct_loss(self):
        plan = _compute_exit_plan(self._contract(mid=2.00), "long_call", self._thesis())
        assert plan["option_stop_loss"] == pytest.approx(1.00)

    def test_underlying_targets_from_thesis(self):
        thesis = self._thesis(target_1=165.0, target_2=180.0, stop_loss=142.0)
        plan = _compute_exit_plan(self._contract(), "long_call", thesis)
        assert plan["underlying_target_1"] == 165.0
        assert plan["underlying_target_2"] == 180.0
        assert plan["underlying_stop"] == 142.0

    def test_event_exit_rule_set_when_earnings_within_window(self):
        thesis = self._thesis(days_to_earnings=10)
        plan = _compute_exit_plan(self._contract(dte=30), "long_call", thesis)
        assert plan["event_exit_rule"] is not None
        assert "earnings" in plan["event_exit_rule"].lower()

    def test_event_exit_rule_none_when_earnings_far(self):
        thesis = self._thesis(days_to_earnings=60)
        plan = _compute_exit_plan(self._contract(dte=21), "long_call", thesis)
        assert plan["event_exit_rule"] is None

    def test_max_holding_rule_present(self):
        plan = _compute_exit_plan(self._contract(), "long_call", self._thesis())
        assert plan["max_holding_rule"] is not None
        assert len(plan["max_holding_rule"]) > 0


# ══════════════════════════════════════════════════════════════════════════════
# Candidate exits plan propagation
# ══════════════════════════════════════════════════════════════════════════════

class TestCandidateExitFields:
    def test_candidate_has_exit_plan_fields(self):
        c = _candidate()
        assert c.holding_window_days is not None
        assert c.exit_by_date is not None
        assert c.option_take_profit_1 is not None
        assert c.option_stop_loss is not None
        assert c.max_holding_rule is not None

    def test_candidate_underlying_targets_from_thesis(self):
        c = _candidate()
        assert c.underlying_target_1 == 160.0
        assert c.underlying_target_2 == 175.0
        assert c.underlying_stop == 140.0

    def test_put_candidate_exit_plan(self):
        from utils.option_candidates import ThesisContext, _compute_exit_plan
        thesis = ThesisContext(
            ticker="AAPL", direction="BEAR", conviction=3,
            target_1=130.0, target_2=115.0, stop_loss=155.0,
        )
        contract = type("C", (), {"dte": 21, "expiry": _expiry(21), "right": "P", "mid": 3.0})()
        plan = _compute_exit_plan(contract, "long_put", thesis)
        assert plan["underlying_target_1"] == 130.0
        assert plan["underlying_stop"] == 155.0


# ══════════════════════════════════════════════════════════════════════════════
# Persistence helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveOptionCandidateSnapshot:
    def _bull_result(self, n_candidates=1) -> CandidateResult:
        candidates = [_candidate() for _ in range(n_candidates)]
        return CandidateResult(
            ticker="AAPL", generated_at="2026-05-30T12:00:00",
            suppressed=False, candidates=candidates,
            rejection_reasons=["C 155.0 2026-06-20: OI 10 < 50 minimum"],
            underlying_price=148.0, chain_source="yfinance",
        )

    def _suppressed_result(self, reason: str = "Conviction too low") -> CandidateResult:
        return CandidateResult(
            ticker="MSFT", generated_at="2026-05-30T12:00:00",
            suppressed=True, suppression_reason=reason,
            candidates=[], rejection_reasons=[],
            underlying_price=None, chain_source="unknown",
        )

    @patch("utils.db.managed_connection")
    def test_candidate_rows_persisted(self, mock_conn_fn):
        mock_conn_fn.return_value = _mock_conn(42)
        result = self._bull_result(n_candidates=1)
        ids = save_option_candidate_snapshot(result, run_date="2026-05-30")
        assert len(ids) >= 1

    @patch("utils.db.managed_connection")
    def test_three_candidates_produce_three_rows(self, mock_conn_fn):
        mock_conn_fn.return_value = _mock_conn(10)
        result = self._bull_result(n_candidates=3)
        ids = save_option_candidate_snapshot(result, run_date="2026-05-30")
        assert len(ids) == 3

    @patch("utils.db.managed_connection")
    def test_suppressed_result_persists_one_row(self, mock_conn_fn):
        mock_conn_fn.return_value = _mock_conn(99)
        result = self._suppressed_result()
        ids = save_option_candidate_snapshot(result, run_date="2026-05-30")
        assert len(ids) == 1

    @patch("utils.db.managed_connection")
    def test_insert_includes_exit_plan_fields(self, mock_conn_fn):
        """Verify that exit-plan fields are included in the INSERT statement."""
        captured_calls: list[str] = []

        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            ("option_candidate_snapshots",),
            (42,),
        ]

        def capture_execute(sql, params=None):
            captured_calls.append(sql)
        mock_cur.execute.side_effect = capture_execute

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_cur)
        ctx.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = ctx

        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(return_value=conn)
        conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_conn_fn.return_value = conn_ctx

        result = self._bull_result(n_candidates=1)
        save_option_candidate_snapshot(result, run_date="2026-05-30")

        # Find the INSERT statement
        insert_sqls = [s for s in captured_calls if "INSERT INTO option_candidate_snapshots" in s]
        assert len(insert_sqls) >= 1
        insert_sql = insert_sqls[0]
        for field in ["holding_window_days", "exit_by_date",
                      "option_take_profit_1", "option_stop_loss",
                      "underlying_target_1", "underlying_stop"]:
            assert field in insert_sql, f"Expected '{field}' in INSERT SQL"

    @patch("utils.db.managed_connection")
    def test_db_failure_returns_empty_list(self, mock_conn_fn):
        mock_conn_fn.side_effect = Exception("db unavailable")
        result = self._bull_result(n_candidates=1)
        ids = save_option_candidate_snapshot(result, run_date="2026-05-30")
        assert ids == []

    @patch("utils.db.managed_connection")
    def test_missing_table_returns_empty_list(self, mock_conn_fn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (None,)   # to_regclass returns NULL
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_cur)
        ctx.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = ctx
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(return_value=conn)
        conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_conn_fn.return_value = conn_ctx

        result = self._bull_result(n_candidates=1)
        ids = save_option_candidate_snapshot(result, run_date="2026-05-30")
        assert ids == []


# ══════════════════════════════════════════════════════════════════════════════
# Issue #1 regression: schema column name must be "iv", not "implied_vol"
# ══════════════════════════════════════════════════════════════════════════════

class TestIVColumnName:
    """
    The migration schema uses the column name ``iv`` for implied volatility.
    The Python dataclass uses ``implied_vol``.  The persistence layer must
    map ``implied_vol`` → ``iv`` at insert time.

    This test class would fail if the two names drifted apart again.
    """

    def _capture_insert_sql(self, result) -> str:
        """Run save_option_candidate_snapshot with a capturing mock and return the INSERT SQL."""
        captured: list[str] = []
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            ("option_candidate_snapshots",),
            (1,),
        ]

        def capture(sql, params=None):
            captured.append(sql)

        mock_cur.execute.side_effect = capture
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_cur)
        ctx.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = ctx
        cctx = MagicMock()
        cctx.__enter__ = MagicMock(return_value=conn)
        cctx.__exit__ = MagicMock(return_value=False)

        with patch("utils.db.managed_connection", return_value=cctx):
            save_option_candidate_snapshot(result, run_date="2026-05-30")

        inserts = [s for s in captured if "INSERT INTO option_candidate_snapshots" in s]
        assert inserts, "Expected at least one INSERT statement"
        return inserts[0]

    def _make_result(self) -> CandidateResult:
        c = _candidate(iv=0.38)
        return CandidateResult(
            ticker="AAPL", generated_at="2026-05-30T12:00:00",
            suppressed=False, candidates=[c],
            rejection_reasons=[], underlying_price=148.0, chain_source="yfinance",
        )

    def test_insert_uses_iv_not_implied_vol(self):
        sql = self._capture_insert_sql(self._make_result())
        assert "iv" in sql, "Column 'iv' must appear in INSERT"
        assert "implied_vol" not in sql, (
            "Column name 'implied_vol' must NOT appear in INSERT — "
            "schema uses 'iv'; this would cause a column-not-found error"
        )

    def test_insert_does_not_contain_implied_vol(self):
        """Regression guard: if this fails, the schema drift is back."""
        sql = self._capture_insert_sql(self._make_result())
        assert "implied_vol" not in sql

    def test_features_json_uses_iv_key(self):
        """The features_json blob must also use 'iv' for downstream ML/analytics."""
        captured_params: list[list] = []
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            ("option_candidate_snapshots",),
            (1,),
        ]

        def capture(sql, params=None):
            if params:
                captured_params.append(list(params))

        mock_cur.execute.side_effect = capture
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_cur)
        ctx.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = ctx
        cctx = MagicMock()
        cctx.__enter__ = MagicMock(return_value=conn)
        cctx.__exit__ = MagicMock(return_value=False)

        with patch("utils.db.managed_connection", return_value=cctx):
            save_option_candidate_snapshot(self._make_result(), run_date="2026-05-30")

        # Find the INSERT params and look for features_json
        all_params = [p for ps in captured_params for p in ps]
        json_blobs = [p for p in all_params if isinstance(p, str) and '"iv"' in p]
        assert json_blobs, (
            "features_json must use key 'iv' — found no JSON blob containing '\"iv\"'"
        )
        # Guard: must not use 'implied_vol' key inside features_json either
        bad_blobs = [p for p in all_params if isinstance(p, str) and '"implied_vol"' in p]
        assert not bad_blobs, "features_json must not use key 'implied_vol'"


# ══════════════════════════════════════════════════════════════════════════════
# Issue #3: thesis context fields persisted in base dict
# ══════════════════════════════════════════════════════════════════════════════

class TestThesisContextPersisted:
    """thesis_date, time_horizon, and signal_agreement must be persisted when provided."""

    def _capture_insert_params(self, thesis_context: dict) -> list:
        """Return the INSERT params list from a single save call."""
        captured_params: list[list] = []
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            ("option_candidate_snapshots",),
            (1,),
        ]

        def capture(sql, params=None):
            if params:
                captured_params.append(list(params))

        mock_cur.execute.side_effect = capture
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_cur)
        ctx.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = ctx
        cctx = MagicMock()
        cctx.__enter__ = MagicMock(return_value=conn)
        cctx.__exit__ = MagicMock(return_value=False)

        c = _candidate()
        result = CandidateResult(
            ticker="AAPL", generated_at="2026-05-30T12:00:00",
            suppressed=False, candidates=[c],
            rejection_reasons=[], underlying_price=148.0, chain_source="yfinance",
        )

        with patch("utils.db.managed_connection", return_value=cctx):
            save_option_candidate_snapshot(
                result, run_date="2026-05-30", thesis_context=thesis_context
            )

        # Flatten all INSERT param lists
        return [p for ps in captured_params for p in ps]

    def test_thesis_date_persisted(self):
        params = self._capture_insert_params({"thesis_date": "2026-05-29"})
        assert "2026-05-29" in params, "thesis_date must appear in INSERT values"

    def test_time_horizon_persisted(self):
        params = self._capture_insert_params({"time_horizon": "2-4 weeks"})
        assert "2-4 weeks" in params, "time_horizon must appear in INSERT values"

    def test_signal_agreement_persisted(self):
        params = self._capture_insert_params({"signal_agreement": 0.78})
        assert 0.78 in params, "signal_agreement must appear in INSERT values"

    def test_missing_thesis_context_is_graceful(self):
        """None thesis_context must not raise — fields default to None."""
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            ("option_candidate_snapshots",),
            (1,),
        ]
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_cur)
        ctx.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = ctx
        cctx = MagicMock()
        cctx.__enter__ = MagicMock(return_value=conn)
        cctx.__exit__ = MagicMock(return_value=False)

        c = _candidate()
        result = CandidateResult(
            ticker="AAPL", generated_at="2026-05-30T12:00:00",
            suppressed=False, candidates=[c], rejection_reasons=[],
            underlying_price=148.0, chain_source="yfinance",
        )
        with patch("utils.db.managed_connection", return_value=cctx):
            # Must not raise
            ids = save_option_candidate_snapshot(result, run_date="2026-05-30", thesis_context=None)
        assert isinstance(ids, list)

    def test_insert_sql_includes_thesis_context_columns(self):
        """Regression: thesis_date, time_horizon, signal_agreement must be in INSERT."""
        captured_sql: list[str] = []
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            ("option_candidate_snapshots",),
            (1,),
        ]

        def capture(sql, params=None):
            captured_sql.append(sql)

        mock_cur.execute.side_effect = capture
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_cur)
        ctx.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = ctx
        cctx = MagicMock()
        cctx.__enter__ = MagicMock(return_value=conn)
        cctx.__exit__ = MagicMock(return_value=False)

        c = _candidate()
        result = CandidateResult(
            ticker="AAPL", generated_at="2026-05-30T12:00:00",
            suppressed=False, candidates=[c], rejection_reasons=[],
            underlying_price=148.0, chain_source="yfinance",
        )
        with patch("utils.db.managed_connection", return_value=cctx):
            save_option_candidate_snapshot(
                result, run_date="2026-05-30",
                thesis_context={"thesis_date": "2026-05-29", "time_horizon": "2-4 weeks", "signal_agreement": 0.78},
            )

        inserts = [s for s in captured_sql if "INSERT INTO option_candidate_snapshots" in s]
        assert inserts
        sql = inserts[0]
        for col in ("thesis_date", "time_horizon", "signal_agreement"):
            assert col in sql, f"Column '{col}' missing from INSERT SQL"
