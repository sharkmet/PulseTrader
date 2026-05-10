"""
Sentiment analysis — pluggable backends, source weighting, rolling windows, momentum.

Backends (controlled by SENTIMENT_ENGINE env var):
  vader   (default) — fast, local, finance-aware
  finbert           — slower, finance-tuned transformer (needs pip install ".[finbert]")
  claude            — best quality, costs tokens, requires ANTHROPIC_API_KEY

Source credibility weights: Reuters/Bloomberg tier-1, unknown sources discounted.
Rolling windows: 1h / 24h / 7d sentiment segmented by published_at.
Momentum: compares 1h vs 24h compound to detect sentiment shifts.
"""
from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from datetime import UTC, datetime, timedelta

from backend.app.config import get_settings
from backend.app.models.schemas import (
    EnrichedNewsFeed,
    NewsItem,
    SentimentMomentum,
    SentimentWindow,
)

logger = logging.getLogger(__name__)

# ── Source credibility weights ────────────────────────────────────────────────
# Keys are lower-case substrings matched against publisher name.
# Higher = more trust. Default for unknown sources = 0.8.

_SOURCE_WEIGHTS: dict[str, float] = {
    "reuters": 1.5,
    "bloomberg": 1.5,
    "wall street journal": 1.4,
    "wsj": 1.4,
    "financial times": 1.4,
    " ft ": 1.4,
    "barron": 1.3,
    "cnbc": 1.2,
    "marketwatch": 1.2,
    "seeking alpha": 1.1,
    "associated press": 1.1,
    " ap ": 1.1,
    "new york times": 1.1,
    "nyt": 1.1,
    "yahoo finance": 1.0,
    "the guardian": 1.0,
    "coindesk": 1.2,
    "cointelegraph": 1.1,
    "decrypt": 1.0,
    "the block": 1.1,
}
_DEFAULT_WEIGHT = 0.8


def _get_source_weight(publisher: str) -> float:
    pub_lower = publisher.lower()
    for key, weight in _SOURCE_WEIGHTS.items():
        if key in pub_lower:
            return weight
    return _DEFAULT_WEIGHT


# ── Entity extraction ─────────────────────────────────────────────────────────

_COMMON_WORDS = frozenset([
    "A", "I", "IT", "AM", "AT", "BE", "DO", "GO", "IF", "IN", "IS",
    "OF", "ON", "OR", "TO", "UP", "US", "BY", "NO", "SO", "AS", "AN",
    "HE", "WE", "MY", "OK", "PM", "AM", "PR", "CEO", "CFO", "COO",
    "IPO", "GDP", "CPI", "Fed", "AI", "UK", "EU", "US", "ECB", "IMF",
])

_CRYPTO_SYMBOLS = frozenset([
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "DOT", "LINK",
    "UNI", "AVAX", "MATIC", "ATOM", "LTC", "TRX", "SHIB", "TON", "SUI",
])


def extract_entities(text: str) -> list[str]:
    """
    Extract stock tickers and crypto symbols from a headline or summary.
    Returns up to 5 unique entities, sorted.
    """
    found: set[str] = set()

    # Dollar-prefixed tickers: $AAPL, $BTC
    dollar = re.findall(r"\$([A-Z]{1,5})", text)
    found.update(dollar)

    # Known crypto symbols anywhere in text (uppercase match)
    upper_text = text.upper()
    for sym in _CRYPTO_SYMBOLS:
        if re.search(rf"\b{sym}\b", upper_text):
            found.add(sym)

    # All-caps 2-5 letter words that look like tickers
    caps = re.findall(r"\b([A-Z]{2,5})\b", text)
    for word in caps:
        if word not in _COMMON_WORDS:
            found.add(word)

    return sorted(found)[:5]


# ── Sentiment backends ────────────────────────────────────────────────────────

def _analyze_vader(text: str) -> float:
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        return float(SentimentIntensityAnalyzer().polarity_scores(text)["compound"])
    except ImportError:
        logger.warning("vaderSentiment not installed — returning 0.0")
        return 0.0


