"""
AI Market Brief endpoint — Gemini-generated overview of the current watchlist.
Cached in SQLite for 15 minutes so it doesn't call Gemini on every page load.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from backend.app.config import get_settings
from backend.app.database import SessionLocal
from backend.app.models.db import AiAnalysisCache
from backend.app.models.schemas import MarketBrief, MarketBriefBullet
from backend.app.services.ai_client import _call_ai, _parse_json, _render
from backend.app.services.budget import BudgetExceeded

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/market", tags=["market"])

_CACHE_KEY = "market:brief"
_CACHE_TTL = 900   # 15 minutes


def _build_market_context() -> str:
    """Assemble a data snapshot of the full watchlist for the AI prompt."""
    from backend.app.database import SessionLocal
    from backend.app.models.db import WatchlistItem

    with SessionLocal() as db:
        items = db.query(WatchlistItem).all()

    if not items:
        return "No watchlist data available."

    lines: list[str] = [
        f"Watchlist snapshot — {len(items)} tickers — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    gainers = sorted(items, key=lambda x: x.change_1d_pct or 0, reverse=True)[:5]
    losers  = sorted(items, key=lambda x: x.change_1d_pct or 0)[:5]
    high_pulse = sorted(items, key=lambda x: x.pulse_score or 50, reverse=True)[:5]
    low_pulse  = sorted(items, key=lambda x: x.pulse_score or 50)[:5]

    bulls = sum(1 for i in items if (i.change_1d_pct or 0) > 0)
    bears = sum(1 for i in items if (i.change_1d_pct or 0) < 0)
    avg_score = sum(i.pulse_score or 50 for i in items) / len(items) if items else 50

    lines.append(f"Breadth: {bulls} up / {bears} down | Avg Pulse Score: {avg_score:.1f}/100")
    lines.append("")

    lines.append("Top gainers:")
    for i in gainers:
        if i.price:
            lines.append(f"  {i.ticker}: ${i.price:.2f} ({'+' if (i.change_1d_pct or 0) >= 0 else ''}{i.change_1d_pct or 0:.2f}%) Pulse={i.pulse_score or 50:.0f}")

    lines.append("Top losers:")
    for i in losers:
        if i.price:
            lines.append(f"  {i.ticker}: ${i.price:.2f} ({'+' if (i.change_1d_pct or 0) >= 0 else ''}{i.change_1d_pct or 0:.2f}%) Pulse={i.pulse_score or 50:.0f}")

    lines.append("Strongest signals (high Pulse):")
    for i in high_pulse:
        lines.append(f"  {i.ticker}: Pulse={i.pulse_score or 50:.0f}")

    lines.append("Weakest signals (low Pulse):")
    for i in low_pulse:
        lines.append(f"  {i.ticker}: Pulse={i.pulse_score or 50:.0f}")

    return "\n".join(lines)


def _neutral_brief() -> MarketBrief:
    return MarketBrief(
        headline="Add GOOGLE_API_KEY to .env to enable the AI market brief.",
        body="The market brief uses Gemini to summarise live watchlist signals into a concise overview. Get a free key at aistudio.google.com.",
        posture="No AI Key",
        confidence=0,
        bullets=[
            MarketBriefBullet(tag="WATCH", text="Set GOOGLE_API_KEY in backend/.env and restart the server."),
        ],
        generated_at=datetime.now(UTC),
        model_used="none",
    )


@router.get("/brief", response_model=MarketBrief)
def get_market_brief() -> MarketBrief:
    """
    AI-generated market overview for the current watchlist.
    Calls Gemini with live price + pulse score data and returns a structured brief.
    Cached for 15 minutes — POST /market/brief/refresh to force a new one.
    """
    settings = get_settings()

    if not settings.has_ai_key:
        return _neutral_brief()

    # Check cache
    cutoff = datetime.now(UTC) - timedelta(seconds=_CACHE_TTL)
    with SessionLocal() as db:
        row = (
            db.query(AiAnalysisCache)
            .filter(
                AiAnalysisCache.ticker == _CACHE_KEY,
                AiAnalysisCache.generated_at >= cutoff,
            )
            .order_by(AiAnalysisCache.generated_at.desc())
            .first()
        )
        if row:
            try:
                data = json.loads(row.analysis_json)
                data["cached"] = True
                return MarketBrief(**data)
            except Exception:
                pass

    # Generate fresh
    context = _build_market_context()
    prompt = _render("market_brief", context=context)

    try:
        text, _in, _out = _call_ai(prompt, max_tokens=1500, purpose="market_brief", ticker="MARKET")
        parsed = _parse_json(text)

        raw_bullets = parsed.get("bullets", [])
        bullets = [
            MarketBriefBullet(
                tag=b.get("tag", "WATCH").upper(),
                text=b.get("text", ""),
            )
            for b in raw_bullets
            if isinstance(b, dict) and b.get("text")
        ]

        brief = MarketBrief(
            headline=parsed.get("headline", "Market data loaded."),
            body=parsed.get("body", ""),
            posture=parsed.get("posture", "Neutral"),
            confidence=int(parsed.get("confidence", 50)),
            bullets=bullets,
            generated_at=datetime.now(UTC),
            model_used=settings.active_ai_model,
        )
    except BudgetExceeded:
        brief = MarketBrief(
            headline="Daily AI budget reached — brief unavailable.",
            body="Increase DAILY_TOKEN_BUDGET_USD in .env to re-enable.",
            posture="Budget Exceeded",
            confidence=0,
            bullets=[],
            generated_at=datetime.now(UTC),
            model_used=settings.active_ai_model,
        )
    except Exception as exc:
        logger.error("Market brief generation failed: %s", exc)
        brief = MarketBrief(
            headline="Brief temporarily unavailable.",
            body=str(exc)[:200],
            posture="Error",
            confidence=0,
            bullets=[],
            generated_at=datetime.now(UTC),
            model_used=settings.active_ai_model,
        )

    # Cache the result (only if it has content)
    if brief.bullets:
        with SessionLocal() as db:
            try:
                db.add(AiAnalysisCache(
                    ticker=_CACHE_KEY,
                    analysis_json=brief.model_dump_json(),
                    generated_at=datetime.now(UTC),
                ))
                db.commit()
            except Exception as exc:
                logger.warning("Market brief cache write failed: %s", exc)

    return brief


@router.post("/brief/refresh", response_model=dict)
def refresh_market_brief() -> dict:
    """Force-expire the cached market brief so the next GET generates a fresh one."""
    with SessionLocal() as db:
        db.query(AiAnalysisCache).filter(AiAnalysisCache.ticker == _CACHE_KEY).delete()
        db.commit()
    return {"status": "cache cleared", "message": "Next GET /market/brief will call the AI provider fresh."}
