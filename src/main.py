"""
Elastic Scanner — Main Orchestrator (v3.0 Weighted Scoring)

Fetches universe → downloads OHLCV → runs elastic engine → weighted scoring
→ tier assignment → exports CSV → updates dashboard Gist → pushes ntfy alerts.

v3.0 changes:
  - No rigid all-pass gates; pure weighted scoring
  - Sector strength (sector ETF trend alignment)
  - Relative strength (stock vs SPY 5-day performance)
  - Market regime (SPY above/below trail)
  - Tiers: ELITE (90+), FIRE (80-89), PREP (70-79), suppress (<70)

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
    SP500_URL, NDX100_URL, RUSSELL1000_URL, SP400_URL, SP600_URL,
    FALLBACK_TICKERS, INDEX_TICKERS, TIMEFRAMES,
    MIN_PRICE, MAX_PRICE, MIN_AVG_VOL, TOP_N, OUTPUT_DIR,
    SECTOR_ETF_TICKERS, SECTOR_ETFS,
)
from src.elastic_engine import process_ticker_tf
from src.signal_engine import classify_signal, extract_last_bar
from src.scoring import score_signal
from src.alerts import (
    export_csv, send_ntfy_transitions, fetch_previous_verdicts,
    print_summary, export_dashboard_json, _build_verdict, _filter_actionable,
)

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
    resp = req.get(url, headers={"User-Agent": "ElasticScanner/3.0"}, timeout=15)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text), header=0)


def load_universe() -> tuple[list[str], dict[str, str]]:
    """
    Load universe from multiple indices: S&P 500, Nasdaq 100, Russell 1000,
    S&P 400 MidCap, S&P 600 SmallCap. Deduped.
    Returns (tickers, sector_map) where sector_map = {ticker: sector_name}.
    """
    tickers = set()
    sector_map = {}

    # ─── S&P 500 ───────────────────────────────────────────────────
    try:
        logger.info("Loading S&P 500 tickers...")
        sp500 = _fetch_html_tables(SP500_URL)[0]
        sp_tickers = sp500["Symbol"].str.replace(".", "-", regex=False).tolist()
        tickers.update(sp_tickers)
        logger.info(f"  -> {len(sp_tickers)} S&P 500 tickers")

        # Extract sector mapping
        if "GICS Sector" in sp500.columns:
            for _, row in sp500.iterrows():
                t = row["Symbol"].replace(".", "-")
                sector_map[t] = row["GICS Sector"]
    except Exception as e:
        logger.warning(f"Failed to load S&P 500: {e}")

    # ─── Nasdaq 100 ───────────────────────────────────────────────
    try:
        logger.info("Loading Nasdaq 100 tickers...")
        ndx_tables = _fetch_html_tables(NDX100_URL)
        for tbl in ndx_tables:
            if "Ticker" in tbl.columns:
                ndx_tickers = tbl["Ticker"].str.replace(".", "-", regex=False).tolist()
                tickers.update(ndx_tickers)
                logger.info(f"  -> {len(ndx_tickers)} Nasdaq 100 tickers")
                break
    except Exception as e:
        logger.warning(f"Failed to load Nasdaq 100: {e}")

    # ─── Russell 1000 ─────────────────────────────────────────────
    try:
        logger.info("Loading Russell 1000 tickers...")
        r1k_tables = _fetch_html_tables(RUSSELL1000_URL)
        for tbl in r1k_tables:
            # Russell 1000 Wikipedia table uses "Ticker" or "Company"
            ticker_col = None
            for col in ["Ticker", "Symbol", "Ticker symbol"]:
                if col in tbl.columns:
                    ticker_col = col
                    break
            if ticker_col:
                r1k_tickers = tbl[ticker_col].dropna().str.replace(".", "-", regex=False).tolist()
                before = len(tickers)
                tickers.update(r1k_tickers)
                logger.info(f"  -> {len(r1k_tickers)} Russell 1000 tickers (+{len(tickers) - before} new)")
                break
    except Exception as e:
        logger.warning(f"Failed to load Russell 1000: {e}")

    # ─── S&P 400 MidCap ──────────────────────────────────────────
    try:
        logger.info("Loading S&P 400 MidCap tickers...")
        sp400 = _fetch_html_tables(SP400_URL)[0]
        col = "Symbol" if "Symbol" in sp400.columns else sp400.columns[0]
        sp400_tickers = sp400[col].str.replace(".", "-", regex=False).tolist()
        before = len(tickers)
        tickers.update(sp400_tickers)
        logger.info(f"  -> {len(sp400_tickers)} S&P 400 tickers (+{len(tickers) - before} new)")

        if "GICS Sector" in sp400.columns:
            for _, row in sp400.iterrows():
                t = str(row[col]).replace(".", "-")
                if t not in sector_map:
                    sector_map[t] = row["GICS Sector"]
    except Exception as e:
        logger.warning(f"Failed to load S&P 400: {e}")

    # ─── S&P 600 SmallCap ────────────────────────────────────────
    try:
        logger.info("Loading S&P 600 SmallCap tickers...")
        sp600 = _fetch_html_tables(SP600_URL)[0]
        col = "Symbol" if "Symbol" in sp600.columns else sp600.columns[0]
        sp600_tickers = sp600[col].str.replace(".", "-", regex=False).tolist()
        before = len(tickers)
        tickers.update(sp600_tickers)
        logger.info(f"  -> {len(sp600_tickers)} S&P 600 tickers (+{len(tickers) - before} new)")

        if "GICS Sector" in sp600.columns:
            for _, row in sp600.iterrows():
                t = str(row[col]).replace(".", "-")
                if t not in sector_map:
                    sector_map[t] = row["GICS Sector"]
    except Exception as e:
        logger.warning(f"Failed to load S&P 600: {e}")

    if not tickers:
        logger.warning("Using fallback ticker list")
        tickers = set(FALLBACK_TICKERS)

    # Always include major indices + sector ETFs
    tickers.update(INDEX_TICKERS)
    tickers.update(SECTOR_ETF_TICKERS)
    logger.info(f"  -> {len(INDEX_TICKERS)} index tickers + {len(SECTOR_ETF_TICKERS)} sector ETFs added")

    result = sorted(tickers)
    logger.info(f"Universe: {len(result)} unique tickers")
    return result, sector_map


# ─── Data Download ─────────────────────────────────────────────────
BATCH_SIZE = 200  # yfinance struggles with >500 tickers in one call

def download_data(tickers: list[str]) -> dict[str, dict[str, pd.DataFrame]]:
    """
    Bulk download OHLCV data for all tickers across all timeframes.
    Returns: {ticker: {"4H": df, "1D": df, "1W": df}}

    Downloads in batches of BATCH_SIZE to avoid yfinance timeouts.
    """
    data = {}

    # Split tickers into batches
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    logger.info(f"Download plan: {len(tickers)} tickers in {len(batches)} batches of ~{BATCH_SIZE}")

    for tf_name, tf_cfg in TIMEFRAMES.items():
        interval = tf_cfg["interval"]
        period = tf_cfg["period"]
        resample_to = tf_cfg.get("resample")

        logger.info(f"Downloading {tf_name} data (interval={interval}, period={period})...")
        t0 = time.time()
        tf_success = 0

        for batch_idx, batch in enumerate(batches):
            try:
                raw = yf.download(
                    batch,
                    period=period,
                    interval=interval,
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
            except Exception as e:
                logger.warning(f"  Batch {batch_idx+1}/{len(batches)} failed for {tf_name}: {e}")
                continue

            # Parse per-ticker DataFrames from this batch
            for ticker in batch:
                if ticker not in data:
                    data[ticker] = {}

                try:
                    if len(batch) == 1:
                        df = raw.dropna(how="all").copy()
                    elif isinstance(raw.columns, pd.MultiIndex):
                        df = raw[ticker].dropna(how="all").copy()
                    else:
                        df = raw.dropna(how="all").copy()

                    if df.empty:
                        continue

                    # Resample 1h → 4h if needed
                    if resample_to:
                        df = _resample_ohlcv(df, resample_to)

                    if len(df) >= 40:  # need enough bars for ATR warmup
                        data[ticker][tf_name] = df
                        tf_success += 1

                except Exception:
                    continue

        elapsed = time.time() - t0
        logger.info(f"  → {tf_name}: {tf_success} tickers loaded in {elapsed:.1f}s")

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
    Index tickers and sector ETFs bypass this gate."""
    # Index tickers and sector ETFs always pass
    if ticker.startswith("^") or ticker in INDEX_TICKERS or ticker in SECTOR_ETF_TICKERS:
        return True

    if df_1d is None or df_1d.empty:
        return False

    last_close = df_1d["Close"].iloc[-1]
    avg_vol = df_1d["Volume"].tail(20).mean()

    return (
        MIN_PRICE <= last_close <= MAX_PRICE
        and avg_vol >= MIN_AVG_VOL
    )


