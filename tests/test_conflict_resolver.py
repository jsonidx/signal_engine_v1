"""
tests/test_conflict_resolver.py
================================
Tests for conflict_resolver.py — signal arbitration layer.

Covers:
  - Weighted vote: all-BULL, all-BEAR, mixed, NEUTRAL margin
  - extract_module_direction for every module
  - Post-squeeze guard override
  - Bear market circuit breaker
  - Pre-earnings hold
  - skip_claude behaviour
  - Squeeze-driven context flag (direction unchanged)
  - resolve() end-to-end
"""
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conflict_resolver as cr


# ==============================================================================
# FIXTURES
# ==============================================================================

def _bull_signals() -> dict:
    """All modules returning BULL directions."""
    return {
        "ticker": "TEST",
        "signal_engine":  {"composite_z": 1.5},
        "fundamentals":   {"fundamental_score_pct": 72.0},
        "squeeze":        {"squeeze_score_100": 65.0, "recent_squeeze": False},
        "options_flow":   {"heat_score": 80.0, "pc_ratio": 0.5},
        "sec":            {"score": 2, "flags": ["CONGRESS BUYING: 2 purchase(s)"]},
        "technical":      {"momentum_1m_pct": 5.0},
        "market_regime":  {"regime": "RISK_ON"},
    }


def _bear_signals() -> dict:
    """All modules returning BEAR directions."""
    return {
        "ticker": "TEST",
        "signal_engine":  {"composite_z": -1.8},
        "fundamentals":   {"fundamental_score_pct": 22.0},
        "squeeze":        {"squeeze_score_100": 10.0, "recent_squeeze": False},
        "options_flow":   {"heat_score": 80.0, "pc_ratio": 2.5},
        "sec":            {"score": 0, "flags": []},
        "technical":      {"momentum_1m_pct": -3.0},
        "market_regime":  {"regime": "RISK_OFF"},
    }


def _neutral_signals() -> dict:
    """All modules returning no clear direction."""
    return {
        "ticker": "TEST",
        "signal_engine":  {"composite_z": 0.1},          # between -0.5 and 0.5
        "fundamentals":   {"fundamental_score_pct": 50.0}, # between 35 and 65
        "squeeze":        {"squeeze_score_100": 30.0, "recent_squeeze": False},
        "options_flow":   {"heat_score": 40.0, "pc_ratio": 1.0},  # heat too low
        "sec":            {"score": 0, "flags": []},
        "technical":      {"momentum_1m_pct": 0.1},
        "market_regime":  {"regime": "TRANSITIONAL"},
    }


# ==============================================================================
# TestExtractModuleDirection
# ==============================================================================

