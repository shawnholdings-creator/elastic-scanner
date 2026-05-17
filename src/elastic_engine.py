"""
Elastic Engine — ATR Trail + Compression + Tension + ALMA + Entry Timing

Full port of the ThinkScript/PineScript Elastic Dashboard v2.5 logic.
Operates on a single-ticker OHLCV DataFrame per timeframe.

Computes:
  - ATR trail state machine (trend, trail, extension, extremum)
  - 5-signal compression score (BB/KC, ATR, range, volume, midline)
  - Tension ratio (BB width / avg BB width → COIL/BUILDING/HOT)
  - ALMA momentum (direction + acceleration)
  - Candle quality (body %, close position)
  - ATR expansion ignition
  - Compression→Expansion transition (expansionFire)
"""

import numpy as np
import pandas as pd

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    ATR_PERIOD, ATR_FACTOR, BB_LEN, BB_MULT, KC_MULT,
    VOL_AVG_PERIOD, TENSION_COMPRESSED, TENSION_BUILDING, TENSION_STRETCHED,
    CANDLE_BODY_MIN, CANDLE_CLOSE_ZONE, VOL_RV_MULT,
)


# ─── ALMA (Arnaud Legoux Moving Average) ───────────────────────────
def alma(series: pd.Series, period: int = 9, offset: float = 0.85, sigma: int = 6) -> pd.Series:
    """Compute ALMA — Gaussian-weighted MA that reduces lag."""
    m = offset * (period - 1)
    s = period / sigma
    weights = np.array([np.exp(-((i - m) ** 2) / (2 * s * s)) for i in range(period)])
    weights /= weights.sum()
    return series.rolling(window=period).apply(lambda x: np.dot(x, weights), raw=True)


# ─── Modified ATR (ThinkScript SuperTrend variant) ─────────────────
def compute_loss(h: pd.Series, l: pd.Series, c: pd.Series,
                 atr_period: int = ATR_PERIOD, atr_factor: float = ATR_FACTOR) -> pd.Series:
    """Compute the ATR 'loss' value using HiLo/HRef/LRef capping."""
    hl_range = h - l
    avg_hl = hl_range.rolling(atr_period).mean()
    hilo = np.minimum(hl_range, 1.5 * avg_hl)

    h_prev = h.shift(1)
    l_prev = l.shift(1)
    c_prev = c.shift(1)

    href = np.where(l <= h_prev, h - c_prev, (h - c_prev) - 0.5 * (l - h_prev))
    lref = np.where(h >= l_prev, c_prev - l, (c_prev - l) - 0.5 * (l_prev - h))

    true_range = np.maximum(hilo, np.maximum(href, lref))
    tr_series = pd.Series(true_range, index=h.index)

    # Wilder's moving average (RMA)
    wilders_ma = tr_series.ewm(alpha=1.0 / atr_period, adjust=False).mean()
    return atr_factor * wilders_ma


