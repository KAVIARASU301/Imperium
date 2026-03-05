"""
core/auto_trader/indicators.py
================================
Pure-NumPy technical indicators used by the auto-trader and chart dialogs.

Design principles (institutional grade):
  - All functions accept np.ndarray, return np.ndarray of the same length.
  - No look-ahead bias: every value at index i only uses data up to index i.
  - Wilder smoothing (RMA) for ATR / ADX — same as TradingView / Bloomberg.
  - VWAP resets per session key — matches exchange VWAP exactly.
"""

from __future__ import annotations

import numpy as np
from typing import Sequence


# ---------------------------------------------------------------------------
# EMA  (Exponential Moving Average)
# ---------------------------------------------------------------------------

def calculate_ema(data: np.ndarray, period: int) -> np.ndarray:
    """
    Standard EMA with SMA seed for the first window.

    Multiplier k = 2 / (period + 1)  — same as most charting platforms.
    The first `period` values are seeded with the simple mean so the warmup
    artefact is minimised.
    """
    data = np.asarray(data, dtype=float)
    n = len(data)
    if n == 0:
        return data.copy()

    result = np.empty(n, dtype=float)
    k = 2.0 / (period + 1)

    # Seed: SMA of first `period` values (or all values if n < period)
    seed_len = min(period, n)
    seed = float(np.mean(data[:seed_len]))
    result[0] = seed

    for i in range(1, n):
        result[i] = data[i] * k + result[i - 1] * (1.0 - k)

    return result


# ---------------------------------------------------------------------------
# VWAP  (Volume-Weighted Average Price, session-reset)
# ---------------------------------------------------------------------------

def calculate_vwap(
    price: np.ndarray,
    volume: np.ndarray,
    session_keys: Sequence | None = None,
) -> np.ndarray:
    """
    VWAP that resets at the start of each trading session.

    Parameters
    ----------
    price        : close prices (or typical price if you prefer)
    volume       : bar volumes
    session_keys : list of date/key per bar; resets cumsum when key changes.
                   If None, treated as a single session.

    Institutions use VWAP as a benchmark execution price — a fill above VWAP
    for buys is considered poor execution, below is alpha.
    """
    price = np.asarray(price, dtype=float)
    volume = np.asarray(volume, dtype=float)
    n = len(price)
    if n == 0:
        return price.copy()

    result = np.empty(n, dtype=float)
    cum_pv = 0.0
    cum_v = 0.0
    prev_key = session_keys[0] if session_keys else None

    for i in range(n):
        key = session_keys[i] if session_keys else None
        if key != prev_key:
            cum_pv = 0.0
            cum_v = 0.0
            prev_key = key

        cum_pv += price[i] * volume[i]
        cum_v += volume[i]
        result[i] = cum_pv / cum_v if cum_v > 0 else price[i]

    return result


# ---------------------------------------------------------------------------
# ATR  (Average True Range — Wilder / RMA smoothing)
# ---------------------------------------------------------------------------

