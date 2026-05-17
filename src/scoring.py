"""
Scoring Engine — Multi-Factor Composite Scoring

Produces a 0-100 composite score for each signal based on:
MTF alignment, compression, momentum, volume, and extension.

Returns factor breakdown for future ML/AI ranking.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    W_MTF, W_COMPRESSION, W_MOMENTUM, W_VOLUME, W_EXTENSION,
)


def score_signal(signal: dict) -> dict:
    """
    Score a signal dict (from signal_engine.classify_signal).
    
    Adds 'score' (0-100 composite) and 'factors' (breakdown dict)
    to the signal dict.
    
    Returns the mutated signal dict.
    """
    # ─── Factor 1: MTF Alignment (30%) ────────────────────────────
    mtf_str = signal.get("mtf", "")
    plus_count = mtf_str.count("+")
    minus_count = mtf_str.count("-")

    if signal["direction"] == "BULL":
        mtf_score = (plus_count / 3) * 100
    elif signal["direction"] == "BEAR":
        mtf_score = (minus_count / 3) * 100
    else:
        mtf_score = 0

    # ─── Factor 2: Compression (25%) ─────────────────────────────
    comp = signal.get("comp_score", 0)
    comp_score = min(100, (comp / 5) * 100)

    # ─── Factor 3: Momentum (20%) ────────────────────────────────
    # ALMA rising + direction match = full score
    alma_rising = signal.get("alma_rising", False)
    is_bull = signal["direction"] == "BULL"

    if (is_bull and alma_rising) or (not is_bull and not alma_rising):
        mom_score = 100
    elif alma_rising is None:
        mom_score = 0
    else:
        mom_score = 20  # momentum exists but wrong direction

    # ─── Factor 4: Volume (15%) ──────────────────────────────────
    vol_ratio = signal.get("vol_ratio", 1.0)
    vol_score = min(100, vol_ratio * 50)  # 2.0x ratio = 100

    # ─── Factor 5: Extension (10%) — inverse scoring ─────────────
    ext = signal.get("ext", 0)
    ext_score = max(0, 100 - ext * 25)  # 0 ext = 100, 4 ext = 0

    # ─── Composite ───────────────────────────────────────────────
    composite = (
        W_MTF * mtf_score +
        W_COMPRESSION * comp_score +
        W_MOMENTUM * mom_score +
        W_VOLUME * vol_score +
        W_EXTENSION * ext_score
    )

    signal["score"] = round(composite, 1)
    signal["factors"] = {
        "mtf": round(mtf_score, 1),
        "compression": round(comp_score, 1),
        "momentum": round(mom_score, 1),
        "volume": round(vol_score, 1),
        "extension": round(ext_score, 1),
    }

    return signal
