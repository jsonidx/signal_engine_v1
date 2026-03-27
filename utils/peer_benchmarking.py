#!/usr/bin/env python3
"""
================================================================================
PEER BENCHMARKING v1.0 — Historical Multiples + Sector Peer Comparison
================================================================================
Two complementary analyses:
  A. Historical P/E — constructs 5-year trailing P/E series from yfinance price
     history + quarterly EPS (rolling 4Q TTM), then compares current vs average
  B. Sector peers — pulls P/E, EV/EBITDA, P/FCF for curated sector peers and
     computes where the stock trades vs sector median and vs large-cap benchmark

OUTPUT per ticker:
    {
        # Historical multiples
        "historical_pe_avg":      float | None   (5yr average trailing P/E)
        "historical_pe_current":  float | None   (today's trailing P/E)
        "pe_vs_history_pct":      float | None   (% premium/discount to own history)
        "pe_5yr_series":          list[dict]     ({"year": int, "pe": float})

        # Peer comparison
        "sector":                 str
        "peer_tickers":           list[str]
        "peer_median_pe":         float | None
        "peer_median_ev_ebitda":  float | None
        "peer_median_pfcf":       float | None
        "stock_pe":               float | None
        "stock_ev_ebitda":        float | None
        "stock_pfcf":             float | None
        "pe_vs_peers_pct":        float | None   (% premium +/discount − to sector median)
        "ev_ebitda_vs_peers_pct": float | None

        # Summary
        "relative_valuation":     str  ("CHEAP"|"FAIR"|"RICH"|"INSUFFICIENT")
        "flags":                  list[str]
    }

DATA SOURCE: yfinance (free). Rate-limited at 0.3s per ticker.
================================================================================
"""

import time
import warnings
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ==============================================================================
# CURATED SECTOR PEER GROUPS
# ==============================================================================
# Each key maps to a list of representative large/mid-cap peers.
# Extend or override by calling get_sector_peers(ticker) with your own dict.

SECTOR_PEERS: dict = {
    "Technology": [
        "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "AVGO", "ORCL",
        "CRM", "AMD", "INTC", "QCOM", "TXN", "NOW", "ADBE",
    ],
    "Healthcare": [
        "JNJ", "LLY", "UNH", "ABT", "MRK", "TMO", "DHR", "BMY",
        "AMGN", "GILD", "REGN", "VRTX", "ISRG", "BSX", "SYK",
    ],
    "Financial Services": [
        "BRK-B", "JPM", "BAC", "WFC", "GS", "MS", "C", "AXP",
        "BLK", "SCHW", "USB", "TFC", "PNC", "COF", "SPGI",
    ],
    "Consumer Cyclical": [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "TJX", "LOW",
        "BKNG", "MAR", "HLT", "ROST", "ORLY", "AZO", "F",
    ],
    "Consumer Defensive": [
        "WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "CL",
        "MDLZ", "GIS", "KHC", "STZ", "SYY", "KR", "CAG",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO",
        "HES", "DVN", "BKR", "FANG", "OXY", "HAL", "PXD",
    ],
    "Utilities": [
        "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PCG",
        "ED", "XEL", "ES", "AWK", "WEC", "PPL", "FE",
    ],
    "Real Estate": [
        "AMT", "PLD", "CCI", "EQIX", "PSA", "DLR", "O", "SPG",
        "SBAC", "EQR", "AVB", "VTR", "WELL", "BXP", "ARE",
    ],
    "Industrials": [
        "GE", "HON", "RTX", "UPS", "CAT", "DE", "MMM", "EMR",
        "LMT", "NOC", "GD", "ITW", "ETN", "FDX", "NSC",
    ],
    "Basic Materials": [
        "LIN", "APD", "ECL", "SHW", "NEM", "FCX", "NUE", "STLD",
        "VMC", "MLM", "IP", "PKG", "ALB", "MOS", "CF",
    ],
    "Communication Services": [
        "GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "CHTR",
        "TMUS", "WBD", "FOXA", "PARA", "MTCH", "IAC", "SNAP",
    ],
    "Crypto": [],  # No fundamental peers
}

# Fallback if sector not found
_DEFAULT_PEERS = ["SPY", "QQQ"]


# ==============================================================================
# SECTION 1: HISTORICAL P/E
# ==============================================================================

