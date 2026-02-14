import logging
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta
import numpy as np

import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QHBoxLayout,
    QPushButton, QWidget, QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QObject, QThread
from pyqtgraph import AxisItem, TextItem

from kiteconnect import KiteConnect
from core.cvd.cvd_historical import CVDHistoricalBuilder
from core.cvd.cvd_mode import CVDMode

logger = logging.getLogger(__name__)

from datetime import time

TRADING_START = time(9, 15)
TRADING_END = time(15, 30)
MINUTES_PER_SESSION = 375  # 6h 15m


# =============================================================================
# Date Navigator (same behavior as multi-chart)
# =============================================================================

class DateNavigator(QWidget):
    date_changed = Signal(datetime, datetime)  # current_date, previous_date

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.btn_back = QPushButton("◀")
        self.btn_back.setFixedSize(40, 32)
        self.btn_back.clicked.connect(self._go_backward)

        self.lbl_dates = QLabel()
        self.lbl_dates.setAlignment(Qt.AlignCenter)
        self.lbl_dates.setMinimumWidth(500)
        self.lbl_dates.setStyleSheet("""
            QLabel {
                color: #E0E0E0;
                font-size: 13px;
                font-weight: 600;
            }
        """)

        self.btn_forward = QPushButton("▶")
        self.btn_forward.setFixedSize(40, 32)
        self.btn_forward.clicked.connect(self._go_forward)

        layout.addStretch()
        layout.addWidget(self.btn_back)
        layout.addWidget(self.lbl_dates)
        layout.addWidget(self.btn_forward)
        layout.addStretch()

    def _get_previous_trading_day(self, date: datetime) -> datetime:
        prev = date - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev

    def _get_next_trading_day(self, date: datetime) -> datetime:
        nxt = date + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt

    def _update_display(self):
        prev = self._get_previous_trading_day(self._current_date)
        cur_str = self._current_date.strftime("%A, %b %d, %Y")
        prev_str = prev.strftime("%A, %b %d, %Y")

        self.lbl_dates.setText(
            f"<span style='color:#5B9BD5;'>Previous: {prev_str}</span>"
            f"  |  "
            f"<span style='color:#26A69A;'>Current: {cur_str}</span>"
        )

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.btn_forward.setEnabled(self._current_date < today)

    def _go_backward(self):
        self._current_date = self._get_previous_trading_day(self._current_date)
        self._update_display()
        self.date_changed.emit(
            self._current_date,
            self._get_previous_trading_day(self._current_date)
        )

    def _go_forward(self):
        self._current_date = self._get_next_trading_day(self._current_date)
        self._update_display()
        self.date_changed.emit(
            self._current_date,
            self._get_previous_trading_day(self._current_date)
        )

    def get_dates(self):
        return (
            self._current_date,
            self._get_previous_trading_day(self._current_date)
        )


# =============================================================================
# Background data fetch worker — keeps kite.historical_data() OFF the GUI thread
# =============================================================================

class _DataFetchWorker(QObject):
    result_ready = Signal(object, object, float)
    error = Signal(str)
    finished = Signal()

    def __init__(self, kite, instrument_token, from_dt, to_dt, timeframe_minutes, focus_mode):
        super().__init__()
        self.kite = kite
        self.instrument_token = instrument_token
        self.from_dt = from_dt
        self.to_dt = to_dt
        self.timeframe_minutes = timeframe_minutes
        self.focus_mode = focus_mode

    def run(self):
        try:
            hist = self.kite.historical_data(
                self.instrument_token,
                self.from_dt,
                self.to_dt,
                interval="minute"
            )

            if not hist:
                self.error.emit("no_data")
                return

            df = pd.DataFrame(hist)
            if df.empty:
                self.error.emit("empty_df")
                return

            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

            if self.timeframe_minutes > 1:
                rule = f"{self.timeframe_minutes}min"
                df = df.resample(rule).agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum"
                }).dropna()

            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)
            cvd_df["session"] = cvd_df.index.date

            sessions = sorted(cvd_df["session"].unique())
            if not sessions:
                self.error.emit("no_sessions")
                return

            prev_close = 0.0
            if len(sessions) >= 2:
                prev_data = cvd_df[cvd_df["session"] == sessions[-2]]
                if not prev_data.empty:
                    prev_close = prev_data["close"].iloc[-1]

            df["session"] = df.index.date

            if self.focus_mode:
                cvd_out = cvd_df[cvd_df["session"] == sessions[-1]].copy()
                price_out = df[df["session"] == sessions[-1]].copy()
            else:
                cvd_out = cvd_df[cvd_df["session"].isin(sessions[-2:])].copy()
                price_out = df[df["session"].isin(sessions[-2:])].copy()

            self.result_ready.emit(cvd_out, price_out, prev_close)

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


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

    def __init__(self, timeframe_minutes: int = 1):
        self.timeframe_minutes = timeframe_minutes
        self.atr_reversal_timestamps = {}  # Store ATR reversal times for confirmation tracking

    def detect_atr_reversal_strategy(
            self,
            price_atr_above: np.ndarray,  # Price ATR reversal - above EMA (potential SHORT)
            price_atr_below: np.ndarray,  # Price ATR reversal - below EMA (potential LONG)
            cvd_atr_above: np.ndarray,  # CVD ATR reversal - above EMA51 (potential SHORT)
            cvd_atr_below: np.ndarray,  # CVD ATR reversal - below EMA51 (potential LONG)
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        ATR REVERSAL STRATEGY:
        Confluence of ATR reversal signals in BOTH Price and CVD at the same time.

        - SHORT: Price ATR reversal above + CVD ATR reversal above (both overbought)
        - LONG: Price ATR reversal below + CVD ATR reversal below (both oversold)

        No waiting required - the confluence itself is the signal.
        """

        # SHORT signals: Both Price and CVD show ATR reversal from above
        short_atr_reversal = price_atr_above & cvd_atr_above

        # LONG signals: Both Price and CVD show ATR reversal from below
        long_atr_reversal = price_atr_below & cvd_atr_below

        return short_atr_reversal, long_atr_reversal

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