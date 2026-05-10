"""Tests for the sentiment module — no API calls made."""
from __future__ import annotations

from backend.app.services.sentiment import (
    _analyze_vader,
    analyze_news,
    rolling_sentiment,
    score_label,
    sentiment_to_score,
)


def test_vader_positive_headline():
    score = _analyze_vader("Apple reports record profits, beats expectations")
    assert score > 0.0


def test_vader_negative_headline():
    score = _analyze_vader("Market crash wipes out billions as recession fears surge")
    assert score < 0.0


def test_vader_neutral_headline():
    score = _analyze_vader("Company announces quarterly earnings")
    assert -0.5 < score < 0.5


def test_score_label_extremes():
    assert score_label(1.0) == "bullish"
    assert score_label(-1.0) == "bearish"
    assert score_label(0.0) == "neutral"


def test_analyze_news_adds_sentiment(sample_news):
    enriched = analyze_news(sample_news)
    assert all(item.sentiment_score is not None for item in enriched)
    assert all(item.sentiment_label != "" for item in enriched)


def test_rolling_sentiment_mean(sample_news):
    import statistics
    enriched = analyze_news(sample_news)
    compound, _label = rolling_sentiment(enriched)
    scores = [i.sentiment_score for i in enriched if i.sentiment_score is not None]
    assert abs(compound - statistics.mean(scores)) < 0.001


def test_sentiment_to_score_range():
    assert sentiment_to_score(1.0) == 100.0
    assert sentiment_to_score(-1.0) == 0.0
    assert sentiment_to_score(0.0) == 50.0


def test_empty_news_rolling_sentiment():
    compound, label = rolling_sentiment([])
    assert compound == 0.0
    assert label == "neutral"
