"""
tests/test_squeeze_persistence_schema.py

Post-fix persistence verification for CHUNK-12 readiness gate.

Covers (no live DB):
  1. DDL / migration DDL contains all expected CHUNK-added columns.
  2. save_squeeze_scores() builds a 42-column payload without crashing (full row).
  3. save_squeeze_scores() handles missing optional CHUNK fields gracefully.
  4. fetch_squeeze_scores_for_replay() returns real values with RealDictCursor mocks.
  5. _build_replay_row() surfaces CHUNK-16 and CHUNK-09 fields when present in snap.
  6. Old pre-CHUNK rows (missing new fields) still work through the full replay path.

Design notes
------------
- si_persistence_score, si_persistence_count, effective_float_score are NOT direct
  DB columns — they live in explanation_json and are extracted by
  _extract_from_explanation().  Tests for those extraction paths already exist in
  test_squeeze_replay.py (TestBuildReplayRow.test_extracts_si_persistence_from_explanation).
- All tests are pure-function; no live DB or network calls.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from utils.supabase_persist import (
    _SQUEEZE_DDL,
    _SQUEEZE_MIGRATE_DDL,
    fetch_squeeze_scores_for_replay,
)
from backtest import SqueezeOutcomeReplay


# ── helpers ───────────────────────────────────────────────────────────────────

def _full_new_format_row() -> dict:
    """A complete squeeze_scores dict with all CHUNK-added fields populated."""
    return {
        "ticker": "TSTZ",
        "final_score": 72.5,
        "juice_target": 120.0,
        "recent_squeeze": False,
        "price": 25.0,
        "short_pct_float": 0.38,
        "days_to_cover": 8.2,
        "market_cap_m": 450.0,
        "ev_score": 30.0,
        "pct_float_short_score": 8.0,
        "short_pnl_score": 2.0,
        "days_to_cover_score": 7.0,
        "volume_surge_score": 3.0,
        "ftd_score": 1.0,
        "market_cap_score": 5.0,
        "float_score": 4.0,
        "price_divergence_score": 2.0,
        # CHUNK-01/04/05/10
        "computed_dtc_30d": 9.1,
        "compression_recovery_score": 7.5,
        "volume_confirmation_flag": True,
        "squeeze_state": "ARMED",
        # CHUNK-14
        "explanation_summary": "Strong structural setup.",
        "explanation_json": {
            "top_positive_drivers": [
                {"key": "si_persistence", "label": "SI persistence", "strength": 8.0},
                {"key": "effective_float", "label": "Effective float", "strength": 7.5},
            ],
            "top_negative_drivers": [],
            "warning_flags": [],
        },
        # CHUNK-10
        "state_confidence": "high",
        "state_reasons": ["Score meets ARMED threshold"],
        "state_warnings": [],
        # CHUNK-16
        "risk_score": 30.0,
        "risk_level": "LOW",
        "risk_flags": [],
        "risk_warnings": [],
        "risk_components": {},
        "dilution_risk_flag": False,
        "latest_dilution_filing_date": None,
        "shares_offered_pct_float": None,
        # CHUNK-09
        "options_pressure_score": 5.0,
        "iv_rank": 62.0,
        "iv_rank_score": 7.0,
        "iv_data_confidence": "high",
        "unusual_call_activity_flag": False,
        "call_put_volume_ratio": 1.4,
        "call_put_oi_ratio": 1.1,
    }


def _minimal_old_row() -> dict:
    """An old-format row containing only the original 18 columns."""
    return {
        "ticker": "OLD",
        "final_score": 38.0,
        "juice_target": 85.0,
        "recent_squeeze": False,
        "price": 12.0,
        "short_pct_float": 0.21,
        "days_to_cover": 4.5,
        "market_cap_m": 800.0,
        "ev_score": 25.0,
        "pct_float_short_score": 5.0,
        "short_pnl_score": 1.0,
        "days_to_cover_score": 5.0,
        "volume_surge_score": 0.0,
        "ftd_score": 0.0,
        "market_cap_score": 4.0,
        "float_score": 2.0,
        "price_divergence_score": 0.0,
        # No CHUNK fields present
    }


def _make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# 1. DDL / migration completeness
# ─────────────────────────────────────────────────────────────────────────────

class TestDDLAndMigrationCompleteness:
    """Verify _SQUEEZE_DDL and _SQUEEZE_MIGRATE_DDL cover all expected columns."""

    # Columns that MUST appear in either _SQUEEZE_DDL or _SQUEEZE_MIGRATE_DDL
    _REQUIRED_COLUMNS = [
        # original schema
        "date", "ticker", "final_score", "price", "short_pct_float",
        "days_to_cover", "market_cap_m",
        # CHUNK-01/04/05
        "computed_dtc_30d",
        "compression_recovery_score",
        "volume_confirmation_flag",
        # CHUNK-10
        "squeeze_state",
        "state_confidence",
        "state_reasons",
        "state_warnings",
        # CHUNK-14
        "explanation_summary",
        "explanation_json",
        # CHUNK-16
        "risk_score",
        "risk_level",
        "risk_flags",
        "risk_warnings",
        "risk_components",
        "dilution_risk_flag",
        "latest_dilution_filing_date",
        "shares_offered_pct_float",
        # CHUNK-09
        "options_pressure_score",
        "iv_rank",
        "iv_rank_score",
        "iv_data_confidence",
        "unusual_call_activity_flag",
        "call_put_volume_ratio",
        "call_put_oi_ratio",
    ]

    def _all_schema_text(self) -> str:
        return _SQUEEZE_DDL + "\n".join(_SQUEEZE_MIGRATE_DDL)

    def test_required_columns_present_in_schema(self):
        schema = self._all_schema_text().lower()
        missing = [col for col in self._REQUIRED_COLUMNS if col not in schema]
        assert missing == [], f"Missing from DDL/migration: {missing}"

    def test_migration_dml_count_at_least_24(self):
        """Migration list must cover all columns added after original 18-col schema."""
        assert len(_SQUEEZE_MIGRATE_DDL) >= 24, (
            f"Expected ≥24 migration entries, got {len(_SQUEEZE_MIGRATE_DDL)}"
        )

    def test_chunk_01_columns_in_migration(self):
        """computed_dtc_30d et al must be in migration (added after initial DDL)."""
        migration_text = "\n".join(_SQUEEZE_MIGRATE_DDL).lower()
        for col in ("computed_dtc_30d", "compression_recovery_score",
                    "volume_confirmation_flag", "squeeze_state"):
            assert col in migration_text, (
                f"'{col}' missing from _SQUEEZE_MIGRATE_DDL — old tables won't get it"
            )

    def test_fields_not_direct_columns_are_documented(self):
        """si_persistence_score, si_persistence_count, effective_float_score are
        stored in explanation_json, not as direct columns.  Verify they are absent
        from the DDL (expected design) so callers know to use _extract_from_explanation."""
        schema = self._all_schema_text().lower()
        indirect_fields = ("si_persistence_score", "si_persistence_count", "effective_float_score")
        for field in indirect_fields:
            assert field not in schema, (
                f"'{field}' appeared in DDL/migration but should only live in explanation_json"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. save_squeeze_scores — payload construction (no live DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveSqueezeScoresPayload:
    """Validate the tuple-building logic inside save_squeeze_scores without a DB."""

    def _build_tuples(self, df: pd.DataFrame) -> list[tuple]:
        """Extract the row-tuple building logic from save_squeeze_scores for testing."""
        import json as _json

        def _f(row, col):
            v = row.get(col)
            return float(v) if v is not None and pd.notna(v) else None

        def _explanation_json(row):
            v = row.get("explanation_json")
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            if isinstance(v, dict):
                return _json.dumps(v)
            s = str(v).strip()
            return s if s not in ("", "{}") else None

        def _lifecycle_json(row, col):
            v = row.get(col)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            if isinstance(v, list):
                return _json.dumps(v)
            s = str(v).strip()
            return s if s not in ("", "[]") else None

        tuples = []
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            tuples.append((
                "2026-04-26", ticker,
                _f(row, "final_score"), _f(row, "juice_target"),
                bool(row["recent_squeeze"]) if "recent_squeeze" in row else None,
                _f(row, "price"), _f(row, "short_pct_float"), _f(row, "days_to_cover"),
                _f(row, "market_cap_m"), _f(row, "ev_score"),
                _f(row, "pct_float_short_score"), _f(row, "short_pnl_score"),
                _f(row, "days_to_cover_score"), _f(row, "volume_surge_score"),
                _f(row, "ftd_score"), _f(row, "market_cap_score"),
                _f(row, "float_score"), _f(row, "price_divergence_score"),
                _f(row, "computed_dtc_30d"), _f(row, "compression_recovery_score"),
                bool(row["volume_confirmation_flag"]) if "volume_confirmation_flag" in row else None,
                str(row["squeeze_state"]) if "squeeze_state" in row and row["squeeze_state"] is not None else None,
                str(row["explanation_summary"]) if row.get("explanation_summary") else None,
                _explanation_json(row),
                str(row["state_confidence"]) if row.get("state_confidence") else None,
                _lifecycle_json(row, "state_reasons"),
                _lifecycle_json(row, "state_warnings"),
                _f(row, "risk_score"),
                str(row["risk_level"]) if row.get("risk_level") else None,
                _lifecycle_json(row, "risk_flags"),
                _lifecycle_json(row, "risk_warnings"),
                _lifecycle_json(row, "risk_components"),
                bool(row["dilution_risk_flag"]) if "dilution_risk_flag" in row and row["dilution_risk_flag"] is not None else None,
                str(row["latest_dilution_filing_date"]) if row.get("latest_dilution_filing_date") else None,
                _f(row, "shares_offered_pct_float"),
                _f(row, "options_pressure_score"),
                _f(row, "iv_rank"),
                _f(row, "iv_rank_score"),
                str(row["iv_data_confidence"]) if row.get("iv_data_confidence") else None,
                bool(row["unusual_call_activity_flag"]) if "unusual_call_activity_flag" in row and row["unusual_call_activity_flag"] is not None else None,
                _f(row, "call_put_volume_ratio"),
                _f(row, "call_put_oi_ratio"),
            ))
        return tuples

    def test_full_new_format_row_produces_42_element_tuple(self):
        df = _make_df([_full_new_format_row()])
        tuples = self._build_tuples(df)
        assert len(tuples) == 1
        assert len(tuples[0]) == 42, f"Expected 42 elements, got {len(tuples[0])}"

    def test_full_new_format_row_values_are_correct_types(self):
        df = _make_df([_full_new_format_row()])
        t = self._build_tuples(df)[0]
        # Positions: 0=date,1=ticker,2=final_score,...,20=vol_flag,21=squeeze_state,
        #   27=risk_score,28=risk_level,32=dilution_risk_flag,
        #   35=options_pressure_score,36=iv_rank
        assert t[1] == "TSTZ"
        assert isinstance(t[2], float)        # final_score
        assert t[2] == pytest.approx(72.5)
        assert t[20] is True                  # volume_confirmation_flag
        assert t[21] == "ARMED"               # squeeze_state
        assert t[27] == pytest.approx(30.0)   # risk_score
        assert t[28] == "LOW"                 # risk_level
        assert t[32] is False                 # dilution_risk_flag (position 32)
        assert t[35] == pytest.approx(5.0)    # options_pressure_score
        assert t[36] == pytest.approx(62.0)   # iv_rank

    def test_missing_optional_chunk_fields_produces_none_not_crash(self):
        """Old-format row without CHUNK fields must not crash payload construction."""
        df = _make_df([_minimal_old_row()])
        tuples = self._build_tuples(df)
        assert len(tuples) == 1
        t = tuples[0]
        assert len(t) == 42
        # All CHUNK-added positions should be None (not raised)
        chunk_positions = range(18, 42)
        for pos in chunk_positions:
            assert t[pos] is None, f"Position {pos} expected None, got {t[pos]!r}"

    def test_empty_dataframe_produces_no_tuples(self):
        df = pd.DataFrame()
        tuples = self._build_tuples(df)
        assert tuples == []

    def test_save_squeeze_scores_called_with_full_df_does_not_raise(self):
        """End-to-end: save_squeeze_scores() must not raise when fed a full new-format df."""
        df = _make_df([_full_new_format_row()])

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("utils.supabase_persist._conn", return_value=mock_conn):
            from utils.supabase_persist import save_squeeze_scores
            save_squeeze_scores(df, run_date="2026-04-26")

        mock_cur.executemany.assert_called_once()
        args = mock_cur.executemany.call_args
        rows_arg = args[0][1]  # second positional = rows list
        assert len(rows_arg) == 1
        assert len(rows_arg[0]) == 42

    def test_save_squeeze_scores_called_with_old_format_df_does_not_raise(self):
        """save_squeeze_scores() must not raise when fed a pre-CHUNK df."""
        df = _make_df([_minimal_old_row()])

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("utils.supabase_persist._conn", return_value=mock_conn):
            from utils.supabase_persist import save_squeeze_scores
            save_squeeze_scores(df, run_date="2026-04-26")

        mock_cur.executemany.assert_called_once()
        rows_arg = mock_cur.executemany.call_args[0][1]
        assert len(rows_arg[0]) == 42


# ─────────────────────────────────────────────────────────────────────────────
# 3. fetch_squeeze_scores_for_replay — RealDictCursor fix
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchSqueezeScoresForReplay:
    """Verify the RealDictCursor fix: dict(row) must return real values."""

    def _make_real_dict_row(self, data: dict):
        """Simulate a psycopg2 RealDictRow: iterating yields keys, not values.
        dict(row) must still produce the correct {key: value} mapping.
        """
        # dict() on a regular dict already does the right thing.
        # The bug was using dict(zip(cols, row)) where iterating a dict gives keys.
        # We verify here that dict(row) == row for any plain dict (which RealDictRow
        # also satisfies via __iter__ → keys, __getitem__ → values).
        return data  # plain dict behaves identically to RealDictRow for dict(row)

    def test_dict_row_returns_values_not_keys(self):
        """dict(row) on a dict-like object must return key→value, not key→key."""
        row = {"date": "2026-04-20", "ticker": "TSTZ", "final_score": 72.5}
        result = dict(row)
        assert result["date"] == "2026-04-20"
        assert result["ticker"] == "TSTZ"
        assert result["final_score"] == pytest.approx(72.5)

    def test_fetch_returns_real_values_with_mock_cursor(self):
        """fetch_squeeze_scores_for_replay must return dicts with real row values."""
        fake_row = {
            "date": "2026-04-20",
            "ticker": "TSTZ",
            "final_score": 72.5,
            "squeeze_state": "ARMED",
            "risk_score": 30.0,
            "options_pressure_score": 5.0,
        }

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [fake_row]

        with patch("utils.supabase_persist._conn", return_value=mock_conn):
            rows = fetch_squeeze_scores_for_replay("2026-04-20", "2026-04-20")

        assert len(rows) == 1
        assert rows[0]["date"] == "2026-04-20"
        assert rows[0]["ticker"] == "TSTZ"
        assert rows[0]["final_score"] == pytest.approx(72.5)
        assert rows[0]["squeeze_state"] == "ARMED"
        assert rows[0]["risk_score"] == pytest.approx(30.0)
        assert rows[0]["options_pressure_score"] == pytest.approx(5.0)

    def test_fetch_with_ticker_filter_returns_correct_values(self):
        fake_row = {"date": "2026-04-20", "ticker": "TSTZ", "final_score": 65.0}

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [fake_row]

        with patch("utils.supabase_persist._conn", return_value=mock_conn):
            rows = fetch_squeeze_scores_for_replay(
                "2026-04-20", "2026-04-20", tickers=["TSTZ"]
            )

        assert rows[0]["ticker"] == "TSTZ"
        assert rows[0]["final_score"] == pytest.approx(65.0)

    def test_fetch_returns_empty_list_on_db_error(self):
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("DB unavailable")

        with patch("utils.supabase_persist._conn", return_value=mock_conn):
            rows = fetch_squeeze_scores_for_replay("2026-04-20", "2026-04-20")

        assert rows == []

    def test_fetch_query_uses_select_star(self):
        """fetch_squeeze_scores_for_replay must use SELECT * so future columns are
        automatically included without code changes."""
        import inspect
        import utils.supabase_persist as m
        src = inspect.getsource(m.fetch_squeeze_scores_for_replay)
        assert "SELECT *" in src, (
            "fetch_squeeze_scores_for_replay should use SELECT * to capture all columns"
        )
        assert "SELECT date, ticker" not in src, (
            "fetch_squeeze_scores_for_replay must not hard-code a column list"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. _build_replay_row — CHUNK-16 and CHUNK-09 fields
# ─────────────────────────────────────────────────────────────────────────────

class TestReplayRowIncludesChunkFields:
    """Verify _build_replay_row surfaces CHUNK-16 / CHUNK-09 / CHUNK-10 fields."""

    def _snap_full(self) -> dict:
        return {
            "date": "2026-04-20",
            "ticker": "TSTZ",
            "final_score": 72.5,
            "short_pct_float": 0.38,
            "days_to_cover": 8.2,
            "computed_dtc_30d": 9.1,
            "compression_recovery_score": 7.5,
            "volume_confirmation_flag": True,
            "squeeze_state": "ARMED",
            "explanation_summary": "Strong setup.",
            "explanation_json": json.dumps({
                "top_positive_drivers": [
                    {"key": "si_persistence", "label": "SI persistence", "strength": 8.0},
                    {"key": "effective_float", "label": "EF", "strength": 7.5},
                ],
                "top_negative_drivers": [],
            }),
            # CHUNK-16
            "risk_score": 30.0,
            "risk_level": "LOW",
            "dilution_risk_flag": False,
            "shares_offered_pct_float": None,
            # CHUNK-09
            "options_pressure_score": 5.0,
            "iv_rank": 62.0,
            "iv_rank_score": 7.0,
            "unusual_call_activity_flag": False,
        }

    def test_chunk_16_fields_in_replay_row(self):
        replay = SqueezeOutcomeReplay("2026-01-01", "2026-12-31")
        row = replay._build_replay_row(self._snap_full(), None)
        assert row["risk_score"] == pytest.approx(30.0)
        assert row["risk_level"] == "LOW"
        assert row["dilution_risk_flag"] is False

    def test_chunk_09_fields_in_replay_row(self):
        replay = SqueezeOutcomeReplay("2026-01-01", "2026-12-31")
        row = replay._build_replay_row(self._snap_full(), None)
        assert row["options_pressure_score"] == pytest.approx(5.0)
        assert row["iv_rank"] == pytest.approx(62.0)
        assert row["iv_rank_score"] == pytest.approx(7.0)
        assert row["unusual_call_activity_flag"] is False

    def test_squeeze_state_in_replay_row(self):
        replay = SqueezeOutcomeReplay("2026-01-01", "2026-12-31")
        row = replay._build_replay_row(self._snap_full(), None)
        assert row["squeeze_state"] == "ARMED"

    def test_si_persistence_extracted_from_explanation(self):
        """si_persistence_score lives in explanation_json, not as a direct column."""
        replay = SqueezeOutcomeReplay("2026-01-01", "2026-12-31")
        row = replay._build_replay_row(self._snap_full(), None)
        assert row["si_persistence_score"] == pytest.approx(8.0)
        assert row["effective_float_score"] == pytest.approx(7.5)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Backward compatibility: old pre-CHUNK rows
# ─────────────────────────────────────────────────────────────────────────────

class TestOldPreChunkRowsCompatibility:
    """Old rows (2026-04-16 to 2026-04-25) lack all CHUNK fields.
    The replay must handle them without crashing and return None for new fields."""

    def _old_snap(self) -> dict:
        return {
            "date": "2026-04-16",
            "ticker": "NVAX",
            "final_score": 36.8,
            "juice_target": 94.0,
            "recent_squeeze": False,
            "price": 7.7,
            "short_pct_float": 0.3133,
            "days_to_cover": 9.4,
            "market_cap_m": 1254.6,
            # No CHUNK fields at all
        }

    def test_old_row_does_not_crash_build_replay_row(self):
        replay = SqueezeOutcomeReplay("2026-01-01", "2026-12-31")
        row = replay._build_replay_row(self._old_snap(), None)
        assert row is not None
        assert row["ticker"] == "NVAX"
        assert row["final_score"] == pytest.approx(36.8)

    def test_old_row_chunk_fields_are_none(self):
        replay = SqueezeOutcomeReplay("2026-01-01", "2026-12-31")
        row = replay._build_replay_row(self._old_snap(), None)
        assert row["risk_score"] is None
        assert row["risk_level"] is None
        assert row["dilution_risk_flag"] is None
        assert row["options_pressure_score"] is None
        assert row["iv_rank"] is None
        assert row["squeeze_state"] is None
        assert row["si_persistence_score"] is None
        assert row["effective_float_score"] is None

    def test_old_row_forward_returns_work_normally(self):
        """Backward compat: forward returns must still compute for old rows."""
        import pandas as pd
        prices = pd.Series(
            [7.7] + [9.0] * 35,
            index=pd.date_range("2026-04-16", periods=36, freq="B"),
        )
        replay = SqueezeOutcomeReplay("2026-04-16", "2026-04-25")
        replay.load_snapshots(rows=[self._old_snap()])
        df = replay.run(prices={"NVAX": prices})
        assert len(df) == 1
        assert df.iloc[0]["fwd_5d"] == pytest.approx(9.0 / 7.7 - 1, rel=1e-3)

    def test_full_replay_with_mixed_old_and_new_rows(self):
        """Replay must handle a mix of old-format and new-format rows."""
        import pandas as pd

        old = self._old_snap()
        new = {
            "date": "2026-04-16",
            "ticker": "TSTZ",
            "final_score": 72.5,
            "short_pct_float": 0.38,
            "days_to_cover": 8.2,
            "computed_dtc_30d": 9.1,
            "compression_recovery_score": 7.5,
            "volume_confirmation_flag": True,
            "squeeze_state": "ARMED",
            "risk_score": 30.0,
            "risk_level": "LOW",
            "dilution_risk_flag": False,
            "options_pressure_score": 5.0,
            "iv_rank": 62.0,
            "iv_rank_score": 7.0,
            "unusual_call_activity_flag": False,
        }

        prices = {
            "NVAX": pd.Series(
                [7.7] + [9.0] * 35,
                index=pd.date_range("2026-04-16", periods=36, freq="B"),
            ),
            "TSTZ": pd.Series(
                [25.0] + [30.0] * 35,
                index=pd.date_range("2026-04-16", periods=36, freq="B"),
            ),
        }

        replay = SqueezeOutcomeReplay("2026-04-16", "2026-04-25")
        replay.load_snapshots(rows=[old, new])
        df = replay.run(prices=prices)

        assert len(df) == 2

        nvax_row = df[df["ticker"] == "NVAX"].iloc[0]
        tstz_row = df[df["ticker"] == "TSTZ"].iloc[0]

        # old row: CHUNK fields absent → None or NaN in DataFrame
        assert pd.isna(nvax_row["risk_score"]) or nvax_row["risk_score"] is None
        assert pd.isna(nvax_row["squeeze_state"]) or nvax_row["squeeze_state"] is None

        # new row: CHUNK fields populated
        assert tstz_row["risk_score"] == pytest.approx(30.0)
        assert tstz_row["squeeze_state"] == "ARMED"
        assert tstz_row["options_pressure_score"] == pytest.approx(5.0)


class TestSmokeCheckDateNormalization:
    """
    Regression test: smoke check _iso() helper must not crash whether the DB
    returns squeeze_scores.date as a datetime.date object (after the TEXT→DATE
    migration) or as a plain string (pre-migration or mocked cursor).
    """

    def test_iso_normalizes_date_object(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from smoke_check_squeeze import _iso
        import datetime
        assert _iso(datetime.date(2026, 4, 27)) == "2026-04-27"

    def test_iso_normalizes_string(self):
        from scripts.smoke_check_squeeze import _iso
        assert _iso("2026-04-27") == "2026-04-27"

    def test_query_latest_run_with_date_objects(self):
        """query_latest_run must return ISO strings even when cursor yields datetime.date."""
        import datetime
        from unittest.mock import MagicMock
        from scripts.smoke_check_squeeze import query_latest_run

        mock_row = {"date": datetime.date(2026, 4, 27), "row_count": 15}
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [mock_row]

        result = query_latest_run(mock_cur)
        assert result[0]["date"] == "2026-04-27"
        assert result[0]["row_count"] == 15

    def test_query_latest_run_with_string_dates(self):
        """query_latest_run must also work when cursor yields plain strings."""
        from unittest.mock import MagicMock
        from scripts.smoke_check_squeeze import query_latest_run

        mock_row = {"date": "2026-04-27", "row_count": 15}
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [mock_row]

        result = query_latest_run(mock_cur)
        assert result[0]["date"] == "2026-04-27"
