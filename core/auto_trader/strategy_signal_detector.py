import logging
from collections import deque
from contextlib import suppress

import numpy as np
import pandas as pd

from core.auto_trader.constants import TRADING_START, TRADING_END, MINUTES_PER_SESSION

logger = logging.getLogger(__name__)


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

    def detect_atr_reversal_strategy(
            self,
            price_atr_above: np.ndarray,  # Price ATR reversal - above EMA (potential SHORT)
            price_atr_below: np.ndarray,  # Price ATR reversal - below EMA (potential LONG)
            cvd_atr_above: np.ndarray,  # CVD ATR reversal - above EMA51 (potential SHORT)
            cvd_atr_below: np.ndarray,  # CVD ATR reversal - below EMA51 (potential LONG)
            active_breakout_long: np.ndarray | None = None,
            active_breakout_short: np.ndarray | None = None,
            breakout_long_momentum_strong: np.ndarray | None = None,
            breakout_short_momentum_strong: np.ndarray | None = None,
            breakout_switch_mode: str = BREAKOUT_SWITCH_ADAPTIVE,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        ATR REVERSAL STRATEGY:
        Confluence of ATR reversal signals in BOTH Price and CVD at the same time.

        - SHORT: Price ATR reversal above + CVD ATR reversal above (both overbought)
        - LONG: Price ATR reversal below + CVD ATR reversal below (both oversold)

        No waiting required - the confluence itself is the signal.

        🆕 SUPPRESSION: Opposing ATR signals blocked during active breakout trades.
        """

        # SHORT signals: Both Price and CVD show ATR reversal from above
        short_atr_reversal = price_atr_above & cvd_atr_above

        # LONG signals: Both Price and CVD show ATR reversal from below
        long_atr_reversal = price_atr_below & cvd_atr_below

        # Keep raw copies BEFORE any suppression — callers use these to count
        # how many ATR signals were skipped during an active breakout trade.
        short_atr_reversal_raw = short_atr_reversal.copy()
        long_atr_reversal_raw = long_atr_reversal.copy()

        if (
                active_breakout_long is not None and
                active_breakout_short is not None and
                len(active_breakout_long) == len(short_atr_reversal) and
                len(active_breakout_short) == len(short_atr_reversal)
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
        vol_avg = pd.Series(volume[:length]).rolling(10, min_periods=1).mean().to_numpy()
        bars_back = max(1, int(slope_lookback_bars))

        for idx in range(length):
            start_idx = max(0, idx - bars_back)
            price_delta = price_close[idx] - price_close[start_idx]
            cvd_delta = cvd_data[idx] - cvd_data[start_idx]

            price_trend_score = abs(price_close[idx] - price_ema10[idx]) / max(abs(price_ema10[idx]), eps)
            cvd_trend_score = abs(cvd_data[idx] - cvd_ema10[idx]) / max(abs(cvd_ema10[idx]), 1.0)
            vol_score = volume[idx] / max(vol_avg[idx], eps)

            bullish_alignment = price_delta > 0 and cvd_delta > 0 and price_close[idx] > price_ema10[idx]
            bearish_alignment = price_delta < 0 and cvd_delta < 0 and price_close[idx] < price_ema10[idx]

            composite = (price_trend_score * 0.45) + (cvd_trend_score * 0.35) + (vol_score * 0.20)

            if long_context[idx] and bullish_alignment and composite >= 1.05:
                long_strong[idx] = True
            if short_context[idx] and bearish_alignment and composite >= 1.05:
                short_strong[idx] = True

        return long_strong, short_strong

    def detect_ema_cvd_cross_strategy(
            self,
            price_data: np.ndarray,
            price_ema10: np.ndarray,
            price_ema51: np.ndarray,
            cvd_data: np.ndarray,
            cvd_ema10: np.ndarray,
            cvd_ema51: np.ndarray,
            cvd_ema_gap_threshold: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        EMA & CVD CROSS STRATEGY:
        - Price already above/below both EMA10 and EMA51
        - CVD already above/below its EMA10
        - CVD crosses above/below its EMA51 → SIGNAL
        """

        # Price position checks
        price_above_both_emas = (price_data > price_ema10) & (price_data > price_ema51)
        price_below_both_emas = (price_data < price_ema10) & (price_data < price_ema51)

        # CVD position checks
        cvd_above_ema10 = cvd_data > cvd_ema10
        cvd_below_ema10 = cvd_data < cvd_ema10

        # Detect CVD crosses of EMA51
        cvd_prev = np.concatenate(([cvd_data[0]], cvd_data[:-1]))
        cvd_ema51_prev = np.concatenate(([cvd_ema51[0]], cvd_ema51[:-1]))

        cvd_cross_above_ema51_raw = (cvd_prev <= cvd_ema51_prev) & (cvd_data > cvd_ema51)
        cvd_cross_below_ema51_raw = (cvd_prev >= cvd_ema51_prev) & (cvd_data < cvd_ema51)

        # Anti-hug filter - CVD must be meaningfully away from EMA51
        gap = np.abs(cvd_data - cvd_ema51)
        min_gap = cvd_ema_gap_threshold * 0.5
        cvd_cross_above_ema51 = cvd_cross_above_ema51_raw & (gap > min_gap)
        cvd_cross_below_ema51 = cvd_cross_below_ema51_raw & (gap > min_gap)

        # Slope confirmation - both price and CVD trending in same direction
        price_up_slope, price_down_slope = self._calculate_slope_masks(price_data)
        cvd_up_slope, cvd_down_slope = self._calculate_slope_masks(cvd_data)

        # LONG signals: Everything bullish
        long_ema_cross = (
                price_above_both_emas &
                cvd_above_ema10 &
                cvd_cross_above_ema51 &
                price_up_slope &
                cvd_up_slope
        )

        # SHORT signals: Everything bearish
        short_ema_cross = (
                price_below_both_emas &
                cvd_below_ema10 &
                cvd_cross_below_ema51 &
                price_down_slope &
                cvd_down_slope
        )

        return short_ema_cross, long_ema_cross

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
            bars_back = max(1, int(round(minutes / max(self.timeframe_minutes, 1))))
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

        # Pre-compute ADX only if needed (expensive — avoid if not configured)
        adx_series = None
        if min_consol_bars > 0 and min_consolidation_adx > 0:
            try:
                # Simple Wilder ADX (14-period)
                adx_series = self._compute_adx_simple(price_high, price_low, price_close, period=14)
            except Exception:
                adx_series = None

        # breakout_threshold_multiplier is converted into a minimum breakout
        # extension beyond the range boundary. 1.0 ~= 3% of the prior range.
        base_breakout_strength = max(0.0, 0.03 * float(breakout_threshold_multiplier))

        # Calculate rolling range and average range
        for i in range(lookback_bars, length):
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

        return long_breakout, short_breakout, range_highs, range_lows

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
        if length < period + 1:
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

        adx_raw[2 * period - 1] = np.mean(dx[period:2 * period])
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
