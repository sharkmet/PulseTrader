"""
PulseTrader MCP Server

Exposes the analysis engine to Claude Desktop and Claude Code as a set of MCP tools.
Claude can call these directly from a conversation to get live market analysis.

Setup (Claude Desktop):
  1. pip install ".[mcp]"
  2. Add to claude_desktop_config.json:
     {
       "mcpServers": {
         "pulsetrader": {
           "command": "pulse-mcp",
           "env": {
             "ANTHROPIC_API_KEY": "...",
             "DATABASE_URL": "sqlite:///./pulsetrader.db"
           }
         }
       }
     }

Available tools:
  get_pulse_score      - Pulse Score (0-100) with 7-component breakdown
  get_indicators       - Full technical indicator set with interpretations
  get_asset_snapshot   - Everything in one call (price + indicators + score)
  compare_assets       - Side-by-side score comparison for multiple tickers
  run_backtest_quick   - Historical signal quality evaluation (no AI, fast)
  get_macro_context    - Current macro regime (VIX, yields, CPI) via FRED

Educational use only. Not financial advice.
"""
from __future__ import annotations

import json
import sys


def _check_mcp() -> None:
    try:
        import mcp  # noqa: F401
    except ImportError:
        print(
            "ERROR: MCP package not installed.\n"
            "Install with: pip install 'pulsetrader-backend[mcp]'\n"
            "or:           pip install mcp",
            file=sys.stderr,
        )
        sys.exit(1)


def _setup_db() -> None:
    from backend.app.database import init_db
    init_db()


# ── Tool implementations ──────────────────────────────────────────────────────

def _tool_get_pulse_score(ticker: str, include_mtf: bool = False) -> str:
    """Compute Pulse Score with full 7-component decomposition."""
    from backend.app.services import data_fetcher
    from backend.app.services.indicators import compute_indicators, compute_multi_timeframe
    from backend.app.services.news_fetcher import get_news_feed
    from backend.app.services.pulse_score import compute_pulse_score

    t = ticker.upper()
    bars = data_fetcher.get_ohlcv(t, period="6mo", interval="1d")
    if not bars:
        return json.dumps({"error": f"No price data found for {t}"})

    snap = compute_indicators(t, bars)
    news = get_news_feed(t)

    mtf = None
    if include_mtf:
        tf_specs = {"1h": ("5d", "1h"), "1d": ("6mo", "1d"), "1w": ("5y", "1wk")}
        bars_by_tf = {lbl: data_fetcher.get_ohlcv(t, p, iv) for lbl, (p, iv) in tf_specs.items()}
        bars_by_tf = {k: v for k, v in bars_by_tf.items() if v}
        if bars_by_tf:
            mtf = compute_multi_timeframe(t, bars_by_tf)

    score = compute_pulse_score(ticker=t, bars=bars, snap=snap, news_feed=news,
                                ai_score=50.0, mtf_snapshot=mtf)
    return score.model_dump_json(indent=2)


def _tool_get_indicators(ticker: str, period: str = "6mo") -> str:
    """Get full technical indicator set with plain-English interpretations."""
    from backend.app.services import data_fetcher
    from backend.app.services.indicators import compute_indicators

    t = ticker.upper()
    bars = data_fetcher.get_ohlcv(t, period=period, interval="1d")
    if not bars:
        return json.dumps({"error": f"No price data for {t}"})

    snap = compute_indicators(t, bars)
    if snap is None:
        return json.dumps({"error": f"Insufficient data for {t} (need ≥30 bars)"})
    return snap.model_dump_json(indent=2)


