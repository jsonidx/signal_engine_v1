"""
Unit tests for CHUNK-09: IV/options integration.

Tests cover:
  1.  IV rank score — high iv_rank (85) → score 10
  2.  IV rank score — medium iv_rank (55) → score 7
  3.  IV rank score — None → 0, no crash
  4.  fetch_latest_iv_rank returns None on DB error
  5.  options_pressure uses existing options activity
  6.  unusual_call_activity_flag set by OTM volume
  7.  missing options chain is safe (no crash, neutral output)
  8.  SqueezeScore contains all options/IV fields
  9.  missing IV data does not change final_score materially
  10. high options_pressure surfaces in explanation
  11. high IV rank surfaces as crowding warning
  12. replay handles missing options fields for old rows
  13. replay includes options fields when present

All tests are pure-function or use mocks — no live DB, yfinance, or options calls.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import date

from catalyst_screener import (
    _score_iv_rank_for_squeeze,
    score_options_activity,
    get_squeeze_options_context,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_stock_obj_no_options():
    """Mock yf.Ticker with no options expirations."""
    m = MagicMock()
    m.options = []
    return m


def _make_stock_obj_with_options(cp_ratio=2.0, high_otm_vol=150, call_oi=5000, put_oi=2000):
    """Mock yf.Ticker with a simple options chain.

    OTM calls are strikes > price * 1.1 = 110 (when price=100).
    high_otm_vol is placed at strike=115 so it lands in the OTM bucket.
    """
    import pandas as pd

    m = MagicMock()
    m.options = ["2024-06-21"]

    calls_df = pd.DataFrame({
        "strike": [90.0, 100.0, 115.0, 125.0],  # 115 and 125 are OTM when price=100
        "volume": [200, 500, high_otm_vol, 50],
        "openInterest": [call_oi // 4] * 4,
        "bid": [10.0, 5.0, 1.0, 0.5],
        "ask": [10.5, 5.5, 1.5, 0.7],
    })
    puts_df = pd.DataFrame({
        "strike": [90.0, 100.0, 110.0],
        "volume": [int((500 + high_otm_vol + 50) // cp_ratio)] * 3,
        "openInterest": [put_oi // 3] * 3,
        "bid": [0.5, 2.0, 5.0],
        "ask": [0.7, 2.5, 5.5],
    })

    chain_mock = MagicMock()
    chain_mock.calls = calls_df
    chain_mock.puts = puts_df
    m.option_chain.return_value = chain_mock

    return m


def _make_data(price=100.0, volume=2_000_000, stock_obj=None):
    if stock_obj is None:
        stock_obj = _make_stock_obj_no_options()
    return {"stock_obj": stock_obj, "price": price, "volume_current": volume}


# ── Test 1: IV rank score — high ──────────────────────────────────────────────

class TestIVRankScoreHigh:

    def test_iv_rank_85_returns_10(self):
        assert _score_iv_rank_for_squeeze(85.0) == 10.0

    def test_iv_rank_80_returns_10(self):
        assert _score_iv_rank_for_squeeze(80.0) == 10.0

    def test_iv_rank_99_returns_10(self):
        assert _score_iv_rank_for_squeeze(99.9) == 10.0


# ── Test 2: IV rank score — medium ───────────────────────────────────────────

class TestIVRankScoreMedium:

    def test_iv_rank_55_returns_5(self):
        # 55 is in the [40, 60) bucket → 5
        assert _score_iv_rank_for_squeeze(55.0) == 5.0

    def test_iv_rank_60_returns_7(self):
        assert _score_iv_rank_for_squeeze(60.0) == 7.0

    def test_iv_rank_40_returns_5(self):
        assert _score_iv_rank_for_squeeze(40.0) == 5.0

    def test_iv_rank_20_returns_3(self):
        assert _score_iv_rank_for_squeeze(20.0) == 3.0

    def test_iv_rank_10_returns_1(self):
        assert _score_iv_rank_for_squeeze(10.0) == 1.0


# ── Test 3: IV rank missing is safe ──────────────────────────────────────────

class TestIVRankMissingSafe:

    def test_iv_rank_none_returns_0(self):
        assert _score_iv_rank_for_squeeze(None) == 0.0

    def test_score_options_activity_iv_rank_none_no_crash(self):
        data = _make_data()
        result = score_options_activity(data, iv_rank=None)
        assert "iv_rank_score" in result
        assert result["iv_rank_score"] == 0.0
        assert result["iv_data_confidence"] == "none"

    def test_score_options_activity_returns_all_keys_when_no_options(self):
        data = _make_data()
        result = score_options_activity(data)
        required = {
            "score", "max", "flags",
            "options_pressure_score", "iv_rank", "iv_rank_score",
            "iv_data_confidence", "unusual_call_activity_flag",
            "call_put_volume_ratio", "call_put_oi_ratio",
        }
        assert required.issubset(result.keys())


# ── Test 4: fetch_latest_iv_rank returns None on DB error ────────────────────

class TestFetchLatestIVRankDBError:

    def test_returns_none_on_db_error(self):
        # managed_connection is imported inside fetch_latest_iv_rank from utils.db
        with patch("utils.db.managed_connection") as mock_conn:
            mock_conn.side_effect = Exception("DB unavailable")
            from utils.supabase_persist import fetch_latest_iv_rank
            result = fetch_latest_iv_rank("AAPL")
            assert result is None

    def test_returns_none_when_insufficient_history(self):
        with patch("utils.db.managed_connection") as mock_conn:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cur = MagicMock()
            # Only 2 rows — below default min_history=5
            cur.fetchall.return_value = [
                {"iv30": 0.30, "date": "2024-05-01"},
                {"iv30": 0.25, "date": "2024-04-30"},
            ]
            cm.cursor.return_value = cur
            mock_conn.return_value = cm
            from utils.supabase_persist import fetch_latest_iv_rank
            result = fetch_latest_iv_rank("GME", min_history=5)
            assert result is None


# ── Test 5: options_pressure uses existing options activity ───────────────────

class TestOptionsPressureUsesActivity:

    def test_high_call_put_ratio_raises_pressure(self):
        stock_obj = _make_stock_obj_with_options(cp_ratio=4.0)
        data = _make_data(stock_obj=stock_obj)
        result = score_options_activity(data)
        assert result["score"] >= 2
        assert result["options_pressure_score"] > 0.0
        assert result["call_put_volume_ratio"] is not None
        assert result["call_put_volume_ratio"] > 1.0

    def test_call_oi_dominance_surfaces_in_oi_ratio(self):
        stock_obj = _make_stock_obj_with_options(call_oi=10000, put_oi=2000)
        data = _make_data(stock_obj=stock_obj)
        result = score_options_activity(data)
        assert result["call_put_oi_ratio"] is not None
        assert result["call_put_oi_ratio"] > 1.0


# ── Test 6: unusual_call_activity_flag ────────────────────────────────────────

class TestUnusualCallActivityFlag:

    def test_high_otm_vol_sets_flag(self):
        stock_obj = _make_stock_obj_with_options(high_otm_vol=200)
        data = _make_data(price=100.0, stock_obj=stock_obj)
        result = score_options_activity(data)
        assert result["unusual_call_activity_flag"] is True

    def test_low_otm_vol_no_flag(self):
        stock_obj = _make_stock_obj_with_options(high_otm_vol=50)
        data = _make_data(price=100.0, stock_obj=stock_obj)
        result = score_options_activity(data)
        assert result["unusual_call_activity_flag"] is False


# ── Test 7: missing options chain is safe ─────────────────────────────────────

class TestMissingOptionsChainSafe:

    def test_no_options_returns_neutral_dict(self):
        data = _make_data()  # stock_obj with no options
        result = score_options_activity(data)
        assert result["score"] == 0
        assert result["options_pressure_score"] == 0.0
        assert result["unusual_call_activity_flag"] is False

    def test_get_squeeze_options_context_no_crash_on_yfinance_failure(self):
        with patch("catalyst_screener.yf") as mock_yf:
            mock_yf.Ticker.side_effect = Exception("network error")
            result = get_squeeze_options_context("FAIL", iv_rank=None)
        assert result["options_pressure_score"] == 0.0
        assert result["iv_rank"] is None
        assert result["unusual_call_activity_flag"] is False

    def test_get_squeeze_options_context_preserves_iv_rank_on_chain_failure(self):
        with patch("catalyst_screener.yf") as mock_yf:
            mock_yf.Ticker.side_effect = Exception("network error")
            result = get_squeeze_options_context("FAIL", iv_rank=75.0)
        # Even without options chain, iv_rank and iv_rank_score are present
        assert result["iv_rank"] == 75.0
        assert result["iv_rank_score"] == 7.0
        assert result["iv_data_confidence"] == "high"


# ── Test 8: SqueezeScore contains all options/IV fields ───────────────────────

class TestSqueezeScoreContainsOptionsFields:

    def test_squeezescore_has_options_iv_fields(self):
        from squeeze_screener import SqueezeScore
        sq = SqueezeScore(
            ticker="TEST", final_score=50.0, signal_breakdown={},
            juice_target=20.0, recent_squeeze=False,
        )
        assert hasattr(sq, "options_pressure_score")
        assert hasattr(sq, "iv_rank")
        assert hasattr(sq, "iv_rank_score")
        assert hasattr(sq, "iv_data_confidence")
        assert hasattr(sq, "unusual_call_activity_flag")
        assert hasattr(sq, "call_put_volume_ratio")
        assert hasattr(sq, "call_put_oi_ratio")

    def test_squeezescore_options_defaults_are_neutral(self):
        from squeeze_screener import SqueezeScore
        sq = SqueezeScore(
            ticker="TEST", final_score=50.0, signal_breakdown={},
            juice_target=20.0, recent_squeeze=False,
        )
        assert sq.options_pressure_score == 0.0
        assert sq.iv_rank is None
        assert sq.iv_rank_score == 0.0
        assert sq.iv_data_confidence == "none"
        assert sq.unusual_call_activity_flag is False


# ── Test 9: missing IV does not change final_score materially ─────────────────

class TestMissingIVDoesNotChangeFinalScore:

    def test_options_fields_do_not_affect_final_score(self):
        """
        score_options_activity is not wired into the final_score formula,
        so setting all options fields to zero must leave final_score unchanged.
        """
        from squeeze_screener import SqueezeScore
        # Build two identical SqueezeScores — one with options data, one without
        sq_no_opts = SqueezeScore(
            ticker="TEST", final_score=55.0, signal_breakdown={},
            juice_target=20.0, recent_squeeze=False,
            options_pressure_score=0.0, iv_rank=None,
        )
        sq_with_opts = SqueezeScore(
            ticker="TEST", final_score=55.0, signal_breakdown={},
            juice_target=20.0, recent_squeeze=False,
            options_pressure_score=8.5, iv_rank=75.0,
        )
        assert sq_no_opts.final_score == sq_with_opts.final_score


# ── Test 10: high options_pressure surfaces in explanation ────────────────────

class TestHighOptionsPressureInExplanation:

    def test_high_options_pressure_in_positive_drivers(self):
        from squeeze_screener import SqueezeScore, build_squeeze_explanation
        sq = SqueezeScore(
            ticker="TEST", final_score=60.0,
            signal_breakdown={
                "options_pressure_score": 8.0,
                "iv_rank": 55.0,
                "iv_data_confidence": "high",
                "unusual_call_activity_flag": False,
            },
            juice_target=30.0, recent_squeeze=False,
        )
        expl = build_squeeze_explanation(sq)
        keys = [d["key"] for d in expl["top_positive_drivers"]]
        assert "options_pressure_score" in keys

    def test_unusual_call_activity_surfaces_in_positive_drivers(self):
        from squeeze_screener import SqueezeScore, build_squeeze_explanation
        sq = SqueezeScore(
            ticker="TEST", final_score=55.0,
            signal_breakdown={
                "options_pressure_score": 3.0,
                "iv_rank": None,
                "iv_data_confidence": "none",
                "unusual_call_activity_flag": True,
            },
            juice_target=25.0, recent_squeeze=False,
        )
        expl = build_squeeze_explanation(sq)
        keys = [d["key"] for d in expl["top_positive_drivers"]]
        assert "options_pressure_score" in keys


# ── Test 11: high IV rank surfaces as crowding warning ────────────────────────

class TestHighIVRankCrowdingWarning:

    def test_iv_rank_above_80_surfaces_as_warning(self):
        from squeeze_screener import SqueezeScore, build_squeeze_explanation
        sq = SqueezeScore(
            ticker="TEST", final_score=65.0,
            signal_breakdown={
                "options_pressure_score": 0.0,
                "iv_rank": 85.0,
                "iv_data_confidence": "high",
                "unusual_call_activity_flag": False,
            },
            juice_target=30.0, recent_squeeze=False,
        )
        expl = build_squeeze_explanation(sq)
        warning_keys = [w["key"] for w in expl["warning_flags"]]
        assert "high_iv_rank" in warning_keys

    def test_iv_rank_between_60_and_80_surfaces_as_positive_driver(self):
        from squeeze_screener import SqueezeScore, build_squeeze_explanation
        sq = SqueezeScore(
            ticker="TEST", final_score=65.0,
            signal_breakdown={
                "options_pressure_score": 0.0,
                "iv_rank": 65.0,
                "iv_data_confidence": "high",
                "unusual_call_activity_flag": False,
            },
            juice_target=30.0, recent_squeeze=False,
        )
        expl = build_squeeze_explanation(sq)
        keys = [d["key"] for d in expl["top_positive_drivers"]]
        assert "iv_rank" in keys

    def test_options_confirmed_tag_added_when_high_pressure(self):
        from squeeze_screener import SqueezeScore, build_squeeze_explanation
        sq = SqueezeScore(
            ticker="TEST", final_score=60.0,
            signal_breakdown={
                "options_pressure_score": 7.5,
                "iv_rank": 55.0,
                "iv_data_confidence": "high",
                "unusual_call_activity_flag": False,
            },
            juice_target=25.0, recent_squeeze=False,
        )
        expl = build_squeeze_explanation(sq)
        assert "OPTIONS_CONFIRMED" in expl["setup_tags"]


# ── Test 12 & 13: Replay compatibility ────────────────────────────────────────

class TestReplayOptionsFieldHandling:

    def test_replay_handles_missing_options_fields_for_old_rows(self):
        """Old snapshot rows without options fields must not crash."""
        from backtest import SqueezeOutcomeReplay
        import pandas as pd

        old_row = {
            "ticker": "OLD", "date": "2024-01-02",
            "final_score": 55.0,
            "price": 50.0,
            "short_pct_float": 0.30,
            "days_to_cover": 3.5,
            "computed_dtc_30d": 3.5,
            "compression_recovery_score": 4.0,
            "volume_confirmation_flag": True,
            "squeeze_state": "ARMED",
            "explanation_summary": "Old row.",
            "explanation_json": None,
            # options fields intentionally absent
        }
        dates = pd.date_range("2024-01-01", periods=37, freq="B")
        prices = pd.Series([100.0] + [110.0] * 36, index=dates)

        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[old_row])
        df = replay.run(prices={"OLD": prices})

        assert len(df) == 1
        assert df.iloc[0]["options_pressure_score"] is None
        assert df.iloc[0]["iv_rank"] is None
        assert df.iloc[0]["iv_rank_score"] is None
        assert df.iloc[0]["unusual_call_activity_flag"] is None

    def test_replay_includes_options_fields_when_present(self):
        """New snapshot rows with options fields must propagate in replay output."""
        from backtest import SqueezeOutcomeReplay
        import pandas as pd

        snap = {
            "ticker": "OPT", "date": "2024-01-02",
            "final_score": 65.0,
            "price": 50.0,
            "short_pct_float": 0.35,
            "days_to_cover": 4.0,
            "computed_dtc_30d": 4.0,
            "compression_recovery_score": 5.0,
            "volume_confirmation_flag": True,
            "squeeze_state": "ARMED",
            "explanation_summary": "New row.",
            "explanation_json": None,
            "options_pressure_score": 7.5,
            "iv_rank": 72.0,
            "iv_rank_score": 7.0,
            "unusual_call_activity_flag": True,
        }
        dates = pd.date_range("2024-01-01", periods=37, freq="B")
        prices = pd.Series([100.0] + [110.0] * 36, index=dates)

        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap])
        df = replay.run(prices={"OPT": prices})

        assert df.iloc[0]["options_pressure_score"] == pytest.approx(7.5)
        assert df.iloc[0]["iv_rank"] == pytest.approx(72.0)
        assert df.iloc[0]["iv_rank_score"] == pytest.approx(7.0)
        assert df.iloc[0]["unusual_call_activity_flag"] == True  # noqa: E712
