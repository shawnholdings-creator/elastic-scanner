"""
Scoring Engine — Entry Quality Score (Additive)

Additive scoring system (0-100) that rewards actionable setups
and penalizes overextension. Each condition adds or subtracts points.

+25  MTF aligned (all 3 TFs agree)
+20  Recent compression present
+20  Fresh 4H flip / trigger
+15  ALMA momentum confirms direction
+10  Volume surge confirms
+10  Good extension (< 1.5 ATR from trail)
-25  Extension > 2.5 ATR
-40  Extension > 3.5 ATR (replaces -25)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    EQS_MTF_ALIGNED, EQS_COMPRESSION, EQS_FRESH_FLIP,
    EQS_MOMENTUM, EQS_VOLUME, EQS_GOOD_EXT,
    EQS_PENALTY_MID_EXT, EQS_PENALTY_HIGH_EXT,
    FRESH_FLIP_BARS, COMP_FIRE_MIN, VOL_SURGE_MULT,
)


def score_signal(signal: dict) -> dict:
    """
    Score a signal using the additive Entry Quality Score system.

    Adds 'score' (0-100 clamped) and 'factors' (breakdown dict)
    to the signal dict.

    Returns the mutated signal dict.
    """
    eqs = 0
    factors = {}

    # ─── +25 MTF Aligned ─────────────────────────────────────────
    mtf_str = signal.get("mtf", "")
    direction = signal.get("direction", "MIXED")

    if direction == "BULL":
        mtf_aligned = mtf_str.count("+") == 3
    elif direction == "BEAR":
        mtf_aligned = mtf_str.count("-") == 3
    else:
        mtf_aligned = False

    if mtf_aligned:
        eqs += EQS_MTF_ALIGNED
        factors["mtf"] = EQS_MTF_ALIGNED
    else:
        factors["mtf"] = 0

    # ─── +20 Recent Compression ──────────────────────────────────
    comp = signal.get("comp_score", 0)
    recent_comp = signal.get("recent_comp_max", comp)
    has_compression = max(comp, recent_comp) >= COMP_FIRE_MIN

    if has_compression:
        eqs += EQS_COMPRESSION
        factors["compression"] = EQS_COMPRESSION
    else:
        factors["compression"] = 0

    # ─── +20 Fresh 4H Flip ───────────────────────────────────────
    flip_age = signal.get("flip_age", 999)
    has_fresh_flip = flip_age <= FRESH_FLIP_BARS

    if has_fresh_flip:
        eqs += EQS_FRESH_FLIP
        factors["fresh_flip"] = EQS_FRESH_FLIP
    else:
        factors["fresh_flip"] = 0

    # ─── +15 Momentum Confirms ───────────────────────────────────
    alma_rising = signal.get("alma_rising", False)
    is_bull = direction == "BULL"

    if (is_bull and alma_rising) or (not is_bull and not alma_rising):
        eqs += EQS_MOMENTUM
        factors["momentum"] = EQS_MOMENTUM
    else:
        factors["momentum"] = 0

    # ─── +10 Volume Surge ────────────────────────────────────────
    vol_ratio = signal.get("vol_ratio", 1.0)

    if vol_ratio >= VOL_SURGE_MULT:
        eqs += EQS_VOLUME
        factors["volume"] = EQS_VOLUME
    else:
        factors["volume"] = 0

    # ─── +10 Good Extension ──────────────────────────────────────
    ext = signal.get("ext", 0)

    if ext <= 1.5:
        eqs += EQS_GOOD_EXT
        factors["extension"] = EQS_GOOD_EXT
    else:
        factors["extension"] = 0

    # ─── Penalties ───────────────────────────────────────────────
    if ext > 3.5:
        eqs += EQS_PENALTY_HIGH_EXT  # -40
        factors["ext_penalty"] = EQS_PENALTY_HIGH_EXT
    elif ext > 2.5:
        eqs += EQS_PENALTY_MID_EXT   # -25
        factors["ext_penalty"] = EQS_PENALTY_MID_EXT
    else:
        factors["ext_penalty"] = 0

    # ─── Clamp to 0-100 ─────────────────────────────────────────
    eqs = max(0, min(100, eqs))

    signal["score"] = eqs
    signal["factors"] = factors

    return signal
