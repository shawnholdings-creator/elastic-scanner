"""
Scoring Engine — Weighted Score (v3.0)

Pure additive scoring with partial confluence. No rigid all-pass filters.
Every ticker gets a score. Tiers are assigned by score alone.

BASE FACTORS:
  +25  MTF aligned (all 3+ TFs agree)
  +20  Recent compression present
  +20  Fresh 4H flip / trigger
  +10  ALMA momentum confirms direction
  +10  RVOL (relative volume surge)
  +10  Sector strength (sector ETF aligned)
  +10  Relative strength (stock vs SPY)

PENALTIES:
  -20  extATR > 2.5
  -40  extATR > 3.5 (replaces -20)
  -25  Earnings within 7 days
  -15  Weak market regime (SPY below trail)

TIERS:
  90+  ELITE
  80-89 FIRE
  70-79 PREP
  <70  suppress

Anti-chase: extATR > 4.0 → hard suppress regardless of score.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    W_MTF_ALIGNED, W_COMPRESSION, W_FRESH_FLIP,
    W_ALMA_MOMENTUM, W_RVOL, W_SECTOR_STRENGTH, W_RELATIVE_STRENGTH,
    P_MID_EXT, P_HIGH_EXT, P_EARNINGS_NEAR, P_WEAK_REGIME,
    TIER_ELITE, TIER_FIRE, TIER_PREP,
    FRESH_FLIP_BARS, COMP_FIRE_MIN, VOL_SURGE_MULT,
    EXT_HARD_CEILING,
)


def score_to_tier(score: int) -> str:
    """Convert numeric score to tier label."""
    if score >= TIER_ELITE:
        return "ELITE"
    elif score >= TIER_FIRE:
        return "FIRE"
    elif score >= TIER_PREP:
        return "PREP"
    return "SUPPRESS"


def score_signal(signal: dict, market_context: dict = None) -> dict:
    """
    Score a signal using the weighted additive system.

    Args:
        signal: dict with ticker analysis data (from signal_engine)
        market_context: dict with market-level data:
            - spy_below_trail: bool — SPY is below its ATR trail
            - sector_bull: dict[str, bool] — {sector: is_bull} for sector ETFs
            - spy_perf_5d: float — SPY 5-day % return (for relative strength)
            - ticker_perf_5d: float — ticker 5-day % return
            - earnings_days: int — days until next earnings (None if unknown)

    Returns the mutated signal dict with 'score', 'tier', and 'factors'.
    """
    if market_context is None:
        market_context = {}

    eqs = 0
    factors = {}
    direction = signal.get("direction", "MIXED")

    # ─── +25 MTF Aligned ─────────────────────────────────────────
    mtf_str = signal.get("mtf", "")

    if direction == "BULL":
        mtf_aligned = mtf_str.count("+") >= 3
    elif direction == "BEAR":
        mtf_aligned = mtf_str.count("-") >= 3
    else:
        mtf_aligned = False

    if mtf_aligned:
        eqs += W_MTF_ALIGNED
        factors["mtf"] = W_MTF_ALIGNED
    else:
        factors["mtf"] = 0

    # ─── +20 Recent Compression ──────────────────────────────────
    comp = signal.get("comp_score", 0)
    recent_comp = signal.get("recent_comp_max", comp)
    has_compression = max(comp, recent_comp) >= COMP_FIRE_MIN

    if has_compression:
        eqs += W_COMPRESSION
        factors["compression"] = W_COMPRESSION
    else:
        factors["compression"] = 0

    # ─── +20 Fresh 4H Flip ───────────────────────────────────────
    flip_age = signal.get("flip_age", 999)
    has_fresh_flip = flip_age <= FRESH_FLIP_BARS

    if has_fresh_flip:
        eqs += W_FRESH_FLIP
        factors["fresh_flip"] = W_FRESH_FLIP
    else:
        factors["fresh_flip"] = 0

    # ─── +10 ALMA Momentum Confirms ──────────────────────────────
    alma_rising = signal.get("alma_rising", False)
    is_bull = direction == "BULL"

    if (is_bull and alma_rising) or (not is_bull and not alma_rising):
        eqs += W_ALMA_MOMENTUM
        factors["momentum"] = W_ALMA_MOMENTUM
    else:
        factors["momentum"] = 0

    # ─── +10 RVOL (Relative Volume) ──────────────────────────────
    vol_ratio = signal.get("vol_ratio", 1.0)

    if vol_ratio >= VOL_SURGE_MULT:
        eqs += W_RVOL
        factors["rvol"] = W_RVOL
    else:
        factors["rvol"] = 0

    # ─── +10 Sector Strength ─────────────────────────────────────
    sector = signal.get("sector", "")
    sector_bull = market_context.get("sector_bull", {})

    if sector and sector in sector_bull:
        sector_aligned = (
            (direction == "BULL" and sector_bull[sector]) or
            (direction == "BEAR" and not sector_bull[sector])
        )
        if sector_aligned:
            eqs += W_SECTOR_STRENGTH
            factors["sector"] = W_SECTOR_STRENGTH
        else:
            factors["sector"] = 0
    else:
        factors["sector"] = 0

    # ─── +10 Relative Strength ───────────────────────────────────
    ticker_perf = market_context.get("ticker_perf_5d", {}).get(signal.get("ticker", ""))
    spy_perf = market_context.get("spy_perf_5d")

    if ticker_perf is not None and spy_perf is not None:
        # Bull: stock outperforming SPY; Bear: stock underperforming SPY
        if direction == "BULL" and ticker_perf > spy_perf:
            eqs += W_RELATIVE_STRENGTH
            factors["relative_strength"] = W_RELATIVE_STRENGTH
        elif direction == "BEAR" and ticker_perf < spy_perf:
            eqs += W_RELATIVE_STRENGTH
            factors["relative_strength"] = W_RELATIVE_STRENGTH
        else:
            factors["relative_strength"] = 0
    else:
        factors["relative_strength"] = 0

    # ─── PENALTIES ───────────────────────────────────────────────

    # Extension penalty
    ext = signal.get("ext", 0)
    if ext > 3.5:
        eqs += P_HIGH_EXT  # -40
        factors["ext_penalty"] = P_HIGH_EXT
    elif ext > 2.5:
        eqs += P_MID_EXT   # -20
        factors["ext_penalty"] = P_MID_EXT
    else:
        factors["ext_penalty"] = 0

    # Earnings proximity penalty
    earnings_days = market_context.get("earnings_days", {}).get(signal.get("ticker", ""))
    if earnings_days is not None and 0 <= earnings_days <= 7:
        eqs += P_EARNINGS_NEAR  # -25
        factors["earnings_penalty"] = P_EARNINGS_NEAR
    else:
        factors["earnings_penalty"] = 0

    # Weak market regime penalty
    spy_below_trail = market_context.get("spy_below_trail", False)
    if spy_below_trail and direction == "BULL":
        eqs += P_WEAK_REGIME  # -15
        factors["regime_penalty"] = P_WEAK_REGIME
    elif not spy_below_trail and direction == "BEAR":
        # Bearish signal in strong market regime also penalized
        eqs += P_WEAK_REGIME
        factors["regime_penalty"] = P_WEAK_REGIME
    else:
        factors["regime_penalty"] = 0

    # ─── Clamp to 0-100 ─────────────────────────────────────────
    eqs = max(0, min(100, eqs))

    # ─── Anti-chase hard suppress ────────────────────────────────
    if ext > EXT_HARD_CEILING:
        eqs = 0

    # ─── Tier assignment ─────────────────────────────────────────
    tier = score_to_tier(eqs)

    signal["score"] = eqs
    signal["tier"] = tier
    signal["factors"] = factors

    return signal
