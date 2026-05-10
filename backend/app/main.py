"""FastAPI application factory."""
from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.config import get_settings
from backend.app.database import init_db
from backend.app.models.schemas import HealthResponse
from backend.app.utils.logging_config import setup_loguru

# ── Request context middleware ────────────────────────────────────────────────

class _RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Attaches a unique request_id to every request.
    Adds X-Request-ID and X-Response-Time-Ms response headers.
    Logs each request at INFO level via loguru.
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[type-arg]
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        start = time.monotonic()
        request.state.request_id = request_id

        response: Response = await call_next(request)

        latency_ms = round((time.monotonic() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(latency_ms)

        # Skip logging for health checks to reduce noise
        if request.url.path not in ("/health",):
            logger.info(
                "{method} {path} → {status} ({latency}ms) [{rid}]",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                latency=latency_ms,
                rid=request_id,
            )

        return response


# ── Lifespan ──────────────────────────────────────────────────────────────────

_DEFAULT_WATCHLIST = [
    # Indices (ETFs)
    ("SPY",     "S&P 500",               "stock"),
    ("QQQ",     "Nasdaq 100",            "stock"),
    ("DIA",     "Dow Jones",             "stock"),
    ("IWM",     "Russell 2000",          "stock"),
    # Mag 7
    ("NVDA",    "NVIDIA Corporation",    "stock"),
    ("AAPL",    "Apple Inc.",            "stock"),
    ("MSFT",    "Microsoft Corp.",       "stock"),
    ("META",    "Meta Platforms",        "stock"),
    ("GOOGL",   "Alphabet Inc.",         "stock"),
    ("AMZN",    "Amazon.com Inc.",       "stock"),
    ("TSLA",    "Tesla, Inc.",           "stock"),
    # AI / semiconductors
    ("AMD",     "Advanced Micro Devices","stock"),
    ("INTC",    "Intel Corporation",     "stock"),
    ("QCOM",    "Qualcomm",              "stock"),
    ("AVGO",    "Broadcom",              "stock"),
    ("TSM",     "Taiwan Semiconductor",  "stock"),
    # High-growth / AI software
    ("PLTR",    "Palantir Technologies", "stock"),
    ("ARM",     "Arm Holdings",          "stock"),
    ("SNOW",    "Snowflake",             "stock"),
    ("CRWD",    "CrowdStrike",           "stock"),
    ("NET",     "Cloudflare",            "stock"),
    # Finance
    ("JPM",     "JPMorgan Chase",        "stock"),
    ("GS",      "Goldman Sachs",         "stock"),
    ("V",       "Visa",                  "stock"),
    # Crypto-adjacent stocks
    ("COIN",    "Coinbase Global",       "stock"),
    ("MSTR",    "MicroStrategy",         "stock"),
    # Crypto
    ("BTC-USD", "Bitcoin",               "crypto"),
    ("ETH-USD", "Ethereum",              "crypto"),
    ("SOL-USD", "Solana",                "crypto"),
    ("BNB-USD", "BNB",                   "crypto"),
    ("XRP-USD", "XRP",                   "crypto"),
]


_DEFAULT_ALERTS = [
    # Score alerts — fire when pulse score crosses threshold
    ("NVDA",    "score_above", 82),
    ("AMD",     "score_above", 78),
    ("PLTR",    "score_below", 40),
    ("TSLA",    "score_below", 38),
    ("BTC-USD", "score_above", 75),
    ("ETH-USD", "score_below", 35),
    # Price alerts
    ("AAPL",    "price_above", 230),
    ("MSFT",    "price_above", 460),
    ("NVDA",    "price_below", 1000),
    ("BTC-USD", "price_above", 110000),
    ("SPY",     "price_below", 520),
]


def _seed_watchlist() -> None:
    """Populate watchlist with defaults if it is empty."""
    from backend.app.database import SessionLocal
    from backend.app.models.db import WatchlistItem
    with SessionLocal() as db:
        if db.query(WatchlistItem).count() > 0:
            return
        for ticker, name, asset_type in _DEFAULT_WATCHLIST:
            db.add(WatchlistItem(ticker=ticker, name=name, asset_type=asset_type))
        db.commit()
    logger.info("Seeded watchlist with %d default tickers", len(_DEFAULT_WATCHLIST))


def _seed_alerts() -> None:
    """Seed default alert rules if the alerts table is empty."""
    from backend.app.database import SessionLocal
    from backend.app.models.db import AlertRule
    with SessionLocal() as db:
        if db.query(AlertRule).count() > 0:
            return
        for ticker, alert_type, threshold in _DEFAULT_ALERTS:
            db.add(AlertRule(
                ticker=ticker,
                alert_type=alert_type,
                threshold=float(threshold),
                notification_channels="browser",
            ))
        db.commit()
    logger.info("Seeded %d default alerts", len(_DEFAULT_ALERTS))


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    setup_loguru(level="DEBUG" if settings.debug else "INFO")
    init_db()
    logger.info("Database initialized")
    _seed_watchlist()
    _seed_alerts()
    import threading

    from backend.app.scheduler import _refresh_watchlist, start_scheduler
    # Warm the watchlist immediately in the background (don't block startup)
    threading.Thread(target=_refresh_watchlist, daemon=True, name="startup-refresh").start()
    start_scheduler()
    yield
    from backend.app.scheduler import stop_scheduler
    stop_scheduler()
    logger.info("Scheduler stopped — shutdown complete")


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="PulseTrader API",
        description=(
            "**Educational** stock and crypto analysis engine.\n\n"
            "NOT financial advice. For research and learning purposes only.\n\n"
            "## Key endpoints\n"
            "- `GET /assets/{ticker}` — full snapshot (price + indicators + score)\n"
            "- `GET /assets/{ticker}/pulse-score` — Pulse Score with 7-component breakdown\n"
            "- `POST /assets/{ticker}/deep-dive` — Claude AI deep-dive analysis\n"
            "- `POST /assets/{ticker}/news/interpret` — AI news theme extraction\n"
            "- `GET /macro/snapshot` — macro indicators (VIX, yields, CPI)\n"
            "- `GET /admin/usage` — token costs and budget status\n"
        ),
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_lifespan,
    )

    app.add_middleware(_RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register specific routes before the catch-all /{ticker} snapshot route
    from backend.app.api import (
        ai,
        alerts,
        assets,
        backtest,
        interpret,
        market,
        news,
        score,
        watchlist,
    )
    app.include_router(assets.router)
    app.include_router(assets.macro_router)
    app.include_router(watchlist.router)
    app.include_router(score.router)
    app.include_router(ai.admin_router)
    app.include_router(market.router)
    app.include_router(news.router)
    app.include_router(ai.router)
    app.include_router(interpret.router)
    app.include_router(alerts.router)
    app.include_router(backtest.router)

    # Snapshot router last — its /{ticker} catch-all must not shadow /search
    from backend.app.api import snapshot
    app.include_router(snapshot.router)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/admin/usage", tags=["admin"])
    def token_usage() -> dict:
        """Token usage with daily/monthly breakdown, budget status, and recent calls."""
        from backend.app.utils.token_logger import get_usage_report
        return get_usage_report()

    @app.get("/admin/budget", tags=["admin"])
    def budget_check() -> dict:
        """Quick budget status — today's spend vs configured daily cap."""
        from backend.app.services.budget import budget_status
        return budget_status()

    @app.get("/admin/requests", tags=["admin"])
    def recent_requests(limit: int = 100) -> dict:
        """Recent external data source calls with latency and success/failure."""
        from backend.app.services.circuit_breaker import all_states
        from backend.app.services.observability import recent_requests as _recent
        from backend.app.services.observability import source_stats
        return {
            "circuit_breakers": all_states(),
            "source_stats": source_stats(),
            "recent": _recent(limit),
        }

    @app.get("/admin/jobs", tags=["admin"])
    def scheduler_jobs(limit: int = 50) -> list[dict]:
        """Recent scheduler job runs with duration, item counts, and error details."""
        from backend.app.scheduler import job_history
        return job_history(limit)

    return app


# Single module-level app — uvicorn targets `backend.app.main:app`.
app = create_app()
