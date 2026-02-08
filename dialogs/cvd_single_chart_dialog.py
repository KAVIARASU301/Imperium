import logging
from datetime import datetime, timedelta
import numpy as np

import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QHBoxLayout,
    QPushButton, QWidget, QCheckBox
)
from PySide6.QtCore import Qt, QTimer, Signal
from pyqtgraph import AxisItem, TextItem

from kiteconnect import KiteConnect
from core.cvd.cvd_historical import CVDHistoricalBuilder
from core.cvd.cvd_mode import CVDMode

logger = logging.getLogger(__name__)

from datetime import time

TRADING_START = time(9, 15)
TRADING_END   = time(15, 30)
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
# Single CVD Chart Dialog (with navigator)
# =============================================================================

class CVDSingleChartDialog(QDialog):
    REFRESH_INTERVAL_MS = 3000

    def __init__(
            self,
            kite: KiteConnect,
            instrument_token: int,
            symbol: str,
            cvd_engine,  # ✅ ADD THIS
            parent=None,
    ):

        super().__init__(parent)

        self.kite = kite
        self.instrument_token = instrument_token
        self.symbol = symbol
        self.cvd_engine = cvd_engine
        self.timeframe_minutes = 1  # default = 1 minute

        self.live_mode = True
        self.current_date = None
        self.previous_date = None

        self.setWindowTitle(f"Price & Cumulative Volume Chart — {symbol}")
        self.setMinimumSize(1100, 680)
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )

        # Prevent flickering during maximize
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, False)

        self._setup_ui()
        self._connect_signals()

        # Init in LIVE mode
        self.current_date, self.previous_date = self.navigator.get_dates()
        self._load_and_plot()
        self._start_refresh_timer()

        self.all_timestamps = []

    # ------------------------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(4)

        # ================= TOP CONTROL BAR =================
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(12)

        top_bar.addStretch()

        # -------- Timeframe buttons (LEFT of center) --------
        tf_layout = QHBoxLayout()
        tf_layout.setSpacing(4)

        self.tf_buttons = {}

        for label, minutes in [("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("1h", 60)]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setStyleSheet("""
                QPushButton {
                    background:#1B1F2B;
                    border:1px solid #3A4458;
                    padding:2px 8px;
                }
                QPushButton:checked {
                    background:#5B9BD5;
                    color:#000;
                    font-weight:600;
                }
            """)
            btn.clicked.connect(lambda checked, m=minutes: self._on_timeframe_changed(m))
            self.tf_buttons[minutes] = btn
            tf_layout.addWidget(btn)

        # Default select 1m
        self.tf_buttons[1].setChecked(True)

        top_bar.addLayout(tf_layout)

        # Navigator (CENTER)
        self.navigator = DateNavigator(self)
        top_bar.addWidget(self.navigator)

        # Focus Button (RIGHT of center)
        self.btn_focus = QPushButton("🎯 Focus (1D)")
        self.btn_focus.setCheckable(True)
        self.btn_focus.setFixedHeight(28)
        self.btn_focus.setMinimumWidth(120)
        self.btn_focus.setStyleSheet("""
            QPushButton {
                background:#212635;
                border:1px solid #3A4458;
                border-radius:4px;
                padding:4px 10px;
            }
            QPushButton:checked {
                background:#26A69A;
                color:#000;
                font-weight:600;
            }
        """)
        self.btn_focus.toggled.connect(self._on_focus_mode_changed)

        top_bar.addWidget(self.btn_focus)
        top_bar.addStretch()

        root.addLayout(top_bar)

        # ================= EMA CONTROL BAR (NEW) =================
        ema_bar = QHBoxLayout()
        ema_bar.setContentsMargins(0, 0, 0, 4)
        ema_bar.setSpacing(16)

        ema_bar.addStretch()

        # EMA Label
        ema_label = QLabel("EMAs:")
        ema_label.setStyleSheet("color: #B0B0B0; font-weight: 600; font-size: 12px;")
        ema_bar.addWidget(ema_label)

        # EMA Checkboxes with institutional colors
        self.ema_checkboxes = {}
        ema_configs = [
            (10, "#00D9FF", "10"),  # Cyan - fast
            (21, "#FFD700", "21"),  # Gold - medium
            (51, "#FF6B6B", "51")  # Salmon - slow
        ]

        for period, color, label in ema_configs:
            cb = QCheckBox(label)

            # ✅ Default: only EMA 51 enabled
            cb.setChecked(period == 51)

            cb.setStyleSheet(f"""
                QCheckBox {{
                    color: {color};
                    font-weight: 600;
                    font-size: 12px;
                    spacing: 6px;
                }}
                QCheckBox::indicator {{
                    width: 16px;
                    height: 16px;
                    border: 2px solid {color};
                    border-radius: 3px;
                    background: #1B1F2B;
                }}
                QCheckBox::indicator:checked {{
                    background: {color};
                }}
            """)
            cb.toggled.connect(lambda checked, p=period: self._on_ema_toggled(p, checked))
            self.ema_checkboxes[period] = cb
            ema_bar.addWidget(cb)

        ema_bar.addStretch()
        root.addLayout(ema_bar)

        # === PRICE CHART (TOP) ===
        self.price_axis = AxisItem(orientation="bottom")
        self.price_axis.setStyle(showValues=False)

        self.price_plot = pg.PlotWidget(axisItems={"bottom": self.price_axis})
        self.price_plot.setBackground("#161A25")
        self.price_plot.showGrid(x=True, y=True, alpha=0.12)
        self.price_plot.setMenuEnabled(False)
        self.price_plot.setMinimumHeight(200)

        # Price Y-axis styling with fixed width
        price_y_axis = self.price_plot.getAxis("left")
        price_y_axis.setWidth(70)
        price_y_axis.setTextPen(pg.mkPen("#FFE57F"))
        price_y_axis.setPen(pg.mkPen("#8A9BA8"))
        price_y_axis.enableAutoSIPrefix(False)

        # Price curves
        self.price_prev_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#9E9E9E", width=2, style=Qt.DashLine)
        )
        self.price_today_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#FFE57F", width=2.5)
        )

        self.price_plot.addItem(self.price_prev_curve)
        self.price_plot.addItem(self.price_today_curve)

        # Price live dot
        self.price_live_dot = pg.ScatterPlotItem(
            size=5,
            brush=pg.mkBrush(255, 229, 127, 200),
            pen=pg.mkPen("#FFFFFF", width=1)
        )
        self.price_plot.addItem(self.price_live_dot)

        # 🔥 INSTITUTIONAL-GRADE PRICE EMAS
        self.price_ema10_curve = pg.PlotCurveItem(
            pen=pg.mkPen('#00D9FF', width=2.0, style=Qt.SolidLine)
        )
        self.price_ema21_curve = pg.PlotCurveItem(
            pen=pg.mkPen('#FFD700', width=2.0, style=Qt.SolidLine)
        )
        self.price_ema51_curve = pg.PlotCurveItem(
            pen=pg.mkPen('#FF6B6B', width=2.0, style=Qt.SolidLine)
        )

        self.price_plot.addItem(self.price_ema10_curve)
        self.price_plot.addItem(self.price_ema21_curve)
        self.price_plot.addItem(self.price_ema51_curve)

        # Full opacity for clear visibility
        self.price_ema10_curve.setOpacity(0.85)
        self.price_ema21_curve.setOpacity(0.85)
        self.price_ema51_curve.setOpacity(0.85)

        # Price crosshair
        pen = pg.mkPen((255, 255, 255, 120), width=1, style=Qt.DashLine)
        self.price_crosshair = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.price_crosshair.hide()
        self.price_plot.addItem(self.price_crosshair)

        # 🔥 Price EMA Legend (top-right corner)
        self.price_legend = pg.LegendItem(offset=(10, 10))
        self.price_legend.setParentItem(self.price_plot.plotItem)
        self.price_legend.anchor((1, 0), (1, 0))  # Top-right

        root.addWidget(self.price_plot, 1)

        # === CVD CHART (BOTTOM) ===
        self.axis = AxisItem(orientation="bottom")
        self.plot = pg.PlotWidget(axisItems={"bottom": self.axis})
        bottom_axis = self.plot.getAxis("bottom")
        bottom_axis.setHeight(32)
        bottom_axis.setStyle(showValues=True)
        bottom_axis.setTextPen(pg.mkPen("#8A9BA8"))
        bottom_axis.setPen(pg.mkPen("#8A9BA8"))

        # CVD Y-axis with fixed width
        cvd_y_axis = self.plot.getAxis("left")
        cvd_y_axis.setWidth(70)
        cvd_y_axis.enableAutoSIPrefix(False)

        def cvd_axis_formatter(values, scale, spacing):
            labels = []
            for v in values:
                if abs(v) >= 1_000_000:
                    labels.append(f'{v / 1_000_000:.1f}M')
                elif abs(v) >= 1_000:
                    labels.append(f'{v / 1_000:.0f}K')
                else:
                    labels.append(f'{int(v)}')
            return labels

        cvd_y_axis.tickStrings = cvd_axis_formatter

        self.plot.setBackground("#161A25")
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.setMenuEnabled(False)
        self.plot.setMinimumHeight(200)

        root.addWidget(self.plot, 1)

        zero_pen = pg.mkPen("#6C7386", style=Qt.DashLine, width=1)
        self.plot.addItem(pg.InfiniteLine(0, angle=0, pen=zero_pen))

        self.prev_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#7A7A7A", width=2, style=Qt.DashLine)
        )
        self.today_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#26A69A", width=2.5)
        )

        self.plot.addItem(self.prev_curve)
        self.plot.addItem(self.today_curve)

        self.live_dot = pg.ScatterPlotItem(
            size=5,
            brush=pg.mkBrush(38, 166, 154, 200),
            pen=pg.mkPen("#FFFFFF", width=1)
        )
        self.plot.addItem(self.live_dot)

        # 🔥 INSTITUTIONAL-GRADE CVD EMAS
        self.cvd_ema10_curve = pg.PlotCurveItem(
            pen=pg.mkPen('#00D9FF', width=1.8, style=Qt.SolidLine)
        )
        self.cvd_ema21_curve = pg.PlotCurveItem(
            pen=pg.mkPen('#FFD700', width=1.8, style=Qt.SolidLine)
        )
        self.cvd_ema51_curve = pg.PlotCurveItem(
            pen=pg.mkPen('#FF6B6B', width=1.8, style=Qt.SolidLine)
        )

        self.plot.addItem(self.cvd_ema10_curve)
        self.plot.addItem(self.cvd_ema21_curve)
        self.plot.addItem(self.cvd_ema51_curve)

        # Higher opacity for CVD (subtle but visible)
        self.cvd_ema10_curve.setOpacity(0.7)
        self.cvd_ema21_curve.setOpacity(0.7)
        self.cvd_ema51_curve.setOpacity(0.7)

        # CVD crosshair
        self.crosshair_line = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.crosshair_line.hide()
        self.plot.addItem(self.crosshair_line)

        # X-axis time label
        self.x_time_label = pg.TextItem(
            "",
            anchor=(0.5, 1),
            color="#E0E0E0",
            fill=pg.mkBrush("#212635"),
            border=pg.mkPen("#3A4458")
        )
        self.x_time_label.hide()
        self.plot.addItem(self.x_time_label, ignoreBounds=True)

        # 🔥 CVD EMA Legend (top-right corner)
        self.cvd_legend = pg.LegendItem(offset=(10, 10))
        self.cvd_legend.setParentItem(self.plot.plotItem)
        self.cvd_legend.anchor((1, 0), (1, 0))  # Top-right

        # Connect mouse events
        self.price_plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # Link X-axis ranges
        self.price_plot.setXLink(self.plot)

        self.dot_timer = QTimer(self)
        self.dot_timer.timeout.connect(self._blink_dot)
        self.dot_timer.start(500)
        self._dot_visible = True

    # ------------------------------------------------------------------

    def _connect_signals(self):
        self.navigator.date_changed.connect(self._on_date_changed)

    # ------------------------------------------------------------------

    def _on_ema_toggled(self, period: int, checked: bool):
        """Toggle EMA visibility"""
        opacity = 0.85 if checked else 0.0

        if period == 10:
            self.price_ema10_curve.setOpacity(opacity if checked else 0)
            self.cvd_ema10_curve.setOpacity(0.7 if checked else 0)
        elif period == 21:
            self.price_ema21_curve.setOpacity(opacity if checked else 0)
            self.cvd_ema21_curve.setOpacity(0.7 if checked else 0)
        elif period == 51:
            self.price_ema51_curve.setOpacity(opacity if checked else 0)
            self.cvd_ema51_curve.setOpacity(0.7 if checked else 0)

        # Update legends
        self._update_ema_legends()

    def _update_ema_legends(self):
        """Update legend visibility based on checkbox state"""
        self.price_legend.clear()
        self.cvd_legend.clear()

        if self.ema_checkboxes[10].isChecked():
            self.price_legend.addItem(self.price_ema10_curve, "EMA 10")
            self.cvd_legend.addItem(self.cvd_ema10_curve, "EMA 10")

        if self.ema_checkboxes[21].isChecked():
            self.price_legend.addItem(self.price_ema21_curve, "EMA 21")
            self.cvd_legend.addItem(self.cvd_ema21_curve, "EMA 21")

        if self.ema_checkboxes[51].isChecked():
            self.price_legend.addItem(self.price_ema51_curve, "EMA 51")
            self.cvd_legend.addItem(self.cvd_ema51_curve, "EMA 51")

    def _on_focus_mode_changed(self, enabled: bool):
        self.btn_focus.setText("🎯 FOCUS ON" if enabled else "Focus (1D)")
        if enabled:
            self.cvd_engine.set_mode(CVDMode.SINGLE_DAY)
        else:
            self.cvd_engine.set_mode(CVDMode.NORMAL)

        # Clear visual state
        self.prev_curve.clear()
        self.today_curve.clear()
        self.live_dot.clear()

        self.price_prev_curve.clear()
        self.price_today_curve.clear()
        self.price_live_dot.clear()

        self.all_timestamps.clear()
        self._load_and_plot()

    def _on_mouse_moved(self, pos):
        in_price_plot = self.price_plot.sceneBoundingRect().contains(pos)
        in_cvd_plot = self.plot.sceneBoundingRect().contains(pos)

        if not (in_price_plot or in_cvd_plot):
            self.crosshair_line.hide()
            self.price_crosshair.hide()
            self.x_time_label.hide()
            return

        if in_price_plot:
            mouse_point = self.price_plot.plotItem.vb.mapSceneToView(pos)
        else:
            mouse_point = self.plot.plotItem.vb.mapSceneToView(pos)

        x = int(round(mouse_point.x()))

        total = len(self.all_timestamps)
        if not (0 <= x < total):
            self.crosshair_line.hide()
            self.price_crosshair.hide()
            self.x_time_label.hide()
            return

        self.crosshair_line.setPos(x)
        self.price_crosshair.setPos(x)
        self.crosshair_line.show()
        self.price_crosshair.show()

        if self.btn_focus.isChecked():
            # Focus mode: find nearest timestamp by session minute
            ts = min(
                self.all_timestamps,
                key=lambda t: abs(self._time_to_session_index(t) - x)
            )
        else:
            ts = self.all_timestamps[x]
        time_text = ts.strftime("%H:%M")

        vb_cvd = self.plot.plotItem.vb
        cvd_y_min, cvd_y_max = vb_cvd.viewRange()[1]
        y_pos_cvd = cvd_y_min - (cvd_y_max - cvd_y_min) * 0.02

        self.x_time_label.setText(time_text)
        self.x_time_label.setPos(x, y_pos_cvd)
        self.x_time_label.show()

    def _on_date_changed(self, current_date: datetime, previous_date: datetime):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self.current_date = current_date
        self.previous_date = previous_date

        if current_date >= today:
            self.live_mode = True
            if not self.refresh_timer.isActive():
                self.refresh_timer.start(self.REFRESH_INTERVAL_MS)
        else:
            self.live_mode = False
            self.refresh_timer.stop()

        self._load_and_plot()

    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_ema(data: np.ndarray, period: int) -> np.ndarray:
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

    # ------------------------------------------------------------------

    def _load_and_plot(self):
        focus_mode = self.btn_focus.isChecked()

        if not self.kite or not getattr(self.kite, "access_token", None):
            return

        try:
            if self.live_mode:
                to_dt = datetime.now()
                from_dt = to_dt - timedelta(days=5)
            else:
                to_dt = self.current_date + timedelta(days=1)
                from_dt = self.previous_date

            hist = self.kite.historical_data(
                self.instrument_token,
                from_dt,
                to_dt,
                interval="minute"
            )
            if not hist:
                return

            df = pd.DataFrame(hist)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

            # Timeframe aggregation
            if self.timeframe_minutes > 1:
                rule = f"{self.timeframe_minutes}min"

                df = df.resample(rule).agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum"
                }).dropna()

            # Build CVD data
            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)
            cvd_df["session"] = cvd_df.index.date

            sessions = sorted(cvd_df["session"].unique())
            if not sessions:
                return

            prev_close = 0.0
            if len(sessions) >= 2:
                prev_data = cvd_df[cvd_df["session"] == sessions[-2]]
                if not prev_data.empty:
                    prev_close = prev_data["close"].iloc[-1]

            df["session"] = df.index.date

            if focus_mode:
                cvd_df = cvd_df[cvd_df["session"] == sessions[-1]]
                price_df = df[df["session"] == sessions[-1]]
            else:
                cvd_df = cvd_df[cvd_df["session"].isin(sessions[-2:])]
                price_df = df[df["session"].isin(sessions[-2:])]

            self._plot_data(cvd_df, price_df, prev_close)

        except Exception:
            logger.exception("Failed to load CVD data")

    # ------------------------------------------------------------------

    def _plot_data(self, cvd_df: pd.DataFrame, price_df: pd.DataFrame, prev_close: float):
        focus_mode = self.btn_focus.isChecked()

        # Clear all curves
        self.prev_curve.clear()
        self.today_curve.clear()
        self.live_dot.clear()
        self.price_prev_curve.clear()
        self.price_today_curve.clear()
        self.price_live_dot.clear()

        # Clear EMA curves
        self.cvd_ema10_curve.clear()
        self.cvd_ema21_curve.clear()
        self.cvd_ema51_curve.clear()
        self.price_ema10_curve.clear()
        self.price_ema21_curve.clear()
        self.price_ema51_curve.clear()

        self.all_timestamps = []
        self.all_cvd_data = []
        self.all_price_data = []

        x_offset = 0
        sessions = sorted(cvd_df["session"].unique())

        for i, sess in enumerate(sessions):
            df_cvd_sess = cvd_df[cvd_df["session"] == sess]
            df_price_sess = price_df[price_df["session"] == sess]

            cvd_y_raw = df_cvd_sess["close"].values
            price_y_raw = df_price_sess["close"].values

            # Rebasing logic for CVD
            if i == 0 and len(sessions) == 2 and not self.btn_focus.isChecked():
                cvd_y = cvd_y_raw - prev_close
            else:
                cvd_y = cvd_y_raw

            price_y = price_y_raw

            # Prepend zero point for current session
            is_current_session = (i == len(sessions) - 1)

            if focus_mode:
                # Fixed session time (09:15 → 15:30)
                xs = [
                    self._time_to_session_index(ts)
                    for ts in df_cvd_sess.index
                ]
            else:
                # Sequential index (comparison mode – old behavior)
                xs = list(range(x_offset, x_offset + len(df_cvd_sess)))

            if not is_current_session:
                self.all_timestamps.extend(df_cvd_sess.index.tolist())
                self.all_cvd_data.extend(cvd_y.tolist())
                self.all_price_data.extend(price_y.tolist())
            else:
                self.all_timestamps.extend(df_cvd_sess.index.tolist())
                self.all_cvd_data.extend(cvd_y.tolist())
                self.all_price_data.extend(price_y.tolist())

            # Plot CVD
            if i == 0 and len(sessions) == 2:
                self.prev_curve.setData(xs, cvd_y)
            else:
                self.today_curve.setData(xs, cvd_y)
                if xs:
                    self.live_dot.setData([xs[-1]], [cvd_y[-1]])

            # Plot Price
            if i == 0 and len(sessions) == 2:
                self.price_prev_curve.setData(xs, price_y)
            else:
                self.price_today_curve.setData(xs, price_y)
                if xs:
                    self.price_live_dot.setData([xs[-1]], [price_y[-1]])

            if not focus_mode:
                x_offset += len(df_cvd_sess)

        # Time axis formatter
        def time_formatter(values, *_):
            labels = []
            base = datetime.now().replace(
                hour=9, minute=15, second=0, microsecond=0
            )

            for v in values:
                minute = int(v)
                if 0 <= minute < MINUTES_PER_SESSION:
                    ts = base + timedelta(minutes=minute)
                    labels.append(ts.strftime("%H:%M"))
                else:
                    labels.append("")
            return labels

        if focus_mode:
            self.axis.tickStrings = time_formatter
            self.price_axis.tickStrings = time_formatter
        else:
            # Restore default numeric axis behavior
            self.axis.tickStrings = self.axis.__class__.tickStrings.__get__(self.axis, AxisItem)
            self.price_axis.tickStrings = self.price_axis.__class__.tickStrings.__get__(self.price_axis, AxisItem)

        # 🔥 PLOT INSTITUTIONAL EMAS
        if len(self.all_cvd_data) > 0:
            cvd_data_array = np.array(self.all_cvd_data)
            price_data_array = np.array(self.all_price_data)
            if focus_mode:
                x_indices = [
                    self._time_to_session_index(ts)
                    for ts in self.all_timestamps
                ]
            else:
                x_indices = list(range(len(self.all_timestamps)))

            # Calculate EMAs
            enabled_emas = self._enabled_ema_periods()

            # --- CVD EMAs ---
            if 10 in enabled_emas:
                self.cvd_ema10_curve.setData(
                    x_indices, self._calculate_ema(cvd_data_array, 10)
                )
            else:
                self.cvd_ema10_curve.clear()

            if 21 in enabled_emas:
                self.cvd_ema21_curve.setData(
                    x_indices, self._calculate_ema(cvd_data_array, 21)
                )
            else:
                self.cvd_ema21_curve.clear()

            if 51 in enabled_emas:
                self.cvd_ema51_curve.setData(
                    x_indices, self._calculate_ema(cvd_data_array, 51)
                )
            else:
                self.cvd_ema51_curve.clear()

            # --- PRICE EMAs ---
            if 10 in enabled_emas:
                self.price_ema10_curve.setData(
                    x_indices, self._calculate_ema(price_data_array, 10)
                )
            else:
                self.price_ema10_curve.clear()

            if 21 in enabled_emas:
                self.price_ema21_curve.setData(
                    x_indices, self._calculate_ema(price_data_array, 21)
                )
            else:
                self.price_ema21_curve.clear()

            if 51 in enabled_emas:
                self.price_ema51_curve.setData(
                    x_indices, self._calculate_ema(price_data_array, 51)
                )
            else:
                self.price_ema51_curve.clear()

            # Update legends
            self._update_ema_legends()

            # ✅ FORCE EMA VISIBILITY BASED ON CHECKBOX STATE
            for period, cb in self.ema_checkboxes.items():
                self._on_ema_toggled(period, cb.isChecked())


        # Set X range
        self.plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.price_plot.enableAutoRange(axis=pg.ViewBox.YAxis)

        if focus_mode:
            # Fixed session view
            self.plot.setXRange(0, MINUTES_PER_SESSION - 1, padding=0)
            self.price_plot.setXRange(0, MINUTES_PER_SESSION - 1, padding=0)
        else:
            # Let chart auto-scale like before
            self.plot.enableAutoRange(axis=pg.ViewBox.XAxis)
            self.price_plot.enableAutoRange(axis=pg.ViewBox.XAxis)

    # ------------------------------------------------------------------
    def _enabled_ema_periods(self) -> set[int]:
        """Return EMA periods currently enabled via checkboxes"""
        return {
            period for period, cb in self.ema_checkboxes.items()
            if cb.isChecked()
        }

    def _blink_dot(self):
        self._dot_visible = not self._dot_visible
        alpha = 220 if self._dot_visible else 60
        self.live_dot.setBrush(pg.mkBrush(38, 166, 154, alpha))
        self.price_live_dot.setBrush(pg.mkBrush(255, 229, 127, alpha))

    # ------------------------------------------------------------------

    def _start_refresh_timer(self):
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._load_and_plot)
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)

    def _fix_axis_after_show(self):
        bottom_axis = self.plot.getAxis("bottom")
        bottom_axis.setHeight(32)
        bottom_axis.update()
        self.plot.updateGeometry()
        self.price_plot.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.plot.update()
        self.price_plot.update()

    def _on_timeframe_changed(self, minutes: int):
        if self.timeframe_minutes == minutes:
            return

        self.timeframe_minutes = minutes

        for m, btn in self.tf_buttons.items():
            btn.setChecked(m == minutes)

        # Clear visuals
        self.prev_curve.clear()
        self.today_curve.clear()
        self.live_dot.clear()
        self.price_prev_curve.clear()
        self.price_today_curve.clear()
        self.price_live_dot.clear()
        self.cvd_ema10_curve.clear()
        self.cvd_ema21_curve.clear()
        self.cvd_ema51_curve.clear()
        self.price_ema10_curve.clear()
        self.price_ema21_curve.clear()
        self.price_ema51_curve.clear()
        self.all_timestamps.clear()

        self._load_and_plot()

    def _time_to_session_index(self, ts: datetime) -> int:
        """
        Converts a timestamp to a fixed session index (0–374)
        """
        session_start = ts.replace(
            hour=9, minute=15, second=0, microsecond=0
        )
        delta_minutes = int((ts - session_start).total_seconds() / 60)
        return max(0, min(delta_minutes, MINUTES_PER_SESSION - 1))

    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fix_axis_after_show)

    def closeEvent(self, event):
        if self.cvd_engine:
            self.cvd_engine.set_mode(CVDMode.NORMAL)
        if hasattr(self, "refresh_timer"):
            self.refresh_timer.stop()
        if hasattr(self, "dot_timer"):
            self.dot_timer.stop()
        super().closeEvent(event)