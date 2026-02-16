import logging
import re
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta
import numpy as np

import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QHBoxLayout,
    QPushButton, QWidget, QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox,
    QFormLayout, QGroupBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QObject, QThread, QSettings
from pyqtgraph import AxisItem, TextItem

from kiteconnect import KiteConnect
from core.cvd.cvd_historical import CVDHistoricalBuilder
from core.cvd.cvd_mode import CVDMode
from core.strategy_signal_detector import StrategySignalDetector

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
# Single CVD Chart Dialog (with navigator)
# =============================================================================

class CVDSingleChartDialog(QDialog):
    REFRESH_INTERVAL_MS = 3000
    LIVE_TICK_MAX_POINTS = 6000
    LIVE_TICK_REPAINT_MS = 80
    LIVE_TICK_DOWNSAMPLE_TARGET = 1500
    automation_signal = Signal(dict)
    automation_state_signal = Signal(dict)
    _cvd_tick_received = Signal(float, float)  # internal: marshal WebSocket thread → GUI thread

    SIGNAL_FILTER_ALL = "all"
    SIGNAL_FILTER_ATR_ONLY = "atr_only"
    SIGNAL_FILTER_EMA_CROSS_ONLY = "ema_cross_only"
    SIGNAL_FILTER_BREAKOUT_ONLY = "breakout_only"  # 🆕 NEW
    SIGNAL_FILTER_OTHERS = "others"

    ATR_MARKER_SHOW_ALL = "show_all"
    ATR_MARKER_CONFLUENCE_ONLY = "confluence_only"
    ATR_MARKER_GREEN_ONLY = "green_only"
    ATR_MARKER_RED_ONLY = "red_only"
    ATR_MARKER_HIDE_ALL = "hide_all"

    ROUTE_BUY_EXIT_PANEL = "buy_exit_panel"
    ROUTE_DIRECT = "direct"

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
        self._settings = QSettings("OptionsBadger", "AutoTrader")
        self.timeframe_minutes = 1  # default = 1 minute
        self.strategy_detector = StrategySignalDetector(timeframe_minutes=self.timeframe_minutes)

        self.live_mode = True
        self.current_date = None
        self.previous_date = None
        self._live_tick_points: deque[tuple[datetime, float]] = deque(maxlen=self.LIVE_TICK_MAX_POINTS)
        self._live_price_points: deque[tuple[datetime, float]] = deque(maxlen=self.LIVE_TICK_MAX_POINTS)
        self._current_session_start_ts: datetime | None = None
        self._current_session_x_base: float = 0.0
        self._live_cvd_offset: float | None = None
        self._current_session_last_cvd_value: float | None = None
        self._is_loading = False
        self._last_live_refresh_minute: datetime | None = None

        # Plot caches
        self.all_timestamps: list[datetime] = []
        self._last_plot_x_indices: list[float] = []

        # 🎯 Confluence signal lines (price + CVD both reversal at same bar)
        self._confluence_lines: list = []  # InfiniteLine items added to both plots
        self._last_emitted_signal_key: str | None = None
        self._last_emitted_closed_bar_ts: str | None = None

        self.setWindowTitle(f"Auto Trader — {self._display_symbol_for_title(symbol)}")
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
        self._load_persisted_setup_values()
        self._connect_signals()

        # Init in LIVE mode
        self.current_date, self.previous_date = self.navigator.get_dates()
        self._load_and_plot(force=True)
        self._start_refresh_timer()

    # ------------------------------------------------------------------

    @staticmethod
    def _display_symbol_for_title(symbol: str) -> str:
        """Hide FUT suffix/token from the window title while keeping internal symbol unchanged."""
        display_symbol = re.sub(r"[-_ ]?FUT$", "", symbol, flags=re.IGNORECASE)
        display_symbol = re.sub(r"\bFUT\b", "", display_symbol, flags=re.IGNORECASE)
        return re.sub(r"\s{2,}", " ", display_symbol).strip() or symbol

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(4)

        compact_spinbox_style = """
            QSpinBox, QDoubleSpinBox {
                background: #1B1F2B;
                color: #E0E0E0;
                font-weight: 600;
                font-size: 11px;
                border: 1px solid #3A4458;
                border-radius: 4px;
                padding: 2px 4px;
                min-height: 22px;
            }
            QSpinBox:hover, QDoubleSpinBox:hover {
                border: 1px solid #5B9BD5;
            }
        """

        compact_toggle_style = """
            QCheckBox {
                color: #9CCAF4;
                font-weight: 600;
                font-size: 11px;
                spacing: 4px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #5B9BD5;
                border-radius: 3px;
                background: #1B1F2B;
            }
            QCheckBox::indicator:checked {
                background: #5B9BD5;
            }
        """

        compact_combo_style = """
            QComboBox {
                background: #1B1F2B;
                color: #E0E0E0;
                font-weight: 600;
                font-size: 11px;
                padding: 2px 8px;
                border: 1px solid #3A4458;
                border-radius: 4px;
                min-height: 22px;
            }
            QComboBox:hover {
                border: 1px solid #5B9BD5;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #8A9BA8;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background: #1B1F2B;
                color: #E0E0E0;
                selection-background-color: #5B9BD5;
                selection-color: #000;
                border: 1px solid #3A4458;
            }
        """

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

        # Focus Button (placed on second row, right side)
        self.btn_focus = QPushButton("Single Day View")
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

        # Automate Toggle (next to Single Day View)
        self.automate_toggle = QCheckBox("Automate")
        self.automate_toggle.setChecked(False)
        self.automate_toggle.setStyleSheet(compact_toggle_style)
        self.automate_toggle.toggled.connect(self._on_automation_settings_changed)

        self.automation_stoploss_input = QSpinBox()
        self.automation_stoploss_input.setRange(1, 1000)
        self.automation_stoploss_input.setValue(50)
        self.automation_stoploss_input.setSingleStep(5)
        self.automation_stoploss_input.setFixedWidth(96)
        self.automation_stoploss_input.setStyleSheet(compact_spinbox_style)
        self.automation_stoploss_input.valueChanged.connect(self._on_automation_settings_changed)

        self.automation_route_combo = QComboBox()
        self.automation_route_combo.setFixedWidth(180)
        self.automation_route_combo.setStyleSheet(compact_combo_style)
        self.automation_route_combo.addItem("Buy Exit Panel", self.ROUTE_BUY_EXIT_PANEL)
        self.automation_route_combo.addItem("Direct", self.ROUTE_DIRECT)
        self.automation_route_combo.setCurrentIndex(0)
        self.automation_route_combo.currentIndexChanged.connect(self._on_automation_settings_changed)

        top_bar.addWidget(self.automate_toggle)

        self.setup_btn = QPushButton("Setup")
        self.setup_btn.setFixedHeight(28)
        self.setup_btn.setMinimumWidth(88)
        self.setup_btn.setToolTip("Open automation and signal settings")
        self.setup_btn.setStyleSheet("""
            QPushButton {
                background:#212635;
                border:1px solid #3A4458;
                border-radius:4px;
                padding:4px 10px;
                color: #9CCAF4;
                font-weight: 600;
            }
            QPushButton:hover {
                border: 1px solid #5B9BD5;
            }
            QPushButton:pressed {
                background: #1B1F2B;
            }
        """)
        self.setup_btn.clicked.connect(self._open_setup_dialog)
        top_bar.addWidget(self.setup_btn)

        # Export button (compact)
        self.btn_export = QPushButton("📸")
        self.btn_export.setFixedSize(28, 28)
        self.btn_export.setToolTip("Export current view as image")
        self.btn_export.setStyleSheet("""
            QPushButton {
                background: #212635;
                border: 1px solid #3A4458;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background: #2A3142;
                border: 1px solid #5B9BD5;
            }
            QPushButton:pressed {
                background: #1B1F2B;
            }
        """)
        self.btn_export.clicked.connect(self._export_chart_image)

        top_bar.addStretch()

        root.addLayout(top_bar)

        # ================= EMA CONTROL BAR (NEW) =================
        ema_bar = QHBoxLayout()
        ema_bar.setContentsMargins(0, 0, 0, 4)
        ema_bar.setSpacing(8)

        ema_bar.addStretch()

        self.atr_base_ema_input = QSpinBox()
        self.atr_base_ema_input.setRange(1, 500)
        self.atr_base_ema_input.setValue(51)
        self.atr_base_ema_input.setFixedWidth(96)
        self.atr_base_ema_input.setStyleSheet(compact_spinbox_style)
        self.atr_base_ema_input.valueChanged.connect(self._on_atr_settings_changed)

        self.atr_distance_input = QDoubleSpinBox()
        self.atr_distance_input.setRange(0.1, 20.0)
        self.atr_distance_input.setDecimals(2)
        self.atr_distance_input.setSingleStep(0.1)
        self.atr_distance_input.setValue(3.01)
        self.atr_distance_input.setFixedWidth(96)
        self.atr_distance_input.setStyleSheet(compact_spinbox_style)
        self.atr_distance_input.valueChanged.connect(self._on_atr_settings_changed)

        self.cvd_ema_gap_input = QSpinBox()
        self.cvd_ema_gap_input.setRange(0, 500000)
        self.cvd_ema_gap_input.setSingleStep(1000)
        self.cvd_ema_gap_input.setValue(3000)
        self.cvd_ema_gap_input.setFixedWidth(120)
        self.cvd_ema_gap_input.setStyleSheet(compact_spinbox_style)
        self.cvd_ema_gap_input.setToolTip(
            "Minimum distance between CVD and its EMA to confirm signal validity.\nFilters out price-hugging conditions where CVD trends weakly.")
        self.cvd_ema_gap_input.valueChanged.connect(self._on_atr_settings_changed)

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
                    font-size: 11px;
                    spacing: 3px;
                }}
                QCheckBox::indicator {{
                    width: 14px;
                    height: 14px;
                    border: 1px solid {color};
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

        ema_bar.addSpacing(4)

        signal_filter_label = QLabel("Filter")
        signal_filter_label.setStyleSheet("color: #8A9BA8; font-size: 11px;")
        ema_bar.addWidget(signal_filter_label)

        self.signal_filter_combo = QComboBox()
        self.signal_filter_combo.setFixedWidth(160)
        self.signal_filter_combo.setStyleSheet(compact_combo_style)
        self.signal_filter_combo.addItem("All Signals", self.SIGNAL_FILTER_ALL)
        self.signal_filter_combo.addItem("ATR Reversal Only", self.SIGNAL_FILTER_ATR_ONLY)
        self.signal_filter_combo.addItem("EMA Cross Only", self.SIGNAL_FILTER_EMA_CROSS_ONLY)
        self.signal_filter_combo.addItem("Range Breakout Only", self.SIGNAL_FILTER_BREAKOUT_ONLY)  # 🆕 NEW
        self.signal_filter_combo.addItem("ATR Divergence", self.SIGNAL_FILTER_OTHERS)
        self.signal_filter_combo.currentIndexChanged.connect(self._on_signal_filter_changed)
        ema_bar.addWidget(self.signal_filter_combo)

        atr_marker_label = QLabel("ATR Markers")
        atr_marker_label.setStyleSheet("color: #8A9BA8; font-size: 11px;")
        ema_bar.addWidget(atr_marker_label)

        self.atr_marker_filter_combo = QComboBox()
        self.atr_marker_filter_combo.setFixedWidth(140)
        self.atr_marker_filter_combo.setStyleSheet(compact_combo_style)
        self.atr_marker_filter_combo.addItem("Show All", self.ATR_MARKER_SHOW_ALL)
        self.atr_marker_filter_combo.addItem("Confluence Only", self.ATR_MARKER_CONFLUENCE_ONLY)
        self.atr_marker_filter_combo.addItem("Green Only", self.ATR_MARKER_GREEN_ONLY)
        self.atr_marker_filter_combo.addItem("Red Only", self.ATR_MARKER_RED_ONLY)
        self.atr_marker_filter_combo.addItem("Hide All", self.ATR_MARKER_HIDE_ALL)
        self.atr_marker_filter_combo.setCurrentIndex(1)
        self.atr_marker_filter_combo.currentIndexChanged.connect(self._on_atr_marker_filter_changed)
        ema_bar.addWidget(self.atr_marker_filter_combo)

        self._build_setup_dialog(compact_combo_style)

        ema_bar.addWidget(self.btn_focus)
        ema_bar.addWidget(self.btn_export)
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

        self.price_today_tick_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#FFE57F", width=1.4)
        )
        self.price_plot.addItem(self.price_today_tick_curve)

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
            symbol="t",
            brush=pg.mkBrush("#FF4444"),
            pen=pg.mkPen("#FFFFFF", width=0.8),
        )
        self.price_atr_below_markers = pg.ScatterPlotItem(
            size=9,
            symbol="t1",
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

        # EMA legends intentionally disabled to avoid overlap with chart lines.
        self.price_legend = None

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
            symbol="t",
            brush=pg.mkBrush("#FF4444"),
            pen=pg.mkPen("#FFFFFF", width=0.8),
        )
        self.cvd_atr_below_markers = pg.ScatterPlotItem(
            size=9,
            symbol="t1",
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

        # EMA legends intentionally disabled to avoid overlap with chart lines.
        self.cvd_legend = None

        # Connect mouse events
        self.price_plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # Link X-axis ranges
        self.price_plot.setXLink(self.plot)

        self.dot_timer = QTimer(self)
        self.dot_timer.timeout.connect(self._blink_dot)
        self.dot_timer.start(500)
        self._dot_visible = True

        # Batch high-frequency tick updates to keep UI smooth
        self._tick_repaint_timer = QTimer(self)
        self._tick_repaint_timer.setSingleShot(True)
        self._tick_repaint_timer.timeout.connect(self._plot_live_ticks_only)

    # ------------------------------------------------------------------

    def _connect_signals(self):
        self.navigator.date_changed.connect(self._on_date_changed)
        # Internal: marshal WebSocket thread ticks to the GUI thread safely.
        self._cvd_tick_received.connect(self._apply_cvd_tick, Qt.QueuedConnection)
        # In CVDSingleChartDialog._connect_signals
        if self.cvd_engine:
            self.cvd_engine.cvd_updated.connect(
                self._on_cvd_tick_update,
                Qt.QueuedConnection
            )

    def _build_setup_dialog(self, compact_combo_style: str):
        self.setup_dialog = QDialog(self)
        self.setup_dialog.setWindowTitle("Auto Trader Setup")
        self.setup_dialog.setModal(False)
        self.setup_dialog.setMinimumWidth(420)
        self.setup_dialog.setStyleSheet("""
            QDialog {
                background: #161A25;
                color: #E0E0E0;
            }
            QGroupBox {
                border: 1px solid #3A4458;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 10px;
                font-weight: 600;
                color: #9CCAF4;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
            QLabel {
                color: #B0B0B0;
                font-size: 11px;
                font-weight: 600;
            }
        """)

        layout = QVBoxLayout(self.setup_dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        auto_group = QGroupBox("Automation")
        auto_form = QFormLayout(auto_group)
        auto_form.setLabelAlignment(Qt.AlignLeft)
        auto_form.addRow("Stop Loss", self.automation_stoploss_input)
        auto_form.addRow("Route", self.automation_route_combo)
        layout.addWidget(auto_group)

        signal_group = QGroupBox("ATR / Signal")
        signal_form = QFormLayout(signal_group)
        signal_form.setLabelAlignment(Qt.AlignLeft)
        signal_form.addRow("ATR Base EMA", self.atr_base_ema_input)
        signal_form.addRow("ATR Distance", self.atr_distance_input)
        signal_form.addRow("CVD EMA Gap", self.cvd_ema_gap_input)

        self.setup_signal_filter_combo = QComboBox()
        self.setup_signal_filter_combo.setFixedWidth(220)
        self.setup_signal_filter_combo.setStyleSheet(compact_combo_style)
        self.setup_signal_filter_combo.addItem("All Signals", self.SIGNAL_FILTER_ALL)
        self.setup_signal_filter_combo.addItem("ATR Reversal Only", self.SIGNAL_FILTER_ATR_ONLY)
        self.setup_signal_filter_combo.addItem("EMA Cross Only", self.SIGNAL_FILTER_EMA_CROSS_ONLY)
        self.setup_signal_filter_combo.addItem("Range Breakout Only", self.SIGNAL_FILTER_BREAKOUT_ONLY)  # 🆕 NEW
        self.setup_signal_filter_combo.addItem("ATR Divergence", self.SIGNAL_FILTER_OTHERS)
        self.setup_signal_filter_combo.setCurrentIndex(self.signal_filter_combo.currentIndex())
        self.setup_signal_filter_combo.currentIndexChanged.connect(self._on_setup_signal_filter_changed)
        signal_form.addRow("Signal Filter", self.setup_signal_filter_combo)

        self.setup_atr_marker_filter_combo = QComboBox()
        self.setup_atr_marker_filter_combo.setFixedWidth(220)
        self.setup_atr_marker_filter_combo.setStyleSheet(compact_combo_style)
        self.setup_atr_marker_filter_combo.addItem("Show All", self.ATR_MARKER_SHOW_ALL)
        self.setup_atr_marker_filter_combo.addItem("Confluence Only", self.ATR_MARKER_CONFLUENCE_ONLY)
        self.setup_atr_marker_filter_combo.addItem("Green Only", self.ATR_MARKER_GREEN_ONLY)
        self.setup_atr_marker_filter_combo.addItem("Red Only", self.ATR_MARKER_RED_ONLY)
        self.setup_atr_marker_filter_combo.addItem("Hide All", self.ATR_MARKER_HIDE_ALL)
        self.setup_atr_marker_filter_combo.setCurrentIndex(self.atr_marker_filter_combo.currentIndex())
        self.setup_atr_marker_filter_combo.currentIndexChanged.connect(self._on_setup_atr_marker_filter_changed)
        signal_form.addRow("ATR Markers", self.setup_atr_marker_filter_combo)
        layout.addWidget(signal_group)

        # 🆕 Range Breakout Settings
        breakout_group = QGroupBox("Range Breakout")
        breakout_form = QFormLayout(breakout_group)
        breakout_form.setLabelAlignment(Qt.AlignLeft)

        self.range_lookback_input = QSpinBox()
        self.range_lookback_input.setRange(10, 120)
        self.range_lookback_input.setValue(30)
        self.range_lookback_input.setSuffix(" min")
        self.range_lookback_input.setStyleSheet("""
            QSpinBox {
                background: #1B1F2B;
                color: #E0E0E0;
                font-weight: 600;
                font-size: 11px;
                border: 1px solid #3A4458;
                border-radius: 4px;
                padding: 2px 4px;
                min-height: 22px;
            }
            QSpinBox:hover {
                border: 1px solid #5B9BD5;
            }
        """)
        self.range_lookback_input.setToolTip(
            "Period to analyze for consolidation range detection.\n"
            "Breakout signals trigger when price breaks above/below this range."
        )
        self.range_lookback_input.valueChanged.connect(self._on_breakout_settings_changed)
        breakout_form.addRow("Range Lookback", self.range_lookback_input)
        layout.addWidget(breakout_group)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.setup_dialog.hide)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def _open_setup_dialog(self):
        self.setup_dialog.show()
        self.setup_dialog.raise_()
        self.setup_dialog.activateWindow()

    def _settings_key_prefix(self) -> str:
        return f"chart_setup/{self.instrument_token}"

    def _load_persisted_setup_values(self):
        key_prefix = self._settings_key_prefix()

        def _apply_combo_value(combo: QComboBox, data_value, fallback_index: int = 0):
            idx = combo.findData(data_value)
            combo.setCurrentIndex(idx if idx >= 0 else fallback_index)

        self.automate_toggle.blockSignals(True)
        self.automation_stoploss_input.blockSignals(True)
        self.automation_route_combo.blockSignals(True)
        self.atr_base_ema_input.blockSignals(True)
        self.atr_distance_input.blockSignals(True)
        self.cvd_ema_gap_input.blockSignals(True)
        self.signal_filter_combo.blockSignals(True)
        self.atr_marker_filter_combo.blockSignals(True)
        self.setup_signal_filter_combo.blockSignals(True)
        self.setup_atr_marker_filter_combo.blockSignals(True)
        self.range_lookback_input.blockSignals(True)  # 🆕 NEW

        self.automate_toggle.setChecked(
            self._settings.value(f"{key_prefix}/enabled", self.automate_toggle.isChecked(), type=bool)
        )
        self.automation_stoploss_input.setValue(
            self._settings.value(
                f"{key_prefix}/stoploss_points",
                self.automation_stoploss_input.value(),
                type=int,
            )
        )
        _apply_combo_value(
            self.automation_route_combo,
            self._settings.value(
                f"{key_prefix}/route",
                self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            ),
            fallback_index=0,
        )

        self.atr_base_ema_input.setValue(
            self._settings.value(
                f"{key_prefix}/atr_base_ema",
                self.atr_base_ema_input.value(),
                type=int,
            )
        )
        self.atr_distance_input.setValue(
            self._settings.value(
                f"{key_prefix}/atr_distance",
                self.atr_distance_input.value(),
                type=float,
            )
        )
        self.cvd_ema_gap_input.setValue(
            self._settings.value(
                f"{key_prefix}/cvd_ema_gap",
                self.cvd_ema_gap_input.value(),
                type=int,
            )
        )

        signal_filter_value = self._settings.value(
            f"{key_prefix}/signal_filter",
            self.signal_filter_combo.currentData() or self.SIGNAL_FILTER_ALL,
        )
        _apply_combo_value(self.signal_filter_combo, signal_filter_value, fallback_index=0)
        _apply_combo_value(self.setup_signal_filter_combo, signal_filter_value, fallback_index=0)

        marker_filter_value = self._settings.value(
            f"{key_prefix}/atr_marker_filter",
            self.atr_marker_filter_combo.currentData() or self.ATR_MARKER_CONFLUENCE_ONLY,
        )
        _apply_combo_value(self.atr_marker_filter_combo, marker_filter_value, fallback_index=1)
        _apply_combo_value(self.setup_atr_marker_filter_combo, marker_filter_value, fallback_index=1)

        # 🆕 Load range breakout settings
        self.range_lookback_input.setValue(
            self._settings.value(
                f"{key_prefix}/range_lookback",
                self.range_lookback_input.value(),
                type=int,
            )
        )

        self.automate_toggle.blockSignals(False)
        self.automation_stoploss_input.blockSignals(False)
        self.automation_route_combo.blockSignals(False)
        self.atr_base_ema_input.blockSignals(False)
        self.atr_distance_input.blockSignals(False)
        self.cvd_ema_gap_input.blockSignals(False)
        self.signal_filter_combo.blockSignals(False)
        self.atr_marker_filter_combo.blockSignals(False)
        self.setup_signal_filter_combo.blockSignals(False)
        self.setup_atr_marker_filter_combo.blockSignals(False)
        self.range_lookback_input.blockSignals(False)  # 🆕 NEW

        self._update_atr_reversal_markers()
        self._on_automation_settings_changed()

    def _persist_setup_values(self):
        key_prefix = self._settings_key_prefix()
        self._settings.setValue(f"{key_prefix}/enabled", self.automate_toggle.isChecked())
        self._settings.setValue(f"{key_prefix}/stoploss_points", int(self.automation_stoploss_input.value()))
        self._settings.setValue(
            f"{key_prefix}/route",
            self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
        )
        self._settings.setValue(f"{key_prefix}/atr_base_ema", int(self.atr_base_ema_input.value()))
        self._settings.setValue(f"{key_prefix}/atr_distance", float(self.atr_distance_input.value()))
        self._settings.setValue(f"{key_prefix}/cvd_ema_gap", int(self.cvd_ema_gap_input.value()))
        self._settings.setValue(f"{key_prefix}/signal_filter", self._selected_signal_filter())
        self._settings.setValue(
            f"{key_prefix}/atr_marker_filter",
            self.atr_marker_filter_combo.currentData() or self.ATR_MARKER_CONFLUENCE_ONLY,
        )
        # 🆕 Persist range breakout settings
        self._settings.setValue(f"{key_prefix}/range_lookback", int(self.range_lookback_input.value()))
        self._settings.sync()

    def _on_automation_settings_changed(self, *_):
        self._persist_setup_values()
        self.automation_state_signal.emit({
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "enabled": self.automate_toggle.isChecked(),
            "stoploss_points": float(self.automation_stoploss_input.value()),
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "signal_filter": self._selected_signal_filter(),
        })

    def _on_signal_filter_changed(self, *_):
        if hasattr(self, "setup_signal_filter_combo"):
            self.setup_signal_filter_combo.blockSignals(True)
            self.setup_signal_filter_combo.setCurrentIndex(self.signal_filter_combo.currentIndex())
            self.setup_signal_filter_combo.blockSignals(False)
        self._update_atr_reversal_markers()
        self._on_automation_settings_changed()

    def _on_setup_signal_filter_changed(self, *_):
        self.signal_filter_combo.blockSignals(True)
        self.signal_filter_combo.setCurrentIndex(self.setup_signal_filter_combo.currentIndex())
        self.signal_filter_combo.blockSignals(False)
        self._update_atr_reversal_markers()
        self._on_automation_settings_changed()

    def _on_atr_marker_filter_changed(self, *_):
        """Handle ATR marker display filter changes"""
        if hasattr(self, "setup_atr_marker_filter_combo"):
            self.setup_atr_marker_filter_combo.blockSignals(True)
            self.setup_atr_marker_filter_combo.setCurrentIndex(self.atr_marker_filter_combo.currentIndex())
            self.setup_atr_marker_filter_combo.blockSignals(False)
        self._update_atr_reversal_markers()
        self._persist_setup_values()

    def _on_setup_atr_marker_filter_changed(self, *_):
        self.atr_marker_filter_combo.blockSignals(True)
        self.atr_marker_filter_combo.setCurrentIndex(self.setup_atr_marker_filter_combo.currentIndex())
        self.atr_marker_filter_combo.blockSignals(False)
        self._update_atr_reversal_markers()
        self._persist_setup_values()

    def _on_breakout_settings_changed(self, *_):
        """Handle range breakout settings changes"""
        self._persist_setup_values()
        # Force reload to apply new range lookback
        self._load_and_plot(force=True)

    def _selected_signal_filter(self) -> str:
        return self.signal_filter_combo.currentData() or self.SIGNAL_FILTER_ALL

    def _on_cvd_tick_update(self, token: int, cvd_value: float, last_price: float):
        if not self.isVisible():
            return

        if token != self.instrument_token or not self.live_mode:
            return

        self._cvd_tick_received.emit(cvd_value, last_price)

    def _apply_cvd_tick(self, cvd_value: float, last_price: float):
        """Slot — always called on the GUI thread via queued signal connection."""
        ts = datetime.now()

        # Align live tick CVD level with historical curve to avoid visual jump.
        if self._live_cvd_offset is None:
            if self._current_session_last_cvd_value is not None:
                self._live_cvd_offset = float(self._current_session_last_cvd_value) - float(cvd_value)
            else:
                self._live_cvd_offset = 0.0

        plotted_cvd = float(cvd_value) + float(self._live_cvd_offset)
        current_price = float(last_price)

        # 🔥 SMART TICK FILTERING - Only append if price/CVD changed meaningfully
        # This prevents wick-like artifacts from back-and-forth movements
        should_append = True

        if self._live_tick_points:
            # Get last recorded values
            last_ts, last_cvd = self._live_tick_points[-1]
            last_price_val = self._live_price_points[-1][1] if self._live_price_points else current_price

            # Only append if:
            # 1. Price changed (to avoid redundant points at same price level)
            # 2. OR it's been more than 1 second (to ensure minimum sampling)
            time_since_last = (ts - last_ts).total_seconds()
            price_changed = abs(current_price - last_price_val) > 0.01
            cvd_changed = abs(plotted_cvd - last_cvd) > 1.0

            # Append only if there's actual movement or time gap
            should_append = price_changed or cvd_changed or time_since_last > 1.0

        if should_append:
            self._live_tick_points.append((ts, plotted_cvd))
            self._live_price_points.append((ts, current_price))
        else:
            # Update the last point in place (no new point, just update current value)
            # This creates a "moving dot" effect rather than drawing lines
            if self._live_tick_points:
                self._live_tick_points[-1] = (ts, plotted_cvd)
            if self._live_price_points:
                self._live_price_points[-1] = (ts, current_price)

        # Trim stale points from previous sessions.
        today = ts.date()
        while self._live_tick_points and self._live_tick_points[0][0].date() < today:
            self._live_tick_points.popleft()
        while self._live_price_points and self._live_price_points[0][0].date() < today:
            self._live_price_points.popleft()

        if not self._tick_repaint_timer.isActive():
            self._tick_repaint_timer.start(self.LIVE_TICK_REPAINT_MS)

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
        """EMA legends are disabled to keep chart area unobstructed."""
        return

    def _on_focus_mode_changed(self, enabled: bool):
        self.btn_focus.setText("Single Day View" if enabled else "Two Day View")
        if self.cvd_engine:
            if enabled:
                self.cvd_engine.set_mode(CVDMode.SINGLE_DAY)
            else:
                self.cvd_engine.set_mode(CVDMode.NORMAL)

        # Clear visual state
        self.prev_curve.clear()
        self.today_curve.clear()
        self.live_dot.clear()
        self.today_tick_curve.clear()
        self.price_today_tick_curve.clear()

        self.price_prev_curve.clear()
        self.price_today_curve.clear()
        self.price_live_dot.clear()

        self._live_tick_points.clear()
        self._live_price_points.clear()
        self._live_cvd_offset = None
        self._current_session_last_cvd_value = None
        self.all_timestamps.clear()
        self._load_and_plot(force=True)

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

        self._load_and_plot(force=True)

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
        self._persist_setup_values()

    def _update_atr_reversal_markers(self):
        """Update ATR reversal triangles using currently plotted price and CVD series."""
        has_price = getattr(self, "all_price_data", None) and self._last_plot_x_indices
        has_cvd = getattr(self, "all_cvd_data", None) and self._last_plot_x_indices

        if not has_price:
            self.price_atr_above_markers.clear()
            self.price_atr_below_markers.clear()
        if not has_cvd:
            self.cvd_atr_above_markers.clear()
            self.cvd_atr_below_markers.clear()
        if not has_price and not has_cvd:
            return

        base_ema_period = int(self.atr_base_ema_input.value())
        distance_threshold = float(self.atr_distance_input.value())
        x_arr = np.array(self._last_plot_x_indices, dtype=float)

        # ── Price markers ────────────────────────────────────────────────
        if has_price:
            price_data_array = np.array(self.all_price_data, dtype=float)
            high_data_array = np.array(self.all_price_high_data, dtype=float)
            low_data_array = np.array(self.all_price_low_data, dtype=float)

            atr_values = self._calculate_atr(high_data_array, low_data_array, price_data_array, period=14)
            base_ema = self._calculate_ema(price_data_array, base_ema_period)
            safe_atr = np.where(atr_values <= 0, np.nan, atr_values)
            distance = np.abs(price_data_array - base_ema) / safe_atr

            above_mask = (distance >= distance_threshold) & (price_data_array > base_ema)
            below_mask = (distance >= distance_threshold) & (price_data_array < base_ema)
            atr_offset = np.nan_to_num(atr_values, nan=0.0) * 0.15

            price_prev = np.concatenate(([price_data_array[0]], price_data_array[:-1]))
            ema_prev = np.concatenate(([base_ema[0]], base_ema[:-1]))
            price_cross_above_ema = (price_prev <= ema_prev) & (price_data_array > base_ema)
            price_cross_below_ema = (price_prev >= ema_prev) & (price_data_array < base_ema)

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
            CVD_ATR_EMA = 51
            CVD_ATR_DISTANCE = 11

            cvd_data_array = np.array(self.all_cvd_data, dtype=float)

            if getattr(self, "all_cvd_high_data", None) and getattr(self, "all_cvd_low_data", None):
                cvd_high = np.array(self.all_cvd_high_data, dtype=float)
                cvd_low = np.array(self.all_cvd_low_data, dtype=float)
            else:
                cvd_high = cvd_data_array.copy()
                cvd_low = cvd_data_array.copy()

            atr_cvd = self._calculate_atr(cvd_high, cvd_low, cvd_data_array, period=14)
            base_ema_c = self._calculate_ema(cvd_data_array, CVD_ATR_EMA)
            safe_atr_c = np.where(atr_cvd <= 0, np.nan, atr_cvd)
            distance_c = np.abs(cvd_data_array - base_ema_c) / safe_atr_c

            # ── Extra gate: raw gap between CVD and its EMA must exceed threshold ──
            cvd_ema_gap_threshold = float(self.cvd_ema_gap_input.value())
            raw_gap_c = np.abs(cvd_data_array - base_ema_c)
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

        # ── Apply ATR Marker Display Filter ──────────────────────────────
        marker_filter = self.atr_marker_filter_combo.currentData()

        if marker_filter == self.ATR_MARKER_HIDE_ALL:
            # Hide all markers
            self.price_atr_above_markers.clear()
            self.price_atr_below_markers.clear()
            self.cvd_atr_above_markers.clear()
            self.cvd_atr_below_markers.clear()
        elif marker_filter == self.ATR_MARKER_GREEN_ONLY:
            # Show only green (below) markers
            self.price_atr_above_markers.clear()
            self.cvd_atr_above_markers.clear()
        elif marker_filter == self.ATR_MARKER_RED_ONLY:
            # Show only red (above) markers
            self.price_atr_below_markers.clear()
            self.cvd_atr_below_markers.clear()
        elif marker_filter == self.ATR_MARKER_CONFLUENCE_ONLY and has_price and has_cvd:
            # Show only markers where both price and CVD have signals at same bar
            if len(above_mask) == len(above_mask_c) == len(x_arr):
                confluence_above = above_mask & above_mask_c
                confluence_below = below_mask & below_mask_c

                if has_price:
                    atr_offset = np.nan_to_num(atr_values, nan=0.0) * 0.15
                    self.price_atr_above_markers.setData(
                        x_arr[confluence_above],
                        high_data_array[confluence_above] + atr_offset[confluence_above],
                    )
                    self.price_atr_below_markers.setData(
                        x_arr[confluence_below],
                        low_data_array[confluence_below] - atr_offset[confluence_below],
                    )

                if has_cvd:
                    atr_offset_c = np.nan_to_num(atr_cvd, nan=0.0) * 0.15
                    self.cvd_atr_above_markers.setData(
                        x_arr[confluence_above],
                        cvd_high[confluence_above] + atr_offset_c[confluence_above],
                    )
                    self.cvd_atr_below_markers.setData(
                        x_arr[confluence_below],
                        cvd_low[confluence_below] - atr_offset_c[confluence_below],
                    )
        # else: ATR_MARKER_SHOW_ALL - markers already set above, do nothing

        # ── Confluence: price reversal + CVD confirmation ─────────────────
        if has_price and has_cvd:
            if len(above_mask) == len(above_mask_c) == len(x_arr):
                self._draw_confluence_lines(
                    price_above_mask=above_mask,
                    price_below_mask=below_mask,
                    price_cross_above_ema=price_cross_above_ema,
                    price_cross_below_ema=price_cross_below_ema,
                    cvd_above_mask=above_mask_c,
                    cvd_below_mask=below_mask_c,
                    cvd_above_ema51=cvd_above_ema51,
                    cvd_below_ema51=cvd_below_ema51,
                    x_arr=x_arr,
                )

        self._emit_automation_market_state()

    def _emit_automation_market_state(self):
        if not self._last_plot_x_indices or not self.all_price_data or not self.all_cvd_data:
            return

        x_arr = np.array(self._last_plot_x_indices, dtype=float)
        price_data_array = np.array(self.all_price_data, dtype=float)
        cvd_data_array = np.array(self.all_cvd_data, dtype=float)
        ema10 = self._calculate_ema(price_data_array, 10)
        ema51 = self._calculate_ema(price_data_array, 51)
        cvd_ema10 = self._calculate_ema(cvd_data_array, 10)
        cvd_ema51 = self._calculate_ema(cvd_data_array, 51)
        idx = self._latest_closed_bar_index()
        if idx is None:
            return

        # Only emit when the closed bar advances or key values change.
        # Emitting on every 3-second refresh floods _on_cvd_automation_market_state
        # which triggers position checks and potentially order placement each time.
        ts_str = self.all_timestamps[idx].isoformat() if idx < len(self.all_timestamps) else None
        new_price_close = float(price_data_array[idx])
        state_key = (ts_str, round(new_price_close, 4))
        if getattr(self, "_last_emitted_state_key", None) == state_key:
            return
        self._last_emitted_state_key = state_key

        self.automation_state_signal.emit({
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "enabled": self.automate_toggle.isChecked(),
            "stoploss_points": float(self.automation_stoploss_input.value()),
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "signal_filter": self._selected_signal_filter(),
            "bar_x": float(x_arr[idx]),
            "price_close": new_price_close,
            "ema10": float(ema10[idx]),
            "ema51": float(ema51[idx]),
            "cvd_close": float(cvd_data_array[idx]),
            "cvd_ema10": float(cvd_ema10[idx]),
            "cvd_ema51": float(cvd_ema51[idx]),
            "timestamp": ts_str,
        })

    def _latest_closed_bar_index(self) -> int | None:
        if not self.all_timestamps:
            return None

        idx = len(self.all_timestamps) - 1
        if not self.live_mode:
            return idx

        latest_ts = pd.Timestamp(self.all_timestamps[idx])
        now_ts = (
            pd.Timestamp.now(tz=latest_ts.tz)
            if latest_ts.tz is not None
            else pd.Timestamp.now()
        )

        # In live mode, treat current minute candle as open/incomplete.
        if latest_ts.floor("min") >= now_ts.floor("min"):
            idx -= 1

        if idx < 0:
            return None
        return idx

    # ------------------------------------------------------------------

    def _load_and_plot(self, force: bool = False):
        """
        Safe background fetch.
        Dialog owns the QThread.
        Worker does NOT own its thread.
        """
        if self.live_mode and getattr(self, "_historical_loaded_once", False) and not force:
            return

        if self._is_loading:
            return

        if not self.kite or not getattr(self.kite, "access_token", None):
            return

        focus_mode = self.btn_focus.isChecked()

        if self.live_mode:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=5)
        else:
            to_dt = self.current_date + timedelta(days=1)
            from_dt = self.previous_date

        self._is_loading = True

        # 🔥 Create thread owned by dialog
        self._fetch_thread = QThread(self)

        self._fetch_worker = _DataFetchWorker(
            self.kite,
            self.instrument_token,
            from_dt,
            to_dt,
            self.timeframe_minutes,
            focus_mode,
        )

        self._fetch_worker.moveToThread(self._fetch_thread)

        # Thread lifecycle
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.finished.connect(self._fetch_thread.quit)

        # Safe cleanup
        self._fetch_thread.finished.connect(self._fetch_worker.deleteLater)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)

        # GUI thread slots (auto queued)
        self._fetch_worker.result_ready.connect(self._on_fetch_result)
        self._fetch_worker.error.connect(self._on_fetch_error)

        self._fetch_thread.start()

    def _on_fetch_result(self, cvd_df, price_df, prev_close):
        self._is_loading = False
        self._plot_data(cvd_df, price_df, prev_close)

        self._historical_loaded_once = True
        if self.live_mode:
            self._last_live_refresh_minute = datetime.now().replace(second=0, microsecond=0)
            # 🔥 Remove live ticks that are now covered by historical data
            self._cleanup_overlapping_ticks()

    def _cleanup_overlapping_ticks(self):
        """
        Remove live tick points that are now covered by historical minute data.
        This prevents double-line rendering where live ticks overlap with historical candles.
        """
        if not self.all_timestamps or not self._live_tick_points:
            return

        # Get the last timestamp from historical data
        last_historical_ts = self.all_timestamps[-1]

        # Convert to pandas Timestamp and normalize timezone for comparison
        cutoff_ts = pd.Timestamp(last_historical_ts)
        if cutoff_ts.tz is not None:
            cutoff_ts = cutoff_ts.tz_localize(None)  # Make timezone-naive

        # Remove all tick points up to and including the last historical minute
        # Keep only ticks that are AFTER the last historical candle

        # Filter CVD tick points
        while self._live_tick_points:
            tick_ts, _ = self._live_tick_points[0]
            # Normalize tick timestamp for comparison
            tick_pd = pd.Timestamp(tick_ts)
            if tick_pd.tz is not None:
                tick_pd = tick_pd.tz_localize(None)

            # If tick is before or in the same minute as historical data, remove it
            if tick_pd.replace(second=0, microsecond=0) <= cutoff_ts.replace(second=0, microsecond=0):
                self._live_tick_points.popleft()
            else:
                break

        # Filter price tick points
        while self._live_price_points:
            tick_ts, _ = self._live_price_points[0]
            # Normalize tick timestamp for comparison
            tick_pd = pd.Timestamp(tick_ts)
            if tick_pd.tz is not None:
                tick_pd = tick_pd.tz_localize(None)

            if tick_pd.replace(second=0, microsecond=0) <= cutoff_ts.replace(second=0, microsecond=0):
                self._live_price_points.popleft()
            else:
                break

        # 🔥 Reset offset so next tick aligns with updated historical data
        # This ensures smooth continuation after historical refresh
        self._live_cvd_offset = None

    # ------------------------------------------------------------------

    def _on_fetch_error(self, msg: str):
        """Called on the GUI thread when background fetch fails."""
        if msg not in ("no_data", "empty_df", "no_sessions"):
            logger.error("Failed to load CVD data: %s", msg)

    def _on_fetch_done(self):
        worker = getattr(self, "_fetch_worker", None)

        if worker is not None:
            # Ensure thread fully stopped
            worker.quit_thread()

        self._fetch_worker = None
        self._is_loading = False

    # ------------------------------------------------------------------

    def _plot_data(self, cvd_df: pd.DataFrame, price_df: pd.DataFrame, prev_close: float):
        focus_mode = self.btn_focus.isChecked()

        # Clear all curves
        self.prev_curve.clear()
        self.today_curve.clear()
        self.live_dot.clear()
        self.today_tick_curve.clear()
        self.price_prev_curve.clear()
        self.price_today_curve.clear()
        self.price_today_tick_curve.clear()
        self.price_live_dot.clear()
        self.price_atr_above_markers.clear()
        self.price_atr_below_markers.clear()
        self.cvd_atr_above_markers.clear()
        self.cvd_atr_below_markers.clear()
        self._clear_confluence_lines()

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
        self._current_session_last_cvd_value = None
        self._live_cvd_offset = None

        for i, sess in enumerate(sessions):
            df_cvd_sess = cvd_df[cvd_df["session"] == sess]
            df_price_sess = price_df[price_df["session"] == sess]

            cvd_y_raw = df_cvd_sess["close"].values
            cvd_high_raw = df_cvd_sess["high"].values if "high" in df_cvd_sess.columns else cvd_y_raw
            cvd_low_raw = df_cvd_sess["low"].values if "low" in df_cvd_sess.columns else cvd_y_raw
            price_y_raw = df_price_sess["close"].values
            price_high_raw = df_price_sess["high"].values
            price_low_raw = df_price_sess["low"].values

            # Rebasing logic for CVD
            if i == 0 and len(sessions) == 2 and not self.btn_focus.isChecked():
                cvd_y = cvd_y_raw - prev_close
                cvd_high = cvd_high_raw - prev_close
                cvd_low = cvd_low_raw - prev_close
            else:
                cvd_y = cvd_y_raw
                cvd_high = cvd_high_raw
                cvd_low = cvd_low_raw

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
                self._current_session_last_cvd_value = float(cvd_y[-1]) if len(cvd_y) else None

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

        def two_day_time_formatter(values, *_):
            """Maps sequential indices to actual timestamps for two-day view"""
            labels = []
            for v in values:
                idx = int(v)
                if 0 <= idx < len(self.all_timestamps):
                    ts = self.all_timestamps[idx]
                    labels.append(ts.strftime("%H:%M"))
                else:
                    labels.append("")
            return labels

        if focus_mode:
            self.axis.tickStrings = time_formatter
            self.price_axis.tickStrings = time_formatter
        else:
            # Use timestamp-based formatter for two-day view
            self.axis.tickStrings = two_day_time_formatter
            self.price_axis.tickStrings = two_day_time_formatter

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

        # Current confluence rendering uses _confluence_line_map.
        # Ensure full cleanup between re-plots so stale keys don't
        # suppress fresh ATR/confluence line rendering.
        line_map = getattr(self, "_confluence_line_map", None)
        if line_map:
            for pairs in line_map.values():
                for plot, line in pairs:
                    try:
                        plot.removeItem(line)
                    except Exception:
                        pass
            line_map.clear()

    def _draw_confluence_lines(
            self,
            price_above_mask: np.ndarray,
            price_below_mask: np.ndarray,
            price_cross_above_ema: np.ndarray,
            price_cross_below_ema: np.ndarray,
            cvd_above_mask: np.ndarray,
            cvd_below_mask: np.ndarray,
            cvd_above_ema51: np.ndarray,
            cvd_below_ema51: np.ndarray,
            x_arr: np.ndarray,
    ):
        if not hasattr(self, "_confluence_line_map"):
            self._confluence_line_map = {}

        # Calculate CVD EMAs and position masks
        cvd_data = np.array(self.all_cvd_data, dtype=float)
        cvd_ema10 = self._calculate_ema(cvd_data, 10)
        cvd_ema51 = self._calculate_ema(cvd_data, 51)

        cvd_above_ema10 = cvd_data > cvd_ema10
        cvd_below_ema10 = cvd_data < cvd_ema10

        # Calculate price EMAs
        price_data = np.array(self.all_price_data, dtype=float)
        price_ema10 = self._calculate_ema(price_data, 10)
        price_ema51 = self._calculate_ema(price_data, 51)

        # ----------------------------------------------------------
        # STRATEGY 1: ATR REVERSAL (Confluence of Price + CVD ATR signals)
        # ----------------------------------------------------------
        short_atr_reversal, long_atr_reversal = self.strategy_detector.detect_atr_reversal_strategy(
            price_atr_above=price_above_mask,
            price_atr_below=price_below_mask,
            cvd_atr_above=cvd_above_mask,
            cvd_atr_below=cvd_below_mask
        )

        # ----------------------------------------------------------
        # STRATEGY 2: EMA & CVD CROSS
        # ----------------------------------------------------------
        short_ema_cross, long_ema_cross = self.strategy_detector.detect_ema_cvd_cross_strategy(
            price_data=price_data,
            price_ema10=price_ema10,
            price_ema51=price_ema51,
            cvd_data=cvd_data,
            cvd_ema10=cvd_ema10,
            cvd_ema51=cvd_ema51,
            cvd_ema_gap_threshold=self.cvd_ema_gap_input.value()
        )

        # ----------------------------------------------------------
        # STRATEGY 3: ATR & CVD DIVERGENCE
        # ----------------------------------------------------------
        short_divergence, long_divergence = self.strategy_detector.detect_atr_cvd_divergence_strategy(
            price_atr_above=price_above_mask,
            price_atr_below=price_below_mask,
            cvd_above_ema10=cvd_above_ema10,
            cvd_below_ema10=cvd_below_ema10,
            cvd_above_ema51=cvd_above_ema51,
            cvd_below_ema51=cvd_below_ema51,
            cvd_data=cvd_data,
            ema_cross_short=short_ema_cross,
            ema_cross_long=long_ema_cross
        )

        # ----------------------------------------------------------
        # Combine signals based on selected filter
        # ----------------------------------------------------------
        signal_filter = self._selected_signal_filter()

        if signal_filter == self.SIGNAL_FILTER_ATR_ONLY:
            short_mask = short_atr_reversal
            long_mask = long_atr_reversal
            signal_label = "atr_reversal"

        elif signal_filter == self.SIGNAL_FILTER_EMA_CROSS_ONLY:
            short_mask = short_ema_cross
            long_mask = long_ema_cross
            signal_label = "ema_cvd_cross"

        elif signal_filter == self.SIGNAL_FILTER_OTHERS:
            short_mask = short_divergence
            long_mask = long_divergence
            signal_label = "atr_cvd_divergence"

        else:  # SIGNAL_FILTER_ALL
            short_mask = short_atr_reversal | short_ema_cross | short_divergence
            long_mask = long_atr_reversal | long_ema_cross | long_divergence
            signal_label = "all"

        # Ensure array alignment
        length = min(len(x_arr), len(short_mask), len(long_mask))
        x_arr = x_arr[:length]
        short_mask = short_mask[:length]
        long_mask = long_mask[:length]

        # ----------------------------------------------------------
        # Draw confluence lines
        # ----------------------------------------------------------
        new_keys = set()

        def _add_line(key: str, x: float, color: str):
            if key in self._confluence_line_map:
                return

            pen = pg.mkPen(color, width=2.0)
            pairs = []

            for plot in (self.price_plot, self.plot):
                line = pg.InfiniteLine(pos=x, angle=90, movable=False, pen=pen)
                line.setZValue(-10)
                plot.addItem(line)
                pairs.append((plot, line))

            self._confluence_line_map[key] = pairs

        for idx in np.where(short_mask)[0]:
            key = f"S:{idx}"
            new_keys.add(key)
            _add_line(key, float(x_arr[idx]), "#FF4444")

        for idx in np.where(long_mask)[0]:
            key = f"L:{idx}"
            new_keys.add(key)
            _add_line(key, float(x_arr[idx]), "#00E676")

        # Remove obsolete lines
        obsolete = set(self._confluence_line_map.keys()) - new_keys
        for key in obsolete:
            for plot, line in self._confluence_line_map[key]:
                plot.removeItem(line)
            del self._confluence_line_map[key]

        # ----------------------------------------------------------
        # AUTOMATION signal emission (existing code)
        # ----------------------------------------------------------
        if not self.automate_toggle.isChecked():
            return

        closed_idx = self._latest_closed_bar_index()
        if closed_idx is None or closed_idx >= length:
            return

        # Time filter: skip first 5 minutes (9:15-9:20) and last 30 minutes (15:00 onwards)
        bar_ts = self.all_timestamps[closed_idx]
        bar_time = bar_ts.time()
        if bar_time < time(9, 20) or bar_time >= time(15, 0):
            return

        if self._is_chop_regime(closed_idx):
            return

        side = None
        if short_mask[closed_idx]:
            side = "short"
        elif long_mask[closed_idx]:
            side = "long"

        if side is None:
            return

        closed_bar_ts = self.all_timestamps[closed_idx].isoformat()

        if self._last_emitted_closed_bar_ts == closed_bar_ts:
            return

        self._last_emitted_closed_bar_ts = closed_bar_ts

        payload = {
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "signal_side": side,
            "signal_type": signal_label,
            "signal_x": float(x_arr[closed_idx]),
            "price_close": float(self.all_price_data[closed_idx]),
            "stoploss_points": float(self.automation_stoploss_input.value()),
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "timestamp": closed_bar_ts,
        }

        QTimer.singleShot(0, lambda p=payload: self.automation_signal.emit(p))

    def _build_slope_direction_masks(self, series: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
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
        """
        In live mode, refresh historical once per completed minute so base curves
        stay in sync while tick overlays provide intraminute motion.
        """
        if not self.live_mode:
            return

        if self._is_loading:
            return

        current_minute = datetime.now().replace(second=0, microsecond=0)
        if self._last_live_refresh_minute is None:
            self._last_live_refresh_minute = current_minute
            return

        if current_minute <= self._last_live_refresh_minute:
            return

        self._load_and_plot(force=True)

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
        self.strategy_detector.timeframe_minutes = minutes

        for m, btn in self.tf_buttons.items():
            btn.setChecked(m == minutes)

        # Clear visuals
        self.prev_curve.clear()
        self.today_curve.clear()
        self.live_dot.clear()
        self.today_tick_curve.clear()
        self.price_prev_curve.clear()
        self.price_today_curve.clear()
        self.price_today_tick_curve.clear()
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
        self._live_tick_points.clear()
        self._live_price_points.clear()
        self._live_cvd_offset = None
        self._current_session_last_cvd_value = None
        self._last_plot_x_indices = []
        self._load_and_plot(force=True)

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
            self.price_today_tick_curve.clear()
            return

        if self._current_session_start_ts is None:
            return

        focus_mode = self.btn_focus.isChecked()
        session_start_ts = pd.Timestamp(self._current_session_start_ts)

        def _align_tick_ts(ts: datetime) -> pd.Timestamp:
            tick_ts = pd.Timestamp(ts)

            # Handle mixed tz-aware / tz-naive arithmetic safely.
            if session_start_ts.tz is None and tick_ts.tz is not None:
                tick_ts = tick_ts.tz_convert(None)
            elif session_start_ts.tz is not None and tick_ts.tz is None:
                tick_ts = tick_ts.tz_localize(session_start_ts.tz)
            elif session_start_ts.tz is not None and tick_ts.tz is not None and tick_ts.tz != session_start_ts.tz:
                tick_ts = tick_ts.tz_convert(session_start_ts.tz)

            return tick_ts

        current_day = session_start_ts.date()
        points = [
            (ts, cvd) for ts, cvd in self._live_tick_points
            if _align_tick_ts(ts).date() == current_day
        ]
        if not points:
            self.today_tick_curve.clear()
            self.price_today_tick_curve.clear()
            return

        if len(points) > self.LIVE_TICK_DOWNSAMPLE_TARGET:
            step = max(1, len(points) // self.LIVE_TICK_DOWNSAMPLE_TARGET)
            points = points[::step]

        x_vals: list[float] = []
        y_vals: list[float] = []
        price_vals: list[float] = []
        price_map = {ts: px for ts, px in self._live_price_points}

        for ts, cvd in points:
            tick_ts = _align_tick_ts(ts)

            if focus_mode:
                tick_dt = tick_ts.to_pydatetime()
                x = self._time_to_session_index(tick_dt) + (tick_dt.second / 60.0) + (
                        tick_dt.microsecond / 60_000_000.0)
            else:
                minute_offset = (tick_ts - session_start_ts).total_seconds() / 60.0
                x = self._current_session_x_base + minute_offset

            x_vals.append(x)
            y_vals.append(cvd)
            price_vals.append(float(price_map.get(ts, np.nan)))

        # Convert to numpy arrays for consistent handling
        x_arr = np.array(x_vals)
        y_arr = np.array(y_vals)
        price_arr = np.array(price_vals)

        # Remove any NaN or invalid values to prevent vertical lines
        valid_cvd_mask = np.isfinite(y_arr)
        valid_price_mask = np.isfinite(price_arr)

        # Detect large jumps in CVD that would create vertical lines
        # This happens when offset changes or session restarts
        if len(y_arr) > 1:
            cvd_deltas = np.abs(np.diff(y_arr))
            # If any jump is > 10x the median change, it's likely an offset issue
            median_change = np.median(cvd_deltas[cvd_deltas > 0]) if np.any(cvd_deltas > 0) else 1
            large_jump_threshold = max(median_change * 10, 10000)  # At least 10k or 10x median
            large_jumps = cvd_deltas > large_jump_threshold

            # Mark points after large jumps as invalid to break connection
            if np.any(large_jumps):
                jump_indices = np.where(large_jumps)[0] + 1  # +1 because diff is offset by 1
                valid_cvd_mask[jump_indices] = False

        # Plot CVD ticks - only connect finite values with consistent pen
        if np.any(valid_cvd_mask):
            # Ensure consistent rendering by setting pen explicitly
            pen = pg.mkPen("#26A69A", width=1.4, cosmetic=True)
            self.today_tick_curve.setPen(pen)
            self.today_tick_curve.setData(
                x_arr[valid_cvd_mask],
                y_arr[valid_cvd_mask],
                connect='finite'
            )
            # Update live dot with last valid CVD point
            last_valid_idx = np.where(valid_cvd_mask)[0][-1]
            self.live_dot.setData([x_arr[last_valid_idx]], [y_arr[last_valid_idx]])

        # Plot price ticks - only connect finite values with consistent pen
        if np.any(valid_price_mask):
            # Ensure consistent rendering by setting pen explicitly
            pen = pg.mkPen("#FFE57F", width=1.4, cosmetic=True)
            self.price_today_tick_curve.setPen(pen)
            self.price_today_tick_curve.setData(
                x_arr[valid_price_mask],
                price_arr[valid_price_mask],
                connect='finite'
            )
            # Update live dot with last valid price point
            last_valid_idx = np.where(valid_price_mask)[0][-1]
            self.price_live_dot.setData([x_arr[last_valid_idx]], [price_arr[last_valid_idx]])

    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fix_axis_after_show)

    def closeEvent(self, event):
        self._persist_setup_values()
        try:
            if hasattr(self, "_fetch_thread") and self._fetch_thread.isRunning():
                self._fetch_thread.quit()
                self._fetch_thread.wait(2000)
        except Exception:
            pass
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # REGIME / CHOP DETECTION
    # ------------------------------------------------------------------

    def _compute_adx(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
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

    def _is_chop_regime(self, idx: int) -> bool:
        """
        Determine if market is in chop regime at given index.
        Uses:
            - ADX
            - EMA 51 slope
            - Price hugging EMA 51
        """

        if idx is None or idx < 20:
            return False

        price = np.array(self.all_price_data, dtype=float)
        high = np.array(self.all_price_high_data, dtype=float)
        low = np.array(self.all_price_low_data, dtype=float)

        ema51 = self._calculate_ema(price, 51)
        atr = self._calculate_atr(high, low, price, 14)
        adx = self._compute_adx(high, low, price, 14)

        atr_val = max(float(atr[idx]), 1e-6)

        # 1️⃣ Low ADX
        low_adx = adx[idx] < 18

        # 2️⃣ EMA slope
        slope = ema51[idx] - ema51[idx - 5]
        flat = abs(slope) < (0.02 * atr_val)

        # 3️⃣ Price hugging EMA
        hugging = abs(price[idx] - ema51[idx]) < (0.25 * atr_val)

        return (
                low_adx
                or (hugging and flat and adx[idx] < 22)
        )

    def _export_chart_image(self):
        """Export current chart view as PNG image"""
        from PySide6.QtWidgets import QFileDialog
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import QPoint
        from datetime import datetime

        # Generate default filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"chart_{self.symbol}_{timestamp}.png"

        # Open save dialog
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Export Chart Image",
            default_filename,
            "PNG Images (*.png);;All Files (*)"
        )

        if not filepath:
            return

        # Ensure .png extension
        if not filepath.lower().endswith('.png'):
            filepath += '.png'

        try:
            # Grab the widget as a pixmap
            pixmap = self.grab()

            # Save the image
            pixmap.save(filepath, "PNG")
            logger.info(f"Chart exported successfully to: {filepath}")
        except Exception as e:
            logger.error(f"Failed to export chart: {e}")

    def changeEvent(self, event):
        # Intentionally NOT stopping/starting the refresh_timer on activation changes.
        # During automation the dialog loses/regains focus constantly; toggling the
        # timer here caused a racing condition where _load_and_plot was re-entered
        # before _is_loading could be set, crashing the app within seconds.
        super().changeEvent(event)