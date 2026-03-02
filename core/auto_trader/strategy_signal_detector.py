import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _rolling_confirmation_vectorized(mask: np.ndarray, window: int) -> np.ndarray:
    """Return True when at least one True exists in trailing window ending at each bar."""
    if window <= 0 or len(mask) == 0:
        return mask.copy()

    int_mask = mask.astype(np.int32)
    cumsum = np.cumsum(int_mask)

    shifted = np.empty_like(cumsum)
    shifted[:window] = 0
    shifted[window:] = cumsum[:-window]

    return (cumsum - shifted) > 0


class StrategySignalDetector:
    """Detector focused only on ATR reversal strategy signals."""

    ATR_EXTENSION_THRESHOLD = 1.10
    ATR_FLAT_VELOCITY_PCT = 0.02

    def __init__(self, timeframe_minutes: int = 1):
        self.timeframe_minutes = timeframe_minutes
        # Keep these flags for compatibility with existing suppression flow.
        self.active_breakout_long = False
        self.active_breakout_short = False
        self.active_ema_cross_long = False
        self.active_ema_cross_short = False

    def detect_atr_reversal_strategy(
        self,
        price_atr_above: np.ndarray,
        price_atr_below: np.ndarray,
        cvd_atr_above: np.ndarray,
        cvd_atr_below: np.ndarray,
        atr_values: np.ndarray | None = None,
        timestamps: list | None = None,
        price_close: np.ndarray | None = None,
        price_open: np.ndarray | None = None,
        price_ema51: np.ndarray | None = None,
        price_vwap: np.ndarray | None = None,
        cvd_data: np.ndarray | None = None,
        vwap_min_distance_atr_mult: float = 0.3,
        divergence_lookback: int = 5,
        exhaustion_min_score: int = 2,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Institutional ATR reversal strategy with exhaustion and candle confirmation."""
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

        price_overextended_short = np.asarray(price_atr_above[:signal_length], dtype=bool)
        price_overextended_long = np.asarray(price_atr_below[:signal_length], dtype=bool)
        cvd_overextended_short = np.asarray(cvd_atr_above[:signal_length], dtype=bool)
        cvd_overextended_long = np.asarray(cvd_atr_below[:signal_length], dtype=bool)

        vwap_gate_short = np.ones(signal_length, dtype=bool)
        vwap_gate_long = np.ones(signal_length, dtype=bool)

        if (
            price_vwap is not None
            and price_close is not None
            and atr_values is not None
            and len(price_vwap) >= signal_length
            and len(price_close) >= signal_length
            and len(atr_values) >= signal_length
        ):
            atr_s = np.asarray(atr_values[:signal_length], dtype=float)
            close_s = np.asarray(price_close[:signal_length], dtype=float)
            vwap_s = np.asarray(price_vwap[:signal_length], dtype=float)
            min_vwap_gap = atr_s * vwap_min_distance_atr_mult
            vwap_gate_short = (close_s - vwap_s) > min_vwap_gap
            vwap_gate_long = (vwap_s - close_s) > min_vwap_gap

        # Core ATR-reversal setup: both price and CVD must be over-extended.
        base_short = price_overextended_short & cvd_overextended_short & vwap_gate_short
        base_long = price_overextended_long & cvd_overextended_long & vwap_gate_long

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
            prev_close = np.concatenate(([close_s[0]], close_s[:-1]))

            bearish_confirm = (close_s < open_s) & (close_s < prev_close)
            bullish_confirm = (close_s > open_s) & (close_s > prev_close)

            pre_conf_short_window = _rolling_confirmation_vectorized(pre_confirmation_short, confirmation_window)
            pre_conf_long_window = _rolling_confirmation_vectorized(pre_confirmation_long, confirmation_window)

            short_atr_reversal = pre_conf_short_window & bearish_confirm
            long_atr_reversal = pre_conf_long_window & bullish_confirm
        else:
            short_atr_reversal = pre_confirmation_short
            long_atr_reversal = pre_confirmation_long

        short_atr_reversal_raw = short_atr_reversal.copy()
        long_atr_reversal_raw = long_atr_reversal.copy()

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
        if n < lookback + 1:
            return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)

        series_price = pd.Series(price_close)
        series_cvd = pd.Series(cvd_data)

        roll_price_high = series_price.rolling(window=lookback + 1, min_periods=lookback).max().to_numpy()
        roll_price_low = series_price.rolling(window=lookback + 1, min_periods=lookback).min().to_numpy()
        roll_cvd_high = series_cvd.rolling(window=lookback + 1, min_periods=lookback).max().to_numpy()
        roll_cvd_low = series_cvd.rolling(window=lookback + 1, min_periods=lookback).min().to_numpy()

        with np.errstate(invalid="ignore"):
            bearish_div = (
                (price_close >= roll_price_high * 0.9995)
                & (cvd_data < roll_cvd_high * 0.9995)
                & np.isfinite(roll_price_high)
            )
            bullish_div = (
                (price_close <= roll_price_low * 1.0005)
                & (cvd_data > roll_cvd_low * 1.0005)
                & np.isfinite(roll_price_low)
            )

        bearish_div[:lookback] = False
        bullish_div[:lookback] = False
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

        decel_short = (prev_slope > 0) & (slope > 0) & (slope < prev_slope)
        decel_long = (prev_slope < 0) & (slope < 0) & (slope > prev_slope)

        return decel_short, decel_long

    def should_suppress_atr_reversal(self) -> tuple[bool, bool]:
        """Suppress opposing ATR signals when another trend-mode trade is active."""
        suppress_short = self.active_breakout_long or self.active_ema_cross_long
        suppress_long = self.active_breakout_short or self.active_ema_cross_short
        return suppress_short, suppress_long
