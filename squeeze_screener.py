#!/usr/bin/env python3
"""
================================================================================
SQUEEZE SCREENER v1.0 — Short Squeeze Candidate Detector
================================================================================
Ranks stocks by "squeezability" using a weighted scoring model across three
signal groups: Positioning, Mechanics, and Structure.

SCORING MODEL (0–100):
    Positioning (~45%)
        - pct_float_short     (20%) — % of float sold short; higher = more fuel
        - short_pnl_estimate  (15%) — are shorts already underwater?

    Mechanics (~35%)
        - days_to_cover       (15%) — short interest / avg daily volume
        - volume_surge        (10%) — today's vol vs 30-day avg
        - ftd_vs_float         (5%) — recent SEC fail-to-delivers as % of float
        - cost_to_borrow_proxy (5%) — hard-to-borrow flag from Finviz (proxy)

    Structure (~20%)
        - market_cap           (7%) — log-scaled; smaller = higher score
        - float_size           (7%) — log-scaled; smaller float = easier to move
        - price_divergence     (6%) — price rising while short interest is high

    Override: recent_squeeze flag → final_score = 0 (squeeze already played out)

DATA SOURCES (all free):
    - yfinance       — price history, volume, short interest, market cap
    - Finviz         — supplemental short data, hard-to-borrow indicator
    - SEC EDGAR      — bi-monthly fail-to-deliver CSVs

HONEST LIMITATIONS:
    - % float on loan (Ortex/S3): NOT freely available — signal omitted
    - Short vs loan ratio: same issue — omitted
    - Cost-to-borrow actual rate: NOT freely available — using Finviz HTB proxy
    - FTD data: ~2-week lag, bi-monthly cadence; use as trailing signal
    - Finviz may rate-limit; scraped with 1s delays and User-Agent header

USAGE:
    python3 squeeze_screener.py                       # Screen default universe
    python3 squeeze_screener.py --universe meme       # Meme stock focus
    python3 squeeze_screener.py --ticker GME AMC BBBY # Specific tickers
    python3 squeeze_screener.py --top 15 --min-score 55

IMPORTANT: This is NOT investment advice. Short squeezes require a catalyst
           (news, retail pile-on) that this screener cannot predict.
           For every GME, there are hundreds of high-SI stocks that flatlined.
================================================================================
"""

import argparse
import csv
import io
import json
import logging
import os
import sys
import time
import warnings
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

try:
    from config import OUTPUT_DIR
except ImportError:
    OUTPUT_DIR = "./signals_output"

# ── optional integration: BeautifulSoup for Finviz scraping ──────────────────
try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False


# ==============================================================================
# UNIVERSES
# ==============================================================================

# No hardcoded universes — loaded dynamically at runtime.
# Backward-compat aliases kept as empty lists.
SQUEEZE_UNIVERSE: list = []
MEME_UNIVERSE:    list = []


def _load_squeeze_universe() -> list:
    """
    Return the dynamic squeeze screening universe.
    Priority: watchlist.txt → user_favorites fallback.
    """
    tickers: list = []

    # watchlist.txt
    wl_paths = [
        os.path.join(os.path.dirname(__file__), "watchlist.txt"),
        "./watchlist.txt",
    ]
    for wl_path in wl_paths:
        if os.path.exists(wl_path):
            with open(wl_path) as f:
                for line in f:
                    tok = line.split("#")[0].strip().upper()
                    if tok and "." not in tok and not tok.startswith("TIER") and not tok.startswith("MANUALLY"):
                        tickers.append(tok)
            break

    if not tickers:
        try:
            from favorites import load_favorites
            tickers = load_favorites()
        except Exception:
            pass

    # Exclude crypto
    tickers = [t for t in tickers if not t.endswith("-USD")]
    return list(dict.fromkeys(tickers))


# ==============================================================================
# OUTPUT DATACLASS
# ==============================================================================

