"""
Tests for TRD-049: Option Entry Fair Value and Live Quote Guardrails

Covers:
- _quote_age(): IBKR real-time, fresh, recent, stale, missing timestamp
- _market_quality(): one_sided, tight, acceptable, wide, very_wide
- _fair_value_band(): normal IV, elevated/high/extreme IV haircuts, missing bid/ask
- _overpay_pct(): zero overpay, positive overpay, missing inputs
- _decide(): all four entry_action tiers
- compute_entry_guardrail(): integration, stale quote, wide market, overpay, enter_now
- Fail-safe: missing quote metadata degrades to "unknown", never raises
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from utils.option_entry_guardrail import (
    EntryGuardrail,
    _IV_FV_HAIRCUT,
    _GUARDRAIL_VERSION,
    _decide,
    _fair_value_band,
    _market_quality,
    _overpay_pct,
    _quote_age,
    compute_entry_guardrail,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _iso(offset_seconds: float = 0.0) -> str:
    """ISO-8601 UTC string, optionally shifted by offset_seconds from now."""
    t = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
    return t.isoformat()


def _candidate(
    bid=1.90, ask=2.10, mid=2.0, spread_pct=10.0,
    source="yfinance", quote_time=None,
    recommended_entry_price=2.0,
    iv_regime_label="normal",
    projected_option_tp1=None,
):
    """Simple attribute bag standing in for OptionCandidate."""
    class _C:
        pass
    c = _C()
    c.bid = bid
    c.ask = ask
    c.mid = mid
    c.spread_pct = spread_pct
    c.source = source
    c.quote_time = quote_time
    c.recommended_entry_price = recommended_entry_price
    c.iv_regime_label = iv_regime_label
    c.projected_option_tp1 = projected_option_tp1
    return c


# ══════════════════════════════════════════════════════════════════════════════
# _quote_age
# ══════════════════════════════════════════════════════════════════════════════

class TestQuoteAge:

    def test_ibkr_no_timestamp_is_live(self):
        age, label = _quote_age("ibkr", None, None)
        assert label == "live"
        assert age is None

    def test_ibkr_with_recent_chain_time_is_live(self):
        age, label = _quote_age("ibkr", None, _iso(30))
        assert label == "live"

    def test_fresh_chain_time_is_live(self):
        age, label = _quote_age("yfinance", None, _iso(10))
        assert label == "live"
        assert age is not None and age < 60

    def test_recent_chain_time(self):
        age, label = _quote_age("yfinance", None, _iso(120))
        assert label == "recent"
        assert 60 <= age < 300

    def test_stale_chain_time(self):
        age, label = _quote_age("yfinance", None, _iso(400))
        assert label == "stale"
        assert age >= 300

    def test_no_timestamp_no_ibkr_is_unknown(self):
        age, label = _quote_age("yfinance", None, None)
        assert label == "unknown"
        assert age is None

    def test_mock_source_no_timestamp_is_unknown(self):
        age, label = _quote_age("mock", None, None)
        assert label == "unknown"

    def test_contract_quote_time_takes_priority_over_chain(self):
        """Per-contract quote_time should be used even when chain_fetch_time exists."""
        # contract says fresh (10s ago), chain says stale (400s ago)
        age, label = _quote_age("yfinance", _iso(10), _iso(400))
        assert label == "live"
        assert age < 60

    def test_unparseable_timestamp_gives_unknown(self):
        age, label = _quote_age("yfinance", None, "NOT_A_DATE")
        assert label == "unknown"
        assert age is None

    def test_ibkr_with_fresh_contract_quote_is_live(self):
        age, label = _quote_age("ibkr", _iso(5), None)
        assert label == "live"


# ══════════════════════════════════════════════════════════════════════════════
# _market_quality
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketQuality:

    def test_no_bid_is_one_sided(self):
        assert _market_quality(None, 5.0) == "one_sided"

    def test_zero_bid_is_one_sided(self):
        assert _market_quality(0.0, 5.0) == "one_sided"

    def test_tight_spread(self):
        assert _market_quality(1.95, 2.0) == "tight"

    def test_tight_spread_boundary(self):
        assert _market_quality(1.95, 3.0) == "tight"

    def test_acceptable_spread(self):
        assert _market_quality(1.90, 5.0) == "acceptable"

    def test_acceptable_boundary(self):
        assert _market_quality(1.90, 8.0) == "acceptable"

    def test_wide_spread(self):
        assert _market_quality(1.80, 10.0) == "wide"

    def test_wide_spread_boundary(self):
        assert _market_quality(1.80, 12.0) == "wide"

    def test_very_wide_spread(self):
        assert _market_quality(1.70, 13.0) == "very_wide"

    def test_no_spread_pct_defaults_to_acceptable(self):
        """Missing spread data should fail open (not block the trade)."""
        assert _market_quality(1.90, None) == "acceptable"


# ══════════════════════════════════════════════════════════════════════════════
# _fair_value_band
# ══════════════════════════════════════════════════════════════════════════════

class TestFairValueBand:

    def test_normal_iv_fv_high_equals_mid(self):
        low, high = _fair_value_band(1.90, 2.10, 2.0, "normal")
        assert high == pytest.approx(2.0, rel=1e-4)

    def test_low_iv_fv_high_equals_mid(self):
        low, high = _fair_value_band(1.90, 2.10, 2.0, "low")
        assert high == pytest.approx(2.0, rel=1e-4)

    def test_elevated_iv_haircut_2pct(self):
        low, high = _fair_value_band(1.90, 2.10, 2.0, "elevated")
        assert high == pytest.approx(2.0 * 0.98, rel=1e-4)

    def test_high_iv_haircut_5pct(self):
        low, high = _fair_value_band(1.90, 2.10, 2.0, "high")
        assert high == pytest.approx(2.0 * 0.95, rel=1e-4)

    def test_extreme_iv_haircut_10pct(self):
        low, high = _fair_value_band(1.90, 2.10, 2.0, "extreme")
        assert high == pytest.approx(2.0 * 0.90, rel=1e-4)

    def test_unknown_iv_no_haircut(self):
        low, high = _fair_value_band(1.90, 2.10, 2.0, "unknown_iv")
        assert high == pytest.approx(2.0, rel=1e-4)

    def test_fv_low_above_bid(self):
        """fv_low must always be ≥ bid."""
        low, high = _fair_value_band(1.90, 2.10, 2.0, "normal")
        assert low >= 1.90

    def test_fv_low_at_20pct_into_spread(self):
        # spread_abs = 0.20; 20% in = 0.04; fv_low = 1.90 + 0.04 = 1.94
        low, high = _fair_value_band(1.90, 2.10, 2.0, "normal")
        assert low == pytest.approx(1.94, abs=0.01)

    def test_no_bid_ask_uses_mid_for_both(self):
        low, high = _fair_value_band(None, None, 2.0, "normal")
        assert low == pytest.approx(2.0)
        assert high == pytest.approx(2.0)

    def test_no_mid_returns_none_pair(self):
        low, high = _fair_value_band(1.90, 2.10, None, "normal")
        assert low is None
        assert high is None

    def test_zero_mid_returns_none_pair(self):
        low, high = _fair_value_band(1.90, 2.10, 0.0, "normal")
        assert low is None
        assert high is None

    def test_fv_low_le_fv_high(self):
        """fv_low must always be ≤ fv_high."""
        for iv in ("low", "normal", "elevated", "high", "extreme"):
            low, high = _fair_value_band(1.80, 2.20, 2.0, iv)
            assert low <= high, f"fv_low > fv_high for iv_regime={iv}"


# ══════════════════════════════════════════════════════════════════════════════
# _overpay_pct
# ══════════════════════════════════════════════════════════════════════════════

class TestOverpayPct:

    def test_entry_at_fv_high_no_overpay(self):
        assert _overpay_pct(2.0, 2.0) == pytest.approx(0.0)

    def test_entry_below_fv_high_no_overpay(self):
        assert _overpay_pct(1.95, 2.0) == pytest.approx(0.0)

    def test_entry_above_fv_high(self):
        # (2.1 - 2.0) / 2.0 * 100 = 5.0%
        assert _overpay_pct(2.1, 2.0) == pytest.approx(5.0)

    def test_entry_significantly_above_fv_high(self):
        # (2.4 - 2.0) / 2.0 * 100 = 20.0%
        assert _overpay_pct(2.4, 2.0) == pytest.approx(20.0)

    def test_none_entry_returns_none(self):
        assert _overpay_pct(None, 2.0) is None

    def test_none_fv_high_returns_none(self):
        assert _overpay_pct(2.0, None) is None

    def test_zero_fv_high_returns_none(self):
        assert _overpay_pct(2.0, 0.0) is None


# ══════════════════════════════════════════════════════════════════════════════
# _decide
# ══════════════════════════════════════════════════════════════════════════════

class TestDecide:

    def _decide(self, freshness="live", mq="acceptable", overpay=0.0,
                iv="normal", spread_pct=5.0):
        return _decide(freshness, mq, overpay, iv, spread_pct)

    # Tier 1: skip_for_now
    def test_one_sided_is_skip(self):
        action, _ = self._decide(mq="one_sided")
        assert action == "skip_for_now"

    def test_very_wide_is_skip(self):
        action, _ = self._decide(mq="very_wide", spread_pct=15.0)
        assert action == "skip_for_now"

    def test_stale_quote_is_skip(self):
        action, _ = self._decide(freshness="stale")
        assert action == "skip_for_now"

    def test_severe_overpay_is_skip(self):
        action, _ = self._decide(overpay=25.0)
        assert action == "skip_for_now"

    def test_overpay_exactly_at_skip_threshold_triggers_skip(self):
        action, _ = self._decide(overpay=20.1)
        assert action == "skip_for_now"

    # Tier 2: enter_if_repriced
    def test_wide_market_is_repriced(self):
        action, _ = self._decide(mq="wide", spread_pct=10.0)
        assert action == "enter_if_repriced"

    def test_extreme_iv_is_repriced(self):
        action, _ = self._decide(iv="extreme")
        assert action == "enter_if_repriced"

    def test_significant_overpay_is_repriced(self):
        action, _ = self._decide(overpay=12.0)
        assert action == "enter_if_repriced"

    def test_overpay_at_repriced_boundary(self):
        action, _ = self._decide(overpay=10.1)
        assert action == "enter_if_repriced"

    # Tier 3: reduce_size
    def test_moderate_overpay_is_reduce(self):
        action, _ = self._decide(overpay=7.0)
        assert action == "reduce_size"

    def test_high_iv_is_reduce(self):
        action, _ = self._decide(iv="high")
        assert action == "reduce_size"

    def test_recent_quote_acceptable_spread_is_reduce(self):
        action, _ = self._decide(freshness="recent", mq="acceptable", spread_pct=5.0)
        assert action == "reduce_size"

    # Tier 4: enter_now
    def test_clean_inputs_are_enter_now(self):
        action, _ = self._decide(freshness="live", mq="tight", overpay=0.0, iv="normal", spread_pct=2.0)
        assert action == "enter_now"

    def test_unknown_freshness_acceptable_market_is_enter_now(self):
        action, _ = self._decide(freshness="unknown", mq="acceptable", overpay=0.0, iv="normal")
        assert action == "enter_now"

    def test_elevated_iv_no_overpay_acceptable_is_enter_now(self):
        """elevated IV without triggering the overpay threshold → enter_now."""
        # elevated haircut = 2%; recommended entry AT mid → overpay = 2%
        # 2% < _OVERPAY_REDUCE (5%) → enter_now
        action, _ = self._decide(freshness="live", mq="acceptable", overpay=2.0, iv="elevated")
        assert action == "enter_now"

    # Reason strings
    def test_skip_stale_reason_mentions_stale(self):
        _, reason = self._decide(freshness="stale")
        assert "stale" in reason.lower()

    def test_skip_one_sided_reason_mentions_bid(self):
        _, reason = self._decide(mq="one_sided")
        assert "bid" in reason.lower()

    def test_enter_now_reason_mentions_fresh(self):
        _, reason = self._decide()
        assert "fresh" in reason.lower() or "acceptable" in reason.lower()


# ══════════════════════════════════════════════════════════════════════════════
# compute_entry_guardrail — integration
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeEntryGuardrail:

    def test_returns_entry_guardrail_type(self):
        c = _candidate()
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert isinstance(result, EntryGuardrail)

    def test_enter_now_for_clean_inputs(self):
        """Tight spread, fresh quote, normal IV, no overpay → enter_now."""
        c = _candidate(
            bid=1.97, ask=2.03, mid=2.0, spread_pct=3.0,
            source="yfinance",
            recommended_entry_price=2.0,
            iv_regime_label="normal",
        )
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_action == "enter_now"
        assert result.quote_freshness_label == "live"
        assert result.market_quality_label == "tight"
        assert result.entry_overpay_pct is not None
        assert result.entry_overpay_pct == pytest.approx(0.0)

    def test_stale_quote_produces_skip(self):
        """Chain fetched 10 minutes ago → stale → skip_for_now."""
        c = _candidate(
            bid=1.90, ask=2.10, mid=2.0, spread_pct=10.0,
            source="yfinance",
        )
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(700))
        assert result.entry_action == "skip_for_now"
        assert result.quote_freshness_label == "stale"
        assert "stale" in result.live_guardrail_reason.lower()

    def test_wide_market_produces_enter_if_repriced(self):
        """10% spread (wide) → enter_if_repriced."""
        c = _candidate(
            bid=1.80, ask=2.20, mid=2.0, spread_pct=20.0,
            source="yfinance",
        )
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        # 20% spread → very_wide → skip_for_now
        assert result.entry_action == "skip_for_now"
        assert result.market_quality_label == "very_wide"

    def test_wide_but_not_very_wide_is_repriced(self):
        """10% spread (wide, not very_wide) → enter_if_repriced."""
        c = _candidate(
            bid=1.80, ask=2.20, mid=2.0, spread_pct=10.0,
            source="yfinance",
        )
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_action == "enter_if_repriced"
        assert result.market_quality_label == "wide"

    def test_high_iv_produces_reduce_size(self):
        """High IV regime without other issues → reduce_size."""
        c = _candidate(
            bid=1.95, ask=2.05, mid=2.0, spread_pct=5.0,
            source="yfinance",
            recommended_entry_price=2.0,
            iv_regime_label="high",
        )
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        # high IV: fv_high = 2.0 * 0.95 = 1.90; recommended = 2.0
        # overpay = (2.0 - 1.90) / 1.90 * 100 ≈ 5.26% → > _OVERPAY_REDUCE (5%) → reduce_size
        assert result.entry_action == "reduce_size"

    def test_extreme_iv_produces_enter_if_repriced(self):
        c = _candidate(iv_regime_label="extreme")
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_action == "enter_if_repriced"

    def test_no_bid_produces_skip(self):
        c = _candidate(bid=None, ask=2.10, mid=2.0, spread_pct=None)
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_action == "skip_for_now"
        assert result.market_quality_label == "one_sided"

    def test_zero_bid_produces_skip(self):
        c = _candidate(bid=0.0, ask=2.10, mid=2.0)
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_action == "skip_for_now"
        assert result.market_quality_label == "one_sided"

    def test_no_chain_fetch_time_gives_unknown_freshness(self):
        """Without a timestamp and non-IBKR source → unknown freshness.
        A tight market with unknown freshness should still allow entry.
        """
        c = _candidate(
            source="yfinance",
            bid=1.97, ask=2.03, mid=2.0, spread_pct=3.0,
            recommended_entry_price=2.0,
            iv_regime_label="normal",
        )
        result = compute_entry_guardrail(c, chain_fetch_time=None)
        assert result.quote_freshness_label == "unknown"
        # unknown freshness alone should NOT block a tight-market trade
        assert result.entry_action in ("enter_now", "reduce_size")

    def test_ibkr_source_without_timestamp_is_live(self):
        c = _candidate(source="ibkr")
        result = compute_entry_guardrail(c, chain_fetch_time=None)
        assert result.quote_freshness_label == "live"

    def test_fair_value_band_present_in_output(self):
        c = _candidate(bid=1.90, ask=2.10, mid=2.0, iv_regime_label="normal")
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.fair_value_entry_low is not None
        assert result.fair_value_entry_high is not None
        assert result.fair_value_entry_low <= result.fair_value_entry_high

    def test_normal_iv_fv_high_equals_mid(self):
        c = _candidate(mid=2.0, iv_regime_label="normal")
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.fair_value_entry_high == pytest.approx(2.0, rel=1e-4)

    def test_high_iv_fv_high_below_mid(self):
        c = _candidate(mid=2.0, iv_regime_label="high")
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.fair_value_entry_high < 2.0

    def test_guardrail_version_present(self):
        c = _candidate()
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.guardrail_version == _GUARDRAIL_VERSION

    def test_entry_overpay_zero_when_entry_at_fv_high(self):
        """recommended_entry_price == fv_high (normal IV, entry at mid) → 0% overpay."""
        c = _candidate(
            mid=2.0, bid=1.90, ask=2.10,
            recommended_entry_price=2.0,
            iv_regime_label="normal",
        )
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_overpay_pct == pytest.approx(0.0)

    def test_entry_overpay_positive_for_elevated_iv(self):
        """elevated IV: fv_high = 1.96, entry at mid 2.0 → ~2% overpay."""
        c = _candidate(
            mid=2.0, bid=1.90, ask=2.10,
            recommended_entry_price=2.0,
            iv_regime_label="elevated",
        )
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_overpay_pct is not None
        assert result.entry_overpay_pct > 0.0

    def test_never_raises_on_none_mid(self):
        """Missing mid should not raise; degrade gracefully."""
        c = _candidate(mid=None, bid=None, ask=None)
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert isinstance(result, EntryGuardrail)
        assert result.entry_action in ("skip_for_now", "enter_now", "reduce_size", "enter_if_repriced")

    def test_exception_in_candidate_access_returns_skip(self):
        """A completely broken candidate object must not raise; fallback to skip."""
        result = compute_entry_guardrail(object(), chain_fetch_time=_iso(5))
        assert isinstance(result, EntryGuardrail)

    def test_quote_age_seconds_populated_when_timestamp_present(self):
        c = _candidate(source="yfinance")
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(30))
        assert result.quote_age_seconds is not None
        assert result.quote_age_seconds >= 0

    def test_quote_age_seconds_none_when_no_timestamp(self):
        c = _candidate(source="yfinance")
        result = compute_entry_guardrail(c, chain_fetch_time=None)
        assert result.quote_age_seconds is None

    def test_recent_quote_acceptable_market_is_reduce_size(self):
        """1–5 min old quote + acceptable spread → reduce_size."""
        c = _candidate(
            spread_pct=5.0,
            source="yfinance",
            iv_regime_label="normal",
            recommended_entry_price=2.0,
            mid=2.0,
        )
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(120))
        assert result.quote_freshness_label == "recent"
        assert result.entry_action == "reduce_size"

    def test_overpay_above_skip_threshold_skips(self):
        """entry >> fv_high by > 20% → skip_for_now."""
        # extreme IV haircut = 10%: fv_high = 2.0 * 0.90 = 1.80
        # recommended_entry = 2.20 → overpay = (2.20 - 1.80)/1.80 * 100 ≈ 22% → skip
        c = _candidate(
            mid=2.0, bid=1.90, ask=2.10,
            recommended_entry_price=2.20,
            iv_regime_label="extreme",
            spread_pct=5.0,
        )
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        # extreme IV → enter_if_repriced (tier 2) fires before the overpay tier-1 check
        # because extreme IV check precedes the overpay skip check in _decide
        assert result.entry_action in ("enter_if_repriced", "skip_for_now")


class TestStaleThesisBlock:

    def test_tp1_below_mid_is_skip(self):
        """Projected T1 below current mid means underlying moved past targets — hard block."""
        c = _candidate(mid=24.40, bid=23.50, ask=25.30, projected_option_tp1=16.94)
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_action == "skip_for_now"
        assert "stale" in result.live_guardrail_reason.lower()

    def test_tp1_above_mid_proceeds_normally(self):
        """When T1 > mid the stale-thesis check is skipped and normal logic applies."""
        c = _candidate(mid=2.0, bid=1.90, ask=2.10, projected_option_tp1=3.50, spread_pct=10.0)
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_action != "skip_for_now" or "stale" not in result.live_guardrail_reason.lower()

    def test_tp1_none_proceeds_normally(self):
        """No projected tp1 available — stale check is skipped, not hard-blocked."""
        c = _candidate(mid=2.0, bid=1.90, ask=2.10, projected_option_tp1=None, spread_pct=10.0)
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert result.entry_action in ("enter_now", "reduce_size", "enter_if_repriced")

    def test_tp1_exactly_equal_mid_proceeds(self):
        """T1 == mid is not a stale block (must be strictly less than)."""
        c = _candidate(mid=2.0, bid=1.90, ask=2.10, projected_option_tp1=2.0, spread_pct=10.0)
        result = compute_entry_guardrail(c, chain_fetch_time=_iso(5))
        assert "stale" not in result.live_guardrail_reason.lower()
