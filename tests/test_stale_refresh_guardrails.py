import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, call

from ai_quant import _get_env_clean, resolve_effective_model, _require_api_key, AI_PREMIUM_THRESHOLD
from scripts.refresh_stale_and_notify import classify_refresh_results


def test_get_env_clean_strips_wrapping_quotes_and_whitespace(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", '  "abc123"  ')
    assert _get_env_clean("XAI_API_KEY") == "abc123"


def test_classify_refresh_results_uses_newer_thesis_timestamp():
    old_snap = {
        "AAA": {"created_at": datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)},
        "BBB": {"created_at": datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)},
    }
    new_snap = {
        "AAA": {"created_at": datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc)},
        "BBB": {"created_at": datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)},
    }

    updated, untouched = classify_refresh_results(["AAA", "BBB"], old_snap, new_snap)

    assert updated == ["AAA"]
    assert untouched == ["BBB"]


# ── resolve_effective_model ────────────────────────────────────────────────────

def test_resolve_effective_model_returns_explicit_llm():
    assert resolve_effective_model("gpt-5.5") == "gpt-5.5"
    assert resolve_effective_model("claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert resolve_effective_model("grok-4.3") == "grok-4.3"


def test_resolve_effective_model_falls_back_to_ai_model_default():
    with patch("ai_quant.AI_MODEL_DEFAULT", "gpt-5.5"):
        result = resolve_effective_model(None)
    assert result == "gpt-5.5"


def test_resolve_effective_model_falls_back_to_ai_model_default_claude():
    with patch("ai_quant.AI_MODEL_DEFAULT", "claude-sonnet-4-6"):
        result = resolve_effective_model(None)
    assert result == "claude-sonnet-4-6"


# ── _require_api_key ───────────────────────────────────────────────────────────

def test_require_api_key_grok_exits_when_missing(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        _require_api_key("grok-4.3")


def test_require_api_key_grok_passes_when_set(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    _require_api_key("grok-4.3")  # should not raise


def test_require_api_key_openai_exits_when_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        _require_api_key("gpt-5.5")


def test_require_api_key_openai_passes_when_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _require_api_key("gpt-5.5")  # should not raise


def test_require_api_key_claude_exits_when_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        _require_api_key("claude-sonnet-4-6")


def test_require_api_key_claude_passes_when_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _require_api_key("claude-sonnet-4-6")  # should not raise


# ── stale-refresh path: DB default is OpenAI, no --llm passed ─────────────────

def test_stale_refresh_uses_db_default_when_no_llm_flag(monkeypatch):
    """When refresh_stale_and_notify calls ai_quant.py without --llm,
    the effective model should come from AI_MODEL_DEFAULT (DB), not grok-4.3."""
    with patch("ai_quant.AI_MODEL_DEFAULT", "gpt-5.5"):
        result = resolve_effective_model(None)
    assert result == "gpt-5.5"
    assert not result.startswith("grok-"), (
        "Stale refresh must not fall into xAI path when DB default is an OpenAI model"
    )


# ── Grok premium escalation (QA regression) ──────────────────────────────────

def test_grok_default_path_allows_premium_escalation():
    """When llm=None and DB default is grok, high prob should escalate to AI_MODEL_PREMIUM.
    Regression guard: QA-006 fix must not pin model=effective_llm when llm arg is None."""
    import ai_quant

    captured = {}

    def fake_call_claude(prompt, verbose=False, use_thinking=False, model=None):
        captured["use_thinking"] = use_thinking
        captured["model"] = model
        return '{"direction":"BULL","conviction":3,"bull_probability":0.6,"bear_probability":0.2,"neutral_probability":0.2,"time_horizon":"1-2 weeks","entry_low":100,"entry_high":105,"stop_loss":95,"target_1":115,"target_2":120,"position_size_pct":5,"thesis":"test","data_quality":"HIGH","notes":"","catalysts":[],"risks":[],"primary_scenario":"up","bear_scenario":"down","key_invalidation":"95","signal_agreement_score":0.8,"expected_moves":[]}'

    high_prob_signals = {
        "ticker": "TEST",
        "timestamp": "2026-06-05T00:00:00",
        "prob_combined": AI_PREMIUM_THRESHOLD + 0.01,
        "signal_agreement_score": AI_PREMIUM_THRESHOLD + 0.01,
    }

    _high_prob = AI_PREMIUM_THRESHOLD + 0.01
    _fake_prob_result = {"prob_combined": _high_prob, "prob_technical": _high_prob,
                         "prob_options": _high_prob, "prob_catalyst": _high_prob, "prob_news": _high_prob}

    with patch("ai_quant._call_claude", side_effect=fake_call_claude), \
         patch("ai_quant.collect_all_signals", return_value=high_prob_signals), \
         patch("ai_quant.get_cached_thesis", return_value=None), \
         patch("ai_quant._inject_universe_rank", side_effect=lambda s, t: s), \
         patch("ai_quant.compute_signal_agreement", return_value=_high_prob), \
         patch("utils.prob_engine.compute_prob_combined", return_value=_fake_prob_result), \
         patch("ai_quant.save_thesis"), \
         patch("ai_quant.AI_MODEL_DEFAULT", "grok-4.3"):
        ai_quant.analyze_ticker("TEST", llm=None, use_cache=False)

    assert captured.get("use_thinking") is True, "Premium escalation must fire for high prob"
    assert captured.get("model") is None, "model must NOT be pinned when llm=None (default Grok path)"


def test_grok_explicit_override_pins_model():
    """When llm='grok-4.3' is explicitly passed, model must be pinned regardless of prob."""
    import ai_quant

    captured = {}

    def fake_call_claude(prompt, verbose=False, use_thinking=False, model=None):
        captured["use_thinking"] = use_thinking
        captured["model"] = model
        return '{"direction":"BULL","conviction":3,"bull_probability":0.6,"bear_probability":0.2,"neutral_probability":0.2,"time_horizon":"1-2 weeks","entry_low":100,"entry_high":105,"stop_loss":95,"target_1":115,"target_2":120,"position_size_pct":5,"thesis":"test","data_quality":"HIGH","notes":"","catalysts":[],"risks":[],"primary_scenario":"up","bear_scenario":"down","key_invalidation":"95","signal_agreement_score":0.8,"expected_moves":[]}'

    high_prob_signals = {
        "ticker": "TEST",
        "timestamp": "2026-06-05T00:00:00",
        "prob_combined": AI_PREMIUM_THRESHOLD + 0.01,
        "signal_agreement_score": AI_PREMIUM_THRESHOLD + 0.01,
    }

    _high_prob = AI_PREMIUM_THRESHOLD + 0.01
    _fake_prob_result = {"prob_combined": _high_prob, "prob_technical": _high_prob,
                         "prob_options": _high_prob, "prob_catalyst": _high_prob, "prob_news": _high_prob}

    with patch("ai_quant._call_claude", side_effect=fake_call_claude), \
         patch("ai_quant.collect_all_signals", return_value=high_prob_signals), \
         patch("ai_quant.get_cached_thesis", return_value=None), \
         patch("ai_quant._inject_universe_rank", side_effect=lambda s, t: s), \
         patch("ai_quant.compute_signal_agreement", return_value=_high_prob), \
         patch("utils.prob_engine.compute_prob_combined", return_value=_fake_prob_result), \
         patch("ai_quant.save_thesis"), \
         patch("ai_quant.AI_MODEL_DEFAULT", "grok-4.3"):
        ai_quant.analyze_ticker("TEST", llm="grok-4.3", use_cache=False)

    assert captured.get("model") == "grok-4.3", "Explicit Grok override must pin the model"