def _build_historical_pe(ticker: str, years: int = 5) -> dict:
    """
    Reconstruct trailing P/E series by combining:
      - Annual price (year-end close) from yfinance history
      - Annual EPS from yfinance .earnings (revenue + earnings table)

    Returns dict with keys: series (list), avg_pe, current_pe
    """
    result = {"series": [], "avg_pe": None, "current_pe": None}
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)

        # Annual earnings (EPS available via .earnings for US equities)
        ann = t.earnings  # DataFrame: Revenue, Earnings — index = year int
        time.sleep(0.2)
        if ann is None or ann.empty:
            return result

        # Annual price history
        hist = t.history(period=f"{years + 1}y", interval="1mo")
        if hist.empty:
            return result
        hist.index = pd.to_datetime(hist.index)

        series = []
        for year_val in ann.index:
            try:
                year_int = int(year_val)
                eps_row = ann.loc[year_val]
                # yfinance .earnings columns vary: "Earnings" or earnings column
                eps = None
                for col in eps_row.index:
                    if "earn" in str(col).lower():
                        eps = float(eps_row[col])
                        break
                if eps is None or eps <= 0:
                    continue

                # Get shares outstanding (use current — approximate)
                shares = t.info.get("sharesOutstanding") or t.info.get("impliedSharesOutstanding")
                if not shares:
                    continue

                eps_per_share = eps / shares

                # Year-end price (December close)
                yr_data = hist[hist.index.year == year_int]
                if yr_data.empty:
                    yr_data = hist[hist.index.year == year_int - 1]
                if yr_data.empty:
                    continue

                price_yr = float(yr_data["Close"].iloc[-1])
                pe = price_yr / eps_per_share
                if 0 < pe < 500:  # sanity check
                    series.append({"year": year_int, "pe": round(pe, 1)})
            except Exception:
                continue

        # Current P/E from info (more accurate)
        current_pe = t.info.get("trailingPE") or t.info.get("forwardPE")

        if len(series) >= 2:
            avg_pe = round(float(np.mean([s["pe"] for s in series])), 1)
            result["series"] = sorted(series, key=lambda x: x["year"])
            result["avg_pe"] = avg_pe
        result["current_pe"] = current_pe

    except Exception:
        pass
    return result


# ==============================================================================
# SECTION 2: PEER MULTIPLES
# ==============================================================================

def get_sector_peers(ticker: str, sector: str = None) -> list:
    """Return peer list for ticker's sector. Excludes the ticker itself."""
    if sector is None:
        try:
            import yfinance as yf
            sector = yf.Ticker(ticker).info.get("sector", "")
            time.sleep(0.2)
        except Exception:
            sector = ""

    peers = SECTOR_PEERS.get(sector, _DEFAULT_PEERS)
    return [p for p in peers if p.upper() != ticker.upper()][:12]  # cap at 12


def _fetch_peer_multiples(peer_tickers: list) -> list:
    """
    Fetch P/E, EV/EBITDA, P/FCF for each peer.
    Returns list of dicts; gracefully skips failed tickers.
    """
    import yfinance as yf
    rows = []
    for p in peer_tickers[:10]:  # limit to 10 peers max
        try:
            info = yf.Ticker(p).info or {}
            time.sleep(0.25)
            pe = info.get("trailingPE") or info.get("forwardPE")
            ev_ebitda = info.get("enterpriseToEbitda")
            # P/FCF: market cap / free cash flow
            mkt_cap = info.get("marketCap")
            fcf = info.get("freeCashflow")
            pfcf = round(mkt_cap / fcf, 1) if (mkt_cap and fcf and fcf > 0) else None

            if pe and 0 < pe < 500:
                rows.append({
                    "ticker": p,
                    "pe": round(float(pe), 1),
                    "ev_ebitda": round(float(ev_ebitda), 1) if ev_ebitda and ev_ebitda > 0 else None,
                    "pfcf": pfcf,
                })
        except Exception:
            continue
    return rows


def _safe_median(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None and not np.isnan(v)]
    if not vals:
        return None
    return round(float(np.median(vals)), 1)


# ==============================================================================
# SECTION 3: MAIN ENTRY POINT
# ==============================================================================

