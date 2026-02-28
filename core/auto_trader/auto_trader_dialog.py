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
from PySide6.QtGui import QColor, QFontMetrics
from pyqtgraph import AxisItem, TextItem

from kiteconnect import KiteConnect
from core.cvd.cvd_historical import CVDHistoricalBuilder
from core.cvd.cvd_mode import CVDMode
from core.auto_trader.strategy_signal_detector import StrategySignalDetector
from core.auto_trader.constants import TRADING_START, TRADING_END, MINUTES_PER_SESSION
from core.auto_trader.data_worker import _DataFetchWorker, load_tick_csv, build_price_cvd_from_ticks
from core.auto_trader.date_navigator import DateNavigator
from core.auto_trader.setup_panel import SetupPanelMixin
from core.auto_trader.settings_manager import SetupSettingsMigrationMixin
from core.auto_trader.signal_renderer import SignalRendererMixin
from core.auto_trader.simulator import SimulatorMixin
from core.auto_trader.indicators import calculate_ema, calculate_vwap, calculate_atr, compute_adx, build_slope_direction_masks, is_chop_regime
from core.auto_trader.hybrid_exit_engine import (
    HybridExitConfig,
    HybridExitEngine,
    HybridExitState,
)
from core.auto_trader.signal_governance import SignalGovernance
from core.auto_trader.stacker import StackerState
from utils.cpr_calculator import CPRCalculator
from core.auto_trader.regime_engine import RegimeEngine, RegimeConfig
from core.auto_trader.regime_tab_mixin import RegimeTabMixin
from core.auto_trader.regime_indicator import RegimeIndicator
from core.auto_trader.trend_change_markers import TrendChangeMarkersMixin
from core.auto_trader.auto_trader_theme import (
    apply_dialog_theme,
    COMPACT_COMBO_STYLE,
    COMPACT_SPINBOX_STYLE,
    COMPACT_TOGGLE_STYLE,
    SignalFeedSidebar,
    StatusBar,
    MetricTile,
    PanelHeader,
    style_plot_widget,
    C,
)
logger = logging.getLogger(__name__)


# =============================================================================
# Auto Trader DIalog
# =============================================================================