def _tool_get_asset_snapshot(ticker: str) -> str:
    """Full analysis snapshot: price + indicators + Pulse Score + news sentiment."""
    from datetime import UTC, datetime

    from backend.app.services import data_fetcher
    from backend.app.services.indicators import compute_indicators
    from backend.app.services.news_fetcher import get_news_feed
    from backend.app.services.pulse_score import compute_pulse_score

    t = ticker.upper()
    bars = data_fetcher.get_ohlcv(t, period="6mo", interval="1d")
    if not bars:
        return json.dumps({"error": f"No price data for {t}"})

    quote = data_fetcher.get_quote(t)
    snap = compute_indicators(t, bars)
    news = get_news_feed(t)
    score = compute_pulse_score(ticker=t, bars=bars, snap=snap, news_feed=news, ai_score=50.0)

    result = {
        "ticker": t,
        "fetched_at": datetime.now(UTC).isoformat(),
        "price": quote.price if quote else bars[-1].close,
        "change_1d_pct": quote.change_1d_pct if quote else None,
        "pulse_score": score.overall,
        "pulse_label": score.label,
        "scoring_version": score.scoring_version,
        "components": {
            "trend": score.trend.score,
            "momentum": score.momentum.score,
            "volatility": score.volatility.score,
            "volume": score.volume.score,
            "sentiment": score.sentiment.score,
            "multi_timeframe": score.multi_timeframe.score,
        },
        "key_indicators": {
            "rsi_14": snap.rsi_14.value if snap else None,
            "rsi_interpretation": snap.rsi_14.interpretation if snap else None,
            "macd_histogram": snap.macd_histogram.value if snap else None,
            "bb_percent_b": snap.bb_percent_b.value if snap else None,
            "hurst_exponent": snap.hurst_exponent.value if snap and snap.hurst_exponent else None,
        },
        "news_sentiment": {
            "compound": news.rolling_sentiment,
            "label": news.sentiment_label,
            "article_count": len(news.items),
            "top_entities": news.top_entities[:3] if hasattr(news, "top_entities") else [],
        },
        "disclaimer": "Educational use only. Not financial advice.",
    }
    return json.dumps(result, indent=2, default=str)


def _tool_compare_assets(tickers: list[str]) -> str:
    """
    Compare Pulse Scores for multiple tickers side by side.
    Returns a ranked table with score breakdown per asset.
    """
    from backend.app.services import data_fetcher
    from backend.app.services.indicators import compute_indicators
    from backend.app.services.news_fetcher import get_news_feed
    from backend.app.services.pulse_score import compute_pulse_score

    results = []
    for raw_ticker in tickers[:8]:  # cap at 8 to avoid abuse
        t = raw_ticker.upper().strip()
        try:
            bars = data_fetcher.get_ohlcv(t, period="3mo", interval="1d")
            if not bars:
                results.append({"ticker": t, "error": "No data"})
                continue
            snap = compute_indicators(t, bars)
            news = get_news_feed(t)
            score = compute_pulse_score(ticker=t, bars=bars, snap=snap, news_feed=news, ai_score=50.0)
            results.append({
                "ticker": t,
                "pulse_score": score.overall,
                "label": score.label,
                "trend": round(score.trend.score, 1),
                "momentum": round(score.momentum.score, 1),
                "sentiment": round(score.sentiment.score, 1),
                "price": bars[-1].close,
            })
        except Exception as exc:
            results.append({"ticker": t, "error": str(exc)})

    results.sort(key=lambda r: r.get("pulse_score", -1), reverse=True)
    return json.dumps({"comparison": results, "disclaimer": "Educational only"}, indent=2)


def _tool_run_backtest_quick(ticker: str, from_date: str, to_date: str, step_days: int = 5) -> str:
    """
    Run a quick historical backtest of the Pulse Score signal.
    Returns directional accuracy, Sharpe ratio, and score calibration.
    No AI or news used — fully deterministic and reproducible.
    """
    from backend.app.services.backtester import fetch_backtest_bars, run_backtest

    bars = fetch_backtest_bars(ticker.upper(), from_date, to_date)
    result = run_backtest(ticker.upper(), bars, from_date, to_date, step_days=step_days, min_lookback=60)

    if result.status == "failed":
        return json.dumps({"error": result.error})

    m = result.metrics
    return json.dumps({
        "ticker": result.ticker,
        "period": f"{from_date} → {to_date}",
        "scoring_version": result.scoring_version,
        "n_predictions": m.n_predictions if m else 0,
        "accuracy_7d": m.accuracy_7d if m else None,
        "sharpe_1d": m.sharpe_1d if m else None,
        "max_drawdown_pct": m.max_drawdown_pct if m else None,
        "mean_score": m.mean_score if m else None,
        "calibration_7d": [
            {"bucket": b.bucket, "n": b.n, "mean_return_7d": b.mean_return_7d}
            for b in (m.calibration if m else [])
        ],
        "report": result.report_markdown,
        "disclaimer": "Educational only. Past signal quality does not predict future returns.",
    }, indent=2, default=str)