def calculate_atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    Wilder's ATR.  True Range = max(H-L, |H-Cp|, |L-Cp|).
    Smoothed with Wilder's RMA: atr[i] = (atr[i-1]*(period-1) + tr[i]) / period.

    ATR is the institutional standard for:
      - Position sizing  (risk = N * ATR)
      - Stop placement   (stop = entry ± k * ATR)
      - Regime detection (is the market moving enough to trade?)
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    if n == 0:
        return np.array([], dtype=float)

    tr = np.empty(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    atr = np.empty(n, dtype=float)
    # Seed with SMA
    seed_len = min(period, n)
    atr[seed_len - 1] = float(np.mean(tr[:seed_len]))
    for i in range(seed_len, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    # Fill warm-up with seed value so length is always n
    atr[:seed_len - 1] = atr[seed_len - 1]

    return atr


# ---------------------------------------------------------------------------
# ADX  (Average Directional Index — Wilder)
# ---------------------------------------------------------------------------

def compute_adx(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    Wilder's ADX with +DI / -DI.
    Returns only ADX (0–100).  ADX > 25 → trending; < 20 → choppy.

    Institutions treat ADX as a *trend strength* filter:
      - ADX rising above 20-25 = trend starting (enter breakouts)
      - ADX > 40 = overextended (reversal risk)
      - ADX falling = trend exhausting (tighten exits)
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    if n < 2:
        return np.zeros(n, dtype=float)

    # Directional movement
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_arr = np.empty(n - 1, dtype=float)
    for i in range(n - 1):
        tr_arr[i] = max(
            high[i + 1] - low[i + 1],
            abs(high[i + 1] - close[i]),
            abs(low[i + 1] - close[i]),
        )

    # Wilder smooth
    def _wilder(arr: np.ndarray) -> np.ndarray:
        out = np.empty(len(arr), dtype=float)
        seed_len = min(period, len(arr))
        out[seed_len - 1] = float(np.sum(arr[:seed_len]))
        for j in range(seed_len, len(arr)):
            out[j] = out[j - 1] - out[j - 1] / period + arr[j]
        out[:seed_len - 1] = out[seed_len - 1]
        return out

    s_tr = _wilder(tr_arr)
    s_plus = _wilder(plus_dm)
    s_minus = _wilder(minus_dm)

    with np.errstate(invalid="ignore", divide="ignore"):
        di_plus = np.where(s_tr > 0, 100.0 * s_plus / s_tr, 0.0)
        di_minus = np.where(s_tr > 0, 100.0 * s_minus / s_tr, 0.0)
        dx = np.where(
            (di_plus + di_minus) > 0,
            100.0 * np.abs(di_plus - di_minus) / (di_plus + di_minus),
            0.0,
        )

    adx_raw = np.empty(len(dx), dtype=float)
    seed_len = min(period, len(dx))
    adx_raw[seed_len - 1] = float(np.mean(dx[:seed_len]))
    for i in range(seed_len, len(dx)):
        adx_raw[i] = (adx_raw[i - 1] * (period - 1) + dx[i]) / period
    adx_raw[:seed_len - 1] = adx_raw[seed_len - 1]

    # Pad back to length n (prepend a zero for the first bar)
    adx_full = np.empty(n, dtype=float)
    adx_full[0] = 0.0
    adx_full[1:] = adx_raw

    return adx_full


# ---------------------------------------------------------------------------
# Slope direction masks
# ---------------------------------------------------------------------------

def build_slope_direction_masks(
    data: np.ndarray,
    period: int = 3,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (rising_mask, falling_mask) based on whether the EMA of `data`
    has a positive / negative slope over `period` bars.

    Used to confirm that EMAs are pointing in the direction of a trade.
    Institutions call this "EMA slope confirmation" — you don't buy into a
    falling EMA even if price crosses above it.
    """
    data = np.asarray(data, dtype=float)
    n = len(data)
    ema = calculate_ema(data, period)

    rising = np.zeros(n, dtype=bool)
    falling = np.zeros(n, dtype=bool)

    if n < 2:
        return rising, falling

    slope = np.diff(ema, prepend=ema[0])
    rising = slope > 0
    falling = slope < 0

    return rising, falling


# ---------------------------------------------------------------------------
# Chop regime  (ADX + ATR ratio filter)
# ---------------------------------------------------------------------------

def is_chop_regime(
    atr_values: np.ndarray,
    adx_values: np.ndarray,
    adx_threshold: float = 20.0,
    atr_ratio_threshold: float = 0.8,
    lookback: int = 10,
) -> np.ndarray:
    """
    Returns a boolean mask where True = choppy / no-trade zone.

    Logic (institutional chop filter):
      1. ADX < threshold  → no directional strength
      2. Current ATR / mean(ATR, lookback) < ratio_threshold
         → volatility contracting (range-bound)

    Both must be true to flag chop.  When chop=True, signal generators
    should suppress entries — false breakouts are very common in chop.
    """
    atr = np.asarray(atr_values, dtype=float)
    adx = np.asarray(adx_values, dtype=float)
    n = len(atr)

    chop = np.zeros(n, dtype=bool)
    for i in range(n):
        start = max(0, i - lookback + 1)
        mean_atr = float(np.mean(atr[start : i + 1]))
        atr_ratio = (atr[i] / mean_atr) if mean_atr > 0 else 1.0
        chop[i] = (adx[i] < adx_threshold) and (atr_ratio < atr_ratio_threshold)

    return chop


# ---------------------------------------------------------------------------
# Regime trend filter  (dual EMA — fast / slow)
# ---------------------------------------------------------------------------

def calculate_regime_trend_filter(
    data: np.ndarray,
    fast_period: int = 20,
    slow_period: int = 50,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (fast_ema, slow_ema).

    Institutional use: regime = BULLISH when fast > slow, BEARISH when fast < slow.
    Used as a higher-timeframe directional bias filter — only take longs in bull
    regime, shorts in bear regime.  Dramatically reduces whipsaws.
    """
    data = np.asarray(data, dtype=float)
    return calculate_ema(data, fast_period), calculate_ema(data, slow_period)


# ---------------------------------------------------------------------------
# CVD Z-Score
# ---------------------------------------------------------------------------

def calculate_cvd_zscore(
    cvd: np.ndarray,
    period: int = 20,
) -> np.ndarray:
    """
    Rolling Z-score of CVD: (CVD - mean(CVD, period)) / std(CVD, period).

    Z-score > +2  → extreme buying pressure (mean-reversion risk for longs)
    Z-score < -2  → extreme selling pressure (mean-reversion risk for shorts)

    Institutions use this to size down or skip entries when CVD is already
    at a statistical extreme — the easy money has been made.
    """
    cvd = np.asarray(cvd, dtype=float)
    n = len(cvd)
    result = np.zeros(n, dtype=float)

    for i in range(n):
        start = max(0, i - period + 1)
        window = cvd[start : i + 1]
        mu = float(np.mean(window))
        sigma = float(np.std(window))
        result[i] = (cvd[i] - mu) / sigma if sigma > 1e-9 else 0.0

    return result