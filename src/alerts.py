"""
Alerts — CSV Export + ntfy Push Notifications

Handles all output: writing scan results to CSV and pushing
actionable signals (FIRE + PREP only) to mobile via ntfy.

ntfy format:
    ticker score (one per line, sorted highest first)
    disclaimer at end of every message
"""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path

import requests

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import NTFY_URL, OUTPUT_DIR, TOP_N, MIN_NTFY_SCORE, DISCLAIMER

logger = logging.getLogger(__name__)


# ─── CSV Export ────────────────────────────────────────────────────
def export_csv(signals: list[dict], output_dir: str = OUTPUT_DIR) -> str:
    """
    Export signals to CSV files:
    1. output/elastic_scan_{timestamp}.csv — timestamped archive
    2. output/latest.csv — overwritten each run

    Returns path to the timestamped file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ts_path = os.path.join(output_dir, f"elastic_scan_{timestamp}.csv")
    latest_path = os.path.join(output_dir, "latest.csv")

    columns = [
        "ticker", "signal", "direction", "score", "ext",
        "mtf", "comp_score", "vol_ratio", "timestamp",
    ]

    for path in [ts_path, latest_path]:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for s in signals:
                row = {**s, "timestamp": timestamp}
                writer.writerow(row)

    logger.info(f"CSV exported: {ts_path} ({len(signals)} signals)")
    return ts_path


# ─── ntfy Push ─────────────────────────────────────────────────────
def send_ntfy(bulls: list[dict], bears: list[dict]) -> bool:
    """
    Push actionable signals to ntfy.sh.

    Only sends FIRE and PREP signals with score >= MIN_NTFY_SCORE.
    EXTENDED signals are suppressed entirely.
    Duplicate tickers are removed.

    Format:
        (chart emoji) ELASTIC BULLISH
        AMZN 92
        NVDA 88

        (chart emoji) ELASTIC BEARISH
        AAPL 86
        CRM 81

        DISCLAIMER

    Returns True if sent successfully.
    """
    # Filter: actionable only (FIRE + PREP), score >= threshold, deduped
    actionable_bulls = _filter_actionable(bulls)
    actionable_bears = _filter_actionable(bears)

    if not actionable_bulls and not actionable_bears:
        logger.info("No actionable signals above threshold -- skipping ntfy")
        return False

    lines = []

    if actionable_bulls:
        lines.append("\U0001F4C8 ELASTIC BULLISH")
        for s in actionable_bulls[:TOP_N]:
            lines.append(f"{s['ticker']} {s['score']}")
        lines.append("")

    if actionable_bears:
        lines.append("\U0001F4C9 ELASTIC BEARISH")
        for s in actionable_bears[:TOP_N]:
            lines.append(f"{s['ticker']} {s['score']}")
        lines.append("")

    lines.append(DISCLAIMER)

    body = "\n".join(lines)

    # Priority: high if any FIRE signal present
    has_fire = any(
        s.get("signal") == "FIRE"
        for s in actionable_bulls + actionable_bears
    )
    priority = "high" if has_fire else "default"
    title = "Elastic Scanner"

    try:
        resp = requests.post(
            NTFY_URL,
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "chart_with_upwards_trend",
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"ntfy sent ({priority} priority, {len(actionable_bulls)}B/{len(actionable_bears)}S)")
        return True
    except Exception as e:
        logger.error(f"ntfy failed: {e}")
        return False


def _filter_actionable(signals: list[dict]) -> list[dict]:
    """
    Filter signals to only actionable ones:
    - FIRE or PREP only (no EXTENDED)
    - Score >= MIN_NTFY_SCORE
    - Deduplicated by ticker (keep highest score)
    - Sorted by score descending
    """
    seen_tickers = set()
    result = []

    # Sort by score first so dedup keeps the highest
    sorted_signals = sorted(signals, key=lambda x: x.get("score", 0), reverse=True)

    for s in sorted_signals:
        # Skip non-actionable signal types
        if s.get("signal") not in ("FIRE", "PREP"):
            continue

        # Skip below threshold
        if s.get("score", 0) < MIN_NTFY_SCORE:
            continue

        # Skip duplicate tickers
        ticker = s.get("ticker")
        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)

        result.append(s)

    return result


# ─── Console Summary ──────────────────────────────────────────────
def print_summary(bulls: list[dict], bears: list[dict]):
    """Print a formatted summary table to stdout."""
    print("\n" + "=" * 65)
    print("  ELASTIC SCANNER RESULTS")
    print("=" * 65)

    # Filter for console too — only show actionable
    ab = _filter_actionable(bulls)
    abr = _filter_actionable(bears)

    if not ab and not abr:
        print("  No actionable signals. Markets quiet or misaligned.")
        print("=" * 65 + "\n")
        return

    header = f"  {'TICKER':<8} {'SIGNAL':<8} {'SCORE':>5}  {'EXT':>5}  {'MTF'}"
    sep = "  " + "-" * 55

    if ab:
        print(f"\n  [+] BULLISH (top {TOP_N})")
        print(sep)
        print(header)
        print(sep)
        for s in ab[:TOP_N]:
            print(f"  {s['ticker']:<8} {s['signal']:<8} {s['score']:>5}  E:{s['ext']:>4.1f}  {s['mtf']}")

    if abr:
        print(f"\n  [-] BEARISH (top {TOP_N})")
        print(sep)
        print(header)
        print(sep)
        for s in abr[:TOP_N]:
            print(f"  {s['ticker']:<8} {s['signal']:<8} {s['score']:>5}  E:{s['ext']:>4.1f}  {s['mtf']}")

    print(f"\n  {DISCLAIMER}")
    print("\n" + "=" * 65 + "\n")
