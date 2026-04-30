#!/usr/bin/env python3
"""
================================================================================
QUANT REPORT v1.0 — Signal Engine v1 Integration
================================================================================
Reads the latest signal report, enriches with live market data + news,
runs a full quantitative analysis pipeline, and outputs:
  1. quant_reports/report_YYYYMMDD.md   — actionable buy/sell/trim decisions
  2. quant_reports/dashboard_YYYYMMDD.html — interactive HTML snapshot
  3. quant_reports/quant_report_cache.json — 4h yfinance + news cache

Usage:
  python3 quant_report.py
  python3 quant_report.py --report path/to/report.txt
  python3 quant_report.py --nav 75000
================================================================================
"""

import os
import re
import sys
import json
import logging
import warnings
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
SIGNAL_REPORTS_DIR = BASE_DIR / "signal_reports"
QUANT_REPORTS_DIR = BASE_DIR / "quant_reports"
CACHE_FILE = QUANT_REPORTS_DIR / "quant_report_cache.json"
CACHE_TTL_HOURS = 4

# ── Trading constants ──────────────────────────────────────────────────────────
PASS_THRESHOLD = {'min_prob': 0.60, 'min_rr': 2.0}
REGIME_VIX_MULT = {'VIX<15': 1.0, 'VIX15-25': 0.75, 'VIX25-35': 0.50, 'VIX>35': 0.25}

EXA_API_KEY: Optional[str] = os.getenv("EXA_API_KEY")

BULLISH_KW = ['beat', 'raised guidance', 'upgrade', 'acquisition', 'buyback',
              'record revenue', 'partnership', 'approval', 'accelerat']
BEARISH_KW = ['miss', 'lowered guidance', 'downgrade', 'lawsuit', 'investigation',
              'recall', 'layoff', 'delay', 'regulatory', 'loss widened']
MATERIAL_RE = re.compile(
    r'earnings|guidance|acquisition|merger|FDA|SEC|lawsuit|investigation|8-K|insider',
    re.IGNORECASE
)

