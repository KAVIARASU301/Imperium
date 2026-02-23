import json
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
    QFormLayout, QGroupBox, QColorDialog, QFileDialog
)
from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QThread, QSettings
from PySide6.QtGui import QColor
from pyqtgraph import AxisItem, TextItem

from kiteconnect import KiteConnect
from core.cvd.cvd_historical import CVDHistoricalBuilder
from core.cvd.cvd_mode import CVDMode
from core.auto_trader.strategy_signal_detector import StrategySignalDetector
from core.auto_trader.constants import TRADING_START, TRADING_END, MINUTES_PER_SESSION
from core.auto_trader.data_worker import _DataFetchWorker
from core.auto_trader.date_navigator import DateNavigator
from core.auto_trader.setup_panel import SetupPanelMixin
from core.auto_trader.settings_manager import SettingsManagerMixin
from core.auto_trader.signal_renderer import SignalRendererMixin
from core.auto_trader.simulator import SimulatorMixin
from core.auto_trader.indicators import calculate_ema, calculate_vwap, calculate_atr, compute_adx, build_slope_direction_masks, is_chop_regime
from core.auto_trader.signal_governance import SignalGovernance
from core.auto_trader.stacker import StackerState
logger = logging.getLogger(__name__)


# =============================================================================
# Auto Trader DIalog
# =============================================================================