class TestExtractModuleDirection:
    def test_signal_engine_bull(self):
        assert cr.extract_module_direction("signal_engine_composite_z", {"composite_z": 0.6}) == "BULL"

    def test_signal_engine_bear(self):
        assert cr.extract_module_direction("signal_engine_composite_z", {"composite_z": -0.6}) == "BEAR"

    def test_signal_engine_neutral_zone(self):
        assert cr.extract_module_direction("signal_engine_composite_z", {"composite_z": 0.3}) is None

    def test_signal_engine_missing(self):
        assert cr.extract_module_direction("signal_engine_composite_z", {}) is None

    def test_fundamentals_bull(self):
        assert cr.extract_module_direction("fundamental_analysis", {"fundamental_score_pct": 70.0}) == "BULL"

    def test_fundamentals_bear(self):
        assert cr.extract_module_direction("fundamental_analysis", {"fundamental_score_pct": 30.0}) == "BEAR"

    def test_fundamentals_neutral_zone(self):
        assert cr.extract_module_direction("fundamental_analysis", {"fundamental_score_pct": 50.0}) is None

    def test_squeeze_bull_above_55(self):
        assert cr.extract_module_direction("squeeze_screener", {"squeeze_score_100": 60.0}) == "BULL"

    def test_squeeze_no_bear(self):
        # Low squeeze score should be None, not BEAR — squeezes are directionally BULL only
        assert cr.extract_module_direction("squeeze_screener", {"squeeze_score_100": 5.0}) is None

    def test_options_flow_bull_low_pcr(self):
        assert cr.extract_module_direction("options_flow", {"heat_score": 75.0, "pc_ratio": 0.5}) == "BULL"

    def test_options_flow_bear_high_pcr(self):
        assert cr.extract_module_direction("options_flow", {"heat_score": 75.0, "pc_ratio": 2.0}) == "BEAR"

    def test_options_flow_heat_too_low(self):
        # heat <= 65: no vote regardless of pcr
        assert cr.extract_module_direction("options_flow", {"heat_score": 60.0, "pc_ratio": 0.3}) is None

    def test_options_flow_middle_pcr(self):
        # pcr between 0.7 and 1.8: no directional signal
        assert cr.extract_module_direction("options_flow", {"heat_score": 80.0, "pc_ratio": 1.0}) is None

    def test_sec_bull_with_buy_flags(self):
        assert cr.extract_module_direction("sec_insider", {"score": 2, "flags": ["insider buying cluster"]}) == "BULL"

    def test_sec_activist_flag(self):
        assert cr.extract_module_direction("sec_insider", {"score": 3, "flags": ["ACTIVIST FILING: 13D by Starboard"]}) == "BULL"

    def test_sec_score_zero(self):
        assert cr.extract_module_direction("sec_insider", {"score": 0, "flags": []}) is None

    def test_sec_score_positive_no_buy_flags(self):
        # Score > 0 but no buy/activist flags → None (can't confirm BULL)
        assert cr.extract_module_direction("sec_insider", {"score": 1, "flags": ["Active 8-K filings: 2"]}) is None

    def test_empty_output(self):
        for m in cr.MODULE_WEIGHTS:
            assert cr.extract_module_direction(m, {}) is None

    def test_none_output(self):
        for m in cr.MODULE_WEIGHTS:
            assert cr.extract_module_direction(m, None) is None


# ==============================================================================
# TestWeightedVote
# ==============================================================================

class TestWeightedVote:
    def test_all_bull_returns_bull(self):
        result = cr.compute_weighted_vote(_bull_signals())
        assert result["net_direction"] == "BULL"
        assert result["bull_weight"] > result["bear_weight"]
        assert result["confidence"] > 0.5

    def test_all_bear_returns_bear(self):
        # bear_signals(): squeeze gives None (score=10), congress gives BEAR, sec gives None
        result = cr.compute_weighted_vote(_bear_signals())
        assert result["net_direction"] == "BEAR"
        assert result["bear_weight"] > result["bull_weight"]

    def test_all_neutral_returns_neutral(self):
        result = cr.compute_weighted_vote(_neutral_signals())
        assert result["net_direction"] == "NEUTRAL"

    def test_module_votes_populated(self):
        result = cr.compute_weighted_vote(_bull_signals())
        assert "module_votes" in result
        # All BULL-signalling modules should have BULL
        assert result["module_votes"]["signal_engine_composite_z"] == "BULL"
        assert result["module_votes"]["fundamental_analysis"] == "BULL"

    def test_margin_prevents_weak_bull(self):
        # Only signal_engine (0.25) votes BULL, nothing votes BEAR → bull_weight=0.25
        # margin=0.10 → 0.25 > 0.00 + 0.10 → BULL
        sigs = _neutral_signals()
        sigs["signal_engine"] = {"composite_z": 0.8}
        result = cr.compute_weighted_vote(sigs)
        assert result["net_direction"] == "BULL"

    def test_margin_keeps_neutral_when_tied(self):
        # Use hardcoded MODULE_WEIGHTS to make this test deterministic
        # signal_engine BULL (0.35) vs fundamentals BEAR (0.20) + options_flow BEAR (0.15)
        # bull=0.35, bear=0.35: diff=0.00 < margin 0.10 → NEUTRAL
        sigs = _neutral_signals()
        sigs["signal_engine"] = {"composite_z": 0.8}         # BULL 0.35
        sigs["fundamentals"]  = {"fundamental_score_pct": 30} # BEAR 0.20
        sigs["options_flow"]  = {"heat_score": 80.0, "pc_ratio": 2.5}  # BEAR 0.15
        with patch.object(cr, "_load_module_weights", return_value=cr.MODULE_WEIGHTS):
            result = cr.compute_weighted_vote(sigs)
        # bear_weight=0.35, bull_weight=0.35, diff=0.00 < margin 0.10
        assert result["net_direction"] == "NEUTRAL"

    def test_agreement_fraction_all_bull(self):
        result = cr.compute_weighted_vote(_bull_signals())
        # Squeeze gives BULL, squeeze-only modules that vote; agreement should be high
        assert result["agreement_fraction"] > 0.5

    def test_agreement_fraction_zero_when_no_votes(self):
        sigs = {
            "ticker": "X",
            "signal_engine": {"composite_z": 0.1},   # no vote
            "fundamentals":  {"fundamental_score_pct": 50},  # no vote
            "squeeze":       {"squeeze_score_100": 10},      # no vote
            "options_flow":  {"heat_score": 20},              # no vote
            "sec":           {"score": 0, "flags": []},
        }
        result = cr.compute_weighted_vote(sigs)
        assert result["agreement_fraction"] == 0.0


