import logging
from collections import deque

import numpy as np
import pandas as pd

from core.auto_trader.constants import TRADING_START, TRADING_END, MINUTES_PER_SESSION

logger = logging.getLogger(__name__)


def _rolling_confirmation_vectorized(mask: np.ndarray, window: int) -> np.ndarray:
    """Return True when at least one True exists in trailing window ending at each bar."""
    if window <= 0:
        return mask.copy()
    if len(mask) == 0:
        return mask.copy()

    int_mask = mask.astype(np.int32)
    cumsum = np.cumsum(int_mask)

    shifted = np.empty_like(cumsum)
    shifted[:window] = 0
    shifted[window:] = cumsum[:-window]

    return (cumsum - shifted) > 0


# =============================================================================
# STRATEGY IMPLEMENTATION - KEY CHANGES START HERE
# =============================================================================

class StrategySignalDetector:
    """
    Encapsulates all three trading strategies with clear naming:

    1. ATR REVERSAL STRATEGY (atr_reversal)
       - Price ATR reversal signal
       - CVD must be on same side of both EMA10 and EMA51
       - Wait 5 minutes for CVD to cross its EMA10 in favor

    2. EMA & CVD CROSS STRATEGY (ema_cvd_cross)
       - Price already above/below both EMA10 and EMA51
       - CVD already above/below its EMA10
       - CVD crosses above/below its EMA51

    3. ATR & CVD STRATEGY (atr_cvd_divergence)
       - ATR reversal in price only
       - CVD already above (for green/long) or below (for red/short) both EMA10 and EMA51
       - CVD continues its trend (no reversal expected)
    """

    CONFIRMATION_WAIT_MINUTES = 5
    ATR_EXTENSION_THRESHOLD = 1.10
    ATR_FLAT_VELOCITY_PCT = 0.02
    BREAKOUT_SWITCH_KEEP = "keep_breakout"
    BREAKOUT_SWITCH_PREFER_ATR = "prefer_atr_reversal"
    BREAKOUT_SWITCH_ADAPTIVE = "adaptive"

    def __init__(self, timeframe_minutes: int = 1):
        self.timeframe_minutes = timeframe_minutes
        self.atr_reversal_timestamps = {}  # Store ATR reversal times for confirmation tracking

        # Range breakout tracking
        self.active_breakout_long = False  # Track if we're in a long breakout trade
        self.active_breakout_short = False  # Track if we're in a short breakout trade
        self.breakout_entry_idx = -1  # Track when breakout started
        self.range_high = 0.0  # Store range boundaries for stop loss
        self.range_low = 0.0

        # EMA+CVD cross tracking (for ATR suppression)
        self.active_ema_cross_long = False
        self.active_ema_cross_short = False
        self.ema_cross_entry_idx = -1

        # Incremental cache for heavy breakout computations
        self._range_breakout_cache: dict[str, object] = {}

        # Avoid re-logging identical Open Drive decisions on every detector refresh.
        self._open_drive_logged_sessions: set[tuple] = set()

    def _log_open_drive_once(self, key: tuple, message: str, *args) -> None:
        """Log Open Drive decisions only once per unique session outcome."""
        if key in self._open_drive_logged_sessions:
            return
        self._open_drive_logged_sessions.add(key)
        logger.info(message, *args)

    def detect_atr_reversal_strategy(
            self,
            price_atr_above: np.ndarray,
            price_atr_below: np.ndarray,
            cvd_atr_above: np.ndarray,
            cvd_atr_below: np.ndarray,
            atr_values: np.ndarray | None = None,
            timestamps: list | None = None,
            active_breakout_long: np.ndarray | None = None,
            active_breakout_short: np.ndarray | None = None,
            breakout_long_momentum_strong: np.ndarray | None = None,
            breakout_short_momentum_strong: np.ndarray | None = None,
            breakout_switch_mode: str = BREAKOUT_SWITCH_ADAPTIVE,
            price_close: np.ndarray | None = None,
            price_open: np.ndarray | None = None,
            price_ema51: np.ndarray | None = None,
            price_vwap: np.ndarray | None = None,
            cvd_data: np.ndarray | None = None,
            vwap_min_distance_atr_mult: float = 0.3,
            divergence_lookback: int = 5,
            exhaustion_min_score: int = 2,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Institutional ATR reversal strategy with exhaustion scoring and confirmation candles.
        """

        signal_length = min(
            len(price_atr_above),
            len(price_atr_below),
            len(cvd_atr_above),
            len(cvd_atr_below),
        )
        short_atr_reversal = np.zeros(signal_length, dtype=bool)
        long_atr_reversal = np.zeros(signal_length, dtype=bool)

        if signal_length == 0:
            return short_atr_reversal, long_atr_reversal, short_atr_reversal.copy(), long_atr_reversal.copy()

        price_overextended_short = price_atr_above[:signal_length].copy()
        price_overextended_long = price_atr_below[:signal_length].copy()

        vwap_gate_short = np.ones(signal_length, dtype=bool)
        vwap_gate_long = np.ones(signal_length, dtype=bool)

        if (
            price_vwap is not None and
            price_close is not None and
            atr_values is not None and
            len(price_vwap) >= signal_length and
            len(price_close) >= signal_length and
            len(atr_values) >= signal_length
        ):
            atr_s = np.asarray(atr_values[:signal_length], dtype=float)
            close_s = np.asarray(price_close[:signal_length], dtype=float)
            vwap_s = np.asarray(price_vwap[:signal_length], dtype=float)
            min_vwap_gap = atr_s * vwap_min_distance_atr_mult
            vwap_gate_short = (close_s - vwap_s) > min_vwap_gap
            vwap_gate_long = (vwap_s - close_s) > min_vwap_gap

        base_short = price_overextended_short & vwap_gate_short
        base_long = price_overextended_long & vwap_gate_long

        exhaustion_score_short = np.zeros(signal_length, dtype=int)
        exhaustion_score_long = np.zeros(signal_length, dtype=int)

        if cvd_data is not None and price_close is not None:
            div_short, div_long = self._cvd_price_divergence_masks(
                price_close=np.asarray(price_close[:signal_length], dtype=float),
                cvd_data=np.asarray(cvd_data[:signal_length], dtype=float),
                lookback=divergence_lookback,
            )
            exhaustion_score_short += div_short.astype(int)
            exhaustion_score_long += div_long.astype(int)

        if cvd_data is not None:
            decel_short, decel_long = self._cvd_deceleration_mask(
                cvd_data=np.asarray(cvd_data[:signal_length], dtype=float),
                lookback=max(3, int(round(3 / max(float(self.timeframe_minutes), 1.0)))),
            )
            exhaustion_score_short += decel_short.astype(int)
            exhaustion_score_long += decel_long.astype(int)

        if atr_values is not None and len(atr_values) >= signal_length:
            atr_slice = np.asarray(atr_values[:signal_length], dtype=float)
            atr_velocity = np.diff(atr_slice, prepend=atr_slice[0])
            prev_atr = np.roll(atr_slice, 1)
            prev_atr[0] = atr_slice[0]
            atr_velocity_pct = np.divide(
                atr_velocity,
                np.where(np.abs(prev_atr) > 1e-9, np.abs(prev_atr), 1.0),
            )
            atr_contracting = (atr_velocity <= 0.0) | (atr_velocity_pct <= self.ATR_FLAT_VELOCITY_PCT)

            if timestamps is not None and len(timestamps) >= signal_length:
                session_index = pd.to_datetime(pd.Series(timestamps[:signal_length])).dt.date
                atr_session_mean = pd.Series(atr_slice).groupby(session_index).transform("mean")
                rolling_30_session_avg = atr_session_mean.groupby(session_index).first().rolling(30, min_periods=1).mean()
                baseline_map = rolling_30_session_avg.to_dict()
                baseline = np.array([baseline_map.get(d, np.nan) for d in session_index], dtype=float)
            else:
                baseline = pd.Series(atr_slice).rolling(30, min_periods=1).mean().to_numpy()

            normalized_atr = np.divide(
                atr_slice,
                np.where(np.abs(baseline) > 1e-9, baseline, np.nan),
            )
            atr_extended_and_contracting = (
                (np.nan_to_num(normalized_atr, nan=0.0) > self.ATR_EXTENSION_THRESHOLD)
                & atr_contracting
            )
            exhaustion_score_short += atr_extended_and_contracting.astype(int)
            exhaustion_score_long += atr_extended_and_contracting.astype(int)

        exhaustion_gate_short = exhaustion_score_short >= exhaustion_min_score
        exhaustion_gate_long = exhaustion_score_long >= exhaustion_min_score

        pre_confirmation_short = base_short & exhaustion_gate_short
        pre_confirmation_long = base_long & exhaustion_gate_long

        confirmation_window = max(1, int(round(5 / max(float(self.timeframe_minutes), 1.0))))

        if price_close is not None and price_open is not None:
            close_s = np.asarray(price_close[:signal_length], dtype=float)
            open_s = np.asarray(price_open[:signal_length], dtype=float)
            prev_low = np.concatenate(([close_s[0]], close_s[:-1]))
            prev_high = np.concatenate(([close_s[0]], close_s[:-1]))

            bearish_confirm = (close_s < open_s) & (close_s < prev_low)
            bullish_confirm = (close_s > open_s) & (close_s > prev_high)

            pre_conf_short_window = _rolling_confirmation_vectorized(pre_confirmation_short, confirmation_window)
            pre_conf_long_window = _rolling_confirmation_vectorized(pre_confirmation_long, confirmation_window)

            short_atr_reversal = pre_conf_short_window & bearish_confirm
            long_atr_reversal = pre_conf_long_window & bullish_confirm
        else:
            short_atr_reversal = pre_confirmation_short
            long_atr_reversal = pre_confirmation_long

        # Keep raw copies BEFORE any suppression — callers use these to count
        # how many ATR signals were skipped during an active breakout trade.
        short_atr_reversal_raw = short_atr_reversal.copy()
        long_atr_reversal_raw = long_atr_reversal.copy()

        if (
                active_breakout_long is not None and
                active_breakout_short is not None and
                len(active_breakout_long) == signal_length and
                len(active_breakout_short) == signal_length
        ):
            long_context = active_breakout_long.astype(bool)
            short_context = active_breakout_short.astype(bool)

            if breakout_switch_mode == self.BREAKOUT_SWITCH_KEEP:
                suppress_short_mask = long_context
                suppress_long_mask = short_context
            elif breakout_switch_mode == self.BREAKOUT_SWITCH_PREFER_ATR:
                suppress_short_mask = np.zeros_like(short_atr_reversal)
                suppress_long_mask = np.zeros_like(long_atr_reversal)
            else:
                long_momentum = (
                    breakout_long_momentum_strong.astype(bool)
                    if breakout_long_momentum_strong is not None and
                       len(breakout_long_momentum_strong) == len(short_atr_reversal)
                    else long_context
                )
                short_momentum = (
                    breakout_short_momentum_strong.astype(bool)
                    if breakout_short_momentum_strong is not None and
                       len(breakout_short_momentum_strong) == len(short_atr_reversal)
                    else short_context
                )
                suppress_short_mask = long_context & long_momentum
                suppress_long_mask = short_context & short_momentum

            short_atr_reversal = short_atr_reversal & (~suppress_short_mask)
            long_atr_reversal = long_atr_reversal & (~suppress_long_mask)
        else:
            # Backward-compatible behavior for any caller that still uses stateful suppression.
            suppress_short, suppress_long = self.should_suppress_atr_reversal()
            if suppress_short:
                short_atr_reversal = np.zeros_like(short_atr_reversal)
            if suppress_long:
                long_atr_reversal = np.zeros_like(long_atr_reversal)

        return short_atr_reversal, long_atr_reversal, short_atr_reversal_raw, long_atr_reversal_raw

    def _cvd_price_divergence_masks(
            self,
            price_close: np.ndarray,
            cvd_data: np.ndarray,
            lookback: int = 5,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Detect CVD-price divergence around local extremes."""
        n = len(price_close)
        bearish_div = np.zeros(n, dtype=bool)
        bullish_div = np.zeros(n, dtype=bool)

        for i in range(lookback, n):
            start = i - lookback
            price_window = price_close[start:i + 1]
            cvd_window = cvd_data[start:i + 1]

            price_high_of_window = np.max(price_window)
            price_low_of_window = np.min(price_window)
            cvd_high_of_window = np.max(cvd_window)
            cvd_low_of_window = np.min(cvd_window)

            price_at_high = price_close[i] >= price_high_of_window * 0.9995
            cvd_high_epsilon = abs(cvd_high_of_window) * 0.0005
            cvd_not_at_high = cvd_data[i] < (cvd_high_of_window - cvd_high_epsilon)
            bearish_div[i] = price_at_high and cvd_not_at_high

            price_at_low = price_close[i] <= price_low_of_window * 1.0005
            cvd_low_epsilon = abs(cvd_low_of_window) * 0.0005
            cvd_not_at_low = cvd_data[i] > (cvd_low_of_window + cvd_low_epsilon)
            bullish_div[i] = price_at_low and cvd_not_at_low

        return bearish_div, bullish_div

    def _cvd_deceleration_mask(
            self,
            cvd_data: np.ndarray,
            lookback: int = 3,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Detect deceleration in CVD momentum."""
        n = len(cvd_data)
        decel_short = np.zeros(n, dtype=bool)
        decel_long = np.zeros(n, dtype=bool)

        min_bars = lookback * 2 + 1
        if n < min_bars:
            return decel_short, decel_long

        slope = np.zeros(n, dtype=float)
        slope[lookback:] = cvd_data[lookback:] - cvd_data[:-lookback]

        prev_slope = np.zeros(n, dtype=float)
        prev_slope[lookback * 2:] = slope[lookback:-lookback] if lookback > 0 else slope[:-lookback]

        cvd_was_rising = prev_slope > 0
        slope_shrinking_up = (slope > 0) & (slope < prev_slope)
        decel_short = cvd_was_rising & slope_shrinking_up

        cvd_was_falling = prev_slope < 0
        slope_shrinking_down = (slope < 0) & (slope > prev_slope)
        decel_long = cvd_was_falling & slope_shrinking_down

        return decel_short, decel_long

    def build_breakout_context_masks(
            self,
            long_breakout: np.ndarray,
            short_breakout: np.ndarray,
            hold_bars: int = 6,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Mark bars that still belong to a recently triggered breakout regime."""
        length = min(len(long_breakout), len(short_breakout))
        long_context = np.zeros(length, dtype=bool)
        short_context = np.zeros(length, dtype=bool)

        if length == 0:
            return long_context, short_context

        hold = max(1, int(hold_bars))
        long_left = 0
        short_left = 0

        for idx in range(length):
            if long_breakout[idx]:
                long_left = hold
                short_left = 0
            elif short_breakout[idx]:
                short_left = hold
                long_left = 0

            if long_left > 0:
                long_context[idx] = True
                long_left -= 1

            if short_left > 0:
                short_context[idx] = True
                short_left -= 1

        return long_context, short_context

    def evaluate_breakout_momentum_strength(
            self,
            price_close: np.ndarray,
            price_ema10: np.ndarray,
            cvd_data: np.ndarray,
            cvd_ema10: np.ndarray,
            volume: np.ndarray,
            long_context: np.ndarray,
            short_context: np.ndarray,
            slope_lookback_bars: int = 3,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Estimate whether breakout continuation momentum is still strong.
        """
        length = min(
            len(price_close), len(price_ema10), len(cvd_data), len(cvd_ema10),
            len(volume), len(long_context), len(short_context)
        )
        long_strong = np.zeros(length, dtype=bool)
        short_strong = np.zeros(length, dtype=bool)

        if length < 2:
            return long_strong, short_strong

        eps = 1e-9
        bars_back = max(1, int(slope_lookback_bars))

        price_arr = np.asarray(price_close[:length], dtype=float)
        ema10_arr = np.asarray(price_ema10[:length], dtype=float)
        cvd_arr = np.asarray(cvd_data[:length], dtype=float)
        cvd_ema_arr = np.asarray(cvd_ema10[:length], dtype=float)
        vol_arr = np.asarray(volume[:length], dtype=float)

        shifted_price = np.empty(length, dtype=float)
        shifted_cvd = np.empty(length, dtype=float)
        shifted_price[:bars_back] = price_arr[:bars_back]
        shifted_cvd[:bars_back] = cvd_arr[:bars_back]
        shifted_price[bars_back:] = price_arr[:-bars_back]
        shifted_cvd[bars_back:] = cvd_arr[:-bars_back]

        price_delta = price_arr - shifted_price
        cvd_delta = cvd_arr - shifted_cvd

        price_trend_score = np.abs(price_arr - ema10_arr) / np.maximum(np.abs(ema10_arr), eps)
        cvd_trend_score = np.abs(cvd_arr - cvd_ema_arr) / np.maximum(np.abs(cvd_ema_arr), 1.0)
        vol_avg = pd.Series(vol_arr).rolling(10, min_periods=1).mean().to_numpy()
        vol_score = vol_arr / np.maximum(vol_avg, eps)

        composite = (price_trend_score * 0.45) + (cvd_trend_score * 0.35) + (vol_score * 0.20)

        bullish_alignment = (price_delta > 0) & (cvd_delta > 0) & (price_arr > ema10_arr)
        bearish_alignment = (price_delta < 0) & (cvd_delta < 0) & (price_arr < ema10_arr)

        long_context_arr = np.asarray(long_context[:length], dtype=bool)
        short_context_arr = np.asarray(short_context[:length], dtype=bool)

        long_strong = long_context_arr & bullish_alignment & (composite >= 1.05)
        short_strong = short_context_arr & bearish_alignment & (composite >= 1.05)

        return long_strong, short_strong

    def detect_ema_cvd_cross_strategy(
            self,
            timestamps: list,
            price_data: np.ndarray,
            price_ema10: np.ndarray,
            price_ema51: np.ndarray,
            cvd_data: np.ndarray,
            cvd_ema10: np.ndarray,
            cvd_ema51: np.ndarray,
            cvd_ema_gap_threshold: float,
            use_parent_mask: bool = True,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        EMA & CVD CROSS STRATEGY:
        - Price already above/below both EMA10 and EMA51
        - CVD already above/below its EMA10
        - CVD crosses above/below its EMA51 → SIGNAL
        - Parent timeframe filter: 5m EMA10 must be above/below EMA51 and
          both slopes must confirm trend direction.
        """

        length = min(
            len(timestamps), len(price_data), len(price_ema10), len(price_ema51),
            len(cvd_data), len(cvd_ema10), len(cvd_ema51),
        )
        if length <= 0:
            return np.array([], dtype=bool), np.array([], dtype=bool)

        if use_parent_mask:
            parent_long_mask, parent_short_mask = self._build_parent_5m_trend_masks(
                timestamps=timestamps[:length],
                price_data=price_data[:length],
            )
        else:
            parent_long_mask = np.ones(length, dtype=bool)
            parent_short_mask = np.ones(length, dtype=bool)

        # Price position checks
        price_above_both_emas = (price_data > price_ema10) & (price_data > price_ema51)
        price_below_both_emas = (price_data < price_ema10) & (price_data < price_ema51)

        # CVD position checks
        cvd_above_ema10 = cvd_data > cvd_ema10
        cvd_below_ema10 = cvd_data < cvd_ema10

        # Detect CVD crosses of EMA10/EMA51
        cvd_prev = np.concatenate(([cvd_data[0]], cvd_data[:-1]))
        cvd_ema10_prev = np.concatenate(([cvd_ema10[0]], cvd_ema10[:-1]))
        cvd_ema51_prev = np.concatenate(([cvd_ema51[0]], cvd_ema51[:-1]))

        cvd_cross_above_ema10_raw = (cvd_prev <= cvd_ema10_prev) & (cvd_data > cvd_ema10)
        cvd_cross_below_ema10_raw = (cvd_prev >= cvd_ema10_prev) & (cvd_data < cvd_ema10)
        cvd_cross_above_ema51_raw = (cvd_prev <= cvd_ema51_prev) & (cvd_data > cvd_ema51)
        cvd_cross_below_ema51_raw = (cvd_prev >= cvd_ema51_prev) & (cvd_data < cvd_ema51)

        # Enforce ordered cross sequence for EMA cross strategy only:
        # long  -> CVD must cross EMA10 first, then EMA51 (not on same bar)
        # short -> CVD must cross EMA10 first, then EMA51 (not on same bar)
        cvd_was_above_ema10 = cvd_prev > cvd_ema10_prev
        cvd_was_below_ema10 = cvd_prev < cvd_ema10_prev

        cvd_cross_above_ema51_ordered = (
            cvd_cross_above_ema51_raw &
            cvd_was_above_ema10 &
            ~cvd_cross_above_ema10_raw
        )
        cvd_cross_below_ema51_ordered = (
            cvd_cross_below_ema51_raw &
            cvd_was_below_ema10 &
            ~cvd_cross_below_ema10_raw
        )

        # Anti-hug filter - CVD must be meaningfully away from EMA51
        gap = np.abs(cvd_data - cvd_ema51)
        min_gap = cvd_ema_gap_threshold * 0.5
        cvd_cross_above_ema51 = cvd_cross_above_ema51_ordered & (gap > min_gap)
        cvd_cross_below_ema51 = cvd_cross_below_ema51_ordered & (gap > min_gap)

        # Slope confirmation - both price and CVD trending in same direction
        price_up_slope, price_down_slope = self._calculate_slope_masks(price_data)
        cvd_up_slope, cvd_down_slope = self._calculate_slope_masks(cvd_data)

        # LONG signals: Everything bullish
        long_ema_cross = (
                price_above_both_emas &
                cvd_above_ema10 &
                cvd_cross_above_ema51 &
                price_up_slope &
                cvd_up_slope &
                parent_long_mask
        )

        # SHORT signals: Everything bearish
        short_ema_cross = (
                price_below_both_emas &
                cvd_below_ema10 &
                cvd_cross_below_ema51 &
                price_down_slope &
                cvd_down_slope &
                parent_short_mask
        )

        return short_ema_cross, long_ema_cross

    def _build_parent_5m_trend_masks(
            self,
            timestamps: list,
            price_data: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build 5m parent-trend masks and map them back to base bars."""
        length = min(len(timestamps), len(price_data))
        if length == 0:
            return np.array([], dtype=bool), np.array([], dtype=bool)

        ts_index = pd.to_datetime(pd.Index(timestamps[:length]))
        base_frame = pd.DataFrame(
            {
                "timestamp": ts_index,
                "price": np.asarray(price_data[:length], dtype=float),
            }
        ).dropna(subset=["timestamp", "price"])

        if base_frame.empty:
            neutral = np.ones(length, dtype=bool)
            return neutral.copy(), neutral

        base_frame = base_frame.set_index("timestamp").sort_index()
        parent_close = base_frame["price"].resample("5min").last().dropna()
        if parent_close.empty:
            neutral = np.ones(length, dtype=bool)
            return neutral.copy(), neutral

        parent_ema10 = parent_close.ewm(span=10, adjust=False).mean()
        parent_ema51 = parent_close.ewm(span=51, adjust=False).mean()

        ema10_up = parent_ema10 > parent_ema10.shift(1)
        ema51_up = parent_ema51 > parent_ema51.shift(1)
        ema10_down = parent_ema10 < parent_ema10.shift(1)
        ema51_down = parent_ema51 < parent_ema51.shift(1)

        parent_long = (parent_ema10 > parent_ema51) & ema10_up & ema51_up
        parent_short = (parent_ema10 < parent_ema51) & ema10_down & ema51_down

        parent_signal = pd.DataFrame(
            {
                "parent_long": parent_long.fillna(False),
                "parent_short": parent_short.fillna(False),
            },
            index=parent_close.index,
        )

        expanded = parent_signal.reindex(base_frame.index, method="ffill").fillna(False)
        long_lookup = expanded["parent_long"].to_dict()
        short_lookup = expanded["parent_short"].to_dict()

        base_timestamps = pd.to_datetime(pd.Index(timestamps[:length]))
        parent_long_mask = np.array([bool(long_lookup.get(ts, False)) for ts in base_timestamps], dtype=bool)
        parent_short_mask = np.array([bool(short_lookup.get(ts, False)) for ts in base_timestamps], dtype=bool)
        return parent_long_mask, parent_short_mask

    def detect_open_drive_strategy(
            self,
            timestamps: list,
            price_data: np.ndarray,
            price_ema10: np.ndarray,
            price_ema51: np.ndarray,
            price_vwap: np.ndarray,
            cvd_data: np.ndarray,
            cvd_ema10: np.ndarray,
            trigger_hour: int,
            trigger_minute: int,
            enabled: bool = True,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Open Drive Model:
        Trigger only at configured time (default 09:17), once per session date.

        LONG:
            price > EMA10 and price > EMA51
            CVD > CVD EMA10

        SHORT:
            price < EMA10 and price < EMA51
            CVD < CVD EMA10

        FIX 1: Removed `with suppress(Exception)` around the time-check `continue`.
               A `continue` inside a `with` block only exits the context manager,
               NOT the enclosing for-loop — so bars were never actually skipped.

        FIX 2: Changed from `candle_minutes >= trigger_minutes` (fires on every bar
               after trigger time) to a tight window of [trigger, trigger + 1 candle).
               This guarantees the check runs exactly at the user-configured time.

        FIX 3: Mark the session as fired even when conditions don't align at the
               trigger bar. Prevents the strategy from drifting to later bars in
               the same session when 9:17 conditions fail.
        """
        length = min(
            len(timestamps), len(price_data), len(price_ema10), len(price_ema51),
            len(price_vwap), len(cvd_data), len(cvd_ema10),
        )
        long_open_drive = np.zeros(length, dtype=bool)
        short_open_drive = np.zeros(length, dtype=bool)

        if not enabled or length == 0:
            return short_open_drive, long_open_drive

        trigger_minutes = int(trigger_hour) * 60 + int(trigger_minute)
        # Trigger window = exactly 1 candle wide at the configured time.
        # For 1m: fires at 9:17 only. For 5m: fires at the 9:15 or 9:20 candle
        # that contains 9:17. This keeps it working across timeframes.
        window_bars = max(1, int(round(self.timeframe_minutes)))

        fired_dates: set = set()
        session_dates_seen: set = set()
        session_dates_evaluated: set = set()

        for idx in range(length):
            ts = timestamps[idx]

            # ── FIX 1: bare try/except so `continue` propagates to the for-loop ──
            try:
                session_date = ts.date()
                candle_minutes = int(ts.hour) * 60 + int(ts.minute)
            except Exception:
                continue

            session_dates_seen.add(session_date)

            if session_date in fired_dates:
                continue

            # ── FIX 2: exact-time window, not "at or after" ──
            if not (trigger_minutes <= candle_minutes < trigger_minutes + window_bars):
                continue

            session_dates_evaluated.add(session_date)

            price = float(price_data[idx])
            ema10 = float(price_ema10[idx])
            ema51 = float(price_ema51[idx])
            vwap = float(price_vwap[idx])
            cvd = float(cvd_data[idx])
            cvd_fast = float(cvd_ema10[idx])

            if not np.isfinite([price, ema10, ema51, vwap, cvd, cvd_fast]).all():
                # ── FIX 3: mark fired even on NaN so we don't drift to later bars ──
                fired_dates.add(session_date)
                self._log_open_drive_once(
                    (session_date, int(trigger_hour), int(trigger_minute), "invalid_values"),
                    "Open Drive @ %s %02d:%02d -> NO TRADE (invalid values). "
                    "price=%s ema10=%s ema51=%s vwap=%s cvd=%s cvd_ema10=%s",
                    session_date,
                    int(trigger_hour),
                    int(trigger_minute),
                    price,
                    ema10,
                    ema51,
                    vwap,
                    cvd,
                    cvd_fast,
                )
                continue

            price_above_both = (price > ema10) and (price > ema51)
            price_below_both = (price < ema10) and (price < ema51)
            cvd_bullish = cvd > cvd_fast
            cvd_bearish = cvd < cvd_fast

            # Open Drive should reflect clear directional alignment at trigger time.
            # We only require price to be above/below BOTH EMAs (not EMA10 > EMA51 ordering),
            # which avoids missing otherwise clear setups early in the session.
            long_cond = price_above_both and cvd_bullish
            short_cond = price_below_both and cvd_bearish

            # ── FIX 3: always mark session fired after checking trigger bar ──
            fired_dates.add(session_date)

            if long_cond:
                long_open_drive[idx] = True
                self._log_open_drive_once(
                    (session_date, int(trigger_hour), int(trigger_minute), "long"),
                    "Open Drive @ %s %02d:%02d -> LONG | price=%.2f ema10=%.2f ema51=%.2f vwap=%.2f cvd=%.2f cvd_ema10=%.2f",
                    session_date,
                    int(trigger_hour),
                    int(trigger_minute),
                    price,
                    ema10,
                    ema51,
                    vwap,
                    cvd,
                    cvd_fast,
                )
            elif short_cond:
                short_open_drive[idx] = True
                self._log_open_drive_once(
                    (session_date, int(trigger_hour), int(trigger_minute), "short"),
                    "Open Drive @ %s %02d:%02d -> SHORT | price=%.2f ema10=%.2f ema51=%.2f vwap=%.2f cvd=%.2f cvd_ema10=%.2f",
                    session_date,
                    int(trigger_hour),
                    int(trigger_minute),
                    price,
                    ema10,
                    ema51,
                    vwap,
                    cvd,
                    cvd_fast,
                )
            # else: conditions didn't align at trigger time — session still marked,
            # no signal fires. Never chase later bars.
            else:
                self._log_open_drive_once(
                    (session_date, int(trigger_hour), int(trigger_minute), "no_trade"),
                    "Open Drive @ %s %02d:%02d -> NO TRADE | "
                    "price=%.2f ema10=%.2f ema51=%.2f vwap=%.2f cvd=%.2f cvd_ema10=%.2f "
                    "flags(price>both=%s price<both=%s cvd>ema10=%s cvd<ema10=%s)",
                    session_date,
                    int(trigger_hour),
                    int(trigger_minute),
                    price,
                    ema10,
                    ema51,
                    vwap,
                    cvd,
                    cvd_fast,
                    price_above_both,
                    price_below_both,
                    cvd_bullish,
                    cvd_bearish,
                )

        missing_trigger_dates = sorted(session_dates_seen - session_dates_evaluated)
        for missing_date in missing_trigger_dates:
            self._log_open_drive_once(
                (missing_date, int(trigger_hour), int(trigger_minute), "missing_trigger_window"),
                "Open Drive @ %s %02d:%02d -> NO TRADE (no candle in trigger window; timeframe=%sm)",
                missing_date,
                int(trigger_hour),
                int(trigger_minute),
                int(window_bars),
            )

        return short_open_drive, long_open_drive
    def detect_atr_cvd_divergence_strategy(
            self,
            price_atr_above: np.ndarray,  # Price ATR reversal - above EMA (potential SHORT)
            price_atr_below: np.ndarray,  # Price ATR reversal - below EMA (potential LONG)
            cvd_above_ema10: np.ndarray,  # CVD above its EMA10
            cvd_below_ema10: np.ndarray,  # CVD below its EMA10
            cvd_above_ema51: np.ndarray,  # CVD above its EMA51
            cvd_below_ema51: np.ndarray,  # CVD below its EMA51
            cvd_data: np.ndarray,
            ema_cross_short: np.ndarray,  # Exclude EMA cross signals
            ema_cross_long: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        ATR & CVD STRATEGY (Divergence):
        - SHORT: Price ATR reversal from above + CVD below both EMAs (continuing down)
        - LONG: Price ATR reversal from below + CVD above both EMAs (continuing up)
        - CVD trend continuation expected (no reversal)
        """

        # CVD slope for trend continuation
        cvd_up_slope, cvd_down_slope = self._calculate_slope_masks(cvd_data)

        # SHORT: Price reversal, CVD continues bearish trend
        short_divergence = (
                price_atr_above &
                cvd_below_ema10 &
                cvd_below_ema51 &
                cvd_down_slope &  # CVD trending down
                (~ema_cross_short)  # Not an EMA cross signal
        )

        # LONG: Price reversal, CVD continues bullish trend
        long_divergence = (
                price_atr_below &
                cvd_above_ema10 &
                cvd_above_ema51 &
                cvd_up_slope &  # CVD trending up
                (~ema_cross_long)  # Not an EMA cross signal
        )

        return short_divergence, long_divergence

    def _calculate_slope_masks(self, series: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Build per-bar slope direction masks.

        Uses timeframe-relative lookbacks (3x and 6x the current timeframe)
        while enforcing a minimum of 3 bars per lookback to avoid 1-bar
        noise on higher timeframes.

        A direction qualifies when either lookback indicates that direction.
        """
        length = len(series)
        up_mask = np.zeros(length, dtype=bool)
        down_mask = np.zeros(length, dtype=bool)

        if length < 2:
            return up_mask, down_mask

        tf_minutes = max(float(self.timeframe_minutes), 1.0)
        for multiplier in (3, 6):
            lookback_minutes = multiplier * tf_minutes
            bars_back = max(3, int(round(lookback_minutes / tf_minutes)))
            if bars_back >= length:
                continue

            delta = np.zeros(length, dtype=float)
            delta[bars_back:] = series[bars_back:] - series[:-bars_back]
            up_mask |= delta > 0
            down_mask |= delta < 0

        return up_mask, down_mask

    def detect_range_breakout_strategy(
            self,
            price_high: np.ndarray,
            price_low: np.ndarray,
            price_close: np.ndarray,
            price_ema10: np.ndarray,
            cvd_data: np.ndarray,
            cvd_ema10: np.ndarray,
            volume: np.ndarray,
            range_lookback_minutes: int = 30,
            breakout_threshold_multiplier: float = 1.5,
            min_consolidation_minutes: int = 0,
            min_consolidation_adx: float = 0.0,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        RANGE BREAKOUT STRATEGY:

        Detects consolidation and breakout patterns:
        1. Calculate rolling range over lookback period
        2. Detect when price breaks beyond range boundaries
        3. Confirm with CVD alignment and volume
        4. Track active breakout for exit management

        Args:
            min_consolidation_minutes: If > 0, require the market to have been
                consolidating (range-bound) for at least this many minutes before
                the breakout bar. Enforces that a real squeeze preceded the move.
            min_consolidation_adx: If > 0, require ADX to have been below this
                threshold for at least min_consolidation_minutes bars before the
                breakout. Set alongside min_consolidation_minutes for best results.

        Returns:
            (long_signals, short_signals, range_highs, range_lows)
            - Signals are True where breakout occurs
            - Range boundaries for stop loss placement
        """
        length = len(price_close)
        long_breakout = np.zeros(length, dtype=bool)
        short_breakout = np.zeros(length, dtype=bool)
        range_highs = np.zeros(length, dtype=float)
        range_lows = np.zeros(length, dtype=float)

        if length < 2:
            return long_breakout, short_breakout, range_highs, range_lows

        # Convert lookback minutes to bars
        lookback_bars = max(2, int(round(range_lookback_minutes / max(self.timeframe_minutes, 1))))

        # Convert consolidation requirement to bars
        min_consol_bars = max(0, int(round(min_consolidation_minutes / max(self.timeframe_minutes, 1))))

        cache_key = (lookback_bars, min_consol_bars, float(min_consolidation_adx), float(breakout_threshold_multiplier))
        cache = self._range_breakout_cache if self._range_breakout_cache.get("key") == cache_key else {}
        can_incremental = bool(cache) and cache.get("length", 0) < length and cache.get("length", 0) >= lookback_bars

        if can_incremental:
            cached_len = int(cache["length"])
            long_breakout[:cached_len] = cache["long"][:cached_len]
            short_breakout[:cached_len] = cache["short"][:cached_len]
            range_highs[:cached_len] = cache["highs"][:cached_len]
            range_lows[:cached_len] = cache["lows"][:cached_len]
            calc_start = max(lookback_bars, cached_len - 2)
        else:
            calc_start = lookback_bars

        # Pre-compute ADX only if needed (expensive — avoid if not configured)
        adx_series = None
        if min_consol_bars > 0 and min_consolidation_adx > 0:
            if can_incremental and isinstance(cache.get("adx"), np.ndarray) and len(cache["adx"]) == cached_len:
                adx_series = self._compute_adx_simple(price_high, price_low, price_close, period=14)
            else:
                adx_series = self._compute_adx_simple(price_high, price_low, price_close, period=14)

        # breakout_threshold_multiplier is converted into a minimum breakout
        # extension beyond the range boundary. 1.0 ~= 3% of the prior range.
        base_breakout_strength = max(0.0, 0.03 * float(breakout_threshold_multiplier))

        # Calculate rolling range and average range
        for i in range(calc_start, length):
            start_idx = max(0, i - lookback_bars)
            window_high = np.max(price_high[start_idx:i])
            window_low = np.min(price_low[start_idx:i])
            range_size = window_high - window_low

            # Store range boundaries
            range_highs[i] = window_high
            range_lows[i] = window_low

            # Calculate average range for volatility context
            avg_range = np.mean(price_high[start_idx:i] - price_low[start_idx:i])

            # Dynamic ATR threshold scaling using BB/Keltner squeeze ratio.
            # Tighter squeeze (lower ratio) lowers the breakout strength required.
            squeeze_ratio = self._compute_squeeze_ratio(
                price_high[start_idx:i],
                price_low[start_idx:i],
                price_close[start_idx:i],
            )
            dynamic_breakout_strength = base_breakout_strength * np.clip(squeeze_ratio, 0.55, 1.60)

            # Detect consolidation: range should be relatively tight
            is_consolidating = range_size < (avg_range * 3.0)

            if not is_consolidating:
                continue

            # ── Minimum consolidation period check ────────────────────────────
            # Require market to have been range-bound for min_consol_bars before
            # this breakout bar. Optionally also require ADX was below threshold.
            if min_consol_bars > 0:
                consol_start = max(0, i - min_consol_bars)
                pre_range_size = (
                    np.max(price_high[consol_start:i]) - np.min(price_low[consol_start:i])
                )
                pre_avg_range = np.mean(price_high[consol_start:i] - price_low[consol_start:i])
                was_consolidating = pre_range_size < (pre_avg_range * 3.0)
                if not was_consolidating:
                    continue

                if min_consolidation_adx > 0 and adx_series is not None:
                    adx_window = adx_series[consol_start:i]
                    if len(adx_window) == 0 or not np.all(adx_window < min_consolidation_adx):
                        continue
            # ─────────────────────────────────────────────────────────────────

            # LONG BREAKOUT: Close above range high
            if price_close[i] > window_high:
                # Volume confirmation: above average
                avg_volume = np.mean(volume[start_idx:i])
                volume_confirmed = avg_volume <= 0 or volume[i] >= avg_volume * 1.05

                # CVD confirmation: trending up or above EMA10
                cvd_bullish = (cvd_data[i] > cvd_ema10[i]) or (cvd_data[i] > cvd_data[i - 1])

                # Price momentum: moved at least threshold beyond range
                breakout_strength = (price_close[i] - window_high) / range_size if range_size > 0 else 0
                strong_breakout = breakout_strength >= dynamic_breakout_strength

                if volume_confirmed and cvd_bullish and strong_breakout:
                    long_breakout[i] = True

            # SHORT BREAKOUT: Close below range low
            elif price_close[i] < window_low:
                # Volume confirmation
                avg_volume = np.mean(volume[start_idx:i])
                volume_confirmed = avg_volume <= 0 or volume[i] >= avg_volume * 1.05

                # CVD confirmation: trending down or below EMA10
                cvd_bearish = (cvd_data[i] < cvd_ema10[i]) or (cvd_data[i] < cvd_data[i - 1])

                # Price momentum
                breakout_strength = (window_low - price_close[i]) / range_size if range_size > 0 else 0
                strong_breakout = breakout_strength >= dynamic_breakout_strength

                if volume_confirmed and cvd_bearish and strong_breakout:
                    short_breakout[i] = True

        self._range_breakout_cache = {
            "key": cache_key,
            "length": length,
            "long": long_breakout.copy(),
            "short": short_breakout.copy(),
            "highs": range_highs.copy(),
            "lows": range_lows.copy(),
            "adx": adx_series.copy() if isinstance(adx_series, np.ndarray) else None,
        }
        return long_breakout, short_breakout, range_highs, range_lows

    def detect_cvd_range_breakout_strategy(
            self,
            price_high: np.ndarray,
            price_low: np.ndarray,
            price_close: np.ndarray,
            price_ema10: np.ndarray,
            cvd_data: np.ndarray,
            cvd_ema10: np.ndarray,
            volume: np.ndarray,
            # ── Core params (unchanged) ─────────────────────────────────────
            cvd_range_lookback_bars: int = 30,
            cvd_range_lookback_min_bars: int | None = None,
            cvd_range_lookback_max_bars: int | None = None,
            cvd_breakout_buffer: float = 0.10,
            cvd_min_consol_bars: int = 15,
            cvd_max_range_ratio: float = 0.80,
            min_consolidation_adx: float = 15.0,
            # ── Institutional upgrade params ────────────────────────────────
            min_conviction_score: int = 4,  # Fire if score >= this (out of 5)
            vol_expansion_mult: float = 1.15,  # Volume must be X× its avg
            atr_expansion_pct: float = 0.05,  # ATR must expand X% vs squeeze
            htf_bars: int = 5,  # Higher-TF proxy: N-bar trend window
            regime_adx_block: float = 30.0,  # Block signal if opposing ADX > this
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Institutional CVD Range Breakout — Conviction Scoring Model.

        ═══════════════════════════════════════════════════════════════
        THE CORE CONCEPT: Conviction Scoring (not flat AND gates)
        ═══════════════════════════════════════════════════════════════
        Institutions don't require ALL conditions simultaneously — that
        kills signal frequency. Instead they score each filter 0/1 and
        require a MINIMUM TOTAL SCORE. This lets signals through when
        4/5 filters agree, not just when all 5 are perfect.

        6 CONVICTION FILTERS (each worth 1 point, max score = 6):
        ┌─────┬──────────────────────────────────────────────────────┐
        │  1  │ ADX expansion: ADX rising at breakout bar            │
        │     │ = Trend momentum is actually building (not decaying) │
        ├─────┼──────────────────────────────────────────────────────┤
        │  2  │ Volatility expansion: ATR[i] > ATR of squeeze window │
        │     │ = The squeeze is genuinely releasing energy          │
        ├─────┼──────────────────────────────────────────────────────┤
        │  3  │ Volume expansion: volume[i] > avg_volume × mult      │
        │     │ = Real participation, not a ghost breakout           │
        ├─────┼──────────────────────────────────────────────────────┤
        │  4  │ EMA position bonus: price on the expected side       │
        │     │ = Trend-confirmed entry (bonus, not a blocker)       │
        │     │   Long below EMA still fires — just scores lower     │
        ├─────┼──────────────────────────────────────────────────────┤
        │  5  │ HTF alignment: price trend over last N bars agrees   │
        │     │ = You're trading WITH the higher-timeframe structure │
        ├─────┼──────────────────────────────────────────────────────┤
        │  6  │ Regime check: no strong opposing regime active       │
        │     │ = Don't long into a raging downtrend (or vice versa) │
        └─────┴──────────────────────────────────────────────────────┘

        Default: min_conviction_score=3 → need 3 of 6. Raise to 4-5 for
        tighter signals, lower to 2 for more signals.

        The 3 BASE conditions from the production method (price slope,
        price vs EMA10, CVD EMA slope) remain as HARD GATES — they are
        cheap to compute and filter obvious noise.

        ── EMA POSITION GATE REMOVED ──────────────────────────────────
        price_close > price_ema10 (for longs) and its mirror for shorts
        is deliberately NOT a gate in this strategy. Reason: CVD leads
        price. The highest-conviction breakouts are often ones where CVD
        breaks its range while price is still below EMA (accumulation) or
        above EMA (distribution). Filtering those out discards the alpha.
        Price SLOPE (direction) and CVD EMA slope remain as hard gates —
        they confirm momentum without requiring price to have already moved.
        ────────────────────────────────────────────────────────────────
        """
        length = min(
            len(price_high), len(price_low), len(price_close),
            len(price_ema10), len(cvd_data), len(cvd_ema10), len(volume)
        )
        long_breakout = np.zeros(length, dtype=bool)
        short_breakout = np.zeros(length, dtype=bool)

        if length < 3:
            return short_breakout, long_breakout

        legacy_lookback = max(3, int(cvd_range_lookback_bars))
        lookback_min = max(3, int(cvd_range_lookback_min_bars if cvd_range_lookback_min_bars is not None else legacy_lookback))
        lookback_max = max(lookback_min, int(cvd_range_lookback_max_bars if cvd_range_lookback_max_bars is not None else legacy_lookback))
        min_consol = max(1, int(cvd_min_consol_bars))
        ratio_limit = max(0.05, float(cvd_max_range_ratio))
        buf_pct = max(0.0, float(cvd_breakout_buffer))
        min_adx = max(0.0, float(min_consolidation_adx))

        # ── Pre-compute series ───────────────────────────────────────────────
        cvd_series = pd.Series(cvd_data[:length])
        lookback_profiles: dict[int, np.ndarray] = {}
        for lb in range(lookback_min, lookback_max + 1):
            rolling_max = cvd_series.rolling(lb, min_periods=2).max().to_numpy()
            rolling_min = cvd_series.rolling(lb, min_periods=2).min().to_numpy()
            rolling_window_range = rolling_max - rolling_min
            lookback_profiles[lb] = (
                pd.Series(rolling_window_range)
                .rolling(lb * 2, min_periods=lb)
                .mean()
                .to_numpy()
            )

        # ATR for volatility expansion filter (filter #2)
        price_series = pd.Series(price_close[:length])
        atr_series = price_series.rolling(14, min_periods=2).std().to_numpy()  # cheap proxy

        volume_avg = pd.Series(volume[:length]).rolling(20, min_periods=5).mean().to_numpy()

        cvd_ema_up, cvd_ema_down = self._calculate_slope_masks(cvd_ema10[:length])
        price_up_slope, price_down_slope = self._calculate_slope_masks(price_close[:length])

        adx_series = self._compute_adx_simple(
            price_high[:length], price_low[:length], price_close[:length], period=14
        )

        for i in range(lookback_min, length):
            breakout_candidates: list[tuple[int, int, float, float, float, bool, bool]] = []
            for lookback in range(lookback_min, lookback_max + 1):
                if i < lookback:
                    continue

                start_idx = max(0, i - lookback)
                prior_window = cvd_data[start_idx:i]
                if len(prior_window) < 2:
                    continue

                range_high = float(np.max(prior_window))
                range_low = float(np.min(prior_window))
                range_size = range_high - range_low
                if range_size <= 1e-9:
                    continue

                avg_range = float(lookback_profiles[lookback][i - 1])
                if not np.isfinite(avg_range) or avg_range <= 1e-9:
                    continue

                is_consolidating = range_size <= (avg_range * ratio_limit)
                if not is_consolidating:
                    continue

                adx_ok = min_adx <= 0.0 or float(adx_series[i]) > min_adx
                if not adx_ok and lookback < min_consol:
                    continue

                ext = range_size * buf_pct
                cvd_value = float(cvd_data[i])
                long_break = cvd_value > (range_high + ext)
                short_break = cvd_value < (range_low - ext)
                if not (long_break or short_break):
                    continue

                breakout_candidates.append((lookback, start_idx, range_high, range_low, range_size, long_break, short_break))

            if not breakout_candidates:
                continue

            # Prefer tighter structures first; they usually represent cleaner squeezes.
            breakout_candidates.sort(key=lambda item: item[0])
            lookback, start_idx, range_high, range_low, range_size, long_break, short_break = breakout_candidates[0]

            # ════════════════════════════════════════════════════════════════
            # HARD GATES (base conditions — must pass to even score)
            # ════════════════════════════════════════════════════════════════
            # NOTE: price_close vs price_ema10 is intentionally NOT a gate here.
            # CVD leads price — the whole edge of this strategy is catching
            # institutional positioning BEFORE price crosses the EMA.
            # A long CVD breakout below EMA10 often precedes the most aggressive
            # upside moves (accumulation into EMA, then explosion through it).
            # Same in reverse for shorts above EMA10 (distribution).
            # Confirmation comes from price SLOPE (direction) + CVD EMA slope
            # (orderflow momentum) — not price position vs its rolling average.
            if long_break:
                base_ok = (
                        price_up_slope[i]
                        and cvd_ema_up[i]
                )
            else:
                base_ok = (
                        price_down_slope[i]
                        and cvd_ema_down[i]
                )

            if not base_ok:
                continue

            # ════════════════════════════════════════════════════════════════
            # CONVICTION SCORING — 5 institutional filters, each 0 or 1
            # ════════════════════════════════════════════════════════════════
            score = 0

            # FILTER 1 — ADX expansion (trend is building, not fading)
            # ADX should be RISING at the breakout bar vs previous bar.
            adx_rising = i > 0 and np.isfinite(adx_series[i]) and adx_series[i] > adx_series[i - 1]
            if adx_rising:
                score += 1

            # FILTER 2 — Volatility expansion (squeeze releasing energy)
            # ATR at bar i must be larger than ATR averaged over the squeeze window.
            # This catches "dead cat" breakouts where vol never expands.
            squeeze_atr_avg = float(np.mean(atr_series[start_idx:i])) if i > start_idx else 0.0
            curr_atr = float(atr_series[i]) if np.isfinite(atr_series[i]) else 0.0
            vol_expansion_ok = (
                    squeeze_atr_avg > 1e-9
                    and curr_atr >= squeeze_atr_avg * (1.0 + atr_expansion_pct)
            )
            if vol_expansion_ok:
                score += 1

            # FILTER 3 — Volume expansion (real participation)
            vol_avg = float(volume_avg[i]) if np.isfinite(volume_avg[i]) else 0.0
            if vol_avg > 0 and volume[i] >= vol_avg * vol_expansion_mult:
                score += 1

            # FILTER 4 — EMA position alignment (bonus, not a block)
            # Price above EMA10 on a long = extra confluence. Price below EMA10
            # on a long = the early aggressive entry (still valid, just no bonus).
            # This replaces the old hard gate — now it rewards but doesn't kill.
            ema_aligned = (
                (long_break and price_close[i] > price_ema10[i]) or
                (short_break and price_close[i] < price_ema10[i])
            )
            if ema_aligned:
                score += 1

            # FILTER 5 — Higher timeframe alignment (HTF proxy)
            # Use last N bars as a "higher timeframe" trend proxy.
            # Long: price trend over htf_bars is up. Short: down.
            # This is a cheap way to avoid counter-trend breakouts.
            if i >= htf_bars:
                htf_trend_up = price_close[i] > price_close[i - htf_bars]
                htf_trend_down = price_close[i] < price_close[i - htf_bars]
                htf_ok = htf_trend_up if long_break else htf_trend_down
            else:
                htf_ok = True  # not enough bars → don't penalise
            if htf_ok:
                score += 1

            # FILTER 6 — Regime block (don't long into a raging downtrend)
            # If ADX > regime_adx_block AND opposing side is dominant → skip.
            adx_val = float(adx_series[i]) if np.isfinite(adx_series[i]) else 0.0
            if regime_adx_block > 0 and adx_val > regime_adx_block:
                # Detect opposing direction dominance via DI lines would need
                # DI+/DI- separately. As a proxy: if ADX is very high AND
                # price is moving AGAINST us strongly, we block.
                if long_break:
                    regime_ok = price_close[i] > price_close[max(0, i - 3)]  # micro-trend with us
                else:
                    regime_ok = price_close[i] < price_close[max(0, i - 3)]
            else:
                regime_ok = True  # ADX not extreme → no block
            if regime_ok:
                score += 1

            # ════════════════════════════════════════════════════════════════
            # FIRE if conviction is sufficient
            # ════════════════════════════════════════════════════════════════
            if score >= min_conviction_score:
                if long_break:
                    long_breakout[i] = True
                else:
                    short_breakout[i] = True

        return short_breakout, long_breakout

    def _compute_squeeze_ratio(
            self,
            high_window: np.ndarray,
            low_window: np.ndarray,
            close_window: np.ndarray,
    ) -> float:
        """
        BB/Keltner squeeze ratio for the provided window.

        Ratio interpretation:
        - < 1.0: Bollinger bands sit inside Keltner channel (tighter squeeze)
        - > 1.0: Expansion regime
        """
        if len(close_window) < 2:
            return 1.0

        close_std = float(np.std(close_window))
        bb_width = max(1e-9, 4.0 * close_std)  # 2σ up + 2σ down

        prev_close = np.concatenate(([close_window[0]], close_window[:-1]))
        true_range = np.maximum.reduce([
            high_window - low_window,
            np.abs(high_window - prev_close),
            np.abs(low_window - prev_close),
        ])
        atr = float(np.mean(true_range))

        # Keltner width uses the common 1.5 * ATR envelope on both sides.
        kc_width = max(1e-9, 3.0 * atr)

        return bb_width / kc_width

    def _compute_adx_simple(
            self,
            high: np.ndarray,
            low: np.ndarray,
            close: np.ndarray,
            period: int = 14,
    ) -> np.ndarray:
        """Lightweight Wilder ADX — same formula used in auto_trader._compute_adx."""
        length = len(close)
        adx = np.zeros(length, dtype=float)
        if period <= 0 or length < period + 1:
            return adx

        tr = np.zeros(length)
        dm_plus = np.zeros(length)
        dm_minus = np.zeros(length)

        for i in range(1, length):
            hl = high[i] - low[i]
            hpc = abs(high[i] - close[i - 1])
            lpc = abs(low[i] - close[i - 1])
            tr[i] = max(hl, hpc, lpc)

            up_move = high[i] - high[i - 1]
            down_move = low[i - 1] - low[i]
            dm_plus[i] = up_move if up_move > down_move and up_move > 0 else 0.0
            dm_minus[i] = down_move if down_move > up_move and down_move > 0 else 0.0

        atr_w = np.zeros(length)
        dmp_w = np.zeros(length)
        dmm_w = np.zeros(length)
        atr_w[period] = np.sum(tr[1:period + 1])
        dmp_w[period] = np.sum(dm_plus[1:period + 1])
        dmm_w[period] = np.sum(dm_minus[1:period + 1])

        for i in range(period + 1, length):
            atr_w[i] = atr_w[i - 1] - atr_w[i - 1] / period + tr[i]
            dmp_w[i] = dmp_w[i - 1] - dmp_w[i - 1] / period + dm_plus[i]
            dmm_w[i] = dmm_w[i - 1] - dmm_w[i - 1] / period + dm_minus[i]

        dx = np.zeros(length)
        adx_raw = np.zeros(length)
        for i in range(period, length):
            denom = atr_w[i]
            if denom < 1e-9:
                continue
            di_plus = 100.0 * dmp_w[i] / denom
            di_minus = 100.0 * dmm_w[i] / denom
            di_sum = di_plus + di_minus
            dx[i] = 100.0 * abs(di_plus - di_minus) / di_sum if di_sum > 0 else 0.0

        first_adx_idx = (2 * period) - 1
        if first_adx_idx >= length:
            return adx_raw

        adx_raw[first_adx_idx] = np.mean(dx[period:2 * period])
        for i in range(2 * period, length):
            adx_raw[i] = (adx_raw[i - 1] * (period - 1) + dx[i]) / period

        return adx_raw

    def check_breakout_exit(
            self,
            current_idx: int,
            price_close: np.ndarray,
            price_ema10: np.ndarray
    ) -> tuple[bool, bool]:
        """
        Check if active breakout trade should exit.

        Exit conditions:
        - LONG: Price closes below EMA10
        - SHORT: Price closes above EMA10

        Returns: (exit_long, exit_short)
        """
        exit_long = False
        exit_short = False

        if current_idx < 0 or current_idx >= len(price_close):
            return exit_long, exit_short

        # Check LONG exit
        if self.active_breakout_long:
            if price_close[current_idx] < price_ema10[current_idx]:
                exit_long = True
                self.active_breakout_long = False
                self.breakout_entry_idx = -1

        # Check SHORT exit
        if self.active_breakout_short:
            if price_close[current_idx] > price_ema10[current_idx]:
                exit_short = True
                self.active_breakout_short = False
                self.breakout_entry_idx = -1

        return exit_long, exit_short

    def update_breakout_state(
            self,
            current_idx: int,
            long_signal: bool,
            short_signal: bool,
            range_high: float,
            range_low: float
    ):
        """
        Update internal state when breakout signals occur.
        Called from main detection loop to track active breakouts.
        """
        if long_signal:
            self.active_breakout_long = True
            self.active_breakout_short = False
            self.breakout_entry_idx = current_idx
            self.range_high = range_high
            self.range_low = range_low

        elif short_signal:
            self.active_breakout_short = True
            self.active_breakout_long = False
            self.breakout_entry_idx = current_idx
            self.range_high = range_high
            self.range_low = range_low

    def should_suppress_atr_reversal(self) -> tuple[bool, bool]:
        """
        Determine if ATR reversal signals should be suppressed.

        During active trending trades (breakout or EMA cross):
        - Suppress OPPOSING ATR signals
        - Allow SAME-DIRECTION signals (trend continuation)

        Returns: (suppress_short_atr, suppress_long_atr)
        """
        # Suppress SHORT ATR during:
        # - Active LONG breakout
        # - Active LONG EMA cross
        suppress_short = self.active_breakout_long or self.active_ema_cross_long

        # Suppress LONG ATR during:
        # - Active SHORT breakout
        # - Active SHORT EMA cross
        suppress_long = self.active_breakout_short or self.active_ema_cross_short

        return suppress_short, suppress_long

    def update_ema_cross_state(
            self,
            current_idx: int,
            long_signal: bool,
            short_signal: bool
    ):
        """
        Update internal state when EMA cross signals occur.
        """
        if long_signal:
            self.active_ema_cross_long = True
            self.active_ema_cross_short = False
            self.ema_cross_entry_idx = current_idx

        elif short_signal:
            self.active_ema_cross_short = True
            self.active_ema_cross_long = False
            self.ema_cross_entry_idx = current_idx

    def check_ema_cross_exit(
            self,
            current_idx: int,
            price_close: np.ndarray,
            price_ema10: np.ndarray,
            cvd_data: np.ndarray,
            cvd_ema10: np.ndarray
    ) -> tuple[bool, bool]:
        """
        Check if active EMA cross trade should exit.

        Exit conditions:
        - LONG: Price closes below EMA10 OR CVD crosses below EMA10
        - SHORT: Price closes above EMA10 OR CVD crosses above EMA10

        Returns: (exit_long, exit_short)
        """
        exit_long = False
        exit_short = False

        if current_idx < 0 or current_idx >= len(price_close):
            return exit_long, exit_short

        # Check LONG exit
        if self.active_ema_cross_long:
            if (price_close[current_idx] < price_ema10[current_idx] or
                    cvd_data[current_idx] < cvd_ema10[current_idx]):
                exit_long = True
                self.active_ema_cross_long = False
                self.ema_cross_entry_idx = -1

        # Check SHORT exit
        if self.active_ema_cross_short:
            if (price_close[current_idx] > price_ema10[current_idx] or
                    cvd_data[current_idx] > cvd_ema10[current_idx]):
                exit_short = True
                self.active_ema_cross_short = False
                self.ema_cross_entry_idx = -1

        return exit_long, exit_short
