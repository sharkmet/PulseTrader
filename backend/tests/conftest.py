"""Shared test fixtures. All external APIs are mocked — tests never hit yfinance or Anthropic."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from backend.app.models.schemas import NewsItem, OHLCVBar


@pytest.fixture()
def sample_bars() -> list[OHLCVBar]:
    """60 daily bars of synthetic AAPL-like data."""
    import math
    import random

    rng = random.Random(42)
    base = 180.0
    now = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    bars = []
    price = base
    for i in range(60):
        drift = math.sin(i / 10) * 0.5
        noise = rng.uniform(-1.5, 1.5)
        close = max(1.0, price + drift + noise)
        bars.append(
            OHLCVBar(
                timestamp=now + dt.timedelta(days=i),
                open=price,
                high=close + rng.uniform(0.5, 2.0),
                low=close - rng.uniform(0.5, 2.0),
                close=close,
                volume=rng.uniform(50_000_000, 120_000_000),
            )
        )
        price = close
    return bars


@pytest.fixture()
def sample_news() -> list[NewsItem]:
    now = dt.datetime.now(dt.UTC)
    return [
        NewsItem(title="Apple reports record earnings", url="http://x.com/1", publisher="Reuters", published_at=now),
        NewsItem(title="Market selloff hits tech stocks", url="http://x.com/2", publisher="Bloomberg", published_at=now),
        NewsItem(title="iPhone demand steady in Asia", url="http://x.com/3", publisher="CNBC", published_at=now),
    ]


@pytest.fixture()
def mock_yfinance(sample_bars):
    """Patch yfinance_client to return synthetic data."""
    with patch("backend.app.services.yfinance_client.fetch_ohlcv", return_value=sample_bars) as m:
        yield m


@pytest.fixture()
def mock_anthropic():
    """Patch the anthropic client so no real API calls are made."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"score": 65, "summary": "Test summary.", "bullish_factors": ["Factor A"], "bearish_factors": ["Factor B"], "watch_for": ["Thing C"]}')]
    mock_msg.usage.input_tokens = 100
    mock_msg.usage.output_tokens = 50
    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_msg
        yield mock_cls
