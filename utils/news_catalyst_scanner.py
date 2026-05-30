"""
utils/news_catalyst_scanner.py
================================
Low-cost pre-Deep-Dive news catalyst scanner.

Fetches free RSS headlines for a bounded ticker universe, classifies them
with catalyst_enrichment (keyword-only, zero LLM cost), and enqueues
eligible tickers into event_queue for Pattern Watch and Telegram.

Cost: $0.00 by default — no Claude/Grok/Exa/OpenAI calls.
      Pass --use-exa to enable paid Exa search (not yet implemented).

Data sources (free, in priority order):
  1. Yahoo Finance RSS  https://finance.yahoo.com/rss/headline?s={ticker}
  2. Google News RSS    https://news.google.com/rss/search?q={ticker}+stock

Eligibility for event_queue:
  - At least one fresh catalyst tag (within CATALYST_LOOKBACK_DAYS)
  - 5d price momentum >= 5%
  - 20d average dollar volume >= $5M

Queue reason format: NEWS_CATALYST:<tag1,tag2,...>

CLI:
  python3 utils/news_catalyst_scanner.py [options]
    --max-tickers N              Universe cap (default: 200)
    --max-headlines-per-ticker N Per source per ticker (default: 5)
    --cache-hours N              Headline cache TTL (default: 6)
    --dry-run                    Score but do not write to event_queue
    --use-exa                    Enable Exa search (not yet implemented)
    --watchlist PATH             Override ticker source file
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

# ── Project root ──────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
# Ensure project root is on sys.path so `from utils.x import y` works whether
# this file is run as a script (python3 utils/news_catalyst_scanner.py) or as
# a module (python3 -m utils.news_catalyst_scanner).
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)

# ── Headline cache ────────────────────────────────────────────────────────────
_CACHE_PATH = _ROOT / "data" / "news_headline_cache.json"

_RSS_TIMEOUT = 10   # seconds per request
_RATE_DELAY  = 0.05 # seconds between fetches (courtesy sleep)


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_headline_cache(cache_path: Optional[Path] = None) -> dict:
    path = cache_path or _CACHE_PATH
    if not path.exists():
        return {}
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("headline cache load failed: %s", exc)
        return {}


def _save_headline_cache(cache: dict, cache_path: Optional[Path] = None) -> None:
    path = cache_path or _CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w") as fh:
            json.dump(cache, fh, indent=2)
    except Exception as exc:
        logger.warning("headline cache save failed: %s", exc)


def _cache_is_fresh(cache: dict, ticker: str, cache_hours: float) -> bool:
    entry = cache.get(ticker)
    if not entry:
        return False
    fetched_at = entry.get("fetched_at", "")
    if not fetched_at:
        return False
    try:
        ts = datetime.fromisoformat(fetched_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age < cache_hours * 3600
    except Exception:
        return False


# ── RSS helpers ───────────────────────────────────────────────────────────────

def _parse_rss_date(date_str: str) -> Optional[datetime]:
    """Parse RSS pubDate (RFC 2822) or ISO 8601 string."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str[:len(fmt)], fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    return None