@dataclass
class SqueezeScore:
    ticker: str
    final_score: float                          # 0–100  (squeeze probability proxy)
    signal_breakdown: Dict[str, float]          # per-signal raw scores
    juice_target: float                         # estimated % upside if squeezed
    recent_squeeze: bool                        # True = score zeroed out (completed squeeze)
    price: float = 0.0
    short_pct_float: float = 0.0               # e.g. 0.35 = 35%
    days_to_cover: float = 0.0
    market_cap_m: float = 0.0                  # USD millions
    float_m: float = 0.0                       # shares, millions
    flags: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    computed_dtc_30d: float = 0.0              # float-adjusted DTC: (SI%×float)/avg_vol_30d
    compression_recovery_score: float = 0.0    # 3-month drawdown + recovery pattern score
    volume_confirmation_flag: bool = False      # True = volume surge confirming squeeze ignition
    squeeze_state: str = "false"               # "false" | "active" | "completed"
    # CHUNK-06: effective-float fields
    effective_float_estimate: float = 0.0
    large_holder_ownership_pct: float = 0.0
    effective_short_float_ratio: float = 0.0
    effective_float_score: float = 0.0
    extreme_float_lock_flag: bool = False
    large_holder_concentration_flag: bool = False
    effective_float_confidence: str = "unknown"

    @property
    def ev_score(self) -> float:
        """
        Expected-value score = (squeeze probability) × (squeeze magnitude).
        Combines final_score (0–100 setup quality) with juice_target (% upside).
        Use this to rank: a 70-score / 100%-juice beats a 90-score / 20%-juice.

        Scale: 0–150  (100-score × 150%-juice = 150 max, but realistic ceiling ~80)
        """
        return round((self.final_score / 100.0) * self.juice_target, 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ev_score"] = self.ev_score
        d["signal_breakdown"] = self.signal_breakdown
        d["flags"] = self.flags
        return d


# ==============================================================================
# SECTION 1: DATA FETCHERS
# ==============================================================================

def fetch_stock_data(ticker: str, prefetched_hist: "pd.DataFrame | None" = None) -> Optional[dict]:
    """
    Pull price history, short interest, and volume from yfinance.
    Returns None if data is insufficient.

    Args:
        prefetched_hist: Pre-fetched OHLCV DataFrame from yf_cache.bulk_history().
            When provided the expensive stock.history() HTTP call is skipped.
            Pass None (default) to fall back to the normal per-ticker fetch.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        # History — use bulk pre-fetched when available
        if prefetched_hist is not None and not prefetched_hist.empty and len(prefetched_hist) >= 20:
            hist = prefetched_hist
        else:
            hist = stock.history(period="6mo")

        if hist.empty or len(hist) < 20:
            return None

        current_price = float(hist["Close"].iloc[-1])
        current_vol = float(hist["Volume"].iloc[-1])
        avg_vol_30d = float(hist["Volume"].iloc[-30:].mean()) if len(hist) >= 30 else float(hist["Volume"].mean())
        avg_vol_5d = float(hist["Volume"].iloc[-5:].mean())

        market_cap = info.get("marketCap", 0) or 0
        float_shares = info.get("floatShares", 0) or 0
        shares_outstanding = info.get("sharesOutstanding", 0) or 0
        short_pct = info.get("shortPercentOfFloat", 0) or 0
        short_ratio = info.get("shortRatio", 0) or 0   # days to cover

        # Estimate short entry price: use 60-day avg price as a rough proxy
        # for the average short seller's cost basis
        hist_60 = hist["Close"].iloc[-60:] if len(hist) >= 60 else hist["Close"]
        avg_price_60d = float(hist_60.mean())

        return {
            "ticker": ticker,
            "price": current_price,
            "market_cap": market_cap,
            "float_shares": float_shares,
            "shares_outstanding": shares_outstanding,
            "short_pct_float": short_pct,
            "short_ratio_dtc": short_ratio,
            "volume_current": current_vol,
            "volume_avg_30d": avg_vol_30d,
            "volume_avg_5d": avg_vol_5d,
            "avg_price_60d": avg_price_60d,
            "history": hist,
            "info": info,
        }

    except Exception:
        return None


def fetch_finviz_data(ticker: str) -> dict:
    """
    Scrape Finviz quote page for supplemental short data.
    Returns dict with keys: short_float_pct, short_ratio, hard_to_borrow.
    Falls back to empty dict on any failure.

    Note: Finviz may return stale short data (updated twice monthly by FINRA).
    """
    result = {"short_float_pct": None, "short_ratio": None, "hard_to_borrow": False}
    if not _BS4_AVAILABLE:
        return result

    url = f"https://finviz.com/quote.ashx?t={ticker}&ty=c&ta=1&p=d"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return result

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", class_="snapshot-table2")
        if table is None:
            # Try alternate table class
            table = soup.find("table", {"class": "fullview-title"})
        if table is None:
            return result

        cells = table.find_all("td")
        label_map = {}
        for i in range(0, len(cells) - 1, 2):
            label = cells[i].get_text(strip=True)
            value = cells[i + 1].get_text(strip=True)
            label_map[label] = value

        # Short Float % e.g. "35.20%"
        sf = label_map.get("Short Float", "") or label_map.get("Short Float %", "")
        if sf and sf != "-":
            try:
                result["short_float_pct"] = float(sf.replace("%", "")) / 100.0
            except ValueError:
                pass

        # Short Ratio (days to cover)
        sr = label_map.get("Short Ratio", "")
        if sr and sr != "-":
            try:
                result["short_ratio"] = float(sr)
            except ValueError:
                pass

        # Hard-to-borrow indicator: look for "HTB" or borrow rate in page text
        page_text = resp.text.lower()
        result["hard_to_borrow"] = "hard to borrow" in page_text or " htb" in page_text

    except Exception:
        pass

    return result


# SEC FTD data: in-memory cache so we only download once per run
_SEC_FTD_CACHE: Optional[pd.DataFrame] = None
_SEC_FTD_LOADED_AT: Optional[datetime] = None


def _get_sec_ftd_url(year: int, month: int, half: str) -> str:
    """Build the SEC EDGAR FTD download URL for a given period."""
    return (
        f"https://www.sec.gov/data/downloads/fails-to-deliver/"
        f"cnsfails{year:04d}{month:02d}{half}.zip"
    )


def fetch_sec_ftd_data(force_refresh: bool = False) -> pd.DataFrame:
    """
    Download and parse SEC fail-to-deliver data.

    The SEC publishes bi-monthly FTD reports (~2-week lag):
        - First half of month (days 1–15): suffix 'a'
        - Second half (days 16–end): suffix 'b'

    Format: pipe-delimited CSV
        SETTLEMENT DATE | CUSIP | TICKER | QUANTITY (FAILS) | DESCRIPTION | PRICE

    Returns DataFrame with columns: ticker, date, quantity, price.
    Returns empty DataFrame on failure.
    """
    global _SEC_FTD_CACHE, _SEC_FTD_LOADED_AT

    # Use cache for 24 hours within a single day's run
    if (
        not force_refresh
        and _SEC_FTD_CACHE is not None
        and _SEC_FTD_LOADED_AT is not None
        and (datetime.now() - _SEC_FTD_LOADED_AT).total_seconds() < 86400
    ):
        return _SEC_FTD_CACHE

    headers = {
        "User-Agent": "signal-engine-research research@example.com",
        "Accept-Encoding": "gzip, deflate",
    }

    # Try the most recent 2 periods (current half + previous half)
    now = datetime.now()
    periods_to_try: List[Tuple[int, int, str]] = []

    for month_offset in range(3):
        dt = now - timedelta(days=30 * month_offset)
        y, m = dt.year, dt.month
        # Try 'b' (second half) before 'a' (first half) — more recent
        if now.day >= 16 or month_offset > 0:
            periods_to_try.append((y, m, "b"))
        periods_to_try.append((y, m, "a"))

    frames: List[pd.DataFrame] = []
    fetched = 0

    for year, month, half in periods_to_try:
        if fetched >= 2:
            break
        url = _get_sec_ftd_url(year, month, half)
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=15) as resp:
                raw = resp.read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                fname = [n for n in z.namelist() if n.endswith(".txt")][0]
                content = z.read(fname).decode("latin-1")

            reader = csv.DictReader(io.StringIO(content), delimiter="|")
            rows = []
            for row in reader:
                try:
                    ticker = (row.get("TICKER") or row.get("SYMBOL") or "").strip().upper()
                    qty_raw = row.get("QUANTITY (FAILS)") or row.get("QUANTITY") or "0"
                    qty = int(str(qty_raw).replace(",", "").strip() or "0")
                    date_raw = row.get("SETTLEMENT DATE") or row.get("DATE") or ""
                    price_raw = row.get("PRICE") or "0"
                    price = float(str(price_raw).replace(",", "").strip() or "0")
                    if ticker and qty > 0:
                        rows.append({"ticker": ticker, "date": date_raw, "quantity": qty, "price": price})
                except (ValueError, KeyError):
                    continue

            if rows:
                frames.append(pd.DataFrame(rows))
                fetched += 1

        except Exception:
            continue

    if frames:
        df = pd.concat(frames, ignore_index=True)
        _SEC_FTD_CACHE = df
        _SEC_FTD_LOADED_AT = datetime.now()
        return df

    _SEC_FTD_CACHE = pd.DataFrame(columns=["ticker", "date", "quantity", "price"])
    _SEC_FTD_LOADED_AT = datetime.now()
    return _SEC_FTD_CACHE


def get_ftd_for_ticker(ticker: str, ftd_df: pd.DataFrame) -> float:
    """
    Return total FTD quantity for a ticker from the loaded SEC dataframe.
    Normalized as a ratio vs float shares if available.
    Returns raw quantity sum (caller normalizes).
    """
    if ftd_df.empty:
        return 0.0
    rows = ftd_df[ftd_df["ticker"] == ticker.upper()]
    return float(rows["quantity"].sum())


# ==============================================================================
# SECTION 2: SI PERSISTENCE HELPERS  (CHUNK-02)
# ==============================================================================

def compute_si_persistence_score(
    history_rows: list,
    latest_short_pct: float,
    score_date: "date | None" = None,
) -> dict:
    """
    Compute SI persistence score from historical short-interest rows.

    Returns a dict with:
        si_persistence_score   — 0–10 signal score
        si_persistence_count   — number of distinct reporting periods seen
        si_trend_direction     — "rising" | "falling" | "stable" | "unknown"

    Anti-lookahead rules (point-in-time safety):
    1. Only rows where publication_date <= score_date are considered.
    2. Prefer distinct settlement_date values when available; each unique
       settlement_date counts as one FINRA reporting period.
    3. When settlement_date is absent (e.g. yfinance snapshots), use
       publication_date but only count rows as distinct periods when spaced
       >= 10 calendar days apart.  This prevents consecutive daily yfinance
       runs from inflating the distinct-period count.
    4. Fewer than 2 distinct periods → neutral score (5.0), not maximum.
       The signal must earn its score through accumulating real history.
    """
    from datetime import date as _date, timedelta as _td

    score_date = score_date or _date.today()

    def _parse(val) -> "_date | None":
        if val is None:
            return None
        if isinstance(val, _date):
            return val
        try:
            return _date.fromisoformat(str(val)[:10])
        except (ValueError, TypeError):
            return None

    # ── Step 1: filter to point-in-time safe rows ─────────────────────────────
    safe: list = []
    for r in (history_rows or []):
        pub = _parse(r.get("publication_date"))
        if pub is not None and pub <= score_date:
            safe.append(r)

    if not safe:
        return {"si_persistence_score": 5.0, "si_persistence_count": 0, "si_trend_direction": "unknown"}

    # ── Step 2: deduplicate into distinct reporting periods ───────────────────
    # Sort chronologically so the gap-rule applies to the earliest available date.
    def _row_sort_key(r):
        return _parse(r.get("settlement_date")) or _parse(r.get("publication_date")) or _date.min

    safe_sorted = sorted(safe, key=_row_sort_key)

    distinct: list = []
    seen_settlements: set = set()

    for row in safe_sorted:
        s_date = _parse(row.get("settlement_date"))
        p_date = _parse(row.get("publication_date"))

        if s_date is not None:
            # True FINRA row: settlement date is the canonical period identifier.
            if s_date not in seen_settlements:
                seen_settlements.add(s_date)
                distinct.append(row)
        else:
            # yfinance snapshot: apply the 10-day gap rule to avoid counting
            # consecutive daily runs as separate FINRA reporting periods.
            if not distinct:
                distinct.append(row)
            else:
                last_p = _parse(distinct[-1].get("publication_date")) or _date.min
                if p_date is not None and (p_date - last_p).days >= 10:
                    distinct.append(row)

    count = len(distinct)

    if count < 2:
        return {"si_persistence_score": 5.0, "si_persistence_count": count, "si_trend_direction": "unknown"}

    # ── Step 3: extract SI values from distinct periods (oldest → newest) ─────
    si_vals = [float(r["short_pct_float"]) for r in distinct if r.get("short_pct_float") is not None]

    if not si_vals:
        return {"si_persistence_score": 5.0, "si_persistence_count": count, "si_trend_direction": "unknown"}

    # ── Step 4: compute trend ─────────────────────────────────────────────────
    trend = "stable"
    if len(si_vals) >= 2:
        delta = si_vals[-1] - si_vals[0]
        if delta >= 0.05:
            trend = "rising"
        elif delta <= -0.05:
            trend = "falling"

    # ── Step 5: score ─────────────────────────────────────────────────────────
    if count >= 3 and all(v >= 0.40 for v in si_vals):
        score = 10.0
    elif count >= 3 and all(v >= 0.30 for v in si_vals):
        score = 8.0
    elif trend == "rising" and count >= 2:
        score = 7.0
    elif trend == "falling":
        score = 3.0
    else:
        score = 5.0

    return {
        "si_persistence_score": score,
        "si_persistence_count": count,
        "si_trend_direction": trend,
    }


def _load_si_history(ticker: str, as_of_date: "date | None" = None) -> list:
    """
    Load SI history from Supabase for use in score_positioning().
    Returns empty list on any failure (non-fatal — screener must not crash).
    """
    try:
        from utils.supabase_persist import fetch_short_interest_history
        return fetch_short_interest_history(ticker, as_of_date=as_of_date)
    except Exception:
        return []


def _load_filing_catalysts(ticker: str, as_of_date: "date | None" = None) -> list:
    """
    Load filing_catalysts rows for use in effective-float analysis.
    Returns empty list on any failure (non-fatal).
    """
    try:
        from utils.supabase_persist import fetch_filing_catalysts
        return fetch_filing_catalysts(ticker, as_of_date=as_of_date)
    except Exception:
        return []


# ==============================================================================
# SECTION 3: SIGNAL SCORING
# ==============================================================================
# Each scoring function returns: {"score": float, "max": float, "flags": List[str]}
# Scores are on a 0–10 scale within each function; weights applied in composite.

def score_positioning(data: dict, finviz: dict, si_history: list | None = None) -> dict:
    """
    Positioning signals (~45% of composite).
    - pct_float_short:    primary fuel for squeeze                  weight 0.20
    - short_pnl_estimate: are shorts already losing?                weight 0.10
    - si_persistence:     SI elevated across multiple FINRA periods  weight 0.05
    Total max = 3.5 (same as before Phase 2A).
    """
    score = 0.0
    flags = []

    # ── Signal 1: pct_float_short ────────────────────────────────────────────
    # Prefer Finviz (more frequently updated) over yfinance
    short_pct = finviz.get("short_float_pct") or data.get("short_pct_float", 0) or 0.0
    short_pct_score = 0.0
    if short_pct >= 0.50:
        short_pct_score = 10.0
        flags.append(f"EXTREME short interest: {short_pct:.1%} of float")
    elif short_pct >= 0.40:
        short_pct_score = 8.5
        flags.append(f"Very high short interest: {short_pct:.1%} of float")
    elif short_pct >= 0.30:
        short_pct_score = 7.0
        flags.append(f"HIGH short interest: {short_pct:.1%} of float")
    elif short_pct >= 0.20:
        short_pct_score = 5.0
        flags.append(f"Elevated short interest: {short_pct:.1%} of float")
    elif short_pct >= 0.10:
        short_pct_score = 2.5
        flags.append(f"Moderate short interest: {short_pct:.1%}")
    elif short_pct >= 0.05:
        short_pct_score = 1.0

    # ── Signal 2: short P&L estimate ─────────────────────────────────────────
    pnl_score = 0.0
    current_price = data.get("price", 0)
    avg_entry = data.get("avg_price_60d", current_price)
    if current_price > 0 and avg_entry > 0:
        pnl_gap = (current_price - avg_entry) / avg_entry
        if pnl_gap >= 0.30:
            pnl_score = 10.0
            flags.append(f"Shorts heavily underwater: +{pnl_gap:.1%} above 60d avg entry")
        elif pnl_gap >= 0.15:
            pnl_score = 7.0
            flags.append(f"Shorts underwater: +{pnl_gap:.1%} above 60d avg entry")
        elif pnl_gap >= 0.05:
            pnl_score = 4.0
            flags.append(f"Shorts mildly underwater: +{pnl_gap:.1%} above 60d avg entry")
        elif pnl_gap <= -0.20:
            pnl_score = 0.0
            flags.append(f"Shorts profitable: {pnl_gap:.1%} vs 60d avg entry")
        else:
            pnl_score = 1.0

    # ── Signal 3: SI persistence across reporting periods ────────────────────
    persistence = compute_si_persistence_score(si_history or [], short_pct)
    si_persistence_score = persistence["si_persistence_score"]
    si_persistence_count = persistence["si_persistence_count"]
    si_trend = persistence["si_trend_direction"]

    if si_persistence_count >= 3 and si_persistence_score >= 8.0:
        flags.append(
            f"SI persistent: {si_persistence_count} reporting periods at high SI "
            f"(trend: {si_trend})"
        )
    elif si_persistence_count >= 2 and si_trend == "rising":
        flags.append(f"SI rising across {si_persistence_count} periods")

    # Weighted sub-total: 0.20 + 0.10 + 0.05 = max 3.5 (unchanged from Phase 1)
    # pnl weight reduced from 0.15 → 0.10 to make room for SI persistence (0.05)
    return {
        "score": short_pct_score * 0.20 + pnl_score * 0.10 + si_persistence_score * 0.05,
        "max": 10 * 0.20 + 10 * 0.10 + 10 * 0.05,   # 2.0 + 1.0 + 0.5 = 3.5
        "short_pct_score": round(short_pct_score, 2),
        "short_pnl_score": round(pnl_score, 2),
        "si_persistence_score": round(si_persistence_score, 2),
        "si_persistence_count": si_persistence_count,
        "si_trend_direction": si_trend,
        "flags": flags,
        "short_pct": short_pct,
    }


def score_mechanics(data: dict, finviz: dict, ftd_df: pd.DataFrame) -> dict:
    """
    Mechanics signals (~35% of composite).
    - days_to_cover: harder to unwind = more squeeze risk
    - volume_surge: retail/institutional piling in
    - ftd_vs_float: SEC fail-to-delivers as % of float
    - cost_to_borrow_proxy: Finviz hard-to-borrow indicator
    """
    score = 0.0
    flags = []

    # ── Signal 1: days to cover ───────────────────────────────────────────────
    # Self-computed DTC: (SI% × float_shares) / avg_vol_30d
    # Vendor shortRatio uses shares_outstanding as denominator → underestimates float-adjusted DTC
    _float = data.get("float_shares", 0) or 0
    _si_pct = data.get("short_pct_float", 0) or 0
    _avg_vol = data.get("volume_avg_30d", 0) or 0
    if _float > 0 and _si_pct > 0 and _avg_vol > 0:
        computed_dtc_30d = (_si_pct * _float) / _avg_vol
    else:
        computed_dtc_30d = (finviz.get("short_ratio") or data.get("short_ratio_dtc", 0)) or 0.0
    dtc = computed_dtc_30d
    dtc_score = 0.0
    if dtc >= 10:
        dtc_score = 10.0
        flags.append(f"Very high days-to-cover: {dtc:.1f}d (trapped)")
    elif dtc >= 7:
        dtc_score = 8.0
        flags.append(f"HIGH days-to-cover: {dtc:.1f}d")
    elif dtc >= 5:
        dtc_score = 6.0
        flags.append(f"Elevated days-to-cover: {dtc:.1f}d")
    elif dtc >= 3:
        dtc_score = 3.0
        flags.append(f"Moderate days-to-cover: {dtc:.1f}d")
    elif dtc >= 1:
        dtc_score = 1.0

    # ── Signal 2: volume surge ────────────────────────────────────────────────
    vol_ratio = data.get("volume_avg_5d", 1) / max(data.get("volume_avg_30d", 1), 1)
    vol_score = 0.0
    hist = data.get("history", pd.DataFrame())
    price_5d_chg = 0.0
    if not hist.empty and len(hist) >= 5:
        price_5d_chg = hist["Close"].iloc[-1] / hist["Close"].iloc[-5] - 1

    if vol_ratio >= 3.0 and price_5d_chg > 0.03:
        vol_score = 10.0
        flags.append(f"EXTREME volume surge: {vol_ratio:.1f}x avg + price up {price_5d_chg:.1%}")
    elif vol_ratio >= 2.0 and price_5d_chg > 0:
        vol_score = 7.5
        flags.append(f"Volume surge: {vol_ratio:.1f}x avg with positive price")
    elif vol_ratio >= 2.0:
        vol_score = 5.0
        flags.append(f"Volume spike: {vol_ratio:.1f}x avg (price flat)")
    elif vol_ratio >= 1.5:
        vol_score = 3.0
        flags.append(f"Above-average volume: {vol_ratio:.1f}x avg")
    elif vol_ratio >= 1.2:
        vol_score = 1.0

    # ── Signal 3: FTD vs float ────────────────────────────────────────────────
    ticker = data.get("ticker", "")
    float_shares = data.get("float_shares", 0) or 0
    ftd_qty = get_ftd_for_ticker(ticker, ftd_df)
    ftd_score = 0.0
    ftd_pct = 0.0
    if float_shares > 0 and ftd_qty > 0:
        ftd_pct = ftd_qty / float_shares
        if ftd_pct >= 0.05:
            ftd_score = 10.0
            flags.append(f"HIGH FTDs: {ftd_pct:.1%} of float ({ftd_qty:,.0f} shares)")
        elif ftd_pct >= 0.02:
            ftd_score = 6.0
            flags.append(f"Elevated FTDs: {ftd_pct:.1%} of float")
        elif ftd_pct >= 0.005:
            ftd_score = 3.0
            flags.append(f"Notable FTDs: {ftd_pct:.1%} of float")

    # ── Signal 4: cost-to-borrow proxy ────────────────────────────────────────
    # Actual CTB rates are not freely available. We use Finviz HTB flag as binary.
    # A HTB stock has scarce borrow supply → short sellers face squeeze pressure.
    ctb_score = 0.0
    if finviz.get("hard_to_borrow"):
        ctb_score = 10.0
        flags.append("Hard-to-borrow (HTB) — limited borrow supply")

    # vol_score excluded from composite — pre-catalyst setups have quiet volume
    # (volume is an ignition confirmation signal, not a setup prerequisite)
    mech_score = dtc_score * 0.15 + ftd_score * 0.05 + ctb_score * 0.05
    mech_max = 10 * 0.15 + 10 * 0.05 + 10 * 0.05
    volume_confirmation_flag = vol_ratio >= 1.5 and price_5d_chg > 0

    return {
        "score": mech_score,
        "max": mech_max,
        "dtc_score": round(dtc_score, 2),
        "vol_score": round(vol_score, 2),
        "ftd_score": round(ftd_score, 2),
        "ctb_score": round(ctb_score, 2),
        "days_to_cover": dtc,
        "computed_dtc_30d": round(computed_dtc_30d, 2),
        "vol_ratio": round(vol_ratio, 2),
        "ftd_pct": round(ftd_pct, 4),
        "volume_confirmation_flag": volume_confirmation_flag,
        "flags": flags,
    }


def score_structure(data: dict) -> dict:
    """
    Structure signals (~20% of composite).
    - market_cap: smaller = easier to squeeze
    - float_size: smaller float = less supply to absorb covering demand
    - price_vs_short_divergence: price rising while SI stays high = early squeeze signal
    """
    score = 0.0
    flags = []

    # ── Signal 1: market cap (log-scaled, smaller = better) ──────────────────
    market_cap = data.get("market_cap", 0) or 0
    mc_score = 0.0
    if 0 < market_cap <= 200_000_000:            # Micro-cap: <$200M
        mc_score = 10.0
        flags.append(f"Micro-cap: ${market_cap/1e6:.0f}M — easiest to squeeze")
    elif market_cap <= 1_000_000_000:             # Small-cap: $200M–$1B
        mc_score = 7.5
        flags.append(f"Small-cap: ${market_cap/1e6:.0f}M")
    elif market_cap <= 5_000_000_000:             # Mid-cap: $1B–$5B
        mc_score = 5.0
        flags.append(f"Mid-cap: ${market_cap/1e6:.0f}M")
    elif market_cap <= 20_000_000_000:            # Large-cap: $5B–$20B
        mc_score = 2.5
    # >$20B: 0 — very hard to squeeze

    # ── Signal 2: float size (log-scaled, smaller = better) ──────────────────
    float_shares = data.get("float_shares", 0) or 0
    float_score = 0.0
    if 0 < float_shares <= 10_000_000:            # <10M shares
        float_score = 10.0
        flags.append(f"Very low float: {float_shares/1e6:.1f}M shares")
    elif float_shares <= 30_000_000:              # 10–30M
        float_score = 8.0
        flags.append(f"Low float: {float_shares/1e6:.1f}M shares")
    elif float_shares <= 75_000_000:              # 30–75M
        float_score = 5.5
        flags.append(f"Moderate float: {float_shares/1e6:.1f}M shares")
    elif float_shares <= 200_000_000:             # 75–200M
        float_score = 3.0
    # >200M: 0 — large float dampens squeeze amplitude

    # ── Signal 3: price vs short divergence ──────────────────────────────────
    # Rising price + high short interest + recent volume = squeeze ignition signal
    hist = data.get("history", pd.DataFrame())
    div_score = 0.0
    if not hist.empty and len(hist) >= 20:
        price_20d = hist["Close"].iloc[-1] / hist["Close"].iloc[-20] - 1
        short_pct = data.get("short_pct_float", 0) or 0
        vol_30d_slope = 0.0
        if len(hist) >= 30:
            vols = hist["Volume"].iloc[-30:].values
            if len(vols) > 1:
                vol_30d_slope = np.polyfit(range(len(vols)), vols, 1)[0]

        if price_20d > 0.15 and short_pct >= 0.20:
            div_score = 10.0
            flags.append(f"Squeeze ignition? Price +{price_20d:.1%} last 20d with {short_pct:.1%} SI")
        elif price_20d > 0.07 and short_pct >= 0.15:
            div_score = 6.0
            flags.append(f"Price rising {price_20d:.1%} vs 20d ago; high SI still in place")
        elif price_20d > 0.03 and short_pct >= 0.10 and vol_30d_slope > 0:
            div_score = 3.5
            flags.append(f"Early divergence: +{price_20d:.1%} price, {short_pct:.1%} SI, rising volume")
        elif price_20d <= -0.15 and short_pct >= 0.20:
            # Shorts winning: negative signal
            flags.append(f"Shorts winning: price down {price_20d:.1%} last 20d")

    # ── Signal 4: compression-recovery ───────────────────────────────────────
    # 3-month drawdown followed by recovery, gated on SI ≥ 20%.
    # CAR-class pattern: stock crushed on macro → shorts pile in → price starts recovering → squeeze.
    comp_rec_score = 0.0
    if not hist.empty and len(hist) >= 63:
        short_pct = data.get("short_pct_float", 0) or 0
        if short_pct >= 0.20:
            hist_3m = hist["Close"].iloc[-63:]
            open_3m = float(hist_3m.iloc[0])
            low_3m = float(hist_3m.min())
            current_now = float(hist_3m.iloc[-1])
            if open_3m > 0 and low_3m > 0:
                drawdown = (open_3m - low_3m) / open_3m
                recovery = (current_now / low_3m) - 1
                if drawdown >= 0.30 and recovery >= 0.15:
                    comp_rec_score = 10.0
                    flags.append(
                        f"Compression-recovery: -{drawdown:.0%} drawdown then +{recovery:.0%} "
                        f"recovery, SI {short_pct:.0%}"
                    )
                elif drawdown >= 0.20 and recovery >= 0.10:
                    comp_rec_score = 6.0
                    flags.append(
                        f"Moderate compression-recovery: -{drawdown:.0%} / +{recovery:.0%}, "
                        f"SI {short_pct:.0%}"
                    )
                elif drawdown >= 0.15 and recovery >= 0.07:
                    comp_rec_score = 3.0
                    flags.append(f"Early compression-recovery pattern, SI {short_pct:.0%}")

    struct_score = mc_score * 0.07 + float_score * 0.07 + div_score * 0.06 + comp_rec_score * 0.04
    struct_max = 10 * 0.07 + 10 * 0.07 + 10 * 0.06 + 10 * 0.04

    return {
        "score": struct_score,
        "max": struct_max,
        "mc_score": round(mc_score, 2),
        "float_score": round(float_score, 2),
        "div_score": round(div_score, 2),
        "comp_rec_score": round(comp_rec_score, 2),
        "flags": flags,
    }


def detect_recent_squeeze(data: dict, lookback_days: int = 30) -> str:
    """
    Return the squeeze state for the recent lookback window.

    Returns:
        "false"     — no recent squeeze detected
        "active"    — price ran >50% from low but SI still ≥30% (shorts still trapped)
        "completed" — price ran >50% from low and SI < 30% (squeeze exhausted, score zeroed)
    """
    hist = data.get("history", pd.DataFrame())
    if hist.empty or len(hist) < lookback_days:
        return "false"

    recent = hist["Close"].iloc[-lookback_days:]
    low = float(recent.min())
    high = float(recent.max())
    current = float(hist["Close"].iloc[-1])

    if low > 0 and (high / low - 1) >= 0.50 and (current / high) >= 0.80:
        short_pct = data.get("short_pct_float", 0) or 0
        # High SI with elevated price = squeeze still active; shorts remain trapped
        if short_pct >= 0.30:
            return "active"
        return "completed"
    return "false"


def estimate_juice_target(short_pct: float, days_to_cover: float, price: float) -> float:
    """
    Rough estimate of potential squeeze upside (in %).
    Based on the mechanical covering demand relative to float supply.

    Formula: covering demand pressure ≈ short_pct × 2 × DTC-adjustment
    Cap at 150% — anything higher is speculation, not quantitative.

    Example: 40% SI, 8 DTC → ~80% squeeze target
             25% SI, 4 DTC → ~40% squeeze target
    """
    if short_pct <= 0 or price <= 0:
        return 0.0
    dtc_multiplier = min(1.5, 1.0 + max(0, days_to_cover - 3) / 10)
    raw = short_pct * 200.0 * dtc_multiplier
    return round(min(150.0, max(0.0, raw)), 1)


# ==============================================================================
# SECTION 3: COMPOSITE SCORER
# ==============================================================================

def _build_si_snapshot(ticker: str, data: dict, sq: "SqueezeScore") -> dict:
    """
    Build a short_interest_history record from a just-computed SqueezeScore.

    publication_date = today (when the system first observed these values).
    settlement_date  = None (yfinance does not expose FINRA settlement dates).
    source           = "yfinance_snapshot".

    shares_short is computed from SI% × float when not directly available from
    yfinance (which does not surface sharesShort in the .info dict reliably).
    """
    from datetime import date as _date, datetime as _dt
    today = _date.today().isoformat()
    float_shares = data.get("float_shares", 0) or 0
    short_pct = data.get("short_pct_float", 0) or 0
    shares_short = round(short_pct * float_shares) if (float_shares > 0 and short_pct > 0) else None

    return {
        "ticker": ticker,
        "publication_date": today,
        "settlement_date": None,
        "snapshot_date": today,
        "source": "yfinance_snapshot",
        "source_timestamp": _dt.utcnow().isoformat() + "Z",
        "shares_short": shares_short,
        "short_pct_float": short_pct if short_pct > 0 else None,
        "float_shares": float_shares if float_shares > 0 else None,
        "avg_volume_30d": data.get("volume_avg_30d"),
        "computed_dtc_30d": sq.computed_dtc_30d if sq.computed_dtc_30d > 0 else None,
        "vendor_short_ratio": data.get("short_ratio_dtc"),
        "data_confidence_score": 0.5,   # yfinance SI has reporting lag and rounding
    }


def compute_squeeze_score(
    ticker: str,
    data: dict,
    finviz: dict,
    ftd_df: pd.DataFrame,
    si_history: list | None = None,
    filing_catalysts: list | None = None,
) -> SqueezeScore:
    """
    Compute the full SqueezeScore for a single ticker.

    si_history: pre-fetched rows from short_interest_history. If None the
    screener will call _load_si_history() to fetch from DB (may be empty on
    first run — that is expected and handled gracefully).

    filing_catalysts: pre-fetched rows from filing_catalysts for CHUNK-06
    effective-float analysis.  If None the screener fetches from DB.
    """
    if si_history is None:
        si_history = _load_si_history(ticker)

    if filing_catalysts is None:
        filing_catalysts = _load_filing_catalysts(ticker)

    pos = score_positioning(data, finviz, si_history=si_history)
    mech = score_mechanics(data, finviz, ftd_df)
    struct = score_structure(data)

    # 3-state squeeze detection: "false" | "active" | "completed"
    squeeze_state = detect_recent_squeeze(data)
    recent_sq = squeeze_state == "completed"   # backward-compat flag

    # ── CHUNK-06: effective-float signal ──────────────────────────────────────
    from effective_float_analyzer import (
        analyze_effective_float,
        compute_effective_short_float_ratio,
        compute_effective_float_score,
    )

    float_shares = data.get("float_shares") or 0
    shares_outstanding = data.get("shares_outstanding") or 0
    short_pct_raw = data.get("short_pct_float") or 0
    shares_short = round(short_pct_raw * float_shares) if (float_shares > 0 and short_pct_raw > 0) else 0

    ef_result = analyze_effective_float(
        ticker=ticker,
        reported_float=float_shares or None,
        shares_outstanding=shares_outstanding or None,
        large_holder_records=filing_catalysts,
    )

    # Inject shares_short to get the ratio + score
    ef_ratio = compute_effective_short_float_ratio(
        shares_short=shares_short or None,
        effective_float_estimate=ef_result["effective_float_estimate"],
    )
    ef_score = compute_effective_float_score(ef_ratio)
    ef_result["effective_short_float_ratio"] = ef_ratio
    ef_result["effective_float_score"] = ef_score

    if ef_result["extreme_float_lock_flag"]:
        pos["flags"].append(
            f"Effective float locked: {ef_result['large_holder_ownership_pct']:.1f}% "
            f"held by large holders (effective SI: {ef_ratio:.1%})"
        )
    elif ef_result["large_holder_concentration_flag"]:
        pos["flags"].append(
            f"Large-holder concentration: {ef_result['large_holder_ownership_pct']:.1f}% "
            f"(effective SI: {ef_ratio:.1%})"
        )

    # Add effective-float score as a small additive secondary component (weight 0.03).
    # Max is already defined inside each sub-scorer; we tack on to raw/max after the fact.
    ef_weight = 0.03
    ef_contribution = ef_score * ef_weight   # 0–0.3 addition to raw score

    # Raw composite (normalize to 100)
    raw = pos["score"] + mech["score"] + struct["score"] + ef_contribution
    max_raw = pos["max"] + mech["max"] + struct["max"] + 10 * ef_weight
    final_score = round((raw / max_raw) * 100, 1) if max_raw > 0 else 0.0

    # Only zero score on completed squeezes; active squeezes preserve their score
    if squeeze_state == "completed":
        final_score = 0.0

    all_flags = pos["flags"] + mech["flags"] + struct["flags"]
    if squeeze_state == "completed":
        all_flags.insert(0, "RECENT SQUEEZE detected — score zeroed out")
    elif squeeze_state == "active":
        all_flags.insert(0, "ACTIVE SQUEEZE in progress — SI still elevated, score preserved")

    signal_breakdown = {
        "pct_float_short_score": pos.get("short_pct_score", 0),
        "short_pnl_score": pos.get("short_pnl_score", 0),
        "si_persistence_score": pos.get("si_persistence_score", 5.0),
        "days_to_cover_score": mech.get("dtc_score", 0),
        "volume_surge_score": mech.get("vol_score", 0),
        "ftd_score": mech.get("ftd_score", 0),
        "cost_to_borrow_score": mech.get("ctb_score", 0),
        "market_cap_score": struct.get("mc_score", 0),
        "float_score": struct.get("float_score", 0),
        "price_divergence_score": struct.get("div_score", 0),
        "compression_recovery_score": struct.get("comp_rec_score", 0),
        # CHUNK-06 effective-float fields
        "effective_float_score": ef_score,
        "effective_float_estimate": ef_result["effective_float_estimate"],
        "large_holder_ownership_pct": ef_result["large_holder_ownership_pct"],
        "effective_short_float_ratio": ef_ratio,
        "extreme_float_lock_flag": float(ef_result["extreme_float_lock_flag"]),
        "large_holder_concentration_flag": float(ef_result["large_holder_concentration_flag"]),
    }

    short_pct = pos.get("short_pct", 0)
    dtc = mech.get("days_to_cover", 0)
    price = data.get("price", 0)
    juice = estimate_juice_target(short_pct, dtc, price)

    return SqueezeScore(
        ticker=ticker,
        final_score=final_score,
        signal_breakdown=signal_breakdown,
        juice_target=juice,
        recent_squeeze=recent_sq,
        price=round(price, 2),
        short_pct_float=round(short_pct, 4),
        days_to_cover=round(dtc, 1),
        market_cap_m=round(data.get("market_cap", 0) / 1e6, 1) if data.get("market_cap") else 0.0,
        float_m=round(data.get("float_shares", 0) / 1e6, 2) if data.get("float_shares") else 0.0,
        flags=all_flags,
        computed_dtc_30d=mech.get("computed_dtc_30d", 0.0),
        compression_recovery_score=struct.get("comp_rec_score", 0.0),
        volume_confirmation_flag=mech.get("volume_confirmation_flag", False),
        squeeze_state=squeeze_state,
        # CHUNK-06 effective-float fields
        effective_float_estimate=ef_result["effective_float_estimate"],
        large_holder_ownership_pct=ef_result["large_holder_ownership_pct"],
        effective_short_float_ratio=ef_ratio,
        effective_float_score=ef_score,
        extreme_float_lock_flag=ef_result["extreme_float_lock_flag"],
        large_holder_concentration_flag=ef_result["large_holder_concentration_flag"],
        effective_float_confidence=ef_result["effective_float_confidence"],
    )


# ==============================================================================
# SECTION 4: MAIN PIPELINE
# ==============================================================================

def run_screener(
    tickers: Optional[List[str]] = None,
    min_score: float = 0.0,
    top_n: int = 20,
    include_finviz: bool = True,
    include_ftd: bool = True,
    sort_by: str = "score",
    verbose: bool = True,
) -> List[SqueezeScore]:
    """
    Run the squeeze screener over a list of tickers.

    Args:
        tickers:        List of ticker symbols. Pass None to use default SQUEEZE_UNIVERSE.
        min_score:      Minimum squeezability score (0–100) to include in results.
        top_n:          Return at most top_n results.
        include_finviz: Scrape Finviz for supplemental short data (slower; adds 1s/ticker).
        include_ftd:    Fetch SEC fail-to-deliver data.
        sort_by:        Ranking key — one of:
                          "score"  — squeeze setup quality (default)
                          "ev"     — expected value = score × juice_target (recommended)
                          "juice"  — raw % upside estimate only
        verbose:        Print progress to stdout.

    Returns:
        List[SqueezeScore] sorted by chosen key descending.
    """
    if tickers is None:
        tickers = _load_squeeze_universe()

    tickers = [t.upper().strip() for t in tickers if t.strip()]

    if verbose:
        print(f"\n{'='*68}")
        print(f"  SQUEEZE SCREENER — {len(tickers)} tickers")
        print(f"  Min score: {min_score}  |  Top-N: {top_n}")
        if not _BS4_AVAILABLE and include_finviz:
            print(f"  [WARN] BeautifulSoup not installed — Finviz scraping disabled")
            print(f"         Install: pip install beautifulsoup4")
        print(f"{'='*68}")

    # ── Load SEC FTD data (once, cached in-memory) ────────────────────────────
    ftd_df = pd.DataFrame()
    if include_ftd:
        if verbose:
            print(f"\n  Loading SEC FTD data...", end="", flush=True)
        ftd_df = fetch_sec_ftd_data()
        if verbose:
            n = len(ftd_df)
            if n > 0:
                tickers_with_ftd = ftd_df["ticker"].nunique()
                print(f" {n:,} records ({tickers_with_ftd:,} unique tickers)")
            else:
                print(f" unavailable (will skip FTD signal)")

    # ── Blacklist + bulk history pre-fetch ───────────────────────────────────
    _hist_cache: dict = {}
    try:
        from yf_cache import filter_blacklisted, bulk_history as _bulk_history
        tickers = filter_blacklisted(tickers)
        if verbose:
            print(f"\n  Pre-fetching {len(tickers)} price histories (bulk)...", end=" ", flush=True)
        _hist_cache = _bulk_history(tickers, period="6mo")
        if verbose:
            print(f"OK ({len(_hist_cache)} loaded)")
    except Exception as _yfc_exc:
        logger.debug("yf_cache unavailable: %s", _yfc_exc)

    # ── Main scan loop ────────────────────────────────────────────────────────
    results: List[SqueezeScore] = []
    si_snapshots: List[dict] = []
    total = len(tickers)

    for i, ticker in enumerate(tickers):
        if verbose:
            print(f"\r  Scanning: {ticker:<8} ({i+1}/{total})", end="", flush=True)

        # 1. yfinance data (history from bulk cache when available)
        data = fetch_stock_data(ticker, prefetched_hist=_hist_cache.get(ticker.upper()))
        if data is None:
            continue

        # 2. Finviz supplemental data
        finviz: dict = {}
        if include_finviz and _BS4_AVAILABLE:
            finviz = fetch_finviz_data(ticker)
            time.sleep(1.0)   # Finviz rate limit: ~1 req/sec safe

        # 3. Compute score (SI history is fetched inside compute_squeeze_score)
        sq = compute_squeeze_score(ticker, data, finviz, ftd_df)
        results.append(sq)

        # 4. Collect SI snapshot for persistence (batch-saved after scan)
        si_snapshots.append(_build_si_snapshot(ticker, data, sq))

        # Brief pause between yfinance .info calls (skipped when bulk history is active)
        if not include_finviz and not _hist_cache:
            time.sleep(0.3)

    if verbose:
        print(f"\r  Scan complete: {len(results)} tickers analyzed" + " " * 20)

    # ── Persist SI snapshots (non-fatal if DB unavailable) ───────────────────
    if si_snapshots:
        try:
            from utils.supabase_persist import save_short_interest_history
            save_short_interest_history(si_snapshots)
        except Exception as _exc:
            logger.debug("SI snapshot persistence failed (non-fatal): %s", _exc)

    # Sort by chosen key descending
    if sort_by == "ev":
        results.sort(key=lambda x: x.ev_score, reverse=True)
    elif sort_by == "juice":
        results.sort(key=lambda x: x.juice_target, reverse=True)
    else:
        results.sort(key=lambda x: x.final_score, reverse=True)

    # Apply filters
    results = [r for r in results if r.final_score >= min_score]
    results = results[:top_n]

    return results


def print_results(results: List[SqueezeScore], top_n: int = 20, sort_by: str = "score"):
    """Print formatted squeeze screening results to stdout."""
    if not results:
        print("\n  No squeeze candidates found.")
        return

    n = min(top_n, len(results))
    sort_label = {"ev": "EV (prob×juice)", "juice": "Juice Target", "score": "Score"}.get(sort_by, "Score")
    print(f"\n{'─'*84}")
    print(f"  TOP {n} SQUEEZE CANDIDATES  [sorted by: {sort_label}]")
    print(f"{'─'*84}")
    print(f"\n  {'#':<4}{'Ticker':<8}{'Price':>8}{'MktCap':>9}{'Float':>8}"
          f"{'Short%':>8}{'DTC':>6}{'Score':>8}{'Juice%':>8}{'EV':>7}  Flags")
    print(f"  {'─'*80}")

    for i, r in enumerate(results[:n]):
        tier = "**" if r.ev_score >= 50 else "* " if r.ev_score >= 25 else "  "
        mc = f"${r.market_cap_m:.0f}M" if r.market_cap_m < 1000 else f"${r.market_cap_m/1000:.1f}B"
        fl = f"{r.float_m:.1f}M" if r.float_m > 0 else "N/A"
        si = f"{r.short_pct_float:.1%}" if r.short_pct_float > 0 else "N/A"
        dtc = f"{r.days_to_cover:.1f}" if r.days_to_cover > 0 else "N/A"
        top_flag = r.flags[0] if r.flags else ""
        top_flag = (top_flag[:30] + "…") if len(top_flag) > 31 else top_flag

        print(f"  {tier}{i+1:<3}{r.ticker:<8}${r.price:>6.2f}{mc:>9}{fl:>8}"
              f"{si:>8}{dtc:>6}{r.final_score:>7.1f}{r.juice_target:>7.0f}%{r.ev_score:>6.1f}  {top_flag}")

    print(f"\n  ** = EV ≥ 50   * = EV ≥ 25   EV = score/100 × juice%  (probability × magnitude)")

    # Detailed breakdown for top 5
    print(f"\n{'─'*76}")
    print(f"  DETAILED BREAKDOWN — Top {min(5, n)}")
    print(f"{'─'*76}")

    for r in results[:5]:
        print(f"\n  [{r.ticker}]  Score: {r.final_score:.1f}/100  |  "
              f"Juice target: ~{r.juice_target:.0f}% upside")
        print(f"  Price: ${r.price:.2f}  |  Short%: {r.short_pct_float:.1%}  |  "
              f"DTC: {r.days_to_cover:.1f}d  |  "
              f"MktCap: ${r.market_cap_m:.0f}M  |  Float: {r.float_m:.1f}M shrs")

        bd = r.signal_breakdown
        print(f"  Scores — ShortPct: {bd.get('pct_float_short_score',0):.1f}  "
              f"PnL: {bd.get('short_pnl_score',0):.1f}  "
              f"DTC: {bd.get('days_to_cover_score',0):.1f}  "
              f"Vol: {bd.get('volume_surge_score',0):.1f}  "
              f"FTD: {bd.get('ftd_score',0):.1f}  "
              f"CTB: {bd.get('cost_to_borrow_score',0):.1f}  "
              f"MCap: {bd.get('market_cap_score',0):.1f}  "
              f"Float: {bd.get('float_score',0):.1f}  "
              f"Div: {bd.get('price_divergence_score',0):.1f}")

        if r.flags:
            print(f"  Flags:")
            for flag in r.flags[:6]:
                print(f"    • {flag}")


def save_results(results: List[SqueezeScore], output_dir: str = OUTPUT_DIR) -> str:
    """
    Save results to a dated CSV in the signals output directory.
    Returns the path to the saved file.
    """
    if not results:
        return ""

    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(output_dir, f"squeeze_signals_{date_str}.csv")

    rows = []
    for r in results:
        row = r.to_dict()
        # Flatten signal_breakdown into columns
        for k, v in row.pop("signal_breakdown", {}).items():
            row[k] = v
        row["flags"] = " | ".join(row.get("flags", []))
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    try:
        from utils.supabase_persist import save_squeeze_scores
        from datetime import date as _date
        save_squeeze_scores(df, _date.today().isoformat())
    except Exception as _exc:
        pass  # non-fatal
    return path


# ==============================================================================
# SECTION 5: CLI ENTRY POINT
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Squeeze Screener — surface short squeeze candidates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 squeeze_screener.py                              # default universe, sorted by EV
  python3 squeeze_screener.py --sort-by ev                 # best probability × upside combo
  python3 squeeze_screener.py --sort-by score              # highest setup quality first
  python3 squeeze_screener.py --sort-by juice              # highest raw upside first
  python3 squeeze_screener.py --universe meme --sort-by ev # meme stocks by EV
  python3 squeeze_screener.py --ticker GME AMC MARA RIOT   # specific tickers
  python3 squeeze_screener.py --top 10 --min-score 30      # filtered
  python3 squeeze_screener.py --no-finviz                  # faster, yfinance only
        """,
    )
    parser.add_argument(
        "--ticker", "--tickers",
        nargs="+",
        help="Specific ticker(s) to analyze",
    )
    parser.add_argument(
        "--universe",
        choices=["default", "meme"],
        default="default",
        help="Universe to screen (default or meme)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top results to display (default: 20)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum score threshold 0–100 (default: 0)",
    )
    parser.add_argument(
        "--no-finviz",
        action="store_true",
        help="Skip Finviz scraping (faster; loses HTB signal)",
    )
    parser.add_argument(
        "--no-ftd",
        action="store_true",
        help="Skip SEC FTD data download",
    )
    parser.add_argument(
        "--sort-by",
        choices=["score", "ev", "juice"],
        default="ev",
        help=(
            "Ranking key: 'ev' = expected value score x juice (default, best for picking), "
            "'score' = squeeze setup quality only, 'juice' = raw upside %% only"
        ),
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not save results to CSV",
    )
    args = parser.parse_args()

    # Resolve ticker list
    if args.ticker:
        tickers = args.ticker
    else:
        tickers = _load_squeeze_universe()  # dynamic universe (all modes)

    results = run_screener(
        tickers=tickers,
        min_score=args.min_score,
        top_n=args.top,
        include_finviz=not args.no_finviz,
        include_ftd=not args.no_ftd,
        sort_by=args.sort_by,
        verbose=True,
    )

    print_results(results, top_n=args.top, sort_by=args.sort_by)

    if not args.no_save:
        path = save_results(results)
        if path:
            print(f"\n  Results saved: {path}")

    return results


if __name__ == "__main__":
    main()
