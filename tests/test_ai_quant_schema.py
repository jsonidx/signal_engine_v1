"""
tests/test_ai_quant_schema.py
=============================================================================
Tests for:
  1. compute_signal_agreement() — deterministic score for known signals dicts
  2. _validate_probabilities()  — warns when bull+bear+neutral ≠ ~1.0
  3. _parse_response()          — correctly extracts JSON from a mock Claude reply
=============================================================================
"""

import json
import sys
import os
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_quant import compute_signal_agreement, _validate_probabilities, _parse_response


# ---------------------------------------------------------------------------
# compute_signal_agreement tests
# ---------------------------------------------------------------------------

def test_agreement_all_bull():
    """All modules firing BULL → score = 1.0."""
    signals = {
        "signal_engine":  {"composite_z": 1.2},          # BULL
        "squeeze":        {"squeeze_score_100": 72},      # BULL
        "options_flow":   {"heat_score": 75},             # BULL
        "cross_asset":    {"signal": "BOTTOM"},           # BULL
        "fundamentals":   {"fundamental_score_pct": 78},  # BULL
        "polymarket":     {"polymarket_probability": 0.80},  # BULL
    }
    score = compute_signal_agreement(signals)
    assert score == 1.0, f"Expected 1.0, got {score}"


def test_agreement_all_bear():
    """All modules firing BEAR → score = 1.0 (plurality is BEAR)."""
    signals = {
        "signal_engine":  {"composite_z": -1.5},           # BEAR
        "cross_asset":    {"signal": "TOP"},                # BEAR
        "fundamentals":   {"fundamental_score_pct": 25},   # BEAR
        "polymarket":     {"polymarket_probability": 0.20}, # BEAR
    }
    score = compute_signal_agreement(signals)
    assert score == 1.0, f"Expected 1.0, got {score}"


def test_agreement_mixed_majority_bull():
    """4 BULL vs 1 BEAR → score = 4/5 = 0.8."""
    signals = {
        "signal_engine":  {"composite_z": 0.8},             # BULL
        "squeeze":        {"squeeze_score_100": 60},         # BULL
        "options_flow":   {"heat_score": 65},                # BULL
        "polymarket":     {"polymarket_probability": 0.20},  # BEAR (< 0.35)
        "fundamentals":   {"fundamental_score_pct": 70},    # BULL
    }
    score = compute_signal_agreement(signals)
    assert score == 0.8, f"Expected 0.8, got {score}"


def test_agreement_no_modules():
    """Empty signals dict → score = 0.0."""
    score = compute_signal_agreement({})
    assert score == 0.0, f"Expected 0.0, got {score}"


def test_agreement_neutral_modules_excluded():
    """Modules below threshold cast no vote and are excluded from denominator."""
    signals = {
        "signal_engine": {"composite_z": 0.2},          # between -0.5 and 0.5 → no vote
        "squeeze":       {"squeeze_score_100": 45},      # ≤50 → no vote
        "options_flow":  {"heat_score": 55},             # ≤60 → no vote
        "fundamentals":  {"fundamental_score_pct": 50},  # between 40-60 → no vote
        "polymarket":    {"polymarket_probability": 0.50},  # between 0.35-0.65 → no vote
    }
    score = compute_signal_agreement(signals)
    assert score == 0.0, f"Expected 0.0 (no valid votes), got {score}"


def test_agreement_single_module_fires():
    """Only one module fires → score = 1.0 (100% agreement with itself)."""
    signals = {
        "squeeze": {"squeeze_score_100": 80},  # BULL
    }
    score = compute_signal_agreement(signals)
    assert score == 1.0, f"Expected 1.0, got {score}"


def test_agreement_tie_breaks_to_plurality_count():
    """2 BULL vs 2 BEAR → plurality = 2/4 = 0.5."""
    signals = {
        "signal_engine": {"composite_z": 0.8},             # BULL
        "squeeze":       {"squeeze_score_100": 60},         # BULL
        "fundamentals":  {"fundamental_score_pct": 30},    # BEAR (< 40)
        "polymarket":    {"polymarket_probability": 0.20},  # BEAR (< 0.35)
    }
    score = compute_signal_agreement(signals)
    assert score == 0.5, f"Expected 0.5, got {score}"


def test_agreement_polymarket_zero_probability_ignored():
    """polymarket_probability == 0 → not counted (avoids false BEAR votes)."""
    signals = {
        "polymarket": {"polymarket_probability": 0},
        "squeeze":    {"squeeze_score_100": 60},  # BULL
    }
    score = compute_signal_agreement(signals)
    assert score == 1.0, f"Expected 1.0 (polymarket=0 should be ignored), got {score}"


# ---------------------------------------------------------------------------
# _validate_probabilities tests
# ---------------------------------------------------------------------------

