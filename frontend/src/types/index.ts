// Shared TypeScript types — mirrors backend Pydantic schemas exactly.
// If you add a field to a backend schema, add it here too.

export interface OHLCVBar {
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface Quote {
  ticker: string
  name: string
  price: number
  change_1d: number
  change_1d_pct: number
  volume: number
  market_cap: number | null
  asset_type: "stock" | "crypto"
  currency: string
  last_updated: string
}

export interface IndicatorValue {
  value: number | null
  interpretation: string
}

export interface IndicatorSnapshot {
  ticker: string
  timestamp: string
  price: number
  sma_20: IndicatorValue
  sma_50: IndicatorValue
  sma_200: IndicatorValue
  ema_12: IndicatorValue
  ema_26: IndicatorValue
  rsi_14: IndicatorValue
  macd_line: IndicatorValue
  macd_signal: IndicatorValue
  macd_histogram: IndicatorValue
  bb_upper: IndicatorValue
  bb_middle: IndicatorValue
  bb_lower: IndicatorValue
  bb_percent_b: IndicatorValue
  volume_sma_20: IndicatorValue
  volume_ratio: IndicatorValue
}

// Convenience aliases used by components
export type { IndicatorSnapshot as Indicators }

export interface NewsItem {
  title: string
  url: string
  publisher: string   // source name
  published_at: string
  summary: string
  sentiment_score: number | null
  sentiment_label: string
}

export interface SentimentWindow {
  window: string
  item_count: number
  compound: number
  weighted_compound: number
  label: string
}

export interface SentimentMomentum {
  direction: "rising" | "falling" | "stable"
  delta: number
  detail: string
}

export interface NewsFeed {
  ticker: string
  items: NewsItem[]
  rolling_sentiment: number
  sentiment_label: string
}

export interface EnrichedNewsFeed extends NewsFeed {
  windows: Record<string, SentimentWindow>
  momentum: SentimentMomentum | null
  source_weighted_sentiment: number
  top_entities: string[]
  engines_used: string[]
}

export interface ScoreComponent {
  score: number
  weight: number
  weighted_contribution: number
  detail: string
}

export interface PulseScoreBreakdown {
  ticker: string
  overall: number           // 0-100
  overall_score?: number    // alias for convenience
  label: string
  scoring_version: string
  trend: ScoreComponent
  momentum: ScoreComponent
  volatility: ScoreComponent
  volume: ScoreComponent
  sentiment: ScoreComponent
  multi_timeframe: ScoreComponent
  ai: ScoreComponent
  computed_at: string
  data_freshness_seconds?: number | null
}

export interface MultiTimeframeSnapshot {
  ticker: string
  computed_at: string
  timeframes: Record<string, IndicatorSnapshot>
  directions: Record<string, number>
  agreement_score: number
  agreement_label: string
  consensus_direction: string
}

export interface AiAnalysis {
  ticker: string
  summary: string
  bullish_factors: string[]
  bearish_factors: string[]
  watch_for: string[]
  ai_score: number
  model_used: string
  cached: boolean
  generated_at: string
  input_tokens: number | null
  output_tokens: number | null
}

export interface WatchlistItem {
  id: number
  ticker: string
  name: string
  asset_type: string
  added_at: string
  price: number | null
  change_1d_pct: number | null
  pulse_score: number | null
}

export interface AlertRule {
  id: number
  ticker: string
  alert_type: "price_above" | "price_below" | "score_above" | "score_below"
  threshold: number
  notification_channels: string[]
  is_active: boolean
  triggered_at: string | null
  created_at: string
}

export interface SearchResult {
  ticker: string
  name: string
  type: string        // "stock" | "crypto"
  asset_type?: string // alias used by some components
  coin_id?: string
}

// UI helpers
export type Timeframe = "1d" | "5d" | "1mo" | "3mo" | "6mo" | "1y" | "2y" | "5y"
export type Interval = "5m" | "15m" | "30m" | "1h" | "1d" | "1wk" | "1mo"

export interface TimeframeOption {
  label: string
  period: string
  interval: Interval
}

export const TIMEFRAME_OPTIONS: TimeframeOption[] = [
  { label: "1D", period: "1d", interval: "5m" },
  { label: "1W", period: "5d", interval: "30m" },
  { label: "1M", period: "1mo", interval: "1d" },
  { label: "3M", period: "3mo", interval: "1d" },
  { label: "1Y", period: "1y", interval: "1d" },
  { label: "5Y", period: "5y", interval: "1wk" },
]

export const TIMEFRAMES = TIMEFRAME_OPTIONS