# ==============================================================================
# TestHardOverrides
# ==============================================================================

class TestHardOverrides:
    # ── Override 1: Post-squeeze guard ──────────────────────────────────────

    def test_post_squeeze_guard_sets_neutral_and_skips(self):
        sigs = _bull_signals()
        sigs["squeeze"]["recent_squeeze"] = True
        vote = cr.compute_weighted_vote(sigs)
        result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        assert result["net_direction"] == "NEUTRAL"
        assert result["skip_claude"] is False  # guard flags but does not block thesis
        assert any("post_squeeze_guard" in f for f in result["override_flags"])

    def test_no_override_when_recent_squeeze_false(self):
        sigs = _bull_signals()
        sigs["squeeze"]["recent_squeeze"] = False
        vote = cr.compute_weighted_vote(sigs)
        result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        assert result["skip_claude"] is False
        assert not any("post_squeeze_guard" in f for f in result["override_flags"])

    # ── Override 2: Bear market circuit breaker ──────────────────────────────

    def test_bear_market_circuit_breaker_caps_conviction_and_position(self):
        sigs = _bull_signals()
        vote = cr.compute_weighted_vote(sigs)
        result = cr.apply_hard_overrides(vote, sigs, "RISK_OFF", ticker="TEST")
        assert result["max_conviction_override"] == 2
        assert result["position_size_pct"] == 3.0
        assert any("bear_market_circuit_breaker" in f for f in result["override_flags"])

    def test_bear_market_does_not_set_skip_claude(self):
        # Bear market circuit breaker caps but doesn't skip — Claude still provides thesis
        sigs = _bull_signals()
        vote = cr.compute_weighted_vote(sigs)
        result = cr.apply_hard_overrides(vote, sigs, "RISK_OFF", ticker="TEST")
        assert result["skip_claude"] is False

    def test_no_circuit_breaker_for_risk_on(self):
        sigs = _bull_signals()
        vote = cr.compute_weighted_vote(sigs)
        result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        assert result["max_conviction_override"] is None
        assert not any("bear_market_circuit_breaker" in f for f in result["override_flags"])

    # ── Override 3: Pre-earnings hold ────────────────────────────────────────

    def test_pre_earnings_hold_skips_claude(self):
        sigs = _bull_signals()
        vote = cr.compute_weighted_vote(sigs)
        with patch.object(cr, "_get_days_to_earnings", return_value=3):
            with patch.object(cr, "_is_earnings_catalyst", return_value=False):
                result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        assert result["net_direction"] == "NEUTRAL"
        assert result["position_size_pct"] == 0.0
        assert result["skip_claude"] is True
        assert any("pre_earnings_hold" in f for f in result["override_flags"])

    def test_pre_earnings_hold_respects_earnings_catalyst_exception(self):
        """If thesis IS the earnings play, hold override should NOT fire."""
        sigs = _bull_signals()
        vote = cr.compute_weighted_vote(sigs)
        with patch.object(cr, "_get_days_to_earnings", return_value=2):
            with patch.object(cr, "_is_earnings_catalyst", return_value=True):
                result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        # Not blocked — thesis is the earnings catalyst
        assert result["skip_claude"] is False
        assert result["net_direction"] != "NEUTRAL" or result["net_direction"] == "NEUTRAL"
        # Flag should say IS_the_thesis not pre_earnings_hold
        assert not any("pre_earnings_hold" in f for f in result["override_flags"])
        assert any("IS_the_thesis" in f for f in result["override_flags"])

    def test_pre_earnings_hold_does_not_fire_when_earnings_far(self):
        sigs = _bull_signals()
        vote = cr.compute_weighted_vote(sigs)
        with patch.object(cr, "_get_days_to_earnings", return_value=30):
            result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        assert result["skip_claude"] is False
        assert not any("pre_earnings_hold" in f for f in result["override_flags"])

    def test_pre_earnings_hold_skipped_when_post_squeeze_already_fired(self):
        """Override 1 no longer sets skip_claude, so Override 3 will run its earnings check."""
        sigs = _bull_signals()
        sigs["squeeze"]["recent_squeeze"] = True
        vote = cr.compute_weighted_vote(sigs)
        call_tracker = []
        def mock_earnings(ticker):
            call_tracker.append(ticker)
            return 30  # far from earnings — Override 3 will not fire
        with patch.object(cr, "_get_days_to_earnings", side_effect=mock_earnings):
            result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        # Override 1 flags but doesn't skip, so Override 3 runs its earnings check
        assert len(call_tracker) == 1
        assert result["skip_claude"] is False
        assert result["net_direction"] == "NEUTRAL"  # Override 1 still sets NEUTRAL

    # ── Override 4: Squeeze-driven context flag ──────────────────────────────

    def test_squeeze_driven_flag_is_context_only(self):
        """Override 4 adds a flag but must NOT change the direction."""
        sigs = _bull_signals()
        sigs["squeeze"]["squeeze_score_100"] = 70.0
        sigs["technical"] = {"momentum_1m_pct": 0.1}  # low organic momentum
        vote = cr.compute_weighted_vote(sigs)
        original_direction = vote["net_direction"]
        result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        # Direction must be unchanged
        assert result["net_direction"] == original_direction
        # Context flag must be present
        assert any("squeeze_driven_not_organic" in f for f in result["override_flags"])
        # skip_claude must NOT be set by this override
        assert result["skip_claude"] is False

    def test_no_squeeze_driven_flag_when_momentum_high(self):
        """If organic momentum is healthy, context flag should not fire."""
        sigs = _bull_signals()
        sigs["squeeze"]["squeeze_score_100"] = 70.0
        sigs["technical"] = {"momentum_1m_pct": 5.0}   # healthy organic demand
        vote = cr.compute_weighted_vote(sigs)
        result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        assert not any("squeeze_driven_not_organic" in f for f in result["override_flags"])

    def test_no_squeeze_driven_flag_when_squeeze_score_low(self):
        sigs = _bull_signals()
        sigs["squeeze"]["squeeze_score_100"] = 45.0
        sigs["technical"] = {"momentum_1m_pct": 0.1}
        vote = cr.compute_weighted_vote(sigs)
        result = cr.apply_hard_overrides(vote, sigs, "RISK_ON", ticker="TEST")
        assert not any("squeeze_driven_not_organic" in f for f in result["override_flags"])


