# PRD — PulseTrader (MVP “very simple”)

## 0) One-liner
A lightweight web app that watches a small set of tickers, runs a few transparent technical signals, and sends clean, throttled alerts with one-tap “mini backtest” context.

---

## 1) Goals & Non-Goals
**Goals**
- Deliver reliable real-time (or near-real-time) alerts for a *few* indicators on a *small* watchlist.
- Keep alerts understandable: what fired, when, on which timeframe, with a chart snapshot.
- Provide a 1-tap “Backtest Lite” on the last 12 months with basic stats.
- Support push/email + Telegram/Discord webhooks.
- Ship fast with a single market-data vendor.

**Non-Goals (MVP)**
- No brokerage execution, options flow, ML models, or social feeds.
- No custom indicators or strategy marketplace.
- No intraday portfolio analytics or PnL tracking.
- No mobile app; web-only (mobile-responsive).

---

## 2) Target Users
- Retail traders who want clean alerts on liquid US stocks and major crypto pairs.
- Time-constrained users who prefer “few, high-quality pings” over dashboards.

**Primary Personas**
1) **Momentum Matt**: cares about breakouts + MACD cross + volume.  
2) **Mean-Rev Mina**: cares about RSI oversold + reversal confirmation.

---

## 3) User Stories (strict MVP)
- As a user, I can sign up and log in with email.
- I can add up to **10** symbols to a watchlist from search.
- I can pick a strategy preset (Momentum Swing or Mean Reversion).
- I can toggle alerts per symbol and per timeframe (Daily + 5m for crypto).
- I receive alerts via email or push; optionally Telegram/Discord via webhook URL.
- I can open an alert and see: trigger, timeframe, last price, quick chart, and a button for Backtest Lite.
- I can run Backtest Lite (12 months) and see hit rate, avg return, max drawdown, trade count.
- I can set quiet hours and a per-symbol cooldown to limit noise.

---

## 4) Top-Level Flows
**Onboarding**
1) Sign up → select preset (Momentum or Mean-Rev) → pick 3–10 tickers → choose alert channels → done.

**Daily Use**
- **Home:** Watchlist with status dots (“No signal”, “Near”, “Fired <Xm ago>”).
- **Alerts:** Reverse-chron list with filter by symbol/strategy.
- **Alert Detail:** Opens a card with chart + indicators + Backtest Lite button.

**Backtest Lite**
- Choose window (default 12m) → run → show stats + equity curve → close.

---

## 5) Scope of Signals (MVP)
**Timeframes**
- **Equities:** Daily  
- **Crypto:** 5m and Daily

**Indicators**
- RSI(14)  
- MACD(12,26,9)  
- SMA(20) and SMA(50)  
- 52-week breakout (equities daily only)  
- Volume surge: current volume > 1.5× 20-bar average

**Strategy Presets**
1) **Momentum Swing**  
   Trigger when **MACD line crosses above signal** AND **close > SMA20 AND close > SMA50** AND **volume surge**.  
   Cooldown: 60 minutes (crypto 5m) or 1 day (equities daily).

2) **Mean Reversion**  
   Trigger when **RSI(14) < 30** AND bar closes green above prior low.  
   Cooldown: 60 minutes (crypto 5m) or 1 day (equities daily).

**Noise Filters**
- Don’t refire for same symbol+timeframe+strategy during cooldown.
- Require indicator “hysteresis”: for crossovers, bar close must confirm cross.

---

## 6) Backtest Lite (MVP rules)
- **Data window:** default 12 months OHLCV for the selected timeframe.  
- **Entry:** next bar **open** after signal.  
- **Exit:**  
  - Momentum Swing: target = +2×ATR(14), stop = −1×ATR(14), or max hold 10 bars.  
  - Mean Reversion: target = +1.5×ATR(14), stop = −1×ATR(14), or max hold 7 bars.  
- **Costs:** slippage 5 bps, fee 1 bps (config defaults).  
- **Output:** trade count, hit rate, avg trade %, max drawdown %, simple equity curve.  
- **Guardrail:** suppress stats if trades < 20; show “insufficient sample”.

---

## 7) Success Metrics (MVP)
- **Activation:** ≥60% of new users create a watchlist and enable alerts within 24h.
- **Alert Quality:** ≥35% of alerts receive “useful” feedback (thumbs-up) over first week.
- **Latency:** crypto 5m alerts delivered <10s after bar close; equities daily <60s after EOD bar.
- **Retention:** Day-7 ≥25% of users with ≥3 alerts viewed or ≥1 backtest run.