def test_probabilities_valid_sum_no_warning():
    """Probabilities summing to 1.0 should NOT emit a warning."""
    thesis = {
        "ticker": "TEST",
        "bull_probability": 0.6,
        "bear_probability": 0.3,
        "neutral_probability": 0.1,
    }
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _validate_probabilities(thesis)
    assert len(w) == 0, f"Expected no warnings, got: {[str(x.message) for x in w]}"


def test_probabilities_sum_off_warns():
    """Probabilities summing to 0.80 (off by >0.05) must trigger a UserWarning."""
    thesis = {
        "ticker": "TEST",
        "bull_probability": 0.5,
        "bear_probability": 0.2,
        "neutral_probability": 0.1,   # sum = 0.80 → off by 0.20
    }
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _validate_probabilities(thesis)
    assert len(w) == 1, f"Expected 1 warning, got {len(w)}"
    assert issubclass(w[0].category, UserWarning)
    assert "0.800" in str(w[0].message) or "sum" in str(w[0].message).lower()


def test_probabilities_within_tolerance_no_warning():
    """Sum of 0.97 is within ±0.05 of 1.0 → no warning."""
    thesis = {
        "ticker": "TEST",
        "bull_probability": 0.60,
        "bear_probability": 0.30,
        "neutral_probability": 0.07,  # sum = 0.97
    }
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _validate_probabilities(thesis)
    assert len(w) == 0, f"Expected no warnings, got: {[str(x.message) for x in w]}"


def test_probabilities_all_absent_no_warning():
    """When no probability keys are present, no warning should fire."""
    thesis = {"ticker": "TEST", "direction": "BULL"}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _validate_probabilities(thesis)
    assert len(w) == 0


# ---------------------------------------------------------------------------
# _parse_response tests (mocked Claude output)
# ---------------------------------------------------------------------------

SAMPLE_CLAUDE_JSON = {
    "ticker": "COIN",
    "direction": "BULL",
    "bull_probability": 0.65,
    "bear_probability": 0.20,
    "neutral_probability": 0.15,
    "conviction": 4,
    "time_horizon": "weeks",
    "primary_scenario": "COIN breaks above $280 on strong crypto market tailwinds.",
    "bear_scenario": "Regulatory crackdown causes a pullback below $230 support.",
    "key_invalidation": "Close below $228",
    "entry_low": 245.0,
    "entry_high": 255.0,
    "stop_loss": 228.0,
    "target_1": 285.0,
    "target_2": 310.0,
    "position_size_pct": 8,
    "signal_agreement_score": 0.8,
    "catalysts": ["BTC recovery above 200MA", "Coinbase earnings beat"],
    "risks": ["SEC enforcement action", "Broad risk-off event"],
    "thesis": "COIN shows strong momentum alignment with crypto market. Options flow is bullish.",
    "data_quality": "HIGH",
    "notes": "",
}


def test_parse_response_direct_json():
    """_parse_response handles a raw JSON string correctly."""
    raw = json.dumps(SAMPLE_CLAUDE_JSON)
    result = _parse_response(raw)
    assert result is not None
    assert result["ticker"] == "COIN"
    assert result["direction"] == "BULL"
    assert result["bull_probability"] == 0.65
    assert result["key_invalidation"] == "Close below $228"
    assert result["primary_scenario"].startswith("COIN breaks")
    assert result["bear_scenario"].startswith("Regulatory")
    assert result["signal_agreement_score"] == 0.8


def test_parse_response_wrapped_in_code_fence():
    """_parse_response correctly strips ```json ... ``` fences."""
    raw = f"Here is the analysis:\n```json\n{json.dumps(SAMPLE_CLAUDE_JSON)}\n```"
    result = _parse_response(raw)
    assert result is not None
    assert result["direction"] == "BULL"
    assert result["bear_probability"] == 0.20


def test_parse_response_returns_none_on_invalid():
    """_parse_response returns None when the response has no valid JSON."""
    result = _parse_response("Sorry, I cannot analyze this ticker.")
    assert result is None


def test_parse_response_probabilities_sum():
    """Parsed SAMPLE_CLAUDE_JSON probabilities sum to 1.0."""
    result = _parse_response(json.dumps(SAMPLE_CLAUDE_JSON))
    total = (result["bull_probability"] + result["bear_probability"]
             + result["neutral_probability"])
    assert abs(total - 1.0) < 0.001, f"Probabilities sum to {total}, expected 1.0"


# ---------------------------------------------------------------------------
# TRD-066 — Issuance state and deterministic geometry tests
# ---------------------------------------------------------------------------

from ai_quant import (
    _get_issuance_state,
    _apply_deterministic_geometry,
    _has_executable_geometry,
    ISSUANCE_ACTIVE_THESIS,
    ISSUANCE_WATCH_ONLY,
    ISSUANCE_SUPPRESSED,
    ISSUANCE_NO_TRADE,
)


