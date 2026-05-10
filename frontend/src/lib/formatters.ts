/**  Formatting utilities — numbers, prices, percentages, dates. */

export function formatPrice(price: number, currency = "USD"): string {
  if (price >= 1000) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(price)
  }
  if (price >= 1) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    }).format(price)
  }
  // Small crypto prices
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 4,
    maximumFractionDigits: 8,
  }).format(price)
}

export function formatPct(value: number, decimals = 2): string {
  const sign = value >= 0 ? "+" : ""
  return `${sign}${value.toFixed(decimals)}%`
}

export function formatLargeNumber(value: number): string {
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`
  if (value >= 1e3) return `$${(value / 1e3).toFixed(2)}K`
  return `$${value.toFixed(2)}`
}

export function formatVolume(value: number): string {
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`
  if (value >= 1e6) return `${(value / 1e6).toFixed(2)}M`
  if (value >= 1e3) return `${(value / 1e3).toFixed(2)}K`
  return value.toFixed(0)
}

export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso))
}

export function formatDateShort(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
  }).format(new Date(iso))
}

export function scoreColor(score: number): string {
  if (score >= 75) return "text-emerald-400"
  if (score >= 60) return "text-green-400"
  if (score >= 45) return "text-yellow-400"
  if (score >= 30) return "text-orange-400"
  return "text-red-400"
}

export function scoreBgColor(score: number): string {
  if (score >= 75) return "bg-emerald-500"
  if (score >= 60) return "bg-green-500"
  if (score >= 45) return "bg-yellow-500"
  if (score >= 30) return "bg-orange-500"
  return "bg-red-500"
}

export function sentimentColor(score: number): string {
  if (score >= 0.35) return "text-emerald-400"
  if (score >= 0.05) return "text-green-400"
  if (score > -0.05) return "text-zinc-400"
  if (score > -0.35) return "text-orange-400"
  return "text-red-400"
}

export function changeColor(pct: number): string {
  return pct >= 0 ? "text-emerald-400" : "text-red-400"
}
