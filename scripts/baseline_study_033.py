"""
scripts/baseline_study_033.py — TRD-033 Baseline Study

Measures the current confirmation pipeline's historical precision,
forward returns, and false-positive burden.

Usage:
    python3 scripts/baseline_study_033.py

Requirements:
    - Python 3.10+ (uses X | Y union syntax in annotations)
    - psycopg2 importable (for DB access via utils.db)
    - DATABASE_URL environment variable set

Outputs:
    reports/baseline_study_TRD033.md
"""

from __future__ import annotations  # allows X | Y annotations on Python 3.9 at parse time

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# ── Constants ────────────────────────────────────────────────────────────────

HORIZONS = [5, 10, 20, 40]  # trading-day forward windows
TP_THRESHOLD_PCT = 5.0       # % move to call a directional alert a "true positive"
LARGE_MOVE_PCT = 10.0        # % move to qualify for lead-time analysis

SECTOR_ETF = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Financial Services": "XLF",
    "Communication Services": "XLC",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}
DEFAULT_BENCHMARK = "SPY"

RANK_BUCKETS = [(1, 5), (6, 10), (11, 20)]

REPORT_PATH = Path(__file__).parent.parent / "reports" / "baseline_study_TRD033.md"


# ── Data loading ─────────────────────────────────────────────────────────────

def load_rankings() -> pd.DataFrame:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.db import get_connection  # lazy: keeps psycopg2 out of import-time scope
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT run_date, rank, ticker, direction,
                   priority_score, agreement_score, sector,
                   t1_price, stop_price, prob_t1, prob_combined
            FROM daily_rankings
            ORDER BY run_date, rank
        """)
        rows = cur.fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    df["run_date"] = pd.to_datetime(df["run_date"]).dt.date
    df["priority_score"] = pd.to_numeric(df["priority_score"], errors="coerce")
    df["agreement_score"] = pd.to_numeric(df["agreement_score"], errors="coerce")
    df["prob_combined"] = pd.to_numeric(df["prob_combined"], errors="coerce")
    return df


def trading_days_calendar(start: date, end: date) -> list[date]:
    """Return list of trading days between start and end inclusive."""
    idx = pd.bdate_range(start=start, end=end, freq="C",
                          holidays=[])  # simplified, ignores US market holidays
    return [d.date() for d in idx]


def nth_trading_day(alert_date: date, n: int,
                    tdays: list[date]) -> date | None:
    """Return the trading day that is n days after alert_date."""
    try:
        pos = tdays.index(alert_date)
    except ValueError:
        return None
    target_pos = pos + n
    if target_pos >= len(tdays):
        return None
    return tdays[target_pos]


def fetch_prices(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Download adjusted close prices for a list of tickers."""
    ticker_str = " ".join(tickers)
    raw = yf.download(
        ticker_str, start=start, end=end + timedelta(days=3),
        auto_adjust=True, progress=False, threads=True
    )
    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"]
    else:
        prices = raw[["Close"]].rename(columns={"Close": tickers[0]})
    prices.index = pd.to_datetime(prices.index).date
    return prices


# ── Core computation ──────────────────────────────────────────────────────────

def compute_forward_returns(
    df: pd.DataFrame,
    prices: pd.DataFrame,
    sector_prices: pd.DataFrame,
    tdays: list[date],
    today: date,
) -> pd.DataFrame:
    """
    For each alert row, compute forward returns at each horizon.
    Appends columns: ret_5d, ret_10d, ret_20d, ret_40d (raw),
                     adj_5d, adj_10d, adj_20d, adj_40d (sector-adjusted),
                     mature_Nd (True if horizon is fully past today).
    """
    results = []
    for _, row in df.iterrows():
        alert_date = row["run_date"]
        ticker = row["ticker"]
        sector = row.get("sector") or "Unknown"
        bench_etf = SECTOR_ETF.get(sector, DEFAULT_BENCHMARK)

        entry_price = (
            prices[ticker].get(alert_date) if ticker in prices.columns else None
        )

        rec = row.to_dict()

        for h in HORIZONS:
            fwd_date = nth_trading_day(alert_date, h, tdays)
            mature = fwd_date is not None and fwd_date <= today
            rec[f"mature_{h}d"] = mature

            if entry_price and entry_price > 0 and mature and fwd_date and ticker in prices.columns:
                fwd_price = prices[ticker].get(fwd_date)
                bench_entry = sector_prices[bench_etf].get(alert_date) if bench_etf in sector_prices.columns else None
                bench_fwd = sector_prices[bench_etf].get(fwd_date) if bench_etf in sector_prices.columns else None

                if fwd_price and fwd_price > 0:
                    raw_ret = (fwd_price / entry_price - 1) * 100.0
                    rec[f"ret_{h}d"] = raw_ret

                    if bench_entry and bench_fwd and bench_entry > 0:
                        bench_ret = (bench_fwd / bench_entry - 1) * 100.0
                        rec[f"adj_{h}d"] = raw_ret - bench_ret
                    else:
                        rec[f"adj_{h}d"] = None
                else:
                    rec[f"ret_{h}d"] = None
                    rec[f"adj_{h}d"] = None
            else:
                rec[f"ret_{h}d"] = None
                rec[f"adj_{h}d"] = None

        results.append(rec)

    return pd.DataFrame(results)


