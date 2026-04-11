"""
Smoke tests for fetch_news_sentiment() in quant_report.py.

Tests cover:
  - Fallback behaviour when MARKETAUX_API_KEY is absent
  - Correct entity-score extraction and averaging from mocked API response
  - Sentiment label thresholds (Bullish / Neutral / Bearish)
  - Graceful handling of empty / malformed API responses
  - In-memory cache hit
"""

import sys
import os
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_marketaux_response(ticker: str, scores: list) -> dict:
    """Build a minimal Marketaux API payload with entity sentiment scores."""
    articles = []
    for score in scores:
        articles.append({
            "title": f"Headline about {ticker}",
            "published_at": datetime.now().isoformat(),
            "entities": [
                {
                    "symbol":          ticker.upper(),
                    "name":            ticker,
                    "sentiment_score": score,
                }
            ],
        })
    return {"data": articles, "meta": {"found": len(articles)}}


def _mock_urlopen(payload: dict):
    """Return a context-manager mock that yields the payload as JSON bytes."""
    response_mock = MagicMock()
    response_mock.status = 200
    response_mock.read.return_value = json.dumps(payload).encode("utf-8")
    response_mock.__enter__ = lambda s: s
    response_mock.__exit__ = MagicMock(return_value=False)
    return response_mock


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestFetchNewsSentimentFallback:
    """When MARKETAUX_API_KEY is absent the function must return neutral defaults."""

    def test_no_key_returns_neutral(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", None)
        # Clear cache to avoid stale hit
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        from quant_report import fetch_news_sentiment
        result = fetch_news_sentiment("AAPL")

        assert result["avg_sentiment"] == 0.0
        assert result["sentiment_label"] == "Neutral"
        assert result["articles_found"] == 0
        assert result["source"] == "fallback_neutral"

    def test_no_key_does_not_raise(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", None)
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        from quant_report import fetch_news_sentiment
        result = fetch_news_sentiment("NVDA")
        assert isinstance(result, dict)


class TestFetchNewsSentimentEntityExtraction:
    """Verify correct entity-score extraction and avg_sentiment calculation."""

    def test_bullish_scores(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", "fake_key")
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        payload = _make_marketaux_response("AAPL", [0.5, 0.6, 0.8])
        mock_resp = _mock_urlopen(payload)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from quant_report import fetch_news_sentiment
            result = fetch_news_sentiment("AAPL")

        assert result["articles_found"] == 3
        assert abs(result["avg_sentiment"] - round((0.5 + 0.6 + 0.8) / 3, 4)) < 1e-4
        assert result["sentiment_label"] == "Bullish"
        assert result["source"] == "marketaux"

    def test_bearish_scores(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", "fake_key")
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        payload = _make_marketaux_response("TSLA", [-0.5, -0.7, -0.3])
        mock_resp = _mock_urlopen(payload)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from quant_report import fetch_news_sentiment
            result = fetch_news_sentiment("TSLA")

        assert result["sentiment_label"] == "Bearish"
        assert result["avg_sentiment"] < -0.20

    def test_neutral_scores(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", "fake_key")
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        payload = _make_marketaux_response("MSFT", [0.1, -0.05, 0.0])
        mock_resp = _mock_urlopen(payload)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from quant_report import fetch_news_sentiment
            result = fetch_news_sentiment("MSFT")

        assert result["sentiment_label"] == "Neutral"
        assert -0.20 <= result["avg_sentiment"] <= 0.20


class TestFetchNewsSentimentSentimentLabels:
    """Boundary conditions for the three label thresholds."""

    def _run(self, scores, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", "fake_key")
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()
        payload = _make_marketaux_response("X", scores)
        mock_resp = _mock_urlopen(payload)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            from quant_report import fetch_news_sentiment
            return fetch_news_sentiment("X")

    def test_exactly_at_bullish_boundary(self, monkeypatch):
        result = self._run([0.21], monkeypatch)
        assert result["sentiment_label"] == "Bullish"

    def test_exactly_at_bearish_boundary(self, monkeypatch):
        result = self._run([-0.21], monkeypatch)
        assert result["sentiment_label"] == "Bearish"

    def test_zero_is_neutral(self, monkeypatch):
        result = self._run([0.0], monkeypatch)
        assert result["sentiment_label"] == "Neutral"


class TestFetchNewsSentimentEdgeCases:
    """Graceful degradation on empty / malformed API payloads."""

    def test_empty_data_array(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", "fake_key")
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        payload = {"data": [], "meta": {"found": 0}}
        mock_resp = _mock_urlopen(payload)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from quant_report import fetch_news_sentiment
            result = fetch_news_sentiment("PLTR")

        assert result["avg_sentiment"] == 0.0
        assert result["articles_found"] == 0

    def test_articles_with_no_matching_entity(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", "fake_key")
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        payload = {
            "data": [
                {
                    "title": "Market update",
                    "entities": [
                        {"symbol": "SPY", "sentiment_score": 0.9}   # wrong ticker
                    ],
                }
            ]
        }
        mock_resp = _mock_urlopen(payload)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from quant_report import fetch_news_sentiment
            result = fetch_news_sentiment("AAPL")

        assert result["avg_sentiment"] == 0.0
        assert result["source"] == "marketaux"

    def test_network_error_returns_neutral(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", "fake_key")
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            from quant_report import fetch_news_sentiment
            result = fetch_news_sentiment("GME")

        assert result["avg_sentiment"] == 0.0
        assert result["sentiment_label"] == "Neutral"
        assert result["source"] == "fallback_neutral"


class TestFetchNewsSentimentCache:
    """Second call for same ticker must hit cache and not make a network request."""

    def test_cache_hit_skips_network(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", "fake_key")
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        payload = _make_marketaux_response("COIN", [0.4, 0.6])
        mock_resp = _mock_urlopen(payload)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_url:
            from quant_report import fetch_news_sentiment
            r1 = fetch_news_sentiment("COIN")
            r2 = fetch_news_sentiment("COIN")

        # urlopen should only have been called once (second call hits cache)
        assert mock_url.call_count == 1
        assert r1["avg_sentiment"] == r2["avg_sentiment"]

    def test_expired_cache_refetches(self, monkeypatch):
        monkeypatch.setattr("quant_report.MARKETAUX_API_KEY", "fake_key")
        import quant_report
        quant_report._MARKETAUX_CACHE.clear()

        # Seed an already-expired cache entry
        quant_report._MARKETAUX_CACHE["AMD"] = {
            "expires": datetime.now() - timedelta(hours=1),
            "result":  {"avg_sentiment": 0.99, "source": "stale"},
        }

        payload = _make_marketaux_response("AMD", [0.1])
        mock_resp = _mock_urlopen(payload)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from quant_report import fetch_news_sentiment
            result = fetch_news_sentiment("AMD")

        assert result["source"] == "marketaux"
        assert abs(result["avg_sentiment"] - 0.1) < 1e-4
