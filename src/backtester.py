"""
Elastic Scanner — Backtester

Walks through historical data bar-by-bar, detects signals using the
full Elastic engine, opens hypothetical trades, and exits on ATR trail
flip or time stop. Measures win rate, R-multiple, and profit factor
per signal type.

Usage:
    python -m src.backtester
    python -m src.backtester --tickers AAPL MSFT NVDA --period 2y

Limitations:
    - Uses Daily as primary TF (yfinance limits intraday to 60 days)
    - Weekly + Monthly for MTF confirmation
    - No pyramiding (one trade per ticker at a time)
"""

import argparse
import logging
import sys
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import FALLBACK_TICKERS, OUTPUT_DIR
from src.elastic_engine import process_ticker_tf
from src.signal_engine import classify_signal, extract_last_bar
from src.scoring import score_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DISCLAIMER = "EDUCATIONAL ANALYSIS ONLY. NOT FINANCIAL ADVICE. NOT A RECOMMENDATION TO BUY, SELL, OR HOLD."

# All tiers except SUPPRESS are actionable for backtest (SUPPRESS = score < 70)
ACTIONABLE = {"ELITE", "FIRE", "PREP", "SUPPRESS"}

# Max holding period (bars) before forced exit
MAX_HOLD = 40


# ─── Trade Record ─────────────────────────────────────────────────
@dataclass
class Trade:
    ticker: str
    signal: str
    direction: str       # BULL or BEAR
    score: int
    entry_bar: int       # bar index of entry
    entry_price: float   # next bar's open
    trail_at_entry: float
    initial_risk: float  # abs(entry - trail)
    exit_bar: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    r_multiple: float = 0.0
    holding_period: int = 0
    pnl_pct: float = 0.0


