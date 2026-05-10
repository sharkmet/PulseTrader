"use client"
import "./globals.css"
import { useState, useEffect, useRef } from "react"
import useSWR from "swr"

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
const fetcher = (url: string) => fetch(BASE + url).then(r => r.json())

// ── Colour helpers ────────────────────────────────────────────────────────────
function pulseColor(score: number) {
  if (score >= 75) return "var(--up)"
  if (score >= 55) return "#9adf6d"
  if (score >= 40) return "var(--warn)"
  if (score >= 25) return "#f08a4a"
  return "var(--down)"
}
function changeColor(n: number) { return n > 0 ? "var(--up)" : n < 0 ? "var(--down)" : "var(--muted)" }
function fmtPct(n: number) { return (n > 0 ? "+" : "") + n.toFixed(2) + "%" }
function fmtPrice(n: number) { return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toFixed(2) }
function fmtLarge(n: number) {
  if (!n) return "—"
  if (n >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T"
  if (n >= 1e9)  return "$" + (n / 1e9).toFixed(2) + "B"
  if (n >= 1e6)  return "$" + (n / 1e6).toFixed(2) + "M"
  return "$" + n.toLocaleString()
}
function formatTime(d: Date) {
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
}
function sessionCountdown(now: Date) {
  const close = new Date(now); close.setHours(16, 0, 0, 0)
  const open  = new Date(now); open.setHours(9, 30, 0, 0)
  let label: string, ms: number
  if (now < open)  { label = "Opens in";  ms = open.getTime() - now.getTime() }
  else if (now < close) { label = "Closes in"; ms = close.getTime() - now.getTime() }
  else { const next = new Date(open); next.setDate(next.getDate() + 1); label = "Opens in"; ms = next.getTime() - now.getTime() }
  const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000)
  return { label, val: h + "h " + String(m).padStart(2, "0") + "m" }
}
function pulseLabel(score: number) {
  if (score >= 75) return "Strong"
  if (score >= 55) return "Constructive"
  if (score >= 40) return "Neutral"
  if (score >= 25) return "Cautious"
  return "Bearish"
}

// ── Atoms ─────────────────────────────────────────────────────────────────────
function Sparkline({ data, w = 60, h = 18, fill }: { data: number[], w?: number, h?: number, fill?: boolean }) {
  if (!data || data.length < 2) return null
  const min = Math.min(...data), max = Math.max(...data)
  const range = max - min || 1
  const stepX = w / (data.length - 1)
  const pts = data.map((v, i) => [i * stepX, h - ((v - min) / range) * (h - 2) - 1] as [number, number])
  const d = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ")
  const fillD = d + " L" + w + "," + h + " L0," + h + " Z"
  const trendUp = data[data.length - 1] >= data[0]
  const stroke = trendUp ? "var(--up)" : "var(--down)"
  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      {fill && <path d={fillD} fill={stroke} opacity="0.12" />}
      <path d={d} fill="none" stroke={stroke} strokeWidth="1.2" />
    </svg>
  )
}

function PulseGauge({ score, size = 44, thick = 3 }: { score: number, size?: number, thick?: number }) {
  const r = size / 2 - thick
  const c = 2 * Math.PI * r
  const p = (score / 100) * c
  const color = pulseColor(score)
  return (
    <div style={{ position: "relative", width: size, height: size, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
      <svg width={size} height={size} style={{ position: "absolute", inset: 0, transform: "rotate(-90deg)" }}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={thick} />
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth={thick} strokeLinecap="butt"
          strokeDasharray={p + " " + c} style={{ transition: "stroke-dasharray 400ms" }} />
      </svg>
      <span className="mono" style={{ position: "relative", fontSize: size > 60 ? 18 : 11, fontWeight: 700, color }}>
        {Math.round(score)}
      </span>
    </div>
  )
}

function PulseBar({ score, w = 60, h = 4 }: { score: number, w?: number | string, h?: number }) {
  const isStr = typeof w === "string"
  return (
    <div style={{ width: isStr ? w : w + "px", height: h, background: "var(--surface-3)", borderRadius: 1, overflow: "hidden" }}>
      <div style={{ width: score + "%", height: "100%", background: pulseColor(score), transition: "width 400ms" }} />
    </div>
  )
}

function Chip({ tone, children }: { tone: string, children: React.ReactNode }) {
  const map: Record<string, { bg: string, color: string }> = {
    up:      { bg: "rgba(61,220,151,0.12)",  color: "var(--up)" },
    down:    { bg: "rgba(255,93,114,0.12)",   color: "var(--down)" },
    warn:    { bg: "rgba(245,185,66,0.12)",   color: "var(--warn)" },
    accent:  { bg: "var(--accent-dim)",        color: "var(--accent)" },
    neutral: { bg: "var(--surface-3)",         color: "var(--muted)" },
  }
  const s = map[tone] || map.neutral
  return <span className="chip" style={{ background: s.bg, color: s.color }}>{children}</span>
}

function SectionTitle({ children, right }: { children: React.ReactNode, right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "9px 14px", borderBottom: "1px solid var(--border)", background: "var(--surface-1)", minHeight: 34 }}>
      <span className="uppercase-sm" style={{ color: "var(--text)", opacity: 0.85 }}>{children}</span>
      {right}
    </div>
  )
}

function SentimentBar({ score, w = 80, h = 6 }: { score: number, w?: number | string, h?: number }) {
  const isStr = typeof w === "string"
  const containerW = isStr ? w : w + "px"
  const absPct = Math.min(1, Math.abs(score)) * 50
  const color = score >= 0 ? "var(--up)" : "var(--down)"
  return (
    <div style={{ width: containerW, height: h, background: "var(--surface-3)", borderRadius: 1, position: "relative", overflow: "hidden" }}>
      <div style={{ position: "absolute", left: "calc(50% - 0.5px)", top: 0, bottom: 0, width: 1, background: "var(--dim)", zIndex: 1 }} />
      <div style={{
        position: "absolute", top: 0, bottom: 0,
        left: score >= 0 ? "50%" : (50 - absPct) + "%",
        width: absPct + "%", background: color, opacity: 0.75,
      }} />
    </div>
  )
}

// ── Status bar ────────────────────────────────────────────────────────────────
function StatusBar({ time, watchlist, alerts, onOpenAdd, addOpen, onHome }: {
  time: string, watchlist: any[], alerts: any[], onOpenAdd: () => void, addOpen: boolean, onHome: () => void
}) {
  const breadth = { up: 0, down: 0 }
  watchlist.forEach(w => { if ((w.change_1d_pct ?? 0) >= 0) breadth.up++; else breadth.down++ })
  const triggered = alerts.filter(a => !a.is_active && a.triggered_at).length
  const session = sessionCountdown(new Date())
  const now = new Date()
  const marketOpen = now.getHours() >= 9 && (now.getHours() < 16 || (now.getHours() === 9 && now.getMinutes() >= 30))

  return (
    <div style={{ height: 30, display: "flex", alignItems: "center", borderBottom: "1px solid var(--border)", background: "var(--bg)", padding: "0 12px", fontSize: 11, gap: 14 }}>
      <button onClick={onHome} style={{ display: "flex", alignItems: "center", gap: 10, paddingRight: 18, borderRight: "1px solid var(--border)", height: "100%", background: "transparent", border: "none", borderRight: "1px solid var(--border)", cursor: "pointer" }}>
        <svg width="15" height="15" viewBox="0 0 14 14">
          <path d="M0 7 L3 7 L4 2 L6 12 L8 4 L10 9 L14 9" stroke="var(--accent)" strokeWidth="1.4" fill="none" strokeLinejoin="round" strokeLinecap="round" />
        </svg>
        <span style={{ fontWeight: 600, fontSize: 12.5, letterSpacing: "-0.01em", color: "var(--text)" }}>
          Pulse<span style={{ color: "var(--accent)" }}>·</span>Trader
        </span>
      </button>

      <div style={{ display: "flex", gap: 4, height: "100%", alignItems: "center" }}>
        <span style={{ padding: "4px 10px", fontSize: 11.5, fontWeight: 600, borderBottom: "1.5px solid var(--accent)", marginBottom: -1, height: "100%", display: "flex", alignItems: "center" }}>
          Markets
        </span>
      </div>

      <div style={{ flex: 1 }} />

      <div style={{ display: "flex", alignItems: "center", gap: 18, color: "var(--muted)" }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span className="live-dot" />
          <span style={{ fontSize: 11, color: marketOpen ? "var(--up)" : "var(--muted)", fontWeight: 500 }}>
            {marketOpen ? "Markets open" : "Markets closed"}
          </span>
          <span style={{ fontSize: 10.5, color: "var(--dim)" }}>·</span>
          <span style={{ fontSize: 10.5, color: "var(--dim)" }}>{session.label}</span>
          <span className="mono" style={{ fontSize: 11, color: "var(--text)", fontWeight: 500 }}>{session.val}</span>
        </span>

        <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11 }}>
          <span className="mono" style={{ color: "var(--up)", fontWeight: 600 }}>▲ {breadth.up}</span>
          <span className="mono" style={{ color: "var(--down)", fontWeight: 600 }}>▼ {breadth.down}</span>
        </span>

        <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11 }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: triggered ? "var(--warn)" : "var(--dim)" }} />
          <span style={{ color: triggered ? "var(--warn)" : "var(--dim)", fontWeight: 500 }}>
            {triggered} alert{triggered === 1 ? "" : "s"}
          </span>
        </span>

        <span className="mono" style={{ fontSize: 11.5, fontWeight: 500 }}>{time}</span>

        <button onClick={onOpenAdd} style={{
          display: "flex", alignItems: "center", gap: 5, padding: "3px 9px",
          background: addOpen ? "var(--accent)" : "var(--accent-dim)",
          color: addOpen ? "var(--bg)" : "var(--accent)", border: "none",
          fontSize: 11, fontWeight: 600, letterSpacing: "0.02em",
        }}>+ Watchlist</button>
      </div>
    </div>
  )
}

