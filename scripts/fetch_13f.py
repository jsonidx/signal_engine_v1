#!/usr/bin/env python3
"""
scripts/fetch_13f.py  —  TRD-083 Hedge Fund 13F Portfolio Monitor

Ingests SEC EDGAR 13F-HR filings for funds listed in config/hedge_funds.json,
computes Q-o-Q position diffs, upserts to Supabase, and fires a Telegram alert
when a new filing period is detected.

Usage:
    python3 scripts/fetch_13f.py                    # all funds
    python3 scripts/fetch_13f.py --fund situational-awareness-lp
    python3 scripts/fetch_13f.py --dry-run          # print rows, skip DB + Telegram

Environment:
    DATABASE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

# ── Env setup ─────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_env = _ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_BASE   = f"https://api.telegram.org/bot{BOT_TOKEN}"

EDGAR_BASE       = "https://data.sec.gov"
EDGAR_ARCHIVES   = "https://www.sec.gov"
EDGAR_HEADERS    = {"User-Agent": "signal-engine/1.0 setiabudi.jason@gmail.com"}
RATE_SLEEP       = 0.15   # seconds between EDGAR requests

CONFIG_PATH = _ROOT / "config" / "hedge_funds.json"


# ── EDGAR helpers ──────────────────────────────────────────────────────────────

def _get(url: str) -> requests.Response:
    time.sleep(RATE_SLEEP)
    r = requests.get(url, headers=EDGAR_HEADERS, timeout=30)
    r.raise_for_status()
    return r


def _pad_cik(cik: str) -> str:
    return cik.lstrip("0").zfill(10)


def fetch_13f_filings(cik: str) -> list[dict]:
    """Return list of 13F-HR filings from EDGAR submissions endpoint."""
    padded = _pad_cik(cik)
    data = _get(f"{EDGAR_BASE}/submissions/CIK{padded}.json").json()

    filings = []
    recent = data.get("filings", {}).get("recent", {})
    forms      = recent.get("form", [])
    acc_nums   = recent.get("accessionNumber", [])
    filed_dates = recent.get("filingDate", [])
    period_dates = recent.get("reportDate", [])

    for form, acc, filed, period in zip(forms, acc_nums, filed_dates, period_dates):
        if form != "13F-HR":
            continue
        filings.append({
            "accession_number": acc,
            "filed_at": filed,
            "period": period,
        })

    # Also check older filings pages
    for page_url in data.get("filings", {}).get("files", []):
        url = f"{EDGAR_BASE}/submissions/{page_url['name']}"
        page = _get(url).json()
        forms2       = page.get("form", [])
        acc_nums2    = page.get("accessionNumber", [])
        filed_dates2 = page.get("filingDate", [])
        period_dates2 = page.get("reportDate", [])
        for form, acc, filed, period in zip(forms2, acc_nums2, filed_dates2, period_dates2):
            if form != "13F-HR":
                continue
            filings.append({"accession_number": acc, "filed_at": filed, "period": period})

    return filings


def fetch_infotable(cik: str, accession_number: str) -> list[dict]:
    """Download and parse the infotable.xml from a 13F-HR filing."""
    import re as _re
    padded    = _pad_cik(cik)
    cik_int   = int(padded)
    acc_clean = accession_number.replace("-", "")
    base_dir  = f"{EDGAR_ARCHIVES}/Archives/edgar/data/{cik_int}/{acc_clean}"

    # Get directory listing (most reliable — works for all filing formats)
    try:
        idx_text = _get(f"{base_dir}/").text
    except Exception:
        try:
            idx_text = _get(f"{base_dir}/{accession_number}-index.htm").text
        except Exception as exc:
            print(f"  [WARN] Could not fetch filing index for {accession_number}: {exc}", file=sys.stderr)
            return []

    # Find all XML hrefs in the listing; exclude primary_doc.xml (cover sheet)
    xml_hrefs = _re.findall(r'href="([^"]+\.xml)"', idx_text, _re.IGNORECASE)
    candidates = [
        href.split("/")[-1] for href in xml_hrefs
        if href.split("/")[-1].lower() not in ("primary_doc.xml",)
        and "/Archives/" in href
    ]

    # Prefer names that look like infotables; fall back to whatever remains
    def _infotable_priority(name: str) -> int:
        n = name.lower()
        if any(k in n for k in ("information", "infotable", "13f", "holding", "position")):
            return 0
        return 1

    candidates.sort(key=_infotable_priority)

    if not candidates:
        print(f"  [WARN] No infotable XML found in {accession_number}", file=sys.stderr)
        return []

    infotable_name = candidates[0]
    xml_url  = f"{base_dir}/{infotable_name}"
    xml_resp = _get(xml_url)
    return _parse_infotable_xml(xml_resp.text)


def _parse_infotable_xml(xml_text: str) -> list[dict]:
    """Parse 13F infotable XML into a list of holding dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  [WARN] XML parse error: {e}", file=sys.stderr)
        return []

    # Strip namespace
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    rows = []
    for entry in root.findall(f"{ns}infoTable"):
        def _txt(tag: str) -> Optional[str]:
            el = entry.find(f"{ns}{tag}")
            if el is None:
                el = entry.find(f".//{ns}{tag}")
            return el.text.strip() if el is not None and el.text else None

        def _int(tag: str) -> Optional[int]:
            v = _txt(tag)
            return int(v.replace(",", "")) if v else None

        put_call_raw = _txt("putCall")
        put_call = put_call_raw.capitalize() if put_call_raw else None  # 'Put' | 'Call' | None

        rows.append({
            "name_of_issuer": _txt("nameOfIssuer"),
            "cusip":          _txt("cusip"),
            "ticker":         _resolve_ticker(_txt("cusip"), _txt("nameOfIssuer")),
            "shares":         _int("sshPrnamt"),
            "value_usd":      _int("value"),        # in thousands
            "put_call":       put_call,
        })

    return rows