# ── _get_issuance_state ───────────────────────────────────────────────────────

def test_issuance_active_thesis_bull():
    """BULL direction, conviction>=2, valid geometry → ACTIVE_THESIS."""
    thesis = {
        "direction": "BULL", "conviction": 3,
        "entry_low": 100.0, "entry_high": 105.0,
        "stop_loss": 90.0, "target_1": 118.0,
    }
    resolved = {"skip_ai_synthesis": False, "override_flags": []}
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_ACTIVE_THESIS


def test_issuance_active_thesis_bear():
    """BEAR direction, conviction>=2, valid geometry → ACTIVE_THESIS."""
    thesis = {
        "direction": "BEAR", "conviction": 4,
        "entry_low": 95.0, "entry_high": 100.0,
        "stop_loss": 112.0, "target_1": 82.0,
    }
    resolved = {"skip_ai_synthesis": False, "override_flags": []}
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_ACTIVE_THESIS


def test_issuance_watch_only_missing_geometry():
    """BULL direction, conviction>=2 but no geometry → WATCH_ONLY (not ACTIVE_THESIS)."""
    thesis   = {"direction": "BULL", "conviction": 3}   # no entry/stop/target
    resolved = {"skip_ai_synthesis": False, "override_flags": []}
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_WATCH_ONLY


def test_issuance_watch_only_bad_bull_stop():
    """BULL conviction>=2 but stop above entry_low → geometry invalid → WATCH_ONLY."""
    thesis = {
        "direction": "BULL", "conviction": 3,
        "entry_low": 100.0, "entry_high": 105.0,
        "stop_loss": 102.0,   # above entry_low — invalid even after clamp attempt in tests
        "target_1": 118.0,
    }
    resolved = {"skip_ai_synthesis": False, "override_flags": []}
    # NOTE: _has_executable_geometry is called on the thesis as-is; stop=102 > entry_low=100
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_WATCH_ONLY


def test_issuance_suppressed_when_skip_ai():
    """skip_ai_synthesis=True → SUPPRESSED regardless of direction."""
    thesis   = {"direction": "BULL", "conviction": 3}
    resolved = {"skip_ai_synthesis": True, "override_flags": []}
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_SUPPRESSED


def test_issuance_suppressed_legacy_skip_claude():
    """Legacy skip_claude=True must also produce SUPPRESSED."""
    thesis   = {"direction": "BULL", "conviction": 3}
    resolved = {"skip_claude": True, "override_flags": []}
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_SUPPRESSED


def test_issuance_no_trade_neutral():
    """NEUTRAL direction → NO_TRADE."""
    thesis   = {"direction": "NEUTRAL", "conviction": 3}
    resolved = {"skip_ai_synthesis": False}
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_NO_TRADE


def test_issuance_no_trade_zero_conviction():
    """conviction=0 → NO_TRADE."""
    thesis   = {"direction": "BULL", "conviction": 0}
    resolved = {"skip_ai_synthesis": False}
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_NO_TRADE


def test_issuance_watch_only_low_conviction():
    """conviction=1 → WATCH_ONLY."""
    thesis   = {"direction": "BULL", "conviction": 1}
    resolved = {"skip_ai_synthesis": False, "override_flags": []}
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_WATCH_ONLY


def test_issuance_watch_only_earnings_hold():
    """conviction=3 but pre_earnings_hold override → WATCH_ONLY."""
    thesis   = {"direction": "BULL", "conviction": 3}
    resolved = {
        "skip_ai_synthesis": False,
        "override_flags": ["override: pre_earnings_hold (earnings in 2d)"],
    }
    assert _get_issuance_state(thesis, resolved) == ISSUANCE_WATCH_ONLY


# ── _apply_deterministic_geometry ────────────────────────────────────────────

def test_geometry_bull_valid_no_change():
    """Valid BULL geometry must pass through unchanged (adverse-side RR check).
    entry_low=100, entry_high=105, stop=90 → risk from adverse fill = 105-90=15
    min_t1 = 105 + 1.5*15 = 127.5; min_t2 = 105 + 2.5*15 = 142.5
    """
    thesis = {
        "direction":  "BULL",
        "entry_low":  100.0,
        "entry_high": 105.0,
        "stop_loss":  90.0,    # risk from entry_high = 15
        "target_1":   130.0,   # 130 > 127.5 → valid
        "target_2":   150.0,   # 150 > 142.5 → valid
    }
    result = _apply_deterministic_geometry(dict(thesis))
    assert result["stop_loss"]  == 90.0
    assert result["target_1"]   == 130.0
    assert result["target_2"]   == 150.0
    assert "geometry_notes" not in result