# ─── ATR Trail State Machine ──────────────────────────────────────
def atr_trail(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run the ATR trail state machine. Returns DataFrame with:
    trail, is_bull, flip_event, flip_bar_age, ext, extremum
    """
    h = df["High"].values
    l = df["Low"].values
    c = df["Close"].values

    loss_series = compute_loss(df["High"], df["Low"], df["Close"])
    loss = loss_series.values

    n = len(c)
    trail = np.full(n, np.nan)
    is_bull = np.full(n, True)
    flip_event = np.full(n, False)
    extremum = np.full(n, np.nan)
    state = np.zeros(n, dtype=int)

    for i in range(n):
        if np.isnan(loss[i]):
            state[i] = 0
            trail[i] = np.nan
            extremum[i] = np.nan
            continue

        if i == 0 or state[i - 1] == 0:
            state[i] = 1
            trail[i] = c[i] - loss[i]
            extremum[i] = h[i]
            continue

        prev_state = state[i - 1]
        prev_trail = trail[i - 1]

        if prev_state == 1:
            if c[i] > prev_trail:
                state[i] = 1
                trail[i] = max(prev_trail, c[i] - loss[i])
                extremum[i] = max(extremum[i - 1], h[i])
            else:
                state[i] = 2
                trail[i] = c[i] + loss[i]
                flip_event[i] = True
                extremum[i] = l[i]
        else:
            if c[i] < prev_trail:
                state[i] = 2
                trail[i] = min(prev_trail, c[i] + loss[i])
                extremum[i] = min(extremum[i - 1], l[i])
            else:
                state[i] = 1
                trail[i] = c[i] - loss[i]
                flip_event[i] = True
                extremum[i] = h[i]

        is_bull[i] = state[i] == 1

    # Extension ratio
    atr14 = df["High"].sub(df["Low"]).rolling(14).mean()
    atr14_vals = atr14.values

    ext = np.zeros(n)
    for i in range(n):
        if np.isnan(trail[i]) or np.isnan(atr14_vals[i]) or atr14_vals[i] <= 0:
            ext[i] = 0.0
        elif is_bull[i]:
            ext[i] = (c[i] - trail[i]) / atr14_vals[i]
        else:
            ext[i] = (trail[i] - c[i]) / atr14_vals[i]

    # Flip bar age
    flip_age = np.zeros(n, dtype=int)
    last_flip = -999
    for i in range(n):
        if flip_event[i]:
            last_flip = i
        flip_age[i] = i - last_flip if last_flip >= 0 else 999

    df = df.copy()
    df["trail"] = trail
    df["is_bull"] = is_bull
    df["flip_event"] = flip_event
    df["flip_bar_age"] = flip_age
    df["ext"] = ext
    df["extremum"] = extremum
    df["atr14"] = atr14

    return df


# ─── Compression Score (5 signals) ────────────────────────────────
def compression_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    5-signal compression score matching Dashboard v2.5:
    1. BB inside KC   2. ATR contraction   3. Range contraction
    4. Volume dryup   5. BB midline proximity
    """
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    v = df["Volume"]

    # BB
    mid_bb = c.rolling(BB_LEN).mean()
    std_bb = c.rolling(BB_LEN).std()

    # KC
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr_kc = tr.rolling(BB_LEN).mean()

    # Signal 1: BB inside KC
    sig1 = (2 * std_bb <= 2 * KC_MULT * atr_kc)

    # Signal 2: ATR contraction
    atr_now = tr.rolling(14).mean()
    atr_avg = atr_now.rolling(10).mean()
    sig2 = atr_now <= (atr_avg * 0.85)

    # Signal 3: Range contraction
    bar_range = h - l
    avg_range = bar_range.rolling(20).mean()
    sig3 = bar_range <= (avg_range * 0.80)

    # Signal 4: Volume dryup
    avg_vol = v.rolling(VOL_AVG_PERIOD).mean()
    sig4 = v <= (avg_vol * 0.80)

    # Signal 5: BB midline proximity
    bb_proximity = (c - mid_bb).abs() / mid_bb
    sig5 = bb_proximity <= 0.015

    df = df.copy()
    df["in_squeeze"] = sig1.astype(bool)
    df["comp_score"] = (
        sig1.astype(int) + sig2.astype(int) + sig3.astype(int) +
        sig4.astype(int) + sig5.astype(int)
    )
    df["vol_ratio"] = (v / avg_vol).fillna(1.0)
    df["mid_bb"] = mid_bb
    df["std_bb"] = std_bb

    return df


# ─── Tension Ratio (BB Width / Avg BB Width) ─────────────────────
def tension_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """
    Dashboard v2.5 tension system:
    tensionVal = bbWidth / avgBBWidth
    isCompressed = tensionVal <= 0.60  (COIL)
    isBuilding   = 0.60 < tensionVal <= 1.00  (BLD)
    isStretched  = tensionVal > 1.40  (HOT)
    """
    df = df.copy()
    bb_width = 2 * df["std_bb"]
    avg_bb_width = bb_width.rolling(BB_LEN).mean()

    df["tension"] = np.where(avg_bb_width > 0, bb_width / avg_bb_width, 1.0)
    df["is_compressed"] = df["tension"] <= TENSION_COMPRESSED
    df["is_building"] = (df["tension"] > TENSION_COMPRESSED) & (df["tension"] <= TENSION_BUILDING)
    df["is_stretched"] = df["tension"] > TENSION_STRETCHED

    return df


# ─── ALMA Ribbon + Momentum + Acceleration ───────────────────────
def alma_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """
    Dashboard v2.5 ALMA system:
    - ALMA(9) for momentum direction
    - ALMA(8) for acceleration detection
    - ALMA(8/13/21/34) ribbon values for zone checks
    """
    df = df.copy()
    c = df["Close"]

    # ALMA(9) — primary momentum
    df["alma_val"] = alma(c, 9, 0.85, 6)
    df["alma_rising"] = df["alma_val"] > df["alma_val"].shift(1)
    df["alma_slope"] = (
        (df["alma_val"] - df["alma_val"].shift(1)) / df["alma_val"].shift(1) * 100
    ).fillna(0)

    # ALMA ribbon (8/13/21/34) — Dashboard v2.5
    df["alma8"] = alma(c, 8, 0.85, 6)
    df["alma13"] = alma(c, 13, 0.85, 6)
    df["alma21"] = alma(c, 21, 0.85, 6)
    df["alma34"] = alma(c, 34, 0.85, 6)

    # ALMA Acceleration: (alma8 - alma8[1]) > (alma8[1] - alma8[2])
    alma8_d1 = df["alma8"] - df["alma8"].shift(1)
    alma8_d2 = df["alma8"].shift(1) - df["alma8"].shift(2)
    df["alma_accel_bull"] = alma8_d1 > alma8_d2
    df["alma_accel_bear"] = (-alma8_d1) > (-alma8_d2)  # bear: falling faster

    return df


# ─── Candle Quality ──────────────────────────────────────────────
def candle_quality(df: pd.DataFrame) -> pd.DataFrame:
    """
    Dashboard v2.5 candle quality filters:
    - bodyPct = abs(close - open) / (high - low) >= 0.60
    - strongBullBar: close > open, close in top 25% of range
    - strongBearBar: close < open, close in bottom 25% of range
    """
    df = df.copy()
    o = df["Open"]
    h = df["High"]
    l = df["Low"]
    c = df["Close"]

    bar_range = h - l
    body_pct = np.where(bar_range > 0, (c - o).abs() / bar_range, 0.0)
    df["body_pct"] = body_pct

    df["strong_bull_bar"] = (
        (c > o) &
        (body_pct >= CANDLE_BODY_MIN) &
        (c >= h - bar_range * CANDLE_CLOSE_ZONE)
    )

    df["strong_bear_bar"] = (
        (c < o) &
        (body_pct >= CANDLE_BODY_MIN) &
        (c <= l + bar_range * CANDLE_CLOSE_ZONE)
    )

    return df


# ─── ATR Expansion Ignition + Compression→Expansion ─────────────
def expansion_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Dashboard v2.5 expansion detection:
    - atrRise: ATR(14) rising for 3 consecutive bars
    - rangeExpand: current true range > previous
    - expansionFire: was compressed recently + atrRise + rangeExpand
    - rvBull: volume > SMA(vol,20) * 1.25 OR 2 consecutive rising volume
    """
    df = df.copy()
    atr14 = df["atr14"]

    # ATR Expansion Ignition: ATR rising 3 bars
    df["atr_rise"] = (atr14 > atr14.shift(1)) & (atr14.shift(1) > atr14.shift(2))

    # Range expansion
    tr = df["High"] - df["Low"]  # simplified TR
    df["range_expand"] = tr > tr.shift(1)

    # Was compressed recently (compScore >= 3 in last 2 bars)
    comp = df["comp_score"]
    df["compressed_recently"] = (comp.shift(1) >= 3) | (comp.shift(2) >= 3)

    # Compression → Expansion transition (THE key slingshot confirmation)
    df["expansion_fire"] = df["compressed_recently"] & df["atr_rise"] & df["range_expand"]

    # Relative Volume (Dashboard v2.5 style)
    avg_vol = df["Volume"].rolling(VOL_AVG_PERIOD).mean()
    vol = df["Volume"]
    df["rv_bull"] = (vol > avg_vol * VOL_RV_MULT) | ((vol > vol.shift(1)) & (vol.shift(1) > vol.shift(2)))

    return df


# ─── Swing Structure (Pivot Highs/Lows) ─────────────────────────
def swing_structure(df: pd.DataFrame, pivot_len: int = 5, lookback: int = 100) -> pd.DataFrame:
    """
    Dashboard v2.5 swing detection:
    - Pivot highs/lows (5-bar lookback/forward)
    - Next swing high above current price (supply)
    - Open air detection (no resistance above)
    """
    df = df.copy()
    h = df["High"].values
    l = df["Low"].values
    c = df["Close"].values
    n = len(c)

    # Detect pivot highs and lows
    pvt_hi = np.full(n, np.nan)
    pvt_lo = np.full(n, np.nan)

    for i in range(pivot_len, n - pivot_len):
        # Pivot high: highest in window
        if h[i] == max(h[i - pivot_len:i + pivot_len + 1]):
            pvt_hi[i] = h[i]
        # Pivot low: lowest in window
        if l[i] == min(l[i - pivot_len:i + pivot_len + 1]):
            pvt_lo[i] = l[i]

    # Track last swing low
    last_swing_low = np.full(n, np.nan)
    for i in range(n):
        if not np.isnan(pvt_lo[i]):
            last_swing_low[i] = pvt_lo[i]
        elif i > 0:
            last_swing_low[i] = last_swing_low[i - 1]

    # Find next swing high above price (nearest supply)
    next_sh_above = np.full(n, np.nan)
    for i in range(n):
        # Look back through recent pivot highs
        start = max(0, i - lookback)
        for j in range(i, start, -1):
            if not np.isnan(pvt_hi[j]) and pvt_hi[j] > c[i]:
                if np.isnan(next_sh_above[i]):
                    next_sh_above[i] = pvt_hi[j]
                else:
                    next_sh_above[i] = min(next_sh_above[i], pvt_hi[j])

    df["last_swing_low"] = last_swing_low
    df["next_sh_above"] = next_sh_above
    df["open_air"] = np.isnan(next_sh_above)

    # Supply ATR distance
    atr14 = df["atr14"].values
    supply_atr = np.full(n, np.nan)
    for i in range(n):
        if not np.isnan(next_sh_above[i]) and not np.isnan(atr14[i]) and atr14[i] > 0 and c[i] < next_sh_above[i]:
            supply_atr[i] = (next_sh_above[i] - c[i]) / atr14[i]
    df["supply_atr"] = supply_atr

    return df


# ─── Full Pipeline (single ticker, single TF) ─────────────────────
def process_ticker_tf(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run the full elastic engine pipeline:
    ATR trail → compression → tension → ALMA → candle → expansion → swings
    """
    if df.empty or len(df) < ATR_PERIOD + 10:
        return pd.DataFrame()

    df = atr_trail(df)
    df = compression_score(df)
    df = tension_ratio(df)
    df = alma_momentum(df)
    df = candle_quality(df)
    df = expansion_signals(df)
    df = swing_structure(df)

    return df