class AutoTraderDialog(TrendChangeMarkersMixin, RegimeTabMixin, SetupPanelMixin, SetupSettingsMigrationMixin, SignalRendererMixin, SimulatorMixin, QDialog):
    REFRESH_INTERVAL_MS = 3000
    LIVE_TICK_MAX_POINTS = 6000
    LIVE_TICK_REPAINT_MS = 80
    LIVE_TICK_DOWNSAMPLE_TARGET = 1500

    automation_signal = Signal(dict)
    automation_state_signal = Signal(dict)
    _cvd_tick_received = Signal(float, float, object)  # internal: marshal WebSocket thread → GUI thread

    SIGNAL_FILTER_ALL = "all"
    SIGNAL_FILTER_ATR_ONLY = "atr_only"
    SIGNAL_FILTER_EMA_CROSS_ONLY = "ema_cross_only"
    SIGNAL_FILTER_BREAKOUT_ONLY = "breakout_only"
    SIGNAL_FILTER_CVD_BREAKOUT_ONLY = "cvd_breakout_only"
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
    ORDER_TYPE_MARKET = "MARKET"
    ORDER_TYPE_LIMIT = "LIMIT"

    STRATEGY_PRIORITY_KEYS = (
        "atr_reversal",
        "atr_divergence",
        "ema_cross",
        "cvd_range_breakout",
        "range_breakout",
        "open_drive",
    )
    STRATEGY_PRIORITY_LABELS = {
        "atr_reversal": "ATR Reversal",
        "atr_divergence": "ATR Divergence",
        "ema_cross": "EMA Cross",
        "cvd_range_breakout": "CVD Range Breakout",
        "range_breakout": "Range Breakout",
        "open_drive": "Open Drive",
    }
    CPR_PRIORITY_LIST_LABELS = {
        "narrow": "Narrow CPR",
        "neutral": "Neutral CPR",
        "wide": "Wide CPR",
        "fallback": "Fallback",
    }

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
        self.regime_engine = RegimeEngine()
        self._current_regime = None

        self.live_mode = True
        self.current_date = None
        self.previous_date = None
        self._live_tick_points: deque[tuple[datetime, float]] = deque(maxlen=self.LIVE_TICK_MAX_POINTS)
        self._live_price_points: deque[tuple[datetime, float]] = deque(maxlen=self.LIVE_TICK_MAX_POINTS)
        self._pending_live_tick: tuple[float, float] | None = None
        self._last_applied_cvd_value: float | None = None
        self._last_applied_price_value: float | None = None
        self._last_applied_tick_ts: datetime | None = None
        self._current_session_start_ts: datetime | None = None
        self._current_session_x_base: float = 0.0
        self._current_session_last_cvd_value: float | None = None
        self._current_session_last_price_value: float | None = None
        self._current_session_last_x: float | None = None  # x coord of last historical candle
        self._current_session_cumulative_volume: int = 0
        self._current_session_volume_scale: float = 1.0
        self._live_cvd_offset: float = 0.0
        self._last_live_tick_ts: datetime | None = None
        self._last_ws_reconnect_attempt_ts: datetime | None = None
        self._ws_status_text: str = "initializing"
        self._is_closing = False
        self._is_loading = False
        self._chart_ready = False
        self._last_live_refresh_minute: datetime | None = None
        self._pending_tick_buffer: deque[tuple[datetime, float, float]] = deque()
        self._uploaded_tick_data: pd.DataFrame | None = None
        self._uploaded_tick_source: str = ""
        self._cvd_auth_error_logged = False

        # Plot caches (explicitly initialized so mixins never depend on dynamic attrs)
        self.all_timestamps: list[datetime] = []
        self.all_cvd_data: list[float] = []
        self.all_cvd_high_data: list[float] = []
        self.all_cvd_low_data: list[float] = []
        self.all_price_data: list[float] = []
        self.all_price_high_data: list[float] = []
        self.all_price_low_data: list[float] = []
        self.all_volume_data: list[float] = []
        self._last_plot_x_indices: list[float] = []

        # 🎯 Confluence signal lines (price + CVD both reversal at same bar)
        self._confluence_lines: list = []  # InfiniteLine items added to both plots
        self._last_emitted_signal_key: str | None = None
        self._last_emitted_closed_bar_ts: str | None = None
        # Counts ATR reversal signals suppressed while a breakout trade is active (live mode)
        self._live_atr_skip_count: int = 0
        self._live_active_breakout_side: str | None = None  # tracks which side the breakout is on
        self._live_trade_info: dict | None = None
        self._live_hybrid_engine: HybridExitEngine | None = None
        self._live_close_history: list[float] = []
        self._live_stacker_state: StackerState | None = None
        self._live_stacker_side: str | None = None
        self._live_stacker_strategy_type: str | None = None
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
        self._show_cpr_lines = True
        self._show_cpr_labels = True
        self._cpr_narrow_threshold = 150.0
        self._cpr_wide_threshold = 200.0
        self._cpr_lines = []
        self._cpr_labels = []
        self._latest_previous_day_cpr: dict | None = None
        self._cpr_strategy_priorities = self._default_cpr_strategy_priorities()
        self._active_priority_list_key = "fallback"
        self._last_logged_priority_list_key: str | None = None

        # 🆕 Strategy-aware chop filter defaults
        self._chop_filter_atr_reversal = True
        self._chop_filter_ema_cross = True
        self._chop_filter_atr_divergence = True
        self._chop_filter_cvd_range_breakout = False

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
        self._init_trend_change_markers()
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
        # ── Apply institutional theme (must be first) ──────────────────────
        apply_dialog_theme(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(4)

        def fit_combo_to_widest_item(combo: QComboBox, extra_px: int = 34):
            metrics = QFontMetrics(combo.font())
            widest = 0
            for idx in range(combo.count()):
                widest = max(widest, metrics.horizontalAdvance(combo.itemText(idx)))
            combo.setFixedWidth(max(84, widest + extra_px))

        # Use themed style constants from dialog_theme
        compact_spinbox_style = COMPACT_SPINBOX_STYLE
        compact_toggle_style  = COMPACT_TOGGLE_STYLE
        compact_combo_style   = COMPACT_COMBO_STYLE

        # ================= ROW 1: DATE NAVIGATOR =================
        navigator_row = QHBoxLayout()
        navigator_row.setContentsMargins(0, 0, 0, 0)
        navigator_row.setSpacing(8)

        # -------- Timeframe dropdown --------
        tf_label = QLabel("TF")
        tf_label.setStyleSheet(f"color: {C['text_2']}; font-size: 11px; font-weight: 600;")

        self.timeframe_combo = QComboBox()
        self.timeframe_combo.setFixedHeight(24)
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

        # Navigator (CENTER only in row 1)
        self.navigator = DateNavigator(self)

        # Day View Toggle (unchecked => 1D, checked => 2D)
        self.btn_focus = QPushButton("1D")
        self.btn_focus.setCheckable(True)
        self.btn_focus.setChecked(False)
        self.btn_focus.setFixedHeight(24)
        self.btn_focus.setMinimumWidth(56)
        self.btn_focus.setStyleSheet(f"""
            QPushButton {{
                background: {C["bg_card"]};
                color: {C["text_2"]};
                border: 1px solid {C["border"]};
                border-radius: 4px;
                padding: 0px 8px;
                font-weight: 700;
                font-size: 10px;
            }}
            QPushButton:checked {{
                background: {C["teal_dim"]};
                color: {C["teal"]};
                border: 1px solid {C["teal"]};
                font-weight: 700;
            }}
            QPushButton:hover {{
                border: 1px solid {C["border_hi"]};
                color: {C["text_1"]};
            }}
        """)
        self.btn_focus.setToolTip("Toggle 2-day view")
        self.btn_focus.toggled.connect(self._on_focus_mode_changed)
        self.btn_focus.setText("1D")

        navigator_row.addStretch()
        navigator_row.addWidget(self.navigator)
        navigator_row.addStretch()

        if self.cvd_engine:
            self.cvd_engine.set_mode(CVDMode.SINGLE_DAY)

        # Automate Toggle
        self.automate_toggle = QCheckBox("Automate")
        self.automate_toggle.setChecked(False)
        self.automate_toggle.setStyleSheet(compact_toggle_style)
        self.automate_toggle.toggled.connect(self._on_automation_settings_changed)

        self.simulator_run_btn = QPushButton("Run Simulator")
        self.simulator_run_btn.setFixedHeight(24)
        self.simulator_run_btn.setMinimumWidth(120)
        self.simulator_run_btn.setToolTip("Run simulator (Space)")
        self.simulator_run_btn.setObjectName("simRunBtn")
        self.simulator_run_btn.clicked.connect(self._on_simulator_run_clicked)

        self.tick_upload_btn = QPushButton("Update CSV")
        self.tick_upload_btn.setFixedHeight(24)
        self.tick_upload_btn.setMinimumWidth(130)
        self.tick_upload_btn.setToolTip("Upload timestamp,ltp,volume tick file for back analysis")
        self.tick_upload_btn.setObjectName("setupBtn")
        self.tick_upload_btn.clicked.connect(self._on_upload_tick_csv)

        self.tick_clear_btn = QPushButton("Live Tick")
        self.tick_clear_btn.setFixedHeight(24)
        self.tick_clear_btn.setMinimumWidth(84)
        self.tick_clear_btn.setToolTip("Clear uploaded tick data and switch back to live/historical feed")
        self.tick_clear_btn.setObjectName("setupBtn")
        self.tick_clear_btn.clicked.connect(self._clear_uploaded_tick_data)
        self.tick_clear_btn.setEnabled(False)

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

        self.hybrid_exit_enabled_check = QCheckBox("Hybrid Exit (Options Phase Engine)")
        self.hybrid_exit_enabled_check.setChecked(True)
        self.hybrid_exit_enabled_check.setToolTip("Enable phase-aware hybrid exit engine for selected trend-following strategies.")
        self.hybrid_exit_enabled_check.toggled.connect(self._on_automation_settings_changed)

        self.max_giveback_atr_reversal_check = QCheckBox("ATR Rev")
        self.max_giveback_atr_reversal_check.setChecked(False)
        self.max_giveback_atr_reversal_check.setToolTip("Apply max profit giveback exit to ATR Reversal trades.")
        self.max_giveback_atr_reversal_check.toggled.connect(self._on_automation_settings_changed)

        self.max_giveback_ema_cross_check = QCheckBox("EMA Cross")
        self.max_giveback_ema_cross_check.setChecked(False)
        self.max_giveback_ema_cross_check.setToolTip("Apply max profit giveback exit to EMA Cross trades.")
        self.max_giveback_ema_cross_check.toggled.connect(self._on_automation_settings_changed)

        self.max_giveback_atr_divergence_check = QCheckBox("ATR Div")
        self.max_giveback_atr_divergence_check.setChecked(False)
        self.max_giveback_atr_divergence_check.setToolTip("Apply max profit giveback exit to ATR Divergence trades.")
        self.max_giveback_atr_divergence_check.toggled.connect(self._on_automation_settings_changed)

        self.max_giveback_range_breakout_check = QCheckBox("Breakout")
        self.max_giveback_range_breakout_check.setChecked(False)
        self.max_giveback_range_breakout_check.setToolTip("Apply max profit giveback exit to Range Breakout trades.")
        self.max_giveback_range_breakout_check.toggled.connect(self._on_automation_settings_changed)

        self.dynamic_exit_atr_reversal_check = QCheckBox("ATR Reversal")
        self.dynamic_exit_atr_reversal_check.setChecked(False)
        self.dynamic_exit_atr_reversal_check.setToolTip("Enable regime-aware trend-unlock exits for ATR Reversal trades.")
        self.dynamic_exit_atr_reversal_check.toggled.connect(self._on_automation_settings_changed)

        self.dynamic_exit_ema_cross_check = QCheckBox("EMA Cross")
        self.dynamic_exit_ema_cross_check.setChecked(True)
        self.dynamic_exit_ema_cross_check.setToolTip("Enable regime-aware trend-unlock exits for EMA Cross trades.")
        self.dynamic_exit_ema_cross_check.toggled.connect(self._on_automation_settings_changed)

        self.dynamic_exit_atr_divergence_check = QCheckBox("ATR Divergence")
        self.dynamic_exit_atr_divergence_check.setChecked(False)
        self.dynamic_exit_atr_divergence_check.setToolTip("Enable regime-aware trend-unlock exits for ATR Divergence trades.")
        self.dynamic_exit_atr_divergence_check.toggled.connect(self._on_automation_settings_changed)

        self.dynamic_exit_range_breakout_check = QCheckBox("Range Breakout")
        self.dynamic_exit_range_breakout_check.setChecked(True)
        self.dynamic_exit_range_breakout_check.setToolTip("Enable regime-aware trend-unlock exits for Range Breakout trades.")
        self.dynamic_exit_range_breakout_check.toggled.connect(self._on_automation_settings_changed)

        self.dynamic_exit_cvd_range_breakout_check = QCheckBox("CVD Range Breakout")
        self.dynamic_exit_cvd_range_breakout_check.setChecked(True)
        self.dynamic_exit_cvd_range_breakout_check.setToolTip("Enable regime-aware trend-unlock exits for CVD Range Breakout trades.")
        self.dynamic_exit_cvd_range_breakout_check.toggled.connect(self._on_automation_settings_changed)

        self.dynamic_exit_open_drive_check = QCheckBox("Open Drive")
        self.dynamic_exit_open_drive_check.setChecked(False)
        self.dynamic_exit_open_drive_check.setToolTip("Enable regime-aware trend-unlock exits for Open Drive trades.")
        self.dynamic_exit_open_drive_check.toggled.connect(self._on_automation_settings_changed)

        self.trend_exit_adx_min_input = QDoubleSpinBox()
        self.trend_exit_adx_min_input.setRange(15.0, 45.0)
        self.trend_exit_adx_min_input.setDecimals(1)
        self.trend_exit_adx_min_input.setSingleStep(0.5)
        self.trend_exit_adx_min_input.setValue(28.0)
        self.trend_exit_adx_min_input.setStyleSheet(compact_spinbox_style)
        self.trend_exit_adx_min_input.setToolTip(
            "Minimum ADX value required to activate trend-ride (unlock) mode.\n"
            "Higher = only unlock in very strong trends. Lower = more permissive.\n"
            "Institutional default: 28. Aggressive: 24. Conservative: 32."
        )
        self.trend_exit_adx_min_input.valueChanged.connect(self._on_automation_settings_changed)

        self.trend_exit_atr_ratio_min_input = QDoubleSpinBox()
        self.trend_exit_atr_ratio_min_input.setRange(0.80, 2.50)
        self.trend_exit_atr_ratio_min_input.setDecimals(2)
        self.trend_exit_atr_ratio_min_input.setSingleStep(0.05)
        self.trend_exit_atr_ratio_min_input.setValue(1.15)
        self.trend_exit_atr_ratio_min_input.setStyleSheet(compact_spinbox_style)
        self.trend_exit_atr_ratio_min_input.setToolTip(
            "Minimum normalized ATR (current ATR / session baseline) to unlock trend mode.\n"
            "Ensures unlock only happens when volatility is expanding (trending market).\n"
            "Default: 1.15. Low vol sessions: try 1.05. High vol: try 1.25."
        )
        self.trend_exit_atr_ratio_min_input.valueChanged.connect(self._on_automation_settings_changed)

        self.trend_exit_confirm_bars_input = QSpinBox()
        self.trend_exit_confirm_bars_input.setRange(1, 8)
        self.trend_exit_confirm_bars_input.setValue(3)
        self.trend_exit_confirm_bars_input.setStyleSheet(compact_spinbox_style)
        self.trend_exit_confirm_bars_input.setToolTip(
            "Consecutive qualifying bars (ADX + ATR both above threshold) required\n"
            "before switching to trend-ride mode. Prevents false unlock on a single spike bar.\n"
            "Default: 3. Faster reaction: 2. Slower/safer: 4-5."
        )
        self.trend_exit_confirm_bars_input.valueChanged.connect(self._on_automation_settings_changed)

        # ── Dynamic Exit Conditions — breakdown detection knobs ───────────────
        self.trend_exit_min_profit_input = QDoubleSpinBox()
        self.trend_exit_min_profit_input.setRange(0.5, 50.0)
        self.trend_exit_min_profit_input.setDecimals(1)
        self.trend_exit_min_profit_input.setSingleStep(0.5)
        self.trend_exit_min_profit_input.setValue(0.0)   # 0 = use stoploss_points as floor (legacy behaviour)
        self.trend_exit_min_profit_input.setStyleSheet(compact_spinbox_style)
        self.trend_exit_min_profit_input.setToolTip(
            "Minimum favorable move (points) the trade must show before trend-ride\n"
            "mode can activate. 0 = auto (uses stoploss value as the floor).\n"
            "Raise this to avoid switching to trend mode on shallow moves."
        )
        self.trend_exit_min_profit_input.valueChanged.connect(self._on_automation_settings_changed)

        self.trend_exit_vol_drop_pct_input = QDoubleSpinBox()
        self.trend_exit_vol_drop_pct_input.setRange(0.50, 0.99)
        self.trend_exit_vol_drop_pct_input.setDecimals(2)
        self.trend_exit_vol_drop_pct_input.setSingleStep(0.01)
        self.trend_exit_vol_drop_pct_input.setValue(0.85)
        self.trend_exit_vol_drop_pct_input.setStyleSheet(compact_spinbox_style)
        self.trend_exit_vol_drop_pct_input.setToolTip(
            "Regime breakdown: exit trend-ride when ATR/vol falls below this fraction\n"
            "of its peak value since trend-ride started. 0.85 = exit if vol drops >15%%\n"
            "from peak. Lower = more tolerant; Higher = exits on small vol pullbacks."
        )
        self.trend_exit_vol_drop_pct_input.valueChanged.connect(self._on_automation_settings_changed)

        self.trend_exit_breakdown_bars_input = QSpinBox()
        self.trend_exit_breakdown_bars_input.setRange(2, 8)
        self.trend_exit_breakdown_bars_input.setValue(3)
        self.trend_exit_breakdown_bars_input.setStyleSheet(compact_spinbox_style)
        self.trend_exit_breakdown_bars_input.setToolTip(
            "Consecutive bars of falling ADX required to confirm regime breakdown\n"
            "and exit trend-ride mode. Default 3. Higher = waits longer, rides more."
        )
        self.trend_exit_breakdown_bars_input.valueChanged.connect(self._on_automation_settings_changed)

        # ── Breakdown lookback (the "5 bars ago" check) ───────────────────
        self.trend_exit_breakdown_lookback_input = QSpinBox()
        self.trend_exit_breakdown_lookback_input.setRange(3, 15)
        self.trend_exit_breakdown_lookback_input.setValue(5)
        self.trend_exit_breakdown_lookback_input.setStyleSheet(compact_spinbox_style)
        self.trend_exit_breakdown_lookback_input.setToolTip(
            "Lookback window (bars) for the regime breakdown 'below X bars ago' check.\n"
            "ADX and vol must both be below their value N bars ago to confirm breakdown.\n"
            "Default 5. Increase for stricter exit (needs deeper sustained fall).\n"
            "Decrease for faster exits (shallow pullbacks trigger breakdown sooner)."
        )
        self.trend_exit_breakdown_lookback_input.valueChanged.connect(self._on_automation_settings_changed)

        # ── Entry: consecutive rising bars required before trend-ride ─────
        self.trend_entry_consecutive_bars_input = QSpinBox()
        self.trend_entry_consecutive_bars_input.setRange(1, 8)
        self.trend_entry_consecutive_bars_input.setValue(3)
        self.trend_entry_consecutive_bars_input.setStyleSheet(compact_spinbox_style)
        self.trend_entry_consecutive_bars_input.setToolTip(
            "How many consecutive bars ADX AND vol must both be rising before the\n"
            "trend-ride mode entry check passes. Independent of Confirm Bars.\n"
            "Default 3 (same as old hardcoded value). Set to 2 for faster entry,\n"
            "4-5 for only entering on strong sustained momentum."
        )
        self.trend_entry_consecutive_bars_input.valueChanged.connect(self._on_automation_settings_changed)

        # ── Entry slope gates (toggleable) ────────────────────────────────
        self.trend_entry_require_adx_slope_check = QCheckBox("Require ADX slope ↑")
        self.trend_entry_require_adx_slope_check.setChecked(True)
        self.trend_entry_require_adx_slope_check.setToolTip(
            "When ON: ADX must be rising on the current bar to enter trend-ride.\n"
            "When OFF: only the ADX Min threshold and consecutive check apply.\n"
            "Turn OFF for markets where ADX oscillates around a high level without\n"
            "continuously rising (e.g. strong sustained trend with choppy ADX)."
        )
        self.trend_entry_require_adx_slope_check.toggled.connect(self._on_automation_settings_changed)

        self.trend_entry_require_vol_slope_check = QCheckBox("Require Vol slope ↑")
        self.trend_entry_require_vol_slope_check.setChecked(True)
        self.trend_entry_require_vol_slope_check.setToolTip(
            "When ON: ATR/vol must be rising on the current bar to enter trend-ride.\n"
            "When OFF: only the ATR Ratio Min threshold applies.\n"
            "Turn OFF when trading high-vol regimes where volatility stays elevated\n"
            "but individual-bar slope flips noise-ily."
        )
        self.trend_entry_require_vol_slope_check.toggled.connect(self._on_automation_settings_changed)

        self.automation_route_combo = QComboBox()
        self.automation_route_combo.setFixedWidth(180)
        self.automation_route_combo.setStyleSheet(compact_combo_style)
        self.automation_route_combo.addItem("Buy Exit Panel", self.ROUTE_BUY_EXIT_PANEL)
        self.automation_route_combo.addItem("Direct", self.ROUTE_DIRECT)
        self.automation_route_combo.setCurrentIndex(0)
        self.automation_route_combo.currentIndexChanged.connect(self._on_automation_settings_changed)

        self.automation_order_type_combo = QComboBox()
        self.automation_order_type_combo.setFixedWidth(180)
        self.automation_order_type_combo.setStyleSheet(compact_combo_style)
        self.automation_order_type_combo.addItem("Market", self.ORDER_TYPE_MARKET)
        self.automation_order_type_combo.addItem("Limit", self.ORDER_TYPE_LIMIT)
        self.automation_order_type_combo.setCurrentIndex(0)
        self.automation_order_type_combo.currentIndexChanged.connect(self._on_automation_settings_changed)

        self.automation_start_time_hour_input = QSpinBox()
        self.automation_start_time_hour_input.setRange(0, 23)
        self.automation_start_time_hour_input.setValue(9)
        self.automation_start_time_hour_input.setFixedWidth(52)
        self.automation_start_time_hour_input.setStyleSheet(compact_spinbox_style)
        self.automation_start_time_hour_input.valueChanged.connect(self._on_automation_settings_changed)

        self.automation_start_time_minute_input = QSpinBox()
        self.automation_start_time_minute_input.setRange(0, 59)
        self.automation_start_time_minute_input.setValue(15)
        self.automation_start_time_minute_input.setFixedWidth(52)
        self.automation_start_time_minute_input.setStyleSheet(compact_spinbox_style)
        self.automation_start_time_minute_input.valueChanged.connect(self._on_automation_settings_changed)

        self.automation_cutoff_time_hour_input = QSpinBox()
        self.automation_cutoff_time_hour_input.setRange(0, 23)
        self.automation_cutoff_time_hour_input.setValue(15)
        self.automation_cutoff_time_hour_input.setFixedWidth(52)
        self.automation_cutoff_time_hour_input.setStyleSheet(compact_spinbox_style)
        self.automation_cutoff_time_hour_input.valueChanged.connect(self._on_automation_settings_changed)

        self.automation_cutoff_time_minute_input = QSpinBox()
        self.automation_cutoff_time_minute_input.setRange(0, 59)
        self.automation_cutoff_time_minute_input.setValue(15)
        self.automation_cutoff_time_minute_input.setFixedWidth(52)
        self.automation_cutoff_time_minute_input.setStyleSheet(compact_spinbox_style)
        self.automation_cutoff_time_minute_input.valueChanged.connect(self._on_automation_settings_changed)

        self.setup_btn = QPushButton("Setup")
        self.setup_btn.setFixedHeight(24)
        self.setup_btn.setMinimumWidth(88)
        self.setup_btn.setToolTip("Open automation and signal settings")
        self.setup_btn.setObjectName("setupBtn")
        self.setup_btn.clicked.connect(self._open_setup_dialog)

        # Regime indicator (live pills)
        self.regime_indicator = RegimeIndicator()

        # Export button (compact)
        self.btn_export = QPushButton("📸")
        self.btn_export.setFixedSize(28, 28)
        self.btn_export.setObjectName("navBtn")
        self.btn_export.setToolTip("Export current view as image")
        self.btn_export.clicked.connect(self._export_chart_image)

        self.btn_refresh_plot = QPushButton("⟳")
        self.btn_refresh_plot.setFixedSize(28, 28)
        self.btn_refresh_plot.setObjectName("navBtn")
        self.btn_refresh_plot.setToolTip("Refresh chart plot")
        self.btn_refresh_plot.clicked.connect(self._refresh_plot_only)

        root.addLayout(navigator_row)

        self.navigator.btn_back.setToolTip("Previous trading day (←)")
        self.navigator.btn_forward.setToolTip("Next trading day (→)")

        # ================= ROW 2+3 WRAPPER (CENTERED BLOCK) =================
        toolbar_block = QWidget(self)
        toolbar_block_layout = QVBoxLayout(toolbar_block)
        toolbar_block_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_block_layout.setSpacing(0)

        # ================= ROW 2: PRIMARY CONTROLS =================
        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 4)
        controls_row.setSpacing(8)

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
        self.cvd_atr_distance_input.setRange(0.5, 6.0)
        self.cvd_atr_distance_input.setDecimals(2)
        self.cvd_atr_distance_input.setSingleStep(0.1)
        self.cvd_atr_distance_input.setValue(2.0)
        self.cvd_atr_distance_input.setFixedWidth(96)
        self.cvd_atr_distance_input.setStyleSheet(compact_spinbox_style)
        self.cvd_atr_distance_input.setToolTip(
            "Minimum z-score for CVD to trigger a marker/signal.\n"
            "Z-score = (CVD - EMA51) / rolling_std(CVD, 50 bars)\n"
            "2.0 = CVD is 2 standard deviations from EMA (institutional default)\n"
            "1.5 = more sensitive. 2.5 = fewer but stronger signals."
        )
        self.cvd_atr_distance_input.valueChanged.connect(self._on_atr_settings_changed)

        self.atr_extension_threshold_input = QDoubleSpinBox()
        self.atr_extension_threshold_input.setRange(0.5, 3.0)
        self.atr_extension_threshold_input.setDecimals(2)
        self.atr_extension_threshold_input.setSingleStep(0.05)
        self.atr_extension_threshold_input.setValue(1.10)
        self.atr_extension_threshold_input.setFixedWidth(96)
        self.atr_extension_threshold_input.setStyleSheet(compact_spinbox_style)
        self.atr_extension_threshold_input.setToolTip(
            "Minimum normalized ATR required for ATR reversal gating.\n"
            "Lower values increase signal frequency; higher values make it stricter."
        )
        self.atr_extension_threshold_input.valueChanged.connect(self._on_atr_settings_changed)

        self.atr_flat_velocity_pct_input = QDoubleSpinBox()
        self.atr_flat_velocity_pct_input.setRange(0.0, 0.2)
        self.atr_flat_velocity_pct_input.setDecimals(3)
        self.atr_flat_velocity_pct_input.setSingleStep(0.005)
        self.atr_flat_velocity_pct_input.setValue(0.020)
        self.atr_flat_velocity_pct_input.setFixedWidth(96)
        self.atr_flat_velocity_pct_input.setStyleSheet(compact_spinbox_style)
        self.atr_flat_velocity_pct_input.setToolTip(
            "Maximum ATR velocity percentage treated as flat/contracting.\n"
            "Higher values allow more signals to pass the flatness gate."
        )
        self.atr_flat_velocity_pct_input.valueChanged.connect(self._on_atr_settings_changed)

        # EMA Label
        ema_label = QLabel("EMAs:")
        ema_label.setStyleSheet(f"color: {C['text_2']}; font-weight: 600; font-size: 11px;")
        controls_row.addWidget(tf_label)
        controls_row.addWidget(self.timeframe_combo)
        controls_row.addWidget(self.btn_focus)
        controls_row.addWidget(ema_label)

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
            controls_row.addWidget(cb)

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
        controls_row.addWidget(self.vwap_checkbox)

        controls_row.addSpacing(4)

        signal_filter_label = QLabel("Filter")
        signal_filter_label.setStyleSheet(f"color: {C['text_2']}; font-size: 11px;")
        controls_row.addWidget(signal_filter_label)

        self.signal_filter_combo = QComboBox()
        self.signal_filter_combo.setFixedWidth(220)
        self.signal_filter_combo.setStyleSheet(compact_combo_style)
        self._init_signal_filter_combo(self.signal_filter_combo)
        fit_combo_to_widest_item(self.signal_filter_combo, extra_px=48)
        self.signal_filter_combo.view().setMinimumWidth(self.signal_filter_combo.width() + 16)
        controls_row.addWidget(self.signal_filter_combo)

        atr_marker_label = QLabel("ATR Markers")
        atr_marker_label.setStyleSheet(f"color: {C['text_2']}; font-size: 11px;")
        controls_row.addWidget(atr_marker_label)

        self.atr_marker_filter_combo = QComboBox()
        self.atr_marker_filter_combo.setFixedWidth(156)
        self.atr_marker_filter_combo.setStyleSheet(compact_combo_style)
        self.atr_marker_filter_combo.addItem("Show All", self.ATR_MARKER_SHOW_ALL)
        self.atr_marker_filter_combo.addItem("Confluence Only", self.ATR_MARKER_CONFLUENCE_ONLY)
        self.atr_marker_filter_combo.addItem("Green Only", self.ATR_MARKER_GREEN_ONLY)
        self.atr_marker_filter_combo.addItem("Red Only", self.ATR_MARKER_RED_ONLY)
        self.atr_marker_filter_combo.addItem("Hide All", self.ATR_MARKER_HIDE_ALL)
        self.atr_marker_filter_combo.setCurrentIndex(1)
        self.atr_marker_filter_combo.currentIndexChanged.connect(self._on_atr_marker_filter_changed)
        fit_combo_to_widest_item(self.atr_marker_filter_combo, extra_px=42)
        self.atr_marker_filter_combo.view().setMinimumWidth(self.atr_marker_filter_combo.width() + 12)
        controls_row.addWidget(self.atr_marker_filter_combo)

        controls_row.addWidget(self.setup_btn)

        self.simulator_summary_label = QLabel("Simulator: click Run Simulator")
        self.simulator_summary_label.setStyleSheet(f"color: {C['text_2']}; font-size: 11px; font-weight: 600;")
        controls_row.addWidget(self.btn_refresh_plot)
        controls_row.addWidget(self.btn_export)
        controls_row.addStretch()
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

        # ── Profit Harvest widgets ──────────────────────────────────────────
        self.harvest_enabled_check = QCheckBox("Harvest")
        self.harvest_enabled_check.setChecked(False)
        self.harvest_enabled_check.setToolTip(
            "FIFO Profit Harvest: when total PnL crosses the threshold,\n"
            "exit the oldest stack (STACK_1) to lock in profit.\n"
            "Works alongside LIFO unwind — these are independent.\n"
            "LIFO: defends against reversals.\n"
            "Harvest: locks profit when you're winning."
        )
        self.harvest_enabled_check.toggled.connect(self._on_stacker_settings_changed)

        self.harvest_threshold_input = QDoubleSpinBox()
        self.harvest_threshold_input.setPrefix("₹")
        self.harvest_threshold_input.setRange(500, 500000)
        self.harvest_threshold_input.setValue(10000)
        self.harvest_threshold_input.setSingleStep(1000)
        self.harvest_threshold_input.setDecimals(0)
        self.harvest_threshold_input.setStyleSheet(compact_spinbox_style)
        self.harvest_threshold_input.setToolTip(
            "Lock profit every time total PnL gains this much.\n"
            "Example: ₹10,000 → harvest at ₹10K, then ₹20K, then ₹30K..."
        )
        self.harvest_threshold_input.valueChanged.connect(self._on_stacker_settings_changed)

        self._build_setup_dialog(compact_combo_style, compact_spinbox_style)

        toolbar_block_layout.addLayout(controls_row)

        # ================= ROW 3: AUTOMATION + REGIME =================
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 4)
        status_row.setSpacing(8)
        status_row.addWidget(self.tick_upload_btn)
        status_row.addWidget(self.tick_clear_btn)
        status_row.addWidget(self.automate_toggle)
        status_row.addWidget(self.stacker_enabled_check)
        status_row.addWidget(self.stacker_step_input)
        status_row.addWidget(self.stacker_max_input)
        status_row.addWidget(self.harvest_enabled_check)
        status_row.addWidget(self.harvest_threshold_input)
        status_row.addWidget(self.regime_indicator)
        status_row.addStretch()
        status_row.addWidget(self.simulator_run_btn)
        toolbar_block_layout.addLayout(status_row)

        # ================= ROW 4: SIMULATOR SUMMARY =================
        simulator_row = QHBoxLayout()
        simulator_row.setContentsMargins(0, 0, 0, 4)
        simulator_row.setSpacing(8)
        simulator_row.addWidget(self.simulator_summary_label)
        simulator_row.addStretch()
        toolbar_block_layout.addLayout(simulator_row)

        toolbar_block.adjustSize()
        toolbar_block.setMaximumWidth(toolbar_block.sizeHint().width())

        toolbar_block_row = QHBoxLayout()
        toolbar_block_row.setContentsMargins(0, 0, 0, 0)
        toolbar_block_row.setSpacing(0)
        toolbar_block_row.addStretch()
        toolbar_block_row.addWidget(toolbar_block)
        toolbar_block_row.addStretch()
        root.addLayout(toolbar_block_row)

        # === PRICE CHART (TOP) ===
        self.price_axis = AxisItem(orientation="bottom")
        self.price_axis.setStyle(showValues=False)

        self.price_plot = pg.PlotWidget(axisItems={"bottom": self.price_axis})
        self.price_plot.setBackground(C["chart_bg"])
        self.price_plot.showGrid(x=True, y=True, alpha=0.06)
        self.price_plot.setMenuEnabled(False)
        self.price_plot.setMinimumHeight(200)

        # Price Y-axis styling
        price_y_axis = self.price_plot.getAxis("left")
        price_y_axis.setWidth(70)
        # Keep price scale markings high-contrast so values remain readable at a glance.
        price_y_axis.setTextPen(pg.mkPen(C["text_1"]))
        price_y_axis.setPen(pg.mkPen(C["text_2"]))
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
        bottom_axis.setTextPen(pg.mkPen(C["text_3"]))
        bottom_axis.setPen(pg.mkPen(C["border_dim"]))

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

        self.plot.setBackground(C["chart_bg"])
        self.plot.showGrid(x=True, y=True, alpha=0.06)
        self.plot.setMenuEnabled(False)
        self.plot.setMinimumHeight(200)

        root.addWidget(self.plot, 1)

        bottom_status_row = QHBoxLayout()
        bottom_status_row.setContentsMargins(2, 2, 2, 0)
        bottom_status_row.setSpacing(14)

        self.cpr_status_label = QLabel("CPR: --")
        self.cpr_status_label.setStyleSheet(f"color: {C['text_2']}; font-size: 11px; font-weight: 700;")
        self.cpr_status_label.setAlignment(Qt.AlignCenter)

        self.priority_order_label = QLabel("Priority order: --")
        self.priority_order_label.setStyleSheet(f"color: {C['text_2']}; font-size: 11px; font-weight: 600;")
        self.priority_order_label.setAlignment(Qt.AlignCenter)

        bottom_status_row.addStretch()
        bottom_status_row.addWidget(self.cpr_status_label)
        bottom_status_row.addWidget(self.priority_order_label)
        bottom_status_row.addStretch()
        root.addLayout(bottom_status_row)

        # ── Institutional status bar (bottom telemetry strip) ──────────────
        self._status_bar = StatusBar(self)
        root.addWidget(self._status_bar)

        zero_pen = pg.mkPen(C["border"], style=Qt.DashLine, width=1)
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
            color=C["text_1"],
            fill=pg.mkBrush(C["bg_card"]),
            border=pg.mkPen(C["border_hi"])
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

        self._cvd_tick_flush_timer = QTimer(self)
        self._cvd_tick_flush_timer.setSingleShot(True)
        self._cvd_tick_flush_timer.timeout.connect(self._flush_pending_cvd_tick)

        self._apply_visual_settings()

        self.ws_status_label = QLabel("Live feed: connecting…")
        self.ws_status_label.setStyleSheet(f"color: {C['warn']}; font-size: 11px; font-weight: 600;")
        self.ws_status_label.hide()

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

        parent = self.parent()
        market_data_worker = getattr(parent, "market_data_worker", None)
        if market_data_worker and hasattr(market_data_worker, "connection_status_changed"):
            market_data_worker.connection_status_changed.connect(
                self._on_market_data_status_changed,
                Qt.QueuedConnection,
            )

    # =========================================================================
    # SECTION 3: SETTINGS PERSISTENCE
    # =========================================================================

    def _load_persisted_setup_values(self):
        key_prefix = self._settings_key_prefix()
        global_key_prefix = self._global_settings_key_prefix()
        migration_settings = self._read_setup_json_for_migration()
        migrated_values: dict[str, object] = {}

        def _coerce_setting(value, default, value_type=None):
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

        def _read_setting(name: str, default, value_type=None):
            token_key = f"{key_prefix}/{name}"
            global_key = f"{global_key_prefix}/{name}"
            if self._settings.contains(global_key):
                key_to_read = global_key
                raw_value = self._settings.value(key_to_read, default)
                return _coerce_setting(raw_value, default, value_type)
            if self._settings.contains(token_key):
                raw_value = self._settings.value(token_key, default)
                return _coerce_setting(raw_value, default, value_type)

            if name in migration_settings:
                migrated_values[name] = migration_settings[name]
                return _coerce_setting(migration_settings[name], default, value_type)

            return default

        def _apply_combo_value(combo: QComboBox, data_value, fallback_index: int = 0):
            idx = combo.findData(data_value)
            combo.setCurrentIndex(idx if idx >= 0 else fallback_index)

        self.automate_toggle.blockSignals(True)
        self.automation_stoploss_input.blockSignals(True)
        self.max_profit_giveback_input.blockSignals(True)
        self.hybrid_exit_enabled_check.blockSignals(True)
        self.max_giveback_atr_reversal_check.blockSignals(True)
        self.max_giveback_ema_cross_check.blockSignals(True)
        self.max_giveback_atr_divergence_check.blockSignals(True)
        self.max_giveback_range_breakout_check.blockSignals(True)
        self.dynamic_exit_atr_reversal_check.blockSignals(True)
        self.dynamic_exit_ema_cross_check.blockSignals(True)
        self.dynamic_exit_atr_divergence_check.blockSignals(True)
        self.dynamic_exit_range_breakout_check.blockSignals(True)
        self.dynamic_exit_cvd_range_breakout_check.blockSignals(True)
        self.dynamic_exit_open_drive_check.blockSignals(True)
        self.trend_exit_adx_min_input.blockSignals(True)
        self.trend_exit_atr_ratio_min_input.blockSignals(True)
        self.trend_exit_confirm_bars_input.blockSignals(True)
        self.trend_exit_min_profit_input.blockSignals(True)
        self.trend_exit_vol_drop_pct_input.blockSignals(True)
        self.trend_exit_breakdown_bars_input.blockSignals(True)
        self.trend_exit_breakdown_lookback_input.blockSignals(True)
        self.trend_entry_consecutive_bars_input.blockSignals(True)
        self.trend_entry_require_adx_slope_check.blockSignals(True)
        self.trend_entry_require_vol_slope_check.blockSignals(True)
        self.automation_route_combo.blockSignals(True)
        self.automation_order_type_combo.blockSignals(True)
        self.automation_start_time_hour_input.blockSignals(True)
        self.automation_start_time_minute_input.blockSignals(True)
        self.automation_cutoff_time_hour_input.blockSignals(True)
        self.automation_cutoff_time_minute_input.blockSignals(True)
        self.atr_base_ema_input.blockSignals(True)
        self.atr_distance_input.blockSignals(True)
        self.cvd_atr_distance_input.blockSignals(True)
        self.atr_extension_threshold_input.blockSignals(True)
        self.atr_flat_velocity_pct_input.blockSignals(True)
        self.cvd_ema_gap_input.blockSignals(True)
        self.ema_cross_use_parent_mask_check.blockSignals(True)
        self.signal_filter_combo.blockSignals(True)
        self.atr_marker_filter_combo.blockSignals(True)
        self.setup_signal_filter_combo.blockSignals(True)
        self.setup_cvd_value_mode_combo.blockSignals(True)
        self.setup_atr_marker_filter_combo.blockSignals(True)
        self.range_lookback_input.blockSignals(True)  # 🆕 NEW
        self.breakout_switch_mode_combo.blockSignals(True)
        self.atr_skip_limit_input.blockSignals(True)
        self.atr_trailing_step_input.blockSignals(True)
        self.deploy_mode_combo.blockSignals(True)
        self.min_confidence_input.blockSignals(True)
        self.canary_ratio_input.blockSignals(True)
        self.health_alert_threshold_input.blockSignals(True)
        self.strategy_weight_decay_input.blockSignals(True)
        self.strategy_weight_floor_input.blockSignals(True)
        self.drift_window_input.blockSignals(True)
        self.hide_simulator_btn_check.blockSignals(True)
        self.hide_tick_backtest_controls_check.blockSignals(True)
        self.chop_filter_atr_reversal_check.blockSignals(True)
        self.chop_filter_ema_cross_check.blockSignals(True)
        self.chop_filter_atr_divergence_check.blockSignals(True)
        self.chop_filter_cvd_range_breakout_check.blockSignals(True)
        self.stacker_enabled_check.setChecked(_read_setting("stacker_enabled", False, bool))
        self.stacker_step_input.setValue(_read_setting("stacker_step_points", 20, int))
        self.stacker_max_input.setValue(_read_setting("stacker_max_stacks", 2, int))
        self.harvest_enabled_check.setChecked(_read_setting("harvest_enabled", False, bool))
        self.harvest_threshold_input.setValue(_read_setting("harvest_threshold_rupees", 10000.0, float))
        self.open_drive_enabled_check.blockSignals(True)
        self.open_drive_time_hour_input.blockSignals(True)
        self.open_drive_time_minute_input.blockSignals(True)
        self.open_drive_stack_enabled_check.blockSignals(True)
        self.open_drive_max_profit_giveback_input.blockSignals(True)
        self.open_drive_tick_drawdown_limit_input.blockSignals(True)
        self.breakout_min_consol_input.blockSignals(True)
        self.breakout_min_consol_adx_input.blockSignals(True)
        self.cvd_range_lookback_input.blockSignals(True)
        self.cvd_breakout_buffer_input.blockSignals(True)
        self.cvd_min_consol_bars_input.blockSignals(True)
        self.cvd_max_range_ratio_input.blockSignals(True)
        self.cvd_conviction_score_input.blockSignals(True)
        self.cvd_vol_expansion_mult_input.blockSignals(True)
        self.cvd_atr_expansion_pct_input.blockSignals(True)
        self.cvd_htf_bars_input.blockSignals(True)
        self.cvd_regime_adx_block_input.blockSignals(True)
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
        self.show_cpr_lines_check.blockSignals(True)
        self.show_cpr_labels_check.blockSignals(True)
        self.cpr_narrow_threshold_input.blockSignals(True)
        self.cpr_wide_threshold_input.blockSignals(True)
        for spin in getattr(self, "cpr_priority_inputs", {}).values():
            spin.blockSignals(True)
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
        self.hybrid_exit_enabled_check.setChecked(_read_setting("hybrid_exit_enabled", True, bool))
        max_giveback_strategies = _read_setting(
            "max_profit_giveback_strategies",
            list(self._max_giveback_strategy_defaults()),
        )
        if not isinstance(max_giveback_strategies, (list, tuple, set)):
            max_giveback_strategies = self._max_giveback_strategy_defaults()
        self._apply_max_giveback_strategy_selection(list(max_giveback_strategies))
        dynamic_exit_strategies = _read_setting(
            "dynamic_exit_trend_following_strategies",
            list(self._dynamic_exit_strategy_defaults()),
        )
        if not isinstance(dynamic_exit_strategies, (list, tuple, set)):
            dynamic_exit_strategies = self._dynamic_exit_strategy_defaults()
        self._apply_dynamic_exit_strategy_selection(list(dynamic_exit_strategies))
        self.trend_exit_adx_min_input.setValue(_read_setting("trend_exit_adx_min", 28.0, float))
        self.trend_exit_atr_ratio_min_input.setValue(_read_setting("trend_exit_atr_ratio_min", 1.15, float))
        self.trend_exit_confirm_bars_input.setValue(_read_setting("trend_exit_confirm_bars", 3, int))
        self.trend_exit_min_profit_input.setValue(_read_setting("trend_exit_min_profit", 0.0, float))
        self.trend_exit_vol_drop_pct_input.setValue(_read_setting("trend_exit_vol_drop_pct", 0.85, float))
        self.trend_exit_breakdown_bars_input.setValue(_read_setting("trend_exit_breakdown_bars", 3, int))
        self.trend_exit_breakdown_lookback_input.setValue(_read_setting("trend_exit_breakdown_lookback", 5, int))
        self.trend_entry_consecutive_bars_input.setValue(_read_setting("trend_entry_consecutive_bars", 3, int))
        self.trend_entry_require_adx_slope_check.setChecked(_read_setting("trend_entry_require_adx_slope", True, bool))
        self.trend_entry_require_vol_slope_check.setChecked(_read_setting("trend_entry_require_vol_slope", True, bool))
        _apply_combo_value(
            self.automation_route_combo,
            _read_setting("route", self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL),
            fallback_index=0,
        )
        _apply_combo_value(
            self.automation_order_type_combo,
            _read_setting("order_type", self.automation_order_type_combo.currentData() or self.ORDER_TYPE_MARKET),
            fallback_index=0,
        )
        self.automation_start_time_hour_input.setValue(_read_setting("automation_start_hour", 9, int))
        self.automation_start_time_minute_input.setValue(_read_setting("automation_start_minute", 15, int))
        self.automation_cutoff_time_hour_input.setValue(_read_setting("automation_cutoff_hour", 15, int))
        self.automation_cutoff_time_minute_input.setValue(_read_setting("automation_cutoff_minute", 15, int))

        self.atr_base_ema_input.setValue(
            _read_setting("atr_base_ema", self.atr_base_ema_input.value(), int)
        )
        self.atr_distance_input.setValue(
            _read_setting("atr_distance", self.atr_distance_input.value(), float)
        )
        self.cvd_atr_distance_input.setValue(
            _read_setting("cvd_atr_distance", self.cvd_atr_distance_input.value(), float)
        )
        self.atr_extension_threshold_input.setValue(
            _read_setting("atr_extension_threshold", self.atr_extension_threshold_input.value(), float)
        )
        self.atr_flat_velocity_pct_input.setValue(
            _read_setting("atr_flat_velocity_pct", self.atr_flat_velocity_pct_input.value(), float)
        )
        self.cvd_ema_gap_input.setValue(
            _read_setting("cvd_ema_gap", self.cvd_ema_gap_input.value(), int)
        )
        self.ema_cross_use_parent_mask_check.setChecked(
            _read_setting("ema_cross_use_parent_mask", True, bool)
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
        self.atr_trailing_step_input.setValue(
            _read_setting("atr_trailing_step_points", 10.0, float)
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
        self.health_alert_threshold_input.setValue(
            _read_setting("health_alert_threshold", self.signal_governance.health_alert_threshold, float)
        )
        self.strategy_weight_decay_input.setValue(
            _read_setting("strategy_weight_decay_lambda", self.signal_governance.strategy_weight_decay_lambda, float)
        )
        self.strategy_weight_floor_input.setValue(
            _read_setting("strategy_weight_floor", self.signal_governance._strategy_weight_floor, float)
        )
        self.drift_window_input.setValue(
            _read_setting("drift_feature_window", self.signal_governance.feature_window, int)
        )
        self._on_governance_settings_changed()

        self.hide_simulator_btn_check.setChecked(
            _read_setting("hide_simulator_button", False, bool)
        )
        self.hide_tick_backtest_controls_check.setChecked(
            _read_setting("hide_tick_backtest_controls", False, bool)
        )
        self.open_drive_enabled_check.setChecked(_read_setting("open_drive_enabled", False, bool))
        self.open_drive_time_hour_input.setValue(_read_setting("open_drive_entry_hour", 9, int))
        self.open_drive_time_minute_input.setValue(_read_setting("open_drive_entry_minute", 17, int))
        self.open_drive_stack_enabled_check.setChecked(_read_setting("open_drive_stack_enabled", True, bool))
        self.open_drive_max_profit_giveback_input.setValue(_read_setting("open_drive_max_profit_giveback_points", 0, int))
        self.open_drive_tick_drawdown_limit_input.setValue(_read_setting("open_drive_tick_drawdown_limit_points", 100, int))
        # 🆕 Load chop filter settings
        self.chop_filter_atr_reversal_check.setChecked(_read_setting("chop_filter_atr_reversal", True, bool))
        self.chop_filter_ema_cross_check.setChecked(_read_setting("chop_filter_ema_cross", True, bool))
        self.chop_filter_atr_divergence_check.setChecked(_read_setting("chop_filter_atr_divergence", True, bool))
        self.chop_filter_cvd_range_breakout_check.setChecked(
            _read_setting("chop_filter_cvd_range_breakout", False, bool)
        )
        self._chop_filter_atr_reversal = self.chop_filter_atr_reversal_check.isChecked()
        self._chop_filter_ema_cross = self.chop_filter_ema_cross_check.isChecked()
        self._chop_filter_atr_divergence = self.chop_filter_atr_divergence_check.isChecked()
        self._chop_filter_cvd_range_breakout = self.chop_filter_cvd_range_breakout_check.isChecked()
        # 🆕 Load consolidation requirement
        self.breakout_min_consol_input.setValue(_read_setting("breakout_min_consolidation_minutes", 0, int))
        self.breakout_min_consol_adx_input.setValue(_read_setting("breakout_min_consolidation_adx", 0.0, float))
        self._breakout_min_consolidation_minutes = self.breakout_min_consol_input.value()
        self._breakout_min_consolidation_adx = float(self.breakout_min_consol_adx_input.value())
        self.cvd_range_lookback_input.setValue(_read_setting("cvd_range_lookback_bars", 30, int))
        self.cvd_breakout_buffer_input.setValue(_read_setting("cvd_breakout_buffer", 0.10, float))
        self.cvd_min_consol_bars_input.setValue(_read_setting("cvd_min_consol_bars", 15, int))
        self.cvd_max_range_ratio_input.setValue(_read_setting("cvd_max_range_ratio", 0.80, float))
        self.cvd_conviction_score_input.setValue(_read_setting("cvd_conviction_score", 3, int))
        self.cvd_vol_expansion_mult_input.setValue(_read_setting("cvd_vol_expansion_mult", 1.15, float))
        self.cvd_atr_expansion_pct_input.setValue(_read_setting("cvd_atr_expansion_pct", 0.05, float))
        self.cvd_htf_bars_input.setValue(_read_setting("cvd_htf_bars", 5, int))
        self.cvd_regime_adx_block_input.setValue(_read_setting("cvd_regime_adx_block", 30.0, float))
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

        show_cpr_default = _read_setting("show_cpr", True, bool)
        self.show_cpr_lines_check.setChecked(_read_setting("show_cpr_lines", show_cpr_default, bool))
        self.show_cpr_labels_check.setChecked(_read_setting("show_cpr_labels", show_cpr_default, bool))
        self.cpr_narrow_threshold_input.setValue(
            _read_setting("cpr_narrow_threshold", self._cpr_narrow_threshold, float)
        )
        self.cpr_wide_threshold_input.setValue(
            _read_setting("cpr_wide_threshold", self._cpr_wide_threshold, float)
        )
        self._show_cpr_lines = self.show_cpr_lines_check.isChecked()
        self._show_cpr_labels = self.show_cpr_labels_check.isChecked()
        self._cpr_narrow_threshold = float(self.cpr_narrow_threshold_input.value())
        self._cpr_wide_threshold = float(self.cpr_wide_threshold_input.value())

        defaults = self._default_cpr_strategy_priorities()
        self._cpr_strategy_priorities = {}
        for list_key in self.CPR_PRIORITY_LIST_LABELS.keys():
            self._cpr_strategy_priorities[list_key] = {}
            for strategy_key in self.STRATEGY_PRIORITY_KEYS:
                value = _read_setting(
                    f"cpr_priority_{list_key}_{strategy_key}",
                    defaults[list_key][strategy_key],
                    int,
                )
                self._cpr_strategy_priorities[list_key][strategy_key] = int(value)
                spin = self.cpr_priority_inputs.get((list_key, strategy_key))
                if spin is not None:
                    spin.setValue(int(value))

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
        self.hybrid_exit_enabled_check.blockSignals(False)
        self.max_giveback_atr_reversal_check.blockSignals(False)
        self.max_giveback_ema_cross_check.blockSignals(False)
        self.max_giveback_atr_divergence_check.blockSignals(False)
        self.max_giveback_range_breakout_check.blockSignals(False)
        self.dynamic_exit_atr_reversal_check.blockSignals(False)
        self.dynamic_exit_ema_cross_check.blockSignals(False)
        self.dynamic_exit_atr_divergence_check.blockSignals(False)
        self.dynamic_exit_range_breakout_check.blockSignals(False)
        self.dynamic_exit_cvd_range_breakout_check.blockSignals(False)
        self.dynamic_exit_open_drive_check.blockSignals(False)
        self.trend_exit_adx_min_input.blockSignals(False)
        self.trend_exit_atr_ratio_min_input.blockSignals(False)
        self.trend_exit_confirm_bars_input.blockSignals(False)
        self.trend_exit_min_profit_input.blockSignals(False)
        self.trend_exit_vol_drop_pct_input.blockSignals(False)
        self.trend_exit_breakdown_bars_input.blockSignals(False)
        self.trend_exit_breakdown_lookback_input.blockSignals(False)
        self.trend_entry_consecutive_bars_input.blockSignals(False)
        self.trend_entry_require_adx_slope_check.blockSignals(False)
        self.trend_entry_require_vol_slope_check.blockSignals(False)
        self.automation_route_combo.blockSignals(False)
        self.automation_order_type_combo.blockSignals(False)
        self.automation_start_time_hour_input.blockSignals(False)
        self.automation_start_time_minute_input.blockSignals(False)
        self.automation_cutoff_time_hour_input.blockSignals(False)
        self.automation_cutoff_time_minute_input.blockSignals(False)
        self.atr_base_ema_input.blockSignals(False)
        self.atr_distance_input.blockSignals(False)
        self.cvd_atr_distance_input.blockSignals(False)
        self.atr_extension_threshold_input.blockSignals(False)
        self.atr_flat_velocity_pct_input.blockSignals(False)
        self.cvd_ema_gap_input.blockSignals(False)
        self.ema_cross_use_parent_mask_check.blockSignals(False)
        self.setup_cvd_value_mode_combo.blockSignals(False)
        self.signal_filter_combo.blockSignals(False)
        self.atr_marker_filter_combo.blockSignals(False)
        self.setup_signal_filter_combo.blockSignals(False)
        self.setup_atr_marker_filter_combo.blockSignals(False)
        self.range_lookback_input.blockSignals(False)  # 🆕 NEW
        self.breakout_switch_mode_combo.blockSignals(False)
        self.atr_skip_limit_input.blockSignals(False)
        self.atr_trailing_step_input.blockSignals(False)
        self.deploy_mode_combo.blockSignals(False)
        self.min_confidence_input.blockSignals(False)
        self.canary_ratio_input.blockSignals(False)
        self.health_alert_threshold_input.blockSignals(False)
        self.strategy_weight_decay_input.blockSignals(False)
        self.strategy_weight_floor_input.blockSignals(False)
        self.drift_window_input.blockSignals(False)
        self.hide_simulator_btn_check.blockSignals(False)
        self.hide_tick_backtest_controls_check.blockSignals(False)
        self.chop_filter_atr_reversal_check.blockSignals(False)
        self.chop_filter_ema_cross_check.blockSignals(False)
        self.chop_filter_atr_divergence_check.blockSignals(False)
        self.chop_filter_cvd_range_breakout_check.blockSignals(False)
        self.open_drive_enabled_check.blockSignals(False)
        self.open_drive_time_hour_input.blockSignals(False)
        self.open_drive_time_minute_input.blockSignals(False)
        self.open_drive_stack_enabled_check.blockSignals(False)
        self.open_drive_max_profit_giveback_input.blockSignals(False)
        self.open_drive_tick_drawdown_limit_input.blockSignals(False)
        self.breakout_min_consol_input.blockSignals(False)
        self.breakout_min_consol_adx_input.blockSignals(False)
        self.cvd_range_lookback_input.blockSignals(False)
        self.cvd_breakout_buffer_input.blockSignals(False)
        self.cvd_min_consol_bars_input.blockSignals(False)
        self.cvd_max_range_ratio_input.blockSignals(False)
        self.cvd_conviction_score_input.blockSignals(False)
        self.cvd_vol_expansion_mult_input.blockSignals(False)
        self.cvd_atr_expansion_pct_input.blockSignals(False)
        self.cvd_htf_bars_input.blockSignals(False)
        self.cvd_regime_adx_block_input.blockSignals(False)
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
        self.show_cpr_lines_check.blockSignals(False)
        self.show_cpr_labels_check.blockSignals(False)
        self.cpr_narrow_threshold_input.blockSignals(False)
        self.cpr_wide_threshold_input.blockSignals(False)
        for spin in getattr(self, "cpr_priority_inputs", {}).values():
            spin.blockSignals(False)
        for cb in self.setup_ema_default_checks.values():
            cb.blockSignals(False)

        self._apply_visual_settings()
        self._update_atr_reversal_markers()

        # ── Restore trend change markers toggle ───────────────────────────────
        if hasattr(self, "show_trend_change_markers_check"):
            self.show_trend_change_markers_check.blockSignals(True)
            self.show_trend_change_markers_check.setChecked(
                _read_setting("show_trend_change_markers", False, bool)
            )
            self.show_trend_change_markers_check.blockSignals(False)

        # ── Regime engine: restore persisted thresholds & strategy matrix ──
        _regime_scalar_keys = [
            "regime_enabled", "regime_adx_strong", "regime_adx_weak",
            "regime_adx_confirm", "regime_atr_window", "regime_atr_high",
            "regime_atr_low", "regime_vol_confirm",
            "regime_open_drive_end", "regime_morning_end", "regime_midday_end",
            "regime_afternoon_end", "regime_pre_close_end",
        ]
        _regime_matrix_keys = [
            f"regime_matrix_{trend}_{vol}_{strategy}"
            for trend in ("STRONG_TREND", "WEAK_TREND", "CHOP")
            for vol in ("HIGH_VOL", "NORMAL_VOL", "LOW_VOL")
            for strategy in ("atr_reversal", "atr_divergence", "ema_cross", "range_breakout")
        ]
        regime_dict = {}
        for k in _regime_scalar_keys + _regime_matrix_keys:
            v = _read_setting(k, None)
            if v is not None:
                regime_dict[k] = v
        if regime_dict:
            self._regime_settings_from_dict(regime_dict)
        self._apply_regime_config()

        self._setup_values_ready = True
        if migrated_values:
            self._persist_setup_values()
            self._mark_setup_json_migrated()
        self._on_automation_settings_changed()
        self._log_active_priority_list_if_needed()

    def _persist_setup_values(self):
        if not getattr(self, "_setup_values_ready", False):
            return

        key_prefix = self._settings_key_prefix()
        global_key_prefix = self._global_settings_key_prefix()

        values_to_persist = {
            "enabled": self.automate_toggle.isChecked(),
            "stoploss_points": int(self.automation_stoploss_input.value()),
            "max_profit_giveback_points": int(self.max_profit_giveback_input.value()),
            "hybrid_exit_enabled": self.hybrid_exit_enabled_check.isChecked(),
            "max_profit_giveback_strategies": self._selected_max_giveback_strategies(),
            "dynamic_exit_trend_following_strategies": self._selected_dynamic_exit_strategies(),
            "trend_exit_adx_min": float(self.trend_exit_adx_min_input.value()),
            "trend_exit_atr_ratio_min": float(self.trend_exit_atr_ratio_min_input.value()),
            "trend_exit_confirm_bars": int(self.trend_exit_confirm_bars_input.value()),
            "trend_exit_min_profit": float(self.trend_exit_min_profit_input.value()),
            "trend_exit_vol_drop_pct": float(self.trend_exit_vol_drop_pct_input.value()),
            "trend_exit_breakdown_bars": int(self.trend_exit_breakdown_bars_input.value()),
            "trend_exit_breakdown_lookback": int(self.trend_exit_breakdown_lookback_input.value()),
            "trend_entry_consecutive_bars": int(self.trend_entry_consecutive_bars_input.value()),
            "trend_entry_require_adx_slope": self.trend_entry_require_adx_slope_check.isChecked(),
            "trend_entry_require_vol_slope": self.trend_entry_require_vol_slope_check.isChecked(),
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "order_type": self.automation_order_type_combo.currentData() or self.ORDER_TYPE_MARKET,
            "automation_start_hour": int(self.automation_start_time_hour_input.value()),
            "automation_start_minute": int(self.automation_start_time_minute_input.value()),
            "automation_cutoff_hour": int(self.automation_cutoff_time_hour_input.value()),
            "automation_cutoff_minute": int(self.automation_cutoff_time_minute_input.value()),
            "atr_base_ema": int(self.atr_base_ema_input.value()),
            "atr_distance": float(self.atr_distance_input.value()),
            "cvd_atr_distance": float(self.cvd_atr_distance_input.value()),
            "atr_extension_threshold": float(self.atr_extension_threshold_input.value()),
            "atr_flat_velocity_pct": float(self.atr_flat_velocity_pct_input.value()),
            "cvd_ema_gap": int(self.cvd_ema_gap_input.value()),
            "ema_cross_use_parent_mask": self.ema_cross_use_parent_mask_check.isChecked(),
            "cvd_value_mode": self.setup_cvd_value_mode_combo.currentData() or self.CVD_VALUE_MODE_RAW,
            "signal_filter": self._selected_signal_filter(),
            "signal_filters": self._selected_signal_filters(),
            "atr_marker_filter": self.atr_marker_filter_combo.currentData() or self.ATR_MARKER_CONFLUENCE_ONLY,
            # 🆕 Persist range breakout settings
            "range_lookback": int(self.range_lookback_input.value()),
            "breakout_switch_mode": self._selected_breakout_switch_mode(),
            "atr_skip_limit": int(self.atr_skip_limit_input.value()),
            "atr_trailing_step_points": float(self.atr_trailing_step_input.value()),
            "deploy_mode": self.deploy_mode_combo.currentData() or "canary",
            "min_confidence_for_live": float(self.min_confidence_input.value()),
            "canary_live_ratio": float(self.canary_ratio_input.value()),
            "health_alert_threshold": float(self.health_alert_threshold_input.value()),
            "strategy_weight_decay_lambda": float(self.strategy_weight_decay_input.value()),
            "strategy_weight_floor": float(self.strategy_weight_floor_input.value()),
            "drift_feature_window": int(self.drift_window_input.value()),
            "hide_simulator_button": self.hide_simulator_btn_check.isChecked(),
            "hide_tick_backtest_controls": self.hide_tick_backtest_controls_check.isChecked(),
            "stacker_enabled": self.stacker_enabled_check.isChecked(),
            "stacker_step_points": int(self.stacker_step_input.value()),
            "stacker_max_stacks": int(self.stacker_max_input.value()),
            "harvest_enabled": self.harvest_enabled_check.isChecked(),
            "harvest_threshold_rupees": float(self.harvest_threshold_input.value()),
            "open_drive_enabled": self.open_drive_enabled_check.isChecked(),
            "open_drive_entry_hour": int(self.open_drive_time_hour_input.value()),
            "open_drive_entry_minute": int(self.open_drive_time_minute_input.value()),
            "open_drive_stack_enabled": self.open_drive_stack_enabled_check.isChecked(),
            "open_drive_max_profit_giveback_points": int(self.open_drive_max_profit_giveback_input.value()),
            "open_drive_tick_drawdown_limit_points": int(self.open_drive_tick_drawdown_limit_input.value()),
            # 🆕 Chop filter per-strategy
            "chop_filter_atr_reversal": self.chop_filter_atr_reversal_check.isChecked(),
            "chop_filter_ema_cross": self.chop_filter_ema_cross_check.isChecked(),
            "chop_filter_atr_divergence": self.chop_filter_atr_divergence_check.isChecked(),
            "chop_filter_cvd_range_breakout": self.chop_filter_cvd_range_breakout_check.isChecked(),
            # 🆕 Breakout consolidation
            "breakout_min_consolidation_minutes": int(self.breakout_min_consol_input.value()),
            "breakout_min_consolidation_adx": float(self.breakout_min_consol_adx_input.value()),
            "cvd_range_lookback_bars": int(self.cvd_range_lookback_input.value()),
            "cvd_breakout_buffer": float(self.cvd_breakout_buffer_input.value()),
            "cvd_min_consol_bars": int(self.cvd_min_consol_bars_input.value()),
            "cvd_max_range_ratio": float(self.cvd_max_range_ratio_input.value()),
            "cvd_conviction_score": int(self.cvd_conviction_score_input.value()),
            "cvd_vol_expansion_mult": float(self.cvd_vol_expansion_mult_input.value()),
            "cvd_atr_expansion_pct": float(self.cvd_atr_expansion_pct_input.value()),
            "cvd_htf_bars": int(self.cvd_htf_bars_input.value()),
            "cvd_regime_adx_block": float(self.cvd_regime_adx_block_input.value()),
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
            "show_cpr": self.show_cpr_lines_check.isChecked() and self.show_cpr_labels_check.isChecked(),
            "show_cpr_lines": self.show_cpr_lines_check.isChecked(),
            "show_cpr_labels": self.show_cpr_labels_check.isChecked(),
            "cpr_narrow_threshold": float(self.cpr_narrow_threshold_input.value()),
            "cpr_wide_threshold": float(self.cpr_wide_threshold_input.value()),
            "show_trend_change_markers": (
                self.show_trend_change_markers_check.isChecked()
                if hasattr(self, "show_trend_change_markers_check") else False
            ),
            **self._regime_settings_to_dict(),
        }

        for list_key, strategy_map in self._cpr_strategy_priorities.items():
            for strategy_key, priority_value in strategy_map.items():
                values_to_persist[f"cpr_priority_{list_key}_{strategy_key}"] = int(priority_value)

        for period, cb in self.setup_ema_default_checks.items():
            values_to_persist[f"ema_default_{period}"] = cb.isChecked()

        for name, value in values_to_persist.items():
            self._settings.setValue(f"{key_prefix}/{name}", value)
            self._settings.setValue(f"{global_key_prefix}/{name}", value)

        self._settings.sync()

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
        active_priority_list, strategy_priorities = self._active_strategy_priorities()
        self.automation_state_signal.emit({
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "enabled": self.automate_toggle.isChecked(),
            "stoploss_points": float(self.automation_stoploss_input.value()),
            "max_profit_giveback_points": float(self.max_profit_giveback_input.value()),
            "hybrid_exit_enabled": self.hybrid_exit_enabled_check.isChecked(),
            "max_profit_giveback_strategies": self._selected_max_giveback_strategies(),
            "dynamic_exit_trend_following_strategies": self._selected_dynamic_exit_strategies(),
            "trend_exit_adx_min": float(self.trend_exit_adx_min_input.value()),
            "trend_exit_atr_ratio_min": float(self.trend_exit_atr_ratio_min_input.value()),
            "trend_exit_confirm_bars": int(self.trend_exit_confirm_bars_input.value()),
            "trend_exit_min_profit": float(self.trend_exit_min_profit_input.value()),
            "trend_exit_vol_drop_pct": float(self.trend_exit_vol_drop_pct_input.value()),
            "trend_exit_breakdown_bars": int(self.trend_exit_breakdown_bars_input.value()),
            "trend_exit_breakdown_lookback": int(self.trend_exit_breakdown_lookback_input.value()),
            "trend_entry_consecutive_bars": int(self.trend_entry_consecutive_bars_input.value()),
            "trend_entry_require_adx_slope": self.trend_entry_require_adx_slope_check.isChecked(),
            "trend_entry_require_vol_slope": self.trend_entry_require_vol_slope_check.isChecked(),
            "open_drive_max_profit_giveback_points": float(self.open_drive_max_profit_giveback_input.value()),
            "open_drive_tick_drawdown_limit_points": float(self.open_drive_tick_drawdown_limit_input.value()),
            "atr_trailing_step_points": float(self.atr_trailing_step_input.value()),
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "order_type": self.automation_order_type_combo.currentData() or self.ORDER_TYPE_MARKET,
            "automation_start_hour": int(self.automation_start_time_hour_input.value()),
            "automation_start_minute": int(self.automation_start_time_minute_input.value()),
            "automation_cutoff_hour": int(self.automation_cutoff_time_hour_input.value()),
            "automation_cutoff_minute": int(self.automation_cutoff_time_minute_input.value()),
            "signal_filter": self._selected_signal_filter(),
            "signal_filters": self._selected_signal_filters(),
            "priority_list": active_priority_list,
            "strategy_priorities": strategy_priorities,
        })
        self._live_stacker_state = None
        # ── Sync status bar ──────────────────────────────────────────────
        if hasattr(self, "_status_bar"):
            if self.automate_toggle.isChecked():
                self._status_bar.set("mode", "AUTO", "teal")
            else:
                self._status_bar.set("mode", "MANUAL", "dim")

        # Rebuild hybrid exit engine from current UI settings.
        self._live_hybrid_engine = self._build_hybrid_engine_from_ui()

    def _build_hybrid_engine_from_ui(self) -> HybridExitEngine | None:
        """Build hybrid engine from UI controls. Returns None if disabled."""
        if not (
            getattr(self, "hybrid_exit_enabled_check", None)
            and self.hybrid_exit_enabled_check.isChecked()
        ):
            return None

        def _v(attr: str, default):
            widget = getattr(self, attr, None)
            if widget is None:
                return default
            with suppress(Exception):
                return widget.value()
            return default

        def _b(attr: str, default: bool = True) -> bool:
            widget = getattr(self, attr, None)
            if widget is None:
                return default
            with suppress(Exception):
                return bool(widget.isChecked())
            return default

        return HybridExitEngine(HybridExitConfig(
            adx_unlock_threshold=float(_v("hybrid_adx_unlock_input", 28.0)),
            atr_ratio_unlock_threshold=float(_v("hybrid_atr_ratio_input", 1.15)),
            adx_rising_bars=int(_v("hybrid_adx_rising_input", 2)),
            velocity_threshold=float(_v("hybrid_vel_thresh_input", 1.5)),
            velocity_collapse_ratio=float(_v("hybrid_vel_collapse_input", 0.5)),
            extreme_extension_atr_multiple=float(_v("hybrid_ext_mult_input", 3.0)),
            profit_giveback_ratio=float(_v("hybrid_profit_ratio_input", 0.30)),
            atr_giveback_multiple=float(_v("hybrid_atr_giveback_input", 1.2)),
            base_giveback_pct=float(_v("hybrid_base_pct_input", 0.003)),
            adx_breakdown_lookback=int(_v("hybrid_breakdown_lb_input", 10)),
            atr_breakdown_ratio=float(_v("hybrid_atr_bdown_input", 0.90)),
            ema_breakdown_crosses=_b("hybrid_ema_bdown_check", True),
        ))

    def reset_stacker(self):
        """Called by coordinator when anchor trade fully exits."""
        self._live_stacker_state = None
        self._live_stacker_side = None
        self._live_stacker_strategy_type = None
        logger.debug(
            "[STACKER] State reset after anchor exit for token=%s",
            self.instrument_token,
        )

    def _set_live_trade_state(self, state: str, trade_info: dict):
        """Receive coordinator trade updates and keep stacker anchor aligned to fill."""
        self._live_trade_info = trade_info if isinstance(trade_info, dict) else None

        if state == "entered" and self._live_trade_info is not None:
            if self._live_hybrid_engine is not None:
                self._live_trade_info.update(HybridExitState().to_dict())
            self._live_close_history.clear()

        # Re-anchor stacker to actual fill price from coordinator
        if state == "entered" and self._live_stacker_state is not None:
            actual_entry = (trade_info or {}).get("entry_underlying")
            if actual_entry and actual_entry > 0:
                self._live_stacker_state.anchor_entry_price = float(actual_entry)
                # Reset trigger from this correct anchor
                self._live_stacker_state.next_trigger_points = self._live_stacker_state.step_points
                logger.info(
                    "[STACKER] Re-anchored to actual fill: %.2f (was bar close)",
                    actual_entry,
                )

    def _check_live_hybrid_exit(
        self,
        current_price: float,
        ema51: float,
        atr: float,
        adx: float,
    ) -> None:
        """Run hybrid exit evaluation for live trade on bar close."""
        if self._live_hybrid_engine is None:
            return
        if self._live_trade_info is None:
            return
        if not self.automate_toggle.isChecked():
            return

        trade = self._live_trade_info
        strategy_type = trade.get("signal_type") or trade.get("strategy_type") or ""
        if strategy_type not in set(self._selected_dynamic_exit_strategies()):
            return

        signal_side = trade.get("signal_side", "long")
        entry_price = float(trade.get("entry_underlying") or trade.get("entry_price", current_price))
        favorable_move = current_price - entry_price if signal_side == "long" else entry_price - current_price

        self._live_close_history.append(current_price)
        max_vel_window = self._live_hybrid_engine.config.velocity_window + 2
        if len(self._live_close_history) > max_vel_window:
            self._live_close_history.pop(0)

        state = HybridExitState.from_dict(trade)
        decision = self._live_hybrid_engine.evaluate(
            state=state,
            favorable_move=favorable_move,
            entry_price=entry_price,
            close=current_price,
            ema51=ema51,
            atr=atr,
            adx=adx,
            signal_side=signal_side,
            velocity_window_close=list(self._live_close_history),
        )
        trade.update(decision.updated_state.to_dict())

        logger.debug(
            "[HYBRID EXIT] token=%s side=%s strategy=%s phase=%s profit=%.1f peak=%.1f exit=%s reason=%s",
            self.instrument_token,
            signal_side,
            strategy_type,
            decision.phase_name,
            favorable_move,
            decision.peak_profit,
            decision.exit_now,
            decision.exit_reason,
        )

        if decision.exit_now:
            active_priority_list, strategy_priorities = self._active_strategy_priorities()
            payload = {
                "instrument_token": self.instrument_token,
                "symbol": self.symbol,
                "signal_side": signal_side,
                "signal_type": strategy_type,
                "priority_list": active_priority_list,
                "strategy_priorities": strategy_priorities,
                "signal_x": 0.0,
                "price_close": current_price,
                "stoploss_points": float(self.automation_stoploss_input.value()),
                "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
                "order_type": self.automation_order_type_combo.currentData() or self.ORDER_TYPE_MARKET,
                "timestamp": f"hybrid_exit_{decision.exit_reason}",
                "is_exit": True,
                "exit_reason": decision.exit_reason,
            }
            logger.info(
                "[HYBRID EXIT] FIRING EXIT token=%s side=%s reason=%s price=%.2f",
                self.instrument_token,
                signal_side,
                decision.exit_reason,
                current_price,
            )
            self.automation_signal.emit(payload)
            self._live_trade_info = None
            self._live_close_history.clear()

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
        self._chop_filter_cvd_range_breakout = self.chop_filter_cvd_range_breakout_check.isChecked()
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
        self.signal_governance.health_alert_threshold = float(self.health_alert_threshold_input.value())
        self.signal_governance.strategy_weight_decay_lambda = float(self.strategy_weight_decay_input.value())
        self.signal_governance._strategy_weight_floor = float(self.strategy_weight_floor_input.value())
        self.signal_governance.feature_window = int(self.drift_window_input.value())
        # keep quality_scorer threshold in sync with min_confidence
        self.signal_governance.quality_scorer.min_score = float(self.min_confidence_input.value())
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

    def _on_cpr_settings_changed(self, *_):
        self._show_cpr_lines = self.show_cpr_lines_check.isChecked()
        self._show_cpr_labels = self.show_cpr_labels_check.isChecked()
        self._cpr_narrow_threshold = float(self.cpr_narrow_threshold_input.value())
        self._cpr_wide_threshold = float(self.cpr_wide_threshold_input.value())
        if hasattr(self, "chart_line_width_input"):
            self._persist_setup_values()
        self._log_active_priority_list_if_needed()
        self._render_cpr_levels()
        self._on_automation_settings_changed()

    def _on_cpr_priorities_changed(self, *_):
        updated: dict[str, dict[str, int]] = {}
        for list_key in self.CPR_PRIORITY_LIST_LABELS.keys():
            updated[list_key] = {}
            for strategy_key in self.STRATEGY_PRIORITY_KEYS:
                spin = self.cpr_priority_inputs.get((list_key, strategy_key))
                value = int(spin.value()) if spin is not None else 0
                updated[list_key][strategy_key] = value
        self._cpr_strategy_priorities = updated
        self._persist_setup_values()
        self._update_cpr_status_bar()
        self._on_automation_settings_changed()

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
        self._current_session_last_cvd_value = None
        self._current_session_last_price_value = None
        self._current_session_last_x = None
        self._current_session_cumulative_volume = 0
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
        self._current_session_last_cvd_value = None
        self._current_session_last_price_value = None
        self._current_session_last_x = None
        self._current_session_cumulative_volume = 0
        self._current_session_volume_scale = 1.0
        self._last_plot_x_indices = []
        self._load_and_plot(force=True)

    def _on_date_changed(self, current_date: datetime, previous_date: datetime):
        if self._uploaded_tick_data is not None:
            self.current_date = current_date
            self.previous_date = previous_date
            self.live_mode = False
            self.refresh_timer.stop()
            self._load_and_plot(force=True)
            return

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

    def _on_upload_tick_csv(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Tick CSV",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_path:
            return

        try:
            tick_df = load_tick_csv(file_path)
            if tick_df.empty:
                raise ValueError("No valid tick rows found in file.")

            self._uploaded_tick_data = tick_df
            self._uploaded_tick_source = file_path
            self.live_mode = False
            self.refresh_timer.stop()
            self.tick_clear_btn.setEnabled(True)
            self.ws_status_label.setText(f"Tick file loaded: {file_path.split('/')[-1]}")
            self._load_and_plot(force=True)
        except Exception as exc:
            logger.error("Failed to load tick CSV: %s", exc)
            self.ws_status_label.setText(f"Tick file error: {exc}")

    def _clear_uploaded_tick_data(self):
        if self._uploaded_tick_data is None:
            return
        self._uploaded_tick_data = None
        self._uploaded_tick_source = ""
        self.tick_clear_btn.setEnabled(False)
        self._historical_loaded_once = False
        self.current_date, self.previous_date = self.navigator.get_dates()
        self._on_date_changed(self.current_date, self.previous_date)

    def _load_from_uploaded_ticks(self):
        if self._uploaded_tick_data is None:
            return False

        cvd_df, price_df = build_price_cvd_from_ticks(self._uploaded_tick_data, self.timeframe_minutes)
        if cvd_df.empty or price_df.empty:
            self._on_fetch_error("empty_df")
            return True

        cvd_df = cvd_df.copy()
        price_df = price_df.copy()
        cvd_df["session"] = cvd_df.index.date
        price_df["session"] = price_df.index.date

        sessions = sorted(cvd_df["session"].unique())
        if not sessions:
            self._on_fetch_error("no_sessions")
            return True

        focus_mode = not self.btn_focus.isChecked()
        selected_sessions = sessions[-1:] if focus_mode else sessions[-2:]

        cvd_out = cvd_df[cvd_df["session"].isin(selected_sessions)].copy()
        price_out = price_df[price_df["session"].isin(selected_sessions)].copy()

        prev_close = 0.0
        previous_day_cpr = None
        if len(selected_sessions) >= 2:
            prev_data = cvd_out[cvd_out["session"] == selected_sessions[0]]
            if not prev_data.empty:
                prev_close = float(prev_data["close"].iloc[-1])
            prev_price = price_out[price_out["session"] == selected_sessions[0]]
            if not prev_price.empty:
                previous_day_cpr = CPRCalculator.get_previous_day_cpr(prev_price)

        self._on_fetch_result(cvd_out, price_out, prev_close, previous_day_cpr)
        return True

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
        combo = self._resolve_signal_filter_combo_from_object(obj)
        if combo is not None:
            if event.type() == QEvent.Type.MouseButtonPress and obj is combo.lineEdit():
                if not combo.view().isVisible():
                    combo.showPopup()
                return True

            # Swallow release on the line edit so Qt doesn't immediately
            # re-handle the same click and collapse the popup.
            if event.type() == QEvent.Type.MouseButtonRelease and obj is combo.lineEdit():
                return True

            # Keep the checkable popup open while users tick/untick options.
            # We handle state changes ourselves through `view().pressed`.
            if event.type() == QEvent.Type.MouseButtonRelease and obj is combo.view().viewport():
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
            view = combo.view()
            if view is not None and (obj is view or obj is view.viewport()):
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
            ("CVD Range Breakout", self.SIGNAL_FILTER_CVD_BREAKOUT_ONLY),
            ("ATR Divergence", self.SIGNAL_FILTER_OTHERS),
            ("Open Drive", self.SIGNAL_FILTER_OPEN_DRIVE_ONLY),
        ]

    def _strategy_filter_values(self) -> list[str]:
        return [
            self.SIGNAL_FILTER_ATR_ONLY,
            self.SIGNAL_FILTER_EMA_CROSS_ONLY,
            self.SIGNAL_FILTER_BREAKOUT_ONLY,
            self.SIGNAL_FILTER_CVD_BREAKOUT_ONLY,
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
                min-height: 22px;
                padding: 1px 8px;
            }
            QComboBox QAbstractItemView::item {
                min-height: 22px;
                padding: 3px 8px;
            }
        """)

        for label, value in self._signal_filter_options():
            combo.addItem(label, value)
            idx = combo.model().index(combo.count() - 1, 0)
            combo.model().setData(idx, Qt.Checked, Qt.CheckStateRole)

        combo.lineEdit().installEventFilter(self)
        combo.view().viewport().installEventFilter(self)
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
        else:
            combo.model().setData(index, next_state, Qt.CheckStateRole)
            self._sync_select_all_check_state(combo)

        self._refresh_signal_filter_combo_text(combo)
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

        self._sync_select_all_check_state(combo)
        self._refresh_signal_filter_combo_text(combo)

    def _refresh_signal_filter_combo_text(self, combo: QComboBox):
        selected = self._checked_signal_filters(combo)
        total = len(self._strategy_filter_values())
        if len(selected) == total:
            text = "All Signals"
        elif not selected:
            text = "No Signals Selected"
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
            return selected
        value_str = str(value)
        if value_str == self.SIGNAL_FILTER_ALL:
            return self._strategy_filter_values()
        return [value_str] if value_str in set(self._strategy_filter_values()) else []

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
    def _dynamic_exit_strategy_defaults(cls) -> tuple[str, ...]:
        return (
            cls.MAX_GIVEBACK_STRATEGY_EMA_CROSS,
            cls.MAX_GIVEBACK_STRATEGY_RANGE_BREAKOUT,
            cls.MAX_GIVEBACK_STRATEGY_CVD_RANGE_BREAKOUT,
        )

    def _selected_dynamic_exit_strategies(self) -> list[str]:
        selected: list[str] = []
        if self.dynamic_exit_atr_reversal_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_ATR_REVERSAL)
        if self.dynamic_exit_ema_cross_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_EMA_CROSS)
        if self.dynamic_exit_atr_divergence_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_ATR_DIVERGENCE)
        if self.dynamic_exit_range_breakout_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_RANGE_BREAKOUT)
        if self.dynamic_exit_cvd_range_breakout_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_CVD_RANGE_BREAKOUT)
        if self.dynamic_exit_open_drive_check.isChecked():
            selected.append(self.MAX_GIVEBACK_STRATEGY_OPEN_DRIVE)
        return selected

    def _apply_dynamic_exit_strategy_selection(self, strategies: list[str]):
        selected = set(strategies or [])
        self.dynamic_exit_atr_reversal_check.setChecked(
            self.MAX_GIVEBACK_STRATEGY_ATR_REVERSAL in selected
        )
        self.dynamic_exit_ema_cross_check.setChecked(
            self.MAX_GIVEBACK_STRATEGY_EMA_CROSS in selected
        )
        self.dynamic_exit_atr_divergence_check.setChecked(
            self.MAX_GIVEBACK_STRATEGY_ATR_DIVERGENCE in selected
        )
        self.dynamic_exit_range_breakout_check.setChecked(
            self.MAX_GIVEBACK_STRATEGY_RANGE_BREAKOUT in selected
        )
        self.dynamic_exit_cvd_range_breakout_check.setChecked(
            self.MAX_GIVEBACK_STRATEGY_CVD_RANGE_BREAKOUT in selected
        )
        self.dynamic_exit_open_drive_check.setChecked(
            self.MAX_GIVEBACK_STRATEGY_OPEN_DRIVE in selected
        )

    @classmethod
    def _max_giveback_strategy_defaults(cls) -> tuple[str, ...]:
        return ()

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

        if self._uploaded_tick_data is not None:
            self._load_from_uploaded_ticks()
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
        self._chart_ready = False
        self._tick_repaint_timer.stop()

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

    def _on_fetch_result(self, cvd_df, price_df, prev_close, previous_day_cpr):
        self._is_loading = False
        self._latest_previous_day_cpr = previous_day_cpr
        self._log_active_priority_list_if_needed()
        self._plot_data(cvd_df, price_df, prev_close)
        self._seed_live_cvd_from_historical()

        self._historical_loaded_once = True
        if self.live_mode:
            self._last_live_refresh_minute = datetime.now().replace(second=0, microsecond=0)
            # 🔥 Remove live ticks that are now covered by historical data
            self._cleanup_overlapping_ticks()
            self._recalibrate_live_cvd_offset()

        # Seal opened: flush any ticks that arrived while historical load was running.
        self._chart_ready = True
        if self._pending_tick_buffer:
            while self._pending_tick_buffer:
                tick_ts, buffered_cvd, buffered_price = self._pending_tick_buffer.popleft()
                self._process_live_tick(buffered_cvd, buffered_price, tick_ts, allow_repaint=False)
            self._plot_live_ticks_only()

    def _seed_live_cvd_from_historical(self):
        if not self.live_mode or not self.cvd_engine:
            return
        if self._current_session_last_cvd_value is None or self._current_session_last_price_value is None:
            return
        self.cvd_engine.seed_from_historical(
            token=self.instrument_token,
            cvd_value=float(self._current_session_last_cvd_value),
            last_price=float(self._current_session_last_price_value),
            cumulative_volume=int(self._current_session_cumulative_volume),
            session_day=datetime.now().date(),
        )

    def _on_fetch_error(self, msg: str):
        """Called on the GUI thread when background fetch fails."""
        if msg == "auth_failed":
            if not self._cvd_auth_error_logged:
                logger.warning(
                    "CVD historical fetch skipped: authentication for historical API failed. "
                    "Live chart updates will continue from WebSocket ticks."
                )
                self._cvd_auth_error_logged = True
            return
        if msg not in ("no_data", "empty_df", "no_sessions"):
            logger.error("Failed to load CVD data: %s", msg)

    def _on_fetch_done(self):
        worker = getattr(self, "_fetch_worker", None)

        if worker is not None:
            # Ensure thread fully stopped
            worker.quit_thread()

        self._fetch_worker = None
        self._is_loading = False

    def _ts_to_x(self, ts: datetime) -> float:
        """Canonical timestamp → x conversion used by both historical and live plotting."""
        if self._current_session_start_ts is None:
            return 0.0

        tick_ts = pd.Timestamp(ts)
        session_start = pd.Timestamp(self._current_session_start_ts)

        if tick_ts.tzinfo is not None:
            tick_ts = tick_ts.tz_localize(None)
        if session_start.tzinfo is not None:
            session_start = session_start.tz_localize(None)

        delta_minutes = (tick_ts - session_start).total_seconds() / 60.0
        return float(self._current_session_x_base) + delta_minutes

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

    def _recalibrate_live_cvd_offset(self):
        if self._current_session_last_cvd_value is None:
            return
        if not self._live_tick_points:
            self._live_cvd_offset = 0.0
            return

        latest_live_raw_cvd = float(self._live_tick_points[-1][1])
        self._live_cvd_offset = float(self._current_session_last_cvd_value) - latest_live_raw_cvd

    def _downsample_live_points(self, points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
        """
        Downsample live tick points while preserving time-order to avoid overlap lines.

        Uses stride-based uniform sampling which keeps points strictly time-ordered.
        No min/max bucket extremes — those cause the line to zigzag back in time.
        Always keeps first and last point for clean anchor + live-edge connection.
        """
        if len(points) <= self.LIVE_TICK_DOWNSAMPLE_TARGET:
            return points

        # Uniform stride: pick evenly spaced indices, always include first and last
        idx = np.linspace(0, len(points) - 1, self.LIVE_TICK_DOWNSAMPLE_TARGET, dtype=int)
        # linspace already includes 0 and len-1, so first/last are always kept
        return [points[i] for i in idx]

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
        self._clear_cpr_levels()
        self._clear_trend_change_markers()

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
        self._current_session_last_price_value = None
        self._current_session_last_x = None
        self._current_session_cumulative_volume = 0
        self._current_session_volume_scale = 1.0

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
                xs = [self._ts_to_x(ts) for ts in df_cvd_sess.index]
                self._current_session_last_cvd_value = float(cvd_y[-1]) if len(cvd_y) else None
                self._current_session_last_price_value = float(price_y[-1]) if len(price_y) else None
                self._current_session_last_x = float(xs[-1]) if xs else None  # anchor for live tick line
                self._current_session_cumulative_volume = int(cumulative_volume[-1]) if len(cumulative_volume) else 0
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

        if self._chart_ready:
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

    def _clear_cpr_levels(self):
        for line in self._cpr_lines:
            with suppress(Exception):
                self.price_plot.removeItem(line)
        for label in self._cpr_labels:
            with suppress(Exception):
                self.price_plot.removeItem(label)
        self._cpr_lines = []
        self._cpr_labels = []

    def _default_cpr_strategy_priorities(self) -> dict[str, dict[str, int]]:
        return {
            "narrow": {
                "open_drive": 1,
                "cvd_range_breakout": 2,
                "range_breakout": 3,
                "ema_cross": 4,
                "atr_divergence": 5,
                "atr_reversal": 6,
            },
            "neutral": {
                "open_drive": 1,
                "cvd_range_breakout": 2,
                "range_breakout": 3,
                "ema_cross": 4,
                "atr_divergence": 5,
                "atr_reversal": 6,
            },
            "wide": {
                "open_drive": 1,
                "atr_reversal": 2,
                "atr_divergence": 3,
                "cvd_range_breakout": 4,
                "range_breakout": 5,
                "ema_cross": 6,
            },
            "fallback": {
                "open_drive": 1,
                "cvd_range_breakout": 2,
                "range_breakout": 3,
                "ema_cross": 4,
                "atr_divergence": 5,
                "atr_reversal": 6,
            },
        }

    def _active_cpr_priority_list_key(self) -> str:
        cpr = self._latest_previous_day_cpr or {}
        width = cpr.get("range_width")
        if width is None:
            return "fallback"
        classification, _ = self._classify_cpr_width(float(width))
        return {
            "Narrow CPR": "narrow",
            "Neutral CPR": "neutral",
            "Wide CPR": "wide",
        }.get(classification, "fallback")

    def _active_strategy_priorities(self) -> tuple[str, dict[str, int]]:
        key = self._active_cpr_priority_list_key()
        priorities = self._cpr_strategy_priorities.get(key) or self._cpr_strategy_priorities.get("fallback", {})
        return key, dict(priorities)

    def _log_active_priority_list_if_needed(self):
        key, priorities = self._active_strategy_priorities()
        self._active_priority_list_key = key
        self._update_cpr_status_bar(key=key, priorities=priorities)
        if self._last_logged_priority_list_key == key:
            return
        self._last_logged_priority_list_key = key
        logger.info(
            "[AUTO] CPR priority list for %s (%s): %s",
            self.symbol,
            self.CPR_PRIORITY_LIST_LABELS.get(key, key.title()),
            priorities,
        )

    def _update_cpr_status_bar(self, key: str | None = None, priorities: dict[str, int] | None = None):
        if key is None or priorities is None:
            key, priorities = self._active_strategy_priorities()

        cpr_text = {
            "narrow": "Narrow",
            "wide": "Wide",
            "neutral": "Neutral",
            "fallback": "--",
        }.get(key, key.title())

        if hasattr(self, "cpr_status_label"):
            self.cpr_status_label.setText(f"CPR: {cpr_text}")

        ranked = sorted(
            priorities.items(),
            key=lambda item: (int(item[1]), self.STRATEGY_PRIORITY_KEYS.index(item[0])),
        )
        priority_text = ", ".join(
            f"{rank}. {self.STRATEGY_PRIORITY_LABELS.get(strategy_key, strategy_key)}"
            for strategy_key, rank in ranked
        ) if ranked else "--"

        if hasattr(self, "priority_order_label"):
            self.priority_order_label.setText(f"Priority order: {priority_text}")

    def _classify_cpr_width(self, width: float) -> tuple[str, str]:
        narrow = max(0.0, self._cpr_narrow_threshold)
        wide_cutoff = max(0.0, self._cpr_wide_threshold)
        if width < narrow:
            return "Narrow CPR", "#00E676"
        if width > wide_cutoff:
            return "Wide CPR", "#FF5252"
        return "Neutral CPR", "#FFD54F"

    def _render_cpr_levels(self):
        self._clear_cpr_levels()
        if (
            (not self._show_cpr_lines and not self._show_cpr_labels)
            or not self.all_timestamps
            or not self.all_price_data
        ):
            return

        data = pd.DataFrame({
            "timestamp": self.all_timestamps,
            "close": self.all_price_data,
            "high": self.all_price_high_data,
            "low": self.all_price_low_data,
        })
        data["session"] = pd.to_datetime(data["timestamp"]).dt.date

        focus_mode = not self.btn_focus.isChecked()
        sessions = list(dict.fromkeys(data["session"].tolist()))

        # 1D mode loads only the latest session for chart clarity. In that case,
        # use CPR computed from the previous trading day in the fetch worker.
        if len(sessions) == 1 and self._latest_previous_day_cpr:
            session_rows = data[data["session"] == sessions[0]]
            if session_rows.empty:
                return

            session_start_pos = int(session_rows.index[0])
            session_end_pos = int(session_rows.index[-1])
            if focus_mode:
                x_start = float(self._time_to_session_index(session_rows.iloc[0]["timestamp"]))
                x_end = float(self._time_to_session_index(session_rows.iloc[-1]["timestamp"]))
            else:
                x_start = float(session_start_pos)
                x_end = float(session_end_pos)

            if x_end <= x_start:
                x_end = x_start + 0.5

            self._draw_cpr_band(self._latest_previous_day_cpr, x_start, x_end)
            return

        for idx, session in enumerate(sessions):
            if idx == 0:
                continue
            prev_session = sessions[idx - 1]
            prev_day = data[data["session"] == prev_session]
            cpr = CPRCalculator.get_previous_day_cpr(prev_day)
            if not cpr:
                continue

            session_rows = data[data["session"] == session]
            if session_rows.empty:
                continue

            session_start_pos = int(session_rows.index[0])
            session_end_pos = int(session_rows.index[-1])
            if focus_mode:
                x_start = float(self._time_to_session_index(session_rows.iloc[0]["timestamp"]))
                x_end = float(self._time_to_session_index(session_rows.iloc[-1]["timestamp"]))
            else:
                x_start = float(session_start_pos)
                x_end = float(session_end_pos)

            # Ensure short sessions still render at least a tiny visible segment.
            if x_end <= x_start:
                x_end = x_start + 0.5

            self._draw_cpr_band(cpr, x_start, x_end)

    def _draw_cpr_band(self, cpr: dict, x_start: float, x_end: float):
        x_anchor = x_start
        levels = (("TC", cpr["tc"]), ("Pivot", cpr["pivot"]), ("BC", cpr["bc"]))
        for level_name, y_val in levels:
            line = pg.PlotDataItem(
                [x_start, x_end],
                [float(y_val), float(y_val)],
                pen=pg.mkPen("#90CAF9", width=1.2, style=Qt.DashLine),
            )
            line.setZValue(20)
            if self._show_cpr_lines:
                self.price_plot.addItem(line)
                self._cpr_lines.append(line)

            txt = TextItem(f"{level_name}: {float(y_val):.2f}", color="#90CAF9", anchor=(0, 1))
            txt.setPos(float(x_anchor), float(y_val))
            txt.setZValue(21)
            if self._show_cpr_labels:
                self.price_plot.addItem(txt)
                self._cpr_labels.append(txt)

        cpr_width = float(cpr["range_width"])
        classification, color = self._classify_cpr_width(cpr_width)
        class_label = TextItem(
            f"{classification} (W={cpr_width:.2f})",
            color=color,
            anchor=(1, 0),
        )
        # Keep CPR classification away from TC/Pivot/BC labels drawn at x_start.
        # Place it at x_end and offset above TC by a dynamic gap so it does not overlap.
        class_label_y = float(cpr["tc"]) + max(2.0, abs(cpr_width) * 0.35)
        class_label.setPos(float(x_end), class_label_y)
        class_label.setZValue(22)
        if self._show_cpr_labels:
            self.price_plot.addItem(class_label)
            self._cpr_labels.append(class_label)

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

        # Cache arrays needed by TrendChangeMarkersMixin._refresh_trend_change_markers
        _price_h = np.array(self.all_price_high_data, dtype=float)
        _price_l = np.array(self.all_price_low_data, dtype=float)
        _price_c = np.array(self.all_price_data, dtype=float)
        self._latest_x_arr   = np.array(x_indices, dtype=float)
        self._latest_atr_arr = calculate_atr(_price_h, _price_l, _price_c, period=14)
        self._latest_adx_arr = compute_adx(_price_h, _price_l, _price_c, period=14)

        self._update_atr_reversal_markers()
        self._update_ema_legends()
        self._render_cpr_levels()
        self._refresh_trend_change_markers()

    def _update_ema_legends(self):
        """EMA legends are disabled to keep chart area unobstructed."""
        return

    def _plot_live_ticks_only(self):
        """Plot tick-level CVD overlay on top of minute candles."""
        if not self._chart_ready:
            return

        if not self._live_tick_points:
            self.today_tick_curve.clear()
            self.price_today_tick_curve.clear()
            return

        if self._current_session_start_ts is None:
            return

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

        points = self._downsample_live_points(points)

        x_vals: list[float] = []
        y_vals: list[float] = []
        price_vals: list[float] = []
        price_map = {ts: px for ts, px in self._live_price_points}

        # ── Anchor point ────────────────────────────────────────────────────
        # Prepend the last historical candle's close as the first point of the
        # live tick line.  This eliminates the visual gap between the historical
        # curve and the live overlay — the two lines now share an exact endpoint.
        if (
            self._current_session_last_x is not None
            and self._current_session_last_cvd_value is not None
            and self._current_session_last_price_value is not None
        ):
            x_vals.append(self._current_session_last_x)
            y_vals.append(float(self._current_session_last_cvd_value))
            price_vals.append(float(self._current_session_last_price_value))
        # ────────────────────────────────────────────────────────────────────

        for ts, raw_cvd in points:
            tick_ts = _align_tick_ts(ts)

            x = self._ts_to_x(tick_ts.to_pydatetime())

            x_vals.append(x)
            y_vals.append(raw_cvd + self._live_cvd_offset)
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

        self._pending_live_tick = (float(cvd_value), float(last_price))
        if not self._cvd_tick_flush_timer.isActive():
            self._cvd_tick_flush_timer.start(33)

    def _flush_pending_cvd_tick(self):
        if self._pending_live_tick is None:
            return

        cvd_value, last_price = self._pending_live_tick
        self._pending_live_tick = None

        ts = datetime.now()
        should_forward = self._last_applied_tick_ts is None
        if not should_forward:
            elapsed = (ts - self._last_applied_tick_ts).total_seconds()
            price_changed = abs(last_price - (self._last_applied_price_value or last_price)) > 0.01
            cvd_changed = abs(cvd_value - (self._last_applied_cvd_value or cvd_value)) > 1.0
            should_forward = price_changed or cvd_changed or elapsed > 1.0

        if should_forward:
            self._last_applied_tick_ts = ts
            self._last_applied_cvd_value = cvd_value
            self._last_applied_price_value = last_price
            self._cvd_tick_received.emit(cvd_value, last_price, ts)

    def _process_live_tick(self, cvd_value: float, last_price: float, ts: datetime, allow_repaint: bool):
        cvd_mode = self.setup_cvd_value_mode_combo.currentData() or self.CVD_VALUE_MODE_RAW
        transformed_cvd = float(cvd_value)
        if cvd_mode == self.CVD_VALUE_MODE_NORMALIZED:
            transformed_cvd = transformed_cvd / max(float(self._current_session_volume_scale), 1.0)

        raw_cvd = transformed_cvd
        current_price = float(last_price)

        self._live_tick_points.append((ts, raw_cvd))
        self._live_price_points.append((ts, current_price))

        # Trim stale points from previous sessions.
        today = ts.date()
        while self._live_tick_points and self._live_tick_points[0][0].date() < today:
            self._live_tick_points.popleft()
        while self._live_price_points and self._live_price_points[0][0].date() < today:
            self._live_price_points.popleft()

        if allow_repaint and self._chart_ready and not self._tick_repaint_timer.isActive():
            self._tick_repaint_timer.start(self.LIVE_TICK_REPAINT_MS)

        return current_price

    def _apply_cvd_tick(self, cvd_value: float, last_price: float, tick_ts: datetime):
        """Slot — always called on the GUI thread via queued signal connection."""
        ts = tick_ts if isinstance(tick_ts, datetime) else datetime.now()
        self._last_live_tick_ts = ts

        # Freeze live-dot motion outside market hours.
        # Some feeds keep pushing ticks after 15:30; if we keep updating the
        # timestamp, the blinking dot drifts right and creates empty chart space.
        if ts.time() < TRADING_START or ts.time() > TRADING_END:
            return

        if not self._chart_ready:
            self._pending_tick_buffer.append((ts, float(cvd_value), float(last_price)))
            return

        current_price = self._process_live_tick(cvd_value, last_price, ts, allow_repaint=True)

        # ── STACKER: check stack/unwind on every live underlying tick ──────────
        if (
            self._live_stacker_state is not None
            and self.live_mode
            and self._live_stacker_side is not None
        ):
            tick_ts = ts.isoformat()
            # x_arr_val is not critical for stack signals — use placeholder for tick-triggered checks
            self._check_and_emit_stack_signals(
                side=self._live_stacker_side,
                strategy_type=self._live_stacker_strategy_type or "atr_reversal",
                current_price=current_price,
                current_bar_idx=-1,
                closed_bar_ts=tick_ts,
                x_arr_val=0.0,
            )

    def _on_market_data_status_changed(self, status):
        status_text = str(status).strip() if status is not None else ""
        normalized = status_text.lower()
        self._ws_status_text = normalized

        if normalized.startswith("connected"):
            self.ws_status_label.setText(f"Live feed: {status_text}")
            self.ws_status_label.setStyleSheet(f"color: {C['profit']}; font-size: 11px; font-weight: 600;")
            if hasattr(self, "_status_bar"):
                self._status_bar.set_connected(True)
            return

        if normalized.startswith("connecting") or normalized.startswith("reconnecting"):
            self.ws_status_label.setText(f"Live feed: {status_text}")
            self.ws_status_label.setStyleSheet(f"color: {C['warn']}; font-size: 11px; font-weight: 600;")
            if hasattr(self, "_status_bar"):
                self._status_bar.set("conn", "CONNECTING", "warn")
            return

        self.ws_status_label.setText(f"Live feed issue: {status_text or 'disconnected'}")
        self.ws_status_label.setStyleSheet(f"color: {C['loss']}; font-size: 11px; font-weight: 700;")
        if hasattr(self, "_status_bar"):
            self._status_bar.set_connected(False)
        self._attempt_manual_ws_reconnect("status_change")

    def _attempt_manual_ws_reconnect(self, reason: str):
        parent = self.parent()
        if self._is_closing or getattr(parent, "_close_in_progress", False):
            return

        market_data_worker = getattr(parent, "market_data_worker", None)
        if market_data_worker is None or not hasattr(market_data_worker, "manual_reconnect"):
            return

        if getattr(market_data_worker, "is_intentional_stop", False):
            return

        now = datetime.now()
        if self._last_ws_reconnect_attempt_ts and (now - self._last_ws_reconnect_attempt_ts).total_seconds() < 15:
            return
        self._last_ws_reconnect_attempt_ts = now
        logger.warning("[AUTO] Manual websocket reconnect requested (%s)", reason)
        market_data_worker.manual_reconnect()

    # =========================================================================
    # SECTION 9: LIVE REFRESH TIMER & DOT BLINK
    # =========================================================================

    def _start_refresh_timer(self):
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_if_live)
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)

        self._ws_watchdog_timer = QTimer(self)
        self._ws_watchdog_timer.timeout.connect(self._check_live_tick_staleness)
        self._ws_watchdog_timer.start(5000)

    def _check_live_tick_staleness(self):
        if not self.live_mode:
            return

        now = datetime.now()
        if now.time() < TRADING_START or now.time() > TRADING_END:
            return

        if self._last_live_tick_ts is None:
            return

        stale_seconds = (now - self._last_live_tick_ts).total_seconds()
        if stale_seconds < 25:
            return

        if self._ws_status_text.startswith("connected"):
            self.ws_status_label.setText("Live feed stalled (>25s without ticks). Attempting reconnect…")
            self.ws_status_label.setStyleSheet(f"color: {C['loss']}; font-size: 11px; font-weight: 700;")
            self._attempt_manual_ws_reconnect("tick_stale")

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

        # Hybrid exit check on bar close before historical reload.
        if self._live_trade_info is not None and self._live_hybrid_engine is not None:
            try:
                price_arr = self.all_price_data
                high_arr = self.all_price_high_data
                low_arr = self.all_price_low_data
                if len(price_arr) >= 14 and len(high_arr) >= 14 and len(low_arr) >= 14:
                    closes = np.array(price_arr[-50:], dtype=float)
                    highs = np.array(high_arr[-50:], dtype=float)
                    lows = np.array(low_arr[-50:], dtype=float)
                    atr_arr = calculate_atr(highs, lows, closes, 14)
                    adx_arr = compute_adx(highs, lows, closes, 14)
                    ema51_arr = calculate_ema(closes, 51)
                    if len(atr_arr) and len(adx_arr) and len(ema51_arr):
                        self._check_live_hybrid_exit(
                            current_price=float(closes[-1]),
                            ema51=float(ema51_arr[-1]),
                            atr=float(atr_arr[-1]),
                            adx=float(adx_arr[-1]),
                        )
            except Exception as exc:
                logger.warning("[HYBRID EXIT] Bar-close check failed: %s", exc)

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
        self._is_closing = True
        self._persist_setup_values()
        try:
            if hasattr(self, "_cvd_tick_flush_timer"):
                self._cvd_tick_flush_timer.stop()
                self._pending_live_tick = None
            if hasattr(self, "_fetch_worker") and self._fetch_worker is not None:
                self._fetch_worker.cancel()
            if hasattr(self, "_fetch_thread") and self._fetch_thread.isRunning():
                self._fetch_thread.quit()
                self._fetch_thread.wait()
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
        On each closed bar or live tick:
          1. Check if any stacked positions should be UNWOUND (price crossed back
             through their entry) — emit unwind exit signals LIFO.
          2. Check if new stacks should be ADDED (price moved further in favour).
             Skipped entirely if any unwinds happened this evaluation cycle.

        The anchor position is untouched by unwind logic — it exits only on its
        own strategy exit signal.

        BUG FIX (2026-02-27): Added _did_unwind guard and StackerState.mark_unwind()
        to prevent the buy→unwind→buy oscillation loop at tick granularity.
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

        # ── 1. LIFO UNWIND: exit stacks whose entry price was breached ──────────
        to_unwind = state.stacks_to_unwind(current_price)
        unwound_ids: set[int] = set()
        _did_unwind = False  # ← mirrors simulator's per-bar guard

        if to_unwind:
            _did_unwind = True
            for entry in to_unwind:
                unwound_ids.add(id(entry))
                active_priority_list, strategy_priorities = self._active_strategy_priorities()
                unwind_ts = f"{closed_bar_ts}_unwind{entry.stack_number}"
                unwind_payload = {
                    "instrument_token": self.instrument_token,
                    "symbol": self.symbol,
                    "signal_side": side,
                    "signal_type": strategy_type,
                    "priority_list": active_priority_list,
                    "strategy_priorities": strategy_priorities,
                    "signal_x": x_arr_val,
                    "price_close": current_price,
                    "stoploss_points": float(self.automation_stoploss_input.value()),
                    "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
                    "order_type": self.automation_order_type_combo.currentData() or self.ORDER_TYPE_MARKET,
                    "timestamp": unwind_ts,
                    "is_stack_unwind": True,          # ← coordinator routes this as EXIT
                    "stack_number": entry.stack_number,
                    "anchor_price": state.anchor_entry_price,
                    "stack_entry_price": entry.entry_price,
                }
                import logging
                logger = logging.getLogger(__name__)
                logger.info(
                    "[STACKER] Unwind stack #%d: token=%s side=%s entry=%.2f current=%.2f",
                    entry.stack_number,
                    self.instrument_token,
                    side,
                    entry.entry_price,
                    current_price,
                )
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda p=unwind_payload: self.automation_signal.emit(p))

            state.remove_stacks(to_unwind)

            # ── KEY FIX: raise the re-stack floor after every unwind ────────────
            # Price must travel one full extra step beyond the current trigger
            # before should_add_stack() returns True again. This prevents the
            # tick loop from immediately re-firing a stack at the same boundary.
            state.mark_unwind()

        # ── 2. FIFO PROFIT HARVEST: lock profit by exiting oldest stack ─────────
        if state.profit_harvest_enabled and state.stack_entries:
            total_pnl = self._get_total_live_pnl()

            while state.should_harvest_profit(total_pnl) and state.stack_entries:
                candidate = state.stack_entries[0]
                if id(candidate) in unwound_ids:
                    import logging
                    logging.getLogger(__name__).warning(
                        "[HARVEST] Skipping STACK_%d — already queued for LIFO unwind this bar",
                        candidate.stack_number,
                    )
                    break

                oldest = state.harvest_oldest_stack()
                if oldest is None:
                    break

                active_priority_list, strategy_priorities = self._active_strategy_priorities()
                harvest_ts = f"{closed_bar_ts}_harvest{oldest.stack_number}"
                harvest_payload = {
                    "instrument_token": self.instrument_token,
                    "symbol": self.symbol,
                    "signal_side": side,
                    "signal_type": strategy_type,
                    "priority_list": active_priority_list,
                    "strategy_priorities": strategy_priorities,
                    "signal_x": x_arr_val,
                    "price_close": current_price,
                    "stoploss_points": float(self.automation_stoploss_input.value()),
                    "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
                    "order_type": self.automation_order_type_combo.currentData() or self.ORDER_TYPE_MARKET,
                    "timestamp": harvest_ts,
                    "is_stack_unwind": True,
                    "is_profit_harvest": True,
                    "stack_number": oldest.stack_number,
                    "anchor_price": state.anchor_entry_price,
                    "stack_entry_price": oldest.entry_price,
                }

                import logging
                logging.getLogger(__name__).info(
                    "[HARVEST] Locking profit — exiting STACK_%d entry=%.2f pnl=₹%.0f floor=₹%.0f",
                    oldest.stack_number,
                    oldest.entry_price,
                    total_pnl,
                    state._harvest_floor,
                )
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda p=harvest_payload: self.automation_signal.emit(p))
                total_pnl = self._get_total_live_pnl()

        # ── 3. STACK ADD: add new positions if price moved further in favour ─────
        # KEY FIX: skip entirely if any unwinds happened this evaluation cycle.
        # This mirrors the simulator's `if not _did_unwind:` guard exactly.
        # Additionally, StackerState.should_add_stack() now checks is_in_unwind_cooldown()
        # so even across different tick calls, re-stacking is blocked until price proves
        # it has moved a full step beyond the unwind level.
        if _did_unwind:
            return  # ← do NOT re-stack on the same tick/bar that had an unwind

        while state.should_add_stack(current_price):
            state.add_stack(entry_price=current_price, bar_idx=current_bar_idx)
            stack_num = len(state.stack_entries)

            stack_ts = f"{closed_bar_ts}_stack{stack_num}"

            active_priority_list, strategy_priorities = self._active_strategy_priorities()
            payload = {
                "instrument_token": self.instrument_token,
                "symbol": self.symbol,
                "signal_side": side,
                "signal_type": strategy_type,
                "priority_list": active_priority_list,
                "strategy_priorities": strategy_priorities,
                "signal_x": x_arr_val,
                "price_close": current_price,
                "stoploss_points": float(self.automation_stoploss_input.value()),
                "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
                "order_type": self.automation_order_type_combo.currentData() or self.ORDER_TYPE_MARKET,
                "timestamp": stack_ts,
                "is_stack": True,
                "stack_number": stack_num,
                "anchor_price": state.anchor_entry_price,
            }

            import logging
            logging.getLogger(__name__).info(
                "[STACKER] Stack #%d fired: token=%s side=%s price=%.2f (anchor=%.2f, step=%.0f)",
                stack_num,
                self.instrument_token,
                side,
                current_price,
                state.anchor_entry_price,
                state.step_points,
            )

            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda p=payload: self.automation_signal.emit(p))

            if not state.can_stack_more:
                break

    def _get_total_live_pnl(self) -> float:
        """
        Read live total PnL directly from position_manager.
        Same number shown in the positions table footer — no manual calculation.
        """
        try:
            positions = self.position_manager.get_all_positions()
            return sum(pos.pnl for pos in positions)
        except Exception:
            return 0.0
