"""
DataSource protocol — every external data adapter must satisfy this interface.
Swap providers without changing any calling code.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.app.models.schemas import NewsItem, OHLCVBar, Quote


@runtime_checkable
class OHLCVSource(Protocol):
    """Provides historical price bars."""

    name: str

    def fetch_ohlcv(self, ticker: str, period: str, interval: str) -> list[OHLCVBar]:
        """Return bars in ascending timestamp order. Empty list on failure."""
        ...

    def supports(self, ticker: str) -> bool:
        """Return True if this source can handle the given ticker."""
        ...


@runtime_checkable
class QuoteSource(Protocol):
    """Provides real-time or delayed quotes."""

    name: str

    def fetch_quote(self, ticker: str) -> Quote | None:
        """Return latest quote, or None on failure."""
        ...

    def supports(self, ticker: str) -> bool: ...


@runtime_checkable
class NewsSource(Protocol):
    """Provides news articles for a ticker."""

    name: str

    def fetch_news(self, ticker: str) -> list[NewsItem]:
        """Return recent news, newest first. Empty list on failure."""
        ...

    def supports(self, ticker: str) -> bool: ...
