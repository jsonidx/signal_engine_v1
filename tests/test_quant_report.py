"""
Tests for quant_report.py
"""
import sys
import os
import json
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_report import (
    SignalReportParser,
    MarketDataFetcher,
    NewsScraper,
    QuantAnalysisPipeline,
    ReportGenerator,
    dcf_valuation,
    kelly,
    calc_wacc,
    quality_score,
    score_metric,
    trail_stop,
    _fmt_eur,
    _load_cache,
    _save_cache,
    PASS_THRESHOLD,
    QUANT_REPORTS_DIR,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_REPORT = """
Step 0 complete.
Step 1 complete.
Step 2 complete.

████████████████████████████████████████████████████████████
  WEEKLY SIGNAL ENGINE — Run Date: 2026-03-23
  Portfolio: €50,000 | Equity: 65% | Crypto: 25% | Cash: 10%
████████████████████████████████████████████████████████████

████████████████████████████████████████████████████████████
  MODULE 1: EQUITY MULTI-FACTOR SCREENER
████████████████████████████████████████████████████████████

────────────────────────────────────────────────────────────
  MACRO REGIME: RISK_OFF  (score: -2)  |  Size mult: 0.4x
────────────────────────────────────────────────────────────
  Trend:-1 (SPY -1.2% vs 200MA)  Vol:+0 (VIX=26.8)  Credit:-1  YldCurve:+0

  TOP 20 RANKED STOCKS (by composite Z-score):

  Rank  Ticker     Composite  Mom12-1  Mom6-1  EpsRev    IVOL 52wkPrx MeanRev VolQual  Factors
  ────────────────────────────────────────────────────────────────────────────────────────────────────
  1     EXAS           1.004    0.580   1.255   1.424   1.829   0.772   0.007   1.835  mom12_1|mom6_1
  2     ANAB           0.707    1.326   2.275     n/a  -0.451   0.412   1.063  -0.398  mom12_1|mom6_1
  3     CFLT           0.646   -0.333   0.554   0.319   1.803   0.772   0.140   1.810  mom12_1|mom6_1

  BOTTOM 5 (AVOID / UNDERWEIGHT):

  285   ELVN          -1.172

  RECOMMENDED POSITION SIZES (Quarter-Kelly, €32,500 equity allocation):

  Ticker       Weight%         EUR   AnnVol%     Cost€
  ────────────────────────────────────────────────────
  EXAS             8.0%      1,040       3.3%      3.12
  CFLT             7.1%        920       4.0%      2.76

  Total allocated: €1,960 | Total est. costs: €5.88

════════════════════════════════════════════════════════════
  📋 PORTFOLIO SUMMARY
════════════════════════════════════════════════════════════

  NAV:             €      50,000
  Equity exposure: €       1,960  (3.9%)
  Crypto exposure: €           0  (0.0%)
  Cash:            €      48,040  (96.1%)

████████████████████████████████████████████████████████████
  OPEN POSITIONS — 2026-03-23
████████████████████████████████████████████████████████████

  Ticker     Shares   Avg Cost    Current        Value       P&L €    P&L %  Days  Status
  ─────────────────────────────────────────────────────────────────────────────────────────
  GME           715   €   20.14   €   19.85   € 14,193.95   €   -207.08    -1.4%   350  Holding
                      Stop: €19.57  |  T1: €21.56 (+8.6%)  |  T2: €22.36 (+12.6%)
  COIN           10   €  211.35   €  172.45   €  1,724.47   €   -389.03   -18.4%    77  Holding
                      Stop: €154.23  |  T1: €184.12 (+6.8%)  |  T2: €226.86 (+31.6%)

  ──────────────────────────────────────────────────────────────────────────────────────────
  TOTAL                                     € 15,918.42   €   -596.11    -3.6%

  Realized P&L:         €+1,649.84
  Unrealized P&L:       €-596.11 (-3.6%)

Step 17 complete.
Step 18 complete.

  Running latest window only: 2025-07-22 - 2026-01-13

  Window: train 2023-08-16 - 2025-07-21 | test 2025-07-22 - 2026-01-13
    Training Sharpe: 0.943 | Top weight: momentum_12_1 (34.15%)
    Test Sharpe: 4.237 | MaxDD: -1.9% | Hit: 70.8% | Turnover: 19.8%
======================================================================
  WALK-FORWARD BACKTEST REPORT
======================================================================

  Sharpe (portfolio):                    4.328
  Sharpe (SPY benchmark):                2.795

  PER-WINDOW SUMMARY
                     Window   Sharpe    MaxDD   HitRate   Turnover
  ---------------------------------------------------------------
    2025-07-22 - 2026-01-13     4.24  -1.9%   70.8%    19.8%

  PER-FACTOR IC TABLE (sorted by |mean_IC|)
  Factor                       Mean IC    Windows    Verdict
  ---------------------------------------------------------
  52wk_high_proximity          -0.0730          1  KEEP
  ivol                         -0.0612          1  KEEP
  momentum_6_1                 +0.0223          1  KEEP
  momentum_12_1                +0.0031          1  REVIEW

"""

SAMPLE_INFO = {
    'currentPrice': 98.50,
    'previousClose': 97.00,
    'regularMarketPrice': 98.50,
    'fiftyTwoWeekHigh': 130.0,
    'fiftyTwoWeekLow': 70.0,
    'beta': 1.2,
    'trailingPE': 25.0,
    'forwardPE': 20.0,
    'revenueGrowth': 0.12,
    'earningsGrowth': 0.18,
    'grossMargins': 0.45,
    'operatingMargins': 0.15,
    'profitMargins': 0.10,
    'returnOnEquity': 0.20,
    'returnOnAssets': 0.10,
    'freeCashflow': 500_000_000,
    'sharesOutstanding': 100_000_000,
    'marketCap': 9_850_000_000,
    'targetMeanPrice': 115.0,
    'targetHighPrice': 140.0,
    'targetLowPrice': 85.0,
    'recommendationMean': 2.0,
    'numberOfAnalystOpinions': 18,
    'fiftyDayAverage': 95.0,
    'twoHundredDayAverage': 88.0,
    'longName': 'Test Corp',
    'sector': 'Technology',
    'industry': 'Software',
    'longBusinessSummary': 'A technology company.',
}

SAMPLE_FINANCIALS = {
    'revenue': [2e9, 1.8e9, 1.5e9, 1.2e9, 1.0e9],
    'fcf': [500e6, 420e6, 380e6, 300e6, 250e6],
    'net_income': [200e6, 180e6, 150e6, 120e6, 100e6],
    'years': [2025, 2024, 2023, 2022, 2021],
}


# ── SignalReportParser tests ──────────────────────────────────────────────────

class TestSignalReportParser:

    def _make_parser(self, text=SAMPLE_REPORT):
        p = object.__new__(SignalReportParser)
        p.text = text
        p.path = Path('/tmp/test_report.txt')
        return p

    def test_parse_run_date(self):
        p = self._make_parser()
        assert p._parse_run_date() == '2026-03-23'

    def test_parse_run_date_fallback(self):
        p = self._make_parser("no date here")
        from datetime import datetime
        assert p._parse_run_date() == datetime.now().strftime('%Y-%m-%d')

    def test_parse_regime_label(self):
        p = self._make_parser()
        r = p._parse_regime()
        assert r['label'] == 'RISK_OFF'
        assert r['score'] == -2
        assert r['size_mult'] == 0.4

    def test_parse_regime_vix(self):
        p = self._make_parser()
        r = p._parse_regime()
        assert r['vix'] == 26.8

    def test_parse_top20_count(self):
        p = self._make_parser()
        top20 = p._parse_top20()
        assert len(top20) == 3  # sample has 3 entries

    def test_parse_top20_fields(self):
        p = self._make_parser()
        top20 = p._parse_top20()
        first = top20[0]
        assert first['rank'] == 1
        assert first['ticker'] == 'EXAS'
        assert abs(first['composite_z'] - 1.004) < 0.001

    def test_parse_position_sizes(self):
        p = self._make_parser()
        sizes = p._parse_position_sizes()
        assert len(sizes) == 2
        assert sizes[0]['ticker'] == 'EXAS'
        assert sizes[0]['weight_pct'] == 8.0
        assert sizes[0]['eur'] == 1040.0

    def test_parse_open_positions_count(self):
        p = self._make_parser()
        positions = p._parse_open_positions()
        assert len(positions) == 2

    def test_parse_open_positions_gme(self):
        p = self._make_parser()
        positions = p._parse_open_positions()
        gme = next(x for x in positions if x['ticker'] == 'GME')
        assert gme['shares'] == 715
        assert abs(gme['avg_cost_eur'] - 20.14) < 0.01
        assert abs(gme['pnl_eur'] - (-207.08)) < 0.01
        assert abs(gme['pnl_pct'] - (-1.4)) < 0.01
        assert abs(gme['stop'] - 19.57) < 0.01
        assert abs(gme['t1'] - 21.56) < 0.01
        assert abs(gme['t2'] - 22.36) < 0.01

    def test_parse_open_positions_coin(self):
        p = self._make_parser()
        positions = p._parse_open_positions()
        coin = next(x for x in positions if x['ticker'] == 'COIN')
        assert abs(coin['stop'] - 154.23) < 0.01
        assert abs(coin['pnl_pct'] - (-18.4)) < 0.01

    def test_parse_portfolio_nav(self):
        p = self._make_parser()
        port = p._parse_portfolio()
        assert port['nav'] == 50000.0

    def test_parse_portfolio_cash(self):
        p = self._make_parser()
        port = p._parse_portfolio()
        assert abs(port['cash_pct'] - 96.1) < 0.1

    def test_parse_portfolio_realized_pnl(self):
        p = self._make_parser()
        port = p._parse_portfolio()
        assert abs(port['realized_pnl'] - 1649.84) < 0.01

    def test_parse_backtest_sharpe(self):
        p = self._make_parser()
        bt = p._parse_backtest()
        assert abs(bt['sharpe'] - 4.328) < 0.001

    def test_parse_backtest_maxdd(self):
        p = self._make_parser()
        bt = p._parse_backtest()
        assert abs(bt['maxdd'] - (-1.9)) < 0.01

    def test_parse_backtest_hitrate(self):
        p = self._make_parser()
        bt = p._parse_backtest()
        assert abs(bt['hitrate'] - 70.8) < 0.01

    def test_parse_factor_ic_count(self):
        p = self._make_parser()
        fic = p._parse_factor_ic()
        assert len(fic) == 4

    def test_parse_factor_ic_fields(self):
        p = self._make_parser()
        fic = p._parse_factor_ic()
        first = fic[0]
        assert first['factor'] == '52wk_high_proximity'
        assert abs(first['mean_ic'] - (-0.073)) < 0.0001
        assert first['verdict'] == 'KEEP'

    def test_full_parse_returns_all_keys(self):
        p = self._make_parser()
        d = p.parse()
        for key in ['run_date', 'regime', 'top20', 'position_sizes',
                    'open_positions', 'portfolio', 'backtest', 'factor_ic']:
            assert key in d


# ── MarketDataFetcher tests ───────────────────────────────────────────────────

class TestMarketDataFetcher:

    def _make_fetcher(self):
        import logging
        log = logging.getLogger('test')
        return MarketDataFetcher(cache={}, logger=log)

    def test_ma_filter_no_price(self):
        f = self._make_fetcher()
        flagged, reason = f.is_ma_or_delisted('XYZ', {})
        assert flagged is True
        assert reason == 'no_price_data'

    def test_ma_filter_price_pinned(self):
        f = self._make_fetcher()
        info = {'currentPrice': 10.0, 'fiftyTwoWeekHigh': 10.20, 'fiftyTwoWeekLow': 10.00}
        flagged, reason = f.is_ma_or_delisted('XYZ', info)
        assert flagged is True
        assert reason == 'price_pinned_ma'

    def test_ma_filter_summary_keyword(self):
        f = self._make_fetcher()
        info = {
            'currentPrice': 10.0,
            'fiftyTwoWeekHigh': 20.0,
            'fiftyTwoWeekLow': 5.0,
            'longBusinessSummary': 'The company was acquired by BigCorp in 2025.',
        }
        flagged, reason = f.is_ma_or_delisted('XYZ', info)
        assert flagged is True
        assert reason == 'summary_keyword'

    def test_ma_filter_mcap_artifact(self):
        f = self._make_fetcher()
        info = {
            'currentPrice': 100.0,
            'fiftyTwoWeekHigh': 150.0,
            'fiftyTwoWeekLow': 50.0,
            'marketCap': 50,
            'longBusinessSummary': 'Normal company.',
        }
        flagged, reason = f.is_ma_or_delisted('XYZ', info)
        assert flagged is True
        assert reason == 'mcap_artifact'

    def test_ma_filter_clean_passes(self):
        f = self._make_fetcher()
        info = {
            'currentPrice': 50.0,
            'fiftyTwoWeekHigh': 80.0,
            'fiftyTwoWeekLow': 30.0,
            'marketCap': 5_000_000_000,
            'longBusinessSummary': 'A software company providing cloud solutions.',
        }
        flagged, reason = f.is_ma_or_delisted('SAAS', info)
        assert flagged is False
        assert reason == ''

    def test_cache_used_on_second_fetch(self):
        import logging
        log = logging.getLogger('test')
        cache = {'market_data': {'AAPL': {'currentPrice': 180.0, '_ticker': 'AAPL'}}}
        f = MarketDataFetcher(cache=cache, logger=log)
        result = f.fetch('AAPL')
        assert result['currentPrice'] == 180.0

    def test_fetch_all_filters_bad(self):
        import logging
        log = logging.getLogger('test')
        cache = {
            'market_data': {
                'GOOD': {'currentPrice': 50.0, 'fiftyTwoWeekHigh': 80.0,
                         'fiftyTwoWeekLow': 30.0, 'marketCap': 5e9,
                         'longBusinessSummary': 'fine', '_ticker': 'GOOD'},
                'BAD': {'_ticker': 'BAD'},
            }
        }
        f = MarketDataFetcher(cache=cache, logger=log)
        valid, filtered = f.fetch_all(['GOOD', 'BAD'])
        assert 'GOOD' in valid
        assert 'BAD' in filtered
        assert filtered['BAD'] == 'no_price_data'


# ── Quant analysis functions ──────────────────────────────────────────────────

class TestDCF:

    def test_basic_dcf(self):
        result = dcf_valuation(
            fcf_base=500e6, growth_stage1=0.10, growth_terminal=0.03,
            wacc=0.10, years_stage1=5, shares=100e6
        )
        assert result['total'] > 0
        assert result['per_share'] is not None
        assert result['per_share'] > 0

    def test_dcf_returns_sensitivity_grid(self):
        result = dcf_valuation(500e6, 0.10, 0.03, 0.10, shares=100e6)
        assert len(result['sensitivity']) == 9  # 3×3

    def test_dcf_negative_fcf_returns_none(self):
        result = dcf_valuation(-100e6, 0.10, 0.03, 0.10, shares=100e6)
        assert result['total'] is None
        assert result['per_share'] is None

    def test_dcf_wacc_below_terminal_returns_none(self):
        result = dcf_valuation(500e6, 0.10, 0.05, 0.04, shares=100e6)
        assert result['total'] is None

    def test_dcf_no_shares_per_share_none(self):
        result = dcf_valuation(500e6, 0.10, 0.03, 0.10, shares=None)
        assert result['per_share'] is None
        assert result['total'] > 0

    def test_dcf_growth_increases_value(self):
        low = dcf_valuation(500e6, 0.05, 0.02, 0.10, shares=100e6)['per_share']
        high = dcf_valuation(500e6, 0.20, 0.02, 0.10, shares=100e6)['per_share']
        assert high > low

    def test_dcf_higher_wacc_decreases_value(self):
        cheap = dcf_valuation(500e6, 0.10, 0.02, 0.08, shares=100e6)['per_share']
        expensive = dcf_valuation(500e6, 0.10, 0.02, 0.15, shares=100e6)['per_share']
        assert cheap > expensive


class TestKelly:

    def test_positive_edge(self):
        k = kelly(p=0.65, b=2.0, regime_mult=1.0)
        assert k['raw_kelly'] > 0
        assert k['verdict'] == 'BUY'
        assert not k['is_negative']

    def test_negative_edge(self):
        k = kelly(p=0.30, b=1.0)
        assert k['raw_kelly'] < 0
        assert k['is_negative']
        assert k['verdict'] == 'SELL'

    def test_regime_scales_down(self):
        k_full = kelly(p=0.65, b=2.0, regime_mult=1.0)
        k_half = kelly(p=0.65, b=2.0, regime_mult=0.5)
        assert k_full['regime_adjusted'] > k_half['regime_adjusted']

    def test_pass_when_tiny_kelly(self):
        k = kelly(p=0.51, b=1.0, regime_mult=0.01)
        assert k['verdict'] == 'PASS'

    def test_max_risk_eur(self):
        k = kelly(p=0.65, b=2.0, nav=50000, max_risk_pct=0.02)
        assert k['max_risk_eur'] == 1000.0

    def test_zero_b_returns_pass(self):
        k = kelly(p=0.65, b=0)
        assert k['verdict'] == 'PASS'


class TestCalcWACC:

    def test_wacc_basic(self):
        wacc = calc_wacc(beta=1.0, rf=0.04, erp=0.055)
        assert abs(wacc - 0.095) < 0.0001

    def test_wacc_high_beta(self):
        wacc_high = calc_wacc(beta=2.0, rf=0.04, erp=0.055)
        wacc_low = calc_wacc(beta=0.5, rf=0.04, erp=0.055)
        assert wacc_high > wacc_low

    def test_wacc_zero_beta_defaults_to_market(self):
        # beta=0 is treated as missing → defaults to 1.0 (market beta)
        wacc = calc_wacc(beta=0.0, rf=0.04, erp=0.055)
        assert abs(wacc - 0.095) < 0.0001  # 0.04 + 1.0 * 0.055


class TestQualityScore:

    def test_good_company(self):
        info = {
            'revenueGrowth': 0.20,
            'operatingMargins': 0.25,
            'returnOnEquity': 0.30,
            'earningsGrowth': 0.20,
        }
        qs = quality_score(info, wacc=0.10)
        assert qs['total'] >= 5.0
        assert 'moat_spread' in qs

    def test_poor_company(self):
        info = {
            'revenueGrowth': -0.05,
            'operatingMargins': -0.15,
            'returnOnEquity': 0.02,
            'earningsGrowth': -0.30,
        }
        qs = quality_score(info, wacc=0.10)
        assert qs['total'] < 5.0

    def test_moat_spread_positive(self):
        info = {'returnOnEquity': 0.25, 'revenueGrowth': 0.15,
                'operatingMargins': 0.20, 'earningsGrowth': 0.15}
        qs = quality_score(info, wacc=0.10)
        assert qs['moat_spread'] > 0

    def test_score_metric_thresholds(self):
        assert score_metric(-0.5, [0, 0.05, 0.15, 0.25]) == 0.0
        assert score_metric(0.03, [0, 0.05, 0.15, 0.25]) == 0.25
        assert score_metric(0.10, [0, 0.05, 0.15, 0.25]) == 0.50
        assert score_metric(0.20, [0, 0.05, 0.15, 0.25]) == 0.75
        assert score_metric(0.30, [0, 0.05, 0.15, 0.25]) == 1.0

    def test_score_metric_none_returns_neutral(self):
        assert score_metric(None, [0, 0.05, 0.15, 0.25]) == 0.25


class TestTrailStop:

    def test_trail_above_original(self):
        # current has moved up enough that trail > original
        ts = trail_stop(entry=100, current=120, stop_original=90, atr=5.0)
        assert ts == 110.0  # 120 - 2*5

    def test_trail_keeps_original_if_higher(self):
        # original stop is higher than trail would be
        ts = trail_stop(entry=100, current=102, stop_original=98, atr=5.0)
        assert ts == 98.0  # max(98, 102 - 10) = max(98, 92) = 98

    def test_zero_atr_returns_original(self):
        ts = trail_stop(entry=100, current=120, stop_original=90, atr=0)
        assert ts == 90.0


# ── Formatting helpers ────────────────────────────────────────────────────────

class TestFmtEur:

    def test_positive(self):
        assert _fmt_eur(1234.56) == '€1,234.56'

    def test_negative(self):
        assert _fmt_eur(-207.08) == '-€207.08'

    def test_zero(self):
        assert _fmt_eur(0) == '€0.00'

    def test_none(self):
        assert _fmt_eur(None) == '—'

    def test_large(self):
        assert _fmt_eur(50000.0) == '€50,000.00'


# ── Cache tests ───────────────────────────────────────────────────────────────

class TestCache:

    def test_fresh_cache_loads(self, tmp_path, monkeypatch):
        import quant_report
        cache_file = tmp_path / 'quant_report_cache.json'
        data = {
            'timestamp': __import__('datetime').datetime.now().isoformat(),
            'ttl_hours': 4,
            'market_data': {'AAPL': {'currentPrice': 180.0}},
        }
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr(quant_report, 'CACHE_FILE', cache_file)
        result = _load_cache()
        assert result.get('market_data', {}).get('AAPL', {}).get('currentPrice') == 180.0

    def test_stale_cache_returns_empty(self, tmp_path, monkeypatch):
        import quant_report
        from datetime import datetime, timedelta
        cache_file = tmp_path / 'quant_report_cache.json'
        data = {
            'timestamp': (datetime.now() - timedelta(hours=6)).isoformat(),
            'ttl_hours': 4,
            'market_data': {'AAPL': {'currentPrice': 180.0}},
        }
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr(quant_report, 'CACHE_FILE', cache_file)
        result = _load_cache()
        assert result == {}

    def test_missing_cache_returns_empty(self, tmp_path, monkeypatch):
        import quant_report
        monkeypatch.setattr(quant_report, 'CACHE_FILE', tmp_path / 'nonexistent.json')
        result = _load_cache()
        assert result == {}


# ── QuantAnalysisPipeline integration test ───────────────────────────────────

class TestQuantPipeline:

    def _make_pipeline(self):
        import logging
        regime = {'label': 'RISK_OFF', 'score': -2, 'size_mult': 0.4, 'vix': 26.8}
        log = logging.getLogger('test')
        return QuantAnalysisPipeline(nav=50000, regime=regime, logger=log)

    def test_analyze_returns_required_keys(self):
        pipeline = self._make_pipeline()
        with patch('quant_report.fetch_10yr_yield', return_value=0.043), \
             patch('quant_report.calc_atr', return_value=2.5):
            result = pipeline.analyze('TEST', SAMPLE_INFO, SAMPLE_FINANCIALS)
        for key in ['ticker', 'company', 'sector', 'price_eur', 'wacc',
                    'dcf', 'quality', 'kelly', 'rec', 'prob', 'rr']:
            assert key in result, f"Missing key: {key}"

    def test_analyze_buy_signal(self):
        pipeline = self._make_pipeline()
        # High prob, good R:R → BUY
        info = dict(SAMPLE_INFO, recommendationMean=1.5)  # strong buy
        with patch('quant_report.fetch_10yr_yield', return_value=0.043), \
             patch('quant_report.calc_atr', return_value=2.5):
            result = pipeline.analyze('TEST', info, SAMPLE_FINANCIALS)
        assert result['rec'] == 'BUY'

    def test_analyze_open_position_exit(self):
        pipeline = self._make_pipeline()
        # Deep loss position → EXIT
        open_pos = {
            'ticker': 'TEST', 'shares': 10, 'avg_cost_eur': 200.0,
            'current_eur': 95.0, 'value_eur': 950.0,
            'pnl_eur': -1050.0, 'pnl_pct': -52.5,
            'stop': 95.0, 't1': 110.0, 't2': 130.0,
        }
        info = dict(SAMPLE_INFO, recommendationMean=4.0)  # sell consensus
        with patch('quant_report.fetch_10yr_yield', return_value=0.043), \
             patch('quant_report.calc_atr', return_value=2.5):
            result = pipeline.analyze('TEST', info, SAMPLE_FINANCIALS, open_pos=open_pos)
        assert result['rec'] in ('EXIT', 'PASS', 'SELL')

    def test_analyze_trim_signal(self):
        pipeline = self._make_pipeline()
        open_pos = {
            'ticker': 'TEST', 'shares': 10, 'avg_cost_eur': 80.0,
            'current_eur': 95.0, 'value_eur': 950.0,
            'pnl_eur': 150.0, 'pnl_pct': 18.75,
            'stop': 85.0, 't1': 110.0, 't2': 130.0,
        }
        info = dict(SAMPLE_INFO, recommendationMean=2.0)
        with patch('quant_report.fetch_10yr_yield', return_value=0.043), \
             patch('quant_report.calc_atr', return_value=2.5):
            result = pipeline.analyze('TEST', info, SAMPLE_FINANCIALS, open_pos=open_pos)
        assert result['rec'] == 'TRIM'

    def test_analyze_trail_stop_computed_for_open_pos(self):
        pipeline = self._make_pipeline()
        open_pos = {
            'ticker': 'TEST', 'shares': 10, 'avg_cost_eur': 80.0,
            'current_eur': 95.0, 'value_eur': 950.0,
            'pnl_eur': 150.0, 'pnl_pct': 18.75,
            'stop': 85.0, 't1': 110.0, 't2': 130.0,
        }
        with patch('quant_report.fetch_10yr_yield', return_value=0.043), \
             patch('quant_report.calc_atr', return_value=3.0):
            result = pipeline.analyze('TEST', SAMPLE_INFO, SAMPLE_FINANCIALS, open_pos=open_pos)
        assert result['trail_stop'] is not None
        assert result['atr'] == 3.0

    def test_analyze_no_trail_stop_for_new_signal(self):
        pipeline = self._make_pipeline()
        with patch('quant_report.fetch_10yr_yield', return_value=0.043), \
             patch('quant_report.calc_atr', return_value=3.0):
            result = pipeline.analyze('TEST', SAMPLE_INFO, SAMPLE_FINANCIALS)
        assert result['trail_stop'] is None

    def test_pass_threshold_enforced(self):
        pipeline = self._make_pipeline()
        # Low prob, low R:R
        info = dict(SAMPLE_INFO, recommendationMean=3.5,
                    targetMeanPrice=99.0)  # barely above current price
        with patch('quant_report.fetch_10yr_yield', return_value=0.043), \
             patch('quant_report.calc_atr', return_value=2.5):
            result = pipeline.analyze('TEST', info, SAMPLE_FINANCIALS)
        # prob < 0.60 or RR < 2.0 → PASS
        if result['prob'] < PASS_THRESHOLD['min_prob'] or result['rr'] < PASS_THRESHOLD['min_rr']:
            assert result['rec'] == 'PASS'

    def test_wacc_incorporated(self):
        pipeline = self._make_pipeline()
        with patch('quant_report.fetch_10yr_yield', return_value=0.043), \
             patch('quant_report.calc_atr', return_value=2.5):
            result = pipeline.analyze('TEST', SAMPLE_INFO, SAMPLE_FINANCIALS)
        expected_wacc = 0.043 + 1.2 * 0.055
        assert abs(result['wacc'] - expected_wacc) < 0.001


# ── ReportGenerator tests ─────────────────────────────────────────────────────

class TestReportGenerator:

    def _make_generator(self, tmp_path, monkeypatch):
        import logging
        import quant_report
        monkeypatch.setattr(quant_report, 'QUANT_REPORTS_DIR', tmp_path)

        # Patch ReportGenerator to use tmp_path
        import quant_report as qr
        original_init = ReportGenerator.__init__

        p = object.__new__(SignalReportParser)
        p.text = SAMPLE_REPORT
        p.path = Path('/tmp/test.txt')
        parsed = p.parse()

        import logging
        log = logging.getLogger('test')
        regime = parsed['regime']
        pipeline = QuantAnalysisPipeline(nav=50000, regime=regime, logger=log)

        with patch('quant_report.fetch_10yr_yield', return_value=0.043), \
             patch('quant_report.calc_atr', return_value=2.5):
            analyses = {}
            for ticker in ['EXAS', 'ANAB', 'GME', 'COIN']:
                analyses[ticker] = pipeline.analyze(
                    ticker, SAMPLE_INFO, SAMPLE_FINANCIALS,
                    open_pos=next(
                        (x for x in parsed['open_positions'] if x['ticker'] == ticker), None
                    )
                )

        news = {'EXAS': [], 'ANAB': [], 'GME': [], 'COIN': []}
        gen = ReportGenerator(parsed, analyses, {}, news, nav=50000, logger=log)
        gen.md_path = tmp_path / 'report_test.md'
        gen.html_path = tmp_path / 'dashboard_test.html'
        gen.err_path = tmp_path / 'errors_test.log'
        return gen

    def test_markdown_generated(self, tmp_path, monkeypatch):
        gen = self._make_generator(tmp_path, monkeypatch)
        with patch('quant_report.fetch_10yr_yield', return_value=0.043):
            md_path = gen.generate_markdown()
        assert Path(md_path).exists()
        content = Path(md_path).read_text()
        assert '# QUANT TRADER REPORT' in content
        assert 'REGIME SNAPSHOT' in content
        assert 'ACTIONABLE SUMMARY' in content

    def test_markdown_contains_open_positions(self, tmp_path, monkeypatch):
        gen = self._make_generator(tmp_path, monkeypatch)
        with patch('quant_report.fetch_10yr_yield', return_value=0.043):
            md_path = gen.generate_markdown()
        content = Path(md_path).read_text()
        assert 'OPEN POSITIONS' in content
        assert 'GME' in content or 'COIN' in content

    def test_html_generated(self, tmp_path, monkeypatch):
        gen = self._make_generator(tmp_path, monkeypatch)
        with patch('quant_report.fetch_10yr_yield', return_value=0.043):
            html_path = gen.generate_html()
        assert Path(html_path).exists()
        content = Path(html_path).read_text()
        assert 'zscoreChart' in content
        assert 'icChart' in content
        assert 'chart.umd.js' in content

    def test_html_contains_regime(self, tmp_path, monkeypatch):
        gen = self._make_generator(tmp_path, monkeypatch)
        with patch('quant_report.fetch_10yr_yield', return_value=0.043):
            html_path = gen.generate_html()
        content = Path(html_path).read_text()
        assert 'RISK_OFF' in content

    def test_actionable_summary_sorted_by_priority(self, tmp_path, monkeypatch):
        gen = self._make_generator(tmp_path, monkeypatch)
        _, rows = gen._actionable_summary()
        priorities = [r['priority'] for r in rows]
        assert priorities == sorted(priorities)

    def test_ma_filter_in_markdown(self, tmp_path, monkeypatch):
        import logging
        p = object.__new__(SignalReportParser)
        p.text = SAMPLE_REPORT
        p.path = Path('/tmp/test.txt')
        parsed = p.parse()
        log = logging.getLogger('test')
        gen = ReportGenerator(
            parsed, {}, {'BADTICKER': 'price_pinned_ma'}, {}, nav=50000, logger=log
        )
        gen.md_path = tmp_path / 'report_test.md'
        gen.html_path = tmp_path / 'dashboard_test.html'
        gen.err_path = tmp_path / 'errors_test.log'
        md_path = gen.generate_markdown()
        content = Path(md_path).read_text()
        assert 'BADTICKER' in content
        assert 'price_pinned_ma' in content


# ── NewsScraper tests ─────────────────────────────────────────────────────────

class TestNewsScraper:

    def _make_scraper(self):
        import logging
        log = logging.getLogger('test')
        return NewsScraper(cache={}, logger=log)

    def test_sentiment_bullish(self):
        s = self._make_scraper()
        score = s._sentiment_score('Company beat earnings and raised guidance on record revenue')
        assert score > 0

    def test_sentiment_bearish(self):
        s = self._make_scraper()
        score = s._sentiment_score('Company missed estimates and lowered guidance amid investigation')
        assert score < 0

    def test_sentiment_neutral(self):
        s = self._make_scraper()
        score = s._sentiment_score('Company releases quarterly results')
        assert score == 0.0

    def test_sentiment_clamped(self):
        s = self._make_scraper()
        score = s._sentiment_score(
            'beat raised guidance upgrade acquisition buyback record revenue partnership approval accelerat'
        )
        assert -1.0 <= score <= 1.0

    def test_cache_returns_cached_articles(self):
        import logging
        log = logging.getLogger('test')
        cached_articles = [{'title': 'Cached news', 'source': 'test', 'published': '2026-03-23',
                             'sentiment_score': 0.5, 'is_material': False, 'url': ''}]
        s = NewsScraper(cache={'news': {'AAPL': cached_articles}}, logger=log)
        result = s.scrape('AAPL', 'Apple Inc')
        assert result == cached_articles

    def test_empty_news_on_failure(self):
        s = self._make_scraper()
        # feedparser is available but feeds will fail/return empty in test env
        result = s.scrape('FAKEXYZ999', 'Fake Corp That Does Not Exist')
        assert isinstance(result, list)
