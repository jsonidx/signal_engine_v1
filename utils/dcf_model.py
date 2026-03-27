#!/usr/bin/env python3
"""
================================================================================
DCF MODEL v1.0 — Discounted Cash Flow Valuation + WACC + ROIC vs WACC Spread
================================================================================
Provides intrinsic value estimate, WACC, and ROIC vs WACC quality spread.

METHODOLOGY:
    1. WACC = w_e * CAPM + w_d * Kd * (1 - tax)
         cost_equity = Rf + beta * 6%  (CAPM; Rf from data/regime_latest.json)
         cost_debt   = interest_expense / total_debt  (income statement)
         weights from market cap vs total debt (book)
    2. FCF Projections (5 years)
         Yr 1-3 : analyst consensus revenue growth → current FCF/Rev margin
         Yr 4-5 : half-step toward terminal rate
    3. Terminal Value = FCF_yr5 * (1 + g) / (WACC - g)  — Gordon Growth
         g = 2.5% (long-run real GDP + inflation proxy)
    4. Intrinsic Value = [PV(FCFs) + PV(TV)] / shares_outstanding
    5. ROIC = NOPAT / Invested Capital
             = EBIT * (1 - eff_tax) / (equity_bv + debt - cash)
    6. ROIC vs WACC spread: > 0 = value creation; < 0 = value destruction

OUTPUT KEYS:
    wacc, cost_equity, cost_debt, beta_used, rf_rate
    roic, roic_wacc_spread
    intrinsic_value, current_price, upside_pct
    fcf_yield, terminal_growth
    fcf_5yr_projections (list)
    data_quality ("HIGH"|"MEDIUM"|"LOW"|"INSUFFICIENT")
    flags (list[str])

NOTE: DCF is highly sensitive to assumptions. Use alongside multiples.
================================================================================
"""

import json
import os
import time
import warnings
from typing import Optional

import numpy as np

warnings.filterwarnings("ignore")

# ── Constants ─────────────────────────────────────────────────────────────────
MRP = 0.06            # Equity risk premium (Damodaran long-run)
DEFAULT_RF = 0.045    # Fallback 10Y risk-free rate
TERMINAL_GROWTH = 0.025   # 2.5% terminal FCF growth
DEFAULT_TAX = 0.21    # US corporate rate
MIN_WACC = 0.06       # Floor (prevent divide-by-zero in terminal value)
MAX_WACC = 0.20       # Ceiling (distressed / very speculative names)
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# ==============================================================================
# SECTION 1: WACC INPUTS
# ==============================================================================

def _get_risk_free_rate() -> float:
    """Return 10Y Treasury yield from cached regime JSON. Falls back to 4.5%."""
    try:
        path = os.path.join(_DATA_DIR, "regime_latest.json")
        with open(path) as f:
            data = json.load(f)
        # regime_latest stores yield_curve_spread (T10Y2Y), not raw 10Y.
        # Use a fixed 4.5% as the best proxy without a live FRED call here.
        return DEFAULT_RF
    except Exception:
        return DEFAULT_RF


def _fetch_income_stmt_values(ticker_obj) -> dict:
    """
    Pull EBIT, interest_expense, and tax rate from yfinance income statement.
    Returns dict with keys: ebit, interest_expense, tax_rate, revenue_ttm.
    Gracefully returns Nones on failure.
    """
    result = {"ebit": None, "interest_expense": None, "tax_rate": DEFAULT_TAX, "revenue_ttm": None}
    try:
        stmt = ticker_obj.income_stmt
        if stmt is None or stmt.empty:
            return result

        def _find_row(keywords_all, keywords_any=None):
            """Case-insensitive row search in income statement."""
            for idx in stmt.index:
                name = str(idx).lower().strip()
                if all(kw in name for kw in keywords_all):
                    if keywords_any is None or any(kw in name for kw in keywords_any):
                        try:
                            val = float(stmt.loc[idx].iloc[0])
                            if np.isnan(val):
                                continue
                            return val
                        except Exception:
                            continue
            return None

        # EBIT / Operating Income
        result["ebit"] = (
            _find_row(["ebit"]) or
            _find_row(["operating", "income"]) or
            _find_row(["operating", "profit"])
        )

        # Interest expense
        result["interest_expense"] = _find_row(["interest", "expense"])
        if result["interest_expense"] is not None:
            result["interest_expense"] = abs(result["interest_expense"])

        # Revenue (TTM)
        result["revenue_ttm"] = (
            _find_row(["total", "revenue"]) or
            _find_row(["revenue"])
        )

        # Effective tax rate
        tax_val = _find_row(["tax", "provision"]) or _find_row(["income", "tax"])
        pretax = _find_row(["pretax", "income"]) or _find_row(["before", "tax"])
        if tax_val is not None and pretax and abs(pretax) > 0:
            tr = abs(tax_val) / abs(pretax)
            if 0.01 <= tr <= 0.45:
                result["tax_rate"] = tr

    except Exception:
        pass
    return result