# ─── Market Context ───────────────────────────────────────────────
def build_market_context(all_data: dict, tickers: list[str]) -> dict:
    """
    Build market-level context for scoring:
    - SPY trend (above/below trail)
    - Sector ETF trends
    - Relative strength (5-day returns)
    """
    context = {
        "spy_below_trail": False,
        "sector_bull": {},
        "spy_perf_10d": None,
        "qqq_perf_10d": None,
        "ticker_perf_10d": {},
        "earnings_days": {},  # placeholder — no API for earnings yet
    }

    # SPY market regime
    spy_data = all_data.get("SPY", {})
    if "1D" in spy_data:
        try:
            spy_df = process_ticker_tf(spy_data["1D"])
            spy_last = extract_last_bar(spy_df)
            if spy_last:
                context["spy_below_trail"] = not spy_last["is_bull"]

                # SPY 10-day performance (Pine uses 10-day)
                spy_close = spy_data["1D"]["Close"]
                if len(spy_close) >= 11:
                    context["spy_perf_10d"] = float(
                        (spy_close.iloc[-1] / spy_close.iloc[-11] - 1) * 100
                    )
        except Exception:
            pass

    # QQQ 10-day performance
    qqq_data = all_data.get("QQQ", {})
    if "1D" in qqq_data:
        try:
            qqq_close = qqq_data["1D"]["Close"]
            if len(qqq_close) >= 11:
                context["qqq_perf_10d"] = float(
                    (qqq_close.iloc[-1] / qqq_close.iloc[-11] - 1) * 100
                )
        except Exception:
            pass

    # Sector ETF trends
    for sector_name, etf_ticker in SECTOR_ETFS.items():
        etf_data = all_data.get(etf_ticker, {})
        if "1D" in etf_data:
            try:
                etf_df = process_ticker_tf(etf_data["1D"])
                etf_last = extract_last_bar(etf_df)
                if etf_last:
                    context["sector_bull"][sector_name] = etf_last["is_bull"]
            except Exception:
                pass

    # Ticker 10-day performance (for relative strength vs SPY/QQQ)
    for ticker in tickers:
        ticker_data = all_data.get(ticker, {})
        if "1D" in ticker_data:
            try:
                close = ticker_data["1D"]["Close"]
                if len(close) >= 11:
                    context["ticker_perf_10d"][ticker] = float(
                        (close.iloc[-1] / close.iloc[-11] - 1) * 100
                    )
            except Exception:
                pass

    return context


