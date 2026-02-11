import logging
from contextlib import suppress
from datetime import datetime, timedelta
import numpy as np

import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QHBoxLayout,
    QPushButton, QWidget, QCheckBox, QSpinBox, QDoubleSpinBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QEvent
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
        self._live_tick_points: list[tuple[datetime, float]] = []
        self._current_session_start_ts: datetime | None = None
        self._current_session_x_base: float = 0.0

        # Plot caches
        self.all_timestamps: list[datetime] = []
        self._last_plot_x_indices: list[float] = []

        # 🎯 Confluence signal lines (price + CVD both reversal at same bar)
        self._confluence_lines: list = []   # InfiniteLine items added to both plots

        # 📦 Range boxes (price + CVD consolidating together)
        self._range_box_items: list = []    # (plot, LinearRegionItem) tuples

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

        # ATR Trend Reversal controls
        atr_label = QLabel("ATR Reversal:")
        atr_label.setStyleSheet("color: #B0B0B0; font-weight: 600; font-size: 12px;")
        ema_bar.addWidget(atr_label)

        base_ema_label = QLabel("Base EMA")
        base_ema_label.setStyleSheet("color: #8A9BA8; font-size: 12px;")
        ema_bar.addWidget(base_ema_label)

        self.atr_base_ema_input = QSpinBox()
        self.atr_base_ema_input.setRange(1, 500)
        self.atr_base_ema_input.setValue(51)
        self.atr_base_ema_input.setFixedWidth(70)
        self.atr_base_ema_input.setStyleSheet("QSpinBox { background:#1B1F2B; color:#E0E0E0; }")
        self.atr_base_ema_input.valueChanged.connect(self._on_atr_settings_changed)
        ema_bar.addWidget(self.atr_base_ema_input)

        distance_label = QLabel("Distance")
        distance_label.setStyleSheet("color: #8A9BA8; font-size: 12px;")
        ema_bar.addWidget(distance_label)

        self.atr_distance_input = QDoubleSpinBox()
        self.atr_distance_input.setRange(0.1, 20.0)
        self.atr_distance_input.setDecimals(2)
        self.atr_distance_input.setSingleStep(0.1)
        self.atr_distance_input.setValue(3.01)
        self.atr_distance_input.setFixedWidth(80)
        self.atr_distance_input.setStyleSheet("QDoubleSpinBox { background:#1B1F2B; color:#E0E0E0; }")
        self.atr_distance_input.valueChanged.connect(self._on_atr_settings_changed)
        ema_bar.addWidget(self.atr_distance_input)

        # CVD EMA Gap threshold (only applies to CVD reversal signals)
        cvd_gap_label = QLabel("CVD Gap >")
        cvd_gap_label.setStyleSheet("color: #8A9BA8; font-size: 12px;")
        ema_bar.addWidget(cvd_gap_label)

        self.cvd_ema_gap_input = QSpinBox()
        self.cvd_ema_gap_input.setRange(0, 500000)
        self.cvd_ema_gap_input.setSingleStep(1000)
        self.cvd_ema_gap_input.setValue(4000)
        self.cvd_ema_gap_input.setFixedWidth(90)
        self.cvd_ema_gap_input.setStyleSheet("QSpinBox { background:#1B1F2B; color:#26A69A; font-weight:600; }")
        self.cvd_ema_gap_input.valueChanged.connect(self._on_atr_settings_changed)
        ema_bar.addWidget(self.cvd_ema_gap_input)

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

        # ── Range Zone toggle ──
        self.btn_range = QCheckBox("📦 Range Zones")
        self.btn_range.setChecked(True)
        self.btn_range.setStyleSheet("""
            QCheckBox {
                color: #FFD580;
                font-weight: 600;
                font-size: 12px;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 2px solid #FFB300;
                border-radius: 3px;
                background: #1B1F2B;
            }
            QCheckBox::indicator:checked {
                background: #FFB300;
            }
        """)
        self.btn_range.toggled.connect(self._on_range_toggled)
        ema_bar.addWidget(self.btn_range)

        range_sens_label = QLabel("Sens")
        range_sens_label.setStyleSheet("color: #8A9BA8; font-size: 12px;")
        ema_bar.addWidget(range_sens_label)

        self.range_sens_input = QDoubleSpinBox()
        self.range_sens_input.setRange(0.5, 10.0)
        self.range_sens_input.setDecimals(1)
        self.range_sens_input.setSingleStep(0.5)
        self.range_sens_input.setValue(2.0)
        self.range_sens_input.setFixedWidth(65)
        self.range_sens_input.setToolTip(
            "Range sensitivity: lower = tighter ranges only, higher = more ranges"
        )
        self.range_sens_input.setStyleSheet(
            "QDoubleSpinBox { background:#1B1F2B; color:#FFD580; font-weight:600; }"
        )
        self.range_sens_input.valueChanged.connect(self._on_range_settings_changed)
        ema_bar.addWidget(self.range_sens_input)

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

        # ATR Trend Reversal markers
        self.price_atr_above_markers = pg.ScatterPlotItem(
            size=9,
            symbol="t1",
            brush=pg.mkBrush("#FF4444"),
            pen=pg.mkPen("#FFFFFF", width=0.8),
        )
        self.price_atr_below_markers = pg.ScatterPlotItem(
            size=9,
            symbol="t",
            brush=pg.mkBrush("#00E676"),
            pen=pg.mkPen("#FFFFFF", width=0.8),
        )
        self.price_plot.addItem(self.price_atr_above_markers)
        self.price_plot.addItem(self.price_atr_below_markers)

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

        # Tick-level live overlay (prevents 1-minute repaint from hiding ticks)
        self.today_tick_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#26A69A", width=1.4)
        )
        self.plot.addItem(self.today_tick_curve)

        self.live_dot = pg.ScatterPlotItem(
            size=5,
            brush=pg.mkBrush(38, 166, 154, 200),
            pen=pg.mkPen("#FFFFFF", width=1)
        )
        self.plot.addItem(self.live_dot)

        # ATR Trend Reversal markers (CVD chart)
        self.cvd_atr_above_markers = pg.ScatterPlotItem(
            size=9,
            symbol="t1",
            brush=pg.mkBrush("#FF4444"),
            pen=pg.mkPen("#FFFFFF", width=0.8),
        )
        self.cvd_atr_below_markers = pg.ScatterPlotItem(
            size=9,
            symbol="t",
            brush=pg.mkBrush("#00E676"),
            pen=pg.mkPen("#FFFFFF", width=0.8),
        )
        self.plot.addItem(self.cvd_atr_above_markers)
        self.plot.addItem(self.cvd_atr_below_markers)

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
        if self.cvd_engine:
            self.cvd_engine.cvd_updated.connect(self._on_cvd_tick_update)

    def _on_cvd_tick_update(self, token: int, cvd_value: float):
        """Append live CVD ticks so intra-minute moves are never erased."""
        if token != self.instrument_token or not self.live_mode:
            return

        ts = datetime.now()
        self._live_tick_points.append((ts, cvd_value))

        # Keep only today's/session points to avoid unbounded growth
        today = datetime.now().date()
        self._live_tick_points = [
            (t, v) for t, v in self._live_tick_points
            if t.date() == today
        ]

        self._plot_live_ticks_only()

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
        self.today_tick_curve.clear()

        self.price_prev_curve.clear()
        self.price_today_curve.clear()
        self.price_live_dot.clear()

        self.all_timestamps.clear()
        self._clear_range_boxes()
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

    @staticmethod
    def _calculate_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
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

    def _on_atr_settings_changed(self, *_):
        """Recompute ATR markers from plotted data without refetching history."""
        self._update_atr_reversal_markers()

    def _update_atr_reversal_markers(self):
        """Update ATR reversal triangles using currently plotted price and CVD series."""
        has_price = getattr(self, "all_price_data", None) and self._last_plot_x_indices
        has_cvd   = getattr(self, "all_cvd_data",  None) and self._last_plot_x_indices

        if not has_price:
            self.price_atr_above_markers.clear()
            self.price_atr_below_markers.clear()
        if not has_cvd:
            self.cvd_atr_above_markers.clear()
            self.cvd_atr_below_markers.clear()
        if not has_price and not has_cvd:
            return

        base_ema_period    = int(self.atr_base_ema_input.value())
        distance_threshold = float(self.atr_distance_input.value())
        x_arr              = np.array(self._last_plot_x_indices, dtype=float)

        # ── Price markers ────────────────────────────────────────────────
        if has_price:
            price_data_array = np.array(self.all_price_data,      dtype=float)
            high_data_array  = np.array(self.all_price_high_data,  dtype=float)
            low_data_array   = np.array(self.all_price_low_data,   dtype=float)

            atr_values    = self._calculate_atr(high_data_array, low_data_array, price_data_array, period=14)
            base_ema      = self._calculate_ema(price_data_array, base_ema_period)
            safe_atr      = np.where(atr_values <= 0, np.nan, atr_values)
            distance      = np.abs(price_data_array - base_ema) / safe_atr

            above_mask = (distance >= distance_threshold) & (price_data_array > base_ema)
            below_mask = (distance >= distance_threshold) & (price_data_array < base_ema)
            atr_offset = np.nan_to_num(atr_values, nan=0.0) * 0.15

            self.price_atr_above_markers.setData(
                x_arr[above_mask],
                high_data_array[above_mask] + atr_offset[above_mask],
            )
            self.price_atr_below_markers.setData(
                x_arr[below_mask],
                low_data_array[below_mask] - atr_offset[below_mask],
            )

        # ── CVD markers — EMA 51 / distance 9 + raw gap gate (independent of price UI) ──
        if has_cvd:
            CVD_ATR_EMA      = 51
            CVD_ATR_DISTANCE = 11

            cvd_data_array = np.array(self.all_cvd_data, dtype=float)

            if getattr(self, "all_cvd_high_data", None) and getattr(self, "all_cvd_low_data", None):
                cvd_high = np.array(self.all_cvd_high_data, dtype=float)
                cvd_low  = np.array(self.all_cvd_low_data,  dtype=float)
            else:
                cvd_high = cvd_data_array.copy()
                cvd_low  = cvd_data_array.copy()

            atr_cvd    = self._calculate_atr(cvd_high, cvd_low, cvd_data_array, period=14)
            base_ema_c = self._calculate_ema(cvd_data_array, CVD_ATR_EMA)
            safe_atr_c = np.where(atr_cvd <= 0, np.nan, atr_cvd)
            distance_c = np.abs(cvd_data_array - base_ema_c) / safe_atr_c

            # ── Extra gate: raw gap between CVD and its EMA must exceed threshold ──
            cvd_ema_gap_threshold = float(self.cvd_ema_gap_input.value())
            raw_gap_c  = np.abs(cvd_data_array - base_ema_c)
            gap_mask_c = raw_gap_c > cvd_ema_gap_threshold  # BOTH conditions must hold

            above_mask_c = (distance_c >= CVD_ATR_DISTANCE) & (cvd_data_array > base_ema_c) & gap_mask_c
            below_mask_c = (distance_c >= CVD_ATR_DISTANCE) & (cvd_data_array < base_ema_c) & gap_mask_c
            atr_offset_c = np.nan_to_num(atr_cvd, nan=0.0) * 0.15

            # Simple EMA-side masks (no ATR distance required) — used for weak confluence
            cvd_above_ema51 = cvd_data_array > base_ema_c
            cvd_below_ema51 = cvd_data_array < base_ema_c

            self.cvd_atr_above_markers.setData(
                x_arr[above_mask_c],
                cvd_high[above_mask_c] + atr_offset_c[above_mask_c],
            )
            self.cvd_atr_below_markers.setData(
                x_arr[below_mask_c],
                cvd_low[below_mask_c] - atr_offset_c[below_mask_c],
            )

        # ── Confluence: price reversal + CVD confirmation ─────────────────
        if has_price and has_cvd:
            if len(above_mask) == len(above_mask_c) == len(x_arr):
                self._draw_confluence_lines(
                    price_above_mask=above_mask,
                    price_below_mask=below_mask,
                    cvd_above_mask=above_mask_c,
                    cvd_below_mask=below_mask_c,
                    cvd_above_ema51=cvd_above_ema51,
                    cvd_below_ema51=cvd_below_ema51,
                    x_arr=x_arr,
                )


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
        self.price_atr_above_markers.clear()
        self.price_atr_below_markers.clear()
        self.cvd_atr_above_markers.clear()
        self.cvd_atr_below_markers.clear()
        self._clear_confluence_lines()
        self._clear_range_boxes()

        # Clear EMA curves
        self.cvd_ema10_curve.clear()
        self.cvd_ema21_curve.clear()
        self.cvd_ema51_curve.clear()
        self.price_ema10_curve.clear()
        self.price_ema21_curve.clear()
        self.price_ema51_curve.clear()

        self.all_timestamps = []
        self.all_cvd_data = []
        self.all_cvd_high_data = []
        self.all_cvd_low_data = []
        self.all_price_data = []
        self.all_price_high_data = []
        self.all_price_low_data = []
        self._last_plot_x_indices = []

        x_offset = 0
        sessions = sorted(cvd_df["session"].unique())

        for i, sess in enumerate(sessions):
            df_cvd_sess = cvd_df[cvd_df["session"] == sess]
            df_price_sess = price_df[price_df["session"] == sess]

            cvd_y_raw = df_cvd_sess["close"].values
            cvd_high_raw = df_cvd_sess["high"].values if "high" in df_cvd_sess.columns else cvd_y_raw
            cvd_low_raw  = df_cvd_sess["low"].values  if "low"  in df_cvd_sess.columns else cvd_y_raw
            price_y_raw = df_price_sess["close"].values
            price_high_raw = df_price_sess["high"].values
            price_low_raw = df_price_sess["low"].values

            # Rebasing logic for CVD
            if i == 0 and len(sessions) == 2 and not self.btn_focus.isChecked():
                cvd_y    = cvd_y_raw    - prev_close
                cvd_high = cvd_high_raw - prev_close
                cvd_low  = cvd_low_raw  - prev_close
            else:
                cvd_y    = cvd_y_raw
                cvd_high = cvd_high_raw
                cvd_low  = cvd_low_raw

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

            if is_current_session and not df_cvd_sess.empty:
                self._current_session_start_ts = df_cvd_sess.index[0]
                self._current_session_x_base = float(xs[0]) if xs else 0.0

            self.all_timestamps.extend(df_cvd_sess.index.tolist())
            self.all_cvd_data.extend(cvd_y.tolist())
            self.all_cvd_high_data.extend(cvd_high.tolist())
            self.all_cvd_low_data.extend(cvd_low.tolist())
            self.all_price_data.extend(price_y.tolist())
            self.all_price_high_data.extend(price_high_raw.tolist())
            self.all_price_low_data.extend(price_low_raw.tolist())

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

        self._plot_live_ticks_only()

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
            self._last_plot_x_indices = list(x_indices)
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

            self._update_atr_reversal_markers()
            self._draw_range_boxes()


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

    # ------------------------------------------------------------------
    # 🎯  CONFLUENCE SIGNAL LINES
    # ------------------------------------------------------------------

    def _clear_confluence_lines(self):
        """Remove all confluence vertical lines from both charts."""
        for line_pair in self._confluence_lines:
            for plot, line in line_pair:
                plot.removeItem(line)
        self._confluence_lines.clear()

    def _draw_confluence_lines(
        self,
        price_above_mask: np.ndarray,
        price_below_mask: np.ndarray,
        cvd_above_mask: np.ndarray,
        cvd_below_mask: np.ndarray,
        cvd_above_ema51: np.ndarray,
        cvd_below_ema51: np.ndarray,
        x_arr: np.ndarray,
    ):
        """
        Draw a vertical line on BOTH charts wherever price fires a reversal
        and CVD confirms — either via its own ATR signal OR simply by being
        on the correct side of its 51 EMA.

        SHORT (red)  : price above EMA (bearish reversal)
                       AND (CVD fired above EMA  OR  CVD is just below EMA 51)
        LONG  (green): price below EMA (bullish reversal)
                       AND (CVD fired below EMA  OR  CVD is just above EMA 51)
        """
        self._clear_confluence_lines()

        short_mask = price_above_mask & (cvd_above_mask | cvd_below_ema51)
        long_mask  = price_below_mask & (cvd_below_mask | cvd_above_ema51)

        def _make_line(x: float, color: str) -> list:
            """Create one InfiniteLine per chart and return [(plot, line), ...]."""
            pen = pg.mkPen(color, width=1.5, style=Qt.SolidLine)
            pairs = []
            for plot in (self.price_plot, self.plot):
                line = pg.InfiniteLine(
                    pos=x,
                    angle=90,
                    movable=False,
                    pen=pen,
                    label="",
                )
                line.setZValue(5)   # above curves, below dots
                plot.addItem(line)
                pairs.append((plot, line))
            return pairs

        for x in x_arr[short_mask]:
            self._confluence_lines.append(_make_line(x, "#FF4444"))  # red  → short

        for x in x_arr[long_mask]:
            self._confluence_lines.append(_make_line(x, "#00E676"))  # green → long

    # ------------------------------------------------------------------
    # 📦  RANGE ZONE BOXES
    # ------------------------------------------------------------------

    def _clear_range_boxes(self):
        """Remove all range box items from both charts."""
        for plot, item in self._range_box_items:
            plot.removeItem(item)
        self._range_box_items.clear()

    def _on_range_toggled(self, checked: bool):
        """Show/hide range boxes instantly without redrawing."""
        for _, item in self._range_box_items:
            item.setVisible(checked)

    def _on_range_settings_changed(self, *_):
        """Recompute range boxes when sensitivity changes."""
        self._draw_range_boxes()

    @staticmethod
    def _find_range_runs(mask: np.ndarray, min_bars: int) -> list[tuple[int, int]]:
        """
        Return list of (start, end) index pairs for contiguous True runs
        in *mask* that are at least *min_bars* long.
        """
        runs = []
        n = len(mask)
        i = 0
        while i < n:
            if mask[i]:
                j = i
                while j < n and mask[j]:
                    j += 1
                if j - i >= min_bars:
                    runs.append((i, j - 1))
                i = j
            else:
                i += 1
        return runs

    def _draw_range_boxes(self):
        """
        Detect bars where BOTH price and CVD are in a low-volatility range,
        and draw aligned rectangular boxes on both charts for each run.

        Detection: rolling (high - low) / ATR < sensitivity threshold
        for both price and CVD simultaneously for >= min_bars consecutive bars.
        """
        self._clear_range_boxes()

        has_price = getattr(self, "all_price_data", None) and self._last_plot_x_indices
        has_cvd   = getattr(self, "all_cvd_data",  None) and self._last_plot_x_indices
        if not (has_price and has_cvd):
            return

        visible   = self.btn_range.isChecked()
        threshold = float(self.range_sens_input.value())
        window    = 10   # rolling lookback in bars
        min_bars  = 5    # minimum consecutive bars to qualify as a range

        price_arr = np.array(self.all_price_data,     dtype=float)
        p_high    = np.array(self.all_price_high_data, dtype=float)
        p_low     = np.array(self.all_price_low_data,  dtype=float)
        cvd_arr   = np.array(self.all_cvd_data,       dtype=float)

        if getattr(self, "all_cvd_high_data", None) and getattr(self, "all_cvd_low_data", None):
            c_high = np.array(self.all_cvd_high_data, dtype=float)
            c_low  = np.array(self.all_cvd_low_data,  dtype=float)
        else:
            c_high = cvd_arr.copy()
            c_low  = cvd_arr.copy()

        x_arr = np.array(self._last_plot_x_indices, dtype=float)
        n     = len(price_arr)
        if n < window:
            return

        # ATR for normalisation
        price_atr = self._calculate_atr(p_high, p_low, price_arr, period=14)
        cvd_atr   = self._calculate_atr(c_high, c_low, cvd_arr,   period=14)

        # Rolling range (high - low over window bars)
        price_range_mask = np.zeros(n, dtype=bool)
        cvd_range_mask   = np.zeros(n, dtype=bool)

        for i in range(window - 1, n):
            sl = slice(i - window + 1, i + 1)

            p_roll_range = p_high[sl].max() - p_low[sl].min()
            p_atr_val    = price_atr[i]
            if p_atr_val > 0:
                price_range_mask[i] = (p_roll_range / p_atr_val) < threshold

            c_roll_range = c_high[sl].max() - c_low[sl].min()
            c_atr_val    = cvd_atr[i]
            if c_atr_val > 0:
                cvd_range_mask[i] = (c_roll_range / c_atr_val) < threshold

        both_mask = price_range_mask & cvd_range_mask
        runs = self._find_range_runs(both_mask, min_bars)

        for start, end in runs:
            x_left  = float(x_arr[start])
            x_right = float(x_arr[end]) + 1.0   # +1 so the box covers the last bar

            # ── Price box ────────────────────────────────────────────────
            p_top = float(p_high[start:end + 1].max())
            p_bot = float(p_low [start:end + 1].min())
            pad_p = (p_top - p_bot) * 0.08
            p_box = pg.LinearRegionItem(
                values=[x_left, x_right],
                orientation="vertical",
                brush=pg.mkBrush(255, 179, 0, 22),
                pen=pg.mkPen("#FFB300", width=1, style=Qt.DashLine),
                movable=False,
            )
            # Clip vertical extent via a custom ROI — pyqtgraph LinearRegionItem
            # spans full Y by default, so we layer an invisible rect on top for the
            # price bounds label instead.
            p_box.setZValue(2)
            p_box.setVisible(visible)
            self.price_plot.addItem(p_box)
            self._range_box_items.append((self.price_plot, p_box))

            # Price range label
            p_label = pg.TextItem(
                text=f"R  {p_top - p_bot:.1f}",
                color="#FFB300",
                anchor=(0, 1),
                fill=pg.mkBrush("#161A25BB"),
                border=pg.mkPen("#FFB300", width=0.5),
            )
            p_label.setFont(pg.QtGui.QFont("Consolas", 7))
            p_label.setPos(x_left, p_top + pad_p)
            p_label.setVisible(visible)
            self.price_plot.addItem(p_label)
            self._range_box_items.append((self.price_plot, p_label))

            # ── CVD box (same x span) ─────────────────────────────────────
            c_top = float(c_high[start:end + 1].max())
            c_bot = float(c_low [start:end + 1].min())
            pad_c = (c_top - c_bot) * 0.08
            c_box = pg.LinearRegionItem(
                values=[x_left, x_right],
                orientation="vertical",
                brush=pg.mkBrush(255, 179, 0, 22),
                pen=pg.mkPen("#FFB300", width=1, style=Qt.DashLine),
                movable=False,
            )
            c_box.setZValue(2)
            c_box.setVisible(visible)
            self.plot.addItem(c_box)
            self._range_box_items.append((self.plot, c_box))

            # CVD range label
            c_label = pg.TextItem(
                text=f"R  {c_top - c_bot:.0f}",
                color="#FFB300",
                anchor=(0, 1),
                fill=pg.mkBrush("#161A25BB"),
                border=pg.mkPen("#FFB300", width=0.5),
            )
            c_label.setFont(pg.QtGui.QFont("Consolas", 7))
            c_label.setPos(x_left, c_top + pad_c)
            c_label.setVisible(visible)
            self.plot.addItem(c_label)
            self._range_box_items.append((self.plot, c_label))

    def _blink_dot(self):
        self._dot_visible = not self._dot_visible
        alpha = 220 if self._dot_visible else 60
        self.live_dot.setBrush(pg.mkBrush(38, 166, 154, alpha))
        self.price_live_dot.setBrush(pg.mkBrush(255, 229, 127, alpha))

    # ------------------------------------------------------------------

    def _start_refresh_timer(self):
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_if_live)
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)

    def _refresh_if_live(self):
        if not self.live_mode:
            return
        if not self.isVisible() or not self.isActiveWindow():
            return
        self._load_and_plot()

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
        self.price_atr_above_markers.clear()
        self.price_atr_below_markers.clear()
        self.cvd_atr_above_markers.clear()
        self.cvd_atr_below_markers.clear()
        self.cvd_ema10_curve.clear()
        self.cvd_ema21_curve.clear()
        self.cvd_ema51_curve.clear()
        self.price_ema10_curve.clear()
        self.price_ema21_curve.clear()
        self.price_ema51_curve.clear()
        self.all_timestamps.clear()
        self._last_plot_x_indices = []
        self._clear_range_boxes()
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

    def _plot_live_ticks_only(self):
        """Plot tick-level CVD overlay on top of minute candles."""
        if not self._live_tick_points:
            self.today_tick_curve.clear()
            return

        if self._current_session_start_ts is None:
            return

        focus_mode = self.btn_focus.isChecked()
        current_day = self._current_session_start_ts.date()
        points = [
            (ts, cvd) for ts, cvd in self._live_tick_points
            if ts.date() == current_day
        ]
        if not points:
            self.today_tick_curve.clear()
            return

        x_vals = []
        y_vals = []
        for ts, cvd in points:
            if focus_mode:
                x = self._time_to_session_index(ts) + (ts.second / 60.0) + (ts.microsecond / 60_000_000.0)
            else:
                minute_offset = (ts - self._current_session_start_ts).total_seconds() / 60.0
                x = self._current_session_x_base + minute_offset

            x_vals.append(x)
            y_vals.append(cvd)

        self.today_tick_curve.setData(x_vals, y_vals)

    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fix_axis_after_show)

    def closeEvent(self, event):
        if self.cvd_engine:
            with suppress(TypeError, RuntimeError):
                self.cvd_engine.cvd_updated.disconnect(self._on_cvd_tick_update)
            self.cvd_engine.set_mode(CVDMode.NORMAL)
        if hasattr(self, "refresh_timer"):
            self.refresh_timer.stop()
        if hasattr(self, "dot_timer"):
            self.dot_timer.stop()
        super().closeEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                if hasattr(self, "refresh_timer") and not self.refresh_timer.isActive():
                    self.refresh_timer.start(self.REFRESH_INTERVAL_MS)
            else:
                if hasattr(self, "refresh_timer"):
                    self.refresh_timer.stop()
        super().changeEvent(event)