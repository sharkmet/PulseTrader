"""
PulseTrader CLI

Usage:
    pulse backtest --ticker AAPL --from 2022-01-01 --to 2024-12-31
    pulse backtest --ticker BTC-USD --from 2023-01-01 --to 2024-12-31 --step 5
    pulse backtest --ticker AAPL --from 2022-01-01 --to 2024-12-31 --output markdown
"""
from __future__ import annotations

import argparse
import json
import sys


def _cmd_backtest(args: argparse.Namespace) -> None:
    """Run a backtest and print results to stdout."""
    from backend.app.services.backtester import fetch_backtest_bars, run_backtest

    print(f"Downloading {args.ticker} bars {args.from_date} → {args.to_date}...", file=sys.stderr)
    all_bars = fetch_backtest_bars(
        ticker=args.ticker.upper(),
        from_date=args.from_date,
        to_date=args.to_date,
    )

    if not all_bars:
        print(f"ERROR: No data found for {args.ticker}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Running backtest: {len(all_bars)} total bars, step={args.step} day(s)...",
        file=sys.stderr,
    )

    result = run_backtest(
        ticker=args.ticker.upper(),
        all_bars=all_bars,
        from_date=args.from_date,
        to_date=args.to_date,
        step_days=args.step,
        min_lookback=args.min_lookback,
    )

    if result.status == "failed":
        print(f"ERROR: {result.error}", file=sys.stderr)
        sys.exit(1)

    m = result.metrics
    print(f"\nCompleted: {result.metrics.n_predictions} predictions", file=sys.stderr)

    if args.output == "markdown":
        print(result.report_markdown or "No report generated")
    elif args.output == "json":
        payload = result.model_dump(
            exclude={"data_points": True} if not args.include_data else {},
            mode="json",
        )
        print(json.dumps(payload, indent=2, default=str))
    else:  # summary
        print("\n=== PulseTrader Backtest Summary ===")
        print(f"Ticker:              {result.ticker}")
        print(f"Period:              {result.from_date} → {result.to_date}")
        print(f"Scoring version:     {result.scoring_version}")
        print(f"Predictions:         {m.n_predictions}")
        print(f"Mean score:          {m.mean_score:.1f} (std {m.score_std:.1f})")
        print(f"1-day accuracy:      {m.accuracy_1d*100:.1f}%" if m.accuracy_1d else "1-day accuracy:      N/A")
        print(f"7-day accuracy:      {m.accuracy_7d*100:.1f}%" if m.accuracy_7d else "7-day accuracy:      N/A")
        print(f"1-day Sharpe:        {m.sharpe_1d:.3f}" if m.sharpe_1d else "1-day Sharpe:        N/A")
        print(f"Max drawdown:        {m.max_drawdown_pct:.1f}%" if m.max_drawdown_pct else "Max drawdown:        N/A")
        print("\nCalibration (score bucket → mean 7d return):")
        for b in m.calibration:
            ret_str = f"{b.mean_return_7d*100:+.2f}%" if b.mean_return_7d is not None else "N/A"
            print(f"  {b.bucket:>6}: {ret_str:>8}  (n={b.n})")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pulse",
        description="PulseTrader CLI — educational stock & crypto analysis",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backtest subcommand
    bt = sub.add_parser("backtest", help="Run a historical backtest")
    bt.add_argument("--ticker", required=True, help="Ticker symbol (AAPL, BTC-USD)")
    bt.add_argument("--from", dest="from_date", required=True,
                    metavar="YYYY-MM-DD", help="Backtest start date")
    bt.add_argument("--to", dest="to_date", required=True,
                    metavar="YYYY-MM-DD", help="Backtest end date")
    bt.add_argument("--step", type=int, default=1,
                    help="Score every N trading days (default: 1 = daily)")
    bt.add_argument("--min-lookback", type=int, default=60,
                    help="Minimum bars before computing indicators (default: 60)")
    bt.add_argument("--output", choices=["summary", "json", "markdown"],
                    default="summary", help="Output format")
    bt.add_argument("--include-data", action="store_true",
                    help="Include per-bar data points in JSON output")
    bt.set_defaults(func=_cmd_backtest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