// ── Ticker tape ───────────────────────────────────────────────────────────────
function TickerTape({ watchlist }: { watchlist: any[] }) {
  const indices = [
    { sym: "S&P 500 (SPY)", ticker: "SPY" },
    { sym: "NASDAQ (QQQ)",  ticker: "QQQ" },
    { sym: "DOW (DIA)",     ticker: "DIA" },
    { sym: "RUSSELL (IWM)", ticker: "IWM" },
  ]
  // Pull from watchlist if present, else show loading
  const tapeItems = [
    ...indices.map(idx => {
      const item = watchlist.find(w => w.ticker === idx.ticker)
      return item ? { sym: idx.sym, val: item.price, ch: item.change_1d_pct ?? 0 } : { sym: idx.sym, val: null, ch: 0 }
    }),
    ...watchlist.map(w => ({ sym: w.ticker.replace("-USD",""), val: w.price, ch: w.change_1d_pct ?? 0 }))
  ]
  const reps = [...tapeItems, ...tapeItems]
  return (
    <div style={{ height: 26, borderBottom: "1px solid var(--border)", background: "var(--surface-1)", overflow: "hidden" }}>
      <div className="tape" style={{ height: "100%", alignItems: "center", display: "flex", width: "max-content" }}>
        {reps.map((idx, i) => (
          <div key={i} style={{ padding: "0 18px", display: "flex", gap: 8, alignItems: "center", borderRight: "1px solid var(--border-soft)", height: "100%" }}>
            <span className="uppercase-sm">{idx.sym}</span>
            {idx.val != null ? (
              <>
                <span className="mono" style={{ fontSize: 11, fontWeight: 600 }}>
                  {idx.val >= 1000 ? idx.val.toLocaleString(undefined, { maximumFractionDigits: 2 }) : idx.val.toFixed(2)}
                </span>
                <span className="mono" style={{ fontSize: 11, fontWeight: 600, color: changeColor(idx.ch) }}>
                  {fmtPct(idx.ch)}
                </span>
              </>
            ) : <span style={{ color: "var(--dim)", fontSize: 10 }}>—</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Watchlist panel ───────────────────────────────────────────────────────────
function WatchlistPanel({ items, selected, onSelect, sortKey, sortDesc, onSortChange, query, setQuery }: {
  items: any[], selected: string | null, onSelect: (t: string) => void,
  sortKey: string, sortDesc: boolean, onSortChange: (k: string) => void,
  query: string, setQuery: (q: string) => void
}) {
  const filtered = items.filter(i =>
    !query || i.ticker.toLowerCase().includes(query.toLowerCase()) || (i.name || "").toLowerCase().includes(query.toLowerCase())
  )
  const sorted = [...filtered].sort((a, b) => {
    const keyMap: Record<string, string> = { ticker: "ticker", price: "price", change: "change_1d_pct", score: "pulse_score" }
    const k = keyMap[sortKey] || sortKey
    const av = a[k] ?? 0, bv = b[k] ?? 0
    return sortDesc ? (bv > av ? 1 : -1) : (av > bv ? 1 : -1)
  })

  const hdrBtn = (label: string, k: string, width: number) => {
    const active = sortKey === k
    return (
      <button onClick={() => onSortChange(k)} style={{
        width, padding: "0 6px", height: "100%",
        textAlign: k === "ticker" ? "left" : "right" as any,
        background: "transparent", border: "none",
        color: active ? "var(--accent)" : "var(--muted)",
        fontSize: 10, letterSpacing: "0.06em", textTransform: "uppercase", fontWeight: 600,
      }}>
        {label}{active ? (sortDesc ? " ↓" : " ↑") : ""}
      </button>
    )
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--surface-1)" }}>
      <div style={{ padding: "8px", borderBottom: "1px solid var(--border)" }}>
        <input type="search" placeholder="Filter symbols" value={query}
          onChange={e => setQuery(e.target.value)} className="mono"
          style={{ width: "100%", fontSize: 11 }} />
      </div>

      <div style={{ display: "flex", alignItems: "center", height: 22, borderBottom: "1px solid var(--border)", background: "var(--bg)" }}>
        {hdrBtn("Sym", "ticker", 56)}
        {hdrBtn("Last", "price", 72)}
        {hdrBtn("Chg%", "change", 54)}
        {hdrBtn("Pulse", "score", 60)}
        <div style={{ flex: 1 }} />
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>
        {sorted.map(item => {
          const sel = item.ticker === selected
          const score = item.pulse_score ?? 50
          const change = item.change_1d_pct ?? 0
          const price = item.price ?? 0
          return (
            <div key={item.ticker} onClick={() => onSelect(item.ticker)}
              className={"row-hov " + (sel ? "row-sel row-sel-bar" : "")}
              style={{ display: "flex", alignItems: "center", height: 44, borderBottom: "1px solid var(--border-soft)", cursor: "pointer" }}>
              <div style={{ width: 60, padding: "0 10px", display: "flex", flexDirection: "column", gap: 2 }}>
                <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: sel ? "var(--accent)" : "var(--text)", letterSpacing: "-0.01em" }}>
                  {item.ticker.replace("-USD", "")}
                </span>
                <span style={{ fontSize: 9.5, color: "var(--dim)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                  {item.asset_type === "crypto" ? "crypto" : "stock"}
                </span>
              </div>
              <div className="num" style={{ width: 70, padding: "0 6px", fontSize: 12, textAlign: "right", fontWeight: 500 }}>
                {price > 0 ? fmtPrice(price) : "—"}
              </div>
              <div className="num" style={{ width: 56, padding: "0 6px", fontSize: 11.5, textAlign: "right", color: changeColor(change), fontWeight: 500 }}>
                {fmtPct(change)}
              </div>
              <div style={{ width: 62, padding: "0 8px", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 7 }}>
                <PulseBar score={score} w={26} h={3} />
                <span className="mono" style={{ fontSize: 11.5, fontWeight: 600, color: pulseColor(score), width: 18, textAlign: "right" }}>
                  {Math.round(score)}
                </span>
              </div>
              <div style={{ flex: 1, padding: "0 10px 0 6px", display: "flex", justifyContent: "flex-end" }}>
                {item._spark ? <Sparkline data={item._spark} w={44} h={18} fill /> : null}
              </div>
            </div>
          )
        })}
      </div>

      <div style={{ padding: "6px 10px", borderTop: "1px solid var(--border)", display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted)" }}>
        <span className="uppercase-sm">{sorted.length} symbols</span>
        <span className="mono"><span className="live-dot" style={{ marginRight: 4 }} />live</span>
      </div>
    </div>
  )
}

// ── Market overview ───────────────────────────────────────────────────────────
function MarketOverview({ watchlist, alerts, onSelect }: { watchlist: any[], alerts: any[], onSelect: (t: string) => void }) {
  const triggered = alerts.filter(a => !a.is_active && a.triggered_at)
  const armed = alerts.filter(a => a.is_active)

  const { data: brief, isLoading: briefLoading } = useSWR("/market/brief", fetcher, { refreshInterval: 900000 })

  const sorted = [...watchlist].filter(w => w.price).sort((a, b) => (b.change_1d_pct ?? 0) - (a.change_1d_pct ?? 0))
  const gainers = sorted.slice(0, 5)
  const losers = [...watchlist].filter(w => w.price).sort((a, b) => (a.change_1d_pct ?? 0) - (b.change_1d_pct ?? 0)).slice(0, 5)
  const avgSentiment = watchlist.length > 0 ? watchlist.reduce((s, w) => s + (w.pulse_score ?? 50), 0) / watchlist.length / 100 * 2 - 1 : 0
  const avgScore = watchlist.length > 0 ? Math.round(watchlist.reduce((s, w) => s + (w.pulse_score ?? 50), 0) / watchlist.length) : 50

  const posture = brief?.posture ?? (avgScore >= 65 ? "Constructive · Selective" : avgScore >= 50 ? "Neutral · Watchful" : "Cautious · Defensive")
  const confidence = brief?.confidence ?? avgScore

  return (
    <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", gridTemplateRows: "auto auto 1fr", overflow: "hidden" }}>
      {/* AI Market Brief */}
      <div style={{ gridColumn: "1 / 3", borderBottom: "1px solid var(--border)" }}>
        <SectionTitle right={
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {brief?.cached === false && <Chip tone="accent">FRESH</Chip>}
            {brief?.cached && <Chip tone="neutral">CACHED</Chip>}
            <Chip tone="accent">{posture}</Chip>
            <span className="uppercase-sm" style={{ color: "var(--dim)" }}>confidence</span>
            <span className="mono accent" style={{ fontSize: 11, fontWeight: 700 }}>{confidence}</span>
          </span>
        }>
          AI Market Brief · {new Date().toLocaleDateString("en-US", { month: "short", day: "numeric" })} · {new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })} ET
        </SectionTitle>

        {briefLoading ? (
          <div style={{ padding: "20px 22px", display: "flex", alignItems: "center", gap: 10, color: "var(--dim)" }}>
            <div style={{ width: 12, height: 12, border: "1.5px solid var(--accent)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
            <span style={{ fontSize: 12 }}>Asking Gemini for market analysis…</span>
          </div>
        ) : (
          <div style={{ padding: "18px 22px", display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 28 }}>
            <div>
              <div style={{ fontSize: 18, fontWeight: 500, letterSpacing: "-0.015em", lineHeight: 1.35, marginBottom: 12 }}>
                {brief?.headline ?? "Loading market analysis…"}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--muted)", lineHeight: 1.6 }}>
                {brief?.body ?? ""}
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {(brief?.bullets ?? []).map((b: any, i: number) => {
                const tone = b.tag === "BULL" ? "up" : b.tag === "BEAR" ? "down" : "warn"
                const color = tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : "var(--warn)"
                return (
                  <div key={i} style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
                    <span style={{ width: 3, alignSelf: "stretch", background: color, borderRadius: 1, opacity: 0.7, marginTop: 2 }} />
                    <div>
                      <span style={{ fontSize: 9.5, letterSpacing: "0.1em", textTransform: "uppercase", color, fontWeight: 600, display: "block", marginBottom: 2 }}>{b.tag}</span>
                      <span style={{ fontSize: 11.5, lineHeight: 1.5 }}>{b.text}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* Top movers */}
      <div style={{ borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)" }}>
        <SectionTitle>Top Movers · Session</SectionTitle>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
          <MoverList items={gainers} dir="up" onSelect={onSelect} />
          <div style={{ borderLeft: "1px solid var(--border)" }}>
            <MoverList items={losers} dir="down" onSelect={onSelect} />
          </div>
        </div>
      </div>

      {/* Sentiment */}
      <div style={{ borderBottom: "1px solid var(--border)" }}>
        <SectionTitle right={
          <span className="mono" style={{ fontSize: 11, fontWeight: 700, color: avgSentiment > 0 ? "var(--up)" : "var(--down)" }}>
            {avgSentiment > 0 ? "+" : ""}{avgSentiment.toFixed(2)}
          </span>
        }>
          Watchlist Pulse · Distribution
        </SectionTitle>
        <WatchlistSentimentPanel watchlist={watchlist} />
      </div>

      {/* Alerts */}
      <div style={{ gridColumn: "1 / 3", overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <SectionTitle right={
          <span style={{ display: "flex", gap: 8 }}>
            <Chip tone="warn">{triggered.length} TRIGGERED</Chip>
            <Chip tone="neutral">{armed.length} ARMED</Chip>
          </span>
        }>Active Alerts</SectionTitle>
        <div style={{ flex: 1, overflowY: "auto" }}>
          <AlertsTable alerts={alerts} onSelect={onSelect} />
        </div>
      </div>
    </div>
  )
}

function MoverList({ items, dir, onSelect }: { items: any[], dir: string, onSelect: (t: string) => void }) {
  const color = dir === "up" ? "var(--up)" : "var(--down)"
  return (
    <div>
      <div style={{ padding: "8px 14px", display: "flex", justifyContent: "space-between", alignItems: "center", background: "var(--surface-1)", borderBottom: "1px solid var(--border-soft)" }}>
        <span style={{ fontSize: 11, color, fontWeight: 500 }}>{dir === "up" ? "Gainers" : "Losers"}</span>
        <span className="uppercase-sm" style={{ fontSize: 9 }}>Pulse</span>
      </div>
      {items.map(m => (
        <div key={m.ticker} onClick={() => onSelect(m.ticker)} className="row-hov"
          style={{ display: "flex", alignItems: "center", padding: "8px 14px", borderBottom: "1px solid var(--border-soft)", cursor: "pointer", fontSize: 11.5 }}>
          <span className="mono" style={{ fontWeight: 600, width: 76 }}>{m.ticker.replace("-USD", "")}</span>
          <span className="num" style={{ flex: 1, textAlign: "right", color, fontWeight: 500 }}>{fmtPct(m.change_1d_pct ?? 0)}</span>
          <span className="mono" style={{ width: 32, textAlign: "right", color: "var(--muted)", fontSize: 10.5 }}>{Math.round(m.pulse_score ?? 50)}</span>
        </div>
      ))}
    </div>
  )
}

function WatchlistSentimentPanel({ watchlist }: { watchlist: any[] }) {
  const buckets = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
  const counts = buckets.map((lo, i) => {
    const hi = lo + 10
    return watchlist.filter(w => (w.pulse_score ?? 50) >= lo && (w.pulse_score ?? 50) < hi).length
  })
  const max = Math.max(...counts, 1)
  const w = 320, h = 60
  return (
    <div style={{ padding: "10px 14px" }}>
      <svg width="100%" viewBox={"0 0 " + w + " " + h} preserveAspectRatio="none" style={{ display: "block" }}>
        {counts.map((count, i) => {
          const barH = (count / max) * (h - 4)
          const score = i * 10 + 5
          const color = score >= 60 ? "var(--up)" : score >= 40 ? "var(--warn)" : "var(--down)"
          const barW = w / counts.length - 2
          return (
            <rect key={i} x={i * (w / counts.length) + 1} y={h - barH} width={barW} height={barH}
              fill={color} opacity="0.75" />
          )
        })}
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 9, color: "var(--dim)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
        <span>Bearish (0)</span><span>Neutral (50)</span><span>Strong (100)</span>
      </div>
      <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
        {[
          { label: "Bearish", lo: 0, hi: 40 },
          { label: "Neutral", lo: 40, hi: 60 },
          { label: "Bullish", lo: 60, hi: 100 },
        ].map(bucket => {
          const count = watchlist.filter(w => (w.pulse_score ?? 50) >= bucket.lo && (w.pulse_score ?? 50) < bucket.hi).length
          const pct = watchlist.length > 0 ? (count / watchlist.length * 100).toFixed(0) : "0"
          const color = bucket.lo >= 60 ? "var(--up)" : bucket.lo < 40 ? "var(--down)" : "var(--warn)"
          return (
            <div key={bucket.label} style={{ padding: "6px 8px", background: "var(--surface-2)", border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 9.5, color: "var(--muted)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500, marginBottom: 3 }}>{bucket.label}</div>
              <div className="mono" style={{ fontSize: 14, fontWeight: 700, color }}>{pct}%</div>
              <div style={{ fontSize: 9.5, color: "var(--dim)", marginTop: 1 }}>{count} of {watchlist.length}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function AlertsTable({ alerts, onSelect }: { alerts: any[], onSelect: (t: string) => void }) {
  if (alerts.length === 0) {
    return <div style={{ padding: 24, textAlign: "center", color: "var(--dim)", fontSize: 11 }}>No alerts configured. Add them via the API at /docs.</div>
  }
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "110px 80px 1fr 120px 110px 70px", padding: "9px 18px", background: "var(--surface-1)", borderBottom: "1px solid var(--border-soft)" }}>
        {["State", "Symbol", "Rule", "Threshold", "Type", "Created"].map(h => (
          <span key={h} className="uppercase-sm" style={{ fontSize: 9 }}>{h}</span>
        ))}
      </div>
      {alerts.map(a => {
        const triggered = !a.is_active && a.triggered_at
        return (
          <div key={a.id} onClick={() => onSelect(a.ticker)} className="row-hov"
            style={{ display: "grid", gridTemplateColumns: "110px 80px 1fr 120px 110px 70px", padding: "10px 18px", borderBottom: "1px solid var(--border-soft)", cursor: "pointer", fontSize: 11.5, alignItems: "center" }}>
            <span>{triggered ? <Chip tone="warn">Triggered</Chip> : <Chip tone="neutral">Armed</Chip>}</span>
            <span className="mono" style={{ fontWeight: 600 }}>{a.ticker.replace("-USD", "")}</span>
            <span>{a.alert_type.replace("_", " ")}</span>
            <span className="num" style={{ color: "var(--muted)" }}>{a.threshold.toLocaleString()}</span>
            <span style={{ fontSize: 10.5, color: "var(--muted)" }}>{a.notification_channels?.join(" · ")}</span>
            <span className="mono" style={{ color: "var(--dim)", fontSize: 10 }}>
              {a.triggered_at ? new Date(a.triggered_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }) : "—"}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ── Asset detail ──────────────────────────────────────────────────────────────
function AssetDetail({ ticker, watchlistItem, onBack, timeframe, setTimeframe }: {
  ticker: string, watchlistItem: any, onBack: () => void, timeframe: string, setTimeframe: (t: string) => void
}) {
  const periodMap: Record<string, [string, string]> = {
    "1D": ["5d", "15m"], "1W": ["1mo", "1h"], "1M": ["3mo", "1d"],
    "3M": ["6mo", "1d"], "1Y": ["1y", "1d"], "5Y": ["5y", "1wk"],
  }
  const [period, interval] = periodMap[timeframe] ?? ["3mo", "1d"]

  const { data: snapshot } = useSWR(`/assets/${ticker}?include_news=true&include_mtf=false`, fetcher, { refreshInterval: 60000 })
  const { data: ohlcv }    = useSWR(`/assets/${ticker}/ohlcv?period=${period}&interval=${interval}`, fetcher, { refreshInterval: 60000 })
  const { data: score }    = useSWR(`/assets/${ticker}/pulse-score?include_ai=false&include_mtf=false`, fetcher, { refreshInterval: 60000 })

  const [deepDive, setDeepDive] = useState<any>(null)
  const [ddLoading, setDdLoading] = useState(false)

  async function loadDeepDive() {
    if (ddLoading || deepDive) return
    setDdLoading(true)
    try {
      const r = await fetch(BASE + `/assets/${ticker}/deep-dive`, { method: "POST" })
      const d = await r.json()
      setDeepDive(d)
    } catch { /* ignore */ }
    setDdLoading(false)
  }

  // Auto-run AI on mount
  useEffect(() => {
    loadDeepDive()
  }, [ticker]) // eslint-disable-line

  const quote = snapshot?.quote ?? watchlistItem
  const news  = snapshot?.news?.items ?? []
  const indicators = snapshot?.indicators
  const price = quote?.price ?? watchlistItem?.price ?? 0
  const change = quote?.change_1d_pct ?? watchlistItem?.change_1d_pct ?? 0
  const pulseScore = score?.overall ?? watchlistItem?.pulse_score ?? 50
  const breakdown = score ? [
    { label: "Trend",      score: score.trend?.score ?? 50,          weight: score.trend?.weight ?? 0.25 },
    { label: "Momentum",   score: score.momentum?.score ?? 50,       weight: score.momentum?.weight ?? 0.20 },
    { label: "Volatility", score: score.volatility?.score ?? 50,     weight: score.volatility?.weight ?? 0.10 },
    { label: "Volume",     score: score.volume?.score ?? 50,         weight: score.volume?.weight ?? 0.10 },
    { label: "Sentiment",  score: score.sentiment?.score ?? 50,      weight: score.sentiment?.weight ?? 0.15 },
    { label: "Multi-TF",   score: score.multi_timeframe?.score ?? 50, weight: score.multi_timeframe?.weight ?? 0.10 },
    { label: "AI",         score: score.ai?.score ?? 50,             weight: score.ai?.weight ?? 0.10 },
  ] : []

  // Build indicator table from snapshot
  const indRows = indicators ? [
    { k: "Price",      v: fmtPrice(price),                              i: "" },
    { k: "RSI 14",     v: indicators.rsi_14?.value?.toFixed(1) ?? "—",  i: indicators.rsi_14?.interpretation ?? "" },
    { k: "MACD",       v: indicators.macd_histogram?.value?.toFixed(3) ?? "—", i: indicators.macd_histogram?.interpretation ?? "" },
    { k: "SMA 20",     v: indicators.sma_20?.value?.toFixed(2) ?? "—",  i: indicators.sma_20?.interpretation ?? "" },
    { k: "SMA 50",     v: indicators.sma_50?.value?.toFixed(2) ?? "—",  i: indicators.sma_50?.interpretation ?? "" },
    { k: "SMA 200",    v: indicators.sma_200?.value?.toFixed(2) ?? "—", i: indicators.sma_200?.interpretation ?? "" },
    { k: "EMA 12",     v: indicators.ema_12?.value?.toFixed(2) ?? "—",  i: indicators.ema_12?.interpretation ?? "" },
    { k: "EMA 26",     v: indicators.ema_26?.value?.toFixed(2) ?? "—",  i: indicators.ema_26?.interpretation ?? "" },
    { k: "BB Upper",   v: indicators.bb_upper?.value?.toFixed(2) ?? "—", i: "" },
    { k: "BB %B",      v: indicators.bb_percent_b?.value?.toFixed(3) ?? "—", i: indicators.bb_percent_b?.interpretation ?? "" },
    { k: "ADX 14",     v: indicators.adx_14?.value?.toFixed(1) ?? "—",  i: indicators.adx_14?.interpretation ?? "" },
    { k: "Stoch K",    v: indicators.stoch_k?.value?.toFixed(1) ?? "—", i: indicators.stoch_k?.interpretation ?? "" },
    { k: "Williams %R",v: indicators.williams_r?.value?.toFixed(1) ?? "—", i: indicators.williams_r?.interpretation ?? "" },
    { k: "OBV",        v: indicators.obv?.value ? (indicators.obv.value / 1e6).toFixed(1) + "M" : "—", i: "" },
    { k: "MFI 14",     v: indicators.mfi_14?.value?.toFixed(1) ?? "—",  i: indicators.mfi_14?.interpretation ?? "" },
    { k: "Vol Ratio",  v: indicators.volume_ratio?.value?.toFixed(2) ?? "—", i: indicators.volume_ratio?.interpretation ?? "" },
    { k: "Hurst",      v: indicators.hurst_exponent?.value?.toFixed(3) ?? "—", i: indicators.hurst_exponent?.interpretation ?? "" },
    { k: "Z-Score",    v: indicators.z_score_20?.value?.toFixed(2) ?? "—", i: indicators.z_score_20?.interpretation ?? "" },
    { k: "Real. Vol",  v: indicators.realized_vol_20?.value?.toFixed(1) ?? "—", i: indicators.realized_vol_20?.interpretation ?? "" },
  ] : []

  const isBull = (s: string) => s.toLowerCase().includes("bull") || s.toLowerCase().includes("above")
  const isBear = (s: string) => s.toLowerCase().includes("bear") || s.toLowerCase().includes("below") || s.toLowerCase().includes("oversold")
  const indColor = (s: string) => isBull(s) ? "var(--up)" : isBear(s) ? "var(--down)" : "var(--muted)"

  const bullCount = indRows.filter(r => r.i && isBull(r.i)).length
  const bearCount = indRows.filter(r => r.i && isBear(r.i)).length

  return (
    <div style={{ flex: 1, display: "grid", gridTemplateRows: "auto 1fr auto", overflow: "hidden" }}>
      {/* Header */}
      <div style={{ borderBottom: "1px solid var(--border)", background: "var(--surface-1)", display: "flex", alignItems: "stretch", minHeight: 76 }}>
        <button onClick={onBack} className="btn-ghost" style={{ margin: "auto 0 auto 12px", alignSelf: "center" }}>← Back</button>
        <div style={{ display: "flex", alignItems: "center", padding: "10px 16px", borderRight: "1px solid var(--border)", marginLeft: 8 }}>
          <div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span className="mono" style={{ fontSize: 19, fontWeight: 600, letterSpacing: "-0.015em" }}>{ticker}</span>
              <Chip tone={watchlistItem?.asset_type === "crypto" ? "warn" : "accent"}>
                {watchlistItem?.asset_type === "crypto" ? "Crypto" : "Equity"}
              </Chip>
            </div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{watchlistItem?.name ?? ticker}</div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", padding: "0 18px", borderRight: "1px solid var(--border)", gap: 14, flexShrink: 0 }}>
          <div>
            <div className="num" style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.025em", lineHeight: 1 }}>
              {price > 0 ? fmtPrice(price) : "Loading…"}
            </div>
            <div style={{ fontSize: 10, color: "var(--muted)", letterSpacing: "0.08em", textTransform: "uppercase", marginTop: 5 }}>Last · USD</div>
          </div>
          <div>
            <div className="num" style={{ fontSize: 12, fontWeight: 500, color: changeColor(change), lineHeight: 1.3 }}>
              {change !== 0 ? (change > 0 ? "+" : "") + (price * change / 100).toFixed(2) : ""}
            </div>
            <div className="num" style={{ fontSize: 12, fontWeight: 500, color: changeColor(change), lineHeight: 1.3 }}>
              {fmtPct(change)}
            </div>
          </div>
        </div>

        <div style={{ flex: 1, display: "flex", alignItems: "center", padding: "0 20px", gap: 28 }}>
          {snapshot?.quote?.market_cap && (
            <div>
              <div style={{ fontSize: 9.5, color: "var(--muted)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>Mkt Cap</div>
              <div className="mono" style={{ fontSize: 13, fontWeight: 500, marginTop: 2 }}>{fmtLarge(snapshot.quote.market_cap)}</div>
            </div>
          )}
          {snapshot?.quote?.volume && (
            <div>
              <div style={{ fontSize: 9.5, color: "var(--muted)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>Volume</div>
              <div className="mono" style={{ fontSize: 13, fontWeight: 500, marginTop: 2 }}>{fmtLarge(snapshot.quote.volume)}</div>
            </div>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", padding: "0 16px", borderLeft: "1px solid var(--border)", gap: 16, flexShrink: 0 }}>
          {deepDive?.price_target && (
            <div style={{ textAlign: "right", borderRight: "1px solid var(--border-soft)", paddingRight: 14 }}>
              <div style={{ fontSize: 9.5, color: "var(--muted)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>
                AI Target · {deepDive.price_target.horizon}
              </div>
              <div className="mono" style={{ fontSize: 13, color: "var(--accent)", fontWeight: 600, marginTop: 3 }}>
                {fmtPrice(deepDive.price_target.mid)}
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--dim)", marginTop: 1 }}>
                {fmtPrice(deepDive.price_target.low)} – {fmtPrice(deepDive.price_target.high)}
              </div>
            </div>
          )}
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 9.5, color: "var(--muted)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>Pulse</div>
            <div style={{ fontSize: 11, color: pulseColor(pulseScore), fontWeight: 500, marginTop: 3 }}>{pulseLabel(pulseScore)}</div>
            <div className="mono" style={{ fontSize: 10, color: "var(--dim)", marginTop: 1 }}>{Math.round(pulseScore)} / 100</div>
          </div>
          <PulseGauge score={pulseScore} size={48} thick={3.5} />
        </div>
      </div>

      {/* Main split */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 260px", overflow: "hidden" }}>
        {/* Left: chart + news */}
        <div style={{ display: "flex", flexDirection: "column", borderRight: "1px solid var(--border)", overflow: "hidden" }}>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <SectionTitle right={<TimeframeStrip value={timeframe} onChange={setTimeframe} />}>
              <span><span className="accent">⌁</span> Price · {timeframe}</span>
            </SectionTitle>
            <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
              {ohlcv && ohlcv.length > 0
                ? <CandleChart candles={ohlcv} />
                : <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--dim)", fontSize: 11 }}>Loading chart…</div>}
            </div>
          </div>
          <div style={{ borderTop: "1px solid var(--border)", maxHeight: "44%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <SectionTitle right={<Chip tone="neutral">{news.length} articles</Chip>}>News · Sentiment</SectionTitle>
            <div style={{ flex: 1, overflowY: "auto" }}>
              {news.length > 0 ? <NewsList news={news} /> : (
                <div style={{ padding: 16, color: "var(--dim)", fontSize: 11 }}>No news loaded yet.</div>
              )}
            </div>
          </div>
        </div>

        {/* Right: indicators */}
        <div style={{ display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--surface-1)" }}>
          <SectionTitle right={
            <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 3, fontSize: 10 }}>
                <span style={{ width: 6, height: 6, background: "var(--up)", borderRadius: 1 }} />
                <span className="mono" style={{ color: "var(--up)", fontWeight: 600 }}>{bullCount}</span>
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 3, fontSize: 10 }}>
                <span style={{ width: 6, height: 6, background: "var(--down)", borderRadius: 1 }} />
                <span className="mono" style={{ color: "var(--down)", fontWeight: 600 }}>{bearCount}</span>
              </span>
            </span>
          }>Indicators</SectionTitle>
          <div style={{ flex: 1, overflowY: "auto" }}>
            {indRows.length > 0 ? (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
                <tbody>
                  {indRows.map((ind, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid var(--border-soft)" }}>
                      <td style={{ padding: "9px 14px", color: "var(--muted)", width: 90, fontSize: 11 }}>{ind.k}</td>
                      <td className="mono" style={{ padding: "9px 10px", fontWeight: 500, textAlign: "right", width: 80 }}>{ind.v}</td>
                      <td style={{ padding: "9px 14px 9px 10px", color: indColor(ind.i), fontSize: 10.5 }}>{ind.i}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div style={{ padding: 16, color: "var(--dim)", fontSize: 11 }}>Loading indicators…</div>
            )}
          </div>
        </div>
      </div>

      {/* Deep dive bottom drawer */}
      <div style={{ borderTop: "1px solid var(--border)", background: "var(--surface-1)", display: "grid", gridTemplateColumns: "1.3fr 1fr 1fr 1fr", height: 280, overflow: "hidden" }}>
        {/* Summary */}
        <div style={{ borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <SectionTitle right={
            deepDive ? <Chip tone={deepDive.cached ? "neutral" : "accent"}>{deepDive.cached ? "Cached" : "Fresh"}</Chip> : null
          }>AI Deep Dive · {ticker}</SectionTitle>
          <div style={{ padding: "10px 14px", overflowY: "auto", flex: 1 }}>
            {deepDive ? (
              <>
                <p style={{ fontSize: 11.5, color: "var(--text)", lineHeight: 1.55, margin: 0 }}>{deepDive.summary}</p>
                <div style={{ marginTop: 8, fontSize: 9, color: "var(--dim)", letterSpacing: "0.04em", textTransform: "uppercase" }}>
                  {deepDive.model_used} · {deepDive.cached ? "cached" : "just generated"}
                </div>
              </>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingTop: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {ddLoading && (
                    <div style={{ width: 10, height: 10, border: "1.5px solid var(--accent)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                  )}
                  <span style={{ fontSize: 11, color: "var(--dim)" }}>
                    {ddLoading ? `Asking ${ticker.includes("USD") ? "Gemini" : "Gemini"} for ${ticker} analysis…` : "AI analysis queued…"}
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>

        {deepDive ? (
          <>
            <FactorList tone="up" title="Bullish factors" items={deepDive.bullish_factors} />
            <FactorList tone="down" title="Bearish factors" items={deepDive.bearish_factors} />
          </>
        ) : (
          <>
            <div style={{ borderRight: "1px solid var(--border)", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <span style={{ color: "var(--dim)", fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.08em" }}>Bullish factors</span>
            </div>
            <div style={{ borderRight: "1px solid var(--border)", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <span style={{ color: "var(--dim)", fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.08em" }}>Bearish factors</span>
            </div>
          </>
        )}

        {/* Pulse breakdown */}
        <div style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <SectionTitle>Pulse Breakdown</SectionTitle>
          <div style={{ padding: "8px 12px", overflowY: "auto", flex: 1 }}>
            {breakdown.length > 0 ? breakdown.map(b => (
              <div key={b.label} style={{ marginBottom: 6 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 2 }}>
                  <span style={{ fontSize: 10, color: "var(--muted)", letterSpacing: "0.04em" }}>
                    {b.label} · <span className="mono dim">w{(b.weight * 100).toFixed(0)}</span>
                  </span>
                  <span className="mono" style={{ fontSize: 11, fontWeight: 700, color: pulseColor(b.score) }}>{Math.round(b.score)}</span>
                </div>
                <PulseBar score={b.score} w="100%" h={3} />
              </div>
            )) : (
              <div style={{ color: "var(--dim)", fontSize: 11 }}>Loading…</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function TimeframeStrip({ value, onChange }: { value: string, onChange: (t: string) => void }) {
  const tfs = ["1D", "1W", "1M", "3M", "1Y", "5Y"]
  return (
    <div style={{ display: "flex", border: "1px solid var(--border)", borderRadius: 2, overflow: "hidden" }}>
      {tfs.map(t => (
        <button key={t} onClick={() => onChange(t)} style={{
          background: value === t ? "var(--accent-dim)" : "transparent",
          color: value === t ? "var(--accent)" : "var(--muted)",
          border: "none", padding: "3px 10px", fontSize: 10, fontWeight: 700,
          letterSpacing: "0.06em", borderRight: "1px solid var(--border-soft)",
        }}>{t}</button>
      ))}
    </div>
  )
}

function NewsList({ news }: { news: any[] }) {
  return (
    <div>
      {news.slice(0, 10).map((n, i) => {
        const sent = n.sentiment_score ?? 0
        const color = sent > 0.2 ? "var(--up)" : sent < -0.2 ? "var(--down)" : "var(--muted)"
        const ago = (() => {
          const pub = new Date(n.published_at)
          const mins = Math.floor((Date.now() - pub.getTime()) / 60000)
          if (mins < 60) return mins + "m"
          if (mins < 1440) return Math.floor(mins / 60) + "h"
          return Math.floor(mins / 1440) + "d"
        })()
        return (
          <a key={i} href={n.url} target="_blank" rel="noopener noreferrer" style={{ textDecoration: "none" }}>
            <div className="row-hov" style={{ display: "grid", gridTemplateColumns: "82px 1fr 80px 56px 48px", padding: "10px 18px", borderBottom: "1px solid var(--border-soft)", alignItems: "center", gap: 12, cursor: "pointer" }}>
              <span style={{ fontSize: 10.5, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.publisher}</span>
              <span style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.title}</span>
              <SentimentBar score={sent} w={80} h={3} />
              <span className="mono" style={{ fontSize: 10.5, color, fontWeight: 500, textAlign: "right" }}>{(sent > 0 ? "+" : "") + sent.toFixed(2)}</span>
              <span className="mono" style={{ fontSize: 10.5, color: "var(--dim)", textAlign: "right" }}>{ago}</span>
            </div>
          </a>
        )
      })}
    </div>
  )
}

function FactorList({ tone, title, items }: { tone: string, title: string, items: string[] }) {
  const color = tone === "up" ? "var(--up)" : "var(--down)"
  return (
    <div style={{ borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{ padding: "6px 10px", borderBottom: "1px solid var(--border)", background: "var(--surface-1)" }}>
        <span className="uppercase-sm" style={{ color }}>{title}</span>
      </div>
      <div style={{ padding: "6px 12px 10px", overflowY: "auto", flex: 1 }}>
        {items.map((t, i) => (
          <div key={i} style={{ display: "flex", gap: 6, padding: "5px 0", alignItems: "flex-start" }}>
            <span style={{ color, fontSize: 11, lineHeight: 1.4, marginTop: 2 }}>▸</span>
            <span style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.45 }}>{t}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Candlestick chart (pure SVG, no external lib) ─────────────────────────────
function CandleChart({ candles }: { candles: any[] }) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const svgRef  = useRef<SVGSVGElement>(null)
  const [dims, setDims] = useState({ w: 800, h: 360 })
  const [hover, setHover] = useState<number | null>(null)
  const [overlay, setOverlay] = useState("none")
  const [chartType, setChartType] = useState("candles")

  useEffect(() => {
    if (!wrapRef.current) return
    const ro = new ResizeObserver(entries => {
      const r = entries[0].contentRect
      setDims({ w: Math.max(360, r.width), h: Math.max(200, r.height) })
    })
    ro.observe(wrapRef.current)
    return () => ro.disconnect()
  }, [])

  if (!candles || candles.length < 2) return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--dim)", fontSize: 11 }}>No chart data</div>

  // Normalise candle shape from API: {timestamp, open, high, low, close, volume}
  const cs = candles.map(c => ({
    open: c.open, high: c.high, low: c.low, close: c.close,
    vol: c.volume, d: new Date(c.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" })
  }))

  const { w, h } = dims
  const padL = 12, padR = 60, padT = 14, padB = 72
  const innerW = w - padL - padR, innerH = h - padT - padB
  const volH = 50
  const min = Math.min(...cs.map(c => c.low))
  const max = Math.max(...cs.map(c => c.high))
  const range = max - min || 1
  const stepX = innerW / cs.length
  const candleW = Math.max(2, stepX * 0.7)
  const maxVol = Math.max(...cs.map(c => c.vol))
  const yFor = (v: number) => padT + innerH - ((v - min) / range) * innerH
  const last = cs[cs.length - 1]
  const prev = cs[cs.length - 2] || last
  const lastY = yFor(last.close)

  const sma = cs.map((_, i) => {
    if (i < 19) return null
    return cs.slice(i - 19, i + 1).reduce((s, c) => s + c.close, 0) / 20
  })
  const bb = cs.map((_, i) => {
    if (i < 19) return null
    const slice = cs.slice(i - 19, i + 1).map(c => c.close)
    const m = slice.reduce((a, b) => a + b, 0) / 20
    const v = slice.reduce((a, b) => a + (b - m) * (b - m), 0) / 20
    const sd = Math.sqrt(v)
    return { mid: m, upper: m + 2 * sd, lower: m - 2 * sd }
  })

  const tickVals = Array.from({ length: 6 }, (_, i) => min + range * i / 5)
  const n = cs.length
  const dateLabels = [0, Math.floor(n * 0.25), Math.floor(n * 0.5), Math.floor(n * 0.75), n - 1].map(i => ({
    x: padL + i * stepX + stepX / 2, label: cs[i]?.d ?? ""
  }))

  const showC = hover != null ? cs[hover] : last
  const showCh = hover != null
    ? ((cs[hover].close - (cs[hover - 1] || cs[hover]).close) / (cs[hover - 1] || cs[hover]).close) * 100
    : ((last.close - prev.close) / prev.close) * 100
  const cUp = showC.close >= showC.open

  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect()
    const svgX = ((e.clientX - r.left) / r.width) * w
    if (svgX < padL || svgX > w - padR) { setHover(null); return }
    setHover(Math.max(0, Math.min(cs.length - 1, Math.floor((svgX - padL) / stepX))))
  }

  return (
    <div style={{ width: "100%", height: "100%", background: "var(--bg)", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", padding: "6px 12px", borderBottom: "1px solid var(--border-soft)", background: "var(--surface-1)", flexShrink: 0 }}>
        <div className="mono" style={{ display: "flex", gap: 14, fontSize: 10.5, alignItems: "baseline" }}>
          <span><span style={{ color: "var(--dim)" }}>O</span> <span>{showC.open.toFixed(2)}</span></span>
          <span><span style={{ color: "var(--dim)" }}>H</span> <span>{showC.high.toFixed(2)}</span></span>
          <span><span style={{ color: "var(--dim)" }}>L</span> <span>{showC.low.toFixed(2)}</span></span>
          <span><span style={{ color: "var(--dim)" }}>C</span> <span style={{ color: cUp ? "var(--up)" : "var(--down)" }}>{showC.close.toFixed(2)}</span></span>
          <span style={{ color: showCh >= 0 ? "var(--up)" : "var(--down)" }}>{showCh >= 0 ? "+" : ""}{showCh.toFixed(2)}%</span>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: "flex", border: "1px solid var(--border)", marginRight: 8 }}>
          {[["candles", "▮"], ["line", "～"]].map(([k, g]) => (
            <button key={k} onClick={() => setChartType(k)} style={{
              background: chartType === k ? "var(--accent-dim)" : "transparent",
              color: chartType === k ? "var(--accent)" : "var(--muted)",
              border: "none", padding: "2px 8px", fontSize: 11, fontWeight: 600,
              borderRight: k === "candles" ? "1px solid var(--border)" : "none",
            }}>{g}</button>
          ))}
        </div>
        <div style={{ display: "flex", border: "1px solid var(--border)" }}>
          {[["none", "—"], ["sma", "SMA20"], ["bb", "BB(20,2)"]].map(([k, label], i) => (
            <button key={k} onClick={() => setOverlay(k)} style={{
              background: overlay === k ? "var(--accent-dim)" : "transparent",
              color: overlay === k ? "var(--accent)" : "var(--muted)",
              border: "none", padding: "2px 9px", fontSize: 10, fontWeight: 600, letterSpacing: "0.04em",
              borderRight: i < 2 ? "1px solid var(--border)" : "none",
            }}>{label}</button>
          ))}
        </div>
      </div>

      <div ref={wrapRef} style={{ flex: 1, minHeight: 0, position: "relative" }}>
        <svg ref={svgRef} width={w} height={h} viewBox={`0 0 ${w} ${h}`}
          style={{ display: "block", cursor: "crosshair", position: "absolute", inset: 0 }}
          onMouseMove={handleMove} onMouseLeave={() => setHover(null)}>

          {tickVals.map((v, i) => {
            const y = yFor(v)
            return (
              <g key={i}>
                <line x1={padL} y1={y} x2={w - padR} y2={y} stroke="var(--border-soft)" strokeWidth="0.5" strokeDasharray="2 3" />
                <text x={w - padR + 4} y={y + 3} fontSize="9" fill="var(--muted)" fontFamily="JetBrains Mono">
                  {v >= 1000 ? v.toFixed(0) : v.toFixed(2)}
                </text>
              </g>
            )
          })}

          {overlay === "bb" && (() => {
            const pts = bb.filter(Boolean)
            if (pts.length < 2) return null
            const upper = bb.map((b, i) => b ? `${(padL + i * stepX + stepX / 2).toFixed(1)},${yFor(b.upper).toFixed(1)}` : null).filter(Boolean)
            const lower = [...bb].reverse().map((b, i) => {
              const ri = bb.length - 1 - i
              return b ? `${(padL + ri * stepX + stepX / 2).toFixed(1)},${yFor(b.lower).toFixed(1)}` : null
            }).filter(Boolean)
            return <path d={"M" + upper.join(" L") + " L" + lower.join(" L") + " Z"} fill="var(--accent)" opacity="0.08" />
          })()}

          {chartType === "candles" ? cs.map((c, i) => {
            const x = padL + i * stepX + stepX / 2
            const yO = yFor(c.open), yC = yFor(c.close)
            const yH = yFor(c.high), yL = yFor(c.low)
            const up = c.close >= c.open
            const color = up ? "var(--up)" : "var(--down)"
            return (
              <g key={i}>
                <line x1={x} y1={yH} x2={x} y2={yL} stroke={color} strokeWidth="0.8" />
                <rect x={x - candleW / 2} y={Math.min(yO, yC)} width={candleW} height={Math.max(1, Math.abs(yO - yC))} fill={color} opacity={up ? 0.9 : 0.95} />
              </g>
            )
          }) : (
            <path d={cs.map((c, i) => (i === 0 ? "M" : "L") + (padL + i * stepX + stepX / 2).toFixed(1) + " " + yFor(c.close).toFixed(1)).join(" ")}
              fill="none" stroke="var(--accent)" strokeWidth="1.4" />
          )}

          {(overlay === "sma" || overlay === "bb") && (
            <path d={sma.map((s, i) => s == null ? "" : ((i === 0 || sma[i-1] == null) ? "M" : "L") + (padL + i * stepX + stepX / 2).toFixed(1) + " " + yFor(s).toFixed(1)).join(" ")}
              fill="none" stroke="var(--accent)" strokeWidth="1.2" opacity="0.85" />
          )}

          <line x1={padL} y1={lastY} x2={w - padR} y2={lastY} stroke="var(--accent)" strokeDasharray="2 2" strokeWidth="0.8" opacity="0.55" />
          <rect x={w - padR} y={lastY - 8} width={padR - 2} height={16} fill="var(--accent)" />
          <text x={w - padR + 4} y={lastY + 3} fontSize="10" fill="var(--bg)" fontFamily="JetBrains Mono" fontWeight="700">{fmtPrice(last.close)}</text>

          {cs.map((c, i) => {
            const x = padL + i * stepX + stepX / 2
            const bh = (c.vol / maxVol) * volH
            return (
              <rect key={"v" + i} x={x - candleW / 2} y={h - padB + 14 + (volH - bh)} width={candleW} height={bh}
                fill={c.close >= c.open ? "var(--up)" : "var(--down)"} opacity={hover === i ? 0.85 : 0.4} />
            )
          })}
          <text x={padL} y={h - padB + 10} fontSize="9" fill="var(--muted)" fontFamily="JetBrains Mono" letterSpacing="0.06em">VOLUME</text>

          {dateLabels.map((d, i) => (
            <text key={i} x={d.x} y={h - 4} fontSize="9" fill="var(--dim)" fontFamily="JetBrains Mono" textAnchor="middle">{d.label}</text>
          ))}

          {hover != null && (() => {
            const cx = padL + hover * stepX + stepX / 2
            const hc = cs[hover]
            const hy = yFor(hc.close)
            return (
              <g pointerEvents="none">
                <line x1={cx} y1={padT} x2={cx} y2={h - padB + 8 + volH + 6} stroke="var(--muted)" strokeWidth="0.6" strokeDasharray="3 3" opacity="0.7" />
                <line x1={padL} y1={hy} x2={w - padR} y2={hy} stroke="var(--muted)" strokeWidth="0.6" strokeDasharray="3 3" opacity="0.7" />
                <rect x={w - padR} y={hy - 8} width={padR - 2} height={16} fill="var(--surface-3)" stroke="var(--muted)" strokeWidth="0.5" />
                <text x={w - padR + 4} y={hy + 3} fontSize="10" fill="var(--text)" fontFamily="JetBrains Mono" fontWeight="700">{hc.close.toFixed(2)}</text>
                <circle cx={cx} cy={hy} r={2.5} fill="var(--accent)" />
              </g>
            )
          })()}
        </svg>
      </div>
    </div>
  )
}

// ── Right rail ────────────────────────────────────────────────────────────────
function MacroDot({ label, tone, val }: { label: string, tone: string, val: string }) {
  const color = tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : "var(--warn)"
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "3px 6px", background: "var(--surface-2)", border: "1px solid var(--border-soft)" }}>
      <span style={{ color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.04em", fontSize: 10 }}>{label}</span>
      <span className="mono" style={{ color, fontWeight: 600, fontSize: 10.5 }}>{val}</span>
    </div>
  )
}

function Heatmap({ items, onSelect }: { items: any[], onSelect: (t: string) => void }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 2 }}>
      {items.map(i => {
        const change = i.change_1d_pct ?? 0
        const intensity = Math.min(0.9, Math.abs(change) / 5)
        const baseColor = change >= 0 ? "61,220,151" : "255,93,114"
        return (
          <div key={i.ticker} onClick={() => onSelect(i.ticker)} className="row-hov"
            style={{
              aspectRatio: "1 / 0.85",
              background: `rgba(${baseColor},${0.1 + intensity * 0.55})`,
              border: "1px solid var(--border-soft)",
              cursor: "pointer", padding: "3px 4px",
              display: "flex", flexDirection: "column", justifyContent: "space-between",
            }}>
            <span className="mono" style={{ fontSize: 9, fontWeight: 700 }}>{i.ticker.replace("-USD","").slice(0,4)}</span>
            <span className="mono" style={{ fontSize: 8, color: change >= 0 ? "var(--up)" : "var(--down)", fontWeight: 600 }}>{fmtPct(change)}</span>
          </div>
        )
      })}
    </div>
  )
}

function RightRail({ onSelect, watchlist, alerts, macroSnap }: {
  onSelect: (t: string) => void, watchlist: any[], alerts: any[], macroSnap: any
}) {
  const triggered = alerts.filter(a => !a.is_active && a.triggered_at)
  const vix  = macroSnap?.vix?.toFixed(2) ?? "—"
  const t10y = macroSnap?.treasury_10y?.toFixed(2) ?? "—"
  const spread = macroSnap?.yield_spread_10y2y?.toFixed(2) ?? "—"
  const cpi  = macroSnap?.cpi?.toFixed(1) ?? "—"
  return (
    <>
      <SectionTitle right={<Chip tone="warn">{triggered.length}</Chip>}>Triggered Alerts</SectionTitle>
      <div style={{ borderBottom: "1px solid var(--border)", maxHeight: 200, overflowY: "auto", flexShrink: 0 }}>
        {triggered.length === 0 ? (
          <div style={{ padding: "12px 14px", fontSize: 10.5, color: "var(--dim)" }}>No triggered alerts</div>
        ) : triggered.map(a => (
          <div key={a.id} className="row-hov" onClick={() => onSelect(a.ticker)}
            style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-soft)", cursor: "pointer", display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 3, height: 32, background: "var(--warn)", flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>{a.ticker.replace("-USD","")}</span>
                <span className="mono" style={{ fontSize: 9, color: "var(--dim)" }}>
                  {a.triggered_at ? new Date(a.triggered_at).toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"}) : "—"}
                </span>
              </div>
              <div style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 2 }}>{a.alert_type.replace("_"," ")} {a.threshold}</div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}>
        <SectionTitle right={<Chip tone={macroSnap ? "accent" : "neutral"}>{macroSnap ? "LIVE" : "NO KEY"}</Chip>}>
          AI Pulse · Macro
        </SectionTitle>
        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.5, marginBottom: 8 }}>
            {macroSnap
              ? <>VIX <span style={{ color: Number(vix) > 20 ? "var(--warn)" : "var(--up)" }}>{vix}</span> · 10Y {t10y}% · Spread <span style={{ color: Number(spread) < 0 ? "var(--down)" : "var(--up)" }}>{spread}%</span></>
              : <span style={{ color: "var(--dim)" }}>Add FRED_API_KEY for macro data</span>}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
            <MacroDot label="VIX"   tone={Number(vix) > 20 ? "warn" : "up"}  val={vix} />
            <MacroDot label="10Y"   tone="warn"  val={t10y + "%"} />
            <MacroDot label="Sprd"  tone={Number(spread) < 0 ? "down" : "up"} val={spread + "%"} />
            <MacroDot label="CPI"   tone="warn"  val={cpi} />
          </div>
        </div>

        <SectionTitle>Session Heatmap · Watchlist</SectionTitle>
        <div style={{ padding: 8, borderBottom: "1px solid var(--border)" }}>
          <Heatmap items={watchlist} onSelect={onSelect} />
        </div>

        <div style={{ flex: 1 }} />
      </div>
    </>
  )
}

// ── Tweaks panel ──────────────────────────────────────────────────────────────
function TweaksPanel({ tweaks, setTweak, onClose }: {
  tweaks: Record<string,any>, setTweak: (k: string, v: any) => void, onClose: () => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const pos = useRef({ right: 16, bottom: 32 })

  function onDragStart(e: React.MouseEvent) {
    const panel = panelRef.current; if (!panel) return
    const r = panel.getBoundingClientRect()
    const sx = e.clientX, sy = e.clientY
    const startR = window.innerWidth - r.right, startB = window.innerHeight - r.bottom
    const move = (ev: MouseEvent) => {
      pos.current = { right: startR - (ev.clientX - sx), bottom: startB - (ev.clientY - sy) }
      panel.style.right = pos.current.right + "px"
      panel.style.bottom = pos.current.bottom + "px"
    }
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up) }
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up)
  }

  return (
    <div ref={panelRef} style={{
      position: "fixed", right: 16, bottom: 32, zIndex: 9999, width: 260,
      background: "rgba(15,18,24,0.95)", border: "1px solid var(--border)",
      boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
      backdropFilter: "blur(16px)", display: "flex", flexDirection: "column",
    }}>
      <div onMouseDown={onDragStart} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 12px", borderBottom: "1px solid var(--border)", cursor: "move", background: "var(--surface-1)" }}>
        <span className="uppercase-sm" style={{ color: "var(--text)" }}>Tweaks</span>
        <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--muted)", fontSize: 13, cursor: "pointer", lineHeight: 1 }}>✕</button>
      </div>
      <div style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 12 }}>
        {/* Accent hue */}
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="uppercase-sm">Accent hue</span>
            <span className="mono" style={{ fontSize: 10, color: "var(--muted)" }}>{tweaks.accentHue}°</span>
          </div>
          <input type="range" min={0} max={360} step={5} value={tweaks.accentHue}
            onChange={e => setTweak("accentHue", Number(e.target.value))}
            style={{ width: "100%", accentColor: "var(--accent)" }} />
        </div>
        {/* Density */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span className="uppercase-sm">Density</span>
          <div style={{ display: "flex", border: "1px solid var(--border)", borderRadius: 2 }}>
            {["compact","normal"].map(v => (
              <button key={v} onClick={() => setTweak("density", v)} style={{
                background: tweaks.density === v ? "var(--accent-dim)" : "transparent",
                color: tweaks.density === v ? "var(--accent)" : "var(--muted)",
                border: "none", padding: "3px 10px", fontSize: 10, fontWeight: 600, letterSpacing: "0.04em",
                borderRight: v === "compact" ? "1px solid var(--border)" : "none",
              }}>{v}</button>
            ))}
          </div>
        </div>
        {/* Toggles */}
        {[
          ["showTape",      "Ticker tape"],
          ["showRightRail", "Right rail"],
        ].map(([k, label]) => (
          <div key={k} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="uppercase-sm">{label}</span>
            <button onClick={() => setTweak(k, !tweaks[k])} style={{
              width: 32, height: 18, borderRadius: 999, border: "none", cursor: "pointer", padding: 0, position: "relative",
              background: tweaks[k] ? "var(--up)" : "rgba(0,0,0,0.3)", transition: "background 0.15s",
            }}>
              <span style={{ position: "absolute", top: 2, left: tweaks[k] ? 16 : 2, width: 14, height: 14, borderRadius: "50%", background: "#fff", transition: "left 0.15s", boxShadow: "0 1px 2px rgba(0,0,0,0.3)" }} />
            </button>
          </div>
        ))}
        {/* Font */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span className="uppercase-sm">Font</span>
          <div style={{ display: "flex", border: "1px solid var(--border)", borderRadius: 2 }}>
            {["inter","mono"].map(v => (
              <button key={v} onClick={() => setTweak("fontStack", v)} style={{
                background: tweaks.fontStack === v ? "var(--accent-dim)" : "transparent",
                color: tweaks.fontStack === v ? "var(--accent)" : "var(--muted)",
                border: "none", padding: "3px 10px", fontSize: 10, fontWeight: 600, letterSpacing: "0.04em",
                borderRight: v === "inter" ? "1px solid var(--border)" : "none",
              }}>{v}</button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Add-ticker popover ────────────────────────────────────────────────────────
function AddTickerPopover({ onAdd, onClose }: { onAdd: (ticker: string) => void, onClose: () => void }) {
  const [val, setVal] = useState("")
  const [err, setErr] = useState("")
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const sym = val.trim().toUpperCase()
    if (!sym) return
    setLoading(true)
    setErr("")
    try {
      const r = await fetch(BASE + "/watchlist/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: sym, name: "", asset_type: sym.includes("-") ? "crypto" : "stock" })
      })
      if (r.status === 409) { setErr("Already in watchlist"); setLoading(false); return }
      if (!r.ok) { setErr("Failed to add"); setLoading(false); return }
      onAdd(sym)
      onClose()
    } catch { setErr("Network error") }
    setLoading(false)
  }

  return (
    <div style={{ position: "fixed", top: 36, right: 12, zIndex: 200, background: "var(--surface-2)", border: "1px solid var(--border)", padding: 12, width: 260, boxShadow: "0 8px 24px rgba(0,0,0,0.5)" }}>
      <div className="uppercase-sm" style={{ marginBottom: 8 }}>Add to watchlist</div>
      <form onSubmit={handleSubmit}>
        <input autoFocus type="text" value={val} onChange={e => setVal(e.target.value)}
          placeholder="AAPL, BTC-USD, SPY…" className="mono"
          style={{ width: "100%", marginBottom: 8, fontSize: 12 }} />
        {err && <div style={{ fontSize: 10, color: "var(--down)", marginBottom: 6 }}>{err}</div>}
        <div style={{ display: "flex", gap: 6 }}>
          <button type="submit" disabled={loading} style={{ flex: 1, background: "var(--accent)", color: "var(--bg)", border: "none", padding: "5px 10px", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>
            {loading ? "Adding…" : "Add"}
          </button>
          <button type="button" onClick={onClose} className="btn-ghost">Cancel</button>
        </div>
      </form>
    </div>
  )
}

// ── Bottom status bar ─────────────────────────────────────────────────────────
function BottomStatus({ watchlistCount, onTweaks }: { watchlistCount: number, onTweaks: () => void }) {
  return (
    <div style={{ height: 24, display: "flex", alignItems: "center", borderTop: "1px solid var(--border)", background: "var(--bg)", padding: "0 14px", gap: 18, fontSize: 10.5, color: "var(--muted)" }}>
      <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
        <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--up)" }} />
        <span>Connected</span>
      </span>
      <span>{watchlistCount} subscriptions</span>
      <span>Educational use only · Not financial advice</span>
      <div style={{ flex: 1 }} />
      <span className="mono" style={{ color: "var(--dim)" }}>PulseTrader · v2.0 · Gemini</span>
      <button onClick={onTweaks} style={{ background: "transparent", border: "1px solid var(--border)", color: "var(--muted)", padding: "1px 7px", fontSize: 10, cursor: "pointer", letterSpacing: "0.04em" }}>
        ⚙ Tweaks
      </button>
    </div>
  )
}

// ── Root app ──────────────────────────────────────────────────────────────────
const TWEAK_DEFAULTS = {
  accentHue: 200,
  density: "normal",
  showTape: true,
  showRightRail: true,
  fontStack: "inter",
}

export default function Terminal() {
  const [selected, setSelected] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState("score")
  const [sortDesc, setSortDesc] = useState(true)
  const [query, setQuery] = useState("")
  const [timeframe, setTimeframe] = useState("3M")
  const [time, setTime] = useState("")
  const [addOpen, setAddOpen] = useState(false)
  const [tweaksOpen, setTweaksOpen] = useState(false)
  const [_lastRefreshed, setLastRefreshed] = useState("—")
  const [tweaks, setTweaksState] = useState(TWEAK_DEFAULTS)

  function setTweak(k: string, v: any) {
    setTweaksState(prev => ({ ...prev, [k]: v }))
  }

  // Apply accent hue
  useEffect(() => {
    document.documentElement.style.setProperty("--accent", `oklch(0.82 0.13 ${tweaks.accentHue})`)
    document.documentElement.style.setProperty("--accent-dim", `oklch(0.82 0.13 ${tweaks.accentHue} / 0.15)`)
  }, [tweaks.accentHue])

  // Apply density
  useEffect(() => {
    document.documentElement.style.setProperty("--row-h", tweaks.density === "compact" ? "24px" : "28px")
  }, [tweaks.density])

  // Apply font stack
  useEffect(() => {
    document.body.style.fontFamily = tweaks.fontStack === "mono"
      ? "'JetBrains Mono', ui-monospace, monospace"
      : "'Inter', system-ui, sans-serif"
  }, [tweaks.fontStack])

  const { data: rawWatchlist = [], mutate: mutateWatchlist } = useSWR("/watchlist/", fetcher, { refreshInterval: 30000 })
  const { data: alerts = [], mutate: mutateAlerts } = useSWR("/alerts/", fetcher, { refreshInterval: 30000 })
  const { data: macroRaw } = useSWR("/macro/snapshot", fetcher, { refreshInterval: 600000 })
  const macroSnap = macroRaw?.available ? macroRaw : null

  // Attach sparklines lazily from OHLCV
  const [sparks, setSparks] = useState<Record<string, number[]>>({})

  useEffect(() => {
    if (!rawWatchlist.length) return
    rawWatchlist.forEach(async (item: any) => {
      if (sparks[item.ticker]) return
      try {
        const r = await fetch(BASE + `/assets/${item.ticker}/ohlcv?period=1mo&interval=1d`)
        if (!r.ok) return
        const bars = await r.json()
        if (bars.length) {
          setSparks(prev => ({ ...prev, [item.ticker]: bars.map((b: any) => b.close) }))
        }
      } catch { /* ignore */ }
    })
  }, [rawWatchlist.map((w: any) => w.ticker).join(",")])

  const watchlist = rawWatchlist.map((w: any) => ({ ...w, _spark: sparks[w.ticker] ?? null }))

  useEffect(() => {
    setTime(formatTime(new Date()))
    const id = setInterval(() => {
      setTime(formatTime(new Date()))
    }, 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (rawWatchlist.length > 0) {
      setLastRefreshed(new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }))
    }
  }, [rawWatchlist])

  function handleAdd(_ticker: string) {
    mutateWatchlist()
    mutateAlerts()
  }

  function toggleSort(k: string) {
    if (sortKey === k) setSortDesc(d => !d)
    else { setSortKey(k); setSortDesc(true) }
  }

  const selectedItem = selected ? watchlist.find((w: any) => w.ticker === selected) : null

  const showRail = tweaks.showRightRail && !selectedItem
  const cols = showRail ? "300px 1fr 280px" : "300px 1fr"

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", overflow: "hidden", fontFamily: "'Inter', system-ui, sans-serif", fontSize: 12, lineHeight: 1.45, letterSpacing: "-0.005em", background: "var(--bg)", color: "var(--text)" }}>
      <StatusBar time={time} watchlist={watchlist} alerts={alerts} onOpenAdd={() => setAddOpen(o => !o)} addOpen={addOpen} onHome={() => setSelected(null)} />
      {tweaks.showTape && <TickerTape watchlist={watchlist} />}

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: cols, overflow: "hidden" }}>
        {/* Watchlist */}
        <div style={{ borderRight: "1px solid var(--border)", overflow: "hidden" }}>
          <WatchlistPanel
            items={watchlist} selected={selected} onSelect={setSelected}
            sortKey={sortKey} sortDesc={sortDesc} onSortChange={toggleSort}
            query={query} setQuery={setQuery}
          />
        </div>

        {/* Center */}
        <div style={{ overflow: "hidden", display: "flex", flexDirection: "column" }}>
          {selectedItem ? (
            <AssetDetail
              ticker={selected!}
              watchlistItem={selectedItem}
              onBack={() => setSelected(null)}
              timeframe={timeframe}
              setTimeframe={setTimeframe}
            />
          ) : (
            <MarketOverview watchlist={watchlist} alerts={alerts} onSelect={setSelected} />
          )}
        </div>

        {/* Right rail — only on overview */}
        {showRail && (
          <div style={{ borderLeft: "1px solid var(--border)", overflow: "hidden", display: "flex", flexDirection: "column", background: "var(--surface-1)" }}>
            <RightRail onSelect={setSelected} watchlist={watchlist} alerts={alerts} macroSnap={macroSnap} />
          </div>
        )}
      </div>

      <BottomStatus watchlistCount={watchlist.length} onTweaks={() => setTweaksOpen(o => !o)} />

      {addOpen && <AddTickerPopover onAdd={handleAdd} onClose={() => setAddOpen(false)} />}
      {tweaksOpen && <TweaksPanel tweaks={tweaks} setTweak={setTweak} onClose={() => setTweaksOpen(false)} />}
    </div>
  )
}
