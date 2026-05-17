"""
Signal Engine — Full Verdict Classification (Dashboard v2.5 Parity)

Classifies tickers using the same priority ladder as the PineScript
Elastic Dashboard v2.5:

1. ELITE ENTRY       - all 7 conditions (expansion + candle + ALMA accel + vol + low ext)
2. IDEAL ENTRY       - pullback to ALMA21 support zone + compressed/building
3. SLINGSHOT FIRED   - fresh flip + wasCoiled + MTF + expansionFire
4. PRIME ENTRY       - coiled + aligned + low ext + above ALMA34 + above trail
5. SLING LOADING     - compScore >= 3 + MTF aligned (armed)
6. FIRE              - fresh 4H flip + comp + momentum + vol (simplified sling)
7. PREP              - MTF aligned + compressed + ideal zone
8. EXTENDED          - aligned but overextended (suppressed from ntfy)

Only ELITE, IDEAL, SLINGSHOT, FIRE, PRIME, and PREP are actionable.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    EXT_LOW, EXT_FIRE_MAX, EXT_HIGH, EXT_ELITE,
    COMP_PREP_MIN, COMP_FIRE_MIN, COMP_FIRE_LOOKBACK,
    FRESH_FLIP_BARS, VOL_SURGE_MULT,
)


def classify_signal(ticker: str, data_4h: dict, data_1d: dict,
                    data_1w: dict, data_1m: dict = None) -> dict | None:
    """
    Classify a ticker using Dashboard v2.5 verdict priority ladder.
    Returns dict with signal info or None if no signal.
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
    mtf_aligned = triple_bull or triple_bear

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
    alma8 = data_4h.get("alma8", 0)
    alma21 = data_4h.get("alma21", 0)
    alma34 = data_4h.get("alma34", 0)
    atr14 = data_4h.get("atr14", 0)
    open_air = data_4h.get("open_air", False)
    tension = data_4h.get("tension", 1.0)

    # Momentum confirms direction
    if triple_bull:
        momentum_ok = alma_rising
    elif triple_bear:
        momentum_ok = not alma_rising
    else:
        momentum_ok = False

    # Slingshot detection
    flip_event = flip_age == 0
    was_coiled = recent_comp >= 3

    # Build base result dict
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
        "bull_count": bull_count,
        "expansion_fire": expansion_fire,
        "strong_candle": strong_bull_bar if direction == "BULL" else strong_bear_bar,
        "alma_accel": alma_accel_bull if direction == "BULL" else alma_accel_bear,
        "is_compressed": is_compressed,
        "is_building": is_building,
        "is_stretched": is_stretched,
        "tension": round(tension, 2),
        "open_air": open_air,
    }

    # ─── 1. ELITE ENTRY ──────────────────────────────────────────
    elite_bull = (triple_bull and expansion_fire and rv_bull and
                  strong_bull_bar and alma_accel_bull and
                  ext <= EXT_ELITE and close > alma8 and close > trail)
    elite_bear = (triple_bear and expansion_fire and
                  strong_bear_bar and alma_accel_bear and
                  ext <= EXT_ELITE and close < alma8 and close < trail)

    if elite_bull or elite_bear:
        return {**base, "signal": "ELITE"}

    # ─── 2. IDEAL ENTRY ──────────────────────────────────────────
    if direction == "BULL":
        ideal = (bull_count >= 3 and close > trail and
                 close <= alma21 and atr14 > 0 and
                 close >= alma21 - 2.0 * atr14 and
                 not is_stretched and (is_compressed or is_building))
    elif direction == "BEAR":
        ideal = (bull_count <= 1 and close < trail and
                 close >= alma21 and atr14 > 0 and
                 close <= alma21 + 2.0 * atr14 and
                 not is_stretched and (is_compressed or is_building))
    else:
        ideal = False

    if ideal:
        return {**base, "signal": "IDEAL"}

    # ─── 3. SLINGSHOT FIRED ──────────────────────────────────────
    sling_bull = (flip_event and is_bull and was_coiled and
                  bull_1d and bull_1w and expansion_fire)
    sling_bear = (flip_event and not is_bull and was_coiled and
                  not bull_1d and not bull_1w and expansion_fire)

    if sling_bull or sling_bear:
        return {**base, "signal": "SLINGSHOT"}

    # ─── 4. PRIME ENTRY ──────────────────────────────────────────
    if direction == "BULL":
        prime = ((is_compressed or is_building) and bull_count >= 3 and
                 ext <= EXT_FIRE_MAX and close > alma34 and close > trail)
    elif direction == "BEAR":
        prime = ((is_compressed or is_building) and bull_count <= 1 and
                 ext <= EXT_FIRE_MAX and close < alma34 and close < trail)
    else:
        prime = False

    if prime:
        return {**base, "signal": "PRIME"}

    # ─── 5. FIRE ─────────────────────────────────────────────────
    if (mtf_aligned
        and flip_age <= FRESH_FLIP_BARS
        and recent_comp >= COMP_FIRE_MIN
        and momentum_ok
        and vol_ratio >= VOL_SURGE_MULT
        and ext <= EXT_FIRE_MAX):
        return {**base, "signal": "FIRE"}

    # ─── 6. PREP ─────────────────────────────────────────────────
    if (mtf_aligned
        and comp_now >= COMP_PREP_MIN
        and ext <= EXT_LOW):
        return {**base, "signal": "PREP"}

    # ─── 7. EXTENDED ─────────────────────────────────────────────
    if (mtf_aligned and ext > EXT_HIGH):
        return {**base, "signal": "EXTENDED"}

    return None


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
    }
