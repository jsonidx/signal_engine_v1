#!/usr/bin/env python3
"""
================================================================================
AI QUANT ANALYST v1.0 — Claude-Powered Signal Synthesis
================================================================================
Uses claude-opus-4-6 with adaptive thinking to analyze aggregated signals for
a ticker and produce a structured quant thesis.

WHAT IT DOES:
    1. Gathers all available signals: technical, options flow, fundamentals,
       SEC filings, congressional trades, social sentiment, polymarket
    2. Sends structured signal packet to Claude API
    3. Returns quant thesis: direction, conviction, entry/stop/target,
       position size, catalysts, risks, time horizon

OUTPUT STRUCTURE (per ticker):
    - Direction    : BULL | BEAR | NEUTRAL
    - Conviction   : 1-5 (1=weak, 5=high)
    - Entry range  : price levels
    - Stop loss    : invalidation level
    - Target       : price target(s)
    - Position %   : suggested allocation of portfolio slice
    - Catalysts    : 3 top supporting factors
    - Risks        : 3 top risk factors
    - Time horizon : days/weeks/months
    - Thesis       : 2-3 sentence narrative

USAGE:
    python3 ai_quant.py --ticker COIN          # Single ticker analysis
    python3 ai_quant.py --tickers COIN GME AI  # Multiple tickers
    python3 ai_quant.py --watchlist            # All TIER 1 + TIER 2 tickers
    python3 ai_quant.py --report <file>        # Analyze existing report file
    python3 ai_quant.py --ticker COIN --raw    # Show raw Claude response

REQUIREMENTS:
    pip install anthropic
    export ANTHROPIC_API_KEY="your-key"

NOTE: Costs ~$0.02-0.04 per ticker with adaptive thinking on Opus 4.6.
      Watchlist of 10 tickers + portfolio briefing ≈ $0.40-0.50 per run.

IMPORTANT: This is NOT investment advice. Claude is analyzing the same
           signals you have — it doesn't have secret alpha. Use as a
           structured second opinion, not gospel.
================================================================================
"""

import argparse
import json
import os
import sys
import time
import warnings
from datetime import datetime
from typing import Dict, List, Optional

warnings.filterwarnings("ignore")

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed.")
    print("       Run: pip install anthropic")
    sys.exit(1)

try:
    from config import OUTPUT_DIR, PORTFOLIO_NAV, CRYPTO_ALLOCATION, EQUITY_ALLOCATION
except ImportError:
    OUTPUT_DIR = "./signals_output"
    PORTFOLIO_NAV = 50_000
    CRYPTO_ALLOCATION = 0.25
    EQUITY_ALLOCATION = 0.65


# ==============================================================================
# SECTION 1: SIGNAL COLLECTION
# ==============================================================================

def _read_watchlist_tickers(tier_filter: Optional[List[str]] = None) -> List[str]:
    """Parse watchlist.txt. tier_filter=['TIER 1','TIER 2'] restricts tiers."""
    paths = [
        os.path.join(os.path.dirname(__file__), "watchlist.txt"),
        "./watchlist.txt",
    ]
    for path in paths:
        if os.path.exists(path):
            tickers = []
            current_tier = None
            with open(path) as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    # Section header detection
                    upper = stripped.upper()
                    if "TIER 1" in upper:
                        current_tier = "TIER 1"
                        continue
                    elif "TIER 2" in upper:
                        current_tier = "TIER 2"
                        continue
                    elif "TIER 3" in upper:
                        current_tier = "TIER 3"
                        continue
                    elif "MANUALLY ADDED" in upper:
                        current_tier = "MANUALLY ADDED"
                        continue
                    if stripped.startswith("#"):
                        continue
                    ticker = stripped.split("#")[0].strip().upper()
                    if not ticker:
                        continue
                    if tier_filter is None or current_tier in tier_filter:
                        tickers.append(ticker)
            return tickers
    return []


