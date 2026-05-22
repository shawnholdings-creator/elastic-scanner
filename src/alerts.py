"""
Alerts — CSV Export + ntfy Push + Dashboard Gist

Handles all output: writing scan results to CSV, pushing
actionable signals to mobile via ntfy, and updating the
AI Dashboard Gist feed.

v4.1 ntfy — State-aware transition alerts (A+B only):
    Only fires when a ticker's setup CHANGES INTO:
    SLINGSHOT, TRIGGER, ELITE, or FIRE
    AND has a conviction score >= 50 (grade A or B).

    🚨 NEW SETUP ALERTS
    AMZN → BULLISH SLINGSHOT (A) 92
    NVDA → BULLISH FIRE (B) 68

    EDUCATIONAL ANALYSIS ONLY. NOT FINANCIAL ADVICE.
    NOT A RECOMMENDATION TO BUY, SELL, OR HOLD.
"""

# Setup types that trigger ntfy alerts on transition
ALERT_SETUPS = {"SLINGSHOT", "TRIGGER", "ELITE", "FIRE"}

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import requests

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import NTFY_URL, OUTPUT_DIR, TOP_N, DISCLAIMER

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


# ─── ntfy Push — State-Aware Transitions ───────────────────────────
def fetch_previous_verdicts() -> dict[str, str]:
    """
    Fetch the current dashboard.json from Gist to get previous verdicts.
    Returns a dict of {ticker: verdict_string} e.g. {"AMZN": "BULLISH SLINGSHOT"}.
    Returns empty dict on failure (treats all signals as new).
    """
    gist_id = os.environ.get("GIST_ID", "")
    gist_token = os.environ.get("GIST_TOKEN", "")

    if not gist_id or not gist_token:
        logger.warning("GIST_ID/GIST_TOKEN not set — no previous state available")
        return {}

    try:
        resp = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers={
                "Authorization": f"Bearer {gist_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        content = resp.json().get("files", {}).get("dashboard.json", {}).get("content", "")
        if not content:
            return {}
        data = json.loads(content)
        return {
            s["ticker"]: s.get("verdict", "")
            for s in data.get("signals", [])
        }
    except Exception as e:
        logger.warning(f"Could not fetch previous verdicts: {e}")
        return {}


def _extract_setup_type(verdict: str) -> str:
    """
    Extract the setup keyword from a verdict string.
    e.g. "BULLISH SLINGSHOT" → "SLINGSHOT", "COIL" → "COIL", "READY" → "READY"
    """
    parts = verdict.upper().split()
    # Check last word first ("BULLISH SLINGSHOT" → "SLINGSHOT")
    for keyword in reversed(parts):
        if keyword in ALERT_SETUPS:
            return keyword
    return parts[-1] if parts else ""


def send_ntfy_transitions(
    current_signals: list[dict],
    previous_verdicts: dict[str, str],
) -> bool:
    """
    Send ntfy alert ONLY for tickers whose setup just transitioned
    INTO SLINGSHOT, TRIGGER, ELITE, or FIRE.

    Compares current scan verdicts against previous Gist state.
    Includes conviction grade in the alert message.

    Returns True if alert was sent.
    """
    transitions = []

    for s in current_signals:
        ticker = s.get("ticker", "")
        score = s.get("score", 0)
        verdict = s.get("verdict", s.get("signal", ""))
        current_setup = _extract_setup_type(verdict)

        # Only care about transitions INTO alert-worthy setups
        if current_setup not in ALERT_SETUPS:
            continue

        # ── A+B GATE: Only alert for score >= 50 ──
        if score < 50:
            logger.debug(f"{ticker} setup={current_setup} score={score} — below A+B threshold, skipping ntfy")
            continue

        # Check what the ticker was BEFORE
        prev_verdict = previous_verdicts.get(ticker, "")
        prev_setup = _extract_setup_type(prev_verdict) if prev_verdict else ""

        # Alert if: ticker is new, or setup type changed
        if prev_setup != current_setup:
            transitions.append(s)

    if not transitions:
        logger.info("No A+B setup transitions detected — skipping ntfy")
        return False

    # Build message
    lines = ["\U0001F6A8 NEW SETUP ALERTS", ""]

    for s in sorted(transitions, key=lambda x: x.get("score", 0), reverse=True)[:TOP_N]:
        ticker = s["ticker"]
        verdict = s.get("verdict", s.get("signal", ""))
        grade = _score_to_grade(s.get("score", 0))
        score = s.get("score", 0)
        lines.append(f"{ticker} → {verdict} ({grade}) {score}")

    lines.append("")
    lines.append(DISCLAIMER)

    body = "\n".join(lines)

    # Priority: high if any A-grade or SLINGSHOT
    has_high = any(
        _score_to_grade(s.get("score", 0)) == "A" or
        _extract_setup_type(s.get("verdict", "")) == "SLINGSHOT"
        for s in transitions
    )
    priority = "high" if has_high else "default"
    title = f"AI Cockpit — {len(transitions)} Setup Transition{'s' if len(transitions) != 1 else ''}"

    try:
        resp = requests.post(
            NTFY_URL,
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "rotating_light",
            },
            timeout=15,
        )
        resp.raise_for_status()
        tickers_str = ", ".join(s["ticker"] for s in transitions[:5])
        logger.info(f"ntfy transition alert sent ({priority} priority, {len(transitions)} transitions: {tickers_str})")
        return True
    except Exception as e:
        logger.error(f"ntfy transition alert failed: {e}")
        return False