def _fetch_dcf_inputs(ticker: str) -> Optional[dict]:
    """
    Pull all DCF inputs from yfinance. Returns None if critically insufficient.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}

        if not info or info.get("quoteType") is None:
            return None

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        if not price or not shares or shares <= 0:
            return None

        stmt_vals = _fetch_income_stmt_values(t)
        time.sleep(0.3)  # gentle with yfinance

        total_debt = float(info.get("totalDebt") or 0)
        total_cash = float(info.get("totalCash") or 0)
        mkt_cap = float(info.get("marketCap") or price * shares)
        fcf = info.get("freeCashflow")
        beta = info.get("beta")
        rev_growth = info.get("revenueGrowth") or 0.0  # YoY consensus
        book_equity = info.get("bookValue")
        if book_equity:
            book_equity = float(book_equity) * shares

        return {
            "ticker": ticker,
            "price": float(price),
            "shares": float(shares),
            "mkt_cap": mkt_cap,
            "fcf": float(fcf) if fcf is not None else None,
            "revenue_growth": float(rev_growth),
            "beta": float(beta) if beta is not None else None,
            "total_debt": total_debt,
            "total_cash": total_cash,
            "book_equity": book_equity,
            "ebit": stmt_vals["ebit"],
            "interest_expense": stmt_vals["interest_expense"],
            "tax_rate": stmt_vals["tax_rate"],
            "revenue_ttm": stmt_vals["revenue_ttm"],
            "sector": info.get("sector", ""),
        }
    except Exception:
        return None


# ==============================================================================
# SECTION 2: WACC COMPUTATION
# ==============================================================================

def compute_wacc(inputs: dict, rf: float) -> dict:
    """
    Compute WACC from fetched inputs.

    Returns dict: wacc, cost_equity, cost_debt, beta_used, rf_rate,
                  weight_equity, weight_debt, data_issues (list)
    """
    issues = []
    mkt_cap = inputs["mkt_cap"]
    total_debt = inputs["total_debt"]
    beta = inputs.get("beta")
    tax = inputs.get("tax_rate", DEFAULT_TAX)
    interest_exp = inputs.get("interest_expense")

    # ── Cost of equity (CAPM) ─────────────────────────────────────────────────
    if beta is None or beta <= 0:
        # Sector-adjusted fallback betas
        sector_betas = {
            "Technology": 1.25, "Healthcare": 0.85, "Financial Services": 1.10,
            "Consumer Cyclical": 1.15, "Consumer Defensive": 0.65, "Energy": 1.05,
            "Utilities": 0.55, "Real Estate": 0.90, "Industrials": 1.00,
            "Basic Materials": 1.05, "Communication Services": 1.10,
        }
        beta = sector_betas.get(inputs.get("sector", ""), 1.0)
        issues.append(f"Beta unavailable — using sector proxy {beta:.2f}")
    beta_used = min(max(float(beta), 0.3), 3.0)  # clip extremes
    cost_equity = rf + beta_used * MRP

    # ── Cost of debt ──────────────────────────────────────────────────────────
    _valid_ie = (
        interest_exp is not None
        and not (isinstance(interest_exp, float) and np.isnan(interest_exp))
        and interest_exp > 0
    )
    if _valid_ie and total_debt > 0:
        cost_debt_raw = interest_exp / total_debt
        cost_debt = min(max(cost_debt_raw, 0.02), 0.15)  # 2–15% range
    elif total_debt > 0:
        # Proxy: use credit-quality estimate from debt/mktcap
        leverage = total_debt / (mkt_cap + 1)
        cost_debt = 0.04 + min(leverage * 0.03, 0.06)  # 4–10%
        issues.append("Interest expense unavailable — using leverage-based cost of debt proxy")
    else:
        cost_debt = 0.04
        issues.append("No debt — cost of debt set to 4%")

    cost_debt_aftertax = cost_debt * (1 - tax)

    # ── Capital structure weights ──────────────────────────────────────────────
    total_capital = mkt_cap + total_debt
    if total_capital <= 0:
        total_capital = mkt_cap or 1
    w_equity = mkt_cap / total_capital
    w_debt = total_debt / total_capital

    # ── WACC ──────────────────────────────────────────────────────────────────
    wacc = w_equity * cost_equity + w_debt * cost_debt_aftertax
    wacc = min(max(wacc, MIN_WACC), MAX_WACC)

    return {
        "wacc": round(wacc, 4),
        "cost_equity": round(cost_equity, 4),
        "cost_debt": round(cost_debt, 4),
        "beta_used": round(beta_used, 2),
        "rf_rate": round(rf, 4),
        "weight_equity": round(w_equity, 3),
        "weight_debt": round(w_debt, 3),
        "data_issues": issues,
    }


# ==============================================================================
# SECTION 3: FCF PROJECTIONS
# ==============================================================================

def project_fcf(inputs: dict, wacc_rate: float, years: int = 5) -> list:
    """
    Build FCF projection for `years` periods.

    Growth schedule:
      - Year 1–3: analyst revenue growth rate → FCF grows at same rate
                  (capped between -20% and +60% to prevent outlier blowups)
      - Year 4–5: linear decay toward terminal growth rate (2.5%)
    FCF margin is held constant at current FCF/Revenue ratio.
    If FCF is unavailable but EBIT is, estimate FCF from EBIT * (1 - tax).

    Returns list of annual FCF values (absolute, in same units as inputs).
    """
    fcf = inputs.get("fcf")
    ebit = inputs.get("ebit")
    tax = inputs.get("tax_rate", DEFAULT_TAX)

    if fcf is None and ebit is not None:
        fcf = ebit * (1 - tax) * 0.85  # EBIT → NOPAT, rough capex haircut
    if fcf is None:
        return []

    # Near-term growth rate
    near_growth = float(inputs.get("revenue_growth") or 0.05)
    near_growth = min(max(near_growth, -0.20), 0.60)

    projections = []
    current = float(fcf)
    for yr in range(1, years + 1):
        if yr <= 3:
            g = near_growth
        else:
            # Fade toward terminal growth
            fade_step = (near_growth - TERMINAL_GROWTH) / 3
            g = near_growth - fade_step * (yr - 3)
            g = max(g, TERMINAL_GROWTH)
        current = current * (1 + g)
        projections.append(round(current, 0))

    return projections


# ==============================================================================
# SECTION 4: INTRINSIC VALUE (FULL DCF)
# ==============================================================================

def compute_intrinsic_value(inputs: dict, wacc_rate: float) -> dict:
    """
    Full DCF: PV of FCF projections + PV of terminal value → per-share value.

    Returns: intrinsic_value, terminal_value, pv_fcfs, upside_pct, data_quality
    """
    fcf_projections = project_fcf(inputs, wacc_rate)
    if not fcf_projections:
        return {
            "intrinsic_value": None,
            "terminal_value": None,
            "pv_fcfs": None,
            "upside_pct": None,
            "fcf_5yr_projections": [],
            "data_quality": "INSUFFICIENT",
        }

    price = inputs["price"]
    shares = inputs["shares"]

    # PV of projected FCFs
    pv_fcfs = 0.0
    for yr, fcf_yr in enumerate(fcf_projections, 1):
        pv_fcfs += fcf_yr / ((1 + wacc_rate) ** yr)

    # Terminal value (Gordon Growth on year 5 FCF)
    fcf_final = fcf_projections[-1]
    if wacc_rate <= TERMINAL_GROWTH:
        # Prevent divide-by-zero; use 2× revenue multiple fallback
        tv = fcf_final * 15
    else:
        tv = fcf_final * (1 + TERMINAL_GROWTH) / (wacc_rate - TERMINAL_GROWTH)

    pv_tv = tv / ((1 + wacc_rate) ** len(fcf_projections))

    # Enterprise value → equity value
    total_debt = inputs.get("total_debt", 0)
    total_cash = inputs.get("total_cash", 0)
    enterprise_value = pv_fcfs + pv_tv
    equity_value = enterprise_value - total_debt + total_cash
    if equity_value <= 0:
        equity_value = enterprise_value * 0.5  # partial recovery estimate

    intrinsic_per_share = equity_value / shares
    upside_pct = (intrinsic_per_share / price - 1) * 100 if price > 0 else None

    # Data quality assessment
    issues = []
    if inputs.get("fcf") is None:
        issues.append("FCF estimated from EBIT")
    if inputs.get("beta") is None:
        issues.append("beta proxied")
    if inputs.get("interest_expense") is None:
        issues.append("cost of debt proxied")

    if len(issues) == 0:
        dq = "HIGH"
    elif len(issues) == 1:
        dq = "MEDIUM"
    elif len(issues) <= 2:
        dq = "LOW"
    else:
        dq = "INSUFFICIENT"

    return {
        "intrinsic_value": round(intrinsic_per_share, 2),
        "terminal_value": round(pv_tv, 0),
        "pv_fcfs": round(pv_fcfs, 0),
        "upside_pct": round(upside_pct, 1) if upside_pct is not None else None,
        "fcf_5yr_projections": fcf_projections,
        "data_quality": dq,
    }


# ==============================================================================
# SECTION 5: ROIC vs WACC SPREAD
# ==============================================================================

def compute_roic(inputs: dict) -> Optional[float]:
    """
    ROIC = NOPAT / Invested Capital
         = EBIT * (1 - tax) / (book_equity + debt - cash)

    Returns float (e.g. 0.18 = 18%) or None if data insufficient.
    """
    ebit = inputs.get("ebit")
    if ebit is None:
        return None

    tax = inputs.get("tax_rate", DEFAULT_TAX)
    nopat = ebit * (1 - tax)

    book_equity = inputs.get("book_equity") or 0
    total_debt = inputs.get("total_debt") or 0
    total_cash = inputs.get("total_cash") or 0

    invested_capital = book_equity + total_debt - total_cash
    if invested_capital <= 0:
        # Asset-light or negative book value — use assets proxy
        invested_capital = inputs.get("mkt_cap", 0) * 0.3  # rough proxy
        if invested_capital <= 0:
            return None

    return round(nopat / invested_capital, 4)


# ==============================================================================
# SECTION 6: MAIN ENTRY POINT
# ==============================================================================

def run_dcf(ticker: str) -> dict:
    """
    Full DCF pipeline for a single ticker.

    Returns consolidated result dict. Always returns a dict (never raises).
    On insufficient data returns data_quality='INSUFFICIENT' with null values.
    """
    flags = []
    rf = _get_risk_free_rate()

    inputs = _fetch_dcf_inputs(ticker)
    if inputs is None:
        return {
            "ticker": ticker,
            "wacc": None,
            "cost_equity": None,
            "cost_debt": None,
            "beta_used": None,
            "rf_rate": rf,
            "roic": None,
            "roic_wacc_spread": None,
            "intrinsic_value": None,
            "current_price": None,
            "upside_pct": None,
            "fcf_yield": None,
            "terminal_growth": TERMINAL_GROWTH,
            "fcf_5yr_projections": [],
            "data_quality": "INSUFFICIENT",
            "flags": ["Could not fetch yfinance data for ticker"],
        }

    wacc_result = compute_wacc(inputs, rf)
    flags.extend(wacc_result.pop("data_issues", []))
    wacc_rate = wacc_result["wacc"]

    dcf_result = compute_intrinsic_value(inputs, wacc_rate)
    flags_from_dq = []
    if dcf_result["data_quality"] in ("LOW", "INSUFFICIENT"):
        flags_from_dq.append(f"Data quality: {dcf_result['data_quality']} — treat DCF as directional only")

    roic = compute_roic(inputs)
    roic_wacc_spread = round(roic - wacc_rate, 4) if roic is not None else None

    # FCF yield (TTM FCF / market cap)
    fcf_yield = None
    if inputs.get("fcf") and inputs["mkt_cap"] > 0:
        fcf_yield = round(inputs["fcf"] / inputs["mkt_cap"] * 100, 2)

    # Narrative flags
    if dcf_result.get("upside_pct") is not None:
        if dcf_result["upside_pct"] > 30:
            flags.append(f"DCF implies {dcf_result['upside_pct']:.0f}% upside — significantly undervalued vs intrinsic")
        elif dcf_result["upside_pct"] > 10:
            flags.append(f"DCF implies {dcf_result['upside_pct']:.0f}% upside — moderately undervalued")
        elif dcf_result["upside_pct"] < -30:
            flags.append(f"DCF implies {abs(dcf_result['upside_pct']):.0f}% downside — significantly overvalued")
        elif dcf_result["upside_pct"] < -10:
            flags.append(f"DCF implies {abs(dcf_result['upside_pct']):.0f}% downside — moderately overvalued")
        else:
            flags.append(f"DCF implies {dcf_result['upside_pct']:.0f}% vs current — roughly fairly valued")

    if roic_wacc_spread is not None:
        if roic_wacc_spread > 0.05:
            flags.append(f"ROIC {roic*100:.1f}% vs WACC {wacc_rate*100:.1f}% — strong value creation (+{roic_wacc_spread*100:.1f}pp spread)")
        elif roic_wacc_spread > 0:
            flags.append(f"ROIC {roic*100:.1f}% vs WACC {wacc_rate*100:.1f}% — marginal value creation")
        elif roic_wacc_spread > -0.03:
            flags.append(f"ROIC {roic*100:.1f}% vs WACC {wacc_rate*100:.1f}% — borderline value destruction")
        else:
            flags.append(f"ROIC {roic*100:.1f}% vs WACC {wacc_rate*100:.1f}% — value destruction ({roic_wacc_spread*100:.1f}pp below cost of capital)")

    if fcf_yield is not None:
        if fcf_yield > 6:
            flags.append(f"FCF yield {fcf_yield:.1f}% — high free cash flow relative to market cap")
        elif fcf_yield < 0:
            flags.append(f"FCF yield {fcf_yield:.1f}% — burning cash (negative FCF)")

    flags.extend(flags_from_dq)

    return {
        "ticker": ticker,
        **wacc_result,
        "roic": round(roic, 4) if roic is not None else None,
        "roic_wacc_spread": roic_wacc_spread,
        "intrinsic_value": dcf_result.get("intrinsic_value"),
        "current_price": inputs["price"],
        "upside_pct": dcf_result.get("upside_pct"),
        "fcf_yield": fcf_yield,
        "terminal_growth": TERMINAL_GROWTH,
        "fcf_5yr_projections": dcf_result.get("fcf_5yr_projections", []),
        "data_quality": dcf_result.get("data_quality", "INSUFFICIENT"),
        "flags": flags,
    }


# ==============================================================================
# CLI
# ==============================================================================

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="DCF Model v1.0")
    parser.add_argument("--ticker", required=True, help="Ticker to analyze")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    result = run_dcf(args.ticker.upper())

    if args.json:
        import json
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'█' * 55}")
        print(f"  DCF VALUATION: {result['ticker']}")
        print(f"{'█' * 55}")
        price = result.get("current_price")
        intrinsic = result.get("intrinsic_value")
        upside = result.get("upside_pct")
        wacc = result.get("wacc")
        roic = result.get("roic")
        spread = result.get("roic_wacc_spread")

        if price:
            print(f"  Current Price : ${price:.2f}")
        if intrinsic:
            print(f"  Intrinsic Val : ${intrinsic:.2f}  ({'+' if upside >= 0 else ''}{upside:.1f}% {'upside' if upside >= 0 else 'downside'})")
        if wacc:
            print(f"  WACC          : {wacc*100:.1f}%  (beta {result.get('beta_used'):.2f}, Rf {result.get('rf_rate')*100:.1f}%)")
        if roic is not None:
            print(f"  ROIC          : {roic*100:.1f}%  (spread vs WACC: {'+' if spread >= 0 else ''}{spread*100:.1f}pp)")
        if result.get("fcf_yield"):
            print(f"  FCF Yield     : {result['fcf_yield']:.1f}%")
        print(f"  Data Quality  : {result.get('data_quality')}")
        print(f"\n  FLAGS:")
        for flag in result.get("flags", []):
            print(f"    • {flag}")
        projs = result.get("fcf_5yr_projections", [])
        if projs:
            print(f"\n  FCF PROJECTIONS (5yr, $M):")
            for i, v in enumerate(projs, 1):
                print(f"    Yr {i}: ${v/1e6:.0f}M")
        print(f"\n  Data Quality: {result['data_quality']}")
        print(f"  ⚠️  DCF is directional only. Not investment advice.\n")