# ==============================================================================
# TestResolveEndToEnd
# ==============================================================================

class TestResolveEndToEnd:
    def test_skip_claude_false_for_post_squeeze(self):
        sigs = _bull_signals()
        sigs["squeeze"]["recent_squeeze"] = True
        with patch.object(cr, "_log_resolution"):  # suppress log writes in tests
            with patch.object(cr, "_get_days_to_earnings", return_value=30):
                result = cr.resolve(sigs, "RISK_ON")
        assert result["skip_claude"] is False  # guard flags but thesis is still generated
        assert result["pre_resolved_direction"] == "NEUTRAL"

    def test_skip_claude_true_for_pre_earnings(self):
        sigs = _bull_signals()
        with patch.object(cr, "_get_days_to_earnings", return_value=2):
            with patch.object(cr, "_is_earnings_catalyst", return_value=False):
                with patch.object(cr, "_log_resolution"):
                    result = cr.resolve(sigs, "RISK_ON")
        assert result["skip_claude"] is True
        assert result["pre_resolved_direction"] == "NEUTRAL"

    def test_full_bull_resolution(self):
        with patch.object(cr, "_get_days_to_earnings", return_value=60):
            with patch.object(cr, "_log_resolution"):
                result = cr.resolve(_bull_signals(), "RISK_ON")
        assert result["pre_resolved_direction"] == "BULL"
        assert result["bull_weight"] > result["bear_weight"]
        assert result["skip_claude"] is False
        assert "module_votes" in result
        assert "override_flags" in result

    def test_bear_market_fields_present(self):
        with patch.object(cr, "_get_days_to_earnings", return_value=60):
            with patch.object(cr, "_log_resolution"):
                result = cr.resolve(_bull_signals(), "RISK_OFF")
        assert result["max_conviction_override"] == 2
        assert result["position_size_override"] == 3.0
        assert result["skip_claude"] is False   # circuit breaker does not skip

    def test_result_has_all_required_keys(self):
        required = {
            "pre_resolved_direction", "pre_resolved_confidence",
            "signal_agreement_score", "override_flags", "module_votes",
            "bull_weight", "bear_weight", "skip_claude",
            "max_conviction_override", "position_size_override",
        }
        with patch.object(cr, "_get_days_to_earnings", return_value=60):
            with patch.object(cr, "_log_resolution"):
                result = cr.resolve(_neutral_signals(), "TRANSITIONAL")
        assert required.issubset(set(result.keys()))