# ─── Legacy ntfy Push (kept as fallback) ───────────────────────────
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
    Filter signals to actionable ones (GOOD+ quality, score >= 50).
    Deduped by ticker (keep highest score), sorted by score descending.
    """
    seen_tickers = set()
    result = []

    sorted_signals = sorted(signals, key=lambda x: x.get("score", 0), reverse=True)

    for s in sorted_signals:
        if s.get("score", 0) < 50:  # GOOD+ threshold (lowered from 55)
            continue
        if s.get("tier") == "SUPPRESS":
            continue

        ticker = s.get("ticker")
        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)
        result.append(s)

    return result


def _filter_for_dashboard(signals: list[dict]) -> list[dict]:
    """
    Filter developing signals for dashboard watchlist (20-54 score range).
    These are HOT/EARLY quality — appear in watchlist but NOT tradable.
    Excludes tickers already in actionable (55+).
    """
    actionable_tickers = {
        s.get("ticker") for s in _filter_actionable(signals)
    }

    seen_tickers = set()
    result = []

    sorted_signals = sorted(signals, key=lambda x: x.get("score", 0), reverse=True)

    for s in sorted_signals:
        score = s.get("score", 0)
        if score < 20 or score >= 50:  # Only 20-49 range
            continue

        ticker = s.get("ticker")
        if ticker in actionable_tickers or ticker in seen_tickers:
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
    """Map score to quality grade (v3.1 recalibrated)."""
    if score >= 75:
        return "A"  # ELITE
    elif score >= 50:
        return "B"  # GOOD
    elif score >= 30:
        return "C"  # EARLY
    elif score >= 20:
        return "D"  # HOT
    return "F"  # POOR


def _build_verdict(signal: dict) -> str:
    """
    Build verdict from the setup field computed by scoring.py.
    Maps to Pine masterState (no-position mode, lines 508-524).
    """
    setup = signal.get("setup", "WATCH")
    direction = signal.get("direction", "BULL")
    prefix = "BULLISH" if direction == "BULL" else "BEARISH"

    if setup == "SLINGSHOT":
        return f"{prefix} SLINGSHOT"
    elif setup == "FIRE":
        return f"{prefix} FIRE"
    elif setup == "TRIGGER":
        return f"{prefix} TRIGGER"
    elif setup == "COIL":
        return "COIL"
    elif setup == "ACTIVE":
        return f"{prefix} ACTIVE"
    return "WATCH"


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

    # Filter: tradable signals (70+) AND developing pipeline (55-69)
    actionable = _filter_actionable(signals)  # 70+ tradable
    developing = _filter_for_dashboard(signals)  # 55-69 watchlist pipeline
    all_dashboard = actionable + developing

    # Build payload matching dashboard schema
    payload = {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tickers_scanned": tickers_scanned,
        "actionable_count": len(actionable),
        "signals": [
            {
                "ticker": s["ticker"],
                "signal": s.get("setup", "WATCH"),
                "verdict": _build_verdict(s),
                "direction": s["direction"],
                "score": s["score"],
                "ext": round(s.get("ext", 0), 1),
                "grade": _score_to_grade(s["score"]),
                "quality": s.get("quality", "POOR"),
                "price": round(s.get("close", 0), 2),
                "struct": s.get("struct_score", 0),
                "momt": s.get("mom_score", 0),
                "rs": s.get("rs_score", 0),
            }
            for s in all_dashboard[:30]  # cap at 30 for payload size
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
        logger.info(f"Dashboard Gist updated ({len(actionable)} tradable + {len(developing)} developing)")
        return True
    except Exception as e:
        logger.error(f"Dashboard Gist update failed: {e}")
        return False