def direction_adjusted_return(ret: float, direction: str) -> float:
    """Sign-flip for BEAR alerts."""
    if direction == "BEAR":
        return -ret
    return ret


def is_true_positive(ret: float | None, direction: str,
                     threshold: float = TP_THRESHOLD_PCT) -> bool | None:
    if ret is None or np.isnan(ret):
        return None
    if direction == "NEUTRAL":
        return None
    return direction_adjusted_return(ret, direction) >= threshold


# ── Report sections ───────────────────────────────────────────────────────────

def section_data_summary(df: pd.DataFrame, today: date) -> str:
    run_dates = sorted(df["run_date"].unique())
    total_alerts = len(df)
    dir_dist = df["direction"].value_counts()

    directional = df[df["direction"].isin(["BULL", "BEAR"])]
    avg_per_day = df.groupby("run_date").size().mean()
    # Reindex over all run dates so zero-directional days are included (same as section_alert_volume).
    avg_dir_per_day = (
        df[df["direction"].isin(["BULL", "BEAR"])]
        .groupby("run_date").size()
        .reindex(df["run_date"].unique(), fill_value=0)
    )

    lines = [
        "## 1. Data Summary",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Analysis date | {today} |",
        f"| Pipeline start | {run_dates[0]} |",
        f"| Pipeline end | {run_dates[-1]} |",
        f"| Calendar span | {(run_dates[-1] - run_dates[0]).days} calendar days (~{len(run_dates)} trading days) |",
        f"| Total alert rows | {total_alerts:,} |",
        f"| Distinct tickers ever ranked | {df['ticker'].nunique()} |",
        f"| Avg tickers per run day | {avg_per_day:.1f} |",
        f"| BULL alerts | {dir_dist.get('BULL', 0)} ({dir_dist.get('BULL', 0)/total_alerts*100:.1f}%) |",
        f"| BEAR alerts | {dir_dist.get('BEAR', 0)} ({dir_dist.get('BEAR', 0)/total_alerts*100:.1f}%) |",
        f"| NEUTRAL alerts | {dir_dist.get('NEUTRAL', 0)} ({dir_dist.get('NEUTRAL', 0)/total_alerts*100:.1f}%) |",
        f"| Directional alerts (BULL+BEAR) | {len(directional)} ({len(directional)/total_alerts*100:.1f}%) |",
        f"| Avg directional alerts per run day | {avg_dir_per_day.mean():.2f} (median {avg_dir_per_day.median():.2f}) |",
        "",
        "**⚠ Data limitation**: The system has been live for ~8 weeks, far short of the 18 months",
        "requested in TRD-033. All metrics in this report are based on this constrained sample.",
        "Results should be treated as directional indicators only, not statistically robust benchmarks.",
        "",
        "**Horizon maturability**: Trading-day forward windows by available data:",
    ]

    for h in HORIZONS:
        mature_col = f"mature_{h}d"
        if mature_col in df.columns:
            n_mature = df[mature_col].sum()
            lines.append(f"- **{h}d**: {n_mature:,} alert-rows with mature forward data")
    lines.append("")
    return "\n".join(lines)


