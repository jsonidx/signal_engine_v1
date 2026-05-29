"""
utils/catalyst_enrichment.py
==============================
Structured catalyst tag classification for breakout detection enrichment.

Classifies news headlines and press releases into deterministic keyword-based
tags.  LLM interpretation is NOT used as the primary classification signal —
keyword matching is the source of truth, with optional LLM as secondary.

Supported catalyst tags:
  AI_INFRASTRUCTURE_LAUNCH   — company launches AI compute hardware / servers /
                                workstations powered by own or partner silicon.
  GUIDANCE_OR_MARGIN_BEAT    — earnings beat, margin expansion, or guidance raise.
  ANALYST_TARGET_CLUSTER     — analyst upgrade / price target raise / new coverage.
  SOCIAL_ATTENTION_SPIKE     — unusual social/options activity or squeeze alert.

Staleness gate:
  Headlines older than CATALYST_LOOKBACK_DAYS (default 7) are ignored.

Public API:
  classify_headline(headline, published_at, lookback_days) -> set[str]
  score_catalyst_bundle(headlines, short_float, momentum_5d, avg_dv) -> dict
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Union

logger = logging.getLogger(__name__)

try:
    from config import CATALYST_LOOKBACK_DAYS
except ImportError:
    CATALYST_LOOKBACK_DAYS = 7


# ---------------------------------------------------------------------------
# Keyword groups
# Each group is a list of lowercase patterns (substring match unless prefixed
# with '^' for start-of-string or wrapped in r'\b' for word-boundary).
# ---------------------------------------------------------------------------

_TAG_PATTERNS: dict[str, list[str]] = {
    "AI_INFRASTRUCTURE_LAUNCH": [
        "launches ai",
        "ai workstation",
        "ai server",
        "ai servers",
        "ai computing platform",
        "ai infrastructure",
        "ai-powered server",
        "ai-powered workstation",
        "artificial intelligence server",
        "artificial intelligence workstation",
        "gpu server",
        "nvidia blackwell",
        "nvidia hopper",
        "amd instinct",
        "gaudi accelerator",
        "data center ai",
        "ai data center",
        "corsair pro ai",          # CRSR-specific but illustrative of brand+ai patterns
        "pro ai workstation",
        "pro ai server",
        "accelerated computing",
        "ai hardware launch",
        "announces ai workstation",
        "announces ai server",
        "releases ai",
        "unveils ai",
    ],
    "GUIDANCE_OR_MARGIN_BEAT": [
        # Explicit beat / surprise signals
        "beats estimates",
        "beats expectations",
        "earnings beat",
        "profit beat",
        "eps beat",
        "above consensus",
        "above analyst estimates",
        "tops estimates",
        "upside surprise",
        "better-than-expected",
        "better than expected",
        # Margin signals — require specific improvement context, not bare "gross margin"
        "margin expansion",
        "gross margin expansion",
        "gross margin improvement",
        "gross margin record",
        "gross margin beat",
        "record gross margin",
        "margin beat",
        "margin improvement",
        # Revenue / profitability milestones
        "record revenue",
        "record profit",
        "record earnings",
        "profitability milestone",
        "first profitable",
        "turns profitable",
        # Guidance raises — explicit signals only
        "raised guidance",
        "raises guidance",
        "raises full-year",
        "raises fiscal",
        "raised full-year",
        "raised its outlook",
        "strong quarterly results",  # "strong quarterly" alone is too vague; require "results"
    ],
    "ANALYST_TARGET_CLUSTER": [
        "price target",
        "analyst upgrade",
        "upgrades to",
        "upgraded to",
        "raised to buy",
        "raised to outperform",
        "raised to overweight",
        "new coverage",
        "initiates coverage",
        "initiating coverage",
        "outperform rating",
        "buy rating",
        "strong buy",
        "target raised",
        "pt raised",
        "pt increase",
        "increases price target",
        "raises price target",
        "new street high",
        "bull case",
    ],
    "SOCIAL_ATTENTION_SPIKE": [
        "unusual options activity",
        "short squeeze",
        "squeeze alert",
        "trending on",
        "going viral",
        "reddit",
        "wallstreetbets",
        "wsb mentions",
        "unusual call volume",
        "options sweep",
        "dark pool",
        "social sentiment",
        "retail attention",
        "meme stock",
    ],
}

# Patterns that SHOULD NOT trigger AI_INFRASTRUCTURE_LAUNCH on their own
# (generic AI mentions without product/launch signal)
_AI_FALSE_POSITIVE_PHRASES: tuple = (
    "conference",
    "panel",
    "forum",
    "discussion",
    "webinar",
    "white paper",
    "survey",
    "participation",
    "joins",
    "partners with",  # generic partnership without product detail
)


# ---------------------------------------------------------------------------
# Core classification
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _has_pattern(text_lower: str, patterns: list[str]) -> bool:
    return any(p in text_lower for p in patterns)


def classify_headline(
    headline: str,
    published_at: Union[datetime, date, str],
    lookback_days: Optional[int] = None,
) -> set:
    """
    Classify *headline* into zero or more catalyst tags.

    Returns an empty set when:
      - The headline is older than *lookback_days* (stale)
      - No keyword pattern matches

    *published_at* can be a datetime, date, or ISO-format string.
    """
    if lookback_days is None:
        lookback_days = CATALYST_LOOKBACK_DAYS

    # ── Staleness check ───────────────────────────────────────────────────────
    if isinstance(published_at, str):
        try:
            published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            try:
                published_at = datetime.strptime(published_at[:10], "%Y-%m-%d")
            except ValueError:
                logger.warning("classify_headline: could not parse date %r", published_at)
                published_at = None

    if published_at is not None:
        if isinstance(published_at, datetime):
            pub_date = published_at.date() if hasattr(published_at, "date") else published_at
        else:
            pub_date = published_at

        cutoff = date.today() - timedelta(days=lookback_days)
        if pub_date < cutoff:
            return set()

    text = _normalize(headline)
    tags: set = set()

    # ── AI_INFRASTRUCTURE_LAUNCH ──────────────────────────────────────────────
    # Require a specific product/launch signal; reject generic AI participation
    if _has_pattern(text, _TAG_PATTERNS["AI_INFRASTRUCTURE_LAUNCH"]):
        if not _has_pattern(text, list(_AI_FALSE_POSITIVE_PHRASES)):
            tags.add("AI_INFRASTRUCTURE_LAUNCH")

    # ── GUIDANCE_OR_MARGIN_BEAT ───────────────────────────────────────────────
    if _has_pattern(text, _TAG_PATTERNS["GUIDANCE_OR_MARGIN_BEAT"]):
        tags.add("GUIDANCE_OR_MARGIN_BEAT")

    # ── ANALYST_TARGET_CLUSTER ────────────────────────────────────────────────
    if _has_pattern(text, _TAG_PATTERNS["ANALYST_TARGET_CLUSTER"]):
        tags.add("ANALYST_TARGET_CLUSTER")

    # ── SOCIAL_ATTENTION_SPIKE ────────────────────────────────────────────────
    if _has_pattern(text, _TAG_PATTERNS["SOCIAL_ATTENTION_SPIKE"]):
        tags.add("SOCIAL_ATTENTION_SPIKE")

    return tags


# ---------------------------------------------------------------------------
# Bundle scoring — combine catalyst evidence with price/liquidity/short interest
# ---------------------------------------------------------------------------

def score_catalyst_bundle(
    headlines: list,
    short_float: float = 0.0,
    momentum_5d: float = 0.0,
    avg_dv_20d: float = 0.0,
    lookback_days: Optional[int] = None,
) -> dict:
    """
    Score a bundle of catalyst evidence for a ticker.

    *headlines* is a list of dicts with at least:
      - "headline" (str)
      - "published_at" (str/datetime/date)

    Returns:
      {
        "tags":       list[str]   — unique catalyst tags found
        "score":      float       — 0–1 readiness score
        "reasons":    list[str]   — human-readable evidence strings
        "queue_eligible": bool    — True if the bundle warrants Deep Dive queuing
      }

    Queue eligibility requires at least ONE catalyst tag PLUS price/liquidity
    confirmation:
      - momentum_5d >= 5%  AND
      - avg_dv_20d >= $5M  (minimum liquidity)
      - bonus points for short interest > 10% (squeeze multiplier)
    """
    all_tags: set = set()
    fresh_headlines: list = []

    for item in headlines:
        raw_text = item.get("headline") or item.get("title") or ""
        pub_at   = item.get("published_at") or item.get("date") or date.today()
        found    = classify_headline(raw_text, pub_at, lookback_days=lookback_days)
        if found:
            all_tags |= found
            fresh_headlines.append({"headline": raw_text, "tags": sorted(found)})

    reasons: list = []
    score = 0.0

    if all_tags:
        reasons.append(f"catalyst_tags: {', '.join(sorted(all_tags))}")
        score += min(len(all_tags) * 0.20, 0.40)   # max 0.40 from tags alone

    if momentum_5d >= 0.35:
        reasons.append(f"strong_5d_momentum: +{momentum_5d:.1%}")
        score += 0.25
    elif momentum_5d >= 0.15:
        reasons.append(f"early_5d_momentum: +{momentum_5d:.1%}")
        score += 0.15
    elif momentum_5d >= 0.05:
        reasons.append(f"positive_5d_momentum: +{momentum_5d:.1%}")
        score += 0.05

    if avg_dv_20d >= 10_000_000:
        reasons.append(f"liquid_20d_dv: ${avg_dv_20d/1e6:.1f}M")
        score += 0.15
    elif avg_dv_20d >= 5_000_000:
        reasons.append(f"adequate_20d_dv: ${avg_dv_20d/1e6:.1f}M")
        score += 0.05

    if short_float >= 15.0:
        reasons.append(f"high_short_float: {short_float:.1f}%")
        score += 0.20
    elif short_float >= 10.0:
        reasons.append(f"elevated_short_float: {short_float:.1f}%")
        score += 0.10

    score = min(round(score, 4), 1.0)

    # Eligibility: at least one catalyst tag + minimum momentum + minimum liquidity
    queue_eligible = bool(
        all_tags
        and momentum_5d >= 0.05
        and avg_dv_20d >= 5_000_000
    )

    return {
        "tags":           sorted(all_tags),
        "score":          score,
        "reasons":        reasons,
        "fresh_headlines": fresh_headlines,
        "queue_eligible": queue_eligible,
    }