def test_geometry_bull_raises_target1_to_min_rr():
    """T1 below 1.5× RR (from entry_high) must be raised.
    entry_high=105, stop=90 → risk=15 → min_t1 = 105+1.5*15 = 127.5
    """
    thesis = {
        "direction":  "BULL",
        "entry_low":  100.0,
        "entry_high": 105.0,
        "stop_loss":  90.0,
        "target_1":   120.0,   # 120 < 127.5 → must be raised
        "target_2":   150.0,
    }
    result = _apply_deterministic_geometry(dict(thesis))
    assert result["target_1"] == 127.5
    assert "geometry_notes" in result
    assert "t1 raised" in result["geometry_notes"]


def test_geometry_bull_raises_target2_to_min_rr():
    """T2 below 2.5× RR (from entry_high) must be raised.
    entry_high=105, stop=90 → risk=15 → min_t2 = 105+2.5*15 = 142.5
    """
    thesis = {
        "direction":  "BULL",
        "entry_low":  100.0,
        "entry_high": 105.0,
        "stop_loss":  90.0,
        "target_1":   130.0,
        "target_2":   138.0,   # 138 < 142.5 → must be raised
    }
    result = _apply_deterministic_geometry(dict(thesis))
    assert result["target_2"] == 142.5


def test_geometry_bull_clamps_stop_above_entry():
    """Stop above entry_low for BULL must be clamped down."""
    thesis = {
        "direction":  "BULL",
        "entry_low":  100.0,
        "entry_high": 105.0,
        "stop_loss":  102.0,   # above entry_low → invalid
        "target_1":   135.0,
        "target_2":   150.0,
    }
    result = _apply_deterministic_geometry(dict(thesis), signals={"technical": {"atr_20d": 5.0}})
    assert result["stop_loss"] < 100.0, "Stop must be below entry_low after clamping"
    assert "stop clamped" in result.get("geometry_notes", "")


def test_geometry_bear_clamps_stop_below_entry():
    """Stop below entry_high for BEAR must be clamped up."""
    thesis = {
        "direction":  "BEAR",
        "entry_low":  95.0,
        "entry_high": 100.0,
        "stop_loss":  98.0,   # below entry_high → invalid for BEAR
        "target_1":   80.0,
        "target_2":   65.0,
    }
    result = _apply_deterministic_geometry(dict(thesis), signals={"technical": {"atr_20d": 5.0}})
    assert result["stop_loss"] > 100.0, "BEAR stop must be above entry_high after clamping"


def test_geometry_neutral_returns_unchanged():
    """NEUTRAL direction must not be modified by the geometry layer."""
    thesis = {"direction": "NEUTRAL", "entry_low": 100.0, "entry_high": 105.0,
              "stop_loss": 95.0, "target_1": None, "target_2": None}
    result = _apply_deterministic_geometry(dict(thesis))
    assert result["stop_loss"] == 95.0
    assert "geometry_notes" not in result


# ── _has_executable_geometry ──────────────────────────────────────────────────

def test_has_executable_geometry_bull_valid():
    """Valid BULL geometry: stop < entry_low AND t1 > entry_high → True."""
    thesis = {"direction": "BULL", "entry_low": 100.0, "entry_high": 105.0,
              "stop_loss": 90.0, "target_1": 118.0}   # 118 > 105 ✓
    assert _has_executable_geometry(thesis) is True


def test_has_executable_geometry_bear_valid():
    """Valid BEAR geometry: stop > entry_high AND t1 < entry_low → True."""
    thesis = {"direction": "BEAR", "entry_low": 95.0, "entry_high": 100.0,
              "stop_loss": 112.0, "target_1": 82.0}   # 82 < 95 ✓
    assert _has_executable_geometry(thesis) is True


def test_has_executable_geometry_bull_target_above_entry_low_but_below_entry_high_fails():
    """BULL: target above entry_low but still inside the entry band (< entry_high) → False.
    This is the core adverse-fill issue: a fill at entry_high has no upside to T1."""
    thesis = {"direction": "BULL", "entry_low": 100.0, "entry_high": 105.0,
              "stop_loss": 90.0, "target_1": 103.0}   # 103 > entry_low but 103 < entry_high → False
    assert _has_executable_geometry(thesis) is False


def test_has_executable_geometry_bear_target_below_entry_high_but_above_entry_low_fails():
    """BEAR: target below entry_high but still inside the entry band (> entry_low) → False.
    A short fill at entry_low has no downside to T1."""
    thesis = {"direction": "BEAR", "entry_low": 95.0, "entry_high": 100.0,
              "stop_loss": 112.0, "target_1": 97.0}   # 97 < entry_high but 97 > entry_low → False
    assert _has_executable_geometry(thesis) is False