def section_forward_returns(df: pd.DataFrame) -> str:
    lines = ["## 2. Forward Returns by Direction"]
    lines.append("")
    lines.append("Median forward return (%) for each horizon and direction subset. Only mature observations.")
    lines.append("")

    for direction in ["BULL", "BEAR", "NEUTRAL", "ALL"]:
        if direction == "ALL":
            subset = df
        else:
            subset = df[df["direction"] == direction]

        if len(subset) == 0:
            continue

        lines.append(f"### {direction} alerts (n={len(subset)})")
        lines.append("")
        lines.append("| Horizon | N mature | Median raw ret % | Median sector-adj % | % positive |")
        lines.append("|---|---|---|---|---|")

        for h in HORIZONS:
            ret_col = f"ret_{h}d"
            adj_col = f"adj_{h}d"
            mat_col = f"mature_{h}d"

            if ret_col not in subset.columns:
                lines.append(f"| {h}d | 0 | — | — | — |")
                continue

            mature_subset = subset[subset[mat_col] == True]
            valid = mature_subset[ret_col].dropna()
            valid_adj = mature_subset[adj_col].dropna() if adj_col in mature_subset.columns else pd.Series([], dtype=float)

            n = len(valid)
            if n == 0:
                lines.append(f"| {h}d | 0 | — | — | — |")
                continue

            if direction == "BEAR":
                pct_pos = (valid < 0).mean() * 100  # BEAR "correct" = negative return
            else:
                pct_pos = (valid > 0).mean() * 100

            med_raw = valid.median()
            med_adj = valid_adj.median() if len(valid_adj) > 0 else float("nan")
            adj_str = f"{med_adj:+.2f}%" if not np.isnan(med_adj) else "—"
            lines.append(f"| {h}d | {n} | {med_raw:+.2f}% | {adj_str} | {pct_pos:.0f}% |")

        lines.append("")

    return "\n".join(lines)


def section_precision_by_bucket(df: pd.DataFrame) -> str:
    lines = [
        "## 3. Precision by Rank Bucket",
        "",
        f"Precision = fraction of directional alerts (BULL/BEAR) where the direction-adjusted",
        f"return exceeds +{TP_THRESHOLD_PCT:.0f}% within the given horizon. NEUTRAL alerts excluded.",
        "",
    ]

    directional = df[df["direction"].isin(["BULL", "BEAR"])].copy()

    if len(directional) == 0:
        lines.append("*No directional alerts available for precision analysis.*")
        lines.append("")
        return "\n".join(lines)

    for h in HORIZONS:
        ret_col = f"ret_{h}d"
        mat_col = f"mature_{h}d"

        if ret_col not in directional.columns:
            continue

        mature = directional[directional[mat_col] == True].copy()
        mature["tp"] = mature.apply(
            lambda r: is_true_positive(r[ret_col], r["direction"]), axis=1
        )
        mature_valid = mature[mature["tp"].notna()]

        if len(mature_valid) == 0:
            lines.append(f"### {h}d horizon — no mature observations")
            lines.append("")
            continue

        lines.append(f"### {h}d horizon (N={len(mature_valid)} mature directional alerts)")
        lines.append("")
        lines.append("| Rank bucket | N alerts | Precision | FP rate |")
        lines.append("|---|---|---|---|")

        for lo, hi in RANK_BUCKETS:
            bucket = mature_valid[
                (mature_valid["rank"] >= lo) & (mature_valid["rank"] <= hi)
            ]
            n = len(bucket)
            if n == 0:
                lines.append(f"| Ranks {lo}–{hi} | 0 | — | — |")
                continue
            precision = bucket["tp"].sum() / n * 100
            fp_rate = (1 - bucket["tp"].mean()) * 100
            lines.append(f"| Ranks {lo}–{hi} | {n} | {precision:.0f}% | {fp_rate:.0f}% |")

        overall_precision = mature_valid["tp"].mean() * 100
        lines.append(f"| **All ranks** | **{len(mature_valid)}** | **{overall_precision:.0f}%** | **{100 - overall_precision:.0f}%** |")
        lines.append("")

    return "\n".join(lines)


def section_alert_volume(df: pd.DataFrame) -> str:
    per_day = df.groupby("run_date").size()
    dir_per_day = df[df["direction"].isin(["BULL", "BEAR"])].groupby("run_date").size().reindex(
        df["run_date"].unique(), fill_value=0
    )

    lines = [
        "## 4. Alert Volume",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Mean tickers/day (all) | {per_day.mean():.1f} |",
        f"| Median tickers/day (all) | {per_day.median():.1f} |",
        f"| Max tickers/day | {per_day.max()} |",
        f"| Min tickers/day | {per_day.min()} |",
        f"| Mean directional alerts/day | {dir_per_day.mean():.2f} |",
        f"| Days with zero directional alerts | {(dir_per_day == 0).sum()} of {len(dir_per_day)} |",
        "",
    ]
    return "\n".join(lines)


