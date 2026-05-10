"use client"
import useSWR from "swr"
import { getAlerts } from "@/lib/api"

export function useAlerts() {
  return useSWR("alerts", getAlerts, { refreshInterval: 30_000 })
}