class AutoTraderDialog(SetupPanelMixin, SettingsManagerMixin, SignalRendererMixin, SimulatorMixin, QDialog):
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
    SIGNAL_FILTER_BREAKOUT_ONLY = "breakout_only"
    SIGNAL_FILTER_OTHERS = "others"
    SIGNAL_FILTER_OPEN_DRIVE_ONLY = "open_drive_only"

    ATR_MARKER_SHOW_ALL = "show_all"
    ATR_MARKER_CONFLUENCE_ONLY = "confluence_only"
    ATR_MARKER_GREEN_ONLY = "green_only"
    ATR_MARKER_RED_ONLY = "red_only"
    ATR_MARKER_HIDE_ALL = "hide_all"

    CVD_VALUE_MODE_RAW = "raw"
    CVD_VALUE_MODE_NORMALIZED = "normalized"

    BREAKOUT_SWITCH_KEEP = "keep_breakout"
    BREAKOUT_SWITCH_PREFER_ATR = "prefer_atr_reversal"
    BREAKOUT_SWITCH_ADAPTIVE = "adaptive"

    MAX_GIVEBACK_STRATEGY_ATR_REVERSAL = "atr_reversal"
    MAX_GIVEBACK_STRATEGY_EMA_CROSS = "ema_cross"
    MAX_GIVEBACK_STRATEGY_ATR_DIVERGENCE = "atr_divergence"
    MAX_GIVEBACK_STRATEGY_RANGE_BREAKOUT = "range_breakout"
    MAX_GIVEBACK_STRATEGY_CVD_RANGE_BREAKOUT = "cvd_range_breakout"
    MAX_GIVEBACK_STRATEGY_OPEN_DRIVE = "open_drive"

    ROUTE_BUY_EXIT_PANEL = "buy_exit_panel"
    ROUTE_DIRECT = "direct"

    BG_TARGET_NONE = "none"
    BG_TARGET_CHART = "chart"
    BG_TARGET_WINDOW = "window"

    # =========================================================================
    # SECTION 1: INITIALIZATION
    # =========================================================================

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
        self._setup_values_ready = False
        self.timeframe_minutes = 1  # default = 1 minute
        self.strategy_detector = StrategySignalDetector(timeframe_minutes=self.timeframe_minutes)
        self.signal_governance = SignalGovernance()

        self.live_mode = True
        self.current_date = None
        self.previous_date = None
        self._live_tick_points: deque[tuple[datetime, float]] = deque(maxlen=self.LIVE_TICK_MAX_POINTS)
        self._live_price_points: deque[tuple[datetime, float]] = deque(maxlen=self.LIVE_TICK_MAX_POINTS)
        self._current_session_start_ts: datetime | None = None
        self._current_session_x_base: float = 0.0
        self._live_cvd_offset: float | None = None
        self._current_session_last_cvd_value: float | None = None
        self._current_session_volume_scale: float = 1.0
        self._is_loading = False
        self._last_live_refresh_minute: datetime | None = None

        # Plot caches
        self.all_timestamps: list[datetime] = []
        self._last_plot_x_indices: list[float] = []

        # 🎯 Confluence signal lines (price + CVD both reversal at same bar)
        self._confluence_lines: list = []  # InfiniteLine items added to both plots
        self._last_emitted_signal_key: str | None = None
        self._last_emitted_closed_bar_ts: str | None = None
        # Counts ATR reversal signals suppressed while a breakout trade is active (live mode)
        self._live_atr_skip_count: int = 0
        self._live_active_breakout_side: str | None = None  # tracks which side the breakout is on
        self._live_stacker_state: StackerState | None = None
        self._simulator_results: dict | None = None
        self._chart_line_color = "#26A69A"
        self._price_line_color = "#FFE57F"
        self._confluence_short_color = "#FF4444"
        self._confluence_long_color = "#00E676"
        self._chart_line_width = 2.5
        self._chart_line_opacity = 1.0
        self._confluence_line_width = 2.0
        self._confluence_line_opacity = 1.0
        self._ema_line_opacity = 0.85
        self._window_bg_image_path = ""
        self._chart_bg_image_path = ""

        # 🆕 Strategy-aware chop filter defaults
        self._chop_filter_atr_reversal = True
        self._chop_filter_ema_cross = True
        self._chop_filter_atr_divergence = True
        # range_breakout is NEVER chop-filtered (hardcoded)

        # 🆕 Breakout consolidation requirement defaults
        self._breakout_min_consolidation_minutes = 0
        self._breakout_min_consolidation_adx = 0.0
        self._live_tick_cvd_pen = pg.mkPen("#26A69A", width=1.4, cosmetic=True)
        self._live_tick_price_pen = pg.mkPen("#FFE57F", width=1.4, cosmetic=True)

        self.setWindowTitle(f"Auto Trader — {self._display_symbol_for_title(symbol)}")
        self.setObjectName("autoTraderWindow")
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

    # =========================================================================
    # SECTION 2: UI SETUP
    # =========================================================================

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

        # -------- Timeframe dropdown (LEFT of center) --------
        tf_label = QLabel("TF")
        tf_label.setStyleSheet("color: #8A9BA8; font-size: 11px; font-weight: 600;")
        top_bar.addWidget(tf_label)

        self.timeframe_combo = QComboBox()
        self.timeframe_combo.setFixedHeight(26)
        self.timeframe_combo.setFixedWidth(84)
        self.timeframe_combo.setStyleSheet(compact_combo_style)

        self._timeframe_options = [
            ("1m", 1),
            ("3m", 3),
            ("5m", 5),
            ("15m", 15),
            ("1h", 60),
        ]
        for label, minutes in self._timeframe_options:
            self.timeframe_combo.addItem(label, minutes)

        # Default select 1m
        self.timeframe_combo.setCurrentIndex(0)
        self.timeframe_combo.currentIndexChanged.connect(self._on_timeframe_combo_changed)
        top_bar.addWidget(self.timeframe_combo)

        # Navigator (CENTER)
        self.navigator = DateNavigator(self)

        # Day View Toggle (unchecked => 1D, checked => 2D)
        self.btn_focus = QPushButton("1D")
        self.btn_focus.setCheckable(True)
        self.btn_focus.setChecked(False)
        self.btn_focus.setFixedHeight(28)
        self.btn_focus.setMinimumWidth(56)
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
        self.btn_focus.setToolTip("Toggle 2-day view")
        self.btn_focus.toggled.connect(self._on_focus_mode_changed)
        top_bar.addWidget(self.btn_focus)
        self.btn_focus.setText("1D")

        top_bar.addWidget(self.navigator)

        if self.cvd_engine:
            self.cvd_engine.set_mode(CVDMode.SINGLE_DAY)

        # Automate Toggle
        self.automate_toggle = QCheckBox("Automate")
        self.automate_toggle.setChecked(False)
        self.automate_toggle.setStyleSheet(compact_toggle_style)
        self.automate_toggle.toggled.connect(self._on_automation_settings_changed)

        self.simulator_run_btn = QPushButton("Run Simulator")
        self.simulator_run_btn.setFixedHeight(28)
        self.simulator_run_btn.setMinimumWidth(120)
        self.simulator_run_btn.setToolTip("Run simulator (Space)")
        self.simulator_run_btn.setStyleSheet("""
            QPushButton {
                background:#212635;
                border:1px solid #3A4458;
                border-radius:4px;
                padding:4px 10px;
                color:#9CCAF4;
                font-weight:600;
            }
            QPushButton:hover { border: 1px solid #5B9BD5; }
            QPushButton:pressed { background:#1B1F2B; }
        """)
        self.simulator_run_btn.clicked.connect(self._on_simulator_run_clicked)

        self.automation_stoploss_input = QSpinBox()
        self.automation_stoploss_input.setRange(1, 1000)
        self.automation_stoploss_input.setValue(50)
        self.automation_stoploss_input.setSingleStep(5)
        self.automation_stoploss_input.setFixedWidth(96)
        self.automation_stoploss_input.setStyleSheet(compact_spinbox_style)
        self.automation_stoploss_input.valueChanged.connect(self._on_automation_settings_changed)

        self.max_profit_giveback_input = QSpinBox()
        self.max_profit_giveback_input.setRange(0, 5000)
        self.max_profit_giveback_input.setValue(75)
        self.max_profit_giveback_input.setSingleStep(5)
        self.max_profit_giveback_input.setSpecialValueText("Off")
        self.max_profit_giveback_input.setFixedWidth(96)
        self.max_profit_giveback_input.setStyleSheet(compact_spinbox_style)
        self.max_profit_giveback_input.setToolTip(
            "Exit when current profit pulls back from peak profit by this many points.\n"
            "0 = Off"
        )
        self.max_profit_giveback_input.valueChanged.connect(self._on_automation_settings_changed)

        self.max_giveback_atr_reversal_check = QCheckBox("ATR Rev")
        self.max_giveback_atr_reversal_check.setChecked(True)
        self.max_giveback_atr_reversal_check.setToolTip("Apply max profit giveback exit to ATR Reversal trades.")
        self.max_giveback_atr_reversal_check.toggled.connect(self._on_automation_settings_changed)

        self.max_giveback_ema_cross_check = QCheckBox("EMA Cross")
        self.max_giveback_ema_cross_check.setChecked(True)
        self.max_giveback_ema_cross_check.setToolTip("Apply max profit giveback exit to EMA Cross trades.")
        self.max_giveback_ema_cross_check.toggled.connect(self._on_automation_settings_changed)

        self.max_giveback_atr_divergence_check = QCheckBox("ATR Div")
        self.max_giveback_atr_divergence_check.setChecked(True)
        self.max_giveback_atr_divergence_check.setToolTip("Apply max profit giveback exit to ATR Divergence trades.")
        self.max_giveback_atr_divergence_check.toggled.connect(self._on_automation_settings_changed)

        self.max_giveback_range_breakout_check = QCheckBox("Breakout")
        self.max_giveback_range_breakout_check.setChecked(True)
        self.max_giveback_range_breakout_check.setToolTip("Apply max profit giveback exit to Range Breakout trades.")
        self.max_giveback_range_breakout_check.toggled.connect(self._on_automation_settings_changed)

        self.automation_route_combo = QComboBox()
        self.automation_route_combo.setFixedWidth(180)
        self.automation_route_combo.setStyleSheet(compact_combo_style)
        self.automation_route_combo.addItem("Buy Exit Panel", self.ROUTE_BUY_EXIT_PANEL)
        self.automation_route_combo.addItem("Direct", self.ROUTE_DIRECT)
        self.automation_route_combo.setCurrentIndex(0)
        self.automation_route_combo.currentIndexChanged.connect(self._on_automation_settings_changed)

        top_bar.addWidget(self.automate_toggle)
        top_bar.addWidget(self.simulator_run_btn)

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

        self.btn_refresh_plot = QPushButton("⟳")
        self.btn_refresh_plot.setFixedSize(28, 28)
        self.btn_refresh_plot.setToolTip("Refresh chart plot")
        self.btn_refresh_plot.setStyleSheet("""
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
        self.btn_refresh_plot.clicked.connect(self._refresh_plot_only)

        top_bar.addStretch()

        root.addLayout(top_bar)

        self.navigator.btn_back.setToolTip("Previous trading day (←)")
        self.navigator.btn_forward.setToolTip("Next trading day (→)")

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

        self.cvd_atr_distance_input = QDoubleSpinBox()
        self.cvd_atr_distance_input.setRange(0.1, 30.0)
        self.cvd_atr_distance_input.setDecimals(2)
        self.cvd_atr_distance_input.setSingleStep(0.1)
        self.cvd_atr_distance_input.setValue(11.0)
        self.cvd_atr_distance_input.setFixedWidth(96)
        self.cvd_atr_distance_input.setStyleSheet(compact_spinbox_style)
        self.cvd_atr_distance_input.setToolTip(
            "ATR-multiple distance threshold used for CVD ATR reversal markers.\n"
            "Higher values reduce signal frequency; lower values make CVD reversal detection more sensitive."
        )
        self.cvd_atr_distance_input.valueChanged.connect(self._on_atr_settings_changed)

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

        self.vwap_checkbox = QCheckBox("VWAP")
        self.vwap_checkbox.setChecked(False)
        self.vwap_checkbox.setStyleSheet("""
            QCheckBox {
                color: #00E676;
                font-weight: 600;
                font-size: 11px;
                spacing: 3px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #00E676;
                border-radius: 3px;
                background: #1B1F2B;
            }
            QCheckBox::indicator:checked {
                background: #00E676;
            }
        """)
        self.vwap_checkbox.toggled.connect(self._on_vwap_toggled)
        ema_bar.addWidget(self.vwap_checkbox)

        ema_bar.addSpacing(4)

        signal_filter_label = QLabel("Filter")
        signal_filter_label.setStyleSheet("color: #8A9BA8; font-size: 11px;")
        ema_bar.addWidget(signal_filter_label)

        self.signal_filter_combo = QComboBox()
        self.signal_filter_combo.setFixedWidth(180)
        self.signal_filter_combo.setStyleSheet(compact_combo_style)
        self._init_signal_filter_combo(self.signal_filter_combo)
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

        self.simulator_summary_label = QLabel("Simulator: click Run Simulator")
        self.simulator_summary_label.setStyleSheet("color: #8A9BA8; font-size: 11px; font-weight: 600;")
        ema_bar.addWidget(self.simulator_summary_label)
        # ── Stacker widgets─
        self.stacker_enabled_check = QCheckBox("Stacker")
        self.stacker_enabled_check.setChecked(False)
        self.stacker_enabled_check.setToolTip(
            "Pyramid scaling: add a new position every N points of favorable move.\n"
            "All stacked positions exit together when the anchor trade exits."
        )
        self.stacker_enabled_check.toggled.connect(self._on_stacker_settings_changed)

        self.stacker_step_input = QSpinBox()
        self.stacker_step_input.setRange(5, 500)
        self.stacker_step_input.setValue(20)
        self.stacker_step_input.setSingleStep(5)
        self.stacker_step_input.setSuffix(" pts")
        self.stacker_step_input.setStyleSheet(compact_spinbox_style)
        self.stacker_step_input.setToolTip(
            "Add a new position every this many points of favorable move from the anchor entry.\n"
            "Example: 20 → stack at +20, +40, +60 points."
        )
        self.stacker_step_input.valueChanged.connect(self._on_stacker_settings_changed)

        self.stacker_max_input = QSpinBox()
        self.stacker_max_input.setRange(1, 100)
        self.stacker_max_input.setValue(10)
        self.stacker_max_input.setSpecialValueText("1×")
        self.stacker_max_input.setStyleSheet(compact_spinbox_style)
        self.stacker_max_input.setToolTip(
            "Maximum number of stack entries to add on top of the anchor.\n"
            "Total positions = anchor + this value. Max 5 for risk safety."
        )
        self.stacker_max_input.valueChanged.connect(self._on_stacker_settings_changed)

        self._build_setup_dialog(compact_combo_style, compact_spinbox_style)

        ema_bar.addWidget(self.btn_refresh_plot)
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

        self.sim_taken_long_markers = pg.ScatterPlotItem(
            size=12,
            symbol="star",
            brush=pg.mkBrush("#00E676"),
            pen=pg.mkPen("#003820", width=1.0),
        )
        self.sim_taken_short_markers = pg.ScatterPlotItem(
            size=12,
            symbol="star",
            brush=pg.mkBrush("#FF5252"),
            pen=pg.mkPen("#4A0E0E", width=1.0),
        )
        self.sim_exit_win_markers = pg.ScatterPlotItem(
            size=10,
            symbol="o",
            brush=pg.mkBrush("#FFD54F"),
            pen=pg.mkPen("#FFFFFF", width=0.9),
        )
        self.sim_exit_loss_markers = pg.ScatterPlotItem(
            size=10,
            symbol="o",
            brush=pg.mkBrush("#EF5350"),
            pen=pg.mkPen("#FFFFFF", width=0.9),
        )
        self.sim_skipped_markers = pg.ScatterPlotItem(
            size=10,
            symbol="x",
            brush=pg.mkBrush("#B0BEC5"),
            pen=pg.mkPen("#ECEFF1", width=1.1),
        )
        self.sim_trade_path_lines = pg.PlotCurveItem(
            pen=pg.mkPen("#B0BEC5", width=1.4, style=Qt.DashLine),
            connect="pairs",
        )
        self.sim_trade_path_lines.setZValue(18)
        self.price_plot.addItem(self.sim_trade_path_lines)
        for marker in (
                self.sim_taken_long_markers,
                self.sim_taken_short_markers,
                self.sim_exit_win_markers,
                self.sim_exit_loss_markers,
                self.sim_skipped_markers,
        ):
            marker.setZValue(20)
            self.price_plot.addItem(marker)

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
        self.price_vwap_curve = pg.PlotCurveItem(
            pen=pg.mkPen('#00E676', width=2.0, style=Qt.SolidLine)
        )

        self.price_plot.addItem(self.price_ema10_curve)
        self.price_plot.addItem(self.price_ema21_curve)
        self.price_plot.addItem(self.price_ema51_curve)
        self.price_plot.addItem(self.price_vwap_curve)

        # Full opacity for clear visibility
        self.price_ema10_curve.setOpacity(0.85)
        self.price_ema21_curve.setOpacity(0.85)
        self.price_ema51_curve.setOpacity(0.85)
        self.price_vwap_curve.setOpacity(0.85)

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

        self._apply_visual_settings()

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

    # =========================================================================
    # SECTION 3: SETTINGS PERSISTENCE
    # =========================================================================

    def _load_persisted_setup_values(self):
        key_prefix = self._settings_key_prefix()
        global_key_prefix = self._global_settings_key_prefix()
        json_settings = self._read_setup_json()

        def _read_setting(name: str, default, value_type=None):
            if name in json_settings:
                value = json_settings[name]
                if value_type is None:
                    return value
                try:
                    if value_type is bool:
                        if isinstance(value, bool):
                            return value
                        if isinstance(value, str):
                            return value.strip().lower() in {"1", "true", "yes", "on"}
                        return bool(value)
                    return value_type(value)
                except (TypeError, ValueError):
                    return default

            token_key = f"{key_prefix}/{name}"
            global_key = f"{global_key_prefix}/{name}"
            key_to_read = global_key if self._settings.contains(global_key) else token_key
            if value_type is None:
                return self._settings.value(key_to_read, default)
            return self._settings.value(key_to_read, default, type=value_type)

        def _apply_combo_value(combo: QComboBox, data_value, fallback_index: int = 0):
            idx = combo.findData(data_value)
            combo.setCurrentIndex(idx if idx >= 0 else fallback_index)

        self.automate_toggle.blockSignals(True)
        self.automation_stoploss_input.blockSignals(True)
        self.max_profit_giveback_input.blockSignals(True)
        self.max_giveback_atr_reversal_check.blockSignals(True)
        self.max_giveback_ema_cross_check.blockSignals(True)
        self.max_giveback_atr_divergence_check.blockSignals(True)
        self.max_giveback_range_breakout_check.blockSignals(True)
        self.automation_route_combo.blockSignals(True)
        self.atr_base_ema_input.blockSignals(True)
        self.atr_distance_input.blockSignals(True)
        self.cvd_atr_distance_input.blockSignals(True)
        self.cvd_ema_gap_input.blockSignals(True)
        self.signal_filter_combo.blockSignals(True)
        self.atr_marker_filter_combo.blockSignals(True)
        self.setup_signal_filter_combo.blockSignals(True)
        self.setup_cvd_value_mode_combo.blockSignals(True)
        self.setup_atr_marker_filter_combo.blockSignals(True)
        self.range_lookback_input.blockSignals(True)  # 🆕 NEW
        self.breakout_switch_mode_combo.blockSignals(True)
        self.atr_skip_limit_input.blockSignals(True)
        self.deploy_mode_combo.blockSignals(True)
        self.min_confidence_input.blockSignals(True)
        self.canary_ratio_input.blockSignals(True)
        self.hide_simulator_btn_check.blockSignals(True)
        self.chop_filter_atr_reversal_check.blockSignals(True)
        self.chop_filter_ema_cross_check.blockSignals(True)
        self.chop_filter_atr_divergence_check.blockSignals(True)
        self.stacker_enabled_check.setChecked(_read_setting("stacker_enabled", False, bool))
        self.stacker_step_input.setValue(_read_setting("stacker_step_points", 20, int))
        self.stacker_max_input.setValue(_read_setting("stacker_max_stacks", 2, int))
        self.open_drive_enabled_check.blockSignals(True)
        self.open_drive_time_hour_input.blockSignals(True)
        self.open_drive_time_minute_input.blockSignals(True)
        self.open_drive_stack_enabled_check.blockSignals(True)
        self.open_drive_max_profit_giveback_input.blockSignals(True)
        self.breakout_min_consol_input.blockSignals(True)
        self.breakout_min_consol_adx_input.blockSignals(True)
        self.cvd_range_lookback_input.blockSignals(True)
        self.cvd_breakout_buffer_input.blockSignals(True)
        self.cvd_min_consol_bars_input.blockSignals(True)
        self.cvd_max_range_ratio_input.blockSignals(True)
        self.cvd_breakout_min_adx_input.blockSignals(True)
        self.chart_line_width_input.blockSignals(True)
        self.chart_line_opacity_input.blockSignals(True)
        self.confluence_line_width_input.blockSignals(True)
        self.confluence_line_opacity_input.blockSignals(True)
        self.ema_line_opacity_input.blockSignals(True)
        self.show_grid_lines_check.blockSignals(True)
        self.window_bg_upload_btn.blockSignals(True)
        self.window_bg_clear_btn.blockSignals(True)
        self.chart_bg_upload_btn.blockSignals(True)
        self.chart_bg_clear_btn.blockSignals(True)
        self.vwap_checkbox.blockSignals(True)
        for cb in self.setup_ema_default_checks.values():
            cb.blockSignals(True)

        self.automate_toggle.setChecked(
            _read_setting("enabled", self.automate_toggle.isChecked(), bool)
        )
        self.automation_stoploss_input.setValue(
            _read_setting("stoploss_points", self.automation_stoploss_input.value(), int)
        )
        self.max_profit_giveback_input.setValue(
            _read_setting("max_profit_giveback_points", self.max_profit_giveback_input.value(), int)
        )
        max_giveback_strategies = _read_setting(
            "max_profit_giveback_strategies",
            list(self._max_giveback_strategy_defaults()),
        )
        if not isinstance(max_giveback_strategies, (list, tuple, set)):
            max_giveback_strategies = self._max_giveback_strategy_defaults()
        self._apply_max_giveback_strategy_selection(list(max_giveback_strategies))
        _apply_combo_value(
            self.automation_route_combo,
            _read_setting("route", self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL),
            fallback_index=0,
        )

        self.atr_base_ema_input.setValue(
            _read_setting("atr_base_ema", self.atr_base_ema_input.value(), int)
        )
        self.atr_distance_input.setValue(
            _read_setting("atr_distance", self.atr_distance_input.value(), float)
        )
        self.cvd_atr_distance_input.setValue(
            _read_setting("cvd_atr_distance", self.cvd_atr_distance_input.value(), float)
        )
        self.cvd_ema_gap_input.setValue(
            _read_setting("cvd_ema_gap", self.cvd_ema_gap_input.value(), int)
        )
        _apply_combo_value(
            self.setup_cvd_value_mode_combo,
            _read_setting("cvd_value_mode", self.setup_cvd_value_mode_combo.currentData() or self.CVD_VALUE_MODE_RAW),
            fallback_index=0,
        )

        signal_filters_value = _read_setting(
            "signal_filters",
            None,
        )
        if isinstance(signal_filters_value, (list, tuple, set)):
            selected_signal_filters = [str(v) for v in signal_filters_value]
        else:
            legacy_signal_filter = _read_setting(
                "signal_filter",
                self.SIGNAL_FILTER_ALL,
            )
            selected_signal_filters = self._coerce_signal_filters(legacy_signal_filter)

        self._set_checked_signal_filters(self.signal_filter_combo, selected_signal_filters)
        self._set_checked_signal_filters(self.setup_signal_filter_combo, selected_signal_filters)

        marker_filter_value = _read_setting(
            "atr_marker_filter",
            self.atr_marker_filter_combo.currentData() or self.ATR_MARKER_CONFLUENCE_ONLY,
        )
        _apply_combo_value(self.atr_marker_filter_combo, marker_filter_value, fallback_index=1)
        _apply_combo_value(self.setup_atr_marker_filter_combo, marker_filter_value, fallback_index=1)

        # 🆕 Load range breakout settings
        self.range_lookback_input.setValue(
            _read_setting("range_lookback", self.range_lookback_input.value(), int)
        )
        _apply_combo_value(
            self.breakout_switch_mode_combo,
            _read_setting("breakout_switch_mode", self.breakout_switch_mode_combo.currentData() or self.BREAKOUT_SWITCH_ADAPTIVE),
            fallback_index=2,
        )
        self.atr_skip_limit_input.setValue(
            _read_setting("atr_skip_limit", 0, int)
        )
        _apply_combo_value(
            self.deploy_mode_combo,
            _read_setting("deploy_mode", self.signal_governance.deploy_mode),
            fallback_index=1,
        )
        self.min_confidence_input.setValue(
            _read_setting("min_confidence_for_live", self.signal_governance.min_confidence_for_live, float)
        )
        self.canary_ratio_input.setValue(
            _read_setting("canary_live_ratio", self.signal_governance.canary_live_ratio, float)
        )
        self._on_governance_settings_changed()

        self.hide_simulator_btn_check.setChecked(
            _read_setting("hide_simulator_button", False, bool)
        )
        self.open_drive_enabled_check.setChecked(_read_setting("open_drive_enabled", False, bool))
        self.open_drive_time_hour_input.setValue(_read_setting("open_drive_entry_hour", 9, int))
        self.open_drive_time_minute_input.setValue(_read_setting("open_drive_entry_minute", 17, int))
        self.open_drive_stack_enabled_check.setChecked(_read_setting("open_drive_stack_enabled", True, bool))
        self.open_drive_max_profit_giveback_input.setValue(_read_setting("open_drive_max_profit_giveback_points", 0, int))
        # 🆕 Load chop filter settings
        self.chop_filter_atr_reversal_check.setChecked(_read_setting("chop_filter_atr_reversal", True, bool))
        self.chop_filter_ema_cross_check.setChecked(_read_setting("chop_filter_ema_cross", True, bool))
        self.chop_filter_atr_divergence_check.setChecked(_read_setting("chop_filter_atr_divergence", True, bool))
        self._chop_filter_atr_reversal = self.chop_filter_atr_reversal_check.isChecked()
        self._chop_filter_ema_cross = self.chop_filter_ema_cross_check.isChecked()
        self._chop_filter_atr_divergence = self.chop_filter_atr_divergence_check.isChecked()
        # 🆕 Load consolidation requirement
        self.breakout_min_consol_input.setValue(_read_setting("breakout_min_consolidation_minutes", 0, int))
        self.breakout_min_consol_adx_input.setValue(_read_setting("breakout_min_consolidation_adx", 0.0, float))
        self._breakout_min_consolidation_minutes = self.breakout_min_consol_input.value()
        self._breakout_min_consolidation_adx = float(self.breakout_min_consol_adx_input.value())
        self.cvd_range_lookback_input.setValue(_read_setting("cvd_range_lookback_bars", 30, int))
        self.cvd_breakout_buffer_input.setValue(_read_setting("cvd_breakout_buffer", 0.10, float))
        self.cvd_min_consol_bars_input.setValue(_read_setting("cvd_min_consol_bars", 15, int))
        self.cvd_max_range_ratio_input.setValue(_read_setting("cvd_max_range_ratio", 0.80, float))
        self.cvd_breakout_min_adx_input.setValue(_read_setting("cvd_breakout_min_adx", 15.0, float))
        self.chart_line_width_input.setValue(
            _read_setting("chart_line_width", self.chart_line_width_input.value(), float)
        )
        self.chart_line_opacity_input.setValue(
            _read_setting("chart_line_opacity", self.chart_line_opacity_input.value(), float)
        )
        self.confluence_line_width_input.setValue(
            _read_setting("confluence_line_width", self.confluence_line_width_input.value(), float)
        )
        self.confluence_line_opacity_input.setValue(
            _read_setting("confluence_line_opacity", self.confluence_line_opacity_input.value(), float)
        )
        self.ema_line_opacity_input.setValue(
            _read_setting("ema_line_opacity", self.ema_line_opacity_input.value(), float)
        )
        self.show_grid_lines_check.setChecked(
            _read_setting("show_grid_lines", self.show_grid_lines_check.isChecked(), bool)
        )

        self._chart_line_color = _read_setting("chart_line_color", self._chart_line_color)
        self._price_line_color = _read_setting("price_line_color", self._price_line_color)
        self._confluence_short_color = _read_setting("confluence_short_color", self._confluence_short_color)
        self._confluence_long_color = _read_setting("confluence_long_color", self._confluence_long_color)

        for period, cb in self.setup_ema_default_checks.items():
            default_enabled = (period == 51)
            cb.setChecked(_read_setting(f"ema_default_{period}", default_enabled, bool))

        self.vwap_checkbox.setChecked(
            _read_setting("show_vwap", self.vwap_checkbox.isChecked(), bool)
        )

        persisted_window_bg = _read_setting("window_background_image_path", "") or ""
        persisted_chart_bg = _read_setting("chart_background_image_path", "") or ""

        # Backward compatibility with older single-target setting.
        legacy_bg_path = _read_setting("background_image_path", "") or ""
        legacy_bg_target = _read_setting("background_target", self.BG_TARGET_NONE)
        if not persisted_window_bg and legacy_bg_target == self.BG_TARGET_WINDOW:
            persisted_window_bg = legacy_bg_path
        if not persisted_chart_bg and legacy_bg_target == self.BG_TARGET_CHART:
            persisted_chart_bg = legacy_bg_path

        self._window_bg_image_path = persisted_window_bg
        self._chart_bg_image_path = persisted_chart_bg
        self._update_bg_image_labels()

        self.automate_toggle.blockSignals(False)
        self.automation_stoploss_input.blockSignals(False)
        self.max_profit_giveback_input.blockSignals(False)
        self.max_giveback_atr_reversal_check.blockSignals(False)
        self.max_giveback_ema_cross_check.blockSignals(False)
        self.max_giveback_atr_divergence_check.blockSignals(False)
        self.max_giveback_range_breakout_check.blockSignals(False)
        self.automation_route_combo.blockSignals(False)
        self.atr_base_ema_input.blockSignals(False)
        self.atr_distance_input.blockSignals(False)
        self.cvd_atr_distance_input.blockSignals(False)
        self.cvd_ema_gap_input.blockSignals(False)
        self.setup_cvd_value_mode_combo.blockSignals(False)
        self.signal_filter_combo.blockSignals(False)
        self.atr_marker_filter_combo.blockSignals(False)
        self.setup_signal_filter_combo.blockSignals(False)
        self.setup_atr_marker_filter_combo.blockSignals(False)
        self.range_lookback_input.blockSignals(False)  # 🆕 NEW
        self.breakout_switch_mode_combo.blockSignals(False)
        self.atr_skip_limit_input.blockSignals(False)
        self.deploy_mode_combo.blockSignals(False)
        self.min_confidence_input.blockSignals(False)
        self.canary_ratio_input.blockSignals(False)
        self.hide_simulator_btn_check.blockSignals(False)
        self.chop_filter_atr_reversal_check.blockSignals(False)
        self.chop_filter_ema_cross_check.blockSignals(False)
        self.chop_filter_atr_divergence_check.blockSignals(False)
        self.open_drive_enabled_check.blockSignals(False)
        self.open_drive_time_hour_input.blockSignals(False)
        self.open_drive_time_minute_input.blockSignals(False)
        self.open_drive_stack_enabled_check.blockSignals(False)
        self.open_drive_max_profit_giveback_input.blockSignals(False)
        self.breakout_min_consol_input.blockSignals(False)
        self.breakout_min_consol_adx_input.blockSignals(False)
        self.cvd_range_lookback_input.blockSignals(False)
        self.cvd_breakout_buffer_input.blockSignals(False)
        self.cvd_min_consol_bars_input.blockSignals(False)
        self.cvd_max_range_ratio_input.blockSignals(False)
        self.cvd_breakout_min_adx_input.blockSignals(False)
        self.chart_line_width_input.blockSignals(False)
        self.chart_line_opacity_input.blockSignals(False)
        self.confluence_line_width_input.blockSignals(False)
        self.confluence_line_opacity_input.blockSignals(False)
        self.ema_line_opacity_input.blockSignals(False)
        self.show_grid_lines_check.blockSignals(False)
        self.window_bg_upload_btn.blockSignals(False)
        self.window_bg_clear_btn.blockSignals(False)
        self.chart_bg_upload_btn.blockSignals(False)
        self.chart_bg_clear_btn.blockSignals(False)
        self.vwap_checkbox.blockSignals(False)
        for cb in self.setup_ema_default_checks.values():
            cb.blockSignals(False)

        self._apply_visual_settings()
        self._update_atr_reversal_markers()
        self._setup_values_ready = True
        self._on_automation_settings_changed()

    def _persist_setup_values(self):
        if not getattr(self, "_setup_values_ready", False):
            return

        key_prefix = self._settings_key_prefix()
        global_key_prefix = self._global_settings_key_prefix()

        values_to_persist = {
            "enabled": self.automate_toggle.isChecked(),
            "stoploss_points": int(self.automation_stoploss_input.value()),
            "max_profit_giveback_points": int(self.max_profit_giveback_input.value()),
            "max_profit_giveback_strategies": self._selected_max_giveback_strategies(),
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "atr_base_ema": int(self.atr_base_ema_input.value()),
            "atr_distance": float(self.atr_distance_input.value()),
            "cvd_atr_distance": float(self.cvd_atr_distance_input.value()),
            "cvd_ema_gap": int(self.cvd_ema_gap_input.value()),
            "cvd_value_mode": self.setup_cvd_value_mode_combo.currentData() or self.CVD_VALUE_MODE_RAW,
            "signal_filter": self._selected_signal_filter(),
            "signal_filters": self._selected_signal_filters(),
            "atr_marker_filter": self.atr_marker_filter_combo.currentData() or self.ATR_MARKER_CONFLUENCE_ONLY,
            # 🆕 Persist range breakout settings
            "range_lookback": int(self.range_lookback_input.value()),
            "breakout_switch_mode": self._selected_breakout_switch_mode(),
            "atr_skip_limit": int(self.atr_skip_limit_input.value()),
            "deploy_mode": self.deploy_mode_combo.currentData() or "canary",
            "min_confidence_for_live": float(self.min_confidence_input.value()),
            "canary_live_ratio": float(self.canary_ratio_input.value()),
            "hide_simulator_button": self.hide_simulator_btn_check.isChecked(),
            "stacker_enabled": self.stacker_enabled_check.isChecked(),
            "stacker_step_points": int(self.stacker_step_input.value()),
            "stacker_max_stacks": int(self.stacker_max_input.value()),
            "open_drive_enabled": self.open_drive_enabled_check.isChecked(),
            "open_drive_entry_hour": int(self.open_drive_time_hour_input.value()),
            "open_drive_entry_minute": int(self.open_drive_time_minute_input.value()),
            "open_drive_stack_enabled": self.open_drive_stack_enabled_check.isChecked(),
            "open_drive_max_profit_giveback_points": int(self.open_drive_max_profit_giveback_input.value()),
            # 🆕 Chop filter per-strategy
            "chop_filter_atr_reversal": self.chop_filter_atr_reversal_check.isChecked(),
            "chop_filter_ema_cross": self.chop_filter_ema_cross_check.isChecked(),
            "chop_filter_atr_divergence": self.chop_filter_atr_divergence_check.isChecked(),
            # 🆕 Breakout consolidation
            "breakout_min_consolidation_minutes": int(self.breakout_min_consol_input.value()),
            "breakout_min_consolidation_adx": float(self.breakout_min_consol_adx_input.value()),
            "cvd_range_lookback_bars": int(self.cvd_range_lookback_input.value()),
            "cvd_breakout_buffer": float(self.cvd_breakout_buffer_input.value()),
            "cvd_min_consol_bars": int(self.cvd_min_consol_bars_input.value()),
            "cvd_max_range_ratio": float(self.cvd_max_range_ratio_input.value()),
            "cvd_breakout_min_adx": float(self.cvd_breakout_min_adx_input.value()),
            "chart_line_width": float(self.chart_line_width_input.value()),
            "chart_line_opacity": float(self.chart_line_opacity_input.value()),
            "confluence_line_width": float(self.confluence_line_width_input.value()),
            "confluence_line_opacity": float(self.confluence_line_opacity_input.value()),
            "ema_line_opacity": float(self.ema_line_opacity_input.value()),
            "show_grid_lines": self.show_grid_lines_check.isChecked(),
            "chart_line_color": self._chart_line_color,
            "price_line_color": self._price_line_color,
            "confluence_short_color": self._confluence_short_color,
            "confluence_long_color": self._confluence_long_color,
            "window_background_image_path": self._window_bg_image_path,
            "chart_background_image_path": self._chart_bg_image_path,
            "show_vwap": self.vwap_checkbox.isChecked(),

        }

        for period, cb in self.setup_ema_default_checks.items():
            values_to_persist[f"ema_default_{period}"] = cb.isChecked()

        for name, value in values_to_persist.items():
            self._settings.setValue(f"{key_prefix}/{name}", value)
            self._settings.setValue(f"{global_key_prefix}/{name}", value)

        self._settings.sync()
        self._write_setup_json(values_to_persist)

    # =========================================================================
    # SECTION 4: SETTINGS CHANGE HANDLERS
    # =========================================================================
    def _on_stacker_settings_changed(self, *_):
        """Persist stacker settings and reset any live stacker state."""
        self._live_stacker_state = None
        self._persist_setup_values()

    def _on_open_drive_settings_changed(self, *_):
        self._live_stacker_state = None
        self._persist_setup_values()
        self._load_and_plot(force=True)

    def _on_automation_settings_changed(self, *_):
        self._persist_setup_values()
        self.automation_state_signal.emit({
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "enabled": self.automate_toggle.isChecked(),
            "stoploss_points": float(self.automation_stoploss_input.value()),
            "max_profit_giveback_points": float(self.max_profit_giveback_input.value()),
            "max_profit_giveback_strategies": self._selected_max_giveback_strategies(),
            "open_drive_max_profit_giveback_points": float(self.open_drive_max_profit_giveback_input.value()),
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "signal_filter": self._selected_signal_filter(),
            "signal_filters": self._selected_signal_filters(),
        })
        self._live_stacker_state = None

    def reset_stacker(self):
        """Called by coordinator when anchor trade fully exits."""
        self._live_stacker_state = None
        logger.debug(
            "[STACKER] State reset after anchor exit for token=%s",
            self.instrument_token,
        )
    def _on_signal_filter_changed(self, *_):
        if getattr(self, "_syncing_signal_filters", False):
            return
        self._sync_signal_filter_combos(self.signal_filter_combo, self.setup_signal_filter_combo)
        self._update_atr_reversal_markers()
        self._on_automation_settings_changed()

    def _on_setup_signal_filter_changed(self, *_):
        if getattr(self, "_syncing_signal_filters", False):
            return
        self._sync_signal_filter_combos(self.setup_signal_filter_combo, self.signal_filter_combo)
        self._update_atr_reversal_markers()
        self._on_automation_settings_changed()

    def _on_chop_filter_settings_changed(self, *_):
        """Handle chop filter and consolidation settings changes."""
        self._chop_filter_atr_reversal = self.chop_filter_atr_reversal_check.isChecked()
        self._chop_filter_ema_cross = self.chop_filter_ema_cross_check.isChecked()
        self._chop_filter_atr_divergence = self.chop_filter_atr_divergence_check.isChecked()
        self._breakout_min_consolidation_minutes = self.breakout_min_consol_input.value()
        self._breakout_min_consolidation_adx = float(self.breakout_min_consol_adx_input.value())
        self._persist_setup_values()
        if self._breakout_min_consolidation_minutes > 0 or self._breakout_min_consolidation_adx > 0:
            self._load_and_plot(force=True)

    def _on_breakout_settings_changed(self, *_):
        """Handle range breakout settings changes"""
        self._persist_setup_values()
        # Force reload to apply new range lookback
        self._load_and_plot(force=True)

    def _on_cvd_value_mode_changed(self, *_):
        """Toggle between raw and session-volume-normalized CVD series."""
        self._persist_setup_values()
        self._load_and_plot(force=True)

    def _on_governance_settings_changed(self, *_):
        self.signal_governance.deploy_mode = self.deploy_mode_combo.currentData() or "canary"
        self.signal_governance.min_confidence_for_live = float(self.min_confidence_input.value())
        self.signal_governance.canary_live_ratio = float(self.canary_ratio_input.value())
        self._persist_setup_values()

    def _on_ema_toggled(self, period: int, checked: bool):
        """Toggle EMA visibility"""
        if hasattr(self, "setup_ema_default_checks") and period in self.setup_ema_default_checks:
            setup_cb = self.setup_ema_default_checks[period]
            if setup_cb.isChecked() != checked:
                setup_cb.blockSignals(True)
                setup_cb.setChecked(checked)
                setup_cb.blockSignals(False)

        if hasattr(self, "chart_line_width_input"):
            self._persist_setup_values()

        self._refresh_plot_only()

    def _on_vwap_toggled(self, checked: bool):
        if hasattr(self, "chart_line_width_input"):
            self._persist_setup_values()
        if not checked:
            self.price_vwap_curve.clear()
        self._refresh_plot_only()

    def _on_focus_mode_changed(self, enabled: bool):
        self.btn_focus.setText("2D" if enabled else "1D")
        if self.cvd_engine:
            if enabled:
                self.cvd_engine.set_mode(CVDMode.NORMAL)
            else:
                self.cvd_engine.set_mode(CVDMode.SINGLE_DAY)

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
        self._current_session_volume_scale = 1.0
        self.all_timestamps.clear()
        self._load_and_plot(force=True)

    def _on_timeframe_combo_changed(self, index: int):
        minutes = self.timeframe_combo.itemData(index)
        if minutes is None:
            return
        self._on_timeframe_changed(int(minutes))

    def _on_timeframe_changed(self, minutes: int):
        if self.timeframe_minutes == minutes:
            return

        self.timeframe_minutes = minutes
        self.strategy_detector.timeframe_minutes = minutes

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
        self._clear_simulation_markers()
        self.cvd_ema10_curve.clear()
        self.cvd_ema21_curve.clear()
        self.cvd_ema51_curve.clear()
        self.price_ema10_curve.clear()
        self.price_ema21_curve.clear()
        self.price_ema51_curve.clear()
        self.price_vwap_curve.clear()
        self.all_timestamps.clear()
        self._live_tick_points.clear()
        self._live_price_points.clear()
        self._live_cvd_offset = None
        self._current_session_last_cvd_value = None
        self._current_session_volume_scale = 1.0
        self._last_plot_x_indices = []
        self._load_and_plot(force=True)

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

        if not self.btn_focus.isChecked():
            # Single-day mode: find nearest timestamp by session minute
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

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            combo = self._resolve_signal_filter_combo_from_object(obj)
            if combo is not None and obj is combo.lineEdit():
                QTimer.singleShot(0, combo.showPopup)
                return True
        return super().eventFilter(obj, event)

    def _resolve_signal_filter_combo_from_object(self, obj):
        combos = [
            getattr(self, "signal_filter_combo", None),
            getattr(self, "setup_signal_filter_combo", None),
        ]
        for combo in combos:
            if combo is None:
                continue
            if obj is combo:
                return combo
            line_edit = combo.lineEdit()
            if line_edit is not None and obj is line_edit:
                return combo
        return None

    # =========================================================================
    # SECTION 5: SETTINGS HELPERS & STRATEGY SELECTORS
    # =========================================================================

    def _signal_filter_options(self) -> list[tuple[str, str]]:
        return [
            ("Select All", self.SIGNAL_FILTER_ALL),
            ("ATR Reversal", self.SIGNAL_FILTER_ATR_ONLY),
            ("EMA Cross", self.SIGNAL_FILTER_EMA_CROSS_ONLY),
            ("Range Breakout", self.SIGNAL_FILTER_BREAKOUT_ONLY),
            ("ATR Divergence", self.SIGNAL_FILTER_OTHERS),
            ("Open Drive", self.SIGNAL_FILTER_OPEN_DRIVE_ONLY),
        ]

    def _strategy_filter_values(self) -> list[str]:
        return [
            self.SIGNAL_FILTER_ATR_ONLY,
            self.SIGNAL_FILTER_EMA_CROSS_ONLY,
            self.SIGNAL_FILTER_BREAKOUT_ONLY,
            self.SIGNAL_FILTER_OTHERS,
            self.SIGNAL_FILTER_OPEN_DRIVE_ONLY,
        ]

    def _init_signal_filter_combo(self, combo: QComboBox):
        combo.setEditable(True)
        combo.lineEdit().setReadOnly(True)
        combo.lineEdit().setAlignment(Qt.AlignLeft)
        combo.clear()
        combo.setStyleSheet(combo.styleSheet() + """
            QComboBox {
                min-height: 28px;
                padding: 2px 10px;
            }
            QComboBox QAbstractItemView::item {
                min-height: 30px;
                padding: 6px 10px;
            }
        """)

        for label, value in self._signal_filter_options():
            combo.addItem(label, value)
            idx = combo.model().index(combo.count() - 1, 0)
            combo.model().setData(idx, Qt.Checked, Qt.CheckStateRole)

        combo.lineEdit().installEventFilter(self)
        combo.installEventFilter(self)
        combo.view().pressed.connect(lambda index, c=combo: self._toggle_signal_filter_item(c, index))
        combo.view().setMinimumWidth(max(combo.width() + 70, 230))
        self._refresh_signal_filter_combo_text(combo)

    def _toggle_signal_filter_item(self, combo: QComboBox, index):
        value = combo.itemData(index.row())
        state = combo.model().data(index, Qt.CheckStateRole)
        next_state = Qt.Unchecked if state == Qt.Checked else Qt.Checked

        if value == self.SIGNAL_FILTER_ALL:
            for i in range(combo.count()):
                idx = combo.model().index(i, 0)
                combo.model().setData(idx, next_state, Qt.CheckStateRole)
            if next_state == Qt.Unchecked:
                combo.model().setData(combo.model().index(1, 0), Qt.Checked, Qt.CheckStateRole)
        else:
            combo.model().setData(index, next_state, Qt.CheckStateRole)

            # Keep at least one strategy selected.
            if not self._checked_signal_filters(combo):
                combo.model().setData(index, Qt.Checked, Qt.CheckStateRole)

            self._sync_select_all_check_state(combo)

        self._refresh_signal_filter_combo_text(combo)
        QTimer.singleShot(0, combo.showPopup)
        if combo is self.signal_filter_combo:
            self._on_signal_filter_changed()
        elif combo is getattr(self, "setup_signal_filter_combo", None):
            self._on_setup_signal_filter_changed()

    def _sync_select_all_check_state(self, combo: QComboBox):
        all_idx = combo.findData(self.SIGNAL_FILTER_ALL)
        if all_idx < 0:
            return
        all_selected = len(self._checked_signal_filters(combo)) == len(self._strategy_filter_values())
        combo.model().setData(
            combo.model().index(all_idx, 0),
            Qt.Checked if all_selected else Qt.Unchecked,
            Qt.CheckStateRole,
        )

    def _checked_signal_filters(self, combo: QComboBox) -> list[str]:
        selected: list[str] = []
        for i in range(combo.count()):
            idx = combo.model().index(i, 0)
            if combo.model().data(idx, Qt.CheckStateRole) == Qt.Checked:
                value = combo.itemData(i)
                if value and value != self.SIGNAL_FILTER_ALL:
                    selected.append(value)
        return selected

    def _set_checked_signal_filters(self, combo: QComboBox, selected_filters: list[str]):
        selected_set = set(selected_filters)
        for i in range(combo.count()):
            idx = combo.model().index(i, 0)
            value = combo.itemData(i)
            if value == self.SIGNAL_FILTER_ALL:
                continue
            combo.model().setData(idx, Qt.Checked if value in selected_set else Qt.Unchecked, Qt.CheckStateRole)

        if not self._checked_signal_filters(combo) and combo.count() > 1:
            combo.model().setData(combo.model().index(1, 0), Qt.Checked, Qt.CheckStateRole)
        self._sync_select_all_check_state(combo)
        self._refresh_signal_filter_combo_text(combo)

    def _refresh_signal_filter_combo_text(self, combo: QComboBox):
        selected = self._checked_signal_filters(combo)
        total = len(self._strategy_filter_values())
        if len(selected) == total:
            text = "All Signals"
        elif len(selected) == 1:
            idx = combo.findData(selected[0])
            text = combo.itemText(idx) if idx >= 0 else "1 selected"
        else:
            idx = combo.findData(selected[0]) if selected else -1
            first = combo.itemText(idx) if idx >= 0 else "Strategies"
            text = f"{first} +..."
        combo.lineEdit().setText(text)

    def _sync_signal_filter_combos(self, source: QComboBox, target: QComboBox):
        self._syncing_signal_filters = True
        try:
            self._set_checked_signal_filters(target, self._checked_signal_filters(source))
        finally:
            self._syncing_signal_filters = False

    def _coerce_signal_filters(self, value) -> list[str]:
        if value in (None, "", self.SIGNAL_FILTER_ALL):
            return self._strategy_filter_values()
        if isinstance(value, (list, tuple, set)):
            valid = set(self._strategy_filter_values())
            selected = [str(v) for v in value if str(v) in valid]
            return selected or self._strategy_filter_values()
        value_str = str(value)
        if value_str == self.SIGNAL_FILTER_ALL:
            return self._strategy_filter_values()
        return [value_str]

    def _selected_signal_filter(self) -> str:
        selected = self._selected_signal_filters()
        if len(selected) == 1:
            return selected[0]
        return self.SIGNAL_FILTER_ALL

    def _selected_signal_filters(self) -> list[str]:
        return self._checked_signal_filters(self.signal_filter_combo)

    def _selected_breakout_switch_mode(self) -> str:
        return self.breakout_switch_mode_combo.currentData() or self.BREAKOUT_SWITCH_ADAPTIVE

    @classmethod
    def _max_giveback_strategy_defaults(cls) -> tuple[str, ...]:
        return (
            cls.MAX_GIVEBACK_STRATEGY_ATR_REVERSAL,
            cls.MAX_GIVEBACK_STRATEGY_EMA_CROSS,
            cls.MAX_GIVEBACK_STRATEGY_ATR_DIVERGENCE,
            cls.MAX_GIVEBACK_STRATEGY_RANGE_BREAKOUT,
            cls.MAX_GIVEBACK_STRATEGY_CVD_RANGE_BREAKOUT,
        )

    def _selected_max_giveback_strategies(self) -> list[str]:
        selected: list[str] = []
        if self.max_giveback_atr_reversal_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_ATR_REVERSAL)
        if self.max_giveback_ema_cross_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_EMA_CROSS)
        if self.max_giveback_atr_divergence_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_ATR_DIVERGENCE)
        if self.max_giveback_range_breakout_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_RANGE_BREAKOUT)
            selected.append(self.MAX_GIVEBACK_STRATEGY_CVD_RANGE_BREAKOUT)
        return selected

    def _apply_max_giveback_strategy_selection(self, strategies: list[str]):
        selected = set(strategies or [])
        self.max_giveback_atr_reversal_check.setChecked(
            self.MAX_GIVEBACK_STRATEGY_ATR_REVERSAL in selected
        )
        self.max_giveback_ema_cross_check.setChecked(
            self.MAX_GIVEBACK_STRATEGY_EMA_CROSS in selected
        )
        self.max_giveback_atr_divergence_check.setChecked(
            self.MAX_GIVEBACK_STRATEGY_ATR_DIVERGENCE in selected
        )
        self.max_giveback_range_breakout_check.setChecked(
            (self.MAX_GIVEBACK_STRATEGY_RANGE_BREAKOUT in selected)
            or (self.MAX_GIVEBACK_STRATEGY_CVD_RANGE_BREAKOUT in selected)
        )

    def _enabled_ema_periods(self) -> set[int]:
        """Return EMA periods currently enabled via checkboxes"""
        return {
            period for period, cb in self.ema_checkboxes.items()
            if cb.isChecked()
        }

    # =========================================================================
    # SECTION 6: DATA LOADING & FETCHING
    # =========================================================================

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

        focus_mode = not self.btn_focus.isChecked()

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

    # =========================================================================
    # SECTION 7: CHART RENDERING & PLOTTING
    # =========================================================================

    def _plot_data(self, cvd_df: pd.DataFrame, price_df: pd.DataFrame, prev_close: float):
        focus_mode = not self.btn_focus.isChecked()

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
        self._clear_simulation_markers()
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
        self.all_volume_data = []  # 🆕 NEW - Store volume data
        self._last_plot_x_indices = []

        x_offset = 0
        sessions = sorted(cvd_df["session"].unique())
        self._current_session_last_cvd_value = None
        self._current_session_volume_scale = 1.0
        self._live_cvd_offset = None

        for i, sess in enumerate(sessions):
            df_cvd_sess = cvd_df[cvd_df["session"] == sess]
            df_price_sess = price_df[price_df["session"] == sess]

            cvd_y_raw = df_cvd_sess["close"].to_numpy(dtype=float)
            cvd_high_raw = df_cvd_sess["high"].to_numpy(dtype=float) if "high" in df_cvd_sess.columns else cvd_y_raw
            cvd_low_raw = df_cvd_sess["low"].to_numpy(dtype=float) if "low" in df_cvd_sess.columns else cvd_y_raw
            price_y_raw = df_price_sess["close"].values
            price_high_raw = df_price_sess["high"].values
            price_low_raw = df_price_sess["low"].values
            volume_raw = df_price_sess["volume"].values if "volume" in df_price_sess.columns else np.ones_like(
                price_y_raw)  # 🆕 NEW
            cumulative_volume = np.cumsum(volume_raw.astype(float)) if len(volume_raw) else np.array([], dtype=float)

            cvd_mode = self.setup_cvd_value_mode_combo.currentData() or self.CVD_VALUE_MODE_RAW
            if cvd_mode == self.CVD_VALUE_MODE_NORMALIZED and len(cvd_y_raw):
                safe_denominator = np.where(cumulative_volume > 0, cumulative_volume, 1.0)
                cvd_y_raw = cvd_y_raw / safe_denominator
                cvd_high_raw = cvd_high_raw / safe_denominator
                cvd_low_raw = cvd_low_raw / safe_denominator
                if i == 0 and len(sessions) == 2 and self.btn_focus.isChecked():
                    prev_close = float(cvd_y_raw[-1])

            # Rebasing logic for CVD
            if i == 0 and len(sessions) == 2 and self.btn_focus.isChecked():
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
                self._current_session_volume_scale = float(cumulative_volume[-1]) if len(cumulative_volume) else 1.0

            self.all_timestamps.extend(df_cvd_sess.index.tolist())
            self.all_cvd_data.extend(cvd_y.tolist())
            self.all_cvd_high_data.extend(cvd_high.tolist())
            self.all_cvd_low_data.extend(cvd_low.tolist())
            self.all_price_data.extend(price_y.tolist())
            self.all_price_high_data.extend(price_high_raw.tolist())
            self.all_price_low_data.extend(price_low_raw.tolist())
            self.all_volume_data.extend(volume_raw.tolist())  # 🆕 NEW

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

        # 🔥 PLOT INSTITUTIONAL EMAS + markers from current in-memory data
        if len(self.all_cvd_data) > 0:
            self._refresh_plot_only()

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

    def _refresh_plot_only(self):
        """Refresh chart overlays from in-memory data without reloading sessions or touching trade state."""
        if not self.all_timestamps or not self.all_cvd_data or not self.all_price_data:
            return

        focus_mode = not self.btn_focus.isChecked()
        if focus_mode:
            x_indices = [self._time_to_session_index(ts) for ts in self.all_timestamps]
        else:
            x_indices = list(range(len(self.all_timestamps)))

        self._last_plot_x_indices = list(x_indices)
        enabled_emas = self._enabled_ema_periods()

        cvd_data_array = np.array(self.all_cvd_data)
        price_data_array = np.array(self.all_price_data)

        if 10 in enabled_emas:
            self.cvd_ema10_curve.setData(x_indices, calculate_ema(cvd_data_array, 10))
        else:
            self.cvd_ema10_curve.clear()

        if 21 in enabled_emas:
            self.cvd_ema21_curve.setData(x_indices, calculate_ema(cvd_data_array, 21))
        else:
            self.cvd_ema21_curve.clear()

        if 51 in enabled_emas:
            self.cvd_ema51_curve.setData(x_indices, calculate_ema(cvd_data_array, 51))
        else:
            self.cvd_ema51_curve.clear()

        if 10 in enabled_emas:
            self.price_ema10_curve.setData(x_indices, calculate_ema(price_data_array, 10))
        else:
            self.price_ema10_curve.clear()

        if 21 in enabled_emas:
            self.price_ema21_curve.setData(x_indices, calculate_ema(price_data_array, 21))
        else:
            self.price_ema21_curve.clear()

        if 51 in enabled_emas:
            self.price_ema51_curve.setData(x_indices, calculate_ema(price_data_array, 51))
        else:
            self.price_ema51_curve.clear()

        if self.vwap_checkbox.isChecked() and self.all_volume_data:
            session_keys = [ts.date() for ts in self.all_timestamps]
            volume_data_array = np.array(self.all_volume_data)
            self.price_vwap_curve.setData(x_indices, calculate_vwap(price_data_array, volume_data_array, session_keys))
            self.price_vwap_curve.setOpacity(self._ema_line_opacity)
        else:
            self.price_vwap_curve.clear()

        for ema_period, cb in self.ema_checkboxes.items():
            opacity = self._ema_line_opacity if cb.isChecked() else 0.0
            if ema_period == 10:
                self.price_ema10_curve.setOpacity(opacity)
                self.cvd_ema10_curve.setOpacity(opacity)
            elif ema_period == 21:
                self.price_ema21_curve.setOpacity(opacity)
                self.cvd_ema21_curve.setOpacity(opacity)
            elif ema_period == 51:
                self.price_ema51_curve.setOpacity(opacity)
                self.cvd_ema51_curve.setOpacity(opacity)

        self._update_atr_reversal_markers()
        self._update_ema_legends()

    def _update_ema_legends(self):
        """EMA legends are disabled to keep chart area unobstructed."""
        return

    def _plot_live_ticks_only(self):
        """Plot tick-level CVD overlay on top of minute candles."""
        if not self._live_tick_points:
            self.today_tick_curve.clear()
            self.price_today_tick_curve.clear()
            return

        if self._current_session_start_ts is None:
            return

        focus_mode = not self.btn_focus.isChecked()
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
            self.today_tick_curve.setPen(self._live_tick_cvd_pen)
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
            self.price_today_tick_curve.setPen(self._live_tick_price_pen)
            self.price_today_tick_curve.setData(
                x_arr[valid_price_mask],
                price_arr[valid_price_mask],
                connect='finite'
            )
            # Update live dot with last valid price point
            last_valid_idx = np.where(valid_price_mask)[0][-1]
            self.price_live_dot.setData([x_arr[last_valid_idx]], [price_arr[last_valid_idx]])

    # =========================================================================
    # SECTION 8: LIVE CVD TICK PROCESSING
    # =========================================================================

    def _on_cvd_tick_update(self, token: int, cvd_value: float, last_price: float):
        if not self.isVisible():
            return

        if token != self.instrument_token or not self.live_mode:
            return

        self._cvd_tick_received.emit(cvd_value, last_price)

    def _apply_cvd_tick(self, cvd_value: float, last_price: float):
        """Slot — always called on the GUI thread via queued signal connection."""
        ts = datetime.now()

        # Freeze live-dot motion outside market hours.
        # Some feeds keep pushing ticks after 15:30; if we keep updating the
        # timestamp, the blinking dot drifts right and creates empty chart space.
        if ts.time() < TRADING_START or ts.time() > TRADING_END:
            return

        cvd_mode = self.setup_cvd_value_mode_combo.currentData() or self.CVD_VALUE_MODE_RAW
        transformed_cvd = float(cvd_value)
        if cvd_mode == self.CVD_VALUE_MODE_NORMALIZED:
            transformed_cvd = transformed_cvd / max(float(self._current_session_volume_scale), 1.0)

        # Align live tick CVD level with historical curve to avoid visual jump.
        if self._live_cvd_offset is None:
            if self._current_session_last_cvd_value is not None:
                self._live_cvd_offset = float(self._current_session_last_cvd_value) - transformed_cvd
            else:
                self._live_cvd_offset = 0.0

        plotted_cvd = transformed_cvd + float(self._live_cvd_offset)
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

    # =========================================================================
    # SECTION 9: LIVE REFRESH TIMER & DOT BLINK
    # =========================================================================

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

    def _blink_dot(self):
        self._dot_visible = not self._dot_visible
        alpha = 220 if self._dot_visible else 60
        self.live_dot.setBrush(pg.mkBrush(38, 166, 154, alpha))
        self.price_live_dot.setBrush(pg.mkBrush(255, 229, 127, alpha))

    # =========================================================================
    # SECTION 10: COORDINATE & AXIS HELPERS
    # =========================================================================

    def _time_to_session_index(self, ts: datetime) -> int:
        """
        Converts a timestamp to a fixed session index (0–374)
        """
        session_start = ts.replace(
            hour=9, minute=15, second=0, microsecond=0
        )
        delta_minutes = int((ts - session_start).total_seconds() / 60)
        return max(0, min(delta_minutes, MINUTES_PER_SESSION - 1))

    def _fix_axis_after_show(self):
        bottom_axis = self.plot.getAxis("bottom")
        bottom_axis.setHeight(32)
        bottom_axis.update()
        self.plot.updateGeometry()
        self.price_plot.updateGeometry()

    # =========================================================================
    # SECTION 11: EXPORT & UTILITIES
    # =========================================================================

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

    @staticmethod
    def _display_symbol_for_title(symbol: str) -> str:
        """Hide FUT suffix/token from the window title while keeping internal symbol unchanged."""
        display_symbol = re.sub(r"[-_ ]?FUT$", "", symbol, flags=re.IGNORECASE)
        display_symbol = re.sub(r"\bFUT\b", "", display_symbol, flags=re.IGNORECASE)
        return re.sub(r"\s{2,}", " ", display_symbol).strip() or symbol

    # =========================================================================
    # SECTION 12: QT EVENT OVERRIDES
    # =========================================================================

    def keyPressEvent(self, event):
        """Keyboard shortcuts for date navigation and simulator execution."""
        focus_widget = self.focusWidget()
        if isinstance(focus_widget, (QSpinBox, QDoubleSpinBox, QComboBox)):
            super().keyPressEvent(event)
            return

        if event.modifiers() == Qt.NoModifier:
            if event.key() == Qt.Key_Left:
                self.navigator.btn_back.click()
                event.accept()
                return

            if event.key() == Qt.Key_Right:
                if self.navigator.btn_forward.isEnabled():
                    self.navigator.btn_forward.click()
                event.accept()
                return

            if event.key() == Qt.Key_Space:
                if self.simulator_run_btn.isEnabled() and self.simulator_run_btn.isVisible():
                    self.simulator_run_btn.click()
                event.accept()
                return

        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fix_axis_after_show)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.plot.update()
        self.price_plot.update()

    def changeEvent(self, event):
        # Intentionally NOT stopping/starting the refresh_timer on activation changes.
        # During automation the dialog loses/regains focus constantly; toggling the
        # timer here caused a racing condition where _load_and_plot was re-entered
        # before _is_loading could be set, crashing the app within seconds.
        super().changeEvent(event)

    def closeEvent(self, event):
        self._persist_setup_values()
        try:
            if hasattr(self, "_fetch_thread") and self._fetch_thread.isRunning():
                self._fetch_thread.quit()
                self._fetch_thread.wait(2000)
        except Exception:
            pass
        super().closeEvent(event)

    def _check_and_emit_stack_signals(
            self,
            side: str,
            strategy_type: str,
            current_price: float,
            current_bar_idx: int,
            closed_bar_ts: str,
            x_arr_val: float,
    ):
        """
        On each closed bar:
          1. Check if any stacked positions should be UNWOUND (price crossed back
             through their entry) — emit unwind exit signals LIFO.
          2. Check if new stacks should be ADDED (price moved further in favour).

        The anchor position is untouched by unwind logic — it exits only on its
        own strategy exit signal.
        """

        if not getattr(self, "stacker_enabled_check", None):
            return
        if not self.stacker_enabled_check.isChecked():
            return

        state = self._live_stacker_state
        if state is None:
            return

        if state.signal_side != side:
            return

        # ── 1. LIFO UNWIND: exit stacks whose entry price was breached ──────
        to_unwind = state.stacks_to_unwind(current_price)
        if to_unwind:
            for entry in to_unwind:
                unwind_ts = f"{closed_bar_ts}_unwind{entry.stack_number}"
                unwind_payload = {
                    "instrument_token": self.instrument_token,
                    "symbol": self.symbol,
                    "signal_side": side,
                    "signal_type": strategy_type,
                    "signal_x": x_arr_val,
                    "price_close": current_price,
                    "stoploss_points": float(self.automation_stoploss_input.value()),
                    "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
                    "timestamp": unwind_ts,
                    "is_stack_unwind": True,          # ← coordinator routes this as EXIT
                    "stack_number": entry.stack_number,
                    "anchor_price": state.anchor_entry_price,
                    "stack_entry_price": entry.entry_price,
                }
                logger.info(
                    "[STACKER] Unwind stack #%d: token=%s side=%s entry=%.2f current=%.2f",
                    entry.stack_number,
                    self.instrument_token,
                    side,
                    entry.entry_price,
                    current_price,
                )
                QTimer.singleShot(0, lambda p=unwind_payload: self.automation_signal.emit(p))

            state.remove_stacks(to_unwind)

        # ── 2. STACK ADD: add new positions if price moved further in favour ─
        while state.should_add_stack(current_price):
            state.add_stack(entry_price=current_price, bar_idx=current_bar_idx)
            stack_num = len(state.stack_entries)

            stack_ts = f"{closed_bar_ts}_stack{stack_num}"

            payload = {
                "instrument_token": self.instrument_token,
                "symbol": self.symbol,
                "signal_side": side,
                "signal_type": strategy_type,
                "signal_x": x_arr_val,
                "price_close": current_price,
                "stoploss_points": float(self.automation_stoploss_input.value()),
                "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
                "timestamp": stack_ts,
                "is_stack": True,
                "stack_number": stack_num,
                "anchor_price": state.anchor_entry_price,
            }

            logger.info(
                "[STACKER] Stack #%d fired: token=%s side=%s price=%.2f (anchor=%.2f, step=%.0f)",
                stack_num,
                self.instrument_token,
                side,
                current_price,
                state.anchor_entry_price,
                state.step_points,
            )

            QTimer.singleShot(0, lambda p=payload: self.automation_signal.emit(p))

            if not state.can_stack_more:
                break