def section_lead_time(df: pd.DataFrame) -> str:
    """
    Lead-time proxy: for alerts where 10d return > LARGE_MOVE_PCT,
    what was the 5d return as a fraction of the 10d return?
    A high fraction means most of the move happened quickly;
    a low fraction means the alert had room to run.
    """
    lines = [
        "## 5. Lead-Time Proxy",
        "",
        "A confirmation pipeline that surfaces names too late will show most of its move",
        "already captured in the first 5 days. We proxy this with:  ",
        "**5d-capture ratio** = ret_5d / ret_10d for alerts where ret_10d > 10%.",
        "",
        "A ratio near 1.0 means the move was nearly complete within 5 days of the alert;",
        "a ratio near 0.5 means the alert still had 5 days of comparable upside remaining.",
        "",
    ]

    if "ret_5d" not in df.columns or "ret_10d" not in df.columns:
        lines.append("*Insufficient data to compute lead-time proxy.*")
        lines.append("")
        return "\n".join(lines)

    mature = df[df["mature_10d"] == True].copy()
    large_movers = mature[mature["ret_10d"] > LARGE_MOVE_PCT].copy()

    if len(large_movers) < 5:
        lines.append(
            f"*Too few large-move events (N={len(large_movers)}, threshold >{LARGE_MOVE_PCT}%)"
            " for reliable lead-time analysis. This metric will become meaningful as the"
            " dataset grows.*"
        )
        lines.append("")
        return "\n".join(lines)

    large_movers["capture_ratio"] = large_movers["ret_5d"] / large_movers["ret_10d"]
    valid = large_movers["capture_ratio"].replace([np.inf, -np.inf], np.nan).dropna()

    lines += [
        f"| Metric | Value |",
        "|---|---|",
        f"| Large-mover alerts (>10% in 10d) | {len(valid)} |",
        f"| Median 5d-capture ratio | {valid.median():.2f} |",
        f"| Mean 5d-capture ratio | {valid.mean():.2f} |",
        f"| Fraction where 5d-capture > 0.8 (mostly done) | {(valid > 0.8).mean()*100:.0f}% |",
        f"| Fraction where 5d-capture < 0.3 (still early) | {(valid < 0.3).mean()*100:.0f}% |",
        "",
    ]
    return "\n".join(lines)


def section_score_distribution(df: pd.DataFrame) -> str:
    lines = [
        "## 6. Score Distributions",
        "",
        "Agreement score and priority score describe how confident and multi-signal each alert is.",
        "",
        "| Metric | Mean | Median | P25 | P75 |",
        "|---|---|---|---|---|",
    ]

    for col, label in [
        ("priority_score", "Priority score"),
        ("agreement_score", "Agreement score"),
        ("prob_combined", "prob_combined"),
    ]:
        valid = df[col].dropna()
        if len(valid) == 0:
            lines.append(f"| {label} | — | — | — | — |")
        else:
            lines.append(
                f"| {label} | {valid.mean():.2f} | {valid.median():.2f} "
                f"| {valid.quantile(0.25):.2f} | {valid.quantile(0.75):.2f} |"
            )
    lines.append("")

    dir_only = df[df["direction"].isin(["BULL", "BEAR"])]
    if len(dir_only) > 0:
        lines.append(
            "Agreement score for directional alerts only — higher agreement should predict precision:"
        )
        lines.append("")
        lines.append("| Direction | N | Mean agreement | Median agreement |")
        lines.append("|---|---|---|---|")
        for d in ["BULL", "BEAR"]:
            sub = dir_only[dir_only["direction"] == d]["agreement_score"].dropna()
            if len(sub):
                lines.append(f"| {d} | {len(sub)} | {sub.mean():.3f} | {sub.median():.3f} |")
    lines.append("")

    return "\n".join(lines)


