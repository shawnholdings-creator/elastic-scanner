"""
Alerts — CSV Export + ntfy Push + Dashboard Gist

Handles all output: writing scan results to CSV, pushing
actionable signals to mobile via ntfy, and updating the
AI Dashboard Gist feed.

v3.0 ntfy format:
    📈 BULLISH
    AMZN 92 ELITE
    NVDA 86 FIRE

    📉 BEARISH
    AAPL 84 FIRE
    CRM 76 PREP

    EDUCATIONAL ANALYSIS ONLY. NOT FINANCIAL ADVICE.
    NOT A RECOMMENDATION TO BUY, SELL, OR HOLD.
"""

import csv
import json
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
        "ticker", "tier", "direction", "score", "ext",
        "mtf", "comp_score", "vol_ratio", "sector", "timestamp",
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

    v3.0 format:
        📈 BULLISH
        AMZN 92 ELITE
        NVDA 86 FIRE

        📉 BEARISH
        AAPL 84 FIRE
        CRM 76 PREP

        DISCLAIMER

    Returns True if sent successfully.
    """
    # Filter: score >= threshold, deduped
    actionable_bulls = _filter_actionable(bulls)
    actionable_bears = _filter_actionable(bears)

    if not actionable_bulls and not actionable_bears:
        logger.info("No actionable signals above threshold -- skipping ntfy")
        return False

    lines = []

    if actionable_bulls:
        lines.append("\U0001F4C8 BULLISH")
        for s in actionable_bulls[:TOP_N]:
            lines.append(f"{s['ticker']} {s['score']} {s.get('tier', '')}")
        lines.append("")

    if actionable_bears:
        lines.append("\U0001F4C9 BEARISH")
        for s in actionable_bears[:TOP_N]:
            lines.append(f"{s['ticker']} {s['score']} {s.get('tier', '')}")
        lines.append("")

    lines.append(DISCLAIMER)

    body = "\n".join(lines)

    # Priority: high if any ELITE signal present
    has_elite = any(
        s.get("tier") == "ELITE"
        for s in actionable_bulls + actionable_bears
    )
    priority = "high" if has_elite else "default"
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
    - Score >= MIN_NTFY_SCORE (70)
    - Tier is ELITE, FIRE, or PREP (not SUPPRESS)
    - Deduplicated by ticker (keep highest score)
    - Sorted by score descending
    """
    seen_tickers = set()
    result = []

    # Sort by score first so dedup keeps the highest
    sorted_signals = sorted(signals, key=lambda x: x.get("score", 0), reverse=True)

    for s in sorted_signals:
        # Skip below threshold
        if s.get("score", 0) < MIN_NTFY_SCORE:
            continue

        # Skip suppressed tier
        if s.get("tier") == "SUPPRESS":
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
    print("  ELASTIC SCANNER v3.0 RESULTS")
    print("=" * 65)

    # Filter for console too — only show actionable
    ab = _filter_actionable(bulls)
    abr = _filter_actionable(bears)

    if not ab and not abr:
        print("  No actionable signals. Markets quiet or misaligned.")
        print("=" * 65 + "\n")
        return

    header = f"  {'TICKER':<8} {'TIER':<8} {'SCORE':>5}  {'EXT':>5}  {'MTF'}"
    sep = "  " + "-" * 55

    if ab:
        print(f"\n  [+] BULLISH (top {TOP_N})")
        print(sep)
        print(header)
        print(sep)
        for s in ab[:TOP_N]:
            print(f"  {s['ticker']:<8} {s.get('tier', ''):<8} {s['score']:>5}  E:{s['ext']:>4.1f}  {s['mtf']}")

    if abr:
        print(f"\n  [-] BEARISH (top {TOP_N})")
        print(sep)
        print(header)
        print(sep)
        for s in abr[:TOP_N]:
            print(f"  {s['ticker']:<8} {s.get('tier', ''):<8} {s['score']:>5}  E:{s['ext']:>4.1f}  {s['mtf']}")

    print(f"\n  {DISCLAIMER}")
    print("\n" + "=" * 65 + "\n")


# ─── Dashboard Gist Export ────────────────────────────────────────
def _score_to_grade(score: int) -> str:
    """Convert numeric score to letter grade."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    return "D"


def _build_verdict(signal: dict) -> str:
    """
    Build a descriptive verdict label for the dashboard.
    Combines direction + compression state + tier for human-readable labels.
    Examples: "BULLISH SLINGSHOT", "BEARISH FIRE", "COIL", "READY"
    """
    direction = signal.get("direction", "BULL")
    tier = signal.get("tier", "PREP")
    is_compressed = signal.get("is_compressed", False)
    flip_age = signal.get("flip_age", 999)
    expansion_fire = signal.get("expansion_fire", False)

    prefix = "BULLISH" if direction == "BULL" else "BEARISH"

    # Compression-based verdicts
    if is_compressed and flip_age <= 2 and expansion_fire:
        return f"{prefix} SLINGSHOT"
    elif is_compressed:
        return "COIL"
    elif flip_age <= 2:
        return f"{prefix} TRIGGER"
    elif tier == "ELITE":
        return f"{prefix} ELITE"
    elif tier == "FIRE":
        return f"{prefix} FIRE"
    else:
        return "READY"


def export_dashboard_json(signals: list[dict], tickers_scanned: int) -> bool:
    """
    Push actionable signals to a GitHub Gist for the AI Dashboard.

    Reads GIST_ID and GIST_TOKEN from environment variables.
    Returns True if successful.
    """
    gist_id = os.environ.get("GIST_ID", "")
    gist_token = os.environ.get("GIST_TOKEN", "")

    if not gist_id or not gist_token:
        logger.warning("GIST_ID / GIST_TOKEN not set — skipping dashboard export")
        return False

    # Filter to actionable signals only
    actionable = _filter_actionable(signals)

    # Build payload matching dashboard schema
    payload = {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tickers_scanned": tickers_scanned,
        "actionable_count": len(actionable),
        "signals": [
            {
                "ticker": s["ticker"],
                "signal": s.get("tier", "PREP"),
                "verdict": _build_verdict(s),
                "direction": s["direction"],
                "score": s["score"],
                "ext": round(s.get("ext", 0), 1),
                "grade": _score_to_grade(s["score"]),
                "price": round(s.get("close", 0), 2),
            }
            for s in actionable[:20]  # cap at 20 for payload size
        ],
    }

    try:
        resp = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers={
                "Authorization": f"Bearer {gist_token}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "files": {
                    "dashboard.json": {
                        "content": json.dumps(payload, indent=2),
                    }
                }
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"Dashboard Gist updated ({len(actionable)} signals)")
        return True
    except Exception as e:
        logger.error(f"Dashboard Gist update failed: {e}")
        return False