def _collect_technical_signals(ticker: str) -> dict:
    """Pull basic price/volume technical signals via yfinance."""
    try:
        import yfinance as yf
        import numpy as np

        t = yf.Ticker(ticker)
        hist = t.history(period="1y")
        if hist.empty:
            return {}

        close = hist["Close"]
        volume = hist["Volume"]
        price = float(close.iloc[-1])

        # Moving averages
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])

        # RSI (14)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = float((100 - 100 / (1 + rs)).iloc[-1])

        # Momentum
        mom_1m = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) > 21 else 0
        mom_3m = float((close.iloc[-1] / close.iloc[-63] - 1) * 100) if len(close) > 63 else 0
        mom_6m = float((close.iloc[-1] / close.iloc[-126] - 1) * 100) if len(close) > 126 else 0

        # Volume trend
        vol_5d_avg = float(volume.iloc[-5:].mean())
        vol_20d_avg = float(volume.iloc[-21:-1].mean())
        vol_ratio = vol_5d_avg / vol_20d_avg if vol_20d_avg > 0 else 1.0

        # 52w range
        high_52w = float(close.rolling(252).max().iloc[-1])
        low_52w = float(close.rolling(252).min().iloc[-1])
        pct_from_high = (price - high_52w) / high_52w * 100 if high_52w > 0 else 0
        pct_from_low = (price - low_52w) / low_52w * 100 if low_52w > 0 else 0

        # Trend assessment
        above_ma200 = price > ma200
        above_ma50 = price > ma50
        above_ma20 = price > ma20

        return {
            "price": round(price, 2),
            "ma20": round(ma20, 2),
            "ma50": round(ma50, 2),
            "ma200": round(ma200, 2),
            "above_ma200": above_ma200,
            "above_ma50": above_ma50,
            "above_ma20": above_ma20,
            "rsi_14": round(rsi, 1),
            "momentum_1m_pct": round(mom_1m, 1),
            "momentum_3m_pct": round(mom_3m, 1),
            "momentum_6m_pct": round(mom_6m, 1),
            "volume_ratio_5d_vs_20d": round(vol_ratio, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "pct_from_52w_high": round(pct_from_high, 1),
            "pct_from_52w_low": round(pct_from_low, 1),
        }
    except Exception:
        return {}


def _collect_fundamental_signals(ticker: str) -> dict:
    """Pull fundamental data from fundamental_analysis module if available."""
    try:
        from fundamental_analysis import fetch_fundamentals, score_fundamentals
        raw = fetch_fundamentals(ticker)
        if raw is None:
            return {}
        scores = score_fundamentals(raw)
        composite = scores.get("composite_score", 0)
        return {
            "fundamental_score_pct": round(composite, 1),
            "valuation_score": scores.get("valuation", 0),
            "growth_score": scores.get("growth", 0),
            "quality_score": scores.get("quality", 0),
            "pe_ratio": raw.get("pe_trailing"),
            "forward_pe": raw.get("pe_forward"),
            "revenue_growth_yoy": raw.get("revenue_growth_yoy"),
            "eps_growth_yoy": raw.get("eps_growth_yoy"),
            "gross_margin": raw.get("gross_margin"),
            "next_earnings_days": raw.get("earnings_days_away"),
            "analyst_rating": raw.get("analyst_rating"),
            "analyst_price_target": raw.get("analyst_price_target"),
            "analyst_upside_pct": raw.get("analyst_upside_pct"),
        }
    except Exception:
        return {}


def _collect_options_signals(ticker: str) -> dict:
    """Pull options flow data from options_flow module."""
    try:
        from options_flow import get_options_heat
        return get_options_heat(ticker)
    except Exception:
        return {}


def _collect_congress_signals(ticker: str) -> dict:
    """Pull congressional trade signal."""
    try:
        from congress_trades import score_congress_signal, get_all_trades
        trades = get_all_trades()
        result = score_congress_signal(ticker, trades)
        if not result:
            return {}
        return {
            "congress_score": result.get("score", 0),
            "congress_direction": result.get("direction", "neutral"),
            "congress_trade_count": result.get("trade_count", 0),
            "congress_notable_traders": result.get("notable_traders", []),
            "congress_recent_trades": result.get("recent_trades", [])[:3],
        }
    except Exception:
        return {}


def _collect_polymarket_signals(ticker: str) -> dict:
    """Pull Polymarket prediction market signal."""
    try:
        from polymarket_screener import PolymarketScreener
        screener = PolymarketScreener()
        result = screener.extract_signal(ticker)
        if not result or result.get("signal_score", 0) == 0:
            return {}
        return {
            "polymarket_score": result.get("signal_score", 0),
            "polymarket_direction": result.get("direction", "neutral"),
            "polymarket_market": result.get("question", ""),
            "polymarket_probability": result.get("probability", 0),
            "polymarket_volume_24h": result.get("volume_24h", 0),
        }
    except Exception:
        return {}


def collect_all_signals(ticker: str, verbose: bool = False) -> dict:
    """
    Collect all available signals for a ticker.
    Returns structured dict for Claude prompt.
    """
    ticker = ticker.upper().strip()
    signals = {"ticker": ticker, "timestamp": datetime.now().isoformat()}

    if verbose:
        print(f"  [{ticker}] Collecting signals...")

    if verbose:
        print(f"  [{ticker}]   → technical...", end=" ", flush=True)
    tech = _collect_technical_signals(ticker)
    signals["technical"] = tech
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → fundamentals...", end=" ", flush=True)
    fund = _collect_fundamental_signals(ticker)
    signals["fundamentals"] = fund
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → options flow...", end=" ", flush=True)
    opts = _collect_options_signals(ticker)
    signals["options_flow"] = opts
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → congress...", end=" ", flush=True)
    cong = _collect_congress_signals(ticker)
    signals["congress"] = cong
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → polymarket...", end=" ", flush=True)
    poly = _collect_polymarket_signals(ticker)
    signals["polymarket"] = poly
    if verbose:
        print("done")

    return signals


# ==============================================================================
# SECTION 2: PROMPT CONSTRUCTION
# ==============================================================================

SYSTEM_PROMPT = """You are a senior quant analyst at a top-tier hedge fund.
You think rigorously, quantitatively, and concisely. You have expertise in:
- Technical analysis and momentum factors
- Options market microstructure and flow interpretation
- Fundamental valuation across sectors
- Event-driven catalysts (earnings, regulatory, insider activity)
- Risk management and position sizing

Your task: analyze a structured signal packet for a stock and produce
a precise, actionable quant thesis. Be specific about price levels.
Always flag if signal data is thin or contradictory.
Do not hedge everything — give a clear directional view with conviction.

Output MUST be in JSON format with this exact structure:
{
  "ticker": "...",
  "direction": "BULL|BEAR|NEUTRAL",
  "conviction": 1-5,
  "time_horizon": "X days|X weeks|X months",
  "entry_low": price,
  "entry_high": price,
  "stop_loss": price,
  "target_1": price,
  "target_2": price (or null),
  "position_size_pct": 0-100 (percent of allocated crypto/equity slice),
  "catalysts": ["...", "...", "..."],
  "risks": ["...", "...", "..."],
  "thesis": "2-3 sentence narrative",
  "data_quality": "HIGH|MEDIUM|LOW",
  "notes": "any caveats or data gaps"
}"""


def _build_prompt(signals: dict) -> str:
    """Build the analysis prompt from collected signals."""
    ticker = signals["ticker"]
    tech = signals.get("technical", {})
    fund = signals.get("fundamentals", {})
    opts = signals.get("options_flow", {})
    cong = signals.get("congress", {})
    poly = signals.get("polymarket", {})

    prompt_parts = [
        f"Analyze {ticker} using the following signal data collected on {datetime.now().strftime('%Y-%m-%d')}.",
        "",
        "## TECHNICAL SIGNALS",
    ]

    if tech:
        price = tech.get("price", "N/A")
        prompt_parts += [
            f"Price: ${price}",
            f"RSI(14): {tech.get('rsi_14', 'N/A')}",
            f"Trend: {'above' if tech.get('above_ma200') else 'below'} 200MA (${tech.get('ma200', 'N/A')}), "
            f"{'above' if tech.get('above_ma50') else 'below'} 50MA (${tech.get('ma50', 'N/A')})",
            f"Momentum: 1M={tech.get('momentum_1m_pct', 'N/A')}%, 3M={tech.get('momentum_3m_pct', 'N/A')}%, "
            f"6M={tech.get('momentum_6m_pct', 'N/A')}%",
            f"Volume ratio (5d/20d): {tech.get('volume_ratio_5d_vs_20d', 'N/A')}x",
            f"52w range: ${tech.get('low_52w', 'N/A')} - ${tech.get('high_52w', 'N/A')} "
            f"(currently {tech.get('pct_from_52w_high', 'N/A')}% from high)",
        ]
    else:
        prompt_parts.append("Technical data: unavailable")

    prompt_parts += ["", "## OPTIONS FLOW"]
    if opts:
        prompt_parts += [
            f"Heat score: {opts.get('heat_score', 'N/A')}/100",
            f"Options direction: {opts.get('direction', 'N/A')}",
            f"Expected move ({opts.get('days_to_exp', '?')}d): {opts.get('expected_move_pct', 'N/A')}%",
            f"Implied vol: {opts.get('implied_vol_pct', 'N/A')}%",
            f"IV rank: {opts.get('iv_rank', 'N/A')}%",
            f"Put/call ratio: {opts.get('pc_ratio', 'N/A')}",
            f"Total options volume: {opts.get('total_options_vol', 'N/A'):,}" if isinstance(opts.get('total_options_vol'), int) else f"Total options volume: {opts.get('total_options_vol', 'N/A')}",
            f"Straddle cost: ${opts.get('straddle_cost', 'N/A')}",
        ]
    else:
        prompt_parts.append("Options data: unavailable (possibly crypto or thin options)")

    prompt_parts += ["", "## FUNDAMENTAL SIGNALS"]
    if fund:
        prompt_parts += [
            f"Fundamental score: {fund.get('fundamental_score_pct', 'N/A')}%",
            f"P/E (trailing): {fund.get('pe_ratio', 'N/A')}",
            f"P/E (forward): {fund.get('forward_pe', 'N/A')}",
            f"Revenue growth YoY: {fund.get('revenue_growth_yoy', 'N/A')}",
            f"EPS growth YoY: {fund.get('eps_growth_yoy', 'N/A')}",
            f"Gross margin: {fund.get('gross_margin', 'N/A')}",
            f"Next earnings: {fund.get('next_earnings_days', 'N/A')} days away",
            f"Analyst consensus: {fund.get('analyst_rating', 'N/A')} | "
            f"Target: ${fund.get('analyst_price_target', 'N/A')} "
            f"({fund.get('analyst_upside_pct', 'N/A')}% upside)",
        ]
    else:
        prompt_parts.append("Fundamental data: unavailable")

    prompt_parts += ["", "## CONGRESSIONAL TRADES"]
    if cong:
        traders = cong.get("congress_notable_traders", [])
        trades = cong.get("congress_recent_trades", [])
        prompt_parts += [
            f"Congress signal score: {cong.get('congress_score', 'N/A')}/100",
            f"Direction: {cong.get('congress_direction', 'N/A')}",
            f"Trade count (recent): {cong.get('congress_trade_count', 'N/A')}",
            f"Notable traders: {', '.join(traders) if traders else 'None'}",
        ]
        if trades:
            prompt_parts.append("Recent trades:")
            for trade in trades[:3]:
                if isinstance(trade, dict):
                    prompt_parts.append(
                        f"  - {trade.get('politician', '?')}: {trade.get('type', '?')} "
                        f"${trade.get('amount', '?')} on {trade.get('date', '?')}"
                    )
    else:
        prompt_parts.append("Congressional trade data: none / unavailable")

    prompt_parts += ["", "## POLYMARKET PREDICTION MARKETS"]
    if poly:
        prompt_parts += [
            f"Polymarket score: {poly.get('polymarket_score', 'N/A')}/5",
            f"Direction: {poly.get('polymarket_direction', 'N/A')}",
            f"Market: \"{poly.get('polymarket_market', 'N/A')}\"",
            f"Probability: {poly.get('polymarket_probability', 'N/A')}",
            f"24h volume: ${poly.get('polymarket_volume_24h', 'N/A'):,}" if isinstance(poly.get('polymarket_volume_24h'), (int, float)) else f"24h volume: {poly.get('polymarket_volume_24h', 'N/A')}",
        ]
    else:
        prompt_parts.append("Polymarket data: no relevant markets found")

    # Portfolio context
    prompt_parts += [
        "",
        "## PORTFOLIO CONTEXT",
        f"Portfolio NAV: ${PORTFOLIO_NAV:,}",
        f"Equity allocation: {EQUITY_ALLOCATION*100:.0f}% | Crypto allocation: {CRYPTO_ALLOCATION*100:.0f}%",
        "",
        "## TASK",
        f"Produce a quant thesis for {ticker} in the required JSON format.",
        "Position size % = percent of the relevant allocation slice (equity or crypto).",
        "Be specific about price levels based on the technical data provided.",
        "If data is contradictory or thin, reflect that in conviction score and data_quality.",
    ]

    return "\n".join(prompt_parts)


# ==============================================================================
# SECTION 3: CLAUDE API CALL
# ==============================================================================

def _call_claude(prompt: str, verbose: bool = False) -> Optional[str]:
    """
    Call claude-opus-4-6 with adaptive thinking and streaming.
    Returns the response text, or None on failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("         export ANTHROPIC_API_KEY='your-key-here'")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    if verbose:
        print("  Calling Claude API (opus-4-6 + adaptive thinking)...", flush=True)

    full_text = ""
    thinking_shown = False

    try:
        # Use streaming to handle long responses and avoid timeouts
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    if hasattr(event, "content_block"):
                        if event.content_block.type == "thinking" and verbose and not thinking_shown:
                            print("  [thinking...]", flush=True)
                            thinking_shown = True
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta"):
                        if event.delta.type == "text_delta":
                            full_text += event.delta.text

        return full_text.strip() if full_text else None

    except anthropic.AuthenticationError:
        print("  ERROR: Invalid ANTHROPIC_API_KEY.")
        return None
    except anthropic.RateLimitError:
        print("  ERROR: Claude API rate limit hit. Wait and retry.")
        return None
    except anthropic.APIStatusError as e:
        print(f"  ERROR: Claude API error {e.status_code}: {e.message}")
        return None
    except Exception as e:
        print(f"  ERROR: Unexpected error calling Claude: {e}")
        return None


def _parse_response(raw: str) -> Optional[dict]:
    """Extract JSON from Claude's response."""
    if not raw:
        return None

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON block
    import re
    patterns = [
        r"```json\s*([\s\S]+?)\s*```",
        r"```\s*([\s\S]+?)\s*```",
        r"(\{[\s\S]+\})",
    ]
    for pattern in patterns:
        m = re.search(pattern, raw)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue

    return None


# ==============================================================================
# SECTION 4: ANALYSIS PIPELINE
# ==============================================================================

def analyze_ticker(ticker: str, verbose: bool = False, raw_output: bool = False) -> Optional[dict]:
    """
    Full AI quant analysis for one ticker.
    Returns parsed thesis dict, or None on failure.
    """
    ticker = ticker.upper().strip()
    print(f"\n  Analyzing {ticker}...")

    # Collect signals
    signals = collect_all_signals(ticker, verbose=verbose)

    # Build prompt
    prompt = _build_prompt(signals)

    if verbose and raw_output:
        print("\n--- PROMPT ---")
        print(prompt)
        print("--- END PROMPT ---\n")

    # Call Claude
    raw = _call_claude(prompt, verbose=verbose)
    if raw is None:
        return None

    if raw_output:
        print("\n--- RAW CLAUDE RESPONSE ---")
        print(raw)
        print("--- END RESPONSE ---\n")

    # Parse response
    thesis = _parse_response(raw)
    if thesis is None:
        print(f"  WARNING: Could not parse JSON from Claude response for {ticker}")
        print(f"  Raw response: {raw[:500]}...")
        return None

    thesis["ticker"] = ticker
    thesis["signals"] = signals
    thesis["raw_response"] = raw

    return thesis


def analyze_tickers(tickers: List[str], verbose: bool = False,
                    raw_output: bool = False) -> List[dict]:
    """Analyze multiple tickers, returning sorted by conviction."""
    results = []
    for i, ticker in enumerate(tickers, 1):
        print(f"\n[{i}/{len(tickers)}] {ticker}")
        result = analyze_ticker(ticker, verbose=verbose, raw_output=raw_output)
        if result:
            results.append(result)
        time.sleep(1)  # Brief pause between API calls

    # Sort by conviction desc, then direction (BULL first)
    def sort_key(r):
        direction_order = {"BULL": 0, "NEUTRAL": 1, "BEAR": 2}
        return (
            -(r.get("conviction", 0)),
            direction_order.get(r.get("direction", "NEUTRAL"), 1),
        )

    return sorted(results, key=sort_key)


# ==============================================================================
# SECTION 5: PRINTING
# ==============================================================================

DIRECTION_ICON = {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "🟡"}
CONVICTION_BARS = {1: "▪░░░░", 2: "▪▪░░░", 3: "▪▪▪░░", 4: "▪▪▪▪░", 5: "▪▪▪▪▪"}


def print_thesis(t: dict) -> None:
    """Print formatted thesis for one ticker."""
    if not t:
        return

    ticker = t.get("ticker", "?")
    direction = t.get("direction", "NEUTRAL")
    conviction = t.get("conviction", 0)
    icon = DIRECTION_ICON.get(direction, "◯")
    bars = CONVICTION_BARS.get(conviction, "?????")

    print()
    print(f"  ┌─ {ticker} {'─'*(54-len(ticker))}┐")
    print(f"  │  {icon} {direction:<8}  Conviction: {conviction}/5 {bars}          │")
    print(f"  │  Horizon: {str(t.get('time_horizon','?')):<10}  Data quality: {t.get('data_quality','?'):<6}    │")
    print(f"  └{'─'*58}┘")
    print()

    # Price levels
    entry_low = t.get("entry_low")
    entry_high = t.get("entry_high")
    stop = t.get("stop_loss")
    t1 = t.get("target_1")
    t2 = t.get("target_2")
    pos_pct = t.get("position_size_pct")

    if entry_low and entry_high:
        print(f"  Entry:     ${entry_low:.2f} – ${entry_high:.2f}")
    if stop:
        print(f"  Stop:      ${stop:.2f}")
    if t1:
        t2_str = f" → ${t2:.2f}" if t2 else ""
        print(f"  Target:    ${t1:.2f}{t2_str}")
    if pos_pct is not None:
        print(f"  Size:      {pos_pct:.0f}% of allocation slice")

    # R/R ratio
    if entry_high and stop and t1:
        try:
            risk = entry_high - stop
            reward = t1 - entry_high
            if risk > 0:
                rr = reward / risk
                print(f"  R/R:       {rr:.1f}x")
        except Exception:
            pass

    print()

    # Thesis
    print(f"  Thesis: {t.get('thesis', 'N/A')}")
    print()

    # Catalysts
    catalysts = t.get("catalysts", [])
    if catalysts:
        print("  Catalysts:")
        for c in catalysts[:3]:
            print(f"    ✓ {c}")

    # Risks
    risks = t.get("risks", [])
    if risks:
        print("  Risks:")
        for r in risks[:3]:
            print(f"    ✗ {r}")

    if t.get("notes"):
        print(f"\n  Notes: {t['notes']}")

    print()


def print_summary_table(results: List[dict]) -> None:
    """Print compact summary of all analyzed tickers."""
    if not results:
        return

    print()
    print("AI QUANT — SUMMARY TABLE")
    print("=" * 80)
    print(f"  {'TICKER':<8} {'DIR':<7} {'CONV':>5}  {'ENTRY':>8}  {'STOP':>8}  {'TARGET':>8}  {'SIZE%':>6}  {'HORIZON'}")
    print("  " + "-" * 74)

    for t in results:
        icon = DIRECTION_ICON.get(t.get("direction", "NEUTRAL"), "◯")
        entry = f"${t['entry_low']:.2f}" if t.get("entry_low") else "   N/A"
        stop = f"${t['stop_loss']:.2f}" if t.get("stop_loss") else "   N/A"
        target = f"${t['target_1']:.2f}" if t.get("target_1") else "   N/A"
        size = f"{t['position_size_pct']:.0f}%" if t.get("position_size_pct") is not None else "  N/A"
        conv = t.get("conviction", 0)
        print(
            f"  {t['ticker']:<8} {icon} {t.get('direction','?'):<5} "
            f"{conv:>5}  {entry:>8}  {stop:>8}  {target:>8}  {size:>6}  "
            f"{t.get('time_horizon','?')}"
        )

    print()


def print_full_report(results: List[dict]) -> None:
    """Print complete AI quant analysis."""
    print()
    print("================================================================")
    print("  AI QUANT ANALYSIS — POWERED BY CLAUDE OPUS 4.6")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("================================================================")

    if not results:
        print("  No results.")
        return

    print_summary_table(results)

    print("─" * 62)
    print("  DETAILED THESES")
    print("─" * 62)

    for t in results:
        print_thesis(t)
        print()

    # Portfolio allocation summary
    bulls = [r for r in results if r.get("direction") == "BULL"]
    bears = [r for r in results if r.get("direction") == "BEAR"]
    neutrals = [r for r in results if r.get("direction") == "NEUTRAL"]

    print("─" * 62)
    print("  PORTFOLIO SIGNAL SUMMARY")
    print("─" * 62)
    print(f"  Bull:    {len(bulls)} ticker(s): {', '.join(r['ticker'] for r in bulls)}")
    print(f"  Bear:    {len(bears)} ticker(s): {', '.join(r['ticker'] for r in bears)}")
    print(f"  Neutral: {len(neutrals)} ticker(s): {', '.join(r['ticker'] for r in neutrals)}")

    high_conv = [r for r in results if r.get("conviction", 0) >= 4]
    if high_conv:
        print()
        print("  High conviction (4-5/5):")
        for r in high_conv:
            icon = DIRECTION_ICON.get(r.get("direction", "NEUTRAL"), "◯")
            print(f"    {icon} {r['ticker']} — {r.get('thesis','')[:80]}...")
    print()


# ==============================================================================
# SECTION 6: REPORT FILE ANALYSIS
# ==============================================================================

def analyze_report_file(report_path: str, verbose: bool = False) -> Optional[str]:
    """
    Send an existing signal report file to Claude for portfolio-level analysis.
    Useful for analyzing the output of run_master.sh.
    """
    if not os.path.exists(report_path):
        print(f"  ERROR: Report file not found: {report_path}")
        return None

    with open(report_path) as f:
        content = f.read()

    # Truncate if very long (keep first 80k chars to stay within context)
    max_chars = 80_000
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n[... report truncated at {max_chars} chars ...]"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ERROR: ANTHROPIC_API_KEY not set.")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    system = """You are a senior portfolio manager and quant analyst at a hedge fund.
Analyze the provided weekly signal report and give a structured portfolio briefing:

1. TOP 3 HIGHEST CONVICTION IDEAS — with thesis, entry, stop, target, sizing
2. KEY RISKS THIS WEEK — macro, sector, position-specific
3. PORTFOLIO POSITIONING — recommended adjustments
4. WATCHLIST PRIORITIES — which tickers deserve immediate deep dive
5. SIGNALS TO IGNORE — what's noise in this report

Be direct, specific, and quantitative. Use actual price levels from the data."""

    prompt = f"""Analyze this weekly signal report for my portfolio:\n\n{content}"""

    print(f"  Sending report to Claude ({len(content):,} chars)...")

    full_response = ""
    try:
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    if hasattr(event, "delta") and event.delta.type == "text_delta":
                        full_response += event.delta.text
                        if verbose:
                            print(event.delta.text, end="", flush=True)

        return full_response.strip()

    except Exception as e:
        print(f"  ERROR: {e}")
        return None