def section_key_findings_and_benchmark(df: pd.DataFrame, today: date) -> str:
    run_dates = sorted(df["run_date"].unique())
    span_days = len(run_dates)
    n_directional = len(df[df["direction"].isin(["BULL", "BEAR"])])
    pct_neutral = (df["direction"] == "NEUTRAL").mean() * 100

    # Check 20d mature forward return data
    if "ret_20d" in df.columns and "mature_20d" in df.columns:
        mature20 = df[df["mature_20d"] == True]["ret_20d"].dropna()
        median_20d = f"{mature20.median():+.2f}%" if len(mature20) else "N/A"
        n_20d = len(mature20)
    else:
        median_20d = "N/A"
        n_20d = 0

    lines = [
        "## 7. Key Findings and Benchmark Table",
        "",
        "These findings form the benchmark that the pre-breakout pipeline (TRD-034 onwards)",
        "must beat or complement.",
        "",
        "### Critical Limitations",
        "",
        "1. **Data span is ~8 weeks (~{} trading days), not 18 months.** All metrics below are".format(span_days),
        "   preliminary estimates. They will require revisiting once ≥6 months of data exist.",
        "",
        "2. **{:.1f}% of all alerts are NEUTRAL direction.** The confirmation pipeline rarely".format(pct_neutral),
        "   commits to a directional view. Only {:,} of {:,} total alert-rows are BULL or BEAR.".format(n_directional, len(df)),
        "   Precision and false-positive metrics apply only to that small directional subset.",
        "",
        "3. **40-day forward data is immature for all alerts.** The first alert dates are",
        "   ~2026-04-08; 40 trading days from that date falls in mid-June 2026, which is",
        "   after today ({}).".format(today),
        "",
        "### Benchmark Table",
        "",
        "| Metric | Current value | Target for pre-breakout pipeline |",
        "|---|---|---|",
        f"| Trading-day span in sample | ~{span_days} days | ≥120 trading days to revisit |",
        f"| Directional alert rate | {100-pct_neutral:.1f}% | TBD (comparison only) |",
        f"| Avg tickers/day | {df.groupby('run_date').size().mean():.1f} | TBD |",
        f"| Median 20d raw return (all, N={n_20d}) | {median_20d} | Outperform by >2% sector-adj |",
        f"| False-positive rate (directional, 10d) | requires directional sample | <40% target |",
        f"| 40d horizon data | immature | revisit when ≥40 trading days past all alerts |",
        "",
        "### Implication for the Pre-Breakout Program",
        "",
        "- The confirmation pipeline's main structural characteristic is that it outputs a high",
        "  fraction of NEUTRAL alerts — the system is conservative rather than directional.",
        "- A pre-breakout pipeline that surfaces BULL setups earlier must beat a near-neutral",
        "  baseline. That is not a high precision bar in absolute terms, but the lead-time",
        "  advantage is the primary value proposition.",
        "- This baseline should be re-run at ~90 and ~120 trading days of history to get",
        "  statistically meaningful precision estimates (target: N ≥ 50 mature directional alerts).",
        "",
    ]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    today = date.today()
    print("TRD-033 Baseline Study")
    print(f"Analysis date: {today}")
    print()

    # 1. Load rankings
    print("Loading daily_rankings...")
    df = load_rankings()
    print(f"  {len(df):,} rows, {df['run_date'].nunique()} dates, "
          f"{df['ticker'].nunique()} distinct tickers")

    run_dates = sorted(df["run_date"].unique())
    price_start = run_dates[0] - timedelta(days=10)
    price_end = today

    # 2. Collect all tickers + sector ETFs
    all_tickers = sorted(df["ticker"].unique().tolist())
    sector_etfs = sorted(set(SECTOR_ETF.values()) | {DEFAULT_BENCHMARK})
    all_symbols = all_tickers + sector_etfs
    print(f"Downloading prices for {len(all_symbols)} symbols "
          f"({price_start} → {price_end})...")

    raw_prices = fetch_prices(all_symbols, price_start, price_end)
    prices = raw_prices[[c for c in raw_prices.columns if c in all_tickers]]
    sector_prices = raw_prices[[c for c in raw_prices.columns if c in sector_etfs]]
    print(f"  Price matrix: {prices.shape[0]} trading days × "
          f"{prices.shape[1]} tickers")

    missing = set(all_tickers) - set(prices.columns)
    if missing:
        print(f"  ⚠ Missing price data for {len(missing)} tickers: "
              f"{sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}")

    # 3. Build trading-day calendar
    tdays = trading_days_calendar(price_start, price_end + timedelta(days=60))

    # 4. Compute forward returns
    print("Computing forward returns...")
    df_out = compute_forward_returns(df, prices, sector_prices, tdays, today)
    print("  Done.")

    # 5. Assemble report
    print("Assembling report...")
    sections = [
        "# TRD-033: Baseline Study — Current Confirmation Pipeline",
        "",
        f"*Generated: {today}*",
        "",
        "This report is the required benchmark for the pre-breakout detection program.",
        "All subsequent tickets (TRD-034 through TRD-040) must reference these metrics",
        "when claiming improvement.",
        "",
        section_data_summary(df_out, today),
        section_forward_returns(df_out),
        section_precision_by_bucket(df_out),
        section_alert_volume(df_out),
        section_lead_time(df_out),
        section_score_distribution(df_out),
        section_key_findings_and_benchmark(df_out, today),
        "---",
        "*Report generated by `scripts/baseline_study_033.py` (TRD-033).*",
        "",
    ]
    report = "\n".join(sections)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Report written: {REPORT_PATH}")


if __name__ == "__main__":
    main()