# ── HTML Jinja2 template ───────────────────────────────────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Quant Report — {{ date }}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  :root {
    --bg: #0f1117; --bg2: #1a1d27; --bg3: #22263a;
    --border: #2e3250; --text: #e8eaf6; --muted: #8b91b8;
    --green: #22c55e; --amber: #f59e0b; --red: #ef4444;
    --blue: #3b82f6; --gray: #6b7280; --purple: #a855f7;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Consolas', monospace; font-size: 13px; padding: 16px; }
  h1 { font-size: 18px; font-weight: 700; letter-spacing: 1px; }
  h2 { font-size: 14px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin: 24px 0 10px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
  h3 { font-size: 13px; color: var(--muted); margin: 14px 0 6px; }
  .header { display: flex; align-items: center; gap: 16px; padding: 12px 16px; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  .header h1 { flex: 1; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
  .badge-risk_on { background: rgba(34,197,94,0.2); color: var(--green); border: 1px solid var(--green); }
  .badge-transitional { background: rgba(245,158,11,0.2); color: var(--amber); border: 1px solid var(--amber); }
  .badge-risk_off { background: rgba(239,68,68,0.2); color: var(--red); border: 1px solid var(--red); }
  .badge-trim { background: rgba(245,158,11,0.25); color: var(--amber); border: 1px solid var(--amber); }
  .badge-hold { background: rgba(59,130,246,0.25); color: var(--blue); border: 1px solid var(--blue); }
  .badge-exit { background: rgba(239,68,68,0.25); color: var(--red); border: 1px solid var(--red); }
  .badge-add { background: rgba(34,197,94,0.25); color: var(--green); border: 1px solid var(--green); }
  .badge-buy { background: rgba(34,197,94,0.25); color: var(--green); border: 1px solid var(--green); }
  .badge-pass { background: rgba(107,114,128,0.25); color: var(--gray); border: 1px solid var(--gray); }
  .badge-sell { background: rgba(239,68,68,0.25); color: var(--red); border: 1px solid var(--red); }
  .badge-material { background: rgba(168,85,247,0.25); color: var(--purple); border: 1px solid var(--purple); font-size: 10px; }
  .metrics { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .metric-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
  .metric-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
  .metric-value { font-size: 20px; font-weight: 700; }
  .metric-value.green { color: var(--green); }
  .metric-value.red { color: var(--red); }
  .metric-value.amber { color: var(--amber); }
  table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
  th { background: var(--bg3); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
  td { padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  tr:hover td { background: var(--bg3); }
  .mono { font-family: 'SF Mono', monospace; }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .conc-bar-wrap { background: var(--bg3); border-radius: 4px; height: 6px; width: 80px; display: inline-block; vertical-align: middle; }
  .conc-bar { height: 6px; border-radius: 4px; }
  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .chart-wrap { position: relative; height: 260px; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .engineering pre { background: var(--bg3); padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 11px; color: var(--muted); margin-top: 8px; }
  .sentiment-pos { color: var(--green); }
  .sentiment-neg { color: var(--red); }
  .sentiment-neu { color: var(--muted); }
  .truncate { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  @media (max-width: 768px) { .two-col { grid-template-columns: 1fr; } }
</style>
</head>
<body>

<!-- 1. HEADER BAR -->
<div class="header">
  <h1>QUANT REPORT — {{ date }}</h1>
  <span class="badge badge-{{ regime_css }}">{{ regime }}</span>
  <span style="color:var(--muted)">Size {{ size_mult }}x</span>
  <span style="color:var(--muted)">VIX {{ vix }}</span>
  <span style="color:var(--muted)">10yr {{ yield_10yr }}%</span>
</div>

<!-- 2. METRIC CARDS -->
<div class="metrics">
  <div class="metric-card">
    <div class="metric-label">NAV</div>
    <div class="metric-value">{{ nav_fmt }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Cash %</div>
    <div class="metric-value {{ 'green' if cash_pct > 20 else 'amber' }}">{{ cash_pct }}%</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Open P&L</div>
    <div class="metric-value {{ 'green' if open_pnl >= 0 else 'red' }}">{{ open_pnl_fmt }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Realized P&L</div>
    <div class="metric-value {{ 'green' if realized_pnl >= 0 else 'red' }}">{{ realized_pnl_fmt }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">VIX</div>
    <div class="metric-value {{ 'green' if vix < 20 else ('amber' if vix < 30 else 'red') }}">{{ vix }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Sharpe (OOS)</div>
    <div class="metric-value {{ 'green' if sharpe > 1 else 'amber' }}">{{ sharpe }}</div>
  </div>
</div>

<!-- 3. OPEN POSITIONS -->
{% if open_positions %}
<h2>Open Positions</h2>
<table>
  <thead><tr>
    <th>Ticker</th><th>Avg Cost</th><th>Current</th><th>P&L €</th><th>P&L %</th>
    <th>NAV %</th><th>Concentration</th><th>Stop</th><th>T1</th><th>T2</th><th>Action</th>
  </tr></thead>
  <tbody>
  {% for p in open_positions %}
  <tr>
    <td><strong>{{ p.ticker }}</strong></td>
    <td class="mono">{{ p.avg_cost_eur_fmt }}</td>
    <td class="mono">{{ p.current_eur_fmt }}</td>
    <td class="mono {{ 'pos' if p.pnl_eur >= 0 else 'neg' }}">{{ p.pnl_eur_fmt }}</td>
    <td class="{{ 'pos' if p.pnl_pct >= 0 else 'neg' }}">{{ p.pnl_pct_fmt }}%</td>
    <td>{{ p.nav_pct }}%</td>
    <td>
      <div class="conc-bar-wrap">
        <div class="conc-bar" style="width:{{ [p.nav_pct_raw * 4, 100] | min }}%;background:{{ 'var(--red)' if p.nav_pct_raw > 15 else ('var(--amber)' if p.nav_pct_raw > 10 else 'var(--green)') }}"></div>
      </div>
    </td>
    <td class="mono neg">{{ p.stop_fmt }}</td>
    <td class="mono">{{ p.t1_fmt }}</td>
    <td class="mono">{{ p.t2_fmt }}</td>
    <td><span class="badge badge-{{ p.action_css }}">{{ p.action }}</span></td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}

<!-- 4. NEW SIGNALS TABLE -->
{% if new_signals %}
<h2>New Signals (Post-Filter)</h2>
<table>
  <thead><tr>
    <th>Rank</th><th>Ticker</th><th>Z-Score</th><th>Kelly Adj</th>
    <th>Prob</th><th>R:R</th><th>Entry €</th><th>Stop €</th><th>Target €</th><th>Rec</th>
  </tr></thead>
  <tbody>
  {% for s in new_signals %}
  <tr>
    <td>{{ s.rank }}</td>
    <td><strong>{{ s.ticker }}</strong></td>
    <td class="mono {{ 'pos' if s.composite_z > 0 else 'neg' }}">{{ "%.3f"|format(s.composite_z) }}</td>
    <td class="mono {{ 'pos' if s.kelly_adj > 0 else 'neg' }}">{{ "+%.3f"|format(s.kelly_adj) if s.kelly_adj > 0 else "%.3f"|format(s.kelly_adj) }}</td>
    <td>{{ "%.0f"|format(s.prob * 100) }}%</td>
    <td>{{ "%.1f"|format(s.rr) }}</td>
    <td class="mono">{{ s.entry_fmt }}</td>
    <td class="mono neg">{{ s.stop_fmt }}</td>
    <td class="mono pos">{{ s.target_fmt }}</td>
    <td><span class="badge badge-{{ s.rec_css }}">{{ s.rec }}</span></td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}

<!-- 5. NEWS DIGEST -->
{% if news_items %}
<h2>News Digest (Material Events)</h2>
<table>
  <thead><tr><th>Ticker</th><th>Headline</th><th>Source</th><th>Date</th><th>Sentiment</th><th>Material</th></tr></thead>
  <tbody>
  {% for n in news_items %}
  <tr>
    <td><strong>{{ n.ticker }}</strong></td>
    <td class="truncate">{{ n.title }}</td>
    <td style="color:var(--muted)">{{ n.source }}</td>
    <td style="color:var(--muted)">{{ n.published }}</td>
    <td class="sentiment-{{ 'pos' if n.sentiment_score > 0.1 else ('neg' if n.sentiment_score < -0.1 else 'neu') }}">
      {{ '▲' if n.sentiment_score > 0.1 else ('▼' if n.sentiment_score < -0.1 else '—') }}
      {{ "%.2f"|format(n.sentiment_score) }}
    </td>
    <td>{% if n.is_material %}<span class="badge badge-material">MATERIAL</span>{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}

<!-- 6 + 7. CHARTS -->
<div class="two-col">
  <div class="card">
    <h3>Composite Z-Scores — Top Valid Signals</h3>
    <div class="chart-wrap"><canvas id="zscoreChart"></canvas></div>
  </div>
  <div class="card">
    <h3>Factor IC — Current vs Suggested Weight</h3>
    <div class="chart-wrap"><canvas id="icChart"></canvas></div>
  </div>
</div>

<!-- 8. SIGNAL ENGINE QUALITY -->
<h2>Signal Engine Quality</h2>
<div class="metrics">
  <div class="metric-card">
    <div class="metric-label">OOS Sharpe</div>
    <div class="metric-value {{ 'green' if sharpe > 1 else 'amber' }}">{{ sharpe }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Max Drawdown</div>
    <div class="metric-value {{ 'green' if maxdd_num > -5 else 'red' }}">{{ maxdd }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Hit Rate</div>
    <div class="metric-value {{ 'green' if hitrate_num > 55 else 'amber' }}">{{ hitrate }}%</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">OOS Period</div>
    <div class="metric-value" style="font-size:13px">{{ oos_period }}</div>
  </div>
</div>

<!-- 9. ENGINEERING NOTES -->
<h2>Engineering Notes</h2>
<div class="card engineering">
  {% if ma_filtered %}
  <h3>M&A Filter — Removed Tickers</h3>
  <table>
    <thead><tr><th>Ticker</th><th>Reason</th></tr></thead>
    <tbody>
    {% for t, r in ma_filtered.items() %}
    <tr><td>{{ t }}</td><td style="color:var(--amber)">{{ r }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
  <h3>Factor Reweight Suggestions (from IC Table)</h3>
  <table>
    <thead><tr><th>Factor</th><th>Mean IC</th><th>Verdict</th><th>Suggested Weight</th></tr></thead>
    <tbody>
    {% for f in factor_ic %}
    <tr>
      <td>{{ f.factor }}</td>
      <td class="mono {{ 'pos' if f.mean_ic > 0 else 'neg' }}">{{ "+%.4f"|format(f.mean_ic) if f.mean_ic > 0 else "%.4f"|format(f.mean_ic) }}</td>
      <td><span class="badge badge-{{ 'hold' if f.verdict == 'KEEP' else 'amber' }}">{{ f.verdict }}</span></td>
      <td class="mono">{{ "%.1f"|format(f.suggested_weight * 100) }}%</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<script>
// Z-score chart
const zCtx = document.getElementById('zscoreChart').getContext('2d');
new Chart(zCtx, {
  type: 'bar',
  data: {
    labels: {{ zscore_labels | tojson }},
    datasets: [{
      label: 'Composite Z',
      data: {{ zscore_values | tojson }},
      backgroundColor: {{ zscore_colors | tojson }},
      borderWidth: 0,
    }]
  },
  options: {
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: '#2e3250' }, ticks: { color: '#8b91b8' } },
      y: { grid: { display: false }, ticks: { color: '#e8eaf6', font: { family: 'monospace' } } }
    }
  }
});

// IC chart
const icCtx = document.getElementById('icChart').getContext('2d');
new Chart(icCtx, {
  type: 'bar',
  data: {
    labels: {{ ic_labels | tojson }},
    datasets: [
      { label: 'Mean IC', data: {{ ic_values | tojson }}, backgroundColor: {{ ic_colors | tojson }}, borderWidth: 0 },
      { label: 'Suggested Wt', data: {{ ic_suggested | tojson }}, backgroundColor: 'rgba(59,130,246,0.4)', borderWidth: 0 }
    ]
  },
  options: {
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: '#8b91b8', font: { size: 11 } } } },
    scales: {
      x: { grid: { color: '#2e3250' }, ticks: { color: '#8b91b8' } },
      y: { grid: { display: false }, ticks: { color: '#e8eaf6', font: { family: 'monospace' } } }
    }
  }
});
</script>

</body>
</html>
"""

# ==============================================================================
# 1. SIGNAL REPORT PARSER
# ==============================================================================

class SignalReportParser:
    """Parse the latest signal_reports/*.txt and extract all structured data."""

    def __init__(self, report_path: Optional[str] = None):
        if report_path:
            self.path = Path(report_path)
        else:
            self.path = self._find_latest()
        self.text = self.path.read_text(errors='replace')
        self.data: dict = {}

    def _find_latest(self) -> Path:
        reports = sorted(SIGNAL_REPORTS_DIR.glob("signal_report_*.txt"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        if not reports:
            raise FileNotFoundError(
                f"No signal reports found in {SIGNAL_REPORTS_DIR}. "
                "Run the pipeline first."
            )
        return reports[0]

    def parse(self) -> dict:
        d = {}
        d['report_path'] = str(self.path)
        d['run_date'] = self._parse_run_date()
        d['regime'] = self._parse_regime()
        d['top20'] = self._parse_top20()
        d['position_sizes'] = self._parse_position_sizes()
        d['open_positions'] = self._parse_open_positions()
        d['portfolio'] = self._parse_portfolio()
        d['backtest'] = self._parse_backtest()
        d['factor_ic'] = self._parse_factor_ic()
        self.data = d
        return d

    def _parse_run_date(self) -> str:
        m = re.search(r'Run Date:\s*(\d{4}-\d{2}-\d{2})', self.text)
        return m.group(1) if m else datetime.now().strftime('%Y-%m-%d')

    def _parse_regime(self) -> dict:
        regime = {'label': 'UNKNOWN', 'score': 0, 'size_mult': 1.0, 'vix': 20.0}
        m = re.search(
            r'MACRO REGIME:\s*(\w+)\s+\(score:\s*([-\d]+)\)\s+\|\s+Size mult:\s*([\d.]+)x',
            self.text
        )
        if m:
            regime['label'] = m.group(1)
            regime['score'] = int(m.group(2))
            regime['size_mult'] = float(m.group(3))
        m2 = re.search(r'VIX=([\d.]+)', self.text)
        if m2:
            regime['vix'] = float(m2.group(1))
        return regime

    def _parse_top20(self) -> list:
        results = []
        # Find the TOP 20 ranked section
        block_m = re.search(
            r'TOP 20 RANKED STOCKS.*?\n(.*?)(?=\n\s*BOTTOM|\n\s*RECOMMENDED|\Z)',
            self.text, re.DOTALL
        )
        if not block_m:
            return results
        block = block_m.group(1)
        for line in block.split('\n'):
            m = re.match(
                r'\s+(\d+)\s+([\w\.\-]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)',
                line
            )
            if m:
                results.append({
                    'rank': int(m.group(1)),
                    'ticker': m.group(2),
                    'composite_z': float(m.group(3)),
                    'mom12_1': float(m.group(4)),
                    'mom6_1': float(m.group(5)),
                })
        return results

    def _parse_position_sizes(self) -> list:
        results = []
        block_m = re.search(
            r'RECOMMENDED POSITION SIZES.*?\n.*?[─\-]+\n(.*?)(?=\n\s*Total|\Z)',
            self.text, re.DOTALL
        )
        if not block_m:
            return results
        for line in block_m.group(1).split('\n'):
            m = re.match(
                r'\s+([\w\.\-]+)\s+([\d.]+)%\s+([\d,]+)\s+([\d.]+)%',
                line
            )
            if m:
                results.append({
                    'ticker': m.group(1),
                    'weight_pct': float(m.group(2)),
                    'eur': float(m.group(3).replace(',', '')),
                    'ann_vol_pct': float(m.group(4)),
                })
        return results

    def _parse_open_positions(self) -> list:
        results = []
        # Separator is Unicode ─ (U+2500) not ASCII -
        block_m = re.search(
            r'OPEN POSITIONS.*?\n.*?[─\-]+\n(.*?)(?=\n\s*[─\-]{10,}|\Z)',
            self.text, re.DOTALL
        )
        if not block_m:
            return results
        lines = block_m.group(1).split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            # Primary row: ticker shares avg_cost current value pnl_eur pnl_pct days status
            m = re.match(
                r'\s+([\w\.\-]+)\s+(\d+)\s+€\s+([\d,.]+)\s+€\s+([\d,.]+)\s+€\s+([\d,.]+)\s+€\s+([-\d,.]+)\s+([-\d.]+)%',
                line
            )
            if m:
                pos = {
                    'ticker': m.group(1),
                    'shares': int(m.group(2)),
                    'avg_cost_eur': float(m.group(3).replace(',', '')),
                    'current_eur': float(m.group(4).replace(',', '')),
                    'value_eur': float(m.group(5).replace(',', '')),
                    'pnl_eur': float(m.group(6).replace(',', '')),
                    'pnl_pct': float(m.group(7)),
                    'stop': None, 't1': None, 't2': None,
                }
                # Next line has Stop / T1 / T2
                if i + 1 < len(lines):
                    line2 = lines[i + 1]
                    sm = re.search(r'Stop:\s*€\s*([\d,.]+)', line2)
                    t1m = re.search(r'T1:\s*€\s*([\d,.]+)', line2)
                    t2m = re.search(r'T2:\s*€\s*([\d,.]+)', line2)
                    if sm:
                        pos['stop'] = float(sm.group(1).replace(',', ''))
                    if t1m:
                        pos['t1'] = float(t1m.group(1).replace(',', ''))
                    if t2m:
                        pos['t2'] = float(t2m.group(1).replace(',', ''))
                results.append(pos)
            i += 1
        return results

    def _parse_portfolio(self) -> dict:
        port = {'nav': 50000, 'equity_eur': 0, 'equity_pct': 0,
                'crypto_eur': 0, 'crypto_pct': 0, 'cash_eur': 0, 'cash_pct': 0,
                'realized_pnl': 0, 'unrealized_pnl': 0}
        m = re.search(r'NAV:\s+€\s+([\d,]+)', self.text)
        if m:
            port['nav'] = float(m.group(1).replace(',', ''))
        m = re.search(r'Equity exposure:\s+€\s+([\d,]+)\s+\(([\d.]+)%\)', self.text)
        if m:
            port['equity_eur'] = float(m.group(1).replace(',', ''))
            port['equity_pct'] = float(m.group(2))
        m = re.search(r'Crypto exposure:\s+€\s+([\d,]+)\s+\(([\d.]+)%\)', self.text)
        if m:
            port['crypto_eur'] = float(m.group(1).replace(',', ''))
            port['crypto_pct'] = float(m.group(2))
        m = re.search(r'Cash:\s+€\s+([\d,]+)\s+\(([\d.]+)%\)', self.text)
        if m:
            port['cash_eur'] = float(m.group(1).replace(',', ''))
            port['cash_pct'] = float(m.group(2))
        m = re.search(r'Realized P&L:\s+€\+([-\d,.]+)', self.text)
        if m:
            port['realized_pnl'] = float(m.group(1).replace(',', ''))
        m = re.search(r'Unrealized P&L:\s+€([-\d,.]+)', self.text)
        if m:
            port['unrealized_pnl'] = float(m.group(1).replace(',', ''))
        return port

    def _parse_backtest(self) -> dict:
        bt = {'sharpe': None, 'maxdd': None, 'hitrate': None, 'oos_period': None}
        m = re.search(r'Sharpe \(portfolio\):\s+([\d.]+)', self.text)
        if m:
            bt['sharpe'] = float(m.group(1))
        m = re.search(r'MaxDD:\s*([-\d.]+)%', self.text)
        if m:
            bt['maxdd'] = float(m.group(1))
        m = re.search(r'Hit(?:Rate)?:\s*([\d.]+)%', self.text)
        if m:
            bt['hitrate'] = float(m.group(1))
        m = re.search(r'(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})\s+[\d.]+\s+[-\d.]+%\s+[\d.]+%', self.text)
        if m:
            bt['oos_period'] = f"{m.group(1)} → {m.group(2)}"
        return bt

    def _parse_factor_ic(self) -> list:
        results = []
        block_m = re.search(
            r'PER-FACTOR IC TABLE.*?\n.*?-+\n(.*?)(?=\n\s*Step|\n\s*$|\Z)',
            self.text, re.DOTALL
        )
        if not block_m:
            return results
        for line in block_m.group(1).split('\n'):
            m = re.match(
                r'\s+([\w_]+)\s+([+-][\d.]+)\s+(\d+)\s+(KEEP|REVIEW)',
                line
            )
            if m:
                results.append({
                    'factor': m.group(1),
                    'mean_ic': float(m.group(2)),
                    'windows': int(m.group(3)),
                    'verdict': m.group(4),
                })
        return results


# ==============================================================================
# 2. MARKET DATA FETCHER
# ==============================================================================

class MarketDataFetcher:
    """Fetch yfinance data for tickers, with M&A filter and 4h cache."""

    FIELDS = [
        'currentPrice', 'previousClose', 'fiftyTwoWeekHigh', 'fiftyTwoWeekLow',
        'beta', 'trailingPE', 'forwardPE', 'priceToBook', 'priceToSalesTrailingTwelveMonths',
        'enterpriseToEbitda', 'enterpriseToRevenue', 'pegRatio',
        'revenueGrowth', 'earningsGrowth', 'grossMargins', 'operatingMargins', 'profitMargins',
        'returnOnEquity', 'returnOnAssets', 'totalDebt', 'totalCash', 'freeCashflow',
        'shortPercentOfFloat', 'sharesShort', 'shortRatio',
        'heldPercentInsiders', 'heldPercentInstitutions',
        'marketCap', 'sharesOutstanding',
        'longName', 'sector', 'industry',
        'targetMeanPrice', 'targetHighPrice', 'targetLowPrice', 'recommendationMean',
        'numberOfAnalystOpinions',
        'fiftyDayAverage', 'twoHundredDayAverage',
        'trailingEps', 'forwardEps',
        'dividendYield', 'payoutRatio',
        'nextEarningsDate',
        'longBusinessSummary', 'regularMarketPrice',
    ]

    def __init__(self, cache: dict, logger: logging.Logger):
        self.cache = cache
        self.log = logger

    def is_ma_or_delisted(self, ticker: str, info: dict) -> tuple:
        if not info.get('regularMarketPrice') and not info.get('currentPrice'):
            return True, "no_price_data"
        hi = info.get('fiftyTwoWeekHigh', 0)
        lo = info.get('fiftyTwoWeekLow', 1)
        if hi > 0 and (hi - lo) / hi < 0.05:
            return True, "price_pinned_ma"
        summary = info.get('longBusinessSummary', '').lower()
        if any(k in summary for k in ['acquired by', 'merger with', 'no longer traded']):
            return True, "summary_keyword"
        mcap = info.get('marketCap', 0)
        price = info.get('currentPrice', info.get('regularMarketPrice', 0))
        if price and price > 1 and mcap and mcap < price * 1000:
            return True, "mcap_artifact"
        return False, ""

    def fetch(self, ticker: str) -> Optional[dict]:
        """Fetch and cache yfinance info for a single ticker."""
        import yfinance as yf
        # Check cache
        mkt_cache = self.cache.get('market_data', {})
        if ticker in mkt_cache:
            return mkt_cache[ticker]
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            result = {f: info.get(f) for f in self.FIELDS}
            result['_ticker'] = ticker
            # Cache it
            if 'market_data' not in self.cache:
                self.cache['market_data'] = {}
            self.cache['market_data'][ticker] = result
            return result
        except Exception as e:
            self.log.warning(f"[{ticker}] market data fetch failed: {e}")
            return None

    def fetch_financials(self, ticker: str) -> dict:
        """Fetch 5yr annual financials and cashflow."""
        import yfinance as yf
        fin_cache = self.cache.get('financials', {})
        if ticker in fin_cache:
            return fin_cache[ticker]
        result = {'revenue': [], 'fcf': [], 'net_income': [], 'years': []}
        try:
            t = yf.Ticker(ticker)
            fin = t.financials
            cf = t.cashflow
            if fin is not None and not fin.empty:
                rev_row = fin.loc['Total Revenue'] if 'Total Revenue' in fin.index else None
                ni_row = fin.loc['Net Income'] if 'Net Income' in fin.index else None
                if rev_row is not None:
                    result['revenue'] = [float(v) for v in rev_row.values[:5] if pd.notna(v)]
                if ni_row is not None:
                    result['net_income'] = [float(v) for v in ni_row.values[:5] if pd.notna(v)]
            if cf is not None and not cf.empty:
                fcf_row = cf.loc['Free Cash Flow'] if 'Free Cash Flow' in cf.index else None
                if fcf_row is not None:
                    result['fcf'] = [float(v) for v in fcf_row.values[:5] if pd.notna(v)]
        except Exception as e:
            self.log.warning(f"[{ticker}] financials fetch failed: {e}")
        if 'financials' not in self.cache:
            self.cache['financials'] = {}
        self.cache['financials'][ticker] = result
        return result

    def fetch_all(self, tickers: list) -> tuple:
        """Returns (valid_data, ma_filtered) dicts."""
        valid = {}
        filtered = {}
        for ticker in tickers:
            info = self.fetch(ticker)
            if info is None:
                self.log.warning(f"[{ticker}] skipped — no data returned")
                filtered[ticker] = "fetch_error"
                continue
            is_bad, reason = self.is_ma_or_delisted(ticker, info)
            if is_bad:
                self.log.warning(f"[{ticker}] M&A/delisted filter: {reason}")
                filtered[ticker] = reason
            else:
                valid[ticker] = info
        return valid, filtered


# ==============================================================================
# 3. NEWS SCRAPER
# ==============================================================================

class NewsScraper:
    """Scrape recent news from Yahoo Finance RSS, Google News, and SEC EDGAR."""

    def __init__(self, cache: dict, logger: logging.Logger):
        self.cache = cache
        self.log = logger
        try:
            import feedparser
            self._feedparser = feedparser
        except ImportError:
            self.log.warning("feedparser not installed — news scraping disabled")
            self._feedparser = None

    def _sentiment_score(self, text: str) -> float:
        text_lower = text.lower()
        score = sum(1 for kw in BULLISH_KW if kw in text_lower)
        score -= sum(1 for kw in BEARISH_KW if kw in text_lower)
        total = sum(1 for kw in BULLISH_KW + BEARISH_KW if kw in text_lower)
        return max(-1.0, min(1.0, score / max(total, 1)))

    def _scrape_yahoo_rss(self, ticker: str) -> list:
        if not self._feedparser:
            return []
        articles = []
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        try:
            feed = self._feedparser.parse(url)
            cutoff = datetime.now() - timedelta(days=7)
            for entry in feed.entries[:10]:
                pub = entry.get('published', '')
                try:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(pub)
                    if pub_dt.replace(tzinfo=None) < cutoff:
                        continue
                    pub_str = pub_dt.strftime('%Y-%m-%d')
                except Exception:
                    pub_str = pub[:10] if len(pub) >= 10 else pub
                title = entry.get('title', '')
                articles.append({
                    'title': title,
                    'source': 'Yahoo Finance',
                    'url': entry.get('link', ''),
                    'published': pub_str,
                    'sentiment_score': self._sentiment_score(title),
                    'is_material': bool(MATERIAL_RE.search(title)),
                })
        except Exception as e:
            self.log.debug(f"[{ticker}] Yahoo RSS failed: {e}")
        return articles

    def _scrape_google_news(self, ticker: str, company_name: str) -> list:
        if not self._feedparser:
            return []
        articles = []
        query = urllib.parse.quote(f"{ticker} OR {company_name} stock")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            feed = self._feedparser.parse(url)
            cutoff = datetime.now() - timedelta(days=7)
            for entry in feed.entries[:8]:
                pub = entry.get('published', '')
                try:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(pub)
                    if pub_dt.replace(tzinfo=None) < cutoff:
                        continue
                    pub_str = pub_dt.strftime('%Y-%m-%d')
                except Exception:
                    pub_str = pub[:10] if len(pub) >= 10 else pub
                title = entry.get('title', '')
                articles.append({
                    'title': title,
                    'source': 'Google News',
                    'url': entry.get('link', ''),
                    'published': pub_str,
                    'sentiment_score': self._sentiment_score(title),
                    'is_material': bool(MATERIAL_RE.search(title)),
                })
        except Exception as e:
            self.log.debug(f"[{ticker}] Google News failed: {e}")
        return articles

    def _scrape_edgar_filings(self, ticker: str) -> list:
        articles = []
        try:
            # CIK lookup via SEC EDGAR full-text search
            search_url = (
                f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22"
                f"&dateRange=custom&startdt={(datetime.now()-timedelta(days=14)).strftime('%Y-%m-%d')}"
                f"&forms=8-K"
            )
            req = urllib.request.Request(
                search_url,
                headers={"User-Agent": "SignalEngine/1.0 quant@example.com"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                hits = data.get('hits', {}).get('hits', [])
                for h in hits[:3]:
                    src = h.get('_source', {})
                    title = src.get('display_names', [ticker])[0] + ' — ' + src.get('form_type', '8-K')
                    filed = src.get('file_date', '')
                    articles.append({
                        'title': title,
                        'source': 'SEC EDGAR',
                        'url': 'https://www.sec.gov' + src.get('file_url', ''),
                        'published': filed,
                        'sentiment_score': 0.0,
                        'is_material': True,
                    })
        except Exception as e:
            self.log.debug(f"[{ticker}] EDGAR scrape failed: {e}")
        return articles

    def scrape(self, ticker: str, company_name: str = '') -> list:
        news_cache = self.cache.get('news', {})
        if ticker in news_cache:
            return news_cache[ticker]
        articles = []
        try:
            articles += self._scrape_yahoo_rss(ticker)
            articles += self._scrape_google_news(ticker, company_name or ticker)
            articles += self._scrape_edgar_filings(ticker)
            # Deduplicate by title
            seen = set()
            deduped = []
            for a in articles:
                key = a['title'][:60].lower()
                if key not in seen:
                    seen.add(key)
                    deduped.append(a)
            articles = deduped
        except Exception as e:
            self.log.warning(f"[{ticker}] news scrape error: {e}")
        if 'news' not in self.cache:
            self.cache['news'] = {}
        self.cache['news'][ticker] = articles
        return articles


# ==============================================================================
# 3b. EXA NEWS SENTIMENT
# ==============================================================================

_EXA_NEWS_CACHE: dict = {}
_EXA_NEWS_CACHE_TTL_HOURS = 6


def fetch_news_sentiment(ticker: str, days_back: int = 7) -> dict:
    """
    Fetch recent news headlines + highlights for a ticker via Exa neural search.

    Returns article titles, dates, and key excerpts so Claude can assess
    sentiment and catalysts directly from the source material — no collapsed
    score that hides the reasoning.

    Falls back to neutral when EXA_API_KEY is not set or the call fails.

    Returns
    -------
    dict with keys:
        ticker, articles_found, avg_sentiment, sentiment_label, period_days,
        source, headlines  (list of {title, url, date, highlights})
    """
    cached = _EXA_NEWS_CACHE.get(ticker)
    if cached and cached["expires"] > datetime.now():
        return cached["result"]

    _default = {
        "ticker":          ticker,
        "articles_found":  0,
        "avg_sentiment":   0.0,
        "sentiment_label": "Neutral",
        "period_days":     days_back,
        "source":          "fallback_neutral",
        "headlines":       [],
    }

    if not EXA_API_KEY:
        logging.getLogger(__name__).warning(
            "EXA_API_KEY not set — news sentiment unavailable. Add EXA_API_KEY to .env."
        )
        return _default

    try:
        from exa_py import Exa
        exa = Exa(api_key=EXA_API_KEY)

        response = exa.search_and_contents(
            f"{ticker} stock news earnings catalyst",
            type="auto",
            category="news",
            num_results=15,
            highlights=True,
        )

        articles = getattr(response, "results", [])
        headlines = []
        for a in articles:
            pub_date = getattr(a, "published_date", None) or getattr(a, "publishedDate", None) or ""
            if pub_date:
                pub_date = pub_date[:10]  # YYYY-MM-DD
            raw_highlights = getattr(a, "highlights", None) or []
            headlines.append({
                "title":      getattr(a, "title", "") or "",
                "url":        getattr(a, "url", "") or "",
                "date":       pub_date,
                "highlights": raw_highlights[:2],  # top 2 excerpts per article
            })

        result = {
            "ticker":          ticker,
            "articles_found":  len(headlines),
            "avg_sentiment":   0.0,   # Claude assesses from headlines; no collapsed score
            "sentiment_label": "Neutral",
            "period_days":     days_back,
            "source":          "exa",
            "headlines":       headlines,
        }

    except Exception as exc:
        logging.getLogger(__name__).warning("[%s] Exa news fetch failed: %s", ticker, exc)
        return _default

    _EXA_NEWS_CACHE[ticker] = {
        "expires": datetime.now() + timedelta(hours=_EXA_NEWS_CACHE_TTL_HOURS),
        "result":  result,
    }
    return result


# ==============================================================================
# 4. QUANT ANALYSIS PIPELINE
# ==============================================================================

def fetch_10yr_yield() -> float:
    """Fetch current 10yr Treasury yield via yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker('^TNX')
        info = t.info
        price = info.get('regularMarketPrice') or info.get('previousClose')
        if price:
            return float(price) / 100
    except Exception:
        pass
    return 0.043  # ~4.3% fallback


def calc_wacc(beta: float, rf: float = None, erp: float = 0.055) -> float:
    if rf is None:
        rf = fetch_10yr_yield()
    beta = beta if beta and not np.isnan(beta) else 1.0
    return rf + beta * erp


def score_metric(value: float, thresholds: list) -> float:
    """Score a metric 0–1 given 4 thresholds [bad, neutral, good, great]."""
    if value is None or np.isnan(value):
        return 0.25  # neutral default
    if value <= thresholds[0]:
        return 0.0
    if value <= thresholds[1]:
        return 0.25
    if value <= thresholds[2]:
        return 0.50
    if value <= thresholds[3]:
        return 0.75
    return 1.0


def dcf_valuation(fcf_base: float, growth_stage1: float, growth_terminal: float,
                  wacc: float, years_stage1: int = 5, shares: float = None) -> dict:
    """2-stage DCF. Returns {total, per_share, sensitivity}."""
    if fcf_base <= 0 or wacc <= growth_terminal:
        return {'total': None, 'per_share': None, 'sensitivity': {}}
    pvs = []
    fcf = fcf_base
    for y in range(1, years_stage1 + 1):
        fcf *= (1 + growth_stage1)
        pvs.append(fcf / (1 + wacc) ** y)
    terminal = (fcf * (1 + growth_terminal)) / (wacc - growth_terminal)
    terminal_pv = terminal / (1 + wacc) ** years_stage1
    total = sum(pvs) + terminal_pv
    per_share = total / shares if shares and shares > 0 else None
    # Sensitivity: ±1% growth, ±50bps WACC
    sens = {}
    for dg in [-0.01, 0, 0.01]:
        for dw in [-0.005, 0, 0.005]:
            new_wacc = wacc + dw
            new_g1 = growth_stage1 + dg
            new_gt = growth_terminal
            if new_wacc <= new_gt:
                sens[(dg, dw)] = None
                continue
            pvs2 = []
            f2 = fcf_base
            for y in range(1, years_stage1 + 1):
                f2 *= (1 + new_g1)
                pvs2.append(f2 / (1 + new_wacc) ** y)
            t2 = (f2 * (1 + new_gt)) / (new_wacc - new_gt)
            t2_pv = t2 / (1 + new_wacc) ** years_stage1
            tot2 = sum(pvs2) + t2_pv
            sens[(dg, dw)] = tot2 / shares if shares and shares > 0 else None
    return {'total': total, 'per_share': per_share, 'sensitivity': sens}


def kelly(p: float, b: float, regime_mult: float = 1.0,
          nav: float = 50000, max_risk_pct: float = 0.02) -> dict:
    """Kelly criterion with regime adjustment."""
    if b <= 0:
        return {'raw_kelly': -1, 'regime_adjusted': -1, 'is_negative': True,
                'max_risk_eur': nav * max_risk_pct, 'verdict': 'PASS'}
    raw_f = (p * (b + 1) - 1) / b
    kelly_adj = raw_f * regime_mult
    portfolio_risk_eur = nav * max_risk_pct
    verdict = 'SELL' if raw_f < 0 else ('PASS' if kelly_adj < 0.01 else 'BUY')
    return {
        'raw_kelly': round(raw_f, 4),
        'regime_adjusted': round(kelly_adj, 4),
        'is_negative': raw_f < 0,
        'max_risk_eur': portfolio_risk_eur,
        'verdict': verdict,
    }


def quality_score(info: dict, wacc: float) -> dict:
    """Quality score 0–10 based on growth, margins, ROIC-WACC spread."""
    roe = info.get('returnOnEquity') or 0
    moat = roe - wacc
    scores = {
        'revenue_growth': score_metric(info.get('revenueGrowth') or 0,
                                       [0, 0.05, 0.15, 0.25]),
        'margin_stability': score_metric(info.get('operatingMargins') or 0,
                                         [-0.1, 0, 0.1, 0.2]),
        'roic_wacc_spread': score_metric(moat, [-0.1, -0.02, 0.05, 0.15]),
        'capital_allocation': 1.0,
        'earnings_reliability': score_metric(info.get('earningsGrowth') or 0,
                                             [-0.2, 0, 0.1, 0.3]),
    }
    total = sum(scores.values()) / len(scores) * 10
    return {**scores, 'total': round(total, 1), 'moat_spread': round(moat, 4)}


def calc_atr(ticker: str, period: int = 14) -> float:
    """14-day ATR via yfinance."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period='1mo')
        if hist.empty or len(hist) < period:
            return 0.0
        tr = pd.concat([
            hist['High'] - hist['Low'],
            (hist['High'] - hist['Close'].shift()).abs(),
            (hist['Low'] - hist['Close'].shift()).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.0


def trail_stop(entry: float, current: float, stop_original: float,
               atr: float, atr_mult: float = 2.0) -> float:
    """Suggested trailing stop = max(original_stop, current - ATR×mult)."""
    if atr <= 0:
        return stop_original
    return max(stop_original, current - atr * atr_mult)


def vix_regime_mult(vix: float) -> float:
    if vix < 15:
        return 1.0
    if vix < 25:
        return 0.75
    if vix < 35:
        return 0.50
    return 0.25


class QuantAnalysisPipeline:
    """Run full quant analysis for a ticker given market data + financials."""

    def __init__(self, nav: float, regime: dict, logger: logging.Logger):
        self.nav = nav
        self.regime = regime
        self.vix = regime.get('vix', 20.0)
        self.size_mult = regime.get('size_mult', 1.0)
        self.log = logger
        self._rf = None  # lazy-fetched

    def _get_rf(self) -> float:
        if self._rf is None:
            self._rf = fetch_10yr_yield()
        return self._rf

    def analyze(self, ticker: str, info: dict, financials: dict,
                signal_data: Optional[dict] = None,
                open_pos: Optional[dict] = None) -> dict:
        """Full pipeline for one ticker. Returns analysis dict."""
        result = {
            'ticker': ticker,
            'company': info.get('longName', ticker),
            'sector': info.get('sector', 'Unknown'),
            'industry': info.get('industry', ''),
        }

        # ── Section 0: Price & valuation ──────────────────────────────────────
        price = info.get('currentPrice') or info.get('regularMarketPrice') or 0
        result['price_usd'] = price
        try:
            from fx_rates import convert_to_eur, get_ticker_currency
            ccy = get_ticker_currency(ticker)
            price_eur = convert_to_eur(price, ccy)
        except Exception:
            price_eur = price * 0.92  # fallback EUR/USD ~0.92
        result['price_eur'] = price_eur

        # ── Section 1: WACC ───────────────────────────────────────────────────
        beta = info.get('beta') or 1.0
        rf = self._get_rf()
        wacc = calc_wacc(beta, rf)
        result['wacc'] = round(wacc, 4)
        result['beta'] = beta
        result['rf'] = round(rf, 4)

        # ── Section 2: DCF valuation ──────────────────────────────────────────
        fcf_list = financials.get('fcf', [])
        fcf_base = fcf_list[0] if fcf_list else (info.get('freeCashflow') or 0)
        rev_growth = info.get('revenueGrowth') or 0.05
        growth_stage1 = max(min(rev_growth, 0.40), -0.20)
        growth_terminal = min(rf + 0.01, 0.03)
        shares = info.get('sharesOutstanding') or 0
        dcf = dcf_valuation(fcf_base, growth_stage1, growth_terminal, wacc,
                            years_stage1=5, shares=shares)
        result['dcf'] = dcf
        if dcf.get('per_share') and price > 0:
            dcf_usd = dcf['per_share']
            result['dcf_upside_pct'] = round((dcf_usd - price) / price * 100, 1)
        else:
            result['dcf_upside_pct'] = None

        # ── Section 3: Quality score ──────────────────────────────────────────
        qs = quality_score(info, wacc)
        result['quality'] = qs

        # ── Section 4: Analyst consensus + implied RR ─────────────────────────
        target_mean_usd = info.get('targetMeanPrice') or price * 1.10
        target_high_usd = info.get('targetHighPrice') or price * 1.20
        rec_mean = info.get('recommendationMean') or 3.0  # 1=strong buy, 5=sell
        # Derive probability: blend prob_combined (signal-based) with analyst consensus.
        # quant_report collects fewer signals than ai_quant, so analyst consensus
        # carries 60% weight here while prob_combined contributes 40%.
        analyst_prob = max(0.30, min(0.85, (5 - rec_mean) / 4))
        try:
            from utils.prob_engine import compute_prob_combined as _compute_pcomb
            _qr_signals = {
                "fundamentals": result.get("quality") and {
                    "fundamental_score_pct": result.get("quality", {}).get("score_pct")
                } or {},
                "signal_agreement_score": None,
            }
            _qr_pr   = _compute_pcomb(_qr_signals)
            _base_pc = _qr_pr["prob_combined"]
            prob = round(_base_pc * 0.40 + analyst_prob * 0.60, 3)
        except Exception:
            prob = analyst_prob
        prob = max(0.30, min(0.85, prob))
        # Convert analyst targets to EUR for consistent display
        try:
            from fx_rates import convert_to_eur, get_ticker_currency
            ccy = get_ticker_currency(ticker)
            target_mean_eur = convert_to_eur(target_mean_usd, ccy)
            target_high_eur = convert_to_eur(target_high_usd, ccy)
        except Exception:
            target_mean_eur = target_mean_usd * 0.92
            target_high_eur = target_high_usd * 0.92
        # Reward:Risk in EUR
        stop_price_eur = open_pos['stop'] if open_pos and open_pos.get('stop') else price_eur * 0.92
        upside = target_mean_eur - price_eur
        downside = price_eur - stop_price_eur
        rr = upside / max(downside, 0.01)
        result['prob'] = round(prob, 3)
        result['rr'] = round(rr, 2)
        result['target_mean'] = target_mean_eur
        result['target_high'] = target_high_eur
        result['stop'] = stop_price_eur

        # ── Section 5: Kelly ──────────────────────────────────────────────────
        regime_mult = min(self.size_mult, vix_regime_mult(self.vix))
        k = kelly(prob, max(rr, 0.01), regime_mult=regime_mult,
                  nav=self.nav, max_risk_pct=0.02)
        result['kelly'] = k

        # ── Section 6: Recommendation logic ──────────────────────────────────
        rec = 'PASS'
        if k['is_negative']:
            rec = 'SELL' if open_pos else 'PASS'
        elif prob < PASS_THRESHOLD['min_prob'] or rr < PASS_THRESHOLD['min_rr']:
            rec = 'PASS'
        elif open_pos:
            pnl_pct = open_pos.get('pnl_pct', 0)
            if pnl_pct >= 10:
                rec = 'TRIM'
            elif pnl_pct <= -15 or (price <= stop_price * 1.02):
                rec = 'EXIT'
            else:
                rec = 'HOLD'
        else:
            rec = 'BUY'
        result['rec'] = rec

        # ── Section 7: Trailing stop (open positions only) ────────────────────
        if open_pos:
            atr_val = calc_atr(ticker)
            entry = open_pos.get('avg_cost_eur', price_eur)
            orig_stop = open_pos.get('stop', price_eur * 0.92)
            ts = trail_stop(entry, price_eur, orig_stop, atr_val)
            result['trail_stop'] = round(ts, 2)
            result['atr'] = round(atr_val, 3)
        else:
            result['trail_stop'] = None
            result['atr'] = None

        return result


# ==============================================================================
# 5. REPORT GENERATOR
# ==============================================================================

def _fmt_eur(value: float) -> str:
    """Format a EUR value as €X,XXX.XX"""
    if value is None:
        return '—'
    sign = '-' if value < 0 else ''
    abs_val = abs(value)
    return f"{sign}€{abs_val:,.2f}"


def _fmt_pct(value: float, decimals: int = 1) -> str:
    if value is None:
        return '—'
    return f"{value:+.{decimals}f}"


class ReportGenerator:
    """Generate markdown report and HTML dashboard."""

    def __init__(self, parsed: dict, analyses: dict, ma_filtered: dict,
                 news: dict, nav: float, logger: logging.Logger):
        self.parsed = parsed
        self.analyses = analyses   # {ticker: analysis_dict}
        self.ma_filtered = ma_filtered
        self.news = news
        self.nav = nav
        self.log = logger
        self.date_str = datetime.now().strftime('%Y%m%d')
        QUANT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self.md_path = QUANT_REPORTS_DIR / f"report_{self.date_str}.md"
        self.html_path = QUANT_REPORTS_DIR / f"dashboard_{self.date_str}.html"
        self.err_path = QUANT_REPORTS_DIR / f"errors_{self.date_str}.log"

    def _regime_section(self) -> str:
        r = self.parsed['regime']
        bt = self.parsed['backtest']
        lines = [
            "## REGIME SNAPSHOT\n",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Regime | **{r['label']}** |",
            f"| Score | {r['score']} |",
            f"| Size Multiplier | {r['size_mult']}x |",
            f"| VIX | {r['vix']} |",
            f"| Backtest Sharpe | {bt.get('sharpe', '—')} |",
            f"| Max Drawdown | {bt.get('maxdd', '—')}% |",
            f"| Hit Rate | {bt.get('hitrate', '—')}% |",
            f"| OOS Period | {bt.get('oos_period', '—')} |",
            "",
        ]
        return '\n'.join(lines)

    def _ma_filter_section(self) -> str:
        if not self.ma_filtered:
            return "## M&A FILTER — REMOVED TICKERS\n\nNone filtered.\n"
        lines = ["## M&A FILTER — REMOVED TICKERS\n",
                 "| Ticker | Reason |",
                 "|--------|--------|"]
        for t, r in self.ma_filtered.items():
            lines.append(f"| {t} | {r} |")
        lines.append("")
        return '\n'.join(lines)

    def _factor_ic_section(self) -> str:
        fic = self.parsed.get('factor_ic', [])
        if not fic:
            return "## FACTOR IC TABLE\n\nNo IC data available.\n"
        # Compute suggested weights proportional to |mean_IC|
        total_ic = sum(abs(f['mean_ic']) for f in fic) or 1
        lines = [
            "## SIGNAL ENGINE QUALITY — FACTOR IC\n",
            "| Factor | Mean IC | Verdict | Suggested Weight |",
            "|--------|---------|---------|-----------------|",
        ]
        for f in fic:
            suggested = abs(f['mean_ic']) / total_ic
            lines.append(
                f"| {f['factor']} | {f['mean_ic']:+.4f} | {f['verdict']} "
                f"| {suggested*100:.1f}% |"
            )
        lines.append("")
        return '\n'.join(lines)

    def _position_section(self, ticker: str, analysis: dict,
                          open_pos: Optional[dict] = None) -> str:
        lines = []
        rec = analysis.get('rec', 'PASS')
        price_eur = analysis.get('price_eur', 0)
        k = analysis.get('kelly', {})
        dcf = analysis.get('dcf', {})
        qs = analysis.get('quality', {})

        header = f"### {ticker} — {rec} — {_fmt_eur(price_eur)}"
        if open_pos:
            header += f"  (open: {open_pos.get('pnl_pct', 0):+.1f}%)"
        lines.append(header)
        lines.append("")

        lines.append(f"**Company:** {analysis.get('company', ticker)} | "
                     f"**Sector:** {analysis.get('sector', '—')} | "
                     f"**Industry:** {analysis.get('industry', '—')}")
        lines.append("")

        # Valuation
        lines.append("**Valuation**")
        lines.append(f"- Beta: {analysis.get('beta', '—'):.2f} | "
                     f"WACC: {analysis.get('wacc', 0)*100:.1f}% | "
                     f"Rf: {analysis.get('rf', 0)*100:.2f}%")
        if dcf.get('per_share'):
            lines.append(f"- DCF fair value: ${dcf['per_share']:,.2f} "
                         f"(upside: {analysis.get('dcf_upside_pct', 0):+.1f}%)")
        else:
            lines.append("- DCF: insufficient FCF data")

        # Quality
        lines.append("")
        lines.append(f"**Quality Score: {qs.get('total', 0)}/10** "
                     f"| ROIC–WACC spread: {qs.get('moat_spread', 0)*100:+.1f}%")

        # Kelly / Decision
        lines.append("")
        lines.append(
            f"**Kelly:** raw={k.get('raw_kelly', 0):+.3f} | "
            f"regime-adjusted={k.get('regime_adjusted', 0):+.3f} | "
            f"verdict={k.get('verdict', 'PASS')}"
        )
        lines.append(
            f"**Prob:** {analysis.get('prob', 0)*100:.0f}% | "
            f"**R:R:** {analysis.get('rr', 0):.1f} | "
            f"**Target:** {_fmt_eur(analysis.get('target_mean'))}"
        )

        # Open position extras
        if open_pos and analysis.get('trail_stop'):
            lines.append(
                f"**Trail Stop:** {_fmt_eur(analysis['trail_stop'])} "
                f"(ATR={analysis.get('atr', 0):.2f})"
            )

        # DCF sensitivity
        sens = dcf.get('sensitivity', {})
        if sens and any(v for v in sens.values() if v):
            lines.append("")
            lines.append("**DCF Sensitivity (per share)**")
            lines.append("| ΔGrowth | WACC-50bp | WACC±0 | WACC+50bp |")
            lines.append("|---------|-----------|--------|-----------|")
            for dg in [-0.01, 0, 0.01]:
                row = f"| {dg*100:+.0f}% |"
                for dw in [-0.005, 0, 0.005]:
                    v = sens.get((dg, dw))
                    row += f" {'${:,.0f}'.format(v) if v else '—'} |"
                lines.append(row)

        lines.append("")
        return '\n'.join(lines)

    def _news_section(self, tickers: list) -> str:
        lines = ["## NEWS DIGEST (MATERIAL EVENTS)\n",
                 "| Ticker | Headline | Source | Date | Sentiment | Material |",
                 "|--------|----------|--------|------|-----------|----------|"]
        count = 0
        for ticker in tickers:
            articles = self.news.get(ticker, [])
            if not articles:
                lines.append(f"| {ticker} | [news unavailable] | — | — | — | — |")
                continue
            for a in articles:
                title = a['title'][:80]
                mat = 'YES' if a['is_material'] else ''
                sent = f"{a['sentiment_score']:+.2f}"
                lines.append(
                    f"| {ticker} | {title} | {a['source']} | "
                    f"{a['published']} | {sent} | {mat} |"
                )
                count += 1
        if count == 0:
            return "## NEWS DIGEST\n\nNo recent news found.\n"
        lines.append("")
        return '\n'.join(lines)

    def _actionable_summary(self) -> tuple:
        """Returns (markdown_table_str, list_of_dicts for HTML)."""
        rows = []
        for ticker, analysis in self.analyses.items():
            rec = analysis.get('rec', 'PASS')
            price_eur = analysis.get('price_eur', 0)
            k = analysis.get('kelly', {})
            rr = analysis.get('rr', 0)
            prob = analysis.get('prob', 0)
            stop = analysis.get('stop', 0)
            target = analysis.get('target_mean', 0)
            composite_z = next(
                (s.get('composite_z', 0) for s in self.parsed['top20']
                 if s['ticker'] == ticker), 0
            )
            rows.append({
                'ticker': ticker,
                'rec': rec,
                'rec_css': rec.lower().replace(' ', ''),
                'price_eur': price_eur,
                'stop': stop,
                'target': target,
                'rr': rr,
                'prob': prob,
                'kelly_adj': k.get('regime_adjusted', 0),
                'composite_z': composite_z,
                'rank': next(
                    (s['rank'] for s in self.parsed['top20'] if s['ticker'] == ticker), 99
                ),
                # Formatted
                'entry_fmt': _fmt_eur(price_eur),
                'stop_fmt': _fmt_eur(stop),
                'target_fmt': _fmt_eur(target),
                'priority': 1 if rec in ('BUY', 'ADD', 'EXIT') else (2 if rec == 'TRIM' else 3),
            })
        rows.sort(key=lambda x: (x['priority'], -x['composite_z']))

        # Markdown table
        hdr = ("| Ticker | Action | Entry/Exit | Size (€) | Stop | Target | "
               "R:R | Prob | Kelly | Priority |\n"
               "|--------|--------|-----------|---------|------|--------|"
               "-----|------|-------|----------|\n")
        body = ""
        for r in rows:
            size_eur = self.nav * 0.02 * max(r['kelly_adj'], 0)
            body += (
                f"| {r['ticker']} | {r['rec']} | {r['entry_fmt']} | "
                f"{_fmt_eur(size_eur)} | {r['stop_fmt']} | {r['target_fmt']} | "
                f"{r['rr']:.1f} | {r['prob']*100:.0f}% | "
                f"{r['kelly_adj']:+.3f} | {'HIGH' if r['priority']==1 else 'MED'} |\n"
            )
        return hdr + body, rows

    def generate_markdown(self) -> str:
        r = self.parsed['regime']
        bt = self.parsed['backtest']
        date_str = self.parsed['run_date']

        lines = [
            f"# QUANT TRADER REPORT — {date_str}",
            f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
            f"NAV: {_fmt_eur(self.nav)}",
            "",
            self._regime_section(),
            self._ma_filter_section(),
            self._factor_ic_section(),
        ]

        # Open positions
        open_pos_map = {p['ticker']: p for p in self.parsed['open_positions']}
        open_tickers = [t for t in self.analyses if t in open_pos_map]
        new_tickers = [t for t in self.analyses if t not in open_pos_map]

        if open_tickers:
            lines.append("## OPEN POSITIONS\n")
            for ticker in open_tickers:
                lines.append(self._position_section(
                    ticker, self.analyses[ticker], open_pos_map.get(ticker)
                ))

        if new_tickers:
            lines.append("## NEW SIGNALS (POST-FILTER)\n")
            for ticker in new_tickers:
                lines.append(self._position_section(ticker, self.analyses[ticker]))

        # Actionable summary
        lines.append("## ACTIONABLE SUMMARY TABLE\n")
        table_str, _ = self._actionable_summary()
        lines.append(table_str)

        # News
        all_news_tickers = list(self.news.keys())
        lines.append(self._news_section(all_news_tickers))

        # Engineering notes
        lines.append("## ENGINEERING NOTES\n")
        fic = self.parsed.get('factor_ic', [])
        if fic:
            total_ic = sum(abs(f['mean_ic']) for f in fic) or 1
            lines.append("**Factor Reweight Suggestions** (proportional to |mean_IC|):")
            for f in sorted(fic, key=lambda x: abs(x['mean_ic']), reverse=True):
                suggested = abs(f['mean_ic']) / total_ic
                lines.append(
                    f"- `{f['factor']}`: IC={f['mean_ic']:+.4f} "
                    f"({f['verdict']}) → suggest {suggested*100:.1f}%"
                )
        lines.append("")
        if self.ma_filtered:
            lines.append("**M&A Filter applied to:** " +
                         ", ".join(f"`{t}` ({r})" for t, r in self.ma_filtered.items()))
        lines.append("")
        lines.append(
            "*All signals are informational. Review before acting. "
            "Not investment advice.*"
        )

        md = '\n'.join(lines)
        self.md_path.write_text(md)
        return str(self.md_path)

    def generate_html(self) -> str:
        from jinja2 import Environment
        env = Environment()
        env.filters['tojson'] = json.dumps
        env.filters['min'] = min

        r = self.parsed['regime']
        bt = self.parsed['backtest']
        port = self.parsed['portfolio']
        open_pos_map = {p['ticker']: p for p in self.parsed['open_positions']}
        _, summary_rows = self._actionable_summary()

        # Open positions for template
        open_pos_list = []
        for p in self.parsed['open_positions']:
            ticker = p['ticker']
            analysis = self.analyses.get(ticker, {})
            nav_pct_raw = p['value_eur'] / self.nav * 100
            rec = analysis.get('rec', 'HOLD')
            open_pos_list.append({
                'ticker': ticker,
                'avg_cost_eur_fmt': _fmt_eur(p['avg_cost_eur']),
                'current_eur_fmt': _fmt_eur(p['current_eur']),
                'pnl_eur': p['pnl_eur'],
                'pnl_eur_fmt': _fmt_eur(p['pnl_eur']),
                'pnl_pct': p['pnl_pct'],
                'pnl_pct_fmt': f"{p['pnl_pct']:+.1f}",
                'nav_pct': f"{nav_pct_raw:.1f}",
                'nav_pct_raw': nav_pct_raw,
                'stop_fmt': _fmt_eur(p.get('stop')),
                't1_fmt': _fmt_eur(p.get('t1')),
                't2_fmt': _fmt_eur(p.get('t2')),
                'action': rec,
                'action_css': rec.lower(),
            })

        # New signals for template
        new_signals = [r for r in summary_rows if r['ticker'] not in open_pos_map]

        # All news (material first)
        all_news = []
        for ticker, articles in self.news.items():
            for a in articles:
                all_news.append({'ticker': ticker, **a})
        all_news.sort(key=lambda x: (-int(x['is_material']), x.get('published', '')))
        news_items = all_news[:30]

        # Chart data — Z-scores (top 10 valid by composite_z)
        chart_tickers = sorted(
            [(t, d) for t, d in self.analyses.items()],
            key=lambda x: x[1].get('composite_z', 0), reverse=True
        )[:10]
        zscore_labels = [t for t, _ in chart_tickers]
        zscore_values = [round(d.get('composite_z', 0), 3) for _, d in chart_tickers]
        zscore_colors = [
            'rgba(34,197,94,0.6)' if v > 0 else 'rgba(239,68,68,0.6)'
            for v in zscore_values
        ]

        # Add composite_z to analyses from parsed top20
        for t, d in self.analyses.items():
            d['composite_z'] = next(
                (s['composite_z'] for s in self.parsed['top20'] if s['ticker'] == t), 0
            )

        # IC chart data
        fic = self.parsed.get('factor_ic', [])
        total_ic = sum(abs(f['mean_ic']) for f in fic) or 1
        ic_labels = [f['factor'] for f in fic]
        ic_values = [round(f['mean_ic'], 4) for f in fic]
        ic_colors = [
            'rgba(34,197,94,0.6)' if v > 0 else 'rgba(239,68,68,0.6)'
            for v in ic_values
        ]
        ic_suggested = [round(abs(f['mean_ic']) / total_ic, 4) for f in fic]

        # Factor IC for engineering table
        factor_ic_ctx = []
        for f in fic:
            factor_ic_ctx.append({
                'factor': f['factor'],
                'mean_ic': f['mean_ic'],
                'verdict': f['verdict'],
                'suggested_weight': abs(f['mean_ic']) / total_ic,
            })

        # Yield from cache or fetch
        try:
            yield_10yr = round(fetch_10yr_yield() * 100, 2)
        except Exception:
            yield_10yr = 4.30

        sharpe_val = bt.get('sharpe') or 0
        maxdd_val = bt.get('maxdd') or 0
        hitrate_val = bt.get('hitrate') or 0

        ctx = {
            'date': self.parsed['run_date'],
            'regime': r['label'],
            'regime_css': r['label'].lower(),
            'size_mult': r['size_mult'],
            'vix': r['vix'],
            'yield_10yr': yield_10yr,
            'nav_fmt': _fmt_eur(self.nav),
            'cash_pct': round(port.get('cash_pct', 0), 1),
            'open_pnl': port.get('unrealized_pnl', 0),
            'open_pnl_fmt': _fmt_eur(port.get('unrealized_pnl', 0)),
            'realized_pnl': port.get('realized_pnl', 0),
            'realized_pnl_fmt': _fmt_eur(port.get('realized_pnl', 0)),
            'sharpe': round(sharpe_val, 3),
            'maxdd': f"{maxdd_val:.1f}",
            'maxdd_num': maxdd_val,
            'hitrate': round(hitrate_val, 1),
            'hitrate_num': hitrate_val,
            'oos_period': bt.get('oos_period', '—'),
            'open_positions': open_pos_list,
            'new_signals': new_signals,
            'news_items': news_items,
            'zscore_labels': zscore_labels,
            'zscore_values': zscore_values,
            'zscore_colors': zscore_colors,
            'ic_labels': ic_labels,
            'ic_values': ic_values,
            'ic_colors': ic_colors,
            'ic_suggested': ic_suggested,
            'ma_filtered': self.ma_filtered,
            'factor_ic': factor_ic_ctx,
        }

        tmpl = env.from_string(HTML_TEMPLATE)
        html = tmpl.render(**ctx)
        self.html_path.write_text(html)
        return str(self.html_path)


# ==============================================================================
# CACHE UTILITIES
# ==============================================================================

def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        ts = datetime.fromisoformat(data.get('timestamp', '2000-01-01'))
        if datetime.now() - ts > timedelta(hours=CACHE_TTL_HOURS):
            return {}
        return data
    except Exception:
        return {}


def _save_cache(cache: dict):
    QUANT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    cache['timestamp'] = datetime.now().isoformat()
    cache['ttl_hours'] = CACHE_TTL_HOURS
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2, default=str)
    except Exception:
        pass


def _setup_logger(date_str: str) -> logging.Logger:
    QUANT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger('quant_report')
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        # File handler
        fh = logging.FileHandler(QUANT_REPORTS_DIR / f"errors_{date_str}.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        log.addHandler(fh)
        # Console handler (warnings only)
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
        log.addHandler(ch)
    return log


# ==============================================================================
# STDOUT SUMMARY
# ==============================================================================

def _print_summary(date_str: str, regime: dict, summary_rows: list,
                   ma_filtered: dict, md_path: str, html_path: str):
    vix = regime.get('vix', 0)
    print("\n" + "═" * 62)
    print(f"  QUANT REPORT — {date_str}  |  Regime: {regime['label']}  |  VIX: {vix}")
    print("═" * 62)
    print(f"  Signals filtered (M&A): {len(ma_filtered)} — "
          f"{', '.join(ma_filtered.keys()) or 'none'}")
    valid_buy = [r for r in summary_rows if r['rec'] in ('BUY', 'ADD', 'TRIM')]
    print(f"  Valid actionable:        {len(valid_buy)}")
    print()
    print("  ACTIONABLE SUMMARY:")
    header = f"  {'Ticker':<8}{'Action':<10}{'Entry/Exit':<14}{'Stop':<12}{'Target':<12}{'R:R':<6}{'Prob':<7}{'Kelly':<8}"
    print(header)
    print("  " + "-" * 70)
    for r in summary_rows[:10]:
        prob_str = f"{r['prob']*100:.0f}%"
        print(
            f"  {r['ticker']:<8}{r['rec']:<10}{r['entry_fmt']:<14}"
            f"{r['stop_fmt']:<12}{r['target_fmt']:<12}"
            f"{r['rr']:<6.1f}{prob_str:<7}{r['kelly_adj']:+.3f}"
        )
    print()
    print(f"  Report:    {md_path}")
    print(f"  Dashboard: {html_path}")
    print("═" * 62 + "\n")


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def run_quant_report(signal_report_path: str = None,
                     nav_eur: float = 50000) -> tuple:
    """
    Main pipeline:
    1. Parse latest signal report (or explicit path)
    2. M&A filter all tickers
    3. Fetch market data for valid tickers + open positions
    4. Scrape news
    5. Run quant pipeline on each ticker
    6. Generate markdown report
    7. Generate HTML dashboard
    8. Print summary to stdout
    Returns: (md_path, html_path)
    """
    date_str = datetime.now().strftime('%Y%m%d')
    log = _setup_logger(date_str)

    # ── 1. Parse signal report ──────────────────────────────────────────────
    print("  [1/7] Parsing signal report...")
    try:
        parser = SignalReportParser(signal_report_path)
        parsed = parser.parse()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        log.error(f"Signal report parse failed: {e}")
        print(f"ERROR parsing signal report: {e}")
        sys.exit(1)

    nav = parsed['portfolio'].get('nav', nav_eur) or nav_eur
    regime = parsed['regime']

    # Collect all tickers to process
    top20_tickers = [s['ticker'] for s in parsed['top20']]
    open_tickers = [p['ticker'] for p in parsed['open_positions']]
    all_tickers = list(dict.fromkeys(top20_tickers + open_tickers))  # preserve order, dedup

    print(f"       Parsed: {len(top20_tickers)} signals, {len(open_tickers)} open positions")
    print(f"       Regime: {regime['label']} | VIX: {regime['vix']} | Size: {regime['size_mult']}x")

    # ── 2. Load / init cache ────────────────────────────────────────────────
    cache = _load_cache()

    # ── 3. Fetch market data + M&A filter ───────────────────────────────────
    print(f"  [2/7] Fetching market data for {len(all_tickers)} tickers...")
    fetcher = MarketDataFetcher(cache, log)
    valid_data, ma_filtered = fetcher.fetch_all(all_tickers)

    # Always include open positions even if M&A flagged (override for clarity)
    for ticker in open_tickers:
        if ticker in ma_filtered:
            log.warning(f"[{ticker}] is in open positions but flagged by M&A filter: "
                        f"{ma_filtered[ticker]} — including anyway")
            info = fetcher.fetch(ticker) or {}
            valid_data[ticker] = info
            del ma_filtered[ticker]

    if not valid_data:
        print("WARNING: All tickers filtered — generating regime/open-positions-only report.")

    print(f"       Valid: {len(valid_data)} | Filtered: {len(ma_filtered)}")

    # ── 4. Scrape news (top-5 + open positions) ────────────────────────────
    print("  [3/7] Scraping news...")
    scraper = NewsScraper(cache, log)
    news_tickers = top20_tickers[:5] + [t for t in open_tickers if t not in top20_tickers[:5]]
    news: dict = {}
    for ticker in news_tickers:
        info = valid_data.get(ticker, {})
        company_name = info.get('longName', ticker)
        news[ticker] = scraper.scrape(ticker, company_name)

    # ── 5. Fetch financials + run quant pipeline ────────────────────────────
    print("  [4/7] Running quant analysis pipeline...")
    pipeline = QuantAnalysisPipeline(nav, regime, log)
    open_pos_map = {p['ticker']: p for p in parsed['open_positions']}
    analyses: dict = {}

    for ticker, info in valid_data.items():
        try:
            fins = fetcher.fetch_financials(ticker)
            analysis = pipeline.analyze(
                ticker, info, fins,
                signal_data=next(
                    (s for s in parsed['top20'] if s['ticker'] == ticker), None
                ),
                open_pos=open_pos_map.get(ticker),
            )
            analyses[ticker] = analysis
        except Exception as e:
            log.error(f"[{ticker}] pipeline error: {e}")

    # ── 6 + 7. Generate reports ─────────────────────────────────────────────
    print("  [5/7] Generating markdown report...")
    gen = ReportGenerator(parsed, analyses, ma_filtered, news, nav, log)
    md_path = gen.generate_markdown()

    print("  [6/7] Generating HTML dashboard...")
    html_path = gen.generate_html()

    # ── Save cache ───────────────────────────────────────────────────────────
    _save_cache(cache)

    # ── 8. Print summary ────────────────────────────────────────────────────
    print("  [7/7] Complete.")
    _, summary_rows = gen._actionable_summary()
    _print_summary(
        date_str=parsed['run_date'],
        regime=regime,
        summary_rows=summary_rows,
        ma_filtered=ma_filtered,
        md_path=md_path,
        html_path=html_path,
    )

    return md_path, html_path


# ==============================================================================
# CLI
# ==============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Quant Report Generator — Signal Engine v1'
    )
    parser.add_argument(
        '--report', type=str, default=None,
        help='Path to signal report .txt (default: latest in signal_reports/)'
    )
    parser.add_argument(
        '--nav', type=float, default=50000,
        help='Portfolio NAV in EUR (default: 50000, or auto-read from report)'
    )
    args = parser.parse_args()

    print("\n" + "=" * 62)
    print(f"  QUANT REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 62)

    md_path, html_path = run_quant_report(
        signal_report_path=args.report,
        nav_eur=args.nav,
    )


if __name__ == '__main__':
    main()