# Per-run caches
_ticker_cache: dict[str, Optional[str]] = {}
_sec_name_to_ticker: dict[str, str] = {}   # normalised_name → ticker

_LEGAL_SUFFIXES = (
    " TECHNOLOGIES", " TECHNOLOGY", " INTERNATIONAL", " HOLDINGS",
    " SOLUTIONS", " SYSTEMS", " SERVICES", " ENTERPRISES",
    " CORPORATION", " CORP", " GROUP", " GLOBAL", " DIGITAL",
    " CAPITAL", " PARTNERS", " INVESTMENTS", " FINANCIAL",
    " ENERGY", " THERAPEUTICS", " BIOSCIENCES", " PHARMA",
    " INC", " LTD", " LLC", " LP", " PLC", " CO",
)


def _normalize_name(name: str) -> str:
    n = name.upper().strip()
    changed = True
    while changed:
        changed = False
        for sfx in _LEGAL_SUFFIXES:
            if n.endswith(sfx):
                n = n[: -len(sfx)].strip().rstrip(",").strip()
                changed = True
    return n


def _load_sec_tickers() -> None:
    global _sec_name_to_ticker
    if _sec_name_to_ticker:
        return
    try:
        r = requests.get(
            f"{EDGAR_ARCHIVES}/files/company_tickers_exchange.json",
            headers=EDGAR_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        # Format: {"fields": ["cik","name","ticker","exchange"], "data": [[cik,name,ticker,exch],...]}
        payload = r.json()
        fields  = payload.get("fields", [])
        rows    = payload.get("data", [])
        if not fields or not rows:
            # Fallback: older dict-of-dicts format
            for entry in payload.values() if isinstance(payload, dict) else []:
                if isinstance(entry, dict):
                    _sec_name_to_ticker[_normalize_name(entry.get("name", ""))] = entry.get("ticker", "")
        else:
            name_idx   = fields.index("name")
            ticker_idx = fields.index("ticker")
            for row in rows:
                raw_name = row[name_idx] or ""
                ticker   = row[ticker_idx] or ""
                if raw_name and ticker:
                    _sec_name_to_ticker[_normalize_name(raw_name)] = ticker
        print(f"  [13f] Loaded {len(_sec_name_to_ticker):,} SEC ticker mappings", file=sys.stderr)
    except Exception as exc:
        print(f"  [WARN] Could not load SEC tickers JSON: {exc}", file=sys.stderr)


def _resolve_ticker(cusip: Optional[str], issuer_name: Optional[str]) -> Optional[str]:
    """Map issuer name → ticker using SEC's company_tickers_exchange.json."""
    if not cusip:
        return None
    if cusip in _ticker_cache:
        return _ticker_cache[cusip]

    _load_sec_tickers()

    ticker = None
    if issuer_name and _sec_name_to_ticker:
        norm = _normalize_name(issuer_name)
        ticker = _sec_name_to_ticker.get(norm)

        # If no exact match, try the normalised name without the last word
        if not ticker and " " in norm:
            short = norm.rsplit(" ", 1)[0]
            ticker = _sec_name_to_ticker.get(short)

    _ticker_cache[cusip] = ticker
    return ticker


# ── Diff computation ───────────────────────────────────────────────────────────

def compute_diffs(fund_slug: str, period: date, rows: list[dict], conn) -> list[dict]:
    """
    Join current quarter rows against prior quarter to set change_type and deltas.
    Returns rows with change_type / shares_delta / value_delta_usd populated.
    """
    cur = conn.cursor()

    # Find prior quarter
    cur.execute("""
        SELECT DISTINCT period FROM hedge_fund_positions
        WHERE fund_slug = %s AND period < %s
        ORDER BY period DESC LIMIT 1
    """, (fund_slug, period))
    row = cur.fetchone()
    prior_period = row["period"] if row else None

    prior_map: dict[tuple, dict] = {}
    if prior_period:
        cur.execute("""
            SELECT cusip, put_call, shares, value_usd
            FROM hedge_fund_positions
            WHERE fund_slug = %s AND period = %s
        """, (fund_slug, prior_period))
        for r in cur.fetchall():
            key = (r["cusip"], r["put_call"])
            prior_map[key] = {"shares": r["shares"], "value_usd": r["value_usd"]}

    enriched = []
    for r in rows:
        key = (r["cusip"], r["put_call"])
        prior = prior_map.get(key)
        cur_shares = r.get("shares") or 0
        cur_value  = r.get("value_usd") or 0

        if prior is None:
            change_type   = "new"
            shares_delta  = None
            value_delta   = None
        else:
            prior_shares = prior["shares"] or 0
            prior_value  = prior["value_usd"] or 0
            shares_delta = cur_shares - prior_shares
            value_delta  = cur_value - prior_value
            if shares_delta > 0:
                change_type = "added"
            elif shares_delta < 0 and cur_shares > 0:
                change_type = "trimmed"
            elif cur_shares == 0:
                change_type = "closed"
            else:
                change_type = "unchanged"

        enriched.append({**r, "change_type": change_type,
                         "shares_delta": shares_delta, "value_delta_usd": value_delta})

    # Mark prior-quarter holdings that disappeared entirely as 'closed'
    current_keys = {(r["cusip"], r["put_call"]) for r in rows}
    for key, prior in prior_map.items():
        if key not in current_keys:
            enriched.append({
                "cusip": key[0], "put_call": key[1],
                "name_of_issuer": None, "ticker": None,
                "shares": 0, "value_usd": 0,
                "change_type": "closed",
                "shares_delta": -(prior["shares"] or 0),
                "value_delta_usd": -(prior["value_usd"] or 0),
            })

    return enriched


# ── DB upsert ──────────────────────────────────────────────────────────────────

def upsert_positions(fund: dict, period: date, filed_at: Optional[date],
                     rows: list[dict], conn) -> int:
    """Upsert position rows. Returns count of rows written."""
    cur = conn.cursor()
    count = 0
    for r in rows:
        cur.execute("""
            INSERT INTO hedge_fund_positions
                (fund_slug, fund_name, cik, period, filed_at,
                 ticker, cusip, name_of_issuer,
                 shares, value_usd, put_call,
                 change_type, shares_delta, value_delta_usd)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (fund_slug, cusip, period, put_call)
            DO UPDATE SET
                ticker          = EXCLUDED.ticker,
                name_of_issuer  = EXCLUDED.name_of_issuer,
                shares          = EXCLUDED.shares,
                value_usd       = EXCLUDED.value_usd,
                change_type     = EXCLUDED.change_type,
                shares_delta    = EXCLUDED.shares_delta,
                value_delta_usd = EXCLUDED.value_delta_usd,
                filed_at        = EXCLUDED.filed_at
        """, (
            fund["slug"], fund["name"], fund["cik"], period, filed_at,
            r.get("ticker"), r.get("cusip"), r.get("name_of_issuer"),
            r.get("shares"), r.get("value_usd"), r.get("put_call"),
            r.get("change_type"), r.get("shares_delta"), r.get("value_delta_usd"),
        ))
        count += 1
    conn.commit()
    return count


def is_new_period(fund_slug: str, period: date, conn) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM hedge_fund_positions WHERE fund_slug=%s AND period=%s LIMIT 1",
        (fund_slug, period)
    )
    return cur.fetchone() is None