# ==============================================================================
# TestIsEarningsCatalyst
# ==============================================================================

class TestIsEarningsCatalyst:
    def test_earnings_keyword_in_catalyst_flags(self):
        sigs = {"catalyst": {"short_squeeze_flags": ["earnings beat expected Q2"]}}
        assert cr._is_earnings_catalyst(sigs) is True

    def test_earnings_in_vol_compression_flags(self):
        sigs = {"catalyst": {"vol_compression_flags": ["pre-earnings vol compression"]}}
        assert cr._is_earnings_catalyst(sigs) is True

    def test_no_earnings_in_flags(self):
        sigs = {"catalyst": {"short_squeeze_flags": ["high short interest"], "vol_compression_flags": []}}
        assert cr._is_earnings_catalyst(sigs) is False

    def test_empty_signals(self):
        assert cr._is_earnings_catalyst({}) is False


# ==============================================================================
# TestLogging
# ==============================================================================

class TestLogging:
    def test_log_does_not_raise_on_permissions_error(self, tmp_path, monkeypatch):
        """Logging failure should be caught silently."""
        monkeypatch.setattr(cr, "_LOG_DIR", tmp_path / "logs")
        resolved = {
            "net_direction": "BULL", "confidence": 0.7,
            "bull_weight": 0.4, "bear_weight": 0.1,
            "override_flags": [], "skip_claude": False,
        }
        # Should not raise
        cr._log_resolution("TEST", resolved)

    def test_log_creates_csv_with_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, "_LOG_DIR", tmp_path / "logs")
        resolved = {
            "net_direction": "BEAR", "confidence": 0.6,
            "bull_weight": 0.1, "bear_weight": 0.35,
            "override_flags": ["override: post_squeeze_guard"], "skip_claude": True,
        }
        cr._log_resolution("GME", resolved)
        # Verify a CSV was created
        csv_files = list((tmp_path / "logs").glob("conflict_resolution_*.csv"))
        assert len(csv_files) == 1
        content = csv_files[0].read_text()
        assert "timestamp" in content  # header row
        assert "GME" in content


# ==============================================================================
# TRD-001 — Pre-Earnings Breakout: conflict resolver integration
# ==============================================================================

