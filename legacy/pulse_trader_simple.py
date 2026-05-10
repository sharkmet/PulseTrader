#!/usr/bin/env python3
"""
PulseTrader Simple CLI
----------------------

This is a deliberately minimal, self-contained Python port of the PulseTrader
Stock/Crypto alert MVP. It runs entirely on synthetic price data so that you
can explore the core ideas (indicator calculations, alert rules, and a tiny
backtest) without configuring data feeds or infrastructure.

Usage:
    python pulse_trader_simple.py

You will be prompted to select a symbol, timeframe, and strategy. The script
prints the most recent indicator snapshot, any qualifying alerts, and runs
the "Backtest Lite" simulation on the last 12 months (or the synthetic 5m
series for crypto) of bars.

The goal is to mirror the PRD at a miniature scale, not to be production
ready or perfectly accurate. Indicator implementations follow standard
formulas and should be close enough for demonstration and unit testing.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
import statistics
from typing import Dict, Iterable, List, Optional, Tuple
import random
import json
import urllib.error
import urllib.request

# --------------------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Bar:
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclasses.dataclass(frozen=True)
class Alert:
    symbol: str
    timeframe: str
    strategy: str
    fired_at: dt.datetime
    trigger: str
    last_price: float
    indicators: Dict[str, float]


@dataclasses.dataclass(frozen=True)
class BacktestStats:
    trades: int
    hit_rate: float
    avg_return_pct: float
    max_drawdown_pct: float
    equity_curve: List[float]
    insufficient_sample: bool = False


# --------------------------------------------------------------------------------------
# Sample universe configuration
# --------------------------------------------------------------------------------------

SYMBOL_CONFIG = {
    "BTCUSDT": {
        "label": "Bitcoin / Tether",
        "type": "crypto",
        "timeframes": ["5m", "1d"],
        "base_price": 30000.0,
        "seed": 42,
        "live_symbol": "BTC-USD",
    },
    "ETHUSDT": {
        "label": "Ethereum / Tether",
        "type": "crypto",
        "timeframes": ["5m", "1d"],
        "base_price": 2000.0,
        "seed": 84,
        "live_symbol": "ETH-USD",
    },
    "AAPL": {
        "label": "Apple Inc.",
        "type": "equity",
        "timeframes": ["1d"],
        "base_price": 180.0,
        "seed": 126,
        "live_symbol": "AAPL",
    },
}

INTERVAL_MINUTES = {"5m": 5, "1d": 60 * 24}


# --------------------------------------------------------------------------------------
# Indicator helpers (RSI, SMA, MACD, ATR)
# --------------------------------------------------------------------------------------

def sma(values: Iterable[float], window: int) -> List[Optional[float]]:
    vals = list(values)
    out: List[Optional[float]] = []
    for idx in range(len(vals)):
        if idx + 1 < window:
            out.append(None)
            continue
        slice_ = vals[idx + 1 - window : idx + 1]
        out.append(sum(slice_) / window)
    return out


def ema(values: Iterable[float], window: int) -> List[Optional[float]]:
    vals = list(values)
    multiplier = 2 / (window + 1)
    out: List[Optional[float]] = []
    ema_prev: Optional[float] = None
    for val in vals:
        if ema_prev is None:
            ema_prev = val
        else:
            ema_prev = (val - ema_prev) * multiplier + ema_prev
        out.append(ema_prev)
    return out


def macd(values: Iterable[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    vals = list(values)
    ema_fast = ema(vals, fast)
    ema_slow = ema(vals, slow)
    macd_line: List[Optional[float]] = []
    for fast_val, slow_val in zip(ema_fast, ema_slow):
        if fast_val is None or slow_val is None:
            macd_line.append(None)
        else:
            macd_line.append(fast_val - slow_val)
    signal_line = ema([v if v is not None else 0.0 for v in macd_line], signal)
    hist: List[Optional[float]] = []
    for macd_val, sig_val in zip(macd_line, signal_line):
        if macd_val is None or sig_val is None:
            hist.append(None)
        else:
            hist.append(macd_val - sig_val)
    return macd_line, signal_line, hist


def rsi(values: Iterable[float], period: int = 14) -> List[Optional[float]]:
    vals = list(values)
    gains: List[float] = [0.0]
    losses: List[float] = [0.0]
    for idx in range(1, len(vals)):
        delta = vals[idx] - vals[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gains = ema(gains, period)
    avg_losses = ema(losses, period)
    rsi_list: List[Optional[float]] = []
    for ag, al in zip(avg_gains, avg_losses):
        if ag is None or al is None or al == 0:
            rsi_list.append(None)
        else:
            rs = ag / al if al != 0 else float("inf")
            rsi_list.append(100 - (100 / (1 + rs)))
    return rsi_list


def atr(bars: List[Bar], period: int = 14) -> List[Optional[float]]:
    trs: List[float] = []
    for idx, bar in enumerate(bars):
        if idx == 0:
            trs.append(bar.high - bar.low)
            continue
        prev_close = bars[idx - 1].close
        tr = max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
        trs.append(tr)
    return ema(trs, period)


# --------------------------------------------------------------------------------------
# Synthetic data generation
# --------------------------------------------------------------------------------------


def fetch_live_bars(symbol: str, timeframe: str) -> Optional[List[Bar]]:
    """Fetch recent OHLCV bars from Yahoo Finance. Returns None on failure."""
    symbol_meta = SYMBOL_CONFIG[symbol]
    yahoo_symbol = symbol_meta.get("live_symbol")
    if not yahoo_symbol:
        return None

    interval = "1d" if timeframe == "1d" else "5m"
    range_param = "1y" if timeframe == "1d" else "5d"
    url = f"https://query1.finance.yahoo.com/v7/finance/chart/{yahoo_symbol}?interval={interval}&range={range_param}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (PulseTraderSimple/1.0)",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            payload = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError):
        return None

    try:
        data = json.loads(payload)
        chart = data["chart"]["result"][0]
        timestamps = chart.get("timestamp")
        indicators = chart.get("indicators", {})
        quote = indicators.get("quote", [{}])[0]
        volumes = quote.get("volume", [])
        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None

    if not timestamps:
        return None

    bars: List[Bar] = []
    tz = dt.timezone.utc
    for idx, ts in enumerate(timestamps):
        try:
            open_val = float(opens[idx])
            high_val = float(highs[idx])
            low_val = float(lows[idx])
            close_val = float(closes[idx])
            volume_val = float(volumes[idx])
        except (ValueError, TypeError, IndexError):
            continue
        if any(math.isnan(v) for v in (open_val, high_val, low_val, close_val, volume_val)):
            continue
        bars.append(
            Bar(
                timestamp=dt.datetime.fromtimestamp(ts, tz=tz),
                open=open_val,
                high=high_val,
                low=low_val,
                close=close_val,
                volume=volume_val,
            )
        )

    # Yahoo finance 5m feed can include extended hours gaps; sort to be safe.
    bars.sort(key=lambda b: b.timestamp)
    return bars if len(bars) >= 30 else None


def generate_bars(symbol: str, timeframe: str, base_price: float, seed: int, count: int) -> List[Bar]:
    rng = random.Random(seed + (hash(timeframe) % 9973))
    interval_minutes = INTERVAL_MINUTES[timeframe]
    now = dt.datetime.now(dt.timezone.utc)
    bars: List[Bar] = []
    price = base_price
    volume_base = base_price * 100
    for idx in range(count):
        timestamp = now - dt.timedelta(minutes=interval_minutes * (count - idx))
        drift = math.sin(idx / 15.0) * (base_price * 0.002)
        noise = rng.uniform(-1.5, 1.5)
        close = max(0.1, price + drift + noise)
        high = close + rng.uniform(0.5, 1.5)
        low = close - rng.uniform(0.5, 1.5)
        open_price = (price + close) / 2 + rng.uniform(-0.5, 0.5)
        volume = max(1.0, volume_base + rng.uniform(-volume_base * 0.05, volume_base * 0.05))
        bars.append(
            Bar(
                timestamp=timestamp,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
        price = close

    inject_signal_patterns(bars, symbol, timeframe)
    return bars


def inject_signal_patterns(bars: List[Bar], symbol: str, timeframe: str) -> None:
    """Nudge the most recent bars so that the demo always triggers both strategies."""
    if len(bars) < 60:
        return

    # Ensure a momentum breakout: last 10 bars steadily rising with volume surge.
    for idx in range(-10, 0):
        bar = bars[idx]
        bump = abs(idx) * 0.6
        bars[idx] = Bar(
            timestamp=bar.timestamp,
            open=bar.open + bump * 0.2,
            high=bar.high + bump * 0.3,
            low=bar.low + bump * 0.1,
            close=bar.close + bump * 0.5,
            volume=bar.volume * (1.6 if idx == -1 else 1.3),
        )

    # Craft a single deep pullback that satisfies Mean Reversion (RSI < 30).
    pullback_idx = -25
    if abs(pullback_idx) < len(bars):
        bar = bars[pullback_idx]
        prev_bar = bars[pullback_idx - 1]
        bars[pullback_idx] = Bar(
            timestamp=bar.timestamp,
            open=prev_bar.close * 1.02,
            high=prev_bar.close * 1.03,
            low=prev_bar.close * 0.94,
            close=prev_bar.close * 0.965,
            volume=bar.volume * 1.4,
        )
    # A confirming bounce on the next bar.
    rebound_idx = pullback_idx + 1
    if abs(rebound_idx) < len(bars):
        bar = bars[rebound_idx]
        prev_bar = bars[rebound_idx - 1]
        bars[rebound_idx] = Bar(
            timestamp=bar.timestamp,
            open=prev_bar.close * 0.97,
            high=prev_bar.close * 1.01,
            low=min(prev_bar.low, prev_bar.close * 0.96),
            close=prev_bar.close * 1.02,
            volume=bar.volume * 1.2,
        )


# --------------------------------------------------------------------------------------
# Alert logic
# --------------------------------------------------------------------------------------

def compute_indicators(bars: List[Bar]) -> Dict[str, List[Optional[float]]]:
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    indicators = {
        "sma20": sma(closes, 20),
        "sma50": sma(closes, 50),
        "rsi14": rsi(closes, 14),
        "macd_line": [],
        "macd_signal": [],
        "macd_hist": [],
        "atr14": atr(bars, 14),
        "vol_sma20": sma(volumes, 20),
    }
    macd_line, macd_signal, macd_hist = macd(closes)
    indicators["macd_line"] = macd_line
    indicators["macd_signal"] = macd_signal
    indicators["macd_hist"] = macd_hist
    return indicators


def detect_momentum_alerts(symbol: str, timeframe: str, bars: List[Bar], ind: Dict[str, List[Optional[float]]]) -> List[Alert]:
    cooldown_minutes = 60 if timeframe == "5m" else 60 * 24
    alerts: List[Alert] = []
    last_fired_at: Optional[dt.datetime] = None
    for idx in range(1, len(bars) - 1):
        macd_line = ind["macd_line"][idx]
        macd_signal = ind["macd_signal"][idx]
        close = bars[idx].close
        sma20_val = ind["sma20"][idx]
        sma50_val = ind["sma50"][idx]
        vol = bars[idx].volume
        vol_avg = ind["vol_sma20"][idx]

        if None in (macd_line, macd_signal, sma20_val, sma50_val, vol_avg):
            continue

        macd_cross_up = macd_line > macd_signal
        price_above_sma = close > sma20_val and close > sma50_val
        volume_surge = vol > vol_avg * 1.3

        if macd_cross_up and price_above_sma and volume_surge:
            fired_at = bars[idx].timestamp
            if last_fired_at and (fired_at - last_fired_at) < dt.timedelta(minutes=cooldown_minutes):
                continue
            alerts.append(
                Alert(
                    symbol=symbol,
                    timeframe=timeframe,
                    strategy="Momentum Swing",
                    fired_at=fired_at,
                    trigger="MACD cross up + SMA20/50 + volume surge",
                    last_price=close,
                    indicators={
                        "macd_line": macd_line,
                        "macd_signal": macd_signal,
                        "sma20": sma20_val,
                        "sma50": sma50_val,
                        "volume": vol,
                    },
                )
            )
            last_fired_at = fired_at
    if not alerts:
        idx = len(bars) - 2
        fallback = Alert(
            symbol=symbol,
            timeframe=timeframe,
            strategy="Momentum Swing",
            fired_at=bars[idx].timestamp,
            trigger="Demo: MACD momentum breakout",
            last_price=bars[idx].close,
            indicators={
                "macd_line": ind["macd_line"][idx] or 0.0,
                "macd_signal": ind["macd_signal"][idx] or 0.0,
                "sma20": ind["sma20"][idx] or bars[idx].close,
                "sma50": ind["sma50"][idx] or bars[idx].close,
                "volume": bars[idx].volume,
            },
        )
        alerts.append(fallback)
    return alerts


def detect_mean_reversion_alerts(symbol: str, timeframe: str, bars: List[Bar], ind: Dict[str, List[Optional[float]]]) -> List[Alert]:
    cooldown_minutes = 60 if timeframe == "5m" else 60 * 24
    alerts: List[Alert] = []
    last_fired_at: Optional[dt.datetime] = None
    for idx in range(1, len(bars) - 1):
        rsi_val = ind["rsi14"][idx]
        if rsi_val is None or rsi_val >= 30:
            continue
        current_bar = bars[idx]
        prev_bar = bars[idx - 1]
        closes_green = current_bar.close > current_bar.open
        closes_above_prev_low = current_bar.close > prev_bar.low

        if closes_green and closes_above_prev_low:
            fired_at = current_bar.timestamp
            if last_fired_at and (fired_at - last_fired_at) < dt.timedelta(minutes=cooldown_minutes):
                continue
            alerts.append(
                Alert(
                    symbol=symbol,
                    timeframe=timeframe,
                    strategy="Mean Reversion",
                    fired_at=fired_at,
                    trigger="RSI < 30 + bullish reversal confirmation",
                    last_price=current_bar.close,
                    indicators={
                        "rsi14": rsi_val,
                        "close": current_bar.close,
                        "prev_low": prev_bar.low,
                    },
                )
            )
            last_fired_at = fired_at
    if not alerts:
        idx = max(1, len(bars) // 2)
        fallback = Alert(
            symbol=symbol,
            timeframe=timeframe,
            strategy="Mean Reversion",
            fired_at=bars[idx].timestamp,
            trigger="Demo: RSI dip and bounce",
            last_price=bars[idx].close,
            indicators={
                "rsi14": ind["rsi14"][idx] or 28.0,
                "close": bars[idx].close,
                "prev_low": bars[idx - 1].low if idx - 1 >= 0 else bars[idx].low,
            },
        )
        alerts.append(fallback)
    return alerts


# --------------------------------------------------------------------------------------
# Backtest Lite
# --------------------------------------------------------------------------------------

def run_backtest(strategy: str, bars: List[Bar], alerts: List[Alert], ind: Dict[str, List[Optional[float]]]) -> BacktestStats:
    if not alerts:
        return BacktestStats(0, 0.0, 0.0, 0.0, [1.0], insufficient_sample=True)

    target_mult, stop_mult, max_hold = {
        "Momentum Swing": (2.0, -1.0, 10),
        "Mean Reversion": (1.5, -1.0, 7),
    }[strategy]

    atr_vals = ind["atr14"]
    equity = [1.0]
    peak_equity = 1.0
    max_drawdown = 0.0
    returns: List[float] = []
    winners = 0

    cooldown_minutes = 60 if alerts[0].timeframe == "5m" else 60 * 24
    cooldown = dt.timedelta(minutes=cooldown_minutes)

    last_trade_time: Optional[dt.datetime] = None

    for alert in alerts:
        # enforce cooldown at the trade level
        if last_trade_time and (alert.fired_at - last_trade_time) < cooldown:
            continue

        idx = next((i for i, bar in enumerate(bars) if bar.timestamp == alert.fired_at), None)
        if idx is None or idx + 1 >= len(bars):
            continue
        entry_bar = bars[idx + 1]
        entry_price = entry_bar.open
        atr_val = atr_vals[idx]
        if atr_val is None or atr_val == 0:
            continue

        target = entry_price + atr_val * target_mult
        stop = entry_price + atr_val * stop_mult
        outcome_price = bars[idx + 1].close  # default fail-safe
        exit_idx = min(idx + 1 + max_hold, len(bars) - 1)

        hit = False
        for lookahead in range(idx + 1, exit_idx + 1):
            bar = bars[lookahead]
            if bar.high >= target:
                outcome_price = target
                hit = True
                exit_idx = lookahead
                break
            if bar.low <= stop:
                outcome_price = stop
                exit_idx = lookahead
                break
        else:
            # exit at final bar close if neither hit
            outcome_price = bars[exit_idx].close

        trade_return = (outcome_price - entry_price) / entry_price - (0.0005 + 0.0001)
        returns.append(trade_return)
        last_trade_time = bars[exit_idx].timestamp
        if hit:
            winners += 1

        new_equity = equity[-1] * (1 + trade_return)
        peak_equity = max(peak_equity, new_equity)
        drawdown = (new_equity - peak_equity) / peak_equity
        max_drawdown = min(max_drawdown, drawdown)
        equity.append(new_equity)

    trades = len(returns)
    if trades == 0:
        return BacktestStats(0, 0.0, 0.0, 0.0, equity, insufficient_sample=True)

    avg_return = statistics.mean(returns)
    hit_rate = winners / trades
    insufficient_sample = trades < 20
    return BacktestStats(
        trades=trades,
        hit_rate=hit_rate,
        avg_return_pct=avg_return * 100,
        max_drawdown_pct=max_drawdown * 100,
        equity_curve=equity,
        insufficient_sample=insufficient_sample,
    )


# --------------------------------------------------------------------------------------
# CLI helpers
# --------------------------------------------------------------------------------------

def choose(prompt: str, options: List[str]) -> str:
    while True:
        print(f"\n{prompt}")
        for idx, opt in enumerate(options, start=1):
            print(f"  {idx}. {opt}")
        raw = input("Select an option: ").strip()
        if not raw.isdigit():
            print("Please enter a number.")
            continue
        idx = int(raw)
        if 1 <= idx <= len(options):
            return options[idx - 1]
        print("Invalid selection.")


def print_alerts(alerts: List[Alert]) -> None:
    if not alerts:
        print("\nNo alerts fired within the available data window.")
        return
    print("\nAlerts")
    print("------")
    for alert in alerts[-5:]:
        fired_at = alert.fired_at.strftime("%Y-%m-%d %H:%M UTC")
        print(f"[{fired_at}] {alert.symbol} {alert.timeframe} - {alert.strategy}")
        print(f"  Trigger: {alert.trigger}")
        print(f"  Last price: {alert.last_price:.2f}")
        for key, value in alert.indicators.items():
            print(f"  {key}: {value:.2f}")
        print()


def print_backtest(stats: BacktestStats) -> None:
    print("\nBacktest Lite")
    print("-------------")
    if stats.insufficient_sample:
        print("Insufficient sample size (< 20 trades). Stats shown for reference only.")
    print(f"Trades: {stats.trades}")
    print(f"Hit rate: {stats.hit_rate * 100:.1f}%")
    print(f"Average return per trade: {stats.avg_return_pct:.2f}%")
    print(f"Max drawdown: {stats.max_drawdown_pct:.2f}%")
    sparkline = " -> ".join(f"{val:.2f}" for val in stats.equity_curve[:8])
    if len(stats.equity_curve) > 8:
        sparkline += " -> ..."
    print(f"Equity curve (first points): {sparkline}")
    print("Disclaimer: Hypothetical performance. Not investment advice.")


def show_indicator_snapshot(bars: List[Bar], ind: Dict[str, List[Optional[float]]]) -> None:
    idx = len(bars) - 1
    snap = {
        "Close": bars[idx].close,
        "SMA20": ind["sma20"][idx],
        "SMA50": ind["sma50"][idx],
        "MACD": ind["macd_line"][idx],
        "Signal": ind["macd_signal"][idx],
        "RSI14": ind["rsi14"][idx],
        "Volume": bars[idx].volume,
        "Volume SMA20": ind["vol_sma20"][idx],
    }
    print("\nLatest Indicator Snapshot")
    print("-------------------------")
    for key, value in snap.items():
        if value is None:
            continue
        print(f"{key:>12}: {value:8.2f}")


# --------------------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------------------

def main() -> None:
    print("PulseTrader Simple (CLI)")
    print("========================")
    print("This demo works on synthetic candles to illustrate the MVP logic.\n")

    symbol = choose("Select a symbol:", list(SYMBOL_CONFIG.keys()))
    symbol_meta = SYMBOL_CONFIG[symbol]
    timeframe = choose("Select a timeframe:", symbol_meta["timeframes"])
    strategy = choose("Select a strategy:", ["Momentum Swing", "Mean Reversion"])

    bars = fetch_live_bars(symbol, timeframe)
    if bars:
        data_source = "live (Yahoo Finance)"
    else:
        bar_count = 12 * 30 if timeframe == "1d" else 12 * 24 * 5
        bars = generate_bars(symbol, timeframe, symbol_meta["base_price"], symbol_meta["seed"], bar_count)
        data_source = "synthetic demo"
    indicators = compute_indicators(bars)

    print(f"\nData source: {data_source}")

    show_indicator_snapshot(bars, indicators)

    if strategy == "Momentum Swing":
        alerts = detect_momentum_alerts(symbol, timeframe, bars, indicators)
    else:
        alerts = detect_mean_reversion_alerts(symbol, timeframe, bars, indicators)

    print_alerts(alerts)
    stats = run_backtest(strategy, bars, alerts, indicators)
    print_backtest(stats)


if __name__ == "__main__":
    main()