---

## 8) Non-Functional Requirements
- Uptime target **99.5%**.
- Alert delivery retries (exponential backoff) up to **3** times.
- PII stored encrypted at rest; webhook secrets hashed.
- All backtests labeled **“hypothetical; not investment advice.”**

---

## 9) System Architecture (lean)
- **Frontend:** Next.js (React), minimal pages: Auth, Watchlist, Alerts, Settings, Backtest modal.  
- **Backend:** Serverless API (Node/TypeScript).  
  - **Workers:** polling cron (per timeframe) to fetch candles, compute indicators, evaluate rules, enqueue alerts.  
  - **Queue/Cache:** Redis for recent candles, cooldown keys, and alert dedupe.  
- **DB:** Postgres (Supabase) for users, symbols, subscriptions, alerts, backtests.  
- **Data Provider:** one vendor offering OHLCV for US equities + major crypto pairs.  
- **Notifications:** Email (Resend/SES), Web push (OneSignal), Telegram/Discord webhooks.

---

## 10) Data Model (initial)
**users**(id, email, plan, created_at)  
**symbols**(id, ticker, market ENUM[‘equity’, ‘crypto’], name, active)  
**watchlists**(id, user_id, name, created_at)  
**watchlist_symbols**(watchlist_id, symbol_id)  
**subscriptions**(id, user_id, symbol_id, strategy ENUM[‘momentum’, ‘meanrev’], tf ENUM[‘D’, ‘5m’], params JSONB, alerts_enabled BOOL)  
**alerts**(id, user_id, symbol_id, strategy, tf, fired_at, payload JSONB)  
**backtests**(id, user_id, symbol_id, strategy, tf, window_months INT, params JSONB, stats JSONB, created_at)

**payload JSONB (alert) example**
```json
{
  "trigger": "MACD_cross_up + SMA20/50 + vol_surge",
  "price": 142.35,
  "rsi": 54.2,
  "macd": {"line": 0.43, "signal": 0.39, "hist": 0.04},
  "sma20": 139.1,
  "sma50": 136.7,
  "volume": 18200000,
  "vol_avg20": 11900000,
  "chart": {"spark": [141.9,142.0,141.8,142.3], "tf":"D"}
}
```

**stats JSONB (backtest) example**
```json
{
  "trades": 42,
  "hit_rate": 0.52,
  "avg_return_pct": 0.9,
  "max_dd_pct": -6.8,
  "equity_curve": [1.0,1.01,1.00,1.02,1.03]
}
```

---

## 11) External Interfaces

### Candle Ingest (read)
`GET /vendor/candles?symbol=TSLA&tf=D&limit=300`  
Response:  
`[ { "t": 1715731200, "o":..., "h":..., "l":..., "c":..., "v":... }, ... ]`

### Indicator Compute (internal lib)
- `rsi(close[], period=14) -> float[]`  
- `macd(close[], fast=12, slow=26, signal=9) -> {macd[], signal[], hist[]}`  
- `sma(close[], n) -> float[]`  
- `atr(high[], low[], close[], n=14) -> float[]`

### Alert Engine (worker)
Input: recent candles for (symbol, tf)  
Steps: compute indicators → evaluate rules → check cooldown key `cooldown:{user}:{symbol}:{tf}:{strategy}` → if pass, create `alerts` row → fan-out notifications.

### Notifications
- **Email:** Resend API with template `alert_basic`  
- **Push:** OneSignal create notification  
- **Webhook (Telegram/Discord):** POST JSON `{text: "...", link: alert_url}`

---

## 12) REST API (app backend)

**Auth**
- `POST /auth/signup` `{email, password}`  
- `POST /auth/login` `{email, password}`

**Symbols**
- `GET /symbols?q=aapl` → list for search

**Watchlist**
- `GET /watchlist`  
- `POST /watchlist` `{name}`  
- `POST /watchlist/{id}/symbols` `{symbol_id}`  
- `DELETE /watchlist/{id}/symbols/{symbol_id}`

**Subscriptions**
- `GET /subs`  
- `POST /subs` `{symbol_id, strategy, tf, alerts_enabled=true}`  
- `PATCH /subs/{id}` `{alerts_enabled, params}`  
- `DELETE /subs/{id}`

**Alerts**
- `GET /alerts?symbol_id=&strategy=&since=`  
- `GET /alerts/{id}`