# ─── Main Pipeline ────────────────────────────────────────────────
def run():
    """Execute the full Elastic Scanner pipeline (v3.0 weighted scoring)."""
    logger.info("=" * 50)
    logger.info("  ELASTIC SCANNER v3.0 — Starting")
    logger.info("=" * 50)
    t_start = time.time()

    # 1. Load universe (now includes sector mapping)
    tickers, sector_map = load_universe()

    # 2. Download data (3 bulk calls)
    all_data = download_data(tickers)

    # 3. Build market context (SPY regime, sector trends, relative strength)
    logger.info("Building market context...")
    market_context = build_market_context(all_data, tickers)
    spy_regime = "BEAR" if market_context["spy_below_trail"] else "BULL"
    logger.info(f"  SPY regime: {spy_regime}")
    logger.info(f"  Sectors tracked: {len(market_context['sector_bull'])}")

    # 4. Process each ticker
    signals = []
    skipped = 0
    skipped_missing_tf = 0
    skipped_quality_price = 0
    skipped_quality_vol = 0
    skipped_quality_nodata = 0
    skipped_engine = 0
    processed = 0

    for ticker in tickers:
        # Skip index/ETF tickers from signals (used for context only)
        if ticker.startswith("^"):
            continue

        ticker_data = all_data.get(ticker, {})

        # Skip if missing required TFs (monthly is optional)
        if not all(tf in ticker_data for tf in ["4H", "1D", "1W"]):
            skipped_missing_tf += 1
            skipped += 1
            continue

        # Quality gate (use daily data) — with rejection tracking
        df_1d = ticker_data["1D"]
        if ticker not in INDEX_TICKERS and ticker not in SECTOR_ETF_TICKERS:
            if df_1d is None or df_1d.empty:
                skipped_quality_nodata += 1
                skipped += 1
                continue
            last_close = df_1d["Close"].iloc[-1]
            avg_vol = df_1d["Volume"].tail(20).mean()
            if last_close < MIN_PRICE or last_close > MAX_PRICE:
                skipped_quality_price += 1
                skipped += 1
                continue
            if avg_vol < MIN_AVG_VOL:
                skipped_quality_vol += 1
                skipped += 1
                continue

        # Run elastic engine on each TF
        results = {}
        for tf in ["4H", "1D", "1W", "1M"]:
            if tf not in ticker_data:
                results[tf] = None
                continue
            try:
                enriched = process_ticker_tf(ticker_data[tf])
                results[tf] = extract_last_bar(enriched)
            except Exception:
                results[tf] = None

        # Must have at least 4H, 1D, 1W
        if not all(results[tf] for tf in ["4H", "1D", "1W"]):
            skipped_engine += 1
            skipped += 1
            continue

        # Get sector for this ticker
        sector = sector_map.get(ticker, "")

        # Build signal (no rigid gates — every valid ticker gets one)
        signal = classify_signal(
            ticker,
            results["4H"],
            results["1D"],
            results["1W"],
            results.get("1M"),
            sector=sector,
        )

        if signal:
            # Enrich with RS vs SPY/QQQ (Pine: rsVsSpy = stockChg10 - spyChg10)
            ticker_perf = market_context.get("ticker_perf_10d", {}).get(ticker)
            spy_perf = market_context.get("spy_perf_10d")
            qqq_perf = market_context.get("qqq_perf_10d")

            signal["rs_vs_spy"] = (ticker_perf - spy_perf) if (ticker_perf is not None and spy_perf is not None) else 0.0
            signal["rs_vs_qqq"] = (ticker_perf - qqq_perf) if (ticker_perf is not None and qqq_perf is not None) else 0.0

            # Score with market context
            signal = score_signal(signal, market_context)

            # Only keep signals above HOT threshold (score >= 20)
            if signal["score"] >= 20:
                signals.append(signal)

        processed += 1

    logger.info(f"Processed: {processed} | Signals: {len(signals)}")
    logger.info(f"Skipped breakdown: missing_TF={skipped_missing_tf} | price_gate={skipped_quality_price} | vol_gate={skipped_quality_vol} | no_data={skipped_quality_nodata} | engine_fail={skipped_engine} | total={skipped}")

    # 5. Rank by score (pure score ranking)
    all_ranked = sorted(signals, key=lambda x: x["score"], reverse=True)

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

    # Count by tier
    elite_count = sum(1 for s in signals if s.get("tier") == "ELITE")
    fire_count = sum(1 for s in signals if s.get("tier") == "FIRE")
    prep_count = sum(1 for s in signals if s.get("tier") == "PREP")
    logger.info(f"Tiers: ELITE={elite_count} FIRE={fire_count} PREP={prep_count}")

    # 6. Fetch previous verdicts BEFORE exporting new data
    previous_verdicts = fetch_previous_verdicts()

    # 7. Output
    export_csv(all_ranked, OUTPUT_DIR)
    export_dashboard_json(all_ranked, len(tickers))
    print_summary(bulls, bears)

    # 8. Push transition-aware alerts
    #    Only fires when a setup CHANGES INTO SLINGSHOT/TRIGGER/ELITE/FIRE
    actionable_for_alert = _filter_actionable(all_ranked)
    dashboard_signals = [
        {
            "ticker": s["ticker"],
            "signal": s.get("tier", "PREP"),
            "verdict": _build_verdict(s),
            "direction": s["direction"],
            "score": s["score"],
        }
        for s in actionable_for_alert
    ]
    send_ntfy_transitions(dashboard_signals, previous_verdicts)

    elapsed = time.time() - t_start
    logger.info(f"Total runtime: {elapsed:.1f}s")
    logger.info("Done.")


if __name__ == "__main__":
    run()
