"""
Phase 4 sentiment tests — source weighting, entity extraction,
rolling windows, momentum, FinBERT availability check.
"""
from __future__ import annotations

import datetime as dt

import pytest

from backend.app.models.schemas import NewsItem
from backend.app.services.sentiment import (
    _analyze_finbert,
    _get_source_weight,
    analyze_news,
    build_enriched_feed,
    compute_sentiment_momentum,
    compute_sentiment_windows,
    extract_entities,
    rolling_sentiment,
    score_label,
    sentiment_to_score,
    top_entities,
    weighted_sentiment,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

def _item(
    title: str,
    publisher: str = "Unknown",
    score: float | None = None,
    minutes_ago: int = 30,
) -> NewsItem:
    pub_time = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes_ago)
    item = NewsItem(
        title=title,
        url="http://example.com",
        publisher=publisher,
        published_at=pub_time,
    )
    if score is not None:
        item = item.model_copy(update={"sentiment_score": score, "source_weight": _get_source_weight(publisher)})
    return item


# ── Source weights ────────────────────────────────────────────────────────────

def test_reuters_gets_high_weight():
    assert _get_source_weight("Reuters") > 1.0


def test_bloomberg_gets_high_weight():
    assert _get_source_weight("Bloomberg") > 1.0


def test_unknown_source_gets_discounted_weight():
    assert _get_source_weight("Random Blog XYZ") < 1.0


def test_source_weight_case_insensitive():
    assert _get_source_weight("REUTERS") == _get_source_weight("reuters")


def test_reuters_weight_greater_than_unknown():
    assert _get_source_weight("Reuters") > _get_source_weight("Some Random Blog")


def test_coindesk_gets_elevated_weight():
    """Crypto-specific sources should get elevated trust."""
    assert _get_source_weight("CoinDesk") > _get_source_weight("Some Blog")


# ── Weighted sentiment ────────────────────────────────────────────────────────

def test_weighted_sentiment_differs_from_simple_mean():
    """High-trust positive article should outweigh low-trust negative."""
    items = [
        _item("Good news from Reuters", publisher="Reuters", score=0.8, minutes_ago=10),
        _item("Bad news from blog", publisher="Unknown Blog", score=-0.8, minutes_ago=10),
    ]
    # Reuters weight ~1.5, unknown ~0.8
    # Weighted = (0.8*1.5 + (-0.8)*0.8) / (1.5 + 0.8) > simple mean of 0.0
    ws = weighted_sentiment(items)
    assert ws > 0.0  # positive because Reuters outweighs unknown


def test_weighted_sentiment_zero_for_empty():
    assert weighted_sentiment([]) == 0.0


def test_weighted_sentiment_single_item():
    item = _item("Test", publisher="Reuters", score=0.5, minutes_ago=5)
    ws = weighted_sentiment([item])
    assert ws == pytest.approx(0.5, abs=0.01)


# ── Entity extraction ─────────────────────────────────────────────────────────

def test_extract_dollar_ticker():
    entities = extract_entities("$AAPL reports strong earnings")
    assert "AAPL" in entities


def test_extract_crypto_symbol():
    entities = extract_entities("Bitcoin BTC hits all-time high amid ETH surge")
    assert "BTC" in entities
    assert "ETH" in entities


def test_extract_multiple_entities():
    entities = extract_entities("$TSLA and $NVDA both rally on AI news")
    assert "TSLA" in entities
    assert "NVDA" in entities


def test_extract_filters_common_words():
    entities = extract_entities("IT IS A GOOD DAY TO BE IN THE US MARKET")
    # Common words should be filtered
    for entity in entities:
        assert entity not in ("IT", "IS", "A", "BE", "IN")


def test_extract_returns_at_most_5():
    text = "$A $BB $CCC $DDDD $EEEEE $FFFFF $GG"
    entities = extract_entities(text)
    assert len(entities) <= 5


def test_top_entities_most_frequent():
    items = [
        _item("$AAPL earnings", minutes_ago=10),
        _item("$AAPL stock rises", minutes_ago=20),
        _item("$GOOG drops", minutes_ago=30),
    ]
    scored = analyze_news(items, ticker="AAPL")
    entities = top_entities(scored)
    assert "AAPL" in entities  # AAPL appears in 2 headlines → most common


# ── Analyze news with enrichment ─────────────────────────────────────────────

def test_analyze_news_adds_source_weight():
    items = [_item("Big news", publisher="Reuters", minutes_ago=5)]
    enriched = analyze_news(items, ticker="AAPL")
    assert enriched[0].source_weight > 1.0


def test_analyze_news_adds_entities():
    items = [_item("$AAPL beats estimates", publisher="Reuters", minutes_ago=5)]
    enriched = analyze_news(items, ticker="AAPL")
    assert "AAPL" in enriched[0].entities


def test_analyze_news_adds_engine_label():
    items = [_item("Apple reports profits", publisher="CNBC", minutes_ago=5)]
    enriched = analyze_news(items, ticker="AAPL")
    assert enriched[0].sentiment_engine in ("vader", "finbert", "claude")


# ── Rolling windows ───────────────────────────────────────────────────────────

def test_rolling_windows_include_1h_24h_7d():
    items = [_item("Test news", score=0.3, minutes_ago=10)]
    windows = compute_sentiment_windows(items)
    assert "1h" in windows
    assert "24h" in windows
    assert "7d" in windows


def test_rolling_window_1h_excludes_old_news():
    old = _item("Old news", score=0.9, minutes_ago=120)  # 2 hours ago
    recent = _item("Recent news", score=0.1, minutes_ago=30)
    windows = compute_sentiment_windows([old, recent])
    # 1h window should only contain recent item
    assert windows["1h"].item_count == 1
    assert abs(windows["1h"].compound - 0.1) < 0.01


