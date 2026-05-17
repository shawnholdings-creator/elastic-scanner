"""
Signal Engine — PREP / FIRE / EXTENDED Classification

Takes elastic_engine output from all 3 timeframes and classifies
each ticker into one of three signal types (or None).

FIRE and PREP are actionable. EXTENDED is informational only
and suppressed from ntfy alerts.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    EXT_LOW, EXT_FIRE_MAX, EXT_HIGH,
    COMP_PREP_MIN, COMP_FIRE_MIN, COMP_FIRE_LOOKBACK,
    FRESH_FLIP_BARS, VOL_SURGE_MULT,
)


def classify_signal(ticker: str, data_4h: dict, data_1d: dict, data_1w: dict) -> dict | None:
    """
    Classify a ticker into FIRE, PREP, EXTENDED, or None.

    FIRE:     Fresh 4H flip + compression + momentum + volume + not extended
    PREP:     MTF aligned + compressed + ideal zone (about to fire)
    EXTENDED: MTF aligned but overextended (informational, suppressed from ntfy)

    Returns dict with signal info or None if no signal.
    """
    if not all([data_4h, data_1d, data_1w]):
        return None

    # ─── MTF Alignment ────────────────────────────────────────────
    bull_4h = data_4h["is_bull"]
    bull_1d = data_1d["is_bull"]
    bull_1w = data_1w["is_bull"]

    triple_bull = bull_4h and bull_1d and bull_1w
    triple_bear = (not bull_4h) and (not bull_1d) and (not bull_1w)
    mtf_aligned = triple_bull or triple_bear

    direction = "BULL" if triple_bull else "BEAR" if triple_bear else "MIXED"
    mtf_label = _mtf_label(bull_4h, bull_1d, bull_1w)

    # Extract 4H values (primary timeframe for signals)
    ext = data_4h["ext"]
    flip_age = data_4h["flip_bar_age"]
    comp_now = data_4h["comp_score"]
    recent_comp = data_4h.get("recent_comp_max", comp_now)
    alma_rising = data_4h["alma_rising"]
    vol_ratio = data_4h["vol_ratio"]

    # Momentum confirms direction
    if triple_bull:
        momentum_ok = alma_rising
    elif triple_bear:
        momentum_ok = not alma_rising
    else:
        momentum_ok = False

    # Build base result dict (shared by all signal types)
    base = {
        "ticker": ticker,
        "direction": direction,
        "ext": round(ext, 2),
        "mtf": mtf_label,
        "comp_score": comp_now,
        "recent_comp_max": recent_comp,
        "flip_age": flip_age,
        "alma_rising": alma_rising,
        "vol_ratio": round(vol_ratio, 2),
    }

    # ─── FIRE: Fresh 4H flip + compression + momentum + vol ───────
    if (mtf_aligned
        and flip_age <= FRESH_FLIP_BARS
        and recent_comp >= COMP_FIRE_MIN
        and momentum_ok
        and vol_ratio >= VOL_SURGE_MULT
        and ext <= EXT_FIRE_MAX):
        return {**base, "signal": "FIRE"}

    # ─── PREP: Aligned + compressed + ideal zone ─────────────────
    if (mtf_aligned
        and comp_now >= COMP_PREP_MIN
        and ext <= EXT_LOW):
        return {**base, "signal": "PREP"}

    # ─── EXTENDED: Aligned but overextended (informational only) ──
    if (mtf_aligned and ext > EXT_HIGH):
        return {**base, "signal": "EXTENDED"}

    return None


def _mtf_label(bull_4h: bool, bull_1d: bool, bull_1w: bool) -> str:
    """Build MTF alignment label like '4H+ 1D+ 1W+' or '4H- 1D- 1W-'."""
    return (
        f"4H{'+'if bull_4h else'-'} "
        f"1D{'+'if bull_1d else'-'} "
        f"1W{'+'if bull_1w else'-'}"
    )


def extract_last_bar(df) -> dict | None:
    """
    Extract the last bar's computed values from an elastic_engine DataFrame.
    Returns a dict ready for classify_signal, or None if insufficient data.
    """
    if df is None or df.empty:
        return None

    last = df.iloc[-1]

    # Recent compression max (look back COMP_FIRE_LOOKBACK bars)
    lookback = min(COMP_FIRE_LOOKBACK, len(df))
    recent_comp_max = df["comp_score"].iloc[-lookback:].max()

    return {
        "is_bull": bool(last.get("is_bull", False)),
        "ext": float(last.get("ext", 0)),
        "comp_score": int(last.get("comp_score", 0)),
        "flip_bar_age": int(last.get("flip_bar_age", 999)),
        "alma_rising": bool(last.get("alma_rising", False)),
        "vol_ratio": float(last.get("vol_ratio", 1.0)),
        "recent_comp_max": int(recent_comp_max) if not isinstance(recent_comp_max, float) or not __import__("math").isnan(recent_comp_max) else 0,
        "trail": float(last.get("trail", 0)),
        "close": float(last.get("Close", 0)),
    }
