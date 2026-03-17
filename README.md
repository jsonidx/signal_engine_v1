# Weekly Signal Engine v1.0

## Multi-Factor Equity Screener + Crypto Trend/Momentum Signal Generator

A systematic screening tool designed for weekly (Sunday evening) signal generation with sub-€50K portfolios. Two independent modules under one risk framework.

---

## ⚠️ IMPORTANT DISCLAIMERS

- **This is NOT investment advice.** All signals are informational and for research/educational purposes only.
- **No automated execution.** This tool generates signals — YOU decide whether and how to act.
- **Consult a licensed financial advisor** before making investment decisions.
- **Past performance does not predict future results.**
- **Yahoo Finance data** is used for prototyping. For production use, consider Bloomberg, Refinitiv, or Polygon.io.

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Your Universe

Edit `config.py`:
- Add tickers to `CUSTOM_WATCHLIST`
- Adjust `PORTFOLIO_NAV` to your actual portfolio size
- Tune factor weights in `EQUITY_FACTORS` if you have conviction
- Set `CRYPTO_TICKERS` to the coins you want to track

### 3. Run

```bash
# Full run — both equity and crypto modules
python signal_engine.py

# Equity screener only
python signal_engine.py --equity-only

# Crypto signals only
python signal_engine.py --crypto-only

# Add custom tickers on the fly
python signal_engine.py --watchlist PLTR,SOFI,RIVN,COIN

# Override portfolio size
python signal_engine.py --nav 30000
```

### 4. Review Output

- Console output shows ranked signals + position sizing
- CSV files are saved to `./signals_output/` with date stamps
- Files generated:
  - `equity_signals_YYYYMMDD.csv` — Full factor scores for all equities
  - `crypto_signals_YYYYMMDD.csv` — Trend/momentum scores for all crypto
  - `equity_positions_YYYYMMDD.csv` — Recommended equity allocations
  - `crypto_positions_YYYYMMDD.csv` — Recommended crypto allocations

---

## Signal Methodology

### Equity Multi-Factor Composite

| Factor | Weight | Logic |
|---|---|---|
| 12-1 Month Momentum | 35% | Jegadeesh-Titman: 12m return, skip last month |
| 6-1 Month Momentum | 20% | Medium-term momentum confirmation |
| 5-Day Mean Reversion | 15% | Short-term contrarian (inverted) |
| Low Volatility | 15% | Quality proxy — lower vol scores higher |
| Risk-Adjusted Momentum | 15% | Momentum / Volatility (Sharpe-like) |

All factors are Z-scored cross-sectionally and winsorized at ±3σ.

### Crypto Trend/Momentum

| Component | Weight | Logic |
|---|---|---|
| Trend Score | 40% | Price vs 21/50/200 EMA stack |
| Multi-Period Momentum | 30% | Weighted ROC: 7d/14d/30d/60d |
| RSI Timing | 15% | Oversold = better entry |
| Trend Confirmation | 15% | Bonus when trend + momentum agree |

**Volatility Regime Filter:**
- Normal (< 80% ann. vol): Full position
- High (80-120% ann. vol): Half position
- Extreme (> 120% ann. vol): Zero position — cash

### Position Sizing

- Quarter-Kelly (conservative)
- Inverse-volatility weighted within the selected positions
- Hard caps: 8% per equity, 10% per crypto
- Minimum position size: €500 (below this, frictional costs dominate)

---

## Recommended Weekly Workflow

1. **Sunday evening:** Run `python signal_engine.py`
2. **Review signals:** Check the top-ranked equities and crypto BUY signals
3. **Cross-reference:** Validate against your own thesis / news / catalysts
4. **Monday morning:** Execute any changes through your broker
5. **Log decisions:** Track what you bought/sold and WHY (for future review)

---

## What This Tool Does NOT Do

- ❌ Automatically place trades
- ❌ Monitor positions in real-time
- ❌ Account for your tax situation
- ❌ Guarantee any level of returns
- ❌ Replace professional financial advice
- ❌ Use point-in-time fundamental data (price-only signals)

---

## Known Limitations & Honest Caveats

1. **Survivorship bias:** The equity universe is defined as of today. Stocks that delisted or went bankrupt are not in the sample. This flatters historical signal quality.
2. **Yahoo Finance data quality:** Occasional gaps, missing adjustments for some EU tickers. Validate any signal that looks anomalous.
3. **No fundamental data:** All signals are price-based. Value and quality signals derived from price (low-vol proxy) are weaker than those using proper accounting data.
4. **Transaction costs are estimates.** Your actual costs depend on your broker, order type, and execution timing.
5. **Crypto signals during regime transitions** are noisy. The trend model will whipsaw during range-bound markets. This is inherent to trend-following.

---

## Extending the Engine

### Adding a New Equity Factor

1. Add the factor config to `EQUITY_FACTORS` in `config.py`
2. Implement the computation function in `signal_engine.py`
3. Add the Z-scored column to the composite calculation
4. Ensure all weights sum to 1.0

### Connecting to a Broker API

The signal output (CSV or DataFrame) can be fed into:
- **Interactive Brokers** — via `ib_insync` Python library
- **Alpaca** — via their REST API
- **QuantConnect** — upload the signal logic as a LEAN algorithm

This requires additional engineering and is NOT included in this tool.

---

## License

For personal, non-commercial use only. No warranty expressed or implied.
