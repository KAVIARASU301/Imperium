import numpy as np
import pandas as pd

ADX_WARMUP_DEFAULT = 28.0  # Neutral ADX value during warm-up (pre-Wilder validity)

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


def calculate_vwap(
        price: np.ndarray,
        volume: np.ndarray,
        session_keys=None,
) -> np.ndarray:
    """Calculate VWAP, optionally resetting cumulative totals per session key."""
    price_arr = np.asarray(price, dtype=float)
    volume_arr = np.asarray(volume, dtype=float)
    length = len(price_arr)

    vwap = np.zeros(length, dtype=float)
    if length == 0:
        return vwap

    if len(volume_arr) != length:
        volume_arr = np.ones(length, dtype=float)

    volume_arr = np.clip(volume_arr, a_min=0.0, a_max=None)

    cumulative_pv = 0.0
    cumulative_volume = 0.0
    previous_session = object()

    for i in range(length):
        current_session = session_keys[i] if session_keys is not None else None
        if session_keys is not None and i > 0 and current_session != previous_session:
            cumulative_pv = 0.0
            cumulative_volume = 0.0

        cumulative_pv += price_arr[i] * volume_arr[i]
        cumulative_volume += volume_arr[i]
        vwap[i] = cumulative_pv / cumulative_volume if cumulative_volume > 1e-12 else price_arr[i]
        previous_session = current_session

    return vwap


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


def calculate_cvd_zscore(
    cvd: np.ndarray,
    ema_period: int = 51,
    zscore_window: int = 50,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute CVD z-score distance from its EMA.

    Replaces ATR-based CVD distance — ATR is mathematically invalid
    on cumulative series like CVD (the high/low per bar have no true range).

    Returns:
        zscore      : signed z-score of (CVD - EMA) for each bar
        cvd_ema     : the EMA51 of CVD (reused downstream)

    Institutional interpretation:
        |z| > 2.0  → CVD strongly extended  (high confidence reversal zone)
        |z| > 1.5  → moderate extension
        |z| < 1.0  → CVD hugging EMA       (low confidence, filter out)
    """
    n = len(cvd)
    zscore = np.zeros(n, dtype=float)
    cvd_ema = calculate_ema(cvd, ema_period)

    deviation = cvd - cvd_ema  # signed: positive = above EMA

    # Rolling std of deviation over zscore_window bars
    dev_series = pd.Series(deviation)
    rolling_std = dev_series.rolling(window=zscore_window, min_periods=max(5, zscore_window // 4)).std().to_numpy()

    # Avoid division by zero
    safe_std = np.where(rolling_std > 1e-9, rolling_std, np.nan)
    zscore = np.nan_to_num(deviation / safe_std, nan=0.0)

    return zscore, cvd_ema



def compute_adx(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    Compute Wilder's ADX with proper warm-up handling.

    Institutional correction:
        - True ADX becomes valid only after (2 * period - 1) bars.
        - During warm-up, return neutral ADX values instead of zeros
          to prevent false chop classification.
    """

    length = len(close)
    if length == 0:
        return np.array([], dtype=float)

    min_bars_needed = 2 * period  # Wilder requirement

    # ─────────────────────────────────────────────
    # WARM-UP GUARD (Institutional Fix)
    # ─────────────────────────────────────────────
    if period <= 0 or length < min_bars_needed:
        warmup = np.full(length, ADX_WARMUP_DEFAULT, dtype=float)
        warmup[0] = 0.0  # preserve semantic: first bar has no prior movement
        return warmup

    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)

    # True Range
    tr = np.maximum(
        high - low,
        np.maximum(
            np.abs(high - np.roll(close, 1)),
            np.abs(low - np.roll(close, 1))
        )
    )
    tr[0] = high[0] - low[0]

    # Directional Movement
    up_move = high - np.roll(high, 1)
    down_move = np.roll(low, 1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm[0] = 0.0
    minus_dm[0] = 0.0

    # Wilder smoothing
    def wilder_smooth(arr: np.ndarray, period: int) -> np.ndarray:
        result = np.zeros_like(arr)
        result[period - 1] = np.sum(arr[:period])
        for i in range(period, len(arr)):
            result[i] = result[i - 1] - (result[i - 1] / period) + arr[i]
        return result

    tr_smooth = wilder_smooth(tr, period)
    plus_dm_smooth = wilder_smooth(plus_dm, period)
    minus_dm_smooth = wilder_smooth(minus_dm, period)

    plus_di = 100 * (plus_dm_smooth / np.where(tr_smooth == 0, 1.0, tr_smooth))
    minus_di = 100 * (minus_dm_smooth / np.where(tr_smooth == 0, 1.0, tr_smooth))

    dx = 100 * (
        np.abs(plus_di - minus_di) /
        np.where((plus_di + minus_di) == 0, 1.0, (plus_di + minus_di))
    )

    # Final ADX smoothing
    adx = np.zeros_like(dx)
    adx[2 * period - 1] = np.mean(dx[period - 1: 2 * period - 1])

    for i in range(2 * period, length):
        adx[i] = ((adx[i - 1] * (period - 1)) + dx[i]) / period

    # Fill earlier values with neutral instead of zeros
    adx[: 2 * period - 1] = ADX_WARMUP_DEFAULT
    adx[0] = 0.0

    return adx


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
    chop_filter_cvd_range_breakout: bool = False,  # default OFF — low-ADX consolidation IS the setup
) -> bool:
    """
    Strategy-aware chop regime detection.

    Accepts pre-computed arrays so callers avoid redundant recalculation
    on every bar. Call this once per bar using cached indicator arrays.

    - range_breakout / open_drive  : NEVER filtered — chop is their setup.
    - cvd_range_breakout           : exempt by default (False). Low-ADX consolidation
                                     is the precondition for CVD breakout signals —
                                     filtering on chop would eat the best setups.
                                     Set chop_filter_cvd_range_breakout=True only if
                                     you want to require a trending market first.
    - atr_reversal / ema_cross / atr_divergence : filtered per toggle flags.
    """
    if strategy_type in {"range_breakout", "open_drive"}:
        return False

    if strategy_type == "cvd_range_breakout":
        if not chop_filter_cvd_range_breakout:
            return False
        # Falls through to ADX/slope check when user explicitly opts in

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