class _FinBERT:
    """Lazy-loading FinBERT wrapper. Loaded once, then cached."""

    def __init__(self) -> None:
        self._pipe = None

    def _load(self):
        if self._pipe is None:
            try:
                from transformers import pipeline as hf_pipeline
                settings = get_settings()
                self._pipe = hf_pipeline(
                    "text-classification",
                    model=settings.finbert_model,
                    return_all_scores=True,
                    device=-1,  # CPU; set device=0 for GPU
                )
                logger.info("FinBERT loaded: %s", settings.finbert_model)
            except ImportError as exc:
                raise ImportError(
                    "FinBERT requires 'transformers' and 'torch'. "
                    "Install with: pip install 'transformers torch'"
                ) from exc
        return self._pipe

    def analyze(self, text: str) -> float:
        pipe = self._load()
        try:
            result = pipe(text[:512], return_all_scores=True)
            # result = [[{"label": "positive", "score": 0.9}, ...]]
            scores = {r["label"]: r["score"] for r in result[0]}
            pos = scores.get("positive", 0.0)
            neg = scores.get("negative", 0.0)
            return round(pos - neg, 4)
        except Exception as exc:
            logger.warning("FinBERT inference failed: %s — falling back to VADER", exc)
            return _analyze_vader(text)

    def available(self) -> bool:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
            return True
        except ImportError:
            return False


_finbert = _FinBERT()


def _analyze_finbert(text: str) -> float:
    """FinBERT compound score -1 to +1. Falls back to VADER if not installed."""
    if not _finbert.available():
        logger.debug("FinBERT not available — using VADER")
        return _analyze_vader(text)
    return _finbert.analyze(text)


