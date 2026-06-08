"""
Tests for utils/ibkr_options.py  (TRD-021)

All tests use mocked data — no live IBKR connection or yfinance network call required.
"""

import math
from datetime import date, datetime, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from utils.ibkr_options import (
    IBKRAuthError,
    IBKRNoDataError,
    OptionChainResult,
    OptionContract,
    _approx_delta,
    _get_underlying_price_yf,
    _norm_cdf,
    _normalize_yf_row,
    _yfinance_chain,
    get_option_chain,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_row(
    strike: float,
    right: str,
    bid: float = 1.0,
    ask: float = 1.10,
    last: float = 1.05,
    iv: float = 0.40,
    oi: int = 200,
    volume: int = 50,
    itm: bool = False,
) -> MagicMock:
    row = MagicMock()
    row.get = lambda key, default=None: {
        "bid": bid,
        "ask": ask,
        "lastPrice": last,
        "impliedVolatility": iv,
        "openInterest": oi,
        "volume": volume,
        "inTheMoney": itm,
    }.get(key, default)
    row.__getitem__ = lambda self, key: {
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "lastPrice": last,
        "impliedVolatility": iv,
        "openInterest": oi,
        "volume": volume,
        "inTheMoney": itm,
    }[key]
    return row


def _future_expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════════════════════
# OptionContract property tests
# ══════════════════════════════════════════════════════════════════════════════

class TestOptionContractProperties:
    def test_spread_computed(self):
        c = OptionContract(
            ticker="AAPL", expiry="2026-06-20", strike=200.0, right="C", dte=21,
            bid=2.00, ask=2.20,
        )
        assert c.spread == pytest.approx(0.20, abs=1e-4)

    def test_spread_pct_computed(self):
        c = OptionContract(
            ticker="AAPL", expiry="2026-06-20", strike=200.0, right="C", dte=21,
            bid=2.00, ask=2.20,
        )
        # spread = 0.20, mid = 2.10 → spread_pct = 0.20 / 2.10 * 100 ≈ 9.52
        assert c.spread_pct == pytest.approx(0.20 / 2.10 * 100, abs=0.1)

    def test_spread_none_when_no_bid(self):
        c = OptionContract(ticker="AAPL", expiry="2026-06-20", strike=200.0, right="C", dte=21, ask=2.20)
        assert c.spread is None
        assert c.spread_pct is None

    def test_spread_pct_none_when_mid_zero(self):
        c = OptionContract(
            ticker="AAPL", expiry="2026-06-20", strike=200.0, right="C", dte=21,
            bid=0.0, ask=0.0,
        )
        assert c.spread_pct is None


# ══════════════════════════════════════════════════════════════════════════════
# Black-Scholes delta approximation
# ══════════════════════════════════════════════════════════════════════════════

class TestApproxDelta:
    def test_atm_call_near_half(self):
        delta = _approx_delta(100.0, 100.0, 0.30, 30, "C")
        assert delta is not None
        assert 0.45 < delta < 0.60   # ATM call ≈ 0.50

    def test_atm_put_near_neg_half(self):
        delta = _approx_delta(100.0, 100.0, 0.30, 30, "P")
        assert delta is not None
        assert -0.60 < delta < -0.40   # ATM put ≈ -0.50

    def test_deep_itm_call_near_one(self):
        delta = _approx_delta(150.0, 100.0, 0.30, 30, "C")
        assert delta is not None
        assert delta > 0.85

    def test_deep_otm_call_near_zero(self):
        delta = _approx_delta(100.0, 200.0, 0.30, 30, "C")
        assert delta is not None
        assert delta < 0.05

    def test_call_put_sum_near_one(self):
        S, K, iv, dte = 100.0, 105.0, 0.35, 45
        c = _approx_delta(S, K, iv, dte, "C")
        p = _approx_delta(S, K, iv, dte, "P")
        assert c is not None and p is not None
        # call_delta = N(d1), put_delta = N(d1) - 1 → call_delta - put_delta = 1
        assert abs(c - p - 1.0) < 0.05

    def test_zero_dte_returns_none(self):
        assert _approx_delta(100.0, 100.0, 0.30, 0, "C") is None

    def test_zero_iv_returns_none(self):
        assert _approx_delta(100.0, 100.0, 0.0, 30, "C") is None


# ══════════════════════════════════════════════════════════════════════════════
# yfinance row normalization
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeYfRow:
    def _base_row(self, **overrides):
        defaults = dict(
            strike=150.0, bid=2.0, ask=2.20, lastPrice=2.10,
            impliedVolatility=0.40, openInterest=500, volume=120, inTheMoney=False,
        )
        defaults.update(overrides)
        row = MagicMock()
        d = defaults
        row.get = lambda k, default=None: d.get(k, default)
        row.__getitem__ = lambda self, k: d[k]
        return row

    def test_basic_fields(self):
        row = self._base_row()
        c = _normalize_yf_row(row, "AAPL", _future_expiry(21), 21, "C", 148.0)
        assert c.ticker == "AAPL"
        assert c.strike == 150.0
        assert c.right == "C"
        assert c.bid == pytest.approx(2.0)
        assert c.ask == pytest.approx(2.20)
        assert c.mid == pytest.approx(2.10)
        assert c.open_interest == 500
        assert c.volume == 120
        assert c.source == "yfinance"

    def test_delta_computed_from_iv(self):
        row = self._base_row(impliedVolatility=0.40)
        c = _normalize_yf_row(row, "AAPL", _future_expiry(30), 30, "C", 148.0)
        assert c.delta is not None
        assert 0 < c.delta < 1.0

    def test_missing_bid_does_not_crash(self):
        row = self._base_row(bid=None, ask=2.20)
        c = _normalize_yf_row(row, "AAPL", _future_expiry(21), 21, "C", 148.0)
        assert c.bid is None
        # mid should fall back to ask
        assert c.mid == pytest.approx(2.20)

    def test_zero_iv_yields_no_delta(self):
        row = self._base_row(impliedVolatility=0.0)
        c = _normalize_yf_row(row, "AAPL", _future_expiry(21), 21, "C", 148.0)
        assert c.delta is None

    def test_nan_iv_yields_no_delta(self):
        row = self._base_row(impliedVolatility=float("nan"))
        c = _normalize_yf_row(row, "AAPL", _future_expiry(21), 21, "C", 148.0)
        assert c.delta is None

    def test_missing_greeks_do_not_crash(self):
        row = self._base_row(impliedVolatility=None)
        c = _normalize_yf_row(row, "AAPL", _future_expiry(21), 21, "C", 148.0)
        assert c.delta is None
        assert c.implied_vol is None

    def test_missing_oi_does_not_crash(self):
        row = self._base_row(openInterest=None)
        c = _normalize_yf_row(row, "AAPL", _future_expiry(21), 21, "C", 148.0)
        assert c.open_interest is None

    def test_zero_oi_is_valid(self):
        row = self._base_row(openInterest=0)
        c = _normalize_yf_row(row, "AAPL", _future_expiry(21), 21, "C", 148.0)
        assert c.open_interest == 0

    def test_put_right(self):
        row = self._base_row()
        c = _normalize_yf_row(row, "AAPL", _future_expiry(21), 21, "P", 148.0)
        assert c.right == "P"
        assert c.delta is not None
        assert c.delta < 0   # put delta is negative

    def test_spread_pct_property(self):
        row = self._base_row(bid=2.0, ask=2.20)
        c = _normalize_yf_row(row, "AAPL", _future_expiry(21), 21, "C", 148.0)
        # spread = 0.20, mid = 2.10
        assert c.spread_pct == pytest.approx(0.20 / 2.10 * 100, abs=0.2)


# ══════════════════════════════════════════════════════════════════════════════
# yfinance chain fetch (mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestYFinanceChain:
    def _mock_ticker(self, price=150.0, expirations=None, calls_df=None, puts_df=None):
        import pandas as pd

        if expirations is None:
            expirations = [
                _future_expiry(21),
                _future_expiry(45),
            ]

        if calls_df is None:
            calls_df = pd.DataFrame([
                {"strike": 145.0, "bid": 2.0, "ask": 2.20, "lastPrice": 2.10,
                 "impliedVolatility": 0.38, "openInterest": 500, "volume": 100, "inTheMoney": False},
                {"strike": 150.0, "bid": 4.0, "ask": 4.30, "lastPrice": 4.15,
                 "impliedVolatility": 0.35, "openInterest": 800, "volume": 200, "inTheMoney": False},
                {"strike": 155.0, "bid": 1.0, "ask": 1.15, "lastPrice": 1.07,
                 "impliedVolatility": 0.42, "openInterest": 300, "volume": 50, "inTheMoney": False},
            ])

        if puts_df is None:
            puts_df = pd.DataFrame([
                {"strike": 145.0, "bid": 1.5, "ask": 1.70, "lastPrice": 1.60,
                 "impliedVolatility": 0.42, "openInterest": 400, "volume": 80, "inTheMoney": False},
                {"strike": 150.0, "bid": 3.5, "ask": 3.80, "lastPrice": 3.65,
                 "impliedVolatility": 0.38, "openInterest": 600, "volume": 150, "inTheMoney": True},
            ])

        hist_df = MagicMock()
        hist_df.empty = False
        hist_df.__len__ = lambda self: 5
        hist_df.__getitem__ = lambda self, key: MagicMock(
            iloc=MagicMock(__getitem__=lambda s, i: price)
        )

        chain = MagicMock()
        chain.calls = calls_df
        chain.puts = puts_df

        t = MagicMock()
        t.history.return_value = hist_df
        t.options = expirations
        t.option_chain.return_value = chain
        return t

    @patch("utils.ibkr_options.yf.Ticker")
    def test_returns_contracts_for_valid_ticker(self, mock_yf_ticker):
        mock_yf_ticker.return_value = self._mock_ticker()
        result = _yfinance_chain("AAPL", min_dte=7, max_dte=90, max_expiries=2)
        assert result.error is None or result.contracts
        assert len(result.contracts) > 0
        assert result.source == "yfinance"
        assert result.underlying_price == pytest.approx(150.0, abs=1.0)

    @patch("utils.ibkr_options.yf.Ticker")
    def test_contracts_have_correct_rights(self, mock_ticker):
        mock_ticker.return_value = self._mock_ticker()
        result = _yfinance_chain("AAPL", min_dte=7, max_dte=90)
        rights = {c.right for c in result.contracts}
        assert "C" in rights
        assert "P" in rights

    @patch("utils.ibkr_options.yf.Ticker")
    def test_no_history_returns_error(self, mock_ticker):
        t = MagicMock()
        hist = MagicMock()
        hist.empty = True
        t.history.return_value = hist
        mock_ticker.return_value = t
        result = _yfinance_chain("BADTICKER")
        assert result.error is not None
        assert result.contracts == []

    @patch("utils.ibkr_options.yf.Ticker")
    def test_no_options_returns_error(self, mock_ticker):
        t = self._mock_ticker()
        t.options = []
        mock_ticker.return_value = t
        result = _yfinance_chain("AAPL")
        assert result.error is not None
        assert result.contracts == []

    @patch("utils.ibkr_options.yf.Ticker")
    def test_dte_outside_range_returns_error(self, mock_ticker):
        t = self._mock_ticker(expirations=[_future_expiry(200)])
        mock_ticker.return_value = t
        result = _yfinance_chain("AAPL", min_dte=7, max_dte=90)
        assert result.error is not None

    @patch("utils.ibkr_options.yf.Ticker")
    def test_missing_greeks_do_not_crash(self, mock_ticker):
        import pandas as pd
        calls_no_iv = pd.DataFrame([
            {"strike": 150.0, "bid": 4.0, "ask": 4.30, "lastPrice": 4.15,
             "impliedVolatility": None, "openInterest": 500, "volume": 100, "inTheMoney": False},
        ])
        t = self._mock_ticker(calls_df=calls_no_iv)
        mock_ticker.return_value = t
        result = _yfinance_chain("AAPL", min_dte=7, max_dte=90)
        # Should not raise; contracts with missing IV have delta=None
        calls_no_delta = [c for c in result.contracts if c.right == "C" and c.delta is None]
        assert len(calls_no_delta) > 0


# ══════════════════════════════════════════════════════════════════════════════
# IBKR tick mapping — normalize_ticker
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeIBKRTicker:
    """
    Explicit verification that IBKR tick fields map to the correct OptionContract
    fields.  These are pure unit tests against the static method; no IBKR session
    needed.

    IBKR generic tick reference (as requested via "100,101,106"):
      100 → Ticker.optVolume        = option daily trading volume
      101 → Ticker.optOpenInterest  = option open interest
      106 → Ticker.modelGreeks      = IV + delta / gamma / theta / vega
    """

    def _make_td(
        self,
        bid=2.00, ask=2.20, last=2.10,
        opt_volume=350,       # tick 100
        opt_open_interest=1500,  # tick 101
        delta=0.42, gamma=0.05, theta=-0.03, vega=0.15, iv=0.38,
    ) -> MagicMock:
        """Build a minimal ib_insync Ticker mock with the correct field names."""
        td = MagicMock()
        td.bid   = bid
        td.ask   = ask
        td.last  = last
        td.volume = float("nan")      # underlying volume — should NOT be used for options
        td.optVolume        = opt_volume       # tick 100
        td.optOpenInterest  = opt_open_interest  # tick 101

        greeks = MagicMock()
        greeks.delta      = delta
        greeks.gamma      = gamma
        greeks.theta      = theta
        greeks.vega       = vega
        greeks.impliedVol = iv
        td.modelGreeks = greeks
        td.bidGreeks   = None
        td.askGreeks   = None
        return td

    def _call(self, td, **kwargs) -> "OptionContract":
        from utils.ibkr_options import IBKROptionsAdapter
        expiry = _future_expiry(kwargs.get("dte", 21))
        return IBKROptionsAdapter._normalize_ticker(
            td,
            ticker=kwargs.get("ticker", "AAPL"),
            exp_iso=expiry,
            dte=kwargs.get("dte", 21),
            strike=kwargs.get("strike", 150.0),
            right=kwargs.get("right", "C"),
            underlying_price=kwargs.get("underlying_price", 148.0),
        )

    def test_opt_open_interest_maps_to_open_interest(self):
        """td.optOpenInterest (tick 101) must populate contract.open_interest."""
        td = self._make_td(opt_open_interest=2400)
        c = self._call(td)
        assert c.open_interest == 2400, (
            "open_interest should come from td.optOpenInterest (tick 101), "
            "not from td.optVolume (tick 100)"
        )

    def test_opt_volume_maps_to_volume(self):
        """td.optVolume (tick 100) must populate contract.volume."""
        td = self._make_td(opt_volume=750)
        c = self._call(td)
        assert c.volume == 750, (
            "volume should come from td.optVolume (tick 100)"
        )

    def test_underlying_volume_not_used(self):
        """td.volume (underlying daily volume) must NOT be used for option OI or volume."""
        td = self._make_td(opt_volume=200, opt_open_interest=1000)
        td.volume = 99_000_000   # large stock volume that should not bleed into option fields
        c = self._call(td)
        assert c.volume        == 200,   "option volume must not be the underlying's td.volume"
        assert c.open_interest == 1000,  "OI must not be the underlying's td.volume"

    def test_nan_opt_open_interest_yields_none(self):
        """NaN optOpenInterest (IBKR sentinel for unavailable) must yield None."""
        td = self._make_td(opt_open_interest=float("nan"))
        c = self._call(td)
        assert c.open_interest is None

    def test_nan_opt_volume_yields_none(self):
        """NaN optVolume (IBKR sentinel for unavailable) must yield None."""
        td = self._make_td(opt_volume=float("nan"))
        c = self._call(td)
        assert c.volume is None

    def test_none_opt_open_interest_yields_none(self):
        td = self._make_td(opt_open_interest=None)
        c = self._call(td)
        assert c.open_interest is None

    def test_none_opt_volume_yields_none(self):
        td = self._make_td(opt_volume=None)
        c = self._call(td)
        assert c.volume is None

    def test_missing_opt_fields_do_not_crash(self):
        td = self._make_td()
        del td.optVolume
        del td.optOpenInterest
        del td.modelGreeks
        del td.bidGreeks
        del td.askGreeks

        c = self._call(td)
        assert c.volume is None
        assert c.open_interest is None
        assert c.delta is None

    def test_zero_open_interest_is_valid(self):
        """OI = 0 is a valid value (no open contracts), must not be treated as None."""
        td = self._make_td(opt_open_interest=0)
        c = self._call(td)
        assert c.open_interest == 0

    def test_greeks_populated_from_model_greeks(self):
        td = self._make_td(delta=0.42, gamma=0.05, theta=-0.03, vega=0.15, iv=0.38)
        c = self._call(td)
        assert c.delta        == pytest.approx(0.42)
        assert c.gamma        == pytest.approx(0.05)
        assert c.theta        == pytest.approx(-0.03)
        assert c.vega         == pytest.approx(0.15)
        assert c.implied_vol  == pytest.approx(0.38)

    def test_no_greeks_all_none(self):
        td = self._make_td()
        td.modelGreeks = None
        td.bidGreeks   = None
        td.askGreeks   = None
        c = self._call(td)
        assert c.delta       is None
        assert c.gamma       is None
        assert c.theta       is None
        assert c.vega        is None
        assert c.implied_vol is None

    def test_bid_ask_mid_computed(self):
        td = self._make_td(bid=3.00, ask=3.40)
        c = self._call(td)
        assert c.bid == pytest.approx(3.00)
        assert c.ask == pytest.approx(3.40)
        assert c.mid == pytest.approx(3.20)

    def test_zero_bid_accepted(self):
        """Bid of 0 is valid (no bids) and should produce a mid = ask / 2."""
        td = self._make_td(bid=0.0, ask=1.50)
        c = self._call(td)
        assert c.bid == pytest.approx(0.0)
        assert c.mid == pytest.approx(0.75)

    def test_tick_request_includes_101(self):
        """
        Smoke-test: ensure the tick string used in reqMktData includes '101'
        (option open interest).  We verify this via the source constant rather
        than a network call.
        """
        import utils.ibkr_options as mod
        import inspect
        src = inspect.getsource(mod.IBKROptionsAdapter.get_chain)
        assert "101" in src, (
            "reqMktData tick string must include '101' (option open interest); "
            "found only: " + repr([line.strip() for line in src.splitlines()
                                    if "reqMktData" in line])
        )


# ══════════════════════════════════════════════════════════════════════════════
# IBKROptionsAdapter  (mocked ib_insync)
# ══════════════════════════════════════════════════════════════════════════════

class TestIBKRAuthError:
    def test_connect_raises_when_ib_unavailable(self):
        """When ib_insync is not installed the adapter raises IBKRAuthError."""
        from utils import ibkr_options as mod
        original = mod._IB_AVAILABLE
        try:
            mod._IB_AVAILABLE = False
            from utils.ibkr_options import IBKROptionsAdapter
            adapter = IBKROptionsAdapter()
            with pytest.raises(IBKRAuthError, match="ib_insync"):
                adapter.connect()
        finally:
            mod._IB_AVAILABLE = original

    def test_get_chain_without_connect_raises(self):
        from utils.ibkr_options import IBKROptionsAdapter
        adapter = IBKROptionsAdapter()
        # _ib is None → should raise IBKRAuthError
        with pytest.raises(IBKRAuthError):
            adapter.get_chain("AAPL")


# ══════════════════════════════════════════════════════════════════════════════
# get_option_chain public API
# ══════════════════════════════════════════════════════════════════════════════

class TestGetOptionChain:
    @patch("utils.ibkr_options.yf.Ticker")
    def test_force_yfinance_skips_ibkr(self, mock_ticker):
        """force_yfinance=True should bypass IBKR entirely."""
        import pandas as pd
        calls_df = pd.DataFrame([
            {"strike": 150.0, "bid": 4.0, "ask": 4.30, "lastPrice": 4.15,
             "impliedVolatility": 0.35, "openInterest": 800, "volume": 200, "inTheMoney": False},
        ])
        puts_df = pd.DataFrame([
            {"strike": 145.0, "bid": 1.5, "ask": 1.70, "lastPrice": 1.60,
             "impliedVolatility": 0.42, "openInterest": 400, "volume": 80, "inTheMoney": False},
        ])
        chain = MagicMock()
        chain.calls = calls_df
        chain.puts = puts_df

        hist_df = MagicMock()
        hist_df.empty = False
        hist_df.__getitem__ = lambda self, key: MagicMock(
            iloc=MagicMock(__getitem__=lambda s, i: 150.0)
        )

        t = MagicMock()
        t.history.return_value = hist_df
        t.options = [_future_expiry(21)]
        t.option_chain.return_value = chain
        mock_ticker.return_value = t

        result = get_option_chain("AAPL", force_yfinance=True)
        assert result.source == "yfinance"
        assert len(result.contracts) > 0


# ══════════════════════════════════════════════════════════════════════════════
# TRD-055: No IBKR snapshot usage for underlying price
# ══════════════════════════════════════════════════════════════════════════════

class TestNoIBKRSnapshot:
    """Verify that the IBKR adapter never uses reqMktData with snapshot=True."""

    def test_get_chain_source_contains_no_snapshot_true(self):
        """
        The adapter must not call reqMktData with snapshot mode enabled.
        Checked via source inspection (non-comment lines only) so this
        always catches a regression even without a live IBKR connection.
        """
        import inspect
        import utils.ibkr_options as mod

        src = inspect.getsource(mod.IBKROptionsAdapter.get_chain)
        bad_lines = [
            l for l in src.splitlines()
            if not l.lstrip().startswith("#") and "snapshot=True" in l
        ]
        assert not bad_lines, (
            "IBKROptionsAdapter.get_chain must not use reqMktData with "
            "snapshot=True (incurs per-snapshot IBKR entitlement cost).\n"
            "Offending lines:\n" + "\n".join(bad_lines)
        )

    def test_no_snapshot_true_in_executable_module_lines(self):
        """Module-wide guard: no executable code line may use snapshot=True."""
        import inspect
        import utils.ibkr_options as mod

        src = inspect.getsource(mod)
        bad_lines = [
            l for l in src.splitlines()
            if not l.lstrip().startswith("#") and "snapshot=True" in l
        ]
        assert not bad_lines, (
            "snapshot=True found in executable code in utils/ibkr_options.py:\n"
            + "\n".join(bad_lines)
        )

    def test_option_contract_reqMktData_still_present(self):
        """
        The live option-contract market-data path (reqMktData with tick
        string '100,101,106') must remain intact.
        """
        import inspect
        import utils.ibkr_options as mod

        src = inspect.getsource(mod.IBKROptionsAdapter.get_chain)
        assert "100,101,106" in src, (
            "reqMktData tick string '100,101,106' must be preserved for "
            "option-contract market data."
        )
        assert "False, False" in src or "False,False" in src or "snapshot=False" not in src, (
            "Option-contract reqMktData call should not use snapshot=True."
        )


# ══════════════════════════════════════════════════════════════════════════════
# TRD-055: _get_underlying_price_yf helper
# ══════════════════════════════════════════════════════════════════════════════

class TestGetUnderlyingPriceYf:
    """Unit tests for the yfinance underlying-price helper."""

    @patch("utils.ibkr_options.yf.Ticker")
    def test_returns_last_close_when_history_available(self, mock_ticker):
        import pandas as pd
        hist = pd.DataFrame({"Close": [148.0, 149.5, 150.0]})
        t = MagicMock()
        t.history.return_value = hist
        mock_ticker.return_value = t

        price = _get_underlying_price_yf("AAPL")
        assert price == pytest.approx(150.0)
        t.history.assert_called_once_with(period="5d")

    @patch("utils.ibkr_options.yf.Ticker")
    def test_returns_none_when_history_empty(self, mock_ticker):
        import pandas as pd
        t = MagicMock()
        t.history.return_value = pd.DataFrame()
        mock_ticker.return_value = t

        price = _get_underlying_price_yf("AAPL")
        assert price is None

    @patch("utils.ibkr_options.yf.Ticker")
    def test_returns_none_when_yfinance_raises(self, mock_ticker):
        mock_ticker.side_effect = RuntimeError("network error")
        price = _get_underlying_price_yf("AAPL")
        assert price is None

    @patch("utils.ibkr_options.yf.Ticker")
    def test_returns_none_when_price_is_zero(self, mock_ticker):
        import pandas as pd
        hist = pd.DataFrame({"Close": [0.0]})
        t = MagicMock()
        t.history.return_value = hist
        mock_ticker.return_value = t

        price = _get_underlying_price_yf("AAPL")
        assert price is None

    @patch("utils.ibkr_options.yf.Ticker")
    def test_returns_none_when_price_is_negative(self, mock_ticker):
        import pandas as pd
        hist = pd.DataFrame({"Close": [-1.0]})
        t = MagicMock()
        t.history.return_value = hist
        mock_ticker.return_value = t

        price = _get_underlying_price_yf("AAPL")
        assert price is None


# ══════════════════════════════════════════════════════════════════════════════
# TRD-055: Underlying-price fallback in the IBKR adapter chain flow
# ══════════════════════════════════════════════════════════════════════════════

class TestIBKRAdapterUnderlyingFallback:
    """
    Verify that when _get_underlying_price_yf returns None the adapter
    degrades safely: strike selection falls back to the middle window and
    the chain result still returns without crashing.
    """

    def _make_mock_ib(self, strikes, expirations, qualify_ok=True):
        """Build a minimal ib_insync mock that covers the adapter's call sequence."""
        ib = MagicMock()
        ib.isConnected.return_value = True

        # qualifyContracts returns the passed contract (first call = stock, rest = options)
        ib.qualifyContracts.side_effect = lambda c: [c] if qualify_ok else []

        # reqSecDefOptParams returns one chain-params object
        chain_params = MagicMock()
        chain_params.expirations = expirations
        chain_params.strikes = strikes
        ib.reqSecDefOptParams.return_value = [chain_params]

        # reqMktData returns an empty ticker (no Greeks, no quotes) — simulates
        # a fresh connection with no subscriptions
        td = MagicMock()
        td.bid = None
        td.ask = None
        td.last = None
        td.optVolume = None
        td.optOpenInterest = None
        td.modelGreeks = None
        td.bidGreeks = None
        td.askGreeks = None
        ib.reqMktData.return_value = td
        ib.ticker.return_value = td
        ib.sleep.return_value = None
        return ib

    @patch("utils.ibkr_options._get_underlying_price_yf")
    def test_none_underlying_price_does_not_crash(self, mock_price):
        """When yfinance returns None the chain fetch must not raise."""
        mock_price.return_value = None

        from utils.ibkr_options import IBKROptionsAdapter
        adapter = IBKROptionsAdapter()

        from datetime import date, timedelta
        exp = (date.today() + timedelta(days=30)).strftime("%Y%m%d")
        adapter._ib = self._make_mock_ib(
            strikes=[140.0, 145.0, 150.0, 155.0, 160.0],
            expirations=[exp],
        )

        result = adapter.get_chain("AAPL", max_expiries=1, strikes_around_atm=3)
        assert result.underlying_price is None
        assert result.error is None   # graceful, not an error state
        assert isinstance(result.contracts, list)

    @patch("utils.ibkr_options._get_underlying_price_yf")
    def test_valid_underlying_price_used_for_strike_selection(self, mock_price):
        """When yfinance returns a price the adapter uses it to select ATM strikes."""
        mock_price.return_value = 150.0

        from utils.ibkr_options import IBKROptionsAdapter
        adapter = IBKROptionsAdapter()

        from datetime import date, timedelta
        exp = (date.today() + timedelta(days=30)).strftime("%Y%m%d")
        strikes = [100.0, 120.0, 140.0, 145.0, 148.0, 150.0, 152.0, 155.0, 170.0, 200.0]
        adapter._ib = self._make_mock_ib(strikes=strikes, expirations=[exp])

        result = adapter.get_chain("AAPL", max_expiries=1, strikes_around_atm=3)
        assert result.underlying_price == pytest.approx(150.0)
        # All selected strikes should be near 150.0 (within the ATM window)
        for c in result.contracts:
            assert abs(c.strike - 150.0) <= 30.0, f"Strike {c.strike} too far from ATM"

    @patch("utils.ibkr_options._get_underlying_price_yf")
    def test_adapter_does_not_call_reqMktData_with_snapshot(self, mock_price):
        """reqMktData must never be called with snapshot=True during get_chain."""
        mock_price.return_value = 150.0

        from utils.ibkr_options import IBKROptionsAdapter
        adapter = IBKROptionsAdapter()

        from datetime import date, timedelta
        exp = (date.today() + timedelta(days=30)).strftime("%Y%m%d")
        adapter._ib = self._make_mock_ib(
            strikes=[148.0, 150.0, 152.0],
            expirations=[exp],
        )

        adapter.get_chain("AAPL", max_expiries=1, strikes_around_atm=2)

        for call in adapter._ib.reqMktData.call_args_list:
            kwargs = call.kwargs
            args = call.args
            # snapshot could be passed as positional arg[3] or as keyword
            snapshot_kwarg = kwargs.get("snapshot", False)
            snapshot_pos = args[3] if len(args) > 3 else False
            assert not snapshot_kwarg, f"reqMktData called with snapshot=True: {call}"
            assert not snapshot_pos, f"reqMktData called with positional snapshot=True: {call}"
