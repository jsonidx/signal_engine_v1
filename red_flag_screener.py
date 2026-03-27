#!/usr/bin/env python3
"""
================================================================================
RED FLAG SCREENER v1.0 — Accounting Quality & Financial Risk Detection
================================================================================
Screens for accounting red flags, financial quality deterioration, and dividend
sustainability issues.

CHECKS:
    1. RESTATEMENT HISTORY  — scans SEC EDGAR 10-K/10-Q/8-K for "restatement",
                               "material weakness", "going concern" keywords
    2. ACCRUALS RATIO        — Sloan (1996): high accruals predict earnings decay
                               ratio = (net_income - op_cash_flow) / avg_total_assets
                               > 5% = yellow flag | > 10% = red flag
    3. GAAP vs ADJUSTED DIV  — divergence between net margin and operating margin
                               as proxy for excessive add-backs / adjusted metrics
    4. PAYOUT SUSTAINABILITY — dividend payout ratio vs free cash flow
                               dividends > FCF = sustainability risk
    5. REVENUE QUALITY       — revenue growth vs gross profit growth divergence
                               (margin compression while reporting top-line growth)

SCORING:
    Each check contributes 0–25 points to a 0–100 red flag score.
    Higher score = MORE red flags = HIGHER RISK.

    0–20  : CLEAN    — no material red flags
    21–45 : CAUTION  — minor concerns, monitor closely
    46–70 : WARNING  — significant accounting risks present
    71–100: CRITICAL — multiple serious red flags

OUTPUT:
    {
        "red_flag_score":     int (0–100)
        "risk_level":         str ("CLEAN"|"CAUTION"|"WARNING"|"CRITICAL")
        "checks": {
            "restatement":    {"score": int, "detail": str, "findings": list}
            "accruals":       {"score": int, "detail": str, "ratio": float}
            "gaap_divergence":{"score": int, "detail": str}
            "payout_risk":    {"score": int, "detail": str}
            "revenue_quality":{"score": int, "detail": str}
        }
        "flags": list[str]
        "data_quality": str
    }

DATA SOURCES:
    - SEC EDGAR (free, no API key) for restatement search
    - yfinance for financial ratios

USAGE:
    python3 red_flag_screener.py --ticker MU
    python3 red_flag_screener.py --watchlist
    python3 red_flag_screener.py --ticker GME --json
================================================================================
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import warnings
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

warnings.filterwarnings("ignore")

try:
    from config import OUTPUT_DIR
except ImportError:
    OUTPUT_DIR = "./signals_output"

# SEC rate limit compliance
SEC_HEADERS = {
    "User-Agent": "SignalEngine/1.0 (educational research; contact@example.com)",
    "Accept": "application/json",
}
SEC_RATE_LIMIT = 0.15   # seconds between SEC requests
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
EDGAR_DATA = "https://data.sec.gov"

# Red flag keyword sets
_RESTATEMENT_KEYWORDS = [
    "restatement", "restate", "material weakness", "going concern",
    "internal control deficiency", "significant deficiency",
    "error correction", "revision to previously", "financial fraud",
]


# ==============================================================================
# SECTION 1: SEC EDGAR RESTATEMENT DETECTION
# ==============================================================================

def _edgar_request(url: str) -> Optional[dict]:
    """Rate-limited SEC EDGAR JSON request."""
    try:
        req = urllib.request.Request(url, headers=SEC_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        time.sleep(SEC_RATE_LIMIT)
        return data
    except Exception:
        return None


def _get_cik(ticker: str) -> Optional[str]:
    """Resolve ticker → 10-digit padded CIK via EDGAR company search."""
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=10-K&hits.hits._source.period_of_report=*"
    # Use the simpler company tickers mapping
    try:
        req = urllib.request.Request(
            "https://www.sec.gov/files/company_tickers.json",
            headers=SEC_HEADERS
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        time.sleep(SEC_RATE_LIMIT)
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                return str(entry["cik_str"]).zfill(10)
    except Exception:
        pass
    return None


def _search_filings_for_keywords(cik: str, form_type: str, keywords: list, lookback_days: int = 730) -> list:
    """
    Search recent filings of a given form type for red flag keywords.
    Returns list of {date, keyword_found, form_type} dicts.
    """
    findings = []
    try:
        url = f"{EDGAR_DATA}/submissions/CIK{cik}.json"
        data = _edgar_request(url)
        if not data:
            return findings

        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        primary_docs = filings.get("primaryDocument", [])
        accession_nums = filings.get("accessionNumber", [])

        cutoff = datetime.now() - timedelta(days=lookback_days)

        for i, (form, date_str, doc, accn) in enumerate(zip(forms, dates, primary_docs, accession_nums)):
            if form.upper() != form_type.upper():
                continue
            try:
                filing_date = datetime.strptime(date_str, "%Y-%m-%d")
                if filing_date < cutoff:
                    continue
            except Exception:
                continue

            # Fetch the primary document HTML
            accn_clean = accn.replace("-", "")
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_clean}/{doc}"
            try:
                req = urllib.request.Request(doc_url, headers={
                    "User-Agent": SEC_HEADERS["User-Agent"]
                })
                with urllib.request.urlopen(req, timeout=15) as resp:
                    content = resp.read().decode("utf-8", errors="ignore").lower()
                time.sleep(SEC_RATE_LIMIT)

                for kw in keywords:
                    if kw.lower() in content:
                        findings.append({
                            "date": date_str,
                            "form": form,
                            "keyword": kw,
                        })
                        break  # One finding per filing is enough

            except Exception:
                time.sleep(SEC_RATE_LIMIT)
                continue

            if len(findings) >= 3:  # Cap search depth
                break

    except Exception:
        pass
    return findings


def check_restatements(ticker: str) -> dict:
    """
    Check SEC filings for restatement/material weakness keywords.
    Returns: {score (0–25), detail, findings}
    """
    score = 0
    detail = "No restatement signals found in recent filings"
    findings = []

    cik = _get_cik(ticker)
    if not cik:
        return {
            "score": 0,
            "detail": "CIK lookup failed — EDGAR restatement check skipped",
            "findings": [],
        }

    # Check 10-K (annual) and 8-K (material events)
    for form_type in ["10-K", "8-K"]:
        found = _search_filings_for_keywords(cik, form_type, _RESTATEMENT_KEYWORDS, lookback_days=730)
        findings.extend(found)
        if len(findings) >= 3:
            break

    if findings:
        keywords_found = list({f["keyword"] for f in findings})
        if any(kw in ("material weakness", "going concern", "financial fraud") for kw in keywords_found):
            score = 25
            detail = f"CRITICAL: Found '{', '.join(keywords_found)}' in recent SEC filings"
        elif any(kw in ("restatement", "restate", "error correction") for kw in keywords_found):
            score = 18
            detail = f"WARNING: Restatement-related language in {len(findings)} recent filing(s)"
        else:
            score = 8
            detail = f"CAUTION: Internal control concerns in {len(findings)} filing(s)"
    else:
        detail = "No restatement signals in 10-K/8-K (last 2 years)"

    return {"score": score, "detail": detail, "findings": findings[:5]}


# ==============================================================================
# SECTION 2: ACCRUALS RATIO (SLOAN)
# ==============================================================================

def check_accruals(ticker: str) -> dict:
    """
    Sloan accruals ratio: (net_income - operating_cash_flow) / avg_total_assets
    Positive accruals = income > cash generation → earnings quality risk.
    > 5% yellow, > 10% red.
    """
    score = 0
    ratio = None
    detail = "Accruals data unavailable"

    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}

        # Cash flow statement for operating CF
        cf = t.cashflow
        time.sleep(0.2)

        operating_cf = None
        if cf is not None and not cf.empty:
            for idx in cf.index:
                if "operating" in str(idx).lower():
                    operating_cf = float(cf.loc[idx].iloc[0])
                    break

        net_income = info.get("netIncomeToCommon") or info.get("netIncome")
        total_assets = info.get("totalAssets")

        if net_income is not None and operating_cf is not None and total_assets and total_assets > 0:
            accruals = net_income - operating_cf
            ratio = round(accruals / total_assets, 4)

            if ratio > 0.10:
                score = 20
                detail = f"HIGH accruals ratio {ratio:.1%}: net income significantly exceeds operating cash flow — earnings quality risk"
            elif ratio > 0.05:
                score = 10
                detail = f"ELEVATED accruals ratio {ratio:.1%}: income outpacing cash generation"
            elif ratio < -0.05:
                score = 0
                detail = f"CLEAN accruals ratio {ratio:.1%}: cash flow exceeds reported income (conservative accounting)"
            else:
                score = 3
                detail = f"NORMAL accruals ratio {ratio:.1%}: income broadly in line with cash generation"
        else:
            detail = "Insufficient balance sheet/CF data for accruals calculation"

    except Exception as e:
        detail = f"Accruals check failed: {str(e)[:50]}"

    return {"score": score, "detail": detail, "ratio": ratio}


# ==============================================================================
# SECTION 3: GAAP VS ADJUSTED DIVERGENCE
# ==============================================================================

def check_gaap_divergence(ticker: str) -> dict:
    """
    Checks for significant divergence between GAAP net margin and operating margin.
    Large divergence suggests heavy non-recurring add-backs in adjusted metrics.
    Also checks if company reports GAAP losses with positive adjusted EBITDA.
    """
    score = 0
    detail = "GAAP vs adjusted divergence check: no data"

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        time.sleep(0.2)

        net_margin = info.get("profitMargins")      # GAAP net margin
        op_margin = info.get("operatingMargins")    # Operating (closer to adjusted)
        gross_margin = info.get("grossMargins")
        ebitda = info.get("ebitda")
        net_income = info.get("netIncomeToCommon")
        revenue = info.get("totalRevenue")

        flags_detail = []

        # Check 1: GAAP loss but positive EBITDA (classic adjusted metric abuse)
        if net_income is not None and ebitda is not None:
            if net_income < 0 and ebitda > 0:
                gap_pct = abs(net_income - ebitda) / max(abs(ebitda), 1) * 100
                if gap_pct > 50:
                    score += 15
                    flags_detail.append(
                        f"GAAP net loss ${net_income/1e6:.0f}M vs positive EBITDA ${ebitda/1e6:.0f}M — "
                        f"large adjusted/GAAP gap ({gap_pct:.0f}%)"
                    )
                else:
                    score += 6
                    flags_detail.append("GAAP loss but positive EBITDA — monitor adjusted add-backs")

        # Check 2: Operating margin >> net margin (heavy below-the-line charges)
        if op_margin is not None and net_margin is not None:
            divergence = op_margin - net_margin
            if divergence > 0.20:
                score += 10
                flags_detail.append(
                    f"Operating margin {op_margin:.0%} vs net margin {net_margin:.0%} — "
                    f"{divergence:.0%} gap suggests large below-the-line charges"
                )
            elif divergence > 0.10:
                score += 4
                flags_detail.append(
                    f"Moderate GAAP/operating divergence: {op_margin:.0%} vs {net_margin:.0%}"
                )

        # Check 3: Gross margin eroding while revenue grows
        rev_growth = info.get("revenueGrowth") or 0
        if gross_margin is not None and rev_growth > 0.10 and gross_margin < 0.20:
            score += 5
            flags_detail.append(
                f"Thin gross margin {gross_margin:.0%} despite {rev_growth:.0%} revenue growth — "
                f"pricing power concern"
            )

        if score == 0:
            detail = "GAAP/adjusted metrics broadly in line — no significant divergence"
        else:
            detail = " | ".join(flags_detail)

    except Exception as e:
        detail = f"GAAP divergence check failed: {str(e)[:50]}"

    return {"score": min(score, 25), "detail": detail}


# ==============================================================================
# SECTION 4: DIVIDEND SUSTAINABILITY
# ==============================================================================

def check_payout_sustainability(ticker: str) -> dict:
    """
    Checks dividend sustainability via FCF payout ratio.
    dividends / FCF > 100% = unsustainable payout risk.
    """
    score = 0
    detail = "No dividend paid"

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        time.sleep(0.2)

        div_rate = info.get("dividendRate")       # Annual $ per share
        div_yield = info.get("dividendYield")     # Decimal
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        fcf = info.get("freeCashflow")            # TTM free cash flow
        payout_ratio = info.get("payoutRatio")    # vs earnings

        if not div_rate or div_rate == 0:
            return {"score": 0, "detail": "No dividend — payout sustainability N/A", "payout_ratio_fcf": None}

        # FCF payout ratio (more conservative than earnings-based)
        fcf_payout = None
        if shares and fcf and shares > 0:
            total_dividends = div_rate * shares
            if fcf > 0:
                fcf_payout = round(total_dividends / fcf * 100, 1)
                if fcf_payout > 120:
                    score = 20
                    detail = f"Dividend payout {fcf_payout:.0f}% of FCF — unsustainable, likely to be cut"
                elif fcf_payout > 90:
                    score = 12
                    detail = f"Dividend payout {fcf_payout:.0f}% of FCF — elevated, limited safety margin"
                elif fcf_payout > 70:
                    score = 5
                    detail = f"Dividend payout {fcf_payout:.0f}% of FCF — manageable but watch FCF trend"
                else:
                    detail = f"Dividend payout {fcf_payout:.0f}% of FCF — well-covered"
            else:
                score = 15
                detail = f"Dividend ${div_rate:.2f}/share paid on negative FCF — funded by debt/asset sales"
                fcf_payout = None  # Can't compute ratio on negative FCF

        elif payout_ratio is not None and payout_ratio > 0:
            # Fallback to earnings-based payout ratio
            if payout_ratio > 1.0:
                score = 15
                detail = f"Earnings payout ratio {payout_ratio:.0%} — dividends exceed GAAP earnings"
            elif payout_ratio > 0.80:
                score = 8
                detail = f"Earnings payout ratio {payout_ratio:.0%} — limited reinvestment capacity"
            else:
                detail = f"Earnings payout ratio {payout_ratio:.0%} — appears sustainable"

        return {"score": score, "detail": detail, "payout_ratio_fcf": fcf_payout}

    except Exception as e:
        return {"score": 0, "detail": f"Payout check failed: {str(e)[:50]}", "payout_ratio_fcf": None}


# ==============================================================================
# SECTION 5: REVENUE QUALITY (GROWTH vs MARGIN TREND)
# ==============================================================================

def check_revenue_quality(ticker: str) -> dict:
    """
    Compares revenue growth vs gross profit growth.
    Revenue growing faster than gross profit → margin compression → quality decay.
    Also flags deceleration in revenue despite analyst consensus growth.
    """
    score = 0
    detail = "Revenue quality check: no data"

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        time.sleep(0.2)

        rev_growth_yoy = info.get("revenueGrowth")
        gross_margin = info.get("grossMargins")
        op_margin = info.get("operatingMargins")

        # Try to get 2 years of annual revenue to compute growth manually
        t = yf.Ticker(ticker)
        try:
            fin = t.financials  # annual income statement
            if fin is not None and not fin.empty and fin.shape[1] >= 2:
                rev_row = None
                gp_row = None
                for idx in fin.index:
                    name = str(idx).lower()
                    if "total revenue" in name or "revenue" == name.strip():
                        rev_row = fin.loc[idx]
                    if "gross profit" in name:
                        gp_row = fin.loc[idx]

                if rev_row is not None and len(rev_row) >= 2:
                    rev_curr = float(rev_row.iloc[0])
                    rev_prev = float(rev_row.iloc[1])
                    if rev_prev > 0:
                        rev_growth_actual = (rev_curr / rev_prev - 1)
                        if rev_growth_yoy is None:
                            rev_growth_yoy = rev_growth_actual

                    if gp_row is not None and len(gp_row) >= 2:
                        gp_curr = float(gp_row.iloc[0])
                        gp_prev = float(gp_row.iloc[1])
                        if gp_prev > 0 and rev_prev > 0:
                            gp_growth = (gp_curr / gp_prev - 1)
                            rev_growth_actual = (rev_curr / rev_prev - 1)
                            # Margin compression: revenue grows faster than gross profit
                            divergence = rev_growth_actual - gp_growth
                            if divergence > 0.10 and rev_growth_actual > 0.05:
                                score += 12
                                detail = (
                                    f"Revenue +{rev_growth_actual:.0%} YoY vs gross profit +{gp_growth:.0%} — "
                                    f"margin compression ({divergence:.0%} divergence), watch pricing power"
                                )
                            elif gp_growth < 0 and rev_growth_actual > 0:
                                score += 8
                                detail = f"Gross profit declining {gp_growth:.0%} despite revenue growth {rev_growth_actual:.0%}"
                            else:
                                detail = f"Revenue +{rev_growth_actual:.0%} with gross profit +{gp_growth:.0%} — quality growth"
        except Exception:
            pass

        if score == 0 and detail == "Revenue quality check: no data":
            if rev_growth_yoy is not None:
                if rev_growth_yoy < -0.05:
                    score = 8
                    detail = f"Revenue declining {rev_growth_yoy:.0%} YoY — top-line deterioration"
                elif rev_growth_yoy < 0:
                    score = 4
                    detail = f"Slight revenue decline {rev_growth_yoy:.0%} — watch for trend continuation"
                else:
                    detail = f"Revenue growing {rev_growth_yoy:.0%} YoY — no quality flags"
            else:
                detail = "Revenue growth data unavailable"

    except Exception as e:
        detail = f"Revenue quality check failed: {str(e)[:50]}"
        score = 0

    return {"score": min(score, 25), "detail": detail}


# ==============================================================================
# SECTION 6: COMPOSITE SCORING
# ==============================================================================

def run_red_flag_screener(ticker: str, skip_edgar: bool = False) -> dict:
    """
    Run all 5 red flag checks and return composite score + detail.

    skip_edgar=True skips the SEC restatement check (faster, avoids network calls).
    """
    ticker = ticker.upper()
    flags = []
    all_checks = {}

    # Check 1: Restatements (EDGAR)
    if not skip_edgar:
        restatement = check_restatements(ticker)
    else:
        restatement = {"score": 0, "detail": "EDGAR restatement check skipped (skip_edgar=True)", "findings": []}
    all_checks["restatement"] = restatement
    if restatement["score"] > 0:
        flags.append(f"RESTATEMENT: {restatement['detail']}")

    # Check 2: Accruals
    accruals = check_accruals(ticker)
    all_checks["accruals"] = accruals
    if accruals["score"] > 5:
        flags.append(f"ACCRUALS: {accruals['detail']}")

    # Check 3: GAAP divergence
    gaap = check_gaap_divergence(ticker)
    all_checks["gaap_divergence"] = gaap
    if gaap["score"] > 5:
        flags.append(f"GAAP: {gaap['detail']}")

    # Check 4: Payout sustainability
    payout = check_payout_sustainability(ticker)
    all_checks["payout_risk"] = payout
    if payout["score"] > 0:
        flags.append(f"PAYOUT: {payout['detail']}")

    # Check 5: Revenue quality
    rev_quality = check_revenue_quality(ticker)
    all_checks["revenue_quality"] = rev_quality
    if rev_quality["score"] > 5:
        flags.append(f"REVENUE: {rev_quality['detail']}")

    total = sum(c["score"] for c in all_checks.values())
    total = min(total, 100)

    if total <= 20:
        risk_level = "CLEAN"
    elif total <= 45:
        risk_level = "CAUTION"
    elif total <= 70:
        risk_level = "WARNING"
    else:
        risk_level = "CRITICAL"

    # Data quality
    checks_with_data = sum(
        1 for c in all_checks.values()
        if "failed" not in c.get("detail", "").lower()
        and "unavailable" not in c.get("detail", "").lower()
        and "skipped" not in c.get("detail", "").lower()
    )
    data_quality = "HIGH" if checks_with_data >= 4 else ("MEDIUM" if checks_with_data >= 3 else "LOW")

    if not flags:
        flags.append("No material accounting red flags detected")

    return {
        "ticker": ticker,
        "red_flag_score": total,
        "risk_level": risk_level,
        "checks": all_checks,
        "flags": flags,
        "data_quality": data_quality,
    }


# ==============================================================================
# CLI
# ==============================================================================

def _read_watchlist(path: str = "./watchlist.txt") -> list:
    tickers = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                t = line.split("#")[0].strip().upper()
                if t and not t.endswith("-USD"):  # skip crypto
                    tickers.append(t)
    except FileNotFoundError:
        pass
    return list(dict.fromkeys(tickers))


def main():
    parser = argparse.ArgumentParser(description="Red Flag Screener v1.0")
    parser.add_argument("--ticker", type=str)
    parser.add_argument("--tickers", type=str, help="Comma-separated")
    parser.add_argument("--watchlist", action="store_true")
    parser.add_argument("--skip-edgar", action="store_true",
                        help="Skip SEC restatement check (faster)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=None)
    args = parser.parse_args()

    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    elif args.watchlist:
        tickers = _read_watchlist()
    else:
        parser.print_help()
        return

    results = []
    for i, t in enumerate(tickers):
        print(f"\r  Scanning {t} ({i+1}/{len(tickers)})...", end="", flush=True)
        r = run_red_flag_screener(t, skip_edgar=args.skip_edgar)
        results.append(r)
        time.sleep(0.2)

    print(f"\r  Done: {len(results)} tickers scanned." + " " * 30)

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))
        return

    # Sort by risk score descending
    results.sort(key=lambda x: x["red_flag_score"], reverse=True)
    if args.top:
        results = results[:args.top]

    print(f"\n{'─' * 70}")
    print(f"  RED FLAG SCREENER — {len(results)} tickers")
    print(f"{'─' * 70}")
    print(f"\n  {'Ticker':<8} {'Score':>6}  {'Level':<10}  {'Top Flag'}")
    print(f"  {'─' * 66}")
    for r in results:
        icon = {"CLEAN": "✅", "CAUTION": "🟡", "WARNING": "🟠", "CRITICAL": "🔴"}.get(r["risk_level"], "◯")
        top_flag = r["flags"][0][:55] if r["flags"] else ""
        print(f"  {r['ticker']:<8} {r['red_flag_score']:>5}/100  {icon} {r['risk_level']:<8}  {top_flag}")

    if len(results) == 1:
        r = results[0]
        print(f"\n{'─' * 60}")
        print(f"  DETAIL: {r['ticker']}")
        print(f"{'─' * 60}")
        for name, check in r["checks"].items():
            score_bar = "█" * (check["score"] // 5) + "░" * (5 - check["score"] // 5)
            print(f"\n  [{score_bar}] {name.upper()} ({check['score']}/25)")
            print(f"    {check.get('detail', '')}")

    # Export CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = os.path.join(OUTPUT_DIR, f"red_flags_{date_str}.csv")
    rows = []
    for r in results:
        rows.append({
            "ticker": r["ticker"],
            "red_flag_score": r["red_flag_score"],
            "risk_level": r["risk_level"],
            "restatement_score": r["checks"]["restatement"]["score"],
            "accruals_score": r["checks"]["accruals"]["score"],
            "accruals_ratio": r["checks"]["accruals"].get("ratio"),
            "gaap_score": r["checks"]["gaap_divergence"]["score"],
            "payout_score": r["checks"]["payout_risk"]["score"],
            "payout_ratio_fcf": r["checks"]["payout_risk"].get("payout_ratio_fcf"),
            "rev_quality_score": r["checks"]["revenue_quality"]["score"],
            "data_quality": r["data_quality"],
            "top_flag": r["flags"][0] if r["flags"] else "",
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"\n  📁 Exported: {path}\n")


if __name__ == "__main__":
    main()