def _analyze_claude(text: str, ticker: str) -> float:
    settings = get_settings()
    if not settings.has_anthropic_key:
        return _analyze_vader(text)
    try:
        import anthropic

        from backend.app.utils.token_logger import log_usage
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = (
            f"Analyze the sentiment of this financial news for {ticker}. "
            f"Reply with ONLY a number from -1.0 (extremely bearish) to +1.0 (extremely bullish). "
            f"No explanation.\n\nText: {text[:400]}"
        )
        msg = client.messages.create(
            model=settings.claude_model,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        log_usage(
            purpose="sentiment",
            ticker=ticker,
            model=settings.claude_model,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )
        return max(-1.0, min(1.0, float(msg.content[0].text.strip())))
    except Exception as exc:
        logger.warning("Claude sentiment failed (%s) — falling back to VADER", exc)
        return _analyze_vader(text)


def _run_engine(text: str, engine: str, ticker: str) -> tuple[float, str]:
    """Run the configured engine, return (compound, engine_name)."""
    if engine == "finbert":
        return _analyze_finbert(text), "finbert"
    if engine == "claude":
        return _analyze_claude(text, ticker), "claude"
    return _analyze_vader(text), "vader"


# ── Public sentiment label ────────────────────────────────────────────────────

def score_label(compound: float) -> str:
    if compound >= 0.35: return "bullish"
    if compound >= 0.05: return "slightly bullish"
    if compound > -0.05: return "neutral"
    if compound > -0.35: return "slightly bearish"
    return "bearish"


# ── Main enrichment function ─────────────────────────────────────────────────

def analyze_news(items: list[NewsItem], ticker: str = "") -> list[NewsItem]:
    """
    Enrich each NewsItem with:
    - sentiment_score / sentiment_label / sentiment_engine
    - source_weight (credibility)
    - entities (ticker/company mentions)
    """
    settings = get_settings()
    engine = settings.sentiment_engine.lower()

    # FinBERT: limit items to finbert_max_items to control latency
    finbert_limit = settings.finbert_max_items if engine == "finbert" else len(items)
    enriched: list[NewsItem] = []

    for i, item in enumerate(items):
        text = item.title + (" " + item.summary if item.summary else "")

        if i < finbert_limit:
            compound, engine_used = _run_engine(text, engine, ticker)
        else:
            # Fall back to VADER for items beyond finbert_limit
            compound, engine_used = _analyze_vader(text), "vader"

        enriched.append(
            item.model_copy(
                update={
                    "sentiment_score": round(compound, 4),
                    "sentiment_label": score_label(compound),
                    "sentiment_engine": engine_used,
                    "source_weight": _get_source_weight(item.publisher),
                    "entities": extract_entities(item.title),
                }
            )
        )
    return enriched


# ── Rolling windows ───────────────────────────────────────────────────────────

def compute_sentiment_windows(items: list[NewsItem]) -> dict[str, SentimentWindow]:
    """
    Segment scored news items into time windows and compute per-window sentiment.
    Requires items to have sentiment_score populated.
    """
    now = datetime.now(UTC)
    window_defs: dict[str, timedelta] = {
        "1h": timedelta(hours=1),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
    }
    result: dict[str, SentimentWindow] = {}

    for label, delta in window_defs.items():
        cutoff = now - delta
        window_items = [i for i in items if i.published_at >= cutoff]
        scored = [i for i in window_items if i.sentiment_score is not None]

        if scored:
            compound = statistics.mean(i.sentiment_score for i in scored)  # type: ignore[arg-type]
            total_w = sum(i.source_weight for i in scored)
            weighted_compound = (
                sum(i.sentiment_score * i.source_weight for i in scored) / total_w  # type: ignore[operator]
                if total_w > 0 else 0.0
            )
        else:
            compound = weighted_compound = 0.0

        result[label] = SentimentWindow(
            window=label,
            item_count=len(window_items),
            compound=round(compound, 4),
            weighted_compound=round(weighted_compound, 4),
            label=score_label(compound),
        )

    return result


def compute_sentiment_momentum(windows: dict[str, SentimentWindow]) -> SentimentMomentum:
    """
    Compare 1h vs 24h sentiment to detect recent shifts.
    A positive delta means sentiment is improving.
    """
    recent = windows.get("1h")
    baseline = windows.get("24h")

    if recent is None or baseline is None or recent.item_count == 0:
        return SentimentMomentum(
            direction="stable",
            delta=0.0,
            detail="Insufficient recent news for momentum signal",
        )

    delta = recent.compound - baseline.compound

    if abs(delta) < 0.05:
        direction = "stable"
        detail = f"Sentiment stable (1h={recent.compound:+.3f} vs 24h={baseline.compound:+.3f})"
    elif delta > 0:
        direction = "rising"
        detail = f"Sentiment improving: 1h {recent.compound:+.3f} vs 24h {baseline.compound:+.3f}"
    else:
        direction = "falling"
        detail = f"Sentiment deteriorating: 1h {recent.compound:+.3f} vs 24h {baseline.compound:+.3f}"

    return SentimentMomentum(direction=direction, delta=round(delta, 4), detail=detail)


def weighted_sentiment(items: list[NewsItem]) -> float:
    """Source-credibility-weighted compound sentiment, -1 to +1."""
    scored = [i for i in items if i.sentiment_score is not None]
    if not scored:
        return 0.0
    total_w = sum(i.source_weight for i in scored)
    if total_w <= 0:
        return 0.0
    return round(
        sum(i.sentiment_score * i.source_weight for i in scored) / total_w,  # type: ignore[operator]
        4,
    )


def top_entities(items: list[NewsItem], limit: int = 5) -> list[str]:
    """Return the most-mentioned entities across all news items."""
    counter: Counter[str] = Counter()
    for item in items:
        counter.update(item.entities)
    return [ent for ent, _ in counter.most_common(limit)]


# ── Legacy helpers ────────────────────────────────────────────────────────────

def rolling_sentiment(items: list[NewsItem]) -> tuple[float, str]:
    """Simple mean compound sentiment. Kept for backward compatibility."""
    scored = [i.sentiment_score for i in items if i.sentiment_score is not None]
    if not scored:
        return 0.0, "neutral"
    avg = statistics.mean(scored)
    return round(avg, 4), score_label(avg)


def sentiment_to_score(compound: float) -> float:
    """Map compound [-1, +1] to Pulse Score component [0, 100]."""
    return (compound + 1) / 2 * 100


def build_enriched_feed(ticker: str, scored_items: list[NewsItem]) -> EnrichedNewsFeed:
    """
    Given a list of scored NewsItems, build the full EnrichedNewsFeed with
    windows, momentum, weighted sentiment, and entity summary.
    """
    compound, label = rolling_sentiment(scored_items)
    windows = compute_sentiment_windows(scored_items)
    momentum = compute_sentiment_momentum(windows)
    weighted = weighted_sentiment(scored_items)
    entities = top_entities(scored_items)
    engines = list({i.sentiment_engine for i in scored_items if i.sentiment_engine})

    return EnrichedNewsFeed(
        ticker=ticker,
        items=scored_items,
        rolling_sentiment=compound,
        sentiment_label=label,
        windows=windows,
        momentum=momentum,
        source_weighted_sentiment=weighted,
        top_entities=entities,
        engines_used=sorted(engines),
    )
