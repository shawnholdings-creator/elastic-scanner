"""
Alerts — CSV Export + ntfy Push Notifications

Handles all output: writing scan results to CSV and pushing
top signals to mobile via ntfy.sh.
"""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path

import requests

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import NTFY_URL, OUTPUT_DIR, TOP_N

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
    Push top signals to ntfy.sh for mobile notifications.
    
    Format:
        🟢 BULL
        AAPL  FIRE  87  E:1.2  MTF:4H+ 1D+ 1W+
        ...
        🔴 BEAR
        XOM   FIRE  82  E:1.5  MTF:4H- 1D- 1W-
    
    Returns True if sent successfully.
    """
    if not bulls and not bears:
        logger.info("No signals to push — skipping ntfy")
        return False

    lines = []

    if bulls:
        lines.append("🟢 BULL")
        for s in bulls[:TOP_N]:
            lines.append(_format_signal_line(s))
        lines.append("")

    if bears:
        lines.append("🔴 BEAR")
        for s in bears[:TOP_N]:
            lines.append(_format_signal_line(s))

    body = "\n".join(lines)

    # Determine priority
    has_fire = any(s.get("signal") == "FIRE" for s in bulls + bears)
    priority = "high" if has_fire else "default"

    # Title must be latin-1 safe (no emoji in headers)
    if has_fire:
        title = "Elastic Scanner - FIRE"
    else:
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
        logger.info(f"ntfy sent ({priority} priority)")
        return True
    except Exception as e:
        logger.error(f"ntfy failed: {e}")
        return False


def _format_signal_line(s: dict) -> str:
    """Format a single signal for the ntfy body."""
    ticker = s.get("ticker", "???").ljust(6)
    signal = s.get("signal", "???").ljust(8)
    score = str(int(s.get("score", 0))).rjust(3)
    ext = f"E:{s.get('ext', 0):.1f}"
    mtf = s.get("mtf", "???")
    return f"  {ticker} {signal} {score}  {ext}  {mtf}"


# ─── Console Summary ──────────────────────────────────────────────
def print_summary(bulls: list[dict], bears: list[dict]):
    """Print a formatted summary table to stdout."""
    print("\n" + "=" * 65)
    print("  ELASTIC SCANNER RESULTS")
    print("=" * 65)

    if not bulls and not bears:
        print("  No signals found. Markets quiet or misaligned.")
        print("=" * 65 + "\n")
        return

    header = f"  {'TICKER':<8} {'SIGNAL':<10} {'SCORE':>5}  {'EXT':>5}  {'MTF'}"
    sep = "  " + "-" * 55

    if bulls:
        print(f"\n  [+] TOP {TOP_N} BULL")
        print(sep)
        print(header)
        print(sep)
        for s in bulls[:TOP_N]:
            print(f"  {s['ticker']:<8} {s['signal']:<10} {s['score']:>5.1f}  E:{s['ext']:>4.1f}  {s['mtf']}")

    if bears:
        print(f"\n  [-] TOP {TOP_N} BEAR")
        print(sep)
        print(header)
        print(sep)
        for s in bears[:TOP_N]:
            print(f"  {s['ticker']:<8} {s['signal']:<10} {s['score']:>5.1f}  E:{s['ext']:>4.1f}  {s['mtf']}")

    print("\n" + "=" * 65 + "\n")