# ── Telegram ───────────────────────────────────────────────────────────────────

def _tg_send(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"{TG_BASE}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
    except Exception as exc:
        print(f"[tg] send failed: {exc}", file=sys.stderr)


def build_alert(fund: dict, period: date, filed_at: Optional[date], rows: list[dict]) -> str:
    # value_usd is stored as-filed — some funds use thousands, others raw USD
    total_value = sum((r.get("value_usd") or 0) for r in rows)
    n_positions = len([r for r in rows if r.get("change_type") != "closed"])

    def _names(ct: str) -> list[str]:
        return [r.get("ticker") or r.get("name_of_issuer") or "?"
                for r in rows if r.get("change_type") == ct]

    new_     = _names("new")
    added    = _names("added")
    trimmed  = _names("trimmed")
    closed   = _names("closed")

    def _fmt(label: str, items: list[str]) -> str:
        if not items:
            return ""
        return f"{label} ({len(items)}): {', '.join(items[:8])}{'…' if len(items) > 8 else ''}\n"

    period_str  = period.strftime("%Q %Y") if hasattr(period, "strftime") else str(period)
    filed_str   = filed_at.strftime("%Y-%m-%d") if filed_at else "unknown"
    value_str   = f"${total_value / 1_000_000_000:.1f}B" if total_value >= 1_000_000_000 else f"${total_value / 1_000_000:.0f}M"

    msg = (
        f"📋 <b>NEW 13F — {fund['name']}</b>\n"
        f"Period: {period} | Filed: {filed_str}\n"
        f"Positions: {n_positions} | Total value: {value_str}\n\n"
        + _fmt("🟢 NEW", new_)
        + _fmt("➕ ADDED", added)
        + _fmt("✂️ TRIMMED", trimmed)
        + _fmt("🔴 CLOSED", closed)
    )
    return msg.strip()


# ── Main ───────────────────────────────────────────────────────────────────────

def process_fund(fund: dict, dry_run: bool, conn) -> None:
    print(f"\n[13f] Processing: {fund['name']} (CIK {fund['cik']})")

    filings = fetch_13f_filings(fund["cik"])
    print(f"  Found {len(filings)} 13F-HR filings on EDGAR")

    # Process oldest-first so Q-o-Q diffs are computed against already-inserted prior quarters
    filings = sorted(filings, key=lambda f: f.get("period", ""))

    for filing in filings:
        period_str = filing["period"]
        if not period_str:
            continue

        period   = date.fromisoformat(period_str)
        filed_at_raw = filing.get("filed_at")
        filed_at = date.fromisoformat(filed_at_raw) if filed_at_raw else None

        newly_seen = not dry_run and is_new_period(fund["slug"], period, conn)

        print(f"  [{period}] Fetching infotable…", end=" ", flush=True)
        rows = fetch_infotable(fund["cik"], filing["accession_number"])
        if not rows:
            print("empty — skipping")
            continue
        print(f"{len(rows)} holdings")

        if not dry_run:
            enriched = compute_diffs(fund["slug"], period, rows, conn)
            written  = upsert_positions(fund, period, filed_at, enriched, conn)
            print(f"  [{period}] Upserted {written} rows (change_type dist: "
                  + ", ".join(f"{ct}={sum(1 for r in enriched if r.get('change_type')==ct)}"
                               for ct in ["new","added","trimmed","closed","unchanged"])
                  + ")")

            if newly_seen:
                enriched_full = compute_diffs(fund["slug"], period, rows, conn)
                alert = build_alert(fund, period, filed_at, enriched_full)
                _tg_send(alert)
                print(f"  [{period}] Telegram alert sent")
        else:
            print(f"  [{period}] DRY RUN — sample rows:")
            for r in rows[:3]:
                print(f"    {r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch 13F filings from SEC EDGAR")
    parser.add_argument("--fund", help="Slug of a single fund to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print rows without writing to DB or sending Telegram")
    args = parser.parse_args()

    funds = json.loads(CONFIG_PATH.read_text())
    if args.fund:
        funds = [f for f in funds if f["slug"] == args.fund]
        if not funds:
            print(f"ERROR: no fund with slug '{args.fund}' in config/hedge_funds.json")
            sys.exit(1)

    if args.dry_run:
        print("[13f] DRY RUN mode — no DB writes, no Telegram")
        for fund in funds:
            process_fund(fund, dry_run=True, conn=None)
        return

    from utils.db import get_connection
    conn = get_connection()
    try:
        for fund in funds:
            process_fund(fund, dry_run=False, conn=conn)
    finally:
        conn.close()

    print("\n[13f] Done.")


if __name__ == "__main__":
    main()
