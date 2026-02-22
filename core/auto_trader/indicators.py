import numpy as np
import pandas as pd


def calculate_ema(data: np.ndarray, period: int) -> np.ndarray:
    """Calculate Exponential Moving Average"""
    ema = np.zeros_like(data, dtype=float)
    if len(data) == 0:
        return ema

    # Start with SMA for first value
    ema[0] = data[0]
    multiplier = 2 / (period + 1)

    for i in range(1, len(data)):
        ema[i] = (data[i] * multiplier) + (ema[i - 1] * (1 - multiplier))

    return ema


def calculate_kama(
        data: np.ndarray,
        er_period: int = 10,
        fast_period: int = 2,
        slow_period: int = 30,
) -> np.ndarray:
    """Kaufman's Adaptive Moving Average (KAMA)."""
    kama = np.zeros_like(data, dtype=float)
    length = len(data)
    if length == 0:
        return kama

    kama[0] = data[0]
    fast_sc = 2.0 / (max(1, fast_period) + 1.0)
    slow_sc = 2.0 / (max(1, slow_period) + 1.0)
    lookback = max(1, int(er_period))

    for i in range(1, length):
        start = max(0, i - lookback)
        direction = abs(data[i] - data[start])
        volatility = np.sum(np.abs(np.diff(data[start:i + 1])))
        efficiency_ratio = direction / volatility if volatility > 1e-12 else 0.0
        smoothing_constant = (efficiency_ratio * (fast_sc - slow_sc) + slow_sc) ** 2
        kama[i] = kama[i - 1] + smoothing_constant * (data[i] - kama[i - 1])

    return kama


def calculate_volatility_scaled_ema(
        data: np.ndarray,
        base_period: int = 21,
        min_period: int = 8,
        max_period: int = 55,
        volatility_window: int = 20,
) -> np.ndarray:
    """EMA whose period adapts to realized volatility regime."""
    length = len(data)
    ema = np.zeros(length, dtype=float)
    if length == 0:
        return ema

    returns = np.diff(data, prepend=data[0])
    abs_returns = np.abs(returns)
    short_vol = pd.Series(abs_returns).rolling(max(2, volatility_window // 2), min_periods=1).mean().to_numpy()
    long_vol = pd.Series(abs_returns).rolling(max(3, volatility_window), min_periods=1).mean().to_numpy()
    vol_ratio = np.divide(short_vol, np.maximum(long_vol, 1e-12))
    vol_ratio = np.clip(vol_ratio, 0.3, 3.0)  # Wider range handles Indian market open spikes

    # Higher volatility -> shorter EMA period (faster response)
    adaptive_period = base_period / vol_ratio
    adaptive_period = np.clip(adaptive_period, min_period, max_period)

    ema[0] = data[0]
    for i in range(1, length):
        alpha = 2.0 / (adaptive_period[i] + 1.0)
        ema[i] = alpha * data[i] + (1.0 - alpha) * ema[i - 1]

    return ema


def calculate_regime_trend_filter(data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Adaptive trend engine combining KAMA (slow) + volatility-scaled EMA (fast)."""
    fast = calculate_volatility_scaled_ema(data, base_period=18, min_period=6, max_period=34, volatility_window=20)
    slow = calculate_kama(data, er_period=10, fast_period=2, slow_period=30)
    return fast, slow



def calculate_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Calculate ATR using Wilder's smoothing (RMA), aligned to input length."""
    length = len(close)
    atr = np.zeros(length, dtype=float)
    if length == 0:
        return atr

    prev_close = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])

    atr[0] = tr[0]
    alpha = 1.0 / max(period, 1)
    for i in range(1, length):
        atr[i] = (tr[i] * alpha) + (atr[i - 1] * (1 - alpha))

    return atr



def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    length = len(close)
    if length < period + 5:
        return np.zeros(length)

    plus_dm = np.zeros(length)
    minus_dm = np.zeros(length)
    tr = np.zeros(length)

    for i in range(1, length):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]

        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )

    # Wilder smoothing — ATR, +DM, -DM all use the same method
    atr = np.zeros(length)
    smooth_plus_dm = np.zeros(length)
    smooth_minus_dm = np.zeros(length)

    # Seed with sum of first `period` bars (Wilder initialisation)
    atr[period] = np.sum(tr[1:period + 1])
    smooth_plus_dm[period] = np.sum(plus_dm[1:period + 1])
    smooth_minus_dm[period] = np.sum(minus_dm[1:period + 1])

    for i in range(period + 1, length):
        atr[i] = atr[i - 1] - (atr[i - 1] / period) + tr[i]
        smooth_plus_dm[i] = smooth_plus_dm[i - 1] - (smooth_plus_dm[i - 1] / period) + plus_dm[i]
        smooth_minus_dm[i] = smooth_minus_dm[i - 1] - (smooth_minus_dm[i - 1] / period) + minus_dm[i]

    safe_atr = np.where(atr > 1e-12, atr, np.nan)
    plus_di = 100.0 * smooth_plus_dm / safe_atr
    minus_di = 100.0 * smooth_minus_dm / safe_atr

    di_sum = np.abs(plus_di) + np.abs(minus_di)
    safe_di_sum = np.where(di_sum > 1e-12, di_sum, np.nan)
    dx = 100.0 * np.abs(plus_di - minus_di) / safe_di_sum

    # Wilder smoothing of DX to get ADX
    adx = np.zeros(length)
    first_valid = period * 2
    if first_valid < length:
        adx[first_valid] = np.nanmean(dx[period:first_valid + 1])
        for i in range(first_valid + 1, length):
            if np.isnan(dx[i]):
                adx[i] = adx[i - 1]
            else:
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return np.nan_to_num(adx)