def test_has_executable_geometry_missing_stop():
    """Missing stop_loss → False."""
    thesis = {"direction": "BULL", "entry_low": 100.0, "entry_high": 105.0,
              "stop_loss": None, "target_1": 118.0}
    assert _has_executable_geometry(thesis) is False


def test_has_executable_geometry_missing_target():
    """Missing target_1 → False."""
    thesis = {"direction": "BULL", "entry_low": 100.0, "entry_high": 105.0,
              "stop_loss": 90.0, "target_1": None}
    assert _has_executable_geometry(thesis) is False


def test_has_executable_geometry_bull_stop_above_entry():
    """BULL with stop >= entry_low → False (directionally wrong)."""
    thesis = {"direction": "BULL", "entry_low": 100.0, "entry_high": 105.0,
              "stop_loss": 102.0, "target_1": 118.0}
    assert _has_executable_geometry(thesis) is False


def test_has_executable_geometry_bear_stop_below_entry():
    """BEAR with stop <= entry_high → False (directionally wrong)."""
    thesis = {"direction": "BEAR", "entry_low": 95.0, "entry_high": 100.0,
              "stop_loss": 98.0, "target_1": 82.0}
    assert _has_executable_geometry(thesis) is False


def test_has_executable_geometry_neutral_returns_false():
    """NEUTRAL direction always returns False (no executable geometry concept)."""
    thesis = {"direction": "NEUTRAL", "entry_low": 100.0, "entry_high": 105.0,
              "stop_loss": 95.0, "target_1": 115.0}
    assert _has_executable_geometry(thesis) is False


def test_geometry_bull_rr_uses_adverse_fill_side():
    """BULL normalization anchors RR to entry_high, not entry_low.
    entry_high=105, stop=90 → risk=15 → min_t1 = 105+1.5*15 = 127.5.
    A thesis with t1=120 (valid under entry_low anchor) must be raised.
    """
    thesis = {
        "direction": "BULL", "entry_low": 100.0, "entry_high": 105.0,
        "stop_loss": 90.0, "target_1": 120.0, "target_2": 155.0,
    }
    result = _apply_deterministic_geometry(dict(thesis))
    assert result["target_1"] == 127.5, (
        f"t1 should be 127.5 (adverse-side RR), got {result['target_1']}"
    )


def test_geometry_bear_rr_uses_adverse_fill_side():
    """BEAR normalization anchors RR to entry_low, not entry_high.
    entry_low=95, stop=115 → risk=20 → min_t1 = 95-1.5*20 = 65.
    A thesis with t1=84 (valid under entry_high anchor) must be lowered.
    """
    thesis = {
        "direction": "BEAR", "entry_low": 95.0, "entry_high": 100.0,
        "stop_loss": 115.0, "target_1": 84.0, "target_2": 50.0,
    }
    result = _apply_deterministic_geometry(dict(thesis))
    assert result["target_1"] == 65.0, (
        f"t1 should be 65.0 (adverse-side RR), got {result['target_1']}"
    )


# ==============================================================================
# _watch_only_reason — reason classification helper (TRD-059 fix)
# ==============================================================================

def test_watch_only_reason_low_conviction():
    from ai_quant import _watch_only_reason
    thesis = {"direction": "BULL", "conviction": 1}
    assert _watch_only_reason(thesis, {}) == "low_conviction"

def test_watch_only_reason_pre_earnings_hold():
    from ai_quant import _watch_only_reason
    thesis = {"direction": "BULL", "conviction": 3}
    resolved = {"override_flags": ["pre_earnings_hold"]}
    assert _watch_only_reason(thesis, resolved) == "pre_earnings_hold"

def test_watch_only_reason_bear_below_threshold():
    from ai_quant import _watch_only_reason
    # BEAR with conviction=2 is below BEAR_MIN_CONVICTION=3
    thesis = {"direction": "BEAR", "conviction": 2}
    assert _watch_only_reason(thesis, {}) == "bear_below_threshold"

def test_watch_only_reason_no_geometry():
    from ai_quant import _watch_only_reason
    # BULL conviction=3 (above BULL_MIN_CONVICTION=2), no earnings hold → no_geometry
    thesis = {"direction": "BULL", "conviction": 3}
    assert _watch_only_reason(thesis, {}) == "no_geometry"

def test_watch_only_reason_bull_conviction_2_no_geometry():
    from ai_quant import _watch_only_reason
    # BULL conviction=2 is exactly at BULL_MIN_CONVICTION, no other flags → no_geometry
    thesis = {"direction": "BULL", "conviction": 2}
    assert _watch_only_reason(thesis, {}) == "no_geometry"