# ─── Backtest Engine ──────────────────────────────────────────────
def backtest_ticker(ticker: str, df_1d: pd.DataFrame,
                    df_1w: pd.DataFrame, df_1m: pd.DataFrame) -> list[Trade]:
    """
    Walk through daily bars for a single ticker, detect signals,
    open trades, and exit on trail flip or time stop.
    """
    # Run elastic engine on all TFs
    try:
        enriched_1d = process_ticker_tf(df_1d)
        enriched_1w = process_ticker_tf(df_1w)
        enriched_1m = process_ticker_tf(df_1m) if df_1m is not None and len(df_1m) >= 40 else None
    except Exception as e:
        logger.debug(f"{ticker}: engine failed — {e}")
        return []

    if enriched_1d.empty or enriched_1w.empty:
        return []

    # We need to align weekly/monthly to daily dates
    # For each daily bar, find the most recent weekly/monthly bar
    weekly_data = _build_mtf_lookup(enriched_1w)
    monthly_data = _build_mtf_lookup(enriched_1m) if enriched_1m is not None and not enriched_1m.empty else None

    trades = []
    open_trade = None
    n = len(enriched_1d)

    # Start at bar 40 to ensure warmup
    for i in range(40, n - 1):  # -1 because we enter at bar i+1
        bar = enriched_1d.iloc[i]
        next_bar = enriched_1d.iloc[i + 1]

        # ─── Check exit for open trade ────────────────────────────
        if open_trade is not None:
            bars_held = i - open_trade.entry_bar
            trail_flipped = False

            if open_trade.direction == "BULL":
                # Exit if trail flips to bearish (close drops below trail)
                if not bar.get("is_bull", True):
                    trail_flipped = True
            else:
                # Exit if trail flips to bullish
                if bar.get("is_bull", False):
                    trail_flipped = True

            should_exit = trail_flipped or bars_held >= MAX_HOLD

            if should_exit:
                exit_price = float(bar["Close"])
                open_trade.exit_bar = i
                open_trade.exit_price = exit_price
                open_trade.holding_period = bars_held
                open_trade.exit_reason = "trail_flip" if trail_flipped else "time_stop"

                # R-multiple calculation
                if open_trade.initial_risk > 0:
                    if open_trade.direction == "BULL":
                        raw_pnl = exit_price - open_trade.entry_price
                    else:
                        raw_pnl = open_trade.entry_price - exit_price
                    open_trade.r_multiple = round(raw_pnl / open_trade.initial_risk, 2)
                    open_trade.pnl_pct = round(raw_pnl / open_trade.entry_price * 100, 2)

                trades.append(open_trade)
                open_trade = None

        # ─── Check for new signal (only if no open trade) ─────────
        if open_trade is not None:
            continue

        # Get MTF data for this date
        bar_date = enriched_1d.index[i]
        data_1d = extract_last_bar(enriched_1d.iloc[:i + 1])
        data_1w = _get_mtf_at_date(weekly_data, bar_date)
        data_1m = _get_mtf_at_date(monthly_data, bar_date) if monthly_data else None

        if data_1d is None or data_1w is None:
            continue

        # Classify signal
        signal = classify_signal(ticker, data_1d, data_1d, data_1w, data_1m)
        if signal is None:
            continue

        signal = score_signal(signal)

        # Trade all scored signals for backtest analysis
        # (scoring engine already filters weak signals to low scores)

        # ─── Open trade at next bar's open ────────────────────────
        entry_price = float(next_bar["Open"])
        trail_val = float(bar.get("trail", 0))

        if entry_price <= 0 or trail_val <= 0:
            continue

        initial_risk = abs(entry_price - trail_val)
        min_risk = entry_price * 0.005  # minimum 0.5% of price
        initial_risk = max(initial_risk, min_risk)
        if initial_risk <= 0:
            continue

        open_trade = Trade(
            ticker=ticker,
            signal=signal["tier"],
            direction=signal["direction"],
            score=signal["score"],
            entry_bar=i + 1,
            entry_price=entry_price,
            trail_at_entry=trail_val,
            initial_risk=initial_risk,
        )

    # Close any remaining open trade at last bar
    if open_trade is not None:
        last_bar = enriched_1d.iloc[-1]
        exit_price = float(last_bar["Close"])
        open_trade.exit_bar = n - 1
        open_trade.exit_price = exit_price
        open_trade.holding_period = (n - 1) - open_trade.entry_bar
        open_trade.exit_reason = "end_of_data"
        if open_trade.initial_risk > 0:
            if open_trade.direction == "BULL":
                raw_pnl = exit_price - open_trade.entry_price
            else:
                raw_pnl = open_trade.entry_price - exit_price
            open_trade.r_multiple = round(raw_pnl / open_trade.initial_risk, 2)
            open_trade.pnl_pct = round(raw_pnl / open_trade.entry_price * 100, 2)
        trades.append(open_trade)

    return trades


def _build_mtf_lookup(df: pd.DataFrame) -> list[dict]:
    """Pre-compute extract_last_bar for each bar in a TF DataFrame."""
    results = []
    for i in range(len(df)):
        bar_data = extract_last_bar(df.iloc[:i + 1])
        if bar_data:
            results.append({"date": df.index[i], "data": bar_data})
    return results


def _get_mtf_at_date(lookup: list[dict], target_date) -> dict | None:
    """Get the most recent MTF data at or before target_date."""
    if not lookup:
        return None
    best = None
    for item in lookup:
        if item["date"] <= target_date:
            best = item["data"]
    return best


