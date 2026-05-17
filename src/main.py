"""
Elastic Scanner — Main Orchestrator

Fetches universe → downloads OHLCV → runs elastic engine → classifies signals
→ scores → ranks → exports CSV → pushes ntfy alerts.

Usage:
    python -m src.main
"""

import logging
import sys
import os
import time

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    SP500_URL, NDX100_URL, FALLBACK_TICKERS, INDEX_TICKERS, TIMEFRAMES,
    MIN_PRICE, MAX_PRICE, MIN_AVG_VOL, TOP_N, OUTPUT_DIR,
)
from src.elastic_engine import process_ticker_tf
from src.signal_engine import classify_signal, extract_last_bar
from src.scoring import score_signal
from src.alerts import export_csv, send_ntfy, print_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─── Universe Loading ──────────────────────────────────────────────
def _fetch_html_tables(url: str) -> list[pd.DataFrame]:
    """Fetch HTML from URL with a proper User-Agent and parse tables."""
    import requests as req
    from io import StringIO
    resp = req.get(url, headers={"User-Agent": "ElasticScanner/1.0"}, timeout=15)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text), header=0)


def load_universe() -> list[str]:
    """Load SP500 + NDX100 tickers from Wikipedia, deduped."""
    tickers = set()

    try:
        logger.info("Loading S&P 500 tickers...")
        sp500 = _fetch_html_tables(SP500_URL)[0]
        sp_tickers = sp500["Symbol"].str.replace(".", "-", regex=False).tolist()
        tickers.update(sp_tickers)
        logger.info(f"  -> {len(sp_tickers)} S&P 500 tickers")
    except Exception as e:
        logger.warning(f"Failed to load S&P 500: {e}")

    try:
        logger.info("Loading Nasdaq 100 tickers...")
        ndx_tables = _fetch_html_tables(NDX100_URL)
        # Find the table with a 'Ticker' column
        for tbl in ndx_tables:
            if "Ticker" in tbl.columns:
                ndx_tickers = tbl["Ticker"].str.replace(".", "-", regex=False).tolist()
                tickers.update(ndx_tickers)
                logger.info(f"  -> {len(ndx_tickers)} Nasdaq 100 tickers")
                break
    except Exception as e:
        logger.warning(f"Failed to load Nasdaq 100: {e}")

    if not tickers:
        logger.warning("Using fallback ticker list")
        tickers = set(FALLBACK_TICKERS)

    # Always include major indices
    tickers.update(INDEX_TICKERS)
    logger.info(f"  -> {len(INDEX_TICKERS)} index tickers added")

    result = sorted(tickers)
    logger.info(f"Universe: {len(result)} unique tickers")
    return result


# ─── Data Download ─────────────────────────────────────────────────
def download_data(tickers: list[str]) -> dict[str, dict[str, pd.DataFrame]]:
    """
    Bulk download OHLCV data for all tickers across all timeframes.
    Returns: {ticker: {"4H": df, "1D": df, "1W": df}}
    
    Uses yfinance bulk download (1 HTTP call per TF) for speed.
    """
    data = {}

    for tf_name, tf_cfg in TIMEFRAMES.items():
        interval = tf_cfg["interval"]
        period = tf_cfg["period"]
        resample_to = tf_cfg.get("resample")

        logger.info(f"Downloading {tf_name} data (interval={interval}, period={period})...")
        t0 = time.time()

        try:
            raw = yf.download(
                tickers,
                period=period,
                interval=interval,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception as e:
            logger.error(f"Failed to download {tf_name}: {e}")
            continue

        elapsed = time.time() - t0
        logger.info(f"  → Downloaded in {elapsed:.1f}s")

        # Parse per-ticker DataFrames
        for ticker in tickers:
            if ticker not in data:
                data[ticker] = {}

            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    # Multi-ticker download: columns are (ticker, OHLCV)
                    df = raw[ticker].dropna(how="all").copy()
                else:
                    # Single ticker fallback
                    df = raw.dropna(how="all").copy()

                if df.empty:
                    continue

                # Resample 1h → 4h if needed
                if resample_to:
                    df = _resample_ohlcv(df, resample_to)

                if len(df) >= 40:  # need enough bars for ATR warmup
                    data[ticker][tf_name] = df

            except Exception:
                continue

    return data


def _resample_ohlcv(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample intraday OHLCV to a coarser frequency (e.g. 1h → 4h)."""
    resampled = df.resample(freq).agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()
    return resampled


# ─── Quality Gate ──────────────────────────────────────────────────
def passes_quality(ticker: str, df_1d: pd.DataFrame) -> bool:
    """Check price and volume quality gates using daily data.
    Index tickers bypass this gate (no volume data, prices above MAX_PRICE)."""
    # Index tickers always pass
    if ticker.startswith("^") or ticker in INDEX_TICKERS:
        return True

    if df_1d is None or df_1d.empty:
        return False

    last_close = df_1d["Close"].iloc[-1]
    avg_vol = df_1d["Volume"].tail(20).mean()

    return (
        MIN_PRICE <= last_close <= MAX_PRICE
        and avg_vol >= MIN_AVG_VOL
    )


# ─── Main Pipeline ────────────────────────────────────────────────
def run():
    """Execute the full Elastic Scanner pipeline."""
    logger.info("=" * 50)
    logger.info("  ELASTIC SCANNER — Starting")
    logger.info("=" * 50)
    t_start = time.time()

    # 1. Load universe
    tickers = load_universe()

    # 2. Download data (3 bulk calls)
    all_data = download_data(tickers)

    # 3. Process each ticker
    signals = []
    skipped = 0
    processed = 0

    for ticker in tickers:
        ticker_data = all_data.get(ticker, {})

        # Skip if missing any TF
        if not all(tf in ticker_data for tf in ["4H", "1D", "1W"]):
            skipped += 1
            continue

        # Quality gate (use daily data)
        if not passes_quality(ticker, ticker_data["1D"]):
            skipped += 1
            continue

        # Run elastic engine on each TF
        results = {}
        for tf in ["4H", "1D", "1W"]:
            try:
                enriched = process_ticker_tf(ticker_data[tf])
                results[tf] = extract_last_bar(enriched)
            except Exception:
                results[tf] = None

        if not all(results.values()):
            skipped += 1
            continue

        # Classify signal
        signal = classify_signal(
            ticker,
            results["4H"],
            results["1D"],
            results["1W"],
        )

        if signal:
            signal = score_signal(signal)
            signals.append(signal)

        processed += 1

    logger.info(f"Processed: {processed} | Skipped: {skipped} | Signals: {len(signals)}")

    # 4. Rank and split (pass all to alerts — filter happens there)
    bulls = sorted(
        [s for s in signals if s["direction"] == "BULL"],
        key=lambda x: x["score"],
        reverse=True,
    )

    bears = sorted(
        [s for s in signals if s["direction"] == "BEAR"],
        key=lambda x: x["score"],
        reverse=True,
    )

    actionable = [s for s in signals if s.get("signal") in ("FIRE", "PREP")]
    logger.info(f"Actionable (FIRE+PREP): {len(actionable)} | EXTENDED (suppressed): {len(signals) - len(actionable)}")

    # 5. Output
    all_ranked = sorted(signals, key=lambda x: x["score"], reverse=True)
    export_csv(all_ranked, OUTPUT_DIR)
    print_summary(bulls, bears)

    # 6. Push alerts (only actionable signals sent to ntfy)
    send_ntfy(bulls, bears)

    elapsed = time.time() - t_start
    logger.info(f"Total runtime: {elapsed:.1f}s")
    logger.info("Done.")


if __name__ == "__main__":
    run()
