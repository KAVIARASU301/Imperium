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

    # Wilder smoothing
    atr = np.zeros(length)
    atr[period] = np.mean(tr[1:period + 1])

    for i in range(period + 1, length):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    plus_di = 100 * (pd.Series(plus_dm).rolling(period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(period).mean() / atr)

    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.rolling(period).mean()

    return np.nan_to_num(adx.values)



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



def is_chop_regime(self, idx: int, strategy_type: str = None) -> bool:
    """
    Strategy-aware chop regime detection.

    - range_breakout: NEVER filtered — chop is its setup.
    - ema_cross / atr_divergence / atr_reversal: filtered per user toggle.
    """
    # Range breakout thrives in chop — never filter it
    if strategy_type == "range_breakout":
        return False

    # Per-strategy opt-out
    if strategy_type == "atr_reversal" and not getattr(self, "_chop_filter_atr_reversal", True):
        return False
    if strategy_type == "ema_cross" and not getattr(self, "_chop_filter_ema_cross", True):
        return False
    if strategy_type == "atr_divergence" and not getattr(self, "_chop_filter_atr_divergence", True):
        return False

    if idx is None or idx < 20:
        return False

    price = np.array(self.all_price_data, dtype=float)
    high = np.array(self.all_price_high_data, dtype=float)
    low = np.array(self.all_price_low_data, dtype=float)

    ema51 = calculate_ema(price, 51)
    atr = calculate_atr(high, low, price, 14)
    adx = compute_adx(high, low, price, 14)

    atr_val = max(float(atr[idx]), 1e-6)

    low_adx = adx[idx] < 18
    slope = ema51[idx] - ema51[idx - 5]
    flat = abs(slope) < (0.02 * atr_val)
    hugging = abs(price[idx] - ema51[idx]) < (0.25 * atr_val)

    return low_adx or (hugging and flat and adx[idx] < 22)