# ─── Data Download ────────────────────────────────────────────────
def download_backtest_data(tickers: list[str], period: str = "1y"):
    """Download daily, weekly, monthly data for backtesting."""
    data = {}

    configs = {
        "1D": {"interval": "1d", "period": period},
        "1W": {"interval": "1wk", "period": "2y"},
        "1M": {"interval": "1mo", "period": "5y"},
    }

    for tf_name, cfg in configs.items():
        logger.info(f"Downloading {tf_name} data ({cfg['interval']}, {cfg['period']})...")
        t0 = time.time()

        try:
            raw = yf.download(
                tickers,
                period=cfg["period"],
                interval=cfg["interval"],
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception as e:
            logger.error(f"Failed to download {tf_name}: {e}")
            continue

        logger.info(f"  -> Downloaded in {time.time() - t0:.1f}s")

        for ticker in tickers:
            if ticker not in data:
                data[ticker] = {}
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    df = raw[ticker].dropna(how="all").copy()
                else:
                    df = raw.dropna(how="all").copy()
                if not df.empty and len(df) >= 40:
                    data[ticker][tf_name] = df
            except Exception:
                continue

    return data


# ─── Results Analysis ─────────────────────────────────────────────
def analyze_results(trades: list[Trade]) -> dict:
    """Compute aggregate metrics from trade list."""
    if not trades:
        return {"total": 0}

    wins = [t for t in trades if t.r_multiple > 0]
    losses = [t for t in trades if t.r_multiple <= 0]

    gross_profit = sum(t.r_multiple for t in wins) if wins else 0
    gross_loss = abs(sum(t.r_multiple for t in losses)) if losses else 0.001

    # Max drawdown in cumulative R
    cum_r = np.cumsum([t.r_multiple for t in trades])
    peak = np.maximum.accumulate(cum_r)
    drawdown = peak - cum_r
    max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0

    return {
        "total": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_r": round(np.mean([t.r_multiple for t in trades]), 2),
        "median_r": round(np.median([t.r_multiple for t in trades]), 2),
        "best_r": round(max(t.r_multiple for t in trades), 2),
        "worst_r": round(min(t.r_multiple for t in trades), 2),
        "profit_factor": round(gross_profit / gross_loss, 2),
        "max_drawdown_r": round(max_dd, 2),
        "avg_hold": round(np.mean([t.holding_period for t in trades]), 1),
        "total_r": round(sum(t.r_multiple for t in trades), 2),
    }


def print_results(all_trades: list[Trade]):
    """Print formatted backtest results."""
    print("\n" + "=" * 75)
    print("  ELASTIC SCANNER — BACKTEST RESULTS")
    print("=" * 75)

    # Overall
    stats = analyze_results(all_trades)
    if stats["total"] == 0:
        print("  No trades generated.")
        print("=" * 75)
        return

    print(f"\n  OVERALL ({stats['total']} trades)")
    _print_stats(stats)

    # By signal type
    signal_types = sorted(set(t.signal for t in all_trades))
    for sig in signal_types:
        sig_trades = [t for t in all_trades if t.signal == sig]
        sig_stats = analyze_results(sig_trades)
        if sig_stats["total"] > 0:
            print(f"\n  {sig} ({sig_stats['total']} trades)")
            _print_stats(sig_stats)

    # By direction
    for direction in ["BULL", "BEAR"]:
        dir_trades = [t for t in all_trades if t.direction == direction]
        if dir_trades:
            dir_stats = analyze_results(dir_trades)
            print(f"\n  {direction} ({dir_stats['total']} trades)")
            _print_stats(dir_stats)

    # Top 10 best trades
    print(f"\n  TOP 10 BEST TRADES")
    print(f"  {'TICKER':<8} {'SIGNAL':<12} {'DIR':<5} {'SCORE':>5} {'R':>6} {'PNL%':>7} {'HOLD':>5} {'EXIT':<10}")
    print("  " + "-" * 65)
    for t in sorted(all_trades, key=lambda x: x.r_multiple, reverse=True)[:10]:
        print(f"  {t.ticker:<8} {t.signal:<12} {t.direction:<5} {t.score:>5} {t.r_multiple:>6.2f} {t.pnl_pct:>6.1f}% {t.holding_period:>5} {t.exit_reason:<10}")

    # Top 10 worst trades
    print(f"\n  TOP 10 WORST TRADES")
    print(f"  {'TICKER':<8} {'SIGNAL':<12} {'DIR':<5} {'SCORE':>5} {'R':>6} {'PNL%':>7} {'HOLD':>5} {'EXIT':<10}")
    print("  " + "-" * 65)
    for t in sorted(all_trades, key=lambda x: x.r_multiple)[:10]:
        print(f"  {t.ticker:<8} {t.signal:<12} {t.direction:<5} {t.score:>5} {t.r_multiple:>6.2f} {t.pnl_pct:>6.1f}% {t.holding_period:>5} {t.exit_reason:<10}")

    print(f"\n  {DISCLAIMER}")
    print("\n" + "=" * 75 + "\n")


def _print_stats(s: dict):
    """Print a stats dict."""
    print(f"    Win Rate:      {s['win_rate']}%")
    print(f"    Avg R:         {s['avg_r']}R")
    print(f"    Median R:      {s['median_r']}R")
    print(f"    Profit Factor: {s['profit_factor']}")
    print(f"    Total R:       {s['total_r']}R")
    print(f"    Best/Worst:    {s['best_r']}R / {s['worst_r']}R")
    print(f"    Max Drawdown:  {s['max_drawdown_r']}R")
    print(f"    Avg Hold:      {s['avg_hold']} bars")


def export_trades(trades: list[Trade], output_dir: str = OUTPUT_DIR):
    """Export individual trades to CSV."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(output_dir, "backtest_results.csv")

    import csv
    columns = [
        "ticker", "signal", "direction", "score",
        "entry_price", "exit_price", "trail_at_entry", "initial_risk",
        "r_multiple", "pnl_pct", "holding_period", "exit_reason",
    ]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "ticker": t.ticker,
                "signal": t.signal,
                "direction": t.direction,
                "score": t.score,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "trail_at_entry": round(t.trail_at_entry, 2),
                "initial_risk": round(t.initial_risk, 2),
                "r_multiple": t.r_multiple,
                "pnl_pct": t.pnl_pct,
                "holding_period": t.holding_period,
                "exit_reason": t.exit_reason,
            })

    logger.info(f"Trades exported: {path} ({len(trades)} trades)")


# ─── Main ─────────────────────────────────────────────────────────
def run(tickers: list[str] = None, period: str = "1y"):
    """Run the full backtest pipeline."""
    logger.info("=" * 50)
    logger.info("  ELASTIC SCANNER — BACKTESTER")
    logger.info("=" * 50)
    t_start = time.time()

    if tickers is None:
        tickers = FALLBACK_TICKERS

    logger.info(f"Universe: {len(tickers)} tickers, period: {period}")

    # Download data
    all_data = download_backtest_data(tickers, period)

    # Run backtest for each ticker
    all_trades = []
    tested = 0
    skipped = 0

    for ticker in tickers:
        ticker_data = all_data.get(ticker, {})

        if "1D" not in ticker_data or "1W" not in ticker_data:
            skipped += 1
            continue

        df_1d = ticker_data["1D"]
        df_1w = ticker_data["1W"]
        df_1m = ticker_data.get("1M")

        trades = backtest_ticker(ticker, df_1d, df_1w, df_1m)
        all_trades.extend(trades)
        tested += 1

        if trades:
            logger.debug(f"  {ticker}: {len(trades)} trades")

    logger.info(f"Tested: {tested} | Skipped: {skipped} | Total trades: {len(all_trades)}")

    # Results
    print_results(all_trades)
    export_trades(all_trades)

    elapsed = time.time() - t_start
    logger.info(f"Backtest runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Elastic Scanner Backtester")
    parser.add_argument("--tickers", nargs="+", default=None, help="Tickers to test")
    parser.add_argument("--period", default="1y", help="Data period (default: 1y)")
    args = parser.parse_args()

    run(tickers=args.tickers, period=args.period)