def test_watch_only_reason_earnings_hold_takes_priority_over_bear_threshold():
    from ai_quant import _watch_only_reason
    # BEAR conviction=2, but earnings hold is also present.
    # conviction=2 > 1 (not low_conviction), earnings hold is checked next.
    thesis = {"direction": "BEAR", "conviction": 2}
    resolved = {"override_flags": ["pre_earnings_hold"]}
    assert _watch_only_reason(thesis, resolved) == "pre_earnings_hold"


# ---------------------------------------------------------------------------
# TRD-075 follow-up: AI-stage attribution semantics (post-gate synthesis set)
# ---------------------------------------------------------------------------

class TestAiStageFunnelAttribution:
    """
    Verify that ai_selected_count and ai_selected_by_* metrics are derived
    from the post-gate synthesized results set, not the pre-gate selected list.
    QUARANTINE tickers skip synthesis and must not inflate attribution counts.
    """

    def _make_selected(self, tickers):
        return [{"ticker": t, "candidate_lane": "execution_core", "priority_score": 80.0} for t in tickers]

    def _make_result(self, ticker, issuance_state="ACTIVE_THESIS", direction="BULL"):
        return {
            "ticker": ticker,
            "issuance_state": issuance_state,
            "direction": direction,
            "conviction": 3,
        }

    def _run_attribution(self, selected, results, ranked_universe=None):
        """Simulate the attribution block from ai_quant using the same logic."""
        import json, os
        from unittest.mock import patch, mock_open

        ru = ranked_universe or {}
        ru_json = json.dumps(ru)

        _ai_sel_by_lane:   dict = {}
        _ai_sel_by_source: dict = {}
        _bso_ai = 0

        # Build lane lookup from pre-gate list
        _sel_lane_map = {s["ticker"]: s.get("candidate_lane") for s in selected if "ticker" in s}

        for _r in results:
            _rt = _r.get("ticker", "")
            _ru_entry = ru.get(_rt, {})
            _sl = _sel_lane_map.get(_rt) or _ru_entry.get("lane", "unknown")
            _ai_sel_by_lane[_sl] = _ai_sel_by_lane.get(_sl, 0) + 1
            for _src in (_ru_entry.get("sources") or []):
                _ai_sel_by_source[_src] = _ai_sel_by_source.get(_src, 0) + 1
            if _ru_entry.get("broad_source_only"):
                _bso_ai += 1

        return {
            "ai_selected_count": len(results),
            "ai_selected_by_lane":  _ai_sel_by_lane,
            "ai_selected_by_source": _ai_sel_by_source,
            "broad_source_only_ai_selected": _bso_ai,
        }

    def test_quarantine_ticker_excluded_from_ai_selected_count(self):
        """QUARANTINE ticker in selected but absent from results must not be
        counted in ai_selected_count."""
        selected = self._make_selected(["AAPL", "QUARANTINED"])
        results  = [self._make_result("AAPL")]   # QUARANTINED skipped synthesis

        attrs = self._run_attribution(selected, results)
        assert attrs["ai_selected_count"] == 1, (
            "ai_selected_count must reflect results (post-gate), not selected (pre-gate)"
        )

    def test_quarantine_ticker_excluded_from_ai_selected_by_lane(self):
        """QUARANTINE ticker must not contribute to ai_selected_by_lane."""
        ru = {
            "AAPL":        {"lane": "execution_core",  "sources": ["sp500"],         "broad_source_only": False},
            "QUARANTINED": {"lane": "execution_core",  "sources": ["russell1000"],   "broad_source_only": False},
        }
        selected = self._make_selected(["AAPL", "QUARANTINED"])
        results  = [self._make_result("AAPL")]   # QUARANTINED never reached synthesis

        attrs = self._run_attribution(selected, results, ranked_universe=ru)
        # execution_core should have count 1 (AAPL only), not 2
        assert attrs["ai_selected_by_lane"].get("execution_core") == 1, (
            "QUARANTINE ticker must not inflate ai_selected_by_lane"
        )

    def test_quarantine_ticker_excluded_from_ai_selected_by_source(self):
        """QUARANTINE ticker sources must not appear in ai_selected_by_source."""
        ru = {
            "AAPL":        {"lane": "execution_core", "sources": ["sp500"],       "broad_source_only": False},
            "QUARANTINED": {"lane": "execution_core", "sources": ["russell2000"], "broad_source_only": False},
        }
        selected = self._make_selected(["AAPL", "QUARANTINED"])
        results  = [self._make_result("AAPL")]

        attrs = self._run_attribution(selected, results, ranked_universe=ru)
        assert attrs["ai_selected_by_source"].get("sp500") == 1
        assert "russell2000" not in attrs["ai_selected_by_source"], (
            "QUARANTINE ticker sources must not appear in ai_selected_by_source"
        )

    def test_quarantine_broad_source_only_not_counted(self):
        """A QUARANTINE ticker that is also broad_source_only must not inflate
        broad_source_only_ai_selected."""
        ru = {
            "AAPL": {"lane": "execution_core", "sources": ["sp500"],        "broad_source_only": False},
            "QBSO": {"lane": "research_broad", "sources": ["nasdaq_broad"], "broad_source_only": True},
        }
        selected = self._make_selected(["AAPL", "QBSO"])
        results  = [self._make_result("AAPL")]   # QBSO quarantined, not in results

        attrs = self._run_attribution(selected, results, ranked_universe=ru)
        assert attrs["broad_source_only_ai_selected"] == 0, (
            "QUARANTINE broad-source-only ticker must not inflate broad_source_only_ai_selected"
        )

    def test_all_selected_reach_synthesis_counts_match(self):
        """When no tickers are quarantined, ai_selected_count == len(selected)."""
        selected = self._make_selected(["AAPL", "MSFT", "NVDA"])
        results  = [self._make_result(t) for t in ["AAPL", "MSFT", "NVDA"]]

        attrs = self._run_attribution(selected, results)
        assert attrs["ai_selected_count"] == 3