def run_peer_benchmarking(ticker: str) -> dict:
    """
    Full peer benchmarking pipeline for a single ticker.
    Always returns a dict; degrades gracefully on data gaps.
    """
    flags = []
    ticker = ticker.upper()

    # ── Historical P/E ────────────────────────────────────────────────────────
    hist_pe = _build_historical_pe(ticker)
    current_pe = hist_pe.get("current_pe")
    avg_pe = hist_pe.get("avg_pe")

    pe_vs_hist = None
    if current_pe and avg_pe and avg_pe > 0:
        pe_vs_hist = round((current_pe / avg_pe - 1) * 100, 1)
        if pe_vs_hist > 30:
            flags.append(f"P/E {current_pe:.0f}x vs 5yr avg {avg_pe:.0f}x — trading {pe_vs_hist:.0f}% premium to own history")
        elif pe_vs_hist < -20:
            flags.append(f"P/E {current_pe:.0f}x vs 5yr avg {avg_pe:.0f}x — trading {abs(pe_vs_hist):.0f}% discount to own history")
        else:
            flags.append(f"P/E {current_pe:.0f}x vs 5yr avg {avg_pe:.0f}x — in-line with own valuation history")

    # ── Sector peers ──────────────────────────────────────────────────────────
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        time.sleep(0.2)
        sector = info.get("sector", "Unknown")
        stock_pe = info.get("trailingPE") or info.get("forwardPE")
        mkt_cap = info.get("marketCap")
        fcf = info.get("freeCashflow")
        stock_ev_ebitda = info.get("enterpriseToEbitda")
        stock_pfcf = round(mkt_cap / fcf, 1) if (mkt_cap and fcf and fcf > 0) else None
    except Exception:
        sector = "Unknown"
        stock_pe = current_pe
        stock_ev_ebitda = None
        stock_pfcf = None

    peers = get_sector_peers(ticker, sector)
    peer_data = _fetch_peer_multiples(peers)

    peer_median_pe = _safe_median([r["pe"] for r in peer_data])
    peer_median_ev_ebitda = _safe_median([r["ev_ebitda"] for r in peer_data if r.get("ev_ebitda")])
    peer_median_pfcf = _safe_median([r["pfcf"] for r in peer_data if r.get("pfcf")])

    # Premium/discount vs sector median
    pe_vs_peers = None
    if stock_pe and peer_median_pe and peer_median_pe > 0:
        pe_vs_peers = round((stock_pe / peer_median_pe - 1) * 100, 1)
        if pe_vs_peers > 40:
            flags.append(f"P/E {stock_pe:.0f}x vs sector median {peer_median_pe:.0f}x — significant premium (+{pe_vs_peers:.0f}%)")
        elif pe_vs_peers < -25:
            flags.append(f"P/E {stock_pe:.0f}x vs sector median {peer_median_pe:.0f}x — significant discount ({pe_vs_peers:.0f}%)")
        else:
            flags.append(f"P/E {stock_pe:.0f}x vs sector median {peer_median_pe:.0f}x ({'+' if pe_vs_peers >= 0 else ''}{pe_vs_peers:.0f}%)")

    ev_ebitda_vs_peers = None
    if stock_ev_ebitda and peer_median_ev_ebitda and peer_median_ev_ebitda > 0:
        ev_ebitda_vs_peers = round((stock_ev_ebitda / peer_median_ev_ebitda - 1) * 100, 1)
        if ev_ebitda_vs_peers > 40:
            flags.append(f"EV/EBITDA {stock_ev_ebitda:.0f}x vs peers {peer_median_ev_ebitda:.0f}x — rich relative valuation")
        elif ev_ebitda_vs_peers < -25:
            flags.append(f"EV/EBITDA {stock_ev_ebitda:.0f}x vs peers {peer_median_ev_ebitda:.0f}x — cheap relative valuation")

    # Relative valuation summary
    premiums = [v for v in [pe_vs_peers, ev_ebitda_vs_peers] if v is not None]
    if not premiums and pe_vs_hist is None:
        relative_valuation = "INSUFFICIENT"
    else:
        avg_premium = np.mean(premiums) if premiums else pe_vs_hist or 0
        if avg_premium > 30:
            relative_valuation = "RICH"
        elif avg_premium < -20:
            relative_valuation = "CHEAP"
        else:
            relative_valuation = "FAIR"

    if not flags:
        flags.append("Insufficient peer data for comparison")

    return {
        # Historical multiples
        "historical_pe_avg": avg_pe,
        "historical_pe_current": current_pe,
        "pe_vs_history_pct": pe_vs_hist,
        "pe_5yr_series": hist_pe.get("series", []),
        # Peer comparison
        "sector": sector,
        "peer_tickers": [r["ticker"] for r in peer_data],
        "peer_median_pe": peer_median_pe,
        "peer_median_ev_ebitda": peer_median_ev_ebitda,
        "peer_median_pfcf": peer_median_pfcf,
        "stock_pe": round(float(stock_pe), 1) if stock_pe else None,
        "stock_ev_ebitda": round(float(stock_ev_ebitda), 1) if stock_ev_ebitda else None,
        "stock_pfcf": stock_pfcf,
        "pe_vs_peers_pct": pe_vs_peers,
        "ev_ebitda_vs_peers_pct": ev_ebitda_vs_peers,
        # Summary
        "relative_valuation": relative_valuation,
        "flags": flags,
    }


# ==============================================================================
# CLI
# ==============================================================================

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Peer Benchmarking v1.0")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_peer_benchmarking(args.ticker.upper())
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'█' * 55}")
        print(f"  PEER BENCHMARKING: {result['ticker'] if 'ticker' in result else args.ticker.upper()}")
        print(f"  Sector: {result['sector']}")
        print(f"{'█' * 55}")
        print(f"\n  Historical P/E:")
        print(f"    Current:   {result['historical_pe_current']}")
        print(f"    5yr Avg:   {result['historical_pe_avg']}")
        print(f"    vs History:{result['pe_vs_history_pct']}%")
        print(f"\n  Sector Comparison:")
        print(f"    Stock P/E:      {result['stock_pe']}")
        print(f"    Peer Median P/E:{result['peer_median_pe']}")
        print(f"    vs Peers:       {result['pe_vs_peers_pct']}%")
        print(f"    EV/EBITDA:      {result['stock_ev_ebitda']} vs {result['peer_median_ev_ebitda']}")
        print(f"\n  Verdict: {result['relative_valuation']}")
        print(f"\n  Flags:")
        for flag in result["flags"]:
            print(f"    • {flag}")
        if result["pe_5yr_series"]:
            print(f"\n  Historical P/E Series:")
            for row in result["pe_5yr_series"]:
                print(f"    {row['year']}: {row['pe']:.1f}x")