def test_rolling_window_24h_includes_old_and_recent():
    old = _item("Old news", score=0.9, minutes_ago=120)
    recent = _item("Recent news", score=0.1, minutes_ago=30)
    windows = compute_sentiment_windows([old, recent])
    assert windows["24h"].item_count == 2


def test_rolling_window_empty_when_no_recent_news():
    old = _item("Old news", score=0.5, minutes_ago=180)
    windows = compute_sentiment_windows([old])
    assert windows["1h"].item_count == 0
    assert windows["1h"].compound == 0.0


def test_rolling_window_weighted_compound_differs_from_simple():
    """Higher-trust source in 1h window should raise weighted_compound."""
    reuters_item = _item("Reuters positive news", publisher="Reuters", score=0.7, minutes_ago=10)
    blog_item = _item("Blog negative news", publisher="Unknown Blog", score=-0.5, minutes_ago=20)
    windows = compute_sentiment_windows([reuters_item, blog_item])
    # Simple mean: (0.7 + (-0.5)) / 2 = 0.1
    # Weighted: Reuters 1.5, Unknown 0.8 → (0.7*1.5 + -0.5*0.8) / 2.3 > 0.1
    assert windows["24h"].weighted_compound > windows["24h"].compound


# ── Sentiment momentum ────────────────────────────────────────────────────────

def test_momentum_rising_when_recent_more_positive():
    items = [
        _item("Recent positive", score=0.6, minutes_ago=20),   # in 1h
        _item("Older negative", score=-0.3, minutes_ago=180),  # in 24h only
        _item("Older neutral", score=0.0, minutes_ago=200),
    ]
    windows = compute_sentiment_windows(items)
    momentum = compute_sentiment_momentum(windows)
    # 1h compound ≈ 0.6, 24h compound < 0.6 → delta > 0 → rising
    assert momentum.direction == "rising"
    assert momentum.delta > 0


def test_momentum_falling_when_recent_more_negative():
    items = [
        _item("Recent negative", score=-0.6, minutes_ago=20),
        _item("Older positive", score=0.3, minutes_ago=180),
        _item("Older positive", score=0.3, minutes_ago=200),
    ]
    windows = compute_sentiment_windows(items)
    momentum = compute_sentiment_momentum(windows)
    assert momentum.direction == "falling"
    assert momentum.delta < 0


def test_momentum_stable_when_minimal_change():
    items = [
        _item("News A", score=0.1, minutes_ago=10),
        _item("News B", score=0.12, minutes_ago=120),
    ]
    windows = compute_sentiment_windows(items)
    momentum = compute_sentiment_momentum(windows)
    # delta < 0.05 → stable
    assert momentum.direction == "stable"


def test_momentum_stable_when_no_recent_news():
    items = [_item("Old news only", score=0.5, minutes_ago=180)]
    windows = compute_sentiment_windows(items)
    momentum = compute_sentiment_momentum(windows)
    assert momentum.direction == "stable"  # 1h window empty


def test_momentum_has_detail_string():
    items = [_item("Test", score=0.2, minutes_ago=10)]
    windows = compute_sentiment_windows(items)
    momentum = compute_sentiment_momentum(windows)
    assert len(momentum.detail) > 0


# ── EnrichedNewsFeed builder ──────────────────────────────────────────────────

def test_build_enriched_feed_structure():
    items = [_item("$AAPL beats estimates", publisher="Reuters", score=0.5, minutes_ago=15)]
    scored = analyze_news(items, ticker="AAPL")
    feed = build_enriched_feed("AAPL", scored)

    assert feed.ticker == "AAPL"
    assert len(feed.items) == 1
    assert "1h" in feed.windows
    assert feed.momentum is not None
    assert isinstance(feed.source_weighted_sentiment, float)
    assert isinstance(feed.top_entities, list)
    assert isinstance(feed.engines_used, list)
    assert len(feed.engines_used) > 0


def test_enriched_feed_is_newsfeed_subtype():
    """EnrichedNewsFeed should satisfy NewsFeed interface."""
    from backend.app.models.schemas import NewsFeed
    items = [_item("Test", score=0.1, minutes_ago=5)]
    scored = analyze_news(items, ticker="TEST")
    feed = build_enriched_feed("TEST", scored)
    assert isinstance(feed, NewsFeed)
    assert hasattr(feed, "rolling_sentiment")
    assert hasattr(feed, "sentiment_label")


# ── FinBERT availability (non-destructive) ────────────────────────────────────

def test_finbert_falls_back_to_vader_if_not_installed():
    """FinBERT should silently fall back to VADER when transformers not installed."""
    from unittest.mock import patch
    with patch("backend.app.services.sentiment._finbert") as mock_fb:
        mock_fb.available.return_value = False
        result = _analyze_finbert("Apple stock rises on strong earnings")
        # Should not raise; should return a float
        assert isinstance(result, float)
        assert -1.0 <= result <= 1.0


# ── Backward compatibility ────────────────────────────────────────────────────

def test_rolling_sentiment_still_works():
    """Legacy rolling_sentiment() function should remain functional."""
    items = [_item("Good news", score=0.5, minutes_ago=10)]
    compound, label = rolling_sentiment(items)
    assert isinstance(compound, float)
    assert label in ("bullish", "slightly bullish", "neutral", "slightly bearish", "bearish")


def test_sentiment_to_score_unchanged():
    assert sentiment_to_score(1.0) == 100.0
    assert sentiment_to_score(-1.0) == 0.0
    assert sentiment_to_score(0.0) == 50.0


def test_score_label_unchanged():
    assert score_label(1.0) == "bullish"
    assert score_label(-1.0) == "bearish"
    assert score_label(0.0) == "neutral"
