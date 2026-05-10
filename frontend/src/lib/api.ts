/**
 * Typed API client — all backend calls go through here.
 * Base URL is read from NEXT_PUBLIC_API_URL env var (defaults to localhost:8000).
 */

import type {
  AiAnalysis,
  AlertRule,
  EnrichedNewsFeed,
  IndicatorSnapshot,
  MultiTimeframeSnapshot,
  OHLCVBar,
  PulseScoreBreakdown,
  Quote,
  SearchResult,
  WatchlistItem,
} from "@/types"

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ── Full snapshot ─────────────────────────────────────────────────────────────

export const getAssetSnapshot = (
  ticker: string,
  opts: { includeNews?: boolean; includeMtf?: boolean; includeMacro?: boolean } = {}
) => {
  const { includeNews = true, includeMtf = false, includeMacro = false } = opts
  const params = new URLSearchParams({
    include_news: String(includeNews),
    include_mtf: String(includeMtf),
    include_macro: String(includeMacro),
  })
  return request<Record<string, unknown>>(`/assets/${ticker}?${params}`)
}

// ── Assets ────────────────────────────────────────────────────────────────────

export const searchAssets = (q: string) =>
  request<SearchResult[]>(`/assets/search?q=${encodeURIComponent(q)}`)

export const getQuote = (ticker: string) =>
  request<Quote>(`/assets/${ticker}/quote`)

export const getOHLCV = (ticker: string, period = "3mo", interval = "1d") =>
  request<OHLCVBar[]>(`/assets/${ticker}/ohlcv?period=${period}&interval=${interval}`)

export const getIndicators = (ticker: string, period = "6mo") =>
  request<IndicatorSnapshot>(`/assets/${ticker}/indicators?period=${period}`)

export const getMultiTimeframe = (ticker: string) =>
  request<MultiTimeframeSnapshot>(`/assets/${ticker}/indicators/multi-timeframe`)

// ── Score ─────────────────────────────────────────────────────────────────────

export const getPulseScore = (
  ticker: string,
  opts: { includeAi?: boolean; includeMtf?: boolean } = {}
) => {
  const { includeAi = false, includeMtf = false } = opts
  return request<PulseScoreBreakdown>(
    `/assets/${ticker}/pulse-score?include_ai=${includeAi}&include_mtf=${includeMtf}`
  )
}

// ── News ──────────────────────────────────────────────────────────────────────

export const getNewsFeed = (ticker: string) =>
  request<EnrichedNewsFeed>(`/assets/${ticker}/news`)

export const interpretNews = (ticker: string) =>
  request<Record<string, unknown>>(`/assets/${ticker}/news/interpret`, { method: "POST" })

// ── AI ────────────────────────────────────────────────────────────────────────

export const getAiAnalysis = (ticker: string) =>
  request<AiAnalysis>(`/assets/${ticker}/deep-dive`, { method: "POST" })

// ── Macro ─────────────────────────────────────────────────────────────────────

export const getMacroSnapshot = () =>
  request<Record<string, unknown>>("/macro/snapshot")

export const getMacroContext = () =>
  request<{ context: string }>("/macro/context")

// ── Backtest ──────────────────────────────────────────────────────────────────

export const startBacktest = (payload: {
  ticker: string
  from_date: string
  to_date: string
  step_days?: number
}) =>
  request<{ job_id: string; status: string }>("/backtest", {
    method: "POST",
    body: JSON.stringify(payload),
  })

export const getBacktest = (jobId: string) =>
  request<Record<string, unknown>>(`/backtest/${jobId}`)

export const listBacktests = (limit = 20) =>
  request<Record<string, unknown>[]>(`/backtest?limit=${limit}`)

// ── Watchlist ─────────────────────────────────────────────────────────────────

export const getWatchlist = () => request<WatchlistItem[]>("/watchlist/")

export const addToWatchlist = (ticker: string, name = "", assetType = "stock") =>
  request<WatchlistItem>("/watchlist/", {
    method: "POST",
    body: JSON.stringify({ ticker, name, asset_type: assetType }),
  })

export const removeFromWatchlist = (ticker: string) =>
  request<void>(`/watchlist/${ticker}`, { method: "DELETE" })

// ── Alerts ────────────────────────────────────────────────────────────────────

export const getAlerts = () => request<AlertRule[]>("/alerts/")

export const createAlert = (payload: {
  ticker: string
  alert_type: AlertRule["alert_type"]
  threshold: number
  notification_channels?: string[]
}) =>
  request<AlertRule>("/alerts/", {
    method: "POST",
    body: JSON.stringify({
      ...payload,
      notification_channels: payload.notification_channels ?? ["browser"],
    }),
  })

export const deleteAlert = (id: number) =>
  request<void>(`/alerts/${id}`, { method: "DELETE" })

export const toggleAlert = (id: number) =>
  request<AlertRule>(`/alerts/${id}/toggle`, { method: "PATCH" })

// ── Admin ─────────────────────────────────────────────────────────────────────

export const getUsageReport = () =>
  request<Record<string, unknown>>("/admin/usage")

export const getBudgetStatus = () =>
  request<{ daily_budget_usd: number; today_spent_usd: number; exceeded: boolean }>(
    "/admin/budget"
  )