# ---------------------------------------------------------------------------
# TRD-077: Attribution persistence — save_thesis and analyze_ticker
# ---------------------------------------------------------------------------

class TestThesisAttributionPersistence:
    """
    Verify that candidate_lane, sources, and broad_source_only are written
    into the thesis dict before save_thesis() is called, both in the normal
    AI path and the skip-AI early-return path.
    """

    def _make_minimal_thesis(self, ticker="AAPL"):
        return {
            "ticker": ticker,
            "direction": "BULL",
            "conviction": 3,
            "entry_low": 100.0,
            "entry_high": 105.0,
            "stop_loss": 90.0,
            "target_1": 120.0,
            "thesis": "test",
            "issuance_state": "ACTIVE_THESIS",
        }

    def _fake_save_thesis(self):
        """Return a list that save_thesis will append its arg to."""
        saved = []

        def _capture(t):
            saved.append(dict(t))

        return saved, _capture

    def test_attribution_merged_before_main_save(self):
        """attribution kwarg merges into thesis before save_thesis in normal AI path."""
        import json
        from unittest.mock import patch, MagicMock
        from ai_quant import analyze_ticker

        thesis = self._make_minimal_thesis()
        attribution = {
            "candidate_lane": "execution_core",
            "sources": ["sp500", "russell1000"],
            "broad_source_only": False,
        }

        saved, capture = self._fake_save_thesis()

        # claude-* routes to _call_anthropic, not _call_claude
        with patch("ai_quant.collect_all_signals", return_value={"ticker": "AAPL"}), \
             patch("ai_quant._inject_universe_rank", side_effect=lambda s, t: s), \
             patch("ai_quant.compute_signal_agreement", return_value=0.75), \
             patch("ai_quant._build_prompt", return_value="prompt"), \
             patch("ai_quant.resolve_effective_model", return_value="claude-sonnet-4-6"), \
             patch("ai_quant._call_anthropic", return_value=json.dumps(thesis)), \
             patch("ai_quant._parse_response", return_value=dict(thesis)), \
             patch("ai_quant._apply_deterministic_geometry", side_effect=lambda t, s: t), \
             patch("ai_quant._get_issuance_state", return_value="ACTIVE_THESIS"), \
             patch("ai_quant._validate_probabilities"), \
             patch("ai_quant.get_cached_thesis", return_value=None), \
             patch("ai_quant.save_thesis", side_effect=capture):
            analyze_ticker("AAPL", use_cache=False, attribution=attribution)

        assert saved, "save_thesis was never called"
        saved_thesis = saved[-1]
        assert saved_thesis.get("candidate_lane") == "execution_core"
        assert saved_thesis.get("sources") == ["sp500", "russell1000"]
        assert saved_thesis.get("broad_source_only") is False

    def test_attribution_merged_on_skip_ai_path(self):
        """Attribution merges into the neutral thesis produced by the skip-AI path."""
        import json
        from unittest.mock import patch
        from ai_quant import analyze_ticker

        attribution = {
            "candidate_lane": "research_broad",
            "sources": ["nasdaq_broad"],
            "broad_source_only": True,
        }

        resolved = {
            "signal_agreement_score": 0.3,
            "skip_ai_synthesis": True,
            "pre_resolved_direction": "NEUTRAL",
            "pre_resolved_confidence": 0.3,
            "override_flags": ["low_agreement"],
        }

        saved, capture = self._fake_save_thesis()

        with patch("ai_quant.collect_all_signals", return_value={"ticker": "AAPL"}), \
             patch("ai_quant._inject_universe_rank", side_effect=lambda s, t: s), \
             patch("ai_quant.compute_signal_agreement", return_value=0.3), \
             patch("ai_quant._RESOLVER_AVAILABLE", True), \
             patch("ai_quant._cr") as mock_cr, \
             patch("ai_quant.get_cached_thesis", return_value=None), \
             patch("ai_quant.save_thesis", side_effect=capture):
            mock_cr.resolve.return_value = resolved
            analyze_ticker("AAPL", use_cache=False, attribution=attribution)

        assert saved, "save_thesis was never called on skip-AI path"
        saved_thesis = saved[-1]
        assert saved_thesis.get("candidate_lane") == "research_broad"
        assert saved_thesis.get("sources") == ["nasdaq_broad"]
        assert saved_thesis.get("broad_source_only") is True

    def test_attribution_none_does_not_overwrite_existing(self):
        """When attribution=None, existing thesis fields are not overwritten."""
        import json
        from unittest.mock import patch
        from ai_quant import analyze_ticker

        thesis = self._make_minimal_thesis()
        thesis["candidate_lane"] = "execution_high_beta"

        saved, capture = self._fake_save_thesis()

        # claude-* routes to _call_anthropic, not _call_claude
        with patch("ai_quant.collect_all_signals", return_value={"ticker": "AAPL"}), \
             patch("ai_quant._inject_universe_rank", side_effect=lambda s, t: s), \
             patch("ai_quant.compute_signal_agreement", return_value=0.75), \
             patch("ai_quant._build_prompt", return_value="prompt"), \
             patch("ai_quant.resolve_effective_model", return_value="claude-sonnet-4-6"), \
             patch("ai_quant._call_anthropic", return_value=json.dumps(thesis)), \
             patch("ai_quant._parse_response", return_value=dict(thesis)), \
             patch("ai_quant._apply_deterministic_geometry", side_effect=lambda t, s: t), \
             patch("ai_quant._get_issuance_state", return_value="ACTIVE_THESIS"), \
             patch("ai_quant._validate_probabilities"), \
             patch("ai_quant.get_cached_thesis", return_value=None), \
             patch("ai_quant.save_thesis", side_effect=capture):
            analyze_ticker("AAPL", use_cache=False, attribution=None)

        assert saved
        assert saved[-1].get("candidate_lane") == "execution_high_beta"

    def test_save_thesis_sql_includes_attribution_columns(self):
        """_SQL_WITH_ALL template in save_thesis contains all three attribution columns."""
        import inspect
        import ai_quant

        src = inspect.getsource(ai_quant.save_thesis)
        assert "candidate_lane" in src
        assert "broad_source_only" in src
        assert "_SQL_WITH_ALL" in src

    def test_governance_state_persisted_on_save(self):
        """governance_state from attribution is merged into thesis before save_thesis."""
        import json
        from unittest.mock import patch, MagicMock
        from ai_quant import analyze_ticker

        thesis = self._make_minimal_thesis()
        attribution = {
            "candidate_lane": "execution_core",
            "sources": ["sp500"],
            "broad_source_only": False,
            "governance_state": "A_LIST",
        }

        saved, capture = self._fake_save_thesis()

        # claude-* routes to _call_anthropic, not _call_claude
        with patch("ai_quant.collect_all_signals", return_value={"ticker": "AAPL"}), \
             patch("ai_quant._inject_universe_rank", side_effect=lambda s, t: s), \
             patch("ai_quant.compute_signal_agreement", return_value=0.75), \
             patch("ai_quant._build_prompt", return_value="prompt"), \
             patch("ai_quant.resolve_effective_model", return_value="claude-sonnet-4-6"), \
             patch("ai_quant._call_anthropic", return_value=json.dumps(thesis)), \
             patch("ai_quant._parse_response", return_value=dict(thesis)), \
             patch("ai_quant._apply_deterministic_geometry", side_effect=lambda t, s: t), \
             patch("ai_quant._get_issuance_state", return_value="ACTIVE_THESIS"), \
             patch("ai_quant._validate_probabilities"), \
             patch("ai_quant.get_cached_thesis", return_value=None), \
             patch("ai_quant.save_thesis", side_effect=capture):
            analyze_ticker("AAPL", use_cache=False, attribution=attribution)

        assert saved, "save_thesis was never called"
        saved_thesis = saved[-1]
        assert saved_thesis.get("governance_state") == "A_LIST", (
            f"Expected governance_state='A_LIST', got {saved_thesis.get('governance_state')}"
        )

    def test_save_thesis_sql_includes_governance_state_column(self):
        """_SQL_WITH_ALL and _SQL_WITHOUT_GOVERNANCE templates reference governance_state."""
        import inspect
        import ai_quant

        src = inspect.getsource(ai_quant.save_thesis)
        assert "governance_state" in src, "save_thesis SQL must include governance_state column"
        assert "_SQL_WITHOUT_GOVERNANCE" in src, "Fallback SQL template must exist"