def build_slope_direction_masks(series: np.ndarray, timeframe_minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """
    Build per-bar slope direction masks using two lookbacks:
    - 15 minutes
    - 30 minutes

    A direction qualifies when either lookback indicates that direction.
    """
    length = len(series)
    up_mask = np.zeros(length, dtype=bool)
    down_mask = np.zeros(length, dtype=bool)

    if length < 2:
        return up_mask, down_mask

    lookback_minutes = (15, 30)
    for minutes in lookback_minutes:
        bars_back = max(1, int(round(minutes / max(timeframe_minutes, 1))))
        if bars_back >= length:
            continue

        delta = np.zeros(length, dtype=float)
        delta[bars_back:] = series[bars_back:] - series[:-bars_back]
        up_mask |= delta > 0
        down_mask |= delta < 0

    return up_mask, down_mask



def is_chop_regime(
    idx: int,
    strategy_type: str | None,
    price: np.ndarray,
    ema_slow: np.ndarray,
    atr: np.ndarray,
    adx: np.ndarray,
    chop_filter_atr_reversal: bool = True,
    chop_filter_ema_cross: bool = True,
    chop_filter_atr_divergence: bool = True,
) -> bool:
    """
    Strategy-aware chop regime detection.

    Accepts pre-computed arrays so callers avoid redundant recalculation
    on every bar. Call this once per bar using cached indicator arrays.

    - range_breakout: NEVER filtered — chop is its setup.
    - ema_cross / atr_divergence / atr_reversal: filtered per toggle flags.
    """
    if strategy_type == "range_breakout":
        return False

    if strategy_type == "atr_reversal" and not chop_filter_atr_reversal:
        return False
    if strategy_type == "ema_cross" and not chop_filter_ema_cross:
        return False
    if strategy_type == "atr_divergence" and not chop_filter_atr_divergence:
        return False

    if idx is None or idx < 20:
        return False

    atr_val = max(float(atr[idx]), 1e-6)
    low_adx = adx[idx] < 18
    slope = ema_slow[idx] - ema_slow[idx - 5]
    flat = abs(slope) < (0.02 * atr_val)
    hugging = abs(price[idx] - ema_slow[idx]) < (0.25 * atr_val)

    return low_adx or (hugging and flat and adx[idx] < 22)