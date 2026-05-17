"""
Elastic Engine — ATR Trail State Machine + Compression + Extension

Direct port of the ThinkScript Elastic Trend ATR trail logic.
Operates on a single-ticker OHLCV DataFrame per timeframe.
"""

import numpy as np
import pandas as pd

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    ATR_PERIOD, ATR_FACTOR, BB_LEN, BB_MULT, KC_MULT,
    VOL_AVG_PERIOD,
)


# ─── ALMA (Arnaud Legoux Moving Average) ───────────────────────────
def alma(series: pd.Series, period: int = 9, offset: float = 0.85, sigma: int = 6) -> pd.Series:
    """
    Compute ALMA — a Gaussian-weighted moving average that reduces lag.
    Used for momentum detection (smoother than SMA, less lag than EMA).
    """
    m = offset * (period - 1)
    s = period / sigma
    weights = np.array([np.exp(-((i - m) ** 2) / (2 * s * s)) for i in range(period)])
    weights /= weights.sum()

    result = series.rolling(window=period).apply(
        lambda x: np.dot(x, weights), raw=True
    )
    return result


# ─── Modified ATR (ThinkScript SuperTrend variant) ─────────────────
def compute_loss(h: pd.Series, l: pd.Series, c: pd.Series,
                 atr_period: int = ATR_PERIOD, atr_factor: float = ATR_FACTOR) -> pd.Series:
    """
    Compute the ATR 'loss' value — the distance from price that triggers
    a trend flip. Uses the same HiLo/HRef/LRef capping from ThinkScript.
    """
    # HiLo: min(H-L, 1.5 * SMA(H-L, period))
    hl_range = h - l
    avg_hl = hl_range.rolling(atr_period).mean()
    hilo = np.minimum(hl_range, 1.5 * avg_hl)

    # HRef / LRef — directional range components
    h_prev = h.shift(1)
    l_prev = l.shift(1)
    c_prev = c.shift(1)

    href = np.where(
        l <= h_prev,
        h - c_prev,
        (h - c_prev) - 0.5 * (l - h_prev)
    )
    lref = np.where(
        h >= l_prev,
        c_prev - l,
        (c_prev - l) - 0.5 * (l_prev - h)
    )

    true_range = np.maximum(hilo, np.maximum(href, lref))
    tr_series = pd.Series(true_range, index=h.index)

    # Wilder's moving average (EWM with alpha = 1/period)
    wilders_ma = tr_series.ewm(alpha=1.0 / atr_period, adjust=False).mean()

    return atr_factor * wilders_ma


# ─── ATR Trail State Machine ──────────────────────────────────────
def atr_trail(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run the ATR trail state machine on an OHLCV DataFrame.
    
    Returns DataFrame with added columns:
        - trail: ATR trailing stop value
        - is_bull: True if trend is long
        - flip_event: True on the bar where trend flipped
        - flip_bar_age: bars since last flip
        - ext: extension ratio (distance from trail in ATR units)
        - extremum: running max high (bull) or min low (bear)
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

    # State machine — must iterate (state depends on previous state)
    # States: 0=init, 1=long, 2=short
    state = np.zeros(n, dtype=int)

    for i in range(n):
        if np.isnan(loss[i]):
            state[i] = 0
            trail[i] = np.nan
            extremum[i] = np.nan
            continue

        if i == 0 or state[i - 1] == 0:
            # Init → long
            state[i] = 1
            trail[i] = c[i] - loss[i]
            extremum[i] = h[i]
            continue

        prev_state = state[i - 1]
        prev_trail = trail[i - 1]

        if prev_state == 1:  # was long
            if c[i] > prev_trail:
                state[i] = 1
                trail[i] = max(prev_trail, c[i] - loss[i])
                extremum[i] = max(extremum[i - 1], h[i])
            else:
                state[i] = 2
                trail[i] = c[i] + loss[i]
                flip_event[i] = True
                extremum[i] = l[i]
        else:  # was short
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

    # Extension ratio: distance from trail in ATR(14) units
    atr14 = df["High"].sub(df["Low"]).rolling(14).mean()  # simple ATR
    atr14_vals = atr14.values

    ext = np.zeros(n)
    for i in range(n):
        if np.isnan(trail[i]) or np.isnan(atr14_vals[i]) or atr14_vals[i] <= 0:
            ext[i] = 0.0
        elif is_bull[i]:
            ext[i] = (c[i] - trail[i]) / atr14_vals[i]
        else:
            ext[i] = (trail[i] - c[i]) / atr14_vals[i]

    # Flip bar age (bars since last flip)
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

    return df


# ─── Compression Score (5 signals) ────────────────────────────────
def compression_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute 5-signal compression score matching ThinkScript Dashboard logic:
    
    1. BB inside KC (TTM Squeeze equivalent)
    2. ATR contraction (≤ 85% of 10-bar avg)
    3. Range contraction (≤ 80% of 20-bar avg)
    4. Volume dryup (≤ 80% of 20-bar avg)
    5. BB midline proximity (within 1.5% of BB mid)
    
    Returns DataFrame with added columns:
        - comp_score: 0-5 integer
        - in_squeeze: bool (BB inside KC)
    """
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    v = df["Volume"]

    # BB
    mid_bb = c.rolling(BB_LEN).mean()
    std_bb = c.rolling(BB_LEN).std()
    upper_bb = mid_bb + BB_MULT * std_bb
    lower_bb = mid_bb - BB_MULT * std_bb

    # KC
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr_kc = tr.rolling(BB_LEN).mean()
    upper_kc = mid_bb + KC_MULT * atr_kc
    lower_kc = mid_bb - KC_MULT * atr_kc

    # Signal 1: BB inside KC
    sig1 = (upper_bb <= upper_kc) & (lower_bb >= lower_kc)

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

    return df


# ─── ALMA Momentum ────────────────────────────────────────────────
def alma_momentum(df: pd.DataFrame, period: int = 9,
                  offset: float = 0.85, sigma: int = 6) -> pd.DataFrame:
    """
    Add ALMA-based momentum columns.
    
    Returns DataFrame with:
        - alma_val: ALMA value
        - alma_rising: True if ALMA is rising
        - alma_slope: normalized slope (for scoring)
    """
    df = df.copy()
    df["alma_val"] = alma(df["Close"], period, offset, sigma)
    df["alma_rising"] = df["alma_val"] > df["alma_val"].shift(1)

    # Normalized slope for scoring (% change)
    df["alma_slope"] = (
        (df["alma_val"] - df["alma_val"].shift(1)) / df["alma_val"].shift(1) * 100
    ).fillna(0)

    return df


# ─── Full Pipeline (single ticker, single TF) ─────────────────────
def process_ticker_tf(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run the full elastic engine on a single ticker's OHLCV DataFrame.
    Chains: ATR trail → compression → ALMA momentum.
    
    Returns enriched DataFrame with all computed columns.
    """
    if df.empty or len(df) < ATR_PERIOD + 10:
        return pd.DataFrame()

    df = atr_trail(df)
    df = compression_score(df)
    df = alma_momentum(df)

    return df
