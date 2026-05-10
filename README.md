# PulseTrader

**Educational** AI-powered stock and crypto analysis engine. Not financial advice.

![Educational Use Only](https://img.shields.io/badge/Educational-Use%20Only-amber)
![Python 3.11](https://img.shields.io/badge/Python-3.11-blue)
![Tests](https://img.shields.io/badge/Tests-296%20passing-green)
![Coverage](https://img.shields.io/badge/Coverage-80%25-yellowgreen)

---

## What it does

Given any stock or crypto ticker, PulseTrader:

1. **Pulls multi-source data** — yfinance (stocks), Binance (crypto OHLCV), CoinGecko (crypto search), NewsAPI + RSS + yfinance (news), FRED (VIX, yields, CPI)
2. **Computes a deep indicator set** — 20+ technical indicators (RSI, MACD, ADX, Stochastic, Ichimoku, OBV, MFI, Keltner Channels) + statistical signals (Hurst exponent, z-score, realized volatility, autocorrelation)
3. **Runs AI analysis** — Claude-powered deep-dive and news theme extraction with daily token budget guardrails
4. **Produces a Pulse Score (0-100)** — transparent 7-component decomposition (trend 25%, momentum 20%, volatility 10%, volume 10%, sentiment 15%, multi-timeframe 10%, AI 10%)
5. **Backtests signal quality** — historical replay with no-lookahead bias, directional accuracy, Sharpe ratio, calibration curves
6. **Exposes everything via REST API** — 25+ endpoints with OpenAPI docs at `/docs`
7. **Runs as an MCP server** — Claude Desktop can query it directly from conversation

---

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 20+ (for the frontend)

### Backend

```bash
cd backend
pip install -e ".[dev]"
cp .env.example .env        # fill in API keys (Anthropic optional, FRED optional)
uvicorn backend.app.main:app --reload --port 8000
```

API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

### Frontend (minimal)

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

### Docker

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

### CLI — Backtest

```bash
pip install -e ".[dev]"
pulse backtest --ticker AAPL --from 2022-01-01 --to 2024-12-31
pulse backtest --ticker BTC-USD --from 2023-01-01 --to 2024-12-31 --step 5 --output markdown
```

### MCP Server (Claude Desktop)

```bash
pip install -e ".[mcp]"
# Add to claude_desktop_config.json — see backend/mcp_server.py for setup instructions
pulse-mcp
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/assets/{ticker}` | Full snapshot (price + indicators + score + news) |
| `GET` | `/assets/{ticker}/quote` | Real-time quote |
| `GET` | `/assets/{ticker}/ohlcv` | Historical OHLCV bars |
| `GET` | `/assets/{ticker}/indicators` | 20+ technical indicators with interpretations |
| `GET` | `/assets/{ticker}/indicators/multi-timeframe` | Cross-timeframe agreement (1h/1D/1W) |
| `GET` | `/assets/{ticker}/pulse-score` | 7-component Pulse Score with reasoning trace |
| `GET` | `/assets/{ticker}/pulse-score/weights` | Active formula weights (from TOML config) |
| `GET` | `/assets/{ticker}/news` | Enriched news with rolling sentiment windows |
| `POST` | `/assets/{ticker}/news/interpret` | AI-powered theme extraction |
| `POST` | `/assets/{ticker}/deep-dive` | Claude AI deep-dive analysis |
| `GET` | `/macro/snapshot` | VIX, Fed Funds Rate, Treasury yields, CPI (FRED) |
| `POST` | `/backtest` | Queue a historical backtest |
| `GET` | `/backtest/{job_id}` | Fetch backtest results |
| `GET` | `/admin/usage` | Token costs with daily/monthly breakdown |
| `GET` | `/admin/budget` | Current spend vs daily cap |
| `GET` | `/admin/requests` | External API call latency and circuit breaker states |
| `GET` | `/admin/jobs` | Scheduler job run history |

---

## Pulse Score Formula

All weights are configurable via `backend/config/score_weights.toml` — no code changes needed.

```
PulseScore = 0.25×Trend + 0.20×Momentum + 0.10×Volatility + 0.10×Volume
           + 0.15×Sentiment + 0.10×MultiTimeframe + 0.10×AI

Trend       = 0.40×MA_alignment + 0.35×MACD + 0.25×Ichimoku
Momentum    = 0.35×RSI + 0.25×Stochastic + 0.15×Williams_R + 0.25×Price_returns
Volatility  = 0.60×BB_percent_b + 0.40×Keltner_position
Volume      = 0.50×Volume_ratio + 0.50×MFI
```

Every score includes `sub_scores` and `data_sources` for full transparency.

---

## Backtesting

```bash
pulse backtest --ticker AAPL --from 2022-01-01 --to 2024-12-31 --output markdown
```

**Output includes:**
- Directional accuracy at 1d/7d/30d (does sign(score−50) predict return direction?)
- Sharpe ratio of naive long/short strategy (long when score > 50)
- Max drawdown
- Calibration table: score buckets vs realized returns

**No-lookahead guarantee:** Every score at date T uses only bars with `timestamp ≤ T`. Enforced by an explicit assertion in `_bars_up_to()` — violations raise loudly.

---

## Architecture

```
backend/
├── app/
│   ├── api/              # FastAPI routers (20+ endpoints)
│   ├── models/           # Pydantic schemas + SQLAlchemy ORM
│   ├── services/
│   │   ├── indicators.py        # 20+ indicators + multi-timeframe
│   │   ├── pulse_score.py       # 7-component scoring engine
│   │   ├── sentiment.py         # VADER / FinBERT / Claude
│   │   ├── backtester.py        # No-lookahead historical replay
│   │   ├── ai_client.py         # Anthropic SDK + budget guardrails
│   │   ├── budget.py            # Daily token spend cap
│   │   ├── rate_limiter.py      # Token buckets per data source
│   │   ├── circuit_breaker.py   # 3-state machine per data source
│   │   ├── observability.py     # Latency + success logging to SQLite
│   │   └── sources/
│   │       ├── binance.py       # Crypto OHLCV (no key)
│   │       ├── fred.py          # Macro data (VIX, yields, CPI)
│   │       └── rss.py           # News via feedparser (no key)
│   ├── config.py         # pydantic-settings (all env vars)
│   ├── scheduler.py      # 4 APScheduler jobs with run logging
│   └── main.py           # FastAPI factory + request middleware
├── config/
│   └── score_weights.toml        # Tunable formula weights
├── prompts/
│   ├── deep_dive.txt             # AI deep-dive prompt template
│   ├── ai_score.txt              # Score prompt template
│   └── news_interpreter.txt      # News theme extraction template
├── tests/                        # 296 tests, 80% coverage
├── cli.py                        # pulse backtest CLI
└── mcp_server.py                 # MCP server for Claude Desktop
```

---

## Environment Variables

```bash
# Required for AI features (optional — VADER is used without it)
ANTHROPIC_API_KEY=

# Optional data sources (all free tiers)
NEWS_API_KEY=            # newsapi.org — 100 req/day
COINGECKO_DEMO_KEY=      # coingecko.com — better rate limits
FRED_API_KEY=            # fred.stlouisfed.org — macro data

# AI cost guardrail (set to 0 to disable)
DAILY_TOKEN_BUDGET_USD=5.00

# Sentiment engine: vader (default) | finbert | claude
SENTIMENT_ENGINE=vader

# Scoring formula version (use TOML file to change weights)
# See backend/config/score_weights.toml

# Database (SQLite for dev, Postgres-ready)
DATABASE_URL=sqlite:///./pulsetrader.db
```

---

## Running Tests

```bash
cd backend
pytest --cov=app --cov-report=term-missing -v
# 296 tests, 80% coverage
```

---

## Disclaimer

**This tool is for educational purposes only.** PulseTrader is not a financial advisor. Scores, indicators, and AI analysis should not be used to make real investment decisions. Always do your own research and consult a licensed financial advisor before investing.