def _fetch_rss(url: str, max_items: int = 10, timeout: int = _RSS_TIMEOUT) -> list[dict]:
    """
    Fetch and parse an RSS feed URL.

    Returns a list of dicts:
      {"title": str, "published_at": datetime | None, "source": str}

    Returns [] on any network or parse error — callers must handle gracefully.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; signal-engine/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
    except Exception as exc:
        logger.debug("RSS fetch failed (%s): %s", url, exc)
        return []

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        logger.debug("RSS parse error (%s): %s", url, exc)
        return []

    def _first_found(parent, paths: tuple[str, ...], namespaces: Optional[dict] = None):
        for path in paths:
            found = parent.find(path, namespaces or {})
            if found is not None:
                return found
        return None

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    results = []
    for item in items[:max_items]:
        title_el = _first_found(item, ("title", "atom:title"), ns)
        date_el  = _first_found(
            item,
            ("pubDate", "published", "atom:published", "updated", "atom:updated"),
            ns,
        )
        title    = (title_el.text or "").strip() if title_el is not None else ""
        date_str = (date_el.text  or "").strip() if date_el  is not None else ""

        if title:
            results.append({
                "title":        title,
                "published_at": _parse_rss_date(date_str),
                "source":       url,
            })
    return results


def fetch_yahoo_rss(ticker: str, max_items: int = 10) -> list[dict]:
    """Fetch Yahoo Finance RSS headlines for *ticker*."""
    symbol = urllib.parse.quote_plus(ticker)
    url   = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    items = _fetch_rss(url, max_items=max_items)
    for item in items:
        item["source_name"] = "Yahoo Finance RSS"
    return items


def fetch_google_news_rss(ticker: str, max_items: int = 10) -> list[dict]:
    """Fetch Google News RSS headlines for *ticker*+stock query."""
    query = urllib.parse.quote_plus(f"{ticker} stock")
    url   = (
        f"https://news.google.com/rss/search"
        f"?q={query}&hl=en-US&gl=US&ceid=US:en"
    )
    items = _fetch_rss(url, max_items=max_items)
    for item in items:
        item["source_name"] = "Google News RSS"
    return items


def fetch_headlines(
    ticker: str,
    max_per_source: int = 5,
    use_google: bool = True,
) -> list[dict]:
    """
    Fetch and deduplicate headlines from all configured sources.

    Returns a list of headline dicts (deduped by lowercased title).
    """
    all_items: list[dict] = []
    seen: set[str] = set()

    for item in fetch_yahoo_rss(ticker, max_items=max_per_source):
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            all_items.append(item)

    if use_google:
        for item in fetch_google_news_rss(ticker, max_items=max_per_source):
            key = item["title"].lower()
            if key not in seen:
                seen.add(key)
                all_items.append(item)

    return all_items


# ── Ticker universe ───────────────────────────────────────────────────────────

def load_ticker_universe(
    watchlist_path: Optional[Path] = None,
    max_tickers: int = 200,
) -> list[str]:
    """
    Load ticker universe from watchlist.txt (or override path).
    Falls back to data/resolved_signals.json, then empty list.
    """
    candidates: list[str] = []

    wl = watchlist_path or (_ROOT / "watchlist.txt")
    if wl.exists():
        try:
            candidates = [
                line.strip().upper()
                for line in wl.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]
        except Exception as exc:
            logger.warning("Failed to read watchlist: %s", exc)

    if not candidates:
        sig_path = _ROOT / "data" / "resolved_signals.json"
        if sig_path.exists():
            try:
                data = json.loads(sig_path.read_text())
                if isinstance(data, list):
                    candidates = [r.get("ticker", "").upper() for r in data if r.get("ticker")]
                elif isinstance(data, dict):
                    candidates = list(data.keys())
            except Exception as exc:
                logger.warning("Failed to read resolved_signals.json: %s", exc)

    seen: set[str] = set()
    result: list[str] = []
    for t in candidates:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
            if len(result) >= max_tickers:
                break

    return result


# ── Price / momentum data ─────────────────────────────────────────────────────

def fetch_price_data(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch 5d momentum and 20d average dollar volume via yfinance.

    Returns {ticker: {"momentum_5d": float, "avg_dv_20d": float, "price": float}}.
    Missing / failed tickers are absent from the result dict.
    """
    if not tickers:
        return {}

    import warnings
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not available — price data will be zero")
        return {t: {"momentum_5d": 0.0, "avg_dv_20d": 0.0, "price": 0.0} for t in tickers}

    result: dict[str, dict] = {}
    BATCH = 50

    for i in range(0, len(tickers), BATCH):
        batch = tickers[i : i + BATCH]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = yf.download(
                    batch, period="25d",
                    auto_adjust=True, progress=False, threads=True,
                )
        except Exception as exc:
            logger.warning("yfinance batch %d failed: %s", i, exc)
            continue

        if df.empty:
            continue

        for ticker in batch:
            try:
                has_multiindex = isinstance(df.columns, type(df.columns)) and df.columns.nlevels > 1
                if has_multiindex:
                    if ticker not in df["Close"].columns:
                        continue
                    close  = df["Close"][ticker].dropna()
                    volume = df["Volume"][ticker].dropna() if ticker in df["Volume"].columns else None
                else:
                    close  = df["Close"].dropna()
                    volume = df["Volume"].dropna()

                if len(close) < 2:
                    continue

                price_now   = float(close.iloc[-1])
                price_5d    = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
                momentum_5d = (price_now - price_5d) / price_5d if price_5d > 0 else 0.0

                avg_dv_20d = 0.0
                if volume is not None and len(volume) >= 5:
                    dv = close.iloc[-20:] * volume.iloc[-20:]
                    avg_dv_20d = float(dv.mean()) if not dv.empty else 0.0

                result[ticker] = {
                    "momentum_5d": round(momentum_5d, 6),
                    "avg_dv_20d":  round(avg_dv_20d, 2),
                    "price":       round(price_now, 4),
                }
            except Exception as exc:
                logger.debug("price data failed for %s: %s", ticker, exc)

    return result