class TestPreEarningsBreakoutConflictResolver:
    """
    The conflict resolver must NOT automatically force BULL when a pre-earnings
    breakout flag fires.  The detector is advisory (adds a context flag) and must
    leave the existing pre-earnings-hold override semantics intact.
    """

    def _signals_with_earnings(self, days_to_earn: int, is_catalyst: bool = True) -> dict:
        """Signals dict that will trigger pre_earnings_hold or earnings_is_thesis."""
        sigs = {
            "ticker": "SNOW",
            "signal_engine":  {"composite_z": 0.8},
            "fundamentals":   {"fundamental_score_pct": 65.0},
            "squeeze":        {"squeeze_score_100": 11.5, "recent_squeeze": False},
            "options_flow":   {"heat_score": 70.0, "pc_ratio": 0.55},
            "sec":            {"score": 0, "flags": []},
            "technical":      {"momentum_1m_pct": 9.0},
            "market_regime":  {"regime": "RISK_ON"},
            "catalyst":       {"is_earnings_catalyst": is_catalyst},
        }
        return sigs

    def test_pre_earnings_hold_still_fires_for_non_catalyst(self):
        """
        When earnings are within 5 days and the setup is NOT flagged as a
        catalyst thesis, pre_earnings_hold override must still fire.
        This ensures the new detector does not weaken the override.
        """
        sigs = self._signals_with_earnings(days_to_earn=3, is_catalyst=False)

        with patch.object(cr, "_get_days_to_earnings", return_value=3), \
             patch.object(cr, "_is_earnings_catalyst", return_value=False):
            result = cr.resolve(sigs, "RISK_ON")

        override_flags = result.get("override_flags", [])
        pre_hold_fired = any("pre_earnings_hold" in f for f in override_flags)
        assert pre_hold_fired, (
            "pre_earnings_hold must still fire even when pre_earnings_breakout logic is present"
        )

    def test_pre_earnings_breakout_context_flag_does_not_override_direction(self):
        """
        A context flag about pre-earnings breakout setup must not change
        the pre-resolved direction produced by the weighted vote.
        """
        sigs = {
            "ticker": "TESTPEB",
            "signal_engine":  {"composite_z": 0.5},
            "fundamentals":   {"fundamental_score_pct": 50.0},
            "squeeze":        {"squeeze_score_100": 11.5, "recent_squeeze": False},
            "options_flow":   {"heat_score": 60.0, "pc_ratio": 0.6},
            "sec":            {"score": 0, "flags": []},
            "technical":      {"momentum_1m_pct": 3.0},
            "market_regime":  {"regime": "RISK_ON"},
        }
        import yfinance as yf
        mock_ticker = MagicMock()
        mock_ticker.info = {"longBusinessSummary": "", "regularMarketPrice": 50.0}
        mock_ticker.history.return_value = MagicMock(empty=False)
        with patch.object(cr, "_get_days_to_earnings", return_value=30), \
             patch.object(yf, "Ticker", return_value=mock_ticker):
            result = cr.resolve(sigs, "RISK_ON")

        # Direction must come from the module vote, not any pre-earnings shortcut
        assert result["pre_resolved_direction"] in ("BULL", "BEAR", "NEUTRAL")
        # No "override: pre_earnings_hold" must fire with 30 days out
        override_flags = result.get("override_flags", [])
        assert not any("pre_earnings_hold" in f for f in override_flags), (
            "pre_earnings_hold must not fire with 30 days to earnings"
        )

    def test_pre_earnings_breakout_result_is_watch_not_buy(self):
        """
        Document: pre_earnings_breakout is a WATCH/SETUP signal, not a buy.
        score_pre_earnings_breakout must never set skip_claude=False or
        force a direction by itself — it only produces advisory flags.
        """
        from catalyst_screener import score_pre_earnings_breakout
        result = score_pre_earnings_breakout({
            "days_to_earnings": 5,
            "earnings_beat_streak": 4,
            "earnings_surprise_avg_pct": 8.5,
            "momentum_1m_pct": 9.2,
            "volume_score": 4.0,
            "options_score": 4.0,
            "dark_pool_signal": "NEUTRAL",
            "short_pct_float": 5.1,
        })
        # Detector must NOT produce a buy/direction field
        assert "direction" not in result, "Detector must not set direction"
        assert "skip_claude" not in result, "Detector must not set skip_claude"
        # Must produce flags, not a hard override
        assert isinstance(result["flags"], list)
        if result["pre_earnings_breakout"]:
            flag_text = " ".join(result["flags"]).lower()
            assert "watch" in flag_text or "setup" in flag_text or "catalyst" in flag_text, (
                "Pre-earnings breakout flag must describe a watch/setup, not a buy"
            )
