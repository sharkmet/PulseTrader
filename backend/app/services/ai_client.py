"""
AI wrapper — Gemini (primary, free) or Claude (fallback), with prompt templates
and budget guardrails.

Provider selection (automatic):
  GOOGLE_API_KEY set  → Gemini 2.0 Flash (free tier)
  ANTHROPIC_API_KEY   → Claude Haiku (fallback if no Google key)
  Neither             → neutral stub (no AI features)

Prompts live in backend/prompts/*.txt. Budget is checked before every call.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from backend.app.config import get_settings
from backend.app.models.schemas import (
    AiAnalysis,
    AiPriceTarget,
    IndicatorSnapshot,
    NewsFeed,
    NewsInterpretation,
    NewsItem,
    NewsTheme,
    OHLCVBar,
)
from backend.app.services.budget import BudgetExceeded, check_budget_or_raise
from backend.app.utils.token_logger import log_usage

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_DISCLAIMER = (
    "IMPORTANT: This is an educational analysis tool. "
    "Nothing here constitutes financial advice."
)


# ── Prompt loader ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=16)
def _load_prompt(name: str) -> str:
    """Load a prompt template from prompts/{name}.txt. Cached."""
    path = _PROMPTS_DIR / f"{name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("Prompt file not found: %s — using empty template", path)
    return ""


def _render(name: str, **kwargs) -> str:
    """Load and render a prompt template with the given variables."""
    template = _load_prompt(name)
    return template.format_map({"disclaimer": _DISCLAIMER, **kwargs})


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(
    ticker: str,
    bars: list[OHLCVBar],
    snap: IndicatorSnapshot | None,
    news_feed: NewsFeed | None,
) -> str:
    lines: list[str] = [f"Asset: {ticker}"]

    if bars:
        latest = bars[-1]
        total_return = (latest.close - bars[0].close) / bars[0].close * 100
        lines.append(
            f"Price: ${latest.close:.4f} | "
            f"Period return: {total_return:+.2f}% ({len(bars)} bars)"
        )

    if snap:
        lines.append("\nTechnical Indicators:")
        for label, ind in [
            ("RSI(14)", snap.rsi_14),
            ("MACD histogram", snap.macd_histogram),
            ("ADX(14)", snap.adx_14),
            ("SMA20", snap.sma_20),
            ("SMA50", snap.sma_50),
            ("SMA200", snap.sma_200),
            ("BB %B", snap.bb_percent_b),
            ("Volume ratio", snap.volume_ratio),
            ("MFI(14)", snap.mfi_14),
        ]:
            if ind and ind.value is not None:
                lines.append(f"  {label}: {ind.value:.4f} — {ind.interpretation}")

    if news_feed and news_feed.items:
        lines.append(f"\nNews Sentiment: {news_feed.rolling_sentiment:+.2f} ({news_feed.sentiment_label})")
        lines.append("Recent Headlines:")
        for item in news_feed.items[:5]:
            score_str = f"[{item.sentiment_score:+.2f}]" if item.sentiment_score is not None else ""
            lines.append(f"  {score_str} {item.title}")

    return "\n".join(lines)


# ── JSON response parser ──────────────────────────────────────────────────────

def _parse_json(text: str) -> dict:
    """
    Extract JSON object from model response.
    Handles markdown code fences (```json ... ```) and leading prose.
    Logs the raw response on failure so we can debug.
    """
    if not text:
        logger.warning("Empty response from AI model")
        return {}

    cleaned = text.strip()

    # Strip markdown code fences if present
    for fence in ("```json", "```JSON", "```"):
        if cleaned.startswith(fence):
            cleaned = cleaned[len(fence):]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            break

    # Try the cleaned version first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fall back: find first { ... last }
    try:
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end])
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse failed: %s | Raw response (first 500 chars): %s", exc, text[:500])

    logger.warning("Could not extract JSON. Full response: %s", text[:1000])
    return {}


def _call_gemini(prompt: str, max_tokens: int, purpose: str, ticker: str) -> tuple[str, int, int]:
    """
    Call Gemini API (Google). Returns (text, input_tokens, output_tokens).
    Raises BudgetExceeded if daily cap reached.
    """
    check_budget_or_raise(purpose)
    settings = get_settings()
    from google import genai
    from google.genai import types as gtypes
    client = genai.Client(api_key=settings.google_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=gtypes.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    text = response.text or ""
    usage = response.usage_metadata
    in_tok = getattr(usage, "prompt_token_count", 0) or 0
    out_tok = getattr(usage, "candidates_token_count", 0) or 0
    log_usage(purpose=purpose, ticker=ticker, model=settings.gemini_model,
              input_tokens=in_tok, output_tokens=out_tok)
    return text, in_tok, out_tok


def _call_claude(prompt: str, max_tokens: int, purpose: str, ticker: str) -> tuple[str, int, int]:
    """Call Claude API (Anthropic fallback). Returns (text, input_tokens, output_tokens)."""
    check_budget_or_raise(purpose)
    settings = get_settings()
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    log_usage(purpose=purpose, ticker=ticker, model=settings.claude_model,
              input_tokens=msg.usage.input_tokens, output_tokens=msg.usage.output_tokens)
    return msg.content[0].text, msg.usage.input_tokens, msg.usage.output_tokens


def _call_ai(prompt: str, max_tokens: int, purpose: str, ticker: str) -> tuple[str, int, int]:
    """Route to Gemini (preferred) or Claude (fallback)."""
    settings = get_settings()
    if settings.has_google_key:
        return _call_gemini(prompt, max_tokens, purpose, ticker)
    return _call_claude(prompt, max_tokens, purpose, ticker)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_analysis(
    ticker: str,
    bars: list[OHLCVBar],
    snap: IndicatorSnapshot | None,
    news_feed: NewsFeed | None,
) -> AiAnalysis:
    """Deep-dive analysis. Returns neutral fallback if no API key or budget exceeded."""
    settings = get_settings()

    if not settings.has_ai_key:
        return AiAnalysis(
            ticker=ticker,
            summary="Add GOOGLE_API_KEY to .env to enable AI analysis (free via Gemini).",
            bullish_factors=[],
            bearish_factors=[],
            watch_for=["Set GOOGLE_API_KEY in .env — get a free key at aistudio.google.com"],
            ai_score=50.0,
            model_used="none",
            generated_at=datetime.now(UTC),
        )

    context = _build_context(ticker, bars, snap, news_feed)
    prompt = _render("deep_dive", ticker=ticker, context=context)

    try:
        text, in_tok, out_tok = _call_ai(prompt, max_tokens=2048, purpose="deep_dive", ticker=ticker)
        parsed = _parse_json(text)

        # Parse optional price target
        pt_raw = parsed.get("price_target")
        price_target: AiPriceTarget | None = None
        if isinstance(pt_raw, dict):
            try:
                price_target = AiPriceTarget(
                    low=float(pt_raw["low"]),
                    mid=float(pt_raw["mid"]),
                    high=float(pt_raw["high"]),
                    horizon=str(pt_raw.get("horizon", "12M")),
                )
            except (KeyError, ValueError, TypeError):
                pass

        return AiAnalysis(
            ticker=ticker,
            summary=parsed.get("summary", "Analysis unavailable"),
            bullish_factors=parsed.get("bullish_factors", []),
            bearish_factors=parsed.get("bearish_factors", []),
            watch_for=parsed.get("watch_for", []),
            ai_score=float(parsed.get("score", 50)),
            price_target=price_target,
            model_used=settings.active_ai_model,
            generated_at=datetime.now(UTC),
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
    except BudgetExceeded:
        raise  # let API layer convert to 429
    except Exception as exc:
        logger.error("AI analysis failed for %s: %s", ticker, exc)
        return AiAnalysis(
            ticker=ticker,
            summary=f"Analysis temporarily unavailable: {exc}",
            bullish_factors=[],
            bearish_factors=[],
            watch_for=["Retry in a few moments"],
            ai_score=50.0,
            model_used=settings.active_ai_model,
            generated_at=datetime.now(UTC),
        )


def get_ai_score(ticker: str, context_summary: str) -> float:
    """
    Lightweight score call for the Pulse Score AI component.
    Returns 50.0 on any failure (no crash — score degrades gracefully).
    """
    settings = get_settings()
    if not settings.has_ai_key:
        return 50.0

    prompt = _render("ai_score", ticker=ticker, context=context_summary)
    try:
        text, _, _ = _call_ai(prompt, max_tokens=5, purpose="pulse_score_ai", ticker=ticker)
        return max(0.0, min(100.0, float(text.strip())))
    except BudgetExceeded:
        logger.info("AI score skipped for %s — daily budget exceeded", ticker)
        return 50.0
    except Exception as exc:
        logger.warning("AI score failed for %s: %s — using 50", ticker, exc)
        return 50.0


def interpret_news(ticker: str, items: list[NewsItem]) -> NewsInterpretation:
    """
    Send headlines to Claude and get back structured themes and market implications.
    Raises BudgetExceeded if daily cap is reached.
    """
    settings = get_settings()

    if not settings.has_ai_key:
        return NewsInterpretation(
            ticker=ticker,
            summary="Add GOOGLE_API_KEY to .env to enable news interpretation (free via Gemini).",
            themes=[],
            overall_sentiment="neutral",
            key_catalysts=[],
            risk_factors=[],
            model_used="none",
            generated_at=datetime.now(UTC),
        )

    headlines = "\n".join(
        f"[{i.sentiment_score:+.2f}] {i.title}"
        if i.sentiment_score is not None
        else f"[ N/A ] {i.title}"
        for i in items[:15]
    )
    prompt = _render(
        "news_interpreter",
        ticker=ticker,
        headlines=headlines,
        headline_count=len(items[:15]),
    )

    text, _in_tok, _out_tok = _call_ai(
        prompt, max_tokens=800, purpose="news_interpret", ticker=ticker
    )
    parsed = _parse_json(text)

    raw_themes = parsed.get("themes", [])
    themes = [
        NewsTheme(
            theme=t.get("theme", ""),
            sentiment=t.get("sentiment", "neutral"),
            headline_count=int(t.get("headline_count", 1)),
        )
        for t in raw_themes
        if isinstance(t, dict) and t.get("theme")
    ]

    return NewsInterpretation(
        ticker=ticker,
        summary=parsed.get("summary", "Interpretation unavailable"),
        themes=themes,
        overall_sentiment=parsed.get("overall_sentiment", "neutral"),
        key_catalysts=parsed.get("key_catalysts", []),
        risk_factors=parsed.get("risk_factors", []),
        model_used=settings.claude_model,
        generated_at=datetime.now(UTC),
    )