# ── Main scanner ──────────────────────────────────────────────────────────────

def run_scan(
    max_tickers: int = 200,
    max_headlines_per_ticker: int = 5,
    cache_hours: float = 6.0,
    dry_run: bool = False,
    use_exa: bool = False,
    watchlist_path: Optional[Path] = None,
    queue_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    _price_override: Optional[dict] = None,   # test injection only
    _fetch_override=None,                      # test injection only
) -> list[dict]:
    """
    Run the news catalyst scan.

    Returns a list of queued result dicts.
    Writes to event_queue unless dry_run=True.
    Makes no LLM calls by default.

    _price_override and _fetch_override are test-only injection points that
    bypass yfinance / network calls without patching global state.
    """
    try:
        from utils.catalyst_enrichment import score_catalyst_bundle
        from utils.event_queue import enqueue as eq_enqueue
    except ModuleNotFoundError:
        # Invocation-path fallback for direct script execution when the project
        # root is not import-resolved as a package but this file's directory is.
        from catalyst_enrichment import score_catalyst_bundle
        from event_queue import enqueue as eq_enqueue

    if use_exa:
        logger.warning("--use-exa not yet implemented; ignoring")

    logger.info(
        "News catalyst scanner starting (max_tickers=%d, dry_run=%s)",
        max_tickers, dry_run,
    )

    # ── Ticker universe ───────────────────────────────────────────────────────
    universe = load_ticker_universe(watchlist_path=watchlist_path, max_tickers=max_tickers)
    if not universe:
        logger.warning("Empty ticker universe — scanner exiting early")
        return []
    logger.info("Universe loaded: %d tickers", len(universe))

    # ── Headline cache ────────────────────────────────────────────────────────
    cache = _load_headline_cache(cache_path)

    tickers_needing_fetch = [
        t for t in universe if not _cache_is_fresh(cache, t, cache_hours)
    ]
    logger.info("Fetching headlines for %d tickers (cache miss)", len(tickers_needing_fetch))

    fetch_fn = _fetch_override or fetch_headlines

    for ticker in tickers_needing_fetch:
        try:
            items = fetch_fn(ticker, max_per_source=max_headlines_per_ticker)
            cache[ticker] = {
                "headlines": [
                    {
                        "title":        h["title"],
                        "published_at": (
                            h["published_at"].isoformat()
                            if isinstance(h["published_at"], datetime)
                            else (str(h["published_at"]) if h["published_at"] else None)
                        ),
                        "source_name": h.get("source_name", ""),
                    }
                    for h in items
                ],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            logger.debug("Headline fetch failed for %s: %s", ticker, exc)
            cache[ticker] = {
                "headlines": [],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        time.sleep(_RATE_DELAY)

    if tickers_needing_fetch:
        _save_headline_cache(cache, cache_path)

    # ── Price data ────────────────────────────────────────────────────────────
    logger.info("Fetching price data for %d tickers…", len(universe))
    if _price_override is not None:
        price_data = _price_override
    else:
        price_data = fetch_price_data(universe)

    # ── Score and enqueue eligible tickers ───────────────────────────────────
    queued: list[dict] = []

    for ticker in universe:
        entry       = cache.get(ticker, {})
        headlines_raw = entry.get("headlines", [])

        if not headlines_raw:
            continue

        pd_info       = price_data.get(ticker, {})
        momentum_5d   = float(pd_info.get("momentum_5d", 0.0))
        avg_dv_20d    = float(pd_info.get("avg_dv_20d",  0.0))
        current_price = float(pd_info.get("price",       0.0))

        headlines_for_scoring = [
            {"headline": h["title"], "published_at": h.get("published_at")}
            for h in headlines_raw
        ]

        bundle = score_catalyst_bundle(
            headlines=headlines_for_scoring,
            momentum_5d=momentum_5d,
            avg_dv_20d=avg_dv_20d,
        )

        if not bundle["queue_eligible"]:
            continue

        tags    = bundle["tags"]
        tag_str = ",".join(tags)
        reason  = f"NEWS_CATALYST:{tag_str}"

        source_fields = {
            "headlines": [
                {
                    "title":       h["title"],
                    "date":        h.get("published_at"),
                    "source_name": h.get("source_name", ""),
                }
                for h in headlines_raw
            ],
            "catalyst_tags": tags,
            "scanner_score": bundle["score"],
            "momentum_5d":   momentum_5d,
            "avg_dv_20d":    avg_dv_20d,
            "current_price": current_price,
            "reasons":       bundle["reasons"],
        }

        if dry_run:
            queued_ok = True
            logger.info(
                "[DRY-RUN] Would queue %s: %s (score=%.3f, mom=%.1f%%)",
                ticker, reason, bundle["score"], momentum_5d * 100,
            )
        else:
            queued_ok = eq_enqueue(
                ticker=ticker,
                reason=reason,
                score=bundle["score"],
                source_fields=source_fields,
                queue_path=queue_path,
            )

        if queued_ok:
            record = {
                "ticker":        ticker,
                "reason":        reason,
                "score":         bundle["score"],
                "tags":          tags,
                "momentum_5d":   momentum_5d,
                "avg_dv_20d":    avg_dv_20d,
                "current_price": current_price,
            }
            queued.append(record)
            if not dry_run:
                logger.info(
                    "Queued %s: %s (score=%.3f, mom=%.1f%%, dv=$%.1fM)",
                    ticker, reason, bundle["score"],
                    momentum_5d * 100, avg_dv_20d / 1e6,
                )

    logger.info(
        "Scan complete — %d/%d tickers queued (dry_run=%s)",
        len(queued), len(universe), dry_run,
    )
    return queued


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Low-cost news catalyst scanner — no LLM calls by default",
    )
    parser.add_argument("--max-tickers", type=int, default=200,
                        help="Maximum tickers to scan (default: 200)")
    parser.add_argument("--max-headlines-per-ticker", type=int, default=5,
                        help="Max headlines per ticker per source (default: 5)")
    parser.add_argument("--cache-hours", type=float, default=6.0,
                        help="Headline cache TTL in hours (default: 6)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Score but do not write to event_queue")
    parser.add_argument("--use-exa", action="store_true",
                        help="Enable Exa search (requires EXA_API_KEY; not yet implemented)")
    parser.add_argument("--watchlist", type=str, default=None,
                        help="Override ticker source file")
    args = parser.parse_args()

    watchlist = Path(args.watchlist) if args.watchlist else None
    results = run_scan(
        max_tickers=args.max_tickers,
        max_headlines_per_ticker=args.max_headlines_per_ticker,
        cache_hours=args.cache_hours,
        dry_run=args.dry_run,
        use_exa=args.use_exa,
        watchlist_path=watchlist,
    )

    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(f"\n{prefix}Queued {len(results)} tickers:")
    for r in results:
        dv_m = r["avg_dv_20d"] / 1e6 if r["avg_dv_20d"] else 0.0
        print(
            f"  {r['ticker']:<6} {r['reason']}  "
            f"score={r['score']:.3f}  mom={r['momentum_5d']*100:.1f}%  dv=${dv_m:.1f}M"
        )


if __name__ == "__main__":
    main()
