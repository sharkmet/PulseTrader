"use client"

import useSWR from "swr"
import { getOHLCV, getQuote, getIndicators } from "@/lib/api"

const POLL_INTERVAL = 30_000 // 30 seconds

export function useQuote(ticker: string | null) {
  return useSWR(
    ticker ? `quote-${ticker}` : null,
    () => getQuote(ticker!),
    { refreshInterval: POLL_INTERVAL }
  )
}

export function useOHLCV(ticker: string | null, period = "3mo", interval = "1d") {
  return useSWR(
    ticker ? `ohlcv-${ticker}-${period}-${interval}` : null,
    () => getOHLCV(ticker!, period, interval),
    { refreshInterval: 60_000 }
  )
}

export function useIndicators(ticker: string | null, period = "6mo") {
  return useSWR(
    ticker ? `indicators-${ticker}-${period}` : null,
    () => getIndicators(ticker!, period),
    { refreshInterval: 60_000 }
  )
}
