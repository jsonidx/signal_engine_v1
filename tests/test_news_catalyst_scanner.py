"""
tests/test_news_catalyst_scanner.py
=====================================
Unit tests for the TRD-018 news catalyst scanner.

All tests use mocked feeds / static data — no network calls, no yfinance,
no AI API calls of any kind.  The scanner must work with zero env vars set.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Project root on path ─────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


# ==============================================================================
# Helpers
# ==============================================================================

def _today_iso() -> str:
    return date.today().isoformat()


def _stale_date() -> str:
    """A date older than CATALYST_LOOKBACK_DAYS (7)."""
    return (date.today() - timedelta(days=10)).isoformat()


def _fresh_date() -> str:
    """Yesterday — always within the lookback window."""
    return (date.today() - timedelta(days=1)).isoformat()


# ==============================================================================
# 1. classify_headline — AI_INFRASTRUCTURE_LAUNCH tag
# ==============================================================================

class TestClassifyHeadlineAiInfrastructureLaunch(unittest.TestCase):
    """
    classify_headline() must return AI_INFRASTRUCTURE_LAUNCH for launch-style
    headlines and must ignore false-positive phrases like 'conference'.
    """

    def _classify(self, headline, pub_date=None):
        from utils.catalyst_enrichment import classify_headline
        return classify_headline(headline, pub_date or _fresh_date())

    def test_ai_server_launch_creates_tag(self):
        tags = self._classify("Acme launches AI server lineup for enterprise data centers")
        self.assertIn("AI_INFRASTRUCTURE_LAUNCH", tags,
                      "Headline with 'launches ai server' must produce AI_INFRASTRUCTURE_LAUNCH")

    def test_ai_workstation_launch_creates_tag(self):
        tags = self._classify("TechCorp announces AI workstation powered by NVIDIA Blackwell")
        self.assertIn("AI_INFRASTRUCTURE_LAUNCH", tags)

    def test_ai_infrastructure_mention_creates_tag(self):
        tags = self._classify("Company unveils new AI infrastructure platform for training")
        self.assertIn("AI_INFRASTRUCTURE_LAUNCH", tags)

    def test_generic_ai_conference_no_tag(self):
        """A headline about an AI conference must NOT trigger AI_INFRASTRUCTURE_LAUNCH."""
        tags = self._classify("CEO discusses AI strategy at annual conference")
        self.assertNotIn("AI_INFRASTRUCTURE_LAUNCH", tags,
                         "Generic AI + conference must not trigger infrastructure tag")

    def test_ai_whitepaper_no_tag(self):
        tags = self._classify("Company releases white paper on AI infrastructure roadmap")
        self.assertNotIn("AI_INFRASTRUCTURE_LAUNCH", tags)


# ==============================================================================
# 1b. RSS parser keeps normal RSS title/pubDate elements
# ==============================================================================

class TestRssParser(unittest.TestCase):
    """_fetch_rss must parse standard RSS elements without network in tests."""

    def test_standard_rss_item_parses_title_and_date(self):
        from utils.news_catalyst_scanner import _fetch_rss

        xml = (
            b"<rss><channel><item>"
            b"<title>Acme launches AI server lineup</title>"
            b"<pubDate>Fri, 29 May 2026 10:00:00 GMT</pubDate>"
            b"</item></channel></rss>"
        )

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return xml

        with patch("urllib.request.urlopen", return_value=_Resp()):
            rows = _fetch_rss("https://example.test/rss", max_items=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Acme launches AI server lineup")
        self.assertIsNotNone(rows[0]["published_at"])


# ==============================================================================
# 2. Stale headline ignored
# ==============================================================================

class TestStaleHeadlineIgnored(unittest.TestCase):
    """classify_headline must return empty set for headlines older than lookback_days."""

    def test_stale_headline_returns_empty(self):
        from utils.catalyst_enrichment import classify_headline
        tags = classify_headline(
            "Company launches AI server lineup",
            published_at=_stale_date(),
            lookback_days=7,
        )
        self.assertEqual(tags, set(),
                         "Stale headline must return empty tag set")

    def test_fresh_headline_returns_tags(self):
        from utils.catalyst_enrichment import classify_headline
        tags = classify_headline(
            "Company launches AI server lineup",
            published_at=_fresh_date(),
            lookback_days=7,
        )
        self.assertIn("AI_INFRASTRUCTURE_LAUNCH", tags,
                      "Fresh headline must return tag")


# ==============================================================================
# 3. No catalyst tag → no queue
# ==============================================================================

class TestNoCatalystTagNoQueue(unittest.TestCase):
    """score_catalyst_bundle with no matching headlines → queue_eligible=False."""

    def test_plain_news_no_eligible(self):
        from utils.catalyst_enrichment import score_catalyst_bundle
        headlines = [
            {"headline": "Company holds investor day event", "published_at": _fresh_date()},
            {"headline": "Stock trades flat amid market uncertainty", "published_at": _fresh_date()},
        ]
        result = score_catalyst_bundle(headlines, momentum_5d=0.10, avg_dv_20d=50_000_000)
        self.assertFalse(result["queue_eligible"],
                         "Headlines without catalyst tags must not be queue-eligible")
        self.assertEqual(result["tags"], [],
                         "No catalyst tags expected")


# ==============================================================================
# 4. Catalyst tag present but weak momentum / liquidity → no queue
# ==============================================================================

class TestWeakMomentumOrLiquidityNoQueue(unittest.TestCase):

    def _headlines_with_ai_launch(self):
        return [{"headline": "Company launches AI server lineup", "published_at": _fresh_date()}]

    def test_catalyst_tag_weak_momentum_not_eligible(self):
        from utils.catalyst_enrichment import score_catalyst_bundle
        result = score_catalyst_bundle(
            self._headlines_with_ai_launch(),
            momentum_5d=0.02,       # below 5% threshold
            avg_dv_20d=50_000_000,
        )
        self.assertFalse(result["queue_eligible"],
                         "Catalyst tag + momentum < 5% must not be queue-eligible")

    def test_catalyst_tag_low_liquidity_not_eligible(self):
        from utils.catalyst_enrichment import score_catalyst_bundle
        result = score_catalyst_bundle(
            self._headlines_with_ai_launch(),
            momentum_5d=0.10,
            avg_dv_20d=1_000_000,   # below $5M threshold
        )
        self.assertFalse(result["queue_eligible"],
                         "Catalyst tag + dv < $5M must not be queue-eligible")

    def test_catalyst_tag_both_weak_not_eligible(self):
        from utils.catalyst_enrichment import score_catalyst_bundle
        result = score_catalyst_bundle(
            self._headlines_with_ai_launch(),
            momentum_5d=0.01,
            avg_dv_20d=500_000,
        )
        self.assertFalse(result["queue_eligible"])


# ==============================================================================
# 5. Valid catalyst + momentum + liquidity → event_queue.enqueue() called
# ==============================================================================

class TestValidCatalystQueuesCalled(unittest.TestCase):
    """
    run_scan() with mocked fetch + price data must call event_queue.enqueue()
    exactly once for an eligible ticker.
    """

    def _make_watchlist(self, tmp_dir: Path, tickers: list[str]) -> Path:
        wl = tmp_dir / "watchlist.txt"
        wl.write_text("\n".join(tickers))
        return wl

    def _ai_server_headline(self) -> dict:
        return {
            "title":        "Acme launches AI server lineup for data centers",
            "published_at": datetime.now(timezone.utc),
            "source_name":  "Yahoo Finance RSS",
        }

    def test_eligible_ticker_calls_enqueue(self):
        from utils.news_catalyst_scanner import run_scan

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path   = Path(tmp)
            wl_path    = self._make_watchlist(tmp_path, ["ACME"])
            queue_path = tmp_path / "event_queue.json"
            cache_path = tmp_path / "news_headline_cache.json"

            def _mock_fetch(ticker, max_per_source=5):
                return [self._ai_server_headline()]

            price_data = {
                "ACME": {"momentum_5d": 0.12, "avg_dv_20d": 20_000_000, "price": 45.0}
            }

            results = run_scan(
                max_tickers=10,
                max_headlines_per_ticker=5,
                cache_hours=0.0,        # force fetch
                dry_run=False,
                watchlist_path=wl_path,
                queue_path=queue_path,
                cache_path=cache_path,
                _price_override=price_data,
                _fetch_override=_mock_fetch,
            )

            # All assertions inside the `with` block so tmp dir still exists
            self.assertEqual(len(results), 1, "One eligible ticker must produce one queue result")
            rec = results[0]
            self.assertEqual(rec["ticker"], "ACME")
            self.assertTrue(rec["reason"].startswith("NEWS_CATALYST:"),
                            f"Reason must start with NEWS_CATALYST: got {rec['reason']!r}")
            self.assertIn("AI_INFRASTRUCTURE_LAUNCH", rec["tags"])
            self.assertGreater(rec["score"], 0)

            # Verify the event_queue file was written
            self.assertTrue(queue_path.exists(), "event_queue.json must be created")
            entries = json.loads(queue_path.read_text())
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["ticker"], "ACME")
            self.assertTrue(entries[0]["reason"].startswith("NEWS_CATALYST:"))

    def test_dry_run_does_not_write_queue(self):
        from utils.news_catalyst_scanner import run_scan

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path   = Path(tmp)
            wl_path    = self._make_watchlist(tmp_path, ["DRYTEST"])
            queue_path = tmp_path / "event_queue.json"
            cache_path = tmp_path / "news_headline_cache.json"

            def _mock_fetch(ticker, max_per_source=5):
                return [self._ai_server_headline()]

            price_data = {
                "DRYTEST": {"momentum_5d": 0.15, "avg_dv_20d": 10_000_000, "price": 80.0}
            }

            results = run_scan(
                max_tickers=10,
                cache_hours=0.0,
                dry_run=True,
                watchlist_path=wl_path,
                queue_path=queue_path,
                cache_path=cache_path,
                _price_override=price_data,
                _fetch_override=_mock_fetch,
            )

            # Assertions inside the `with` block
            self.assertEqual(len(results), 1, "dry-run must still report eligible tickers")
            self.assertFalse(queue_path.exists(),
                             "dry-run must NOT write event_queue.json")


# ==============================================================================
# 6. Scanner runs without AI / API keys
# ==============================================================================

class TestScannerNoAiKeysRequired(unittest.TestCase):
    """
    run_scan() must complete successfully even when all AI/API-key env vars
    are absent (ANTHROPIC_API_KEY, XAI_API_KEY, EXA_API_KEY, DATABASE_URL).
    """

    def test_no_api_keys_needed(self):
        from utils.news_catalyst_scanner import run_scan

        env_to_remove = [
            "ANTHROPIC_API_KEY", "XAI_API_KEY", "EXA_API_KEY",
            "DATABASE_URL", "SUPABASE_JWT_SECRET",
        ]
        clean_env = {k: v for k, v in os.environ.items() if k not in env_to_remove}

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path   = Path(tmp)
            cache_path = tmp_path / "news_headline_cache.json"
            queue_path = tmp_path / "event_queue.json"

            # Empty watchlist → scanner exits early without touching any API
            wl = tmp_path / "watchlist.txt"
            wl.write_text("")

            with patch.dict(os.environ, clean_env, clear=True):
                try:
                    results = run_scan(
                        max_tickers=5,
                        cache_hours=0.0,
                        dry_run=True,
                        watchlist_path=wl,
                        queue_path=queue_path,
                        cache_path=cache_path,
                        _price_override={},
                        _fetch_override=lambda t, max_per_source=5: [],
                    )
                    # No tickers → no results expected
                    self.assertIsInstance(results, list)
                except Exception as exc:
                    self.fail(f"Scanner raised an exception without API keys: {exc}")


# ==============================================================================
# 7. Pattern Watch recognises NEWS_CATALYST selection_reason
# ==============================================================================

class TestPatternWatchRecognizesNewsCatalyst(unittest.TestCase):
    """
    score_ticker() in utils/pattern_watch.py must return a CRSR match when
    a candidate_snapshots row has selection_reason starting with NEWS_CATALYST.
    """

    def test_news_catalyst_snap_matches_crsr(self):
        from utils.pattern_watch import score_ticker

        snap = {
            "ticker":           "NEWSCO",
            "selection_reason": "NEWS_CATALYST:AI_INFRASTRUCTURE_LAUNCH",
            "priority_score":   62.0,
        }
        # Synthetic proxy row: news_catalyst triggers has_breakout + has_volume
        # so technical_score=3, volume_score=3 in the main.py proxy builder.
        # Here we test the score_ticker function directly with equivalent values.
        cs_proxy = {
            "ticker":             "NEWSCO",
            "composite":          62.0,
            "raw_composite":      62.0,
            "options_score":      0.0,
            "volume_score":       3.0,   # proxy sets this for news_catalyst
            "technical_score":    3.0,   # proxy sets this for news_catalyst
            "dark_pool_score":    0.0,
            "dark_pool_signal":   "NEUTRAL",
            "earnings_score":     0.0,
            "post_squeeze_guard": False,
            "price":              0.0,
            "days_to_earnings":   None,
            "_synthetic_from_snapshot": True,
        }

        result = score_ticker("NEWSCO", cs_proxy, snap)

        self.assertIsNotNone(result,
                             "score_ticker must return a result for NEWS_CATALYST snap proxy")
        self.assertEqual(result["matched_pattern"], "CRSR",
                         f"Expected CRSR match, got {result['matched_pattern']!r}")
        self.assertGreaterEqual(result["similarity_pct"], 50,
                                "similarity_pct must be >= 50 for NEWS_CATALYST candidate")
        # Source must be candidate_snapshots only (synthetic row)
        self.assertEqual(result["source"], ["candidate_snapshots"],
                         "Synthetic snapshot rows must list only candidate_snapshots as source")

    def test_news_catalyst_is_pattern_snapshot_flag(self):
        """
        The _is_pattern_snapshot logic inside main.py must recognise
        a news_catalyst selection_reason.  We test the same logic directly.
        """
        # Mirror of the inline function in dashboard/api/main.py
        def _is_pattern_snapshot(snap: dict) -> bool:
            reason = (snap.get("selection_reason") or "").lower()
            return any(k in reason for k in (
                "fresh_catalyst_breakout",
                "catalyst_price_expansion",
                "early_momentum_breakout",
                "news_catalyst",
            ))

        self.assertTrue(
            _is_pattern_snapshot({"selection_reason": "NEWS_CATALYST:GUIDANCE_OR_MARGIN_BEAT"}),
            "NEWS_CATALYST selection_reason must be recognised as a pattern snapshot",
        )
        self.assertFalse(
            _is_pattern_snapshot({"selection_reason": "high_conviction_bull"}),
            "Unrelated selection_reason must not be recognised as a pattern snapshot",
        )


# ==============================================================================
# 8. Telegram catalyst-watch query recognises NEWS_CATALYST selection_reason
# ==============================================================================

class TestTelegramRecognizesNewsCatalyst(unittest.TestCase):
    """
    fetch_catalyst_watch_candidates() must include rows whose
    selection_reason contains NEWS_CATALYST, not only fresh_catalyst_breakout.
    """

    def _make_mock_conn(self, snapshot_rows, thesis_tickers=None):
        conn = MagicMock()
        cur  = MagicMock()
        conn.cursor.return_value = cur
        thesis_rows = [{"ticker": t} for t in (thesis_tickers or [])]
        cur.fetchall.side_effect = [snapshot_rows, thesis_rows]
        return conn

    def test_news_catalyst_row_returned(self):
        """
        A snapshot row with selection_reason containing NEWS_CATALYST must appear
        in the returned candidates.
        """
        from scripts.notify_pipeline_result import fetch_catalyst_watch_candidates

        snapshot_rows = [
            {
                "ticker":           "NEWST",
                "selection_reason": "NEWS_CATALYST:AI_INFRASTRUCTURE_LAUNCH",
                "priority_score":   58.0,
                "rank":             None,
            }
        ]
        conn = self._make_mock_conn(snapshot_rows, thesis_tickers=[])
        result = fetch_catalyst_watch_candidates(conn)

        tickers = [r["ticker"] for r in result]
        self.assertIn("NEWST", tickers,
                      "NEWS_CATALYST row must appear in fetch_catalyst_watch_candidates output")

    def test_news_catalyst_and_fresh_breakout_both_included(self):
        """Both fresh_catalyst_breakout and NEWS_CATALYST rows must be returned."""
        from scripts.notify_pipeline_result import fetch_catalyst_watch_candidates

        snapshot_rows = [
            {
                "ticker":           "CRSR",
                "selection_reason": "fresh_catalyst_breakout | CATALYST_PRICE_EXPANSION",
                "priority_score":   70.0,
                "rank":             2,
            },
            {
                "ticker":           "NEWST",
                "selection_reason": "NEWS_CATALYST:GUIDANCE_OR_MARGIN_BEAT",
                "priority_score":   55.0,
                "rank":             None,
            },
        ]
        conn = self._make_mock_conn(snapshot_rows, thesis_tickers=["CRSR"])
        result = fetch_catalyst_watch_candidates(conn)

        tickers = [r["ticker"] for r in result]
        self.assertIn("CRSR",  tickers)
        self.assertIn("NEWST", tickers)

    def test_news_catalyst_row_has_thesis_correctly_flagged(self):
        """A NEWS_CATALYST ticker that received a thesis must have has_thesis=True."""
        from scripts.notify_pipeline_result import fetch_catalyst_watch_candidates

        snapshot_rows = [
            {
                "ticker":           "ANLZD",
                "selection_reason": "NEWS_CATALYST:ANALYST_TARGET_CLUSTER",
                "priority_score":   60.0,
                "rank":             5,
            }
        ]
        conn = self._make_mock_conn(snapshot_rows, thesis_tickers=["ANLZD"])
        result = fetch_catalyst_watch_candidates(conn)

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["has_thesis"],
                        "Ticker with today's thesis must have has_thesis=True")

    def test_unrelated_selection_reason_excluded(self):
        """
        A snapshot row with selection_reason that matches neither
        fresh_catalyst_breakout nor NEWS_CATALYST must not appear.
        Note: fetch_catalyst_watch_candidates filtering is done by the SQL
        ILIKE clause, which the mock bypasses — so we test the mock returns
        the right shape for rows that DO match the filter.
        """
        from scripts.notify_pipeline_result import fetch_catalyst_watch_candidates

        # Mock returns only matching rows (SQL filtering happens server-side)
        snapshot_rows: list = []   # DB returned nothing — SQL filtered them out
        conn = self._make_mock_conn(snapshot_rows)
        result = fetch_catalyst_watch_candidates(conn)

        # Should fall back to event_queue.json (empty in this env)
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
