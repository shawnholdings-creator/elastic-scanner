"""
Signal Engine — Weighted Classification (v3.0)

No rigid all-pass gates. Every ticker that passes MTF extraction
gets a base dict with all computed values. The scoring engine
assigns tiers (ELITE/FIRE/PREP/SUPPRESS) by weighted score alone.

This module:
  1. Extracts MTF alignment + 4H values
  2. Builds a base signal dict for the scoring engine
  3. Determines direction (BULL/BEAR/MIXED)

Classification is purely score-based — partial confluence allowed.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    EXT_LOW, EXT_FIRE_MAX, EXT_HIGH, EXT_ELITE,
    COMP_PREP_MIN, COMP_FIRE_MIN, COMP_FIRE_LOOKBACK,
    FRESH_FLIP_BARS, VOL_SURGE_MULT,
)


def classify_signal(ticker: str, data_4h: dict, data_1d: dict,
                    data_1w: dict, data_1m: dict = None,
                    sector: str = "") -> dict | None:
    """
    Build a signal dict for the scoring engine.
    No rigid gates — every ticker with valid MTF data gets a signal.
    Direction is determined by majority vote across TFs.

    Returns dict with signal info or None if data is missing.
    """
    if not all([data_4h, data_1d, data_1w]):
        return None

    # ─── MTF Alignment ────────────────────────────────────────────
    bull_4h = data_4h["is_bull"]
    bull_1d = data_1d["is_bull"]
    bull_1w = data_1w["is_bull"]
    bull_1m = data_1m["is_bull"] if data_1m else bull_1w  # fallback to weekly

    triple_bull = bull_4h and bull_1d and bull_1w
    triple_bear = (not bull_4h) and (not bull_1d) and (not bull_1w)

    # Direction: triple alignment → strong direction; otherwise MIXED (suppressed by scoring)
    direction = "BULL" if triple_bull else "BEAR" if triple_bear else "MIXED"
    mtf_label = _mtf_label(bull_4h, bull_1d, bull_1w)

    # bullCount (4-way like Dashboard v2.5)
    bull_count = sum([bull_4h, bull_1d, bull_1w, bull_1m])

    # ─── Extract 4H values (primary TF) ───────────────────────────
    ext = data_4h["ext"]
    flip_age = data_4h["flip_bar_age"]
    comp_now = data_4h["comp_score"]
    recent_comp = data_4h.get("recent_comp_max", comp_now)
    alma_rising = data_4h["alma_rising"]
    vol_ratio = data_4h["vol_ratio"]
    is_bull = data_4h["is_bull"]

    # v2.5 precision fields
    expansion_fire = data_4h.get("expansion_fire", False)
    strong_bull_bar = data_4h.get("strong_bull_bar", False)
    strong_bear_bar = data_4h.get("strong_bear_bar", False)
    alma_accel_bull = data_4h.get("alma_accel_bull", False)
    alma_accel_bear = data_4h.get("alma_accel_bear", False)
    rv_bull = data_4h.get("rv_bull", False)
    is_compressed = data_4h.get("is_compressed", False)
    is_building = data_4h.get("is_building", False)
    is_stretched = data_4h.get("is_stretched", False)
    close = data_4h.get("close", 0)
    trail = data_4h.get("trail", 0)
    tension = data_4h.get("tension", 1.0)
    open_air = data_4h.get("open_air", False)

    # ALMA stack + slope (Dashboard v3.0 scoring)
    alma8 = data_4h.get("alma8", 0)
    alma13 = data_4h.get("alma13", 0)
    alma21 = data_4h.get("alma21", 0)
    alma34 = data_4h.get("alma34", 0)
    alma_stack_bull = alma8 > alma13 and alma13 > alma21 and alma21 > alma34 if all([alma8, alma13, alma21, alma34]) else False
    alma_stack_bear = alma8 < alma13 and alma13 < alma21 and alma21 < alma34 if all([alma8, alma13, alma21, alma34]) else False
    alma21_val = alma21
    alma21_prev = data_4h.get("alma21_prev3", alma21_val)  # alma21[3] for slope
    alma21_rising = alma21_val > alma21_prev if alma21_val and alma21_prev else False
    alma21_falling = alma21_val < alma21_prev if alma21_val and alma21_prev else False

    # Expansion sub-signals (for momentum scoring)
    atr_rise = data_4h.get("atr_rise", False)
    range_expand = data_4h.get("range_expand", False)
    compressed_recently = data_4h.get("compressed_recently", False)

    # Build signal dict — scoring engine handles all tier assignment
    return {
        "ticker": ticker,
        "direction": direction,
        "sector": sector,
        "ext": round(ext, 2),
        "mtf": mtf_label,
        "comp_score": comp_now,
        "recent_comp_max": recent_comp,
        "flip_age": flip_age,
        "alma_rising": alma_rising,
        "vol_ratio": round(vol_ratio, 2),
        "bull_count": bull_count,
        "expansion_fire": expansion_fire,
        "strong_candle": strong_bull_bar if direction == "BULL" else strong_bear_bar,
        "alma_accel": alma_accel_bull if direction == "BULL" else alma_accel_bear,
        "is_compressed": is_compressed,
        "is_building": is_building,
        "is_stretched": is_stretched,
        "tension": round(tension, 2),
        "open_air": open_air,
        "close": close,
        "trail": trail,
        # v3.0 scoring fields
        "is_bull_4h": is_bull,
        "alma_stack_bull": alma_stack_bull,
        "alma_stack_bear": alma_stack_bear,
        "alma21_rising": alma21_rising,
        "alma21_falling": alma21_falling,
        "alma21": alma21,
        "atr_rise": atr_rise,
        "range_expand": range_expand,
        "compressed_recently": compressed_recently,
    }


def _mtf_label(bull_4h: bool, bull_1d: bool, bull_1w: bool) -> str:
    """Build MTF alignment label like '4H+ 1D+ 1W+'."""
    return (
        f"4H{'+'if bull_4h else'-'} "
        f"1D{'+'if bull_1d else'-'} "
        f"1W{'+'if bull_1w else'-'}"
    )


def extract_last_bar(df) -> dict | None:
    """
    Extract the last bar's computed values from an elastic_engine DataFrame.
    Includes all v2.5 fields for the full verdict classification.
    """
    if df is None or df.empty:
        return None

    last = df.iloc[-1]

    # Recent compression max
    lookback = min(COMP_FIRE_LOOKBACK, len(df))
    recent_comp_max = df["comp_score"].iloc[-lookback:].max()

    import math

    return {
        # Core
        "is_bull": bool(last.get("is_bull", False)),
        "ext": float(last.get("ext", 0)),
        "comp_score": int(last.get("comp_score", 0)),
        "flip_bar_age": int(last.get("flip_bar_age", 999)),
        "alma_rising": bool(last.get("alma_rising", False)),
        "vol_ratio": float(last.get("vol_ratio", 1.0)),
        "recent_comp_max": int(recent_comp_max) if not (isinstance(recent_comp_max, float) and math.isnan(recent_comp_max)) else 0,
        "trail": float(last.get("trail", 0)),
        "close": float(last.get("Close", 0)),
        # v2.5 precision fields
        "expansion_fire": bool(last.get("expansion_fire", False)),
        "strong_bull_bar": bool(last.get("strong_bull_bar", False)),
        "strong_bear_bar": bool(last.get("strong_bear_bar", False)),
        "alma_accel_bull": bool(last.get("alma_accel_bull", False)),
        "alma_accel_bear": bool(last.get("alma_accel_bear", False)),
        "rv_bull": bool(last.get("rv_bull", False)),
        "is_compressed": bool(last.get("is_compressed", False)),
        "is_building": bool(last.get("is_building", False)),
        "is_stretched": bool(last.get("is_stretched", False)),
        "tension": float(last.get("tension", 1.0)),
        "alma8": float(last.get("alma8", 0)),
        "alma21": float(last.get("alma21", 0)),
        "alma34": float(last.get("alma34", 0)),
        "atr14": float(last.get("atr14", 0)),
        "open_air": bool(last.get("open_air", False)),
        # v3.0 scoring fields
        "alma13": float(last.get("alma13", 0)),
        "alma21_prev3": float(df["alma21"].iloc[-4]) if len(df) >= 4 and "alma21" in df.columns else float(last.get("alma21", 0)),
        "atr_rise": bool(last.get("atr_rise", False)),
        "range_expand": bool(last.get("range_expand", False)),
        "compressed_recently": bool(last.get("compressed_recently", False)),
    }