**Backtest**
- `POST /backtest` `{symbol_id, strategy, tf, window_months=12, params?}` → returns `stats JSONB`

**Settings**
- `GET /settings`  
- `PATCH /settings` `{quiet_hours, channels: {email, push, telegram_webhook, discord_webhook}, cooldown_minutes:{crypto5m:60, daily:1440}}`

---

## 13) Acceptance Criteria

**Alerts**
- When MACD crosses up and price > SMA20 & SMA50 and volume surge on the evaluated bar, an alert is created once and not again within the cooldown window.  
- An alert contains: symbol, timeframe, fired_at (UTC), trigger string, key indicator values, last price, and sparkline array.

**Backtest**
- Given 12 months of candles and default params, returns trade count, hit rate, avg return %, max drawdown %, equity curve length ≥ trades+1.  
- Backtest does not run if trades < 20; returns 400 with message “insufficient sample”.

**Latency**
- Crypto 5m: worker evaluates within 5s of bar close and notifications are sent within 10s.  
- Equities daily: alert sent within 60s of final daily bar availability.

**UX**
- User can add symbols, enable alerts, and receive at least one test alert within 10 minutes using a crypto 5m pair.

---

## 14) Test Cases (high-value)

**Unit**
- RSI, SMA, MACD, ATR functions match known reference vectors.  
- Crossover detection uses *close* values and enforces hysteresis by bar close.

**Integration**
- Simulate 100 bars where MACD crosses up exactly once → expect exactly 1 alert then cooldown holds.  
- Volume surge off by one (1.49× vs 1.5×) → no alert.  
- Backtest with mocked candles producing 25 trades → stats computed; with 12 trades → error “insufficient sample.”

**E2E**
- Create user → add BTCUSDT 5m with Momentum → receive alert in Telegram webhook when synthetic trigger is injected.

---

## 15) UX Notes (wireframe-level)
**Home / Watchlist**
- Row per symbol: last price, % change, status pill: “No signal”, “Near”, or “Fired 9m ago”.  
- Toggle per symbol: Momentum | Mean-Rev | Both (checkboxes).

**Alert Detail Modal**
- Title: “AAPL • Momentum Swing • Daily”  
- Subtitle: “MACD cross up + SMA20/50 + vol surge • 2025-10-18 14:35 UTC”  
- Sparkline + last 30 bars mini chart.  
- Buttons: “Run Backtest”, “Share to Telegram”.

**Backtest Modal**
- Stats grid (Trades, Hit Rate, Avg %, Max DD).  
- Simple equity line chart.  
- “Assumptions” footnote and “Not financial advice”.

---

## 16) Security & Compliance
- Store webhook URLs encrypted; hash secrets if tokens.  
- Rate-limit all public endpoints (per-IP and per-user).  
- Log all alert sends with status; no PII in logs.  
- Prominent disclaimers in onboarding and backtest modal.

---

## 17) Rollout Plan
- **Beta:** crypto only (BTC, ETH, SOL) on 5m; equities daily for 10 tickers.  
- **Payment:** disabled in MVP; add later.  
- **Feedback:** thumbs up/down on each alert with optional note.

---

## 18) Open Questions
- Pick vendor: preference for one API covering both equities + crypto; if not possible, start crypto-only.  
- Where to host chart snapshot? Option A: client-side sparkline; skip image generation in MVP.  
- Add “Near” status heuristic? e.g., MACD delta within 10% of zero, RSI within 2 pts of 30.

---

## 19) Engineering Notes for AI Agent
- Implement indicators as pure functions with arrays.  
- Use Redis keys for cooldown: `cooldown:{user}:{symbol}:{tf}:{strategy}` with TTL = cooldown minutes.  
- Poll cadence:  
  - Crypto 5m: poll every 5s; fire at bar close timestamps aligned to vendor time.  
  - Equities daily: poll once after vendor posts final daily bar (set cron at 21:05–21:10 UTC, configurable).  
- Use idempotency key for alert creation: hash of `{user_id,symbol_id,tf,strategy,fired_bar_time}`.

---

## 20) Definition of Done (MVP)
- A new user can: sign up → add BTCUSDT → enable Momentum 5m → receive at least one real alert in webhook or email within 24h.  
- Backtest Lite runs and returns coherent stats for any symbol/timeframe with enough data.  
- Latency SLOs met in a 48-hour smoke test.  
- Basic analytics (PostHog) capture: onboarding completion, first alert sent, first backtest run.  
- All acceptance tests above pass in CI.