# ==============================================================================
# SECTION 7: CLI
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="AI Quant Analyst — Claude-powered signal synthesis"
    )
    parser.add_argument("--ticker", type=str, help="Single ticker analysis")
    parser.add_argument("--tickers", nargs="+", help="Multiple tickers")
    parser.add_argument("--watchlist", action="store_true", help="TIER 1 + TIER 2 watchlist")
    parser.add_argument("--tier1-only", action="store_true", help="TIER 1 watchlist only")
    parser.add_argument("--report", type=str, help="Analyze existing signal report file")
    parser.add_argument("--raw", action="store_true", help="Show raw Claude response")
    parser.add_argument("--verbose", action="store_true", help="Show collection progress")
    args = parser.parse_args()

    print()
    print("================================================================")
    print("  AI QUANT ANALYST — POWERED BY CLAUDE OPUS 4.6")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("================================================================")
    print()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  ERROR: ANTHROPIC_API_KEY not set.")
        print("  Set it with: export ANTHROPIC_API_KEY='your-key'")
        sys.exit(1)

    if args.report:
        # Analyze a full report file
        print(f"  Analyzing report: {args.report}")
        analysis = analyze_report_file(args.report, verbose=args.verbose)
        if analysis:
            print()
            print("─" * 62)
            print("  CLAUDE'S PORTFOLIO BRIEFING")
            print("─" * 62)
            print()
            print(analysis)
            print()

    elif args.ticker:
        result = analyze_ticker(
            args.ticker.upper(), verbose=args.verbose, raw_output=args.raw
        )
        if result:
            print_thesis(result)

    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
        print(f"  Analyzing {len(tickers)} tickers...")
        results = analyze_tickers(tickers, verbose=args.verbose, raw_output=args.raw)
        print_full_report(results)

    elif args.tier1_only:
        tickers = _read_watchlist_tickers(tier_filter=["TIER 1"])
        if not tickers:
            print("  No TIER 1 tickers found in watchlist.txt")
            sys.exit(1)
        print(f"  Analyzing {len(tickers)} TIER 1 tickers: {', '.join(tickers)}")
        results = analyze_tickers(tickers, verbose=args.verbose, raw_output=args.raw)
        print_full_report(results)

    elif args.watchlist:
        tickers = _read_watchlist_tickers(tier_filter=["TIER 1", "TIER 2"])
        if not tickers:
            print("  No TIER 1/TIER 2 tickers found in watchlist.txt")
            sys.exit(1)
        print(f"  Analyzing {len(tickers)} watchlist tickers: {', '.join(tickers)}")
        results = analyze_tickers(tickers, verbose=args.verbose, raw_output=args.raw)
        print_full_report(results)

    else:
        parser.print_help()
        print()
        print("  Examples:")
        print("    python3 ai_quant.py --ticker COIN")
        print("    python3 ai_quant.py --tickers COIN GME NVDA --verbose")
        print("    python3 ai_quant.py --watchlist")
        print("    python3 ai_quant.py --report signal_reports/signal_report_20260318.txt")
        print()


if __name__ == "__main__":
    main()