def _tool_get_macro_context() -> str:
    """
    Get current macro regime: VIX, Fed Funds Rate, Treasury yields, CPI.
    Requires FRED_API_KEY. Returns empty if not configured.
    """
    from backend.app.services.sources.fred import fetch_snapshot, macro_context_string
    snap = fetch_snapshot()
    if not snap:
        return json.dumps({
            "available": False,
            "message": "FRED_API_KEY not set. Get a free key at https://fred.stlouisfed.org",
        })
    return json.dumps({
        "available": True,
        "summary": macro_context_string(),
        "data": snap,
    }, indent=2, default=str)


# ── MCP server setup ──────────────────────────────────────────────────────────

_TOOLS = [
    {
        "name": "get_pulse_score",
        "description": (
            "Get the Pulse Score (0-100) for a stock or crypto ticker. "
            "Returns a 7-component breakdown: trend, momentum, volatility, volume, "
            "sentiment, multi-timeframe agreement, and AI overlay. "
            "Higher = more bullish signal. Educational use only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL, BTC-USD)"},
                "include_mtf": {"type": "boolean", "description": "Include multi-timeframe analysis", "default": False},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_indicators",
        "description": (
            "Get the full technical indicator set for a ticker: RSI, MACD, Bollinger Bands, "
            "Stochastic, ADX, Ichimoku, OBV, MFI, Hurst exponent, z-score, realized vol, "
            "and more — each with a plain-English interpretation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "period": {"type": "string", "default": "6mo", "description": "Lookback (1mo, 3mo, 6mo, 1y)"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_asset_snapshot",
        "description": (
            "Full analysis snapshot for a ticker: current price, 1-day change, "
            "Pulse Score with breakdown, key indicator values, and news sentiment summary. "
            "Best for a quick overview of an asset."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "compare_assets",
        "description": (
            "Compare up to 8 tickers side by side. Returns a ranked table with "
            "Pulse Score, trend, momentum, and sentiment for each. "
            "Useful for stock screening and relative strength analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of ticker symbols (max 8)",
                },
            },
            "required": ["tickers"],
        },
    },
    {
        "name": "run_backtest_quick",
        "description": (
            "Run a historical backtest of the Pulse Score signal. "
            "Reports directional accuracy (does high score predict positive returns?), "
            "Sharpe ratio of a naive long/short strategy, and score calibration. "
            "No AI or live news used — fully deterministic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                "step_days": {"type": "integer", "default": 5, "description": "Score every N trading days"},
            },
            "required": ["ticker", "from_date", "to_date"],
        },
    },
    {
        "name": "get_macro_context",
        "description": (
            "Get current macro economic indicators: VIX (fear gauge), "
            "Fed Funds Rate, 10Y and 2Y Treasury yields, yield spread, "
            "and CPI. Requires FRED_API_KEY environment variable."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_DISPATCH = {
    "get_pulse_score": lambda args: _tool_get_pulse_score(
        args["ticker"], args.get("include_mtf", False)
    ),
    "get_indicators": lambda args: _tool_get_indicators(
        args["ticker"], args.get("period", "6mo")
    ),
    "get_asset_snapshot": lambda args: _tool_get_asset_snapshot(args["ticker"]),
    "compare_assets": lambda args: _tool_compare_assets(args["tickers"]),
    "run_backtest_quick": lambda args: _tool_run_backtest_quick(
        args["ticker"], args["from_date"], args["to_date"], args.get("step_days", 5)
    ),
    "get_macro_context": lambda _args: _tool_get_macro_context(),
}


async def _serve() -> None:
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server = Server("pulsetrader")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in _TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        args = arguments or {}
        if name not in _DISPATCH:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = _DISPATCH[name](args)
            return [types.TextContent(type="text", text=result)]
        except Exception as exc:
            return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=False)


def main() -> None:
    import asyncio
    _check_mcp()
    _setup_db()
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
