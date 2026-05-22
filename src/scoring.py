"""
Scoring Engine — Elastic Dashboard v3.0 Parity

Direct port of the PineScript scoring engine from Elastic_Dashboard_v3.0.pine.
Uses 3-pillar granular scoring with partial credit:

  STRUCTURE (0-45): MTF alignment (graduated) + compression score (graduated)
  MOMENTUM  (0-35): Expansion fire + ALMA stack/slope
  RS        (0-20): RVOL ratio + relative strength vs SPY/QQQ

  PENALTIES:
    extATR > 3.5 → -30
    extATR > 2.5 → -20
    Weak RS (underperforming both SPY & QQQ by > 2%) → -20

  TOTAL = clamp(struct + momt + rs + penalties, 0, 100)

  QUALITY TIERS:
    75+ = ELITE
    55+ = GOOD
    35+ = EARLY
    20+ = HOT
    <20 = POOR

Anti-chase: extATR > 4.0 → hard suppress.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import EXT_HARD_CEILING


# ─── Quality Tier Thresholds ───────────────────────────────────────
QUALITY_ELITE = 75
QUALITY_GOOD = 50    # lowered from 55 — old threshold made B-grade nearly impossible
QUALITY_EARLY = 30
QUALITY_HOT = 20


def score_to_quality(score: int) -> str:
    """Convert numeric score to quality label (Pine line 406)."""
    if score >= QUALITY_ELITE:
        return "ELITE"
    elif score >= QUALITY_GOOD:
        return "GOOD"
    elif score >= QUALITY_EARLY:
        return "EARLY"
    elif score >= QUALITY_HOT:
        return "HOT"
    return "POOR"


def score_to_tier(score: int) -> str:
    """Legacy tier for backward compat — maps to setup type."""
    if score >= 90:
        return "ELITE"
    elif score >= 80:
        return "FIRE"
    elif score >= 70:
        return "PREP"
    return "SUPPRESS"


# ─── STRUCTURE Sub-Score (0-50) ──────────────────────────────────
def _score_structure(signal: dict) -> tuple[int, dict]:
    """
    Pine lines 383-385 (adapted):
    sATR = dirCount >= 4 ? 25 : >= 3 ? 18 : >= 2 ? 10 : >= 1 ? 5 : 0
    sComp = compScore >= 4 ? 20 : >= 3 ? 15 : >= 2 ? 10 : >= 1 ? 5 : 0
    sTrend = dirCount >= 3 AND ext <= 2.5 ? 5 : 0  (trend persistence bonus)
    structScore = sATR + sComp + sTrend
    """
    bull_count = signal.get("bull_count", 0)
    direction = signal.get("direction", "MIXED")

    # dirCount: for bulls = bullCount, for bears = 4 - bullCount
    if direction == "BULL":
        dir_count = bull_count
    elif direction == "BEAR":
        dir_count = 4 - bull_count
    else:
        # MIXED: use raw bullCount if > 2, else bearCount
        dir_count = max(bull_count, 4 - bull_count)

    # ATR trail alignment (graduated)
    if dir_count >= 4:
        s_atr = 25
    elif dir_count >= 3:
        s_atr = 18
    elif dir_count >= 2:
        s_atr = 10
    elif dir_count >= 1:
        s_atr = 5
    else:
        s_atr = 0

    # Compression score (graduated)
    comp = signal.get("comp_score", 0)
    if comp >= 4:
        s_comp = 20
    elif comp >= 3:
        s_comp = 15
    elif comp >= 2:
        s_comp = 10
    elif comp >= 1:
        s_comp = 5
    else:
        s_comp = 0

    # Trend persistence bonus: strong alignment + not overextended
    ext = signal.get("ext", 0)
    s_trend = 5 if (dir_count >= 3 and ext <= 2.5) else 0

    struct_score = s_atr + s_comp + s_trend
    details = {"s_atr": s_atr, "s_comp": s_comp, "s_trend": s_trend, "struct_total": struct_score}
    return struct_score, details


# ─── MOMENTUM Sub-Score (0-35) ─────────────────────────────────
def _score_momentum(signal: dict) -> tuple[int, dict]:
    """
    Pine lines 388-390 (adapted for bear/mixed parity):
    sExp = dirExpFire ? 20 : (atrRise and rangeExpand) ? 14 : (atrRise or rangeExpand) ? 8 : 0
    sAlma = dirStack and dirSlope ? 15 : dirStack or dirSlope ? 8 : 0
    momScore = sExp + sAlma

    Key fix: BEAR direction now gets full momentum credit (was effectively 0).
    MIXED direction gets 75% partial credit (was 60%).
    ALMA scoring gives partial credit for stack OR slope (was 0 for slope-only).
    """
    direction = signal.get("direction", "MIXED")
    is_bull = direction == "BULL"
    is_bear = direction == "BEAR"
    is_mixed = direction == "MIXED"
    is_bull_4h = signal.get("is_bull_4h", False)

    # For MIXED: use 4H direction as the reference
    if is_mixed:
        use_bull = is_bull_4h
    else:
        use_bull = is_bull

    # Expansion fire (compression→expansion transition + direction aligned)
    expansion_fire = signal.get("expansion_fire", False)
    atr_rise = signal.get("atr_rise", False)
    range_expand = signal.get("range_expand", False)

    # dir_trend: 4H direction matches overall direction
    # For BEAR: 4H bearish = dir_trend aligned
    if is_bull:
        dir_trend = is_bull_4h
    elif is_bear:
        dir_trend = not is_bull_4h
    else:
        dir_trend = True  # MIXED: 4H is the reference, always "aligned" with itself

    # dirExpFire: expansion fire + direction aligned
    dir_exp_fire = expansion_fire and dir_trend

    # Expansion scoring — no longer requires dir_trend for partial credit
    if dir_exp_fire:
        s_exp = 20
    elif (atr_rise and range_expand) and dir_trend:
        s_exp = 14
    elif (atr_rise and range_expand):
        s_exp = 10  # both firing but trend not fully aligned — still significant
    elif (atr_rise or range_expand) and dir_trend:
        s_exp = 8
    elif (atr_rise or range_expand):
        s_exp = 5  # partial credit for any expansion signal
    else:
        s_exp = 0

    # ALMA stack + slope (use 4H direction for MIXED)
    if use_bull:
        alma_stack = signal.get("alma_stack_bull", False)
        alma_slope = signal.get("alma21_rising", False)
    else:
        alma_stack = signal.get("alma_stack_bear", False)
        alma_slope = signal.get("alma21_falling", False)

    if alma_stack and alma_slope:
        s_alma = 15
    elif alma_stack:
        s_alma = 10
    elif alma_slope:
        s_alma = 8   # raised from 5 — slope alone is meaningful directional momentum
    else:
        s_alma = 0

    # MIXED direction penalty: reduce momentum by 25% (was 40% — too harsh)
    if is_mixed:
        s_exp = int(s_exp * 0.75)
        s_alma = int(s_alma * 0.75)

    mom_score = s_exp + s_alma
    details = {"s_exp": s_exp, "s_alma": s_alma, "mom_total": mom_score}
    return mom_score, details


# ─── RS Sub-Score (0-20) ──────────────────────────────────────
def _score_rs(signal: dict, market_context: dict) -> tuple[int, dict]:
    """
    Pine lines 393-395:
    sRvol = rvolRatio >= 1.5 ? 10 : >= 1.25 ? 7 : >= 1.0 ? 3 : 0
    sRSval = rsVsSpy > 0 and rsVsQqq > 0 ? 10 : one > 0 ? 5 : 0
    rsScoreVal = sRvol + sRSval
    """
    direction = signal.get("direction", "MIXED")
    is_bull = direction == "BULL"

    # RVOL ratio (graduated)
    vol_ratio = signal.get("vol_ratio", 0.0)
    if vol_ratio >= 1.5:
        s_rvol = 10
    elif vol_ratio >= 1.25:
        s_rvol = 7
    elif vol_ratio >= 1.0:
        s_rvol = 3
    else:
        s_rvol = 0

    # Relative strength vs SPY and QQQ
    rs_vs_spy = signal.get("rs_vs_spy", 0.0)
    rs_vs_qqq = signal.get("rs_vs_qqq", 0.0)

    if is_bull:
        if rs_vs_spy > 0 and rs_vs_qqq > 0:
            s_rs = 10
        elif rs_vs_spy > 0 or rs_vs_qqq > 0:
            s_rs = 5
        else:
            s_rs = 0
    else:  # BEAR or MIXED
        if rs_vs_spy < 0 and rs_vs_qqq < 0:
            s_rs = 10
        elif rs_vs_spy < 0 or rs_vs_qqq < 0:
            s_rs = 5
        else:
            s_rs = 0

    rs_score = s_rvol + s_rs
    details = {"s_rvol": s_rvol, "s_rs": s_rs, "rs_total": rs_score}
    return rs_score, details


# ─── Penalties ────────────────────────────────────────────────
def _apply_penalties(signal: dict) -> tuple[int, dict]:
    """
    Softened penalties (v3.1):
    penStretch = extATR > 3.5 ? -20 : extATR > 2.5 ? -10 : 0
    Removed weak RS penalty — RS data is unreliable and was double-penalizing.
    """
    ext = signal.get("ext", 0)

    # Extension penalty (softened — old values were -30/-20, too harsh)
    if ext > 3.5:
        pen_stretch = -20
    elif ext > 2.5:
        pen_stretch = -10
    else:
        pen_stretch = 0

    pen_total = pen_stretch
    details = {"pen_stretch": pen_stretch, "pen_total": pen_total}
    return pen_total, details


# ─── Setup Detection (from Pine alert conditions) ─────────────
def _detect_setup(signal: dict) -> str:
    """
    Detect setup type matching Pine alertconditions:
    - SLINGSHOT: slingFire (fresh flip + wasCoiled + MTF aligned + expansion fire)
    - FIRE: trendBull + (slingFire or expansionFire) + ext <= 2.0
    - TRIGGER: bullCount >= 3 + not stretched + ext <= 2.5
    - COIL: isBull + close <= ema21 + close > trail (reloading)
    - ACTIVE: isBull + extended
    - WATCH: everything else
    """
    direction = signal.get("direction", "MIXED")
    is_bull = direction == "BULL"
    bull_count = signal.get("bull_count", 0)
    ext = signal.get("ext", 0)
    is_stretched = signal.get("is_stretched", False)
    expansion_fire = signal.get("expansion_fire", False)
    flip_age = signal.get("flip_age", 999)
    comp_score = signal.get("comp_score", 0)
    recent_comp_max = signal.get("recent_comp_max", 0)
    close = signal.get("close", 0)
    alma21 = signal.get("alma21", 0)
    trail = signal.get("trail", 0)

    # Slingshot: fresh flip + was compressed + MTF aligned + expansion fire
    was_coiled = recent_comp_max >= 3
    mtf_bull_aligned = bull_count >= 3
    fresh_flip = flip_age <= 4

    triple_bull = bull_count >= 3 and signal.get("is_bull_4h", False)
    triple_bear = (4 - bull_count) >= 3 and not signal.get("is_bull_4h", True)

    # SLINGSHOT FIRE (most precise - maps to slingFire alertcondition)
    if is_bull and fresh_flip and was_coiled and mtf_bull_aligned and expansion_fire:
        return "SLINGSHOT"
    if not is_bull and fresh_flip and was_coiled and (4 - bull_count) >= 3 and expansion_fire:
        return "SLINGSHOT"

    # FIRE: triple aligned + (expansion OR tight to trail) + manageable extension
    #   - Classic: expansion_fire + ext <= 2.0
    #   - Trend fire: 3+ aligned + ext <= 1.5 (strong trend near trail, no expansion needed)
    if triple_bull and expansion_fire and ext <= 2.0:
        return "FIRE"
    if triple_bear and expansion_fire and ext <= 2.0:
        return "FIRE"
    if triple_bull and ext <= 1.5 and not is_stretched:
        return "FIRE"
    if triple_bear and ext <= 1.5 and not is_stretched:
        return "FIRE"

    # TRIGGER: 3+ TFs aligned, reasonable extension
    #   - Clean: not stretched + ext <= 2.5
    #   - Mild stretch OK if ext <= 2.0 (BB expanding but price still near trail)
    if bull_count >= 3 and ext <= 2.5 and is_bull:
        if not is_stretched or ext <= 2.0:
            return "TRIGGER"
    if (4 - bull_count) >= 3 and ext <= 2.5 and not is_bull:
        if not is_stretched or ext <= 2.0:
            return "TRIGGER"

    # COIL: bull 4H + pulling back to ALMA21 + above trail (reloading zone)
    is_bull_4h = signal.get("is_bull_4h", False)
    if is_bull_4h and close > 0 and alma21 > 0 and trail > 0:
        if close <= alma21 and close > trail:
            return "COIL"

    # ACTIVE: bull but truly extended (ext > 3.0 or heavily stretched)
    if is_bull_4h and ext > 3.0:
        return "ACTIVE"

    return "WATCH"


# ─── Main Scorer ─────────────────────────────────────────────
def score_signal(signal: dict, market_context: dict = None) -> dict:
    """
    Score a signal using the Elastic Dashboard v3.0 3-pillar system.

    Returns mutated signal with:
      score, quality, tier, setup, struct_score, mom_score, rs_score, penalties, factors
    """
    if market_context is None:
        market_context = {}

    # Compute sub-scores
    struct_score, struct_details = _score_structure(signal)
    mom_score, mom_details = _score_momentum(signal)
    rs_score, rs_details = _score_rs(signal, market_context)
    penalties, pen_details = _apply_penalties(signal)

    # Total (clamped 0-100)
    total = max(0, min(100, struct_score + mom_score + rs_score + penalties))

    # Anti-chase hard suppress
    ext = signal.get("ext", 0)
    if ext > EXT_HARD_CEILING:
        total = 0

    # Quality + tier + setup
    quality = score_to_quality(total)
    tier = score_to_tier(total)
    setup = _detect_setup(signal)

    # Enrich signal
    signal["score"] = total
    signal["quality"] = quality
    signal["tier"] = tier
    signal["setup"] = setup
    signal["struct_score"] = struct_score
    signal["mom_score"] = mom_score
    signal["rs_score"] = rs_score
    signal["penalties"] = penalties
    signal["factors"] = {
        **struct_details,
        **mom_details,
        **rs_details,
        **pen_details,
    }

    return signal
