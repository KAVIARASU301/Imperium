"""
_setup_ui_replacement.py
─────────────────────────────────────────────────────────────────────────────
Drop-in replacement for the _setup_ui method in AutoTraderDialog.

USAGE:
  1. Copy the _setup_ui method below into auto_trader_dialog.py,
     replacing the existing one.
  2. Add ControlPanelMixin to the class MRO (see integration_guide.py).
  3. Import auto_trader_theme at top of auto_trader_dialog.py.

This version:
  • Uses the new three-band control panel from ControlPanelMixin
  • Surfaces ATR/CVD/Risk/Governance params directly in Band B
  • Moves Automate toggle to a prominent lit button in Band C
  • Cleans up the chart area (no more scattered label noise)
  • Applies the institutional dark terminal theme
─────────────────────────────────────────────────────────────────────────────
"""

from core.auto_trader.auto_trader_theme import DIMS, THEME, Styles
from core.auto_trader.auto_trader_control_panel import ControlPanelMixin


def _setup_ui(self):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel,
        QPushButton, QSpinBox, QVBoxLayout, QWidget,
    )
    import pyqtgraph as pg
    from pyqtgraph import AxisItem, TextItem
    from core.cvd.cvd_mode import CVDMode
    from core.auto_trader.constants import MINUTES_PER_SESSION
    from core.auto_trader.date_navigator import DateNavigator
    from core.auto_trader.regime_indicator import RegimeIndicator

    root = QVBoxLayout(self)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    # ── Apply base window theme ───────────────────────────────────────────
    self.setStyleSheet(f"""
        QDialog#autoTraderWindow {{
            background: {THEME['bg_base']};
            color: {THEME['text_primary']};
        }}
        QLabel {{
            color: {THEME['text_secondary']};
        }}
        QScrollBar:vertical {{
            background: {THEME['bg_panel']};
            width: 8px;
        }}
        QScrollBar::handle:vertical {{
            background: {THEME['border']};
            border-radius: 4px;
        }}
    """)

    # ─────────────────────────────────────────────────────────────────────
    # Helper: fit combo to widest item
    # ─────────────────────────────────────────────────────────────────────
    def fit_combo_to_widest_item(combo: QComboBox, extra_px: int = 34):
        metrics = QFontMetrics(combo.font())
        widest = max(
            (metrics.horizontalAdvance(combo.itemText(idx)) for idx in range(combo.count())),
            default=0,
        )
        combo.setFixedWidth(max(84, widest + extra_px))

    # ─────────────────────────────────────────────────────────────────────
    # NAVIGATOR ROW  (centered, above the panel bands)
    # ─────────────────────────────────────────────────────────────────────
    navigator_row = QHBoxLayout()
    navigator_row.setContentsMargins(8, 4, 8, 2)
    navigator_row.setSpacing(8)
    self.navigator = DateNavigator(self)
    navigator_row.addStretch()
    navigator_row.addWidget(self.navigator)
    navigator_row.addStretch()
    if self.cvd_engine:
        self.cvd_engine.set_mode(CVDMode.SINGLE_DAY)
    root.addLayout(navigator_row)

    # ─────────────────────────────────────────────────────────────────────
    # TIMEFRAME COMBO  (created before control panel)
    # ─────────────────────────────────────────────────────────────────────
    self.timeframe_combo = QComboBox()
    self.timeframe_combo.setFixedHeight(DIMS["btn_h"])
    self.timeframe_combo.setFixedWidth(70)
    self.timeframe_combo.setStyleSheet(Styles.COMBO)
    self._timeframe_options = [("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("1h", 60)]
    for label, minutes in self._timeframe_options:
        self.timeframe_combo.addItem(label, minutes)
    self.timeframe_combo.setCurrentIndex(0)
    self.timeframe_combo.currentIndexChanged.connect(self._on_timeframe_combo_changed)

    # ── Focus / Day View toggle ──
    self.btn_focus = QPushButton("1D")
    self.btn_focus.setCheckable(True)
    self.btn_focus.setChecked(False)
    self.btn_focus.setFixedHeight(DIMS["btn_h"])
    self.btn_focus.setFixedWidth(40)
    self.btn_focus.setStyleSheet(Styles.BTN_TOGGLE)
    self.btn_focus.setToolTip("Toggle 2-day view")
    self.btn_focus.toggled.connect(self._on_focus_mode_changed)
    self.btn_focus.toggled.connect(lambda c: self.btn_focus.setText("2D" if c else "1D"))

    # ─────────────────────────────────────────────────────────────────────
    # EMA CHECKBOXES
    # ─────────────────────────────────────────────────────────────────────
    self.ema_checkboxes = {}
    ema_configs = [(10, THEME["ema_10"], "10"), (21, THEME["ema_21"], "21"), (51, THEME["ema_51"], "51")]
    for period, color, label in ema_configs:
        cb = QCheckBox(label)
        cb.setChecked(period == 51)
        cb.toggled.connect(lambda checked, p=period: self._on_ema_toggled(p, checked))
        self.ema_checkboxes[period] = cb

    self.vwap_checkbox = QCheckBox("VWAP")
    self.vwap_checkbox.setChecked(False)
    self.vwap_checkbox.toggled.connect(self._on_vwap_toggled)

    # ─────────────────────────────────────────────────────────────────────
    # SIGNAL FILTER COMBO (checkable multi-select)
    # ─────────────────────────────────────────────────────────────────────
    self.signal_filter_combo = QComboBox()
    self.signal_filter_combo.setFixedHeight(DIMS["btn_h"])
    self.signal_filter_combo.setFixedWidth(160)
    self.signal_filter_combo.setStyleSheet(Styles.COMBO)
    self._init_signal_filter_combo(self.signal_filter_combo)
    fit_combo_to_widest_item(self.signal_filter_combo)

    self.atr_marker_filter_combo = QComboBox()
    self.atr_marker_filter_combo.setFixedHeight(DIMS["btn_h"])
    self.atr_marker_filter_combo.setStyleSheet(Styles.COMBO)
    self.atr_marker_filter_combo.addItem("Show All",        self.ATR_MARKER_SHOW_ALL)
    self.atr_marker_filter_combo.addItem("Confluence Only", self.ATR_MARKER_CONFLUENCE_ONLY)
    self.atr_marker_filter_combo.addItem("Green Only",      self.ATR_MARKER_GREEN_ONLY)
    self.atr_marker_filter_combo.addItem("Red Only",        self.ATR_MARKER_RED_ONLY)
    self.atr_marker_filter_combo.addItem("Hide All",        self.ATR_MARKER_HIDE_ALL)
    self.atr_marker_filter_combo.setCurrentIndex(1)
    self.atr_marker_filter_combo.currentIndexChanged.connect(self._on_atr_marker_filter_changed)
    fit_combo_to_widest_item(self.atr_marker_filter_combo)

    # ─────────────────────────────────────────────────────────────────────
    # AUTOMATION CONTROLS
    # ─────────────────────────────────────────────────────────────────────
    # Hidden signal hub — visible automate button lives in Band C
    self.automate_toggle = QCheckBox()
    self.automate_toggle.setChecked(False)
    self.automate_toggle.hide()
    self.automate_toggle.toggled.connect(self._on_automation_settings_changed)

    _s = Styles.SPINBOX   # shorthand

    self.automation_stoploss_input = QSpinBox()
    self.automation_stoploss_input.setRange(1, 1000)
    self.automation_stoploss_input.setValue(50)
    self.automation_stoploss_input.setSingleStep(5)
    self.automation_stoploss_input.setStyleSheet(_s)
    self.automation_stoploss_input.valueChanged.connect(self._on_automation_settings_changed)

    self.max_profit_giveback_input = QSpinBox()
    self.max_profit_giveback_input.setRange(0, 5000)
    self.max_profit_giveback_input.setValue(75)
    self.max_profit_giveback_input.setSingleStep(5)
    self.max_profit_giveback_input.setSpecialValueText("Off")
    self.max_profit_giveback_input.setStyleSheet(_s)
    self.max_profit_giveback_input.setToolTip("Exit when profit pulls back from peak by N points. 0=Off")
    self.max_profit_giveback_input.valueChanged.connect(self._on_automation_settings_changed)

    # Max giveback checkboxes
    _cb_style = Styles.CHECKBOX
    self.max_giveback_atr_reversal_check  = QCheckBox("ATR Rev");   self.max_giveback_atr_reversal_check.setStyleSheet(_cb_style)
    self.max_giveback_ema_cross_check     = QCheckBox("EMA X");     self.max_giveback_ema_cross_check.setStyleSheet(_cb_style)
    self.max_giveback_atr_divergence_check= QCheckBox("ATR Div");   self.max_giveback_atr_divergence_check.setStyleSheet(_cb_style)
    self.max_giveback_range_breakout_check= QCheckBox("Breakout");  self.max_giveback_range_breakout_check.setStyleSheet(_cb_style)
    for cb in (self.max_giveback_atr_reversal_check, self.max_giveback_ema_cross_check,
               self.max_giveback_atr_divergence_check, self.max_giveback_range_breakout_check):
        cb.setChecked(False)
        cb.toggled.connect(self._on_automation_settings_changed)

    # Dynamic exit checkboxes
    self.dynamic_exit_atr_reversal_check      = QCheckBox("ATR Reversal");     self.dynamic_exit_atr_reversal_check.setChecked(False)
    self.dynamic_exit_ema_cross_check         = QCheckBox("EMA Cross");        self.dynamic_exit_ema_cross_check.setChecked(True)
    self.dynamic_exit_atr_divergence_check    = QCheckBox("ATR Divergence");   self.dynamic_exit_atr_divergence_check.setChecked(False)
    self.dynamic_exit_range_breakout_check    = QCheckBox("Range Breakout");   self.dynamic_exit_range_breakout_check.setChecked(True)
    self.dynamic_exit_cvd_range_breakout_check= QCheckBox("CVD Breakout");     self.dynamic_exit_cvd_range_breakout_check.setChecked(True)
    self.dynamic_exit_open_drive_check        = QCheckBox("Open Drive");       self.dynamic_exit_open_drive_check.setChecked(False)
    for cb in (self.dynamic_exit_atr_reversal_check, self.dynamic_exit_ema_cross_check,
               self.dynamic_exit_atr_divergence_check, self.dynamic_exit_range_breakout_check,
               self.dynamic_exit_cvd_range_breakout_check, self.dynamic_exit_open_drive_check):
        cb.setStyleSheet(_cb_style)
        cb.toggled.connect(self._on_automation_settings_changed)

    # Trend exit threshold inputs
    self.trend_exit_adx_min_input = QDoubleSpinBox()
    self.trend_exit_adx_min_input.setRange(15.0, 45.0); self.trend_exit_adx_min_input.setDecimals(1)
    self.trend_exit_adx_min_input.setSingleStep(0.5);   self.trend_exit_adx_min_input.setValue(28.0)
    self.trend_exit_adx_min_input.setStyleSheet(_s)
    self.trend_exit_adx_min_input.setToolTip("Minimum ADX to activate trend-ride mode")
    self.trend_exit_adx_min_input.valueChanged.connect(self._on_automation_settings_changed)

    self.trend_exit_atr_ratio_min_input = QDoubleSpinBox()
    self.trend_exit_atr_ratio_min_input.setRange(0.80, 2.50); self.trend_exit_atr_ratio_min_input.setDecimals(2)
    self.trend_exit_atr_ratio_min_input.setSingleStep(0.05);  self.trend_exit_atr_ratio_min_input.setValue(1.15)
    self.trend_exit_atr_ratio_min_input.setStyleSheet(_s)
    self.trend_exit_atr_ratio_min_input.setToolTip("Minimum normalized ATR to unlock trend mode")
    self.trend_exit_atr_ratio_min_input.valueChanged.connect(self._on_automation_settings_changed)

    self.trend_exit_confirm_bars_input = QSpinBox()
    self.trend_exit_confirm_bars_input.setRange(1, 8); self.trend_exit_confirm_bars_input.setValue(3)
    self.trend_exit_confirm_bars_input.setStyleSheet(_s)
    self.trend_exit_confirm_bars_input.setToolTip("Consecutive qualifying bars before trend-ride mode")
    self.trend_exit_confirm_bars_input.valueChanged.connect(self._on_automation_settings_changed)

    # Route / Order combos
    self.automation_route_combo = QComboBox()
    self.automation_route_combo.setStyleSheet(Styles.COMBO)
    self.automation_route_combo.addItem("Buy Exit Panel", self.ROUTE_BUY_EXIT_PANEL)
    self.automation_route_combo.addItem("Direct",         self.ROUTE_DIRECT)
    self.automation_route_combo.setCurrentIndex(0)
    self.automation_route_combo.currentIndexChanged.connect(self._on_automation_settings_changed)

    self.automation_order_type_combo = QComboBox()
    self.automation_order_type_combo.setStyleSheet(Styles.COMBO)
    self.automation_order_type_combo.addItem("Market", self.ORDER_TYPE_MARKET)
    self.automation_order_type_combo.addItem("Limit",  self.ORDER_TYPE_LIMIT)
    self.automation_order_type_combo.setCurrentIndex(0)
    self.automation_order_type_combo.currentIndexChanged.connect(self._on_automation_settings_changed)

    # Time window spinboxes
    for attr, default in [
        ("automation_start_time_hour_input",   9),
        ("automation_start_time_minute_input", 15),
        ("automation_cutoff_time_hour_input",  15),
        ("automation_cutoff_time_minute_input",15),
    ]:
        sp = QSpinBox()
        sp.setRange(0, 59 if "minute" in attr else 23)
        sp.setValue(default)
        sp.setStyleSheet(_s)
        sp.valueChanged.connect(self._on_automation_settings_changed)
        setattr(self, attr, sp)

    # ─────────────────────────────────────────────────────────────────────
    # ATR / SIGNAL PARAMS
    # ─────────────────────────────────────────────────────────────────────
    self.atr_base_ema_input = QSpinBox()
    self.atr_base_ema_input.setRange(1, 500); self.atr_base_ema_input.setValue(51)
    self.atr_base_ema_input.setStyleSheet(_s); self.atr_base_ema_input.valueChanged.connect(self._on_atr_settings_changed)

    self.atr_distance_input = QDoubleSpinBox()
    self.atr_distance_input.setRange(0.1, 20.0); self.atr_distance_input.setDecimals(2)
    self.atr_distance_input.setSingleStep(0.1);   self.atr_distance_input.setValue(3.01)
    self.atr_distance_input.setStyleSheet(_s);    self.atr_distance_input.valueChanged.connect(self._on_atr_settings_changed)

    self.cvd_ema_gap_input = QSpinBox()
    self.cvd_ema_gap_input.setRange(0, 500000); self.cvd_ema_gap_input.setSingleStep(1000)
    self.cvd_ema_gap_input.setValue(3000); self.cvd_ema_gap_input.setStyleSheet(_s)
    self.cvd_ema_gap_input.setToolTip("Min CVD–EMA distance to confirm signal validity")
    self.cvd_ema_gap_input.valueChanged.connect(self._on_atr_settings_changed)

    self.cvd_atr_distance_input = QDoubleSpinBox()
    self.cvd_atr_distance_input.setRange(0.5, 6.0); self.cvd_atr_distance_input.setDecimals(2)
    self.cvd_atr_distance_input.setSingleStep(0.1);  self.cvd_atr_distance_input.setValue(2.0)
    self.cvd_atr_distance_input.setStyleSheet(_s)
    self.cvd_atr_distance_input.setToolTip("CVD z-score min (2.0 = institutional default)")
    self.cvd_atr_distance_input.valueChanged.connect(self._on_atr_settings_changed)

    self.atr_extension_threshold_input = QDoubleSpinBox()
    self.atr_extension_threshold_input.setRange(0.5, 3.0); self.atr_extension_threshold_input.setDecimals(2)
    self.atr_extension_threshold_input.setSingleStep(0.05); self.atr_extension_threshold_input.setValue(1.10)
    self.atr_extension_threshold_input.setStyleSheet(_s)
    self.atr_extension_threshold_input.setToolTip("Min normalized ATR for ATR reversal gating")
    self.atr_extension_threshold_input.valueChanged.connect(self._on_atr_settings_changed)

    self.atr_flat_velocity_pct_input = QDoubleSpinBox()
    self.atr_flat_velocity_pct_input.setRange(0.0, 0.2); self.atr_flat_velocity_pct_input.setDecimals(3)
    self.atr_flat_velocity_pct_input.setSingleStep(0.005); self.atr_flat_velocity_pct_input.setValue(0.020)
    self.atr_flat_velocity_pct_input.setStyleSheet(_s)
    self.atr_flat_velocity_pct_input.setToolTip("Max ATR velocity % treated as flat/contracting")
    self.atr_flat_velocity_pct_input.valueChanged.connect(self._on_atr_settings_changed)

    # ATR trailing step (Band B)
    self.atr_trailing_step_input = QDoubleSpinBox()
    self.atr_trailing_step_input.setRange(1.0, 200.0); self.atr_trailing_step_input.setDecimals(1)
    self.atr_trailing_step_input.setSingleStep(1.0);   self.atr_trailing_step_input.setValue(10.0)
    self.atr_trailing_step_input.setStyleSheet(_s)
    self.atr_trailing_step_input.setToolTip("Trailing stop step in points")
    self.atr_trailing_step_input.valueChanged.connect(self._on_automation_settings_changed)

    # ─────────────────────────────────────────────────────────────────────
    # STACKER / HARVEST
    # ─────────────────────────────────────────────────────────────────────
    self.stacker_enabled_check = QCheckBox("Stacker")
    self.stacker_enabled_check.setChecked(False)
    self.stacker_enabled_check.setToolTip("Pyramid scaling: add a new position every N pts of favorable move")
    self.stacker_enabled_check.toggled.connect(self._on_stacker_settings_changed)

    self.stacker_step_input = QSpinBox()
    self.stacker_step_input.setRange(5, 500); self.stacker_step_input.setValue(20)
    self.stacker_step_input.setSingleStep(5);  self.stacker_step_input.setSuffix(" pts")
    self.stacker_step_input.setStyleSheet(_s)
    self.stacker_step_input.setToolTip("Add a new position every N favorable points from anchor entry")
    self.stacker_step_input.valueChanged.connect(self._on_stacker_settings_changed)

    self.stacker_max_input = QSpinBox()
    self.stacker_max_input.setRange(1, 100); self.stacker_max_input.setValue(10)
    self.stacker_max_input.setSpecialValueText("1×"); self.stacker_max_input.setStyleSheet(_s)
    self.stacker_max_input.setToolTip("Max stack entries on top of anchor")
    self.stacker_max_input.valueChanged.connect(self._on_stacker_settings_changed)

    self.harvest_enabled_check = QCheckBox("Harvest")
    self.harvest_enabled_check.setChecked(False)
    self.harvest_enabled_check.setToolTip("FIFO Profit Harvest: exit oldest stack when PnL crosses threshold")
    self.harvest_enabled_check.toggled.connect(self._on_stacker_settings_changed)

    self.harvest_threshold_input = QDoubleSpinBox()
    self.harvest_threshold_input.setPrefix("₹"); self.harvest_threshold_input.setRange(500, 500000)
    self.harvest_threshold_input.setValue(10000); self.harvest_threshold_input.setSingleStep(1000)
    self.harvest_threshold_input.setDecimals(0);  self.harvest_threshold_input.setStyleSheet(_s)
    self.harvest_threshold_input.setToolTip("Lock profit every time total PnL gains this much")
    self.harvest_threshold_input.valueChanged.connect(self._on_stacker_settings_changed)

    # ─────────────────────────────────────────────────────────────────────
    # GOVERNANCE  (deploy_mode / min_confidence / canary_ratio in Band B)
    # ─────────────────────────────────────────────────────────────────────
    self.deploy_mode_combo = QComboBox()
    self.deploy_mode_combo.setStyleSheet(Styles.COMBO)
    self.deploy_mode_combo.addItem("Shadow", "shadow")
    self.deploy_mode_combo.addItem("Canary", "canary")
    self.deploy_mode_combo.addItem("Live",   "live")
    self.deploy_mode_combo.setCurrentIndex(1)
    self.deploy_mode_combo.currentIndexChanged.connect(self._on_governance_settings_changed)

    self.min_confidence_input = QDoubleSpinBox()
    self.min_confidence_input.setRange(0.1, 1.0); self.min_confidence_input.setDecimals(2)
    self.min_confidence_input.setSingleStep(0.05); self.min_confidence_input.setValue(0.55)
    self.min_confidence_input.setStyleSheet(_s)
    self.min_confidence_input.setToolTip("Min confidence score to allow live execution")
    self.min_confidence_input.valueChanged.connect(self._on_governance_settings_changed)

    self.canary_ratio_input = QDoubleSpinBox()
    self.canary_ratio_input.setRange(0.0, 1.0); self.canary_ratio_input.setDecimals(2)
    self.canary_ratio_input.setSingleStep(0.05); self.canary_ratio_input.setValue(0.25)
    self.canary_ratio_input.setStyleSheet(_s)
    self.canary_ratio_input.setToolTip("Fraction of canary signals allowed for live execution")
    self.canary_ratio_input.valueChanged.connect(self._on_governance_settings_changed)

    # ─────────────────────────────────────────────────────────────────────
    # ACTION BUTTONS
    # ─────────────────────────────────────────────────────────────────────
    self.setup_btn = QPushButton("⚙ Setup")
    self.setup_btn.setFixedHeight(DIMS["btn_h"])
    self.setup_btn.setToolTip("Open automation and signal settings")
    self.setup_btn.clicked.connect(self._open_setup_dialog)

    self.simulator_run_btn = QPushButton("▶ Simulator")
    self.simulator_run_btn.setFixedHeight(DIMS["btn_h"])
    self.simulator_run_btn.setToolTip("Run simulator (Space)")
    self.simulator_run_btn.clicked.connect(self._on_simulator_run_clicked)

    self.tick_upload_btn = QPushButton("⬆ CSV")
    self.tick_upload_btn.setFixedHeight(DIMS["btn_h"])
    self.tick_upload_btn.setToolTip("Upload timestamp,ltp,volume tick CSV for back-analysis")
    self.tick_upload_btn.clicked.connect(self._on_upload_tick_csv)

    self.tick_clear_btn = QPushButton("Live Tick")
    self.tick_clear_btn.setFixedHeight(DIMS["btn_h"])
    self.tick_clear_btn.setToolTip("Clear uploaded tick data, revert to live/historical feed")
    self.tick_clear_btn.clicked.connect(self._clear_uploaded_tick_data)
    self.tick_clear_btn.setEnabled(False)

    self.btn_refresh_plot = QPushButton("⟳")
    self.btn_refresh_plot.setFixedSize(DIMS["btn_h"], DIMS["btn_h"])
    self.btn_refresh_plot.setToolTip("Refresh chart plot")
    self.btn_refresh_plot.clicked.connect(self._refresh_plot_only)

    self.btn_export = QPushButton("📸")
    self.btn_export.setFixedSize(DIMS["btn_h"], DIMS["btn_h"])
    self.btn_export.setToolTip("Export chart as PNG")
    self.btn_export.clicked.connect(self._export_chart_image)

    # ─────────────────────────────────────────────────────────────────────
    # SKIP / LIMIT
    # ─────────────────────────────────────────────────────────────────────
    self.atr_skip_limit_input = QSpinBox()
    self.atr_skip_limit_input.setRange(0, 20); self.atr_skip_limit_input.setValue(0)
    self.atr_skip_limit_input.setStyleSheet(_s)
    self.atr_skip_limit_input.valueChanged.connect(self._on_automation_settings_changed)

    # ─────────────────────────────────────────────────────────────────────
    # REGIME INDICATOR (live pills)
    # ─────────────────────────────────────────────────────────────────────
    self.regime_indicator = RegimeIndicator()

    # ─────────────────────────────────────────────────────────────────────
    # SIMULATOR SUMMARY LABEL
    # ─────────────────────────────────────────────────────────────────────
    self.simulator_summary_label = QLabel("Sim: —")
    self.simulator_summary_label.setStyleSheet(
        f"color: {THEME['text_secondary']}; font-size: 10px; font-weight: 600;"
    )

    # ─────────────────────────────────────────────────────────────────────
    # BUILD SETUP DIALOG (must precede control panel — needs those widgets)
    # ─────────────────────────────────────────────────────────────────────
    self._build_setup_dialog(Styles.COMBO, Styles.SPINBOX)

    # ─────────────────────────────────────────────────────────────────────
    # BUILD THREE-BAND CONTROL PANEL
    # ─────────────────────────────────────────────────────────────────────
    self._build_control_panel(root)

    # ─────────────────────────────────────────────────────────────────────
    # PRICE CHART
    # ─────────────────────────────────────────────────────────────────────
    self.price_axis = AxisItem(orientation="bottom")
    self.price_axis.setStyle(showValues=False)
    self.price_plot = pg.PlotWidget(axisItems={"bottom": self.price_axis})
    self.price_plot.setBackground(THEME["bg_surface"])
    self.price_plot.showGrid(x=True, y=True, alpha=0.08)
    self.price_plot.setMenuEnabled(False)
    self.price_plot.setMinimumHeight(200)

    price_y_axis = self.price_plot.getAxis("left")
    price_y_axis.setWidth(72)
    price_y_axis.setTextPen(pg.mkPen(THEME["accent_gold"]))
    price_y_axis.setPen(pg.mkPen(THEME["border"]))
    price_y_axis.enableAutoSIPrefix(False)

    self.price_prev_curve = pg.PlotCurveItem(pen=pg.mkPen("#5A5A5A", width=1.8, style=Qt.DashLine))
    self.price_today_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["accent_gold"], width=2.2))
    self.price_plot.addItem(self.price_prev_curve)
    self.price_plot.addItem(self.price_today_curve)

    self.price_today_tick_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["accent_gold"], width=1.4))
    self.price_plot.addItem(self.price_today_tick_curve)

    self.price_live_dot = pg.ScatterPlotItem(size=5, brush=pg.mkBrush(255, 229, 127, 200),
                                              pen=pg.mkPen("#FFFFFF", width=1))
    self.price_plot.addItem(self.price_live_dot)

    # ATR markers — price chart
    self.price_atr_above_markers = pg.ScatterPlotItem(size=9, symbol="t",
        brush=pg.mkBrush(THEME["sig_short"]), pen=pg.mkPen("#FFFFFF", width=0.8))
    self.price_atr_below_markers = pg.ScatterPlotItem(size=9, symbol="t1",
        brush=pg.mkBrush(THEME["sig_long"]),  pen=pg.mkPen("#FFFFFF", width=0.8))
    self.price_plot.addItem(self.price_atr_above_markers)
    self.price_plot.addItem(self.price_atr_below_markers)

    # Simulator markers
    self.sim_taken_long_markers  = pg.ScatterPlotItem(size=12, symbol="star", brush=pg.mkBrush(THEME["sig_long"]),  pen=pg.mkPen("#003820", width=1.0))
    self.sim_taken_short_markers = pg.ScatterPlotItem(size=12, symbol="star", brush=pg.mkBrush(THEME["sig_short"]), pen=pg.mkPen("#4A0E0E", width=1.0))
    self.sim_exit_win_markers    = pg.ScatterPlotItem(size=10, symbol="o",    brush=pg.mkBrush(THEME["accent_gold"]), pen=pg.mkPen("#FFFFFF", width=0.9))
    self.sim_exit_loss_markers   = pg.ScatterPlotItem(size=10, symbol="o",    brush=pg.mkBrush(THEME["sig_short"]),   pen=pg.mkPen("#FFFFFF", width=0.9))
    self.sim_skipped_markers     = pg.ScatterPlotItem(size=10, symbol="x",    brush=pg.mkBrush("#B0BEC5"),             pen=pg.mkPen("#ECEFF1", width=1.1))
    self.sim_trade_path_lines    = pg.PlotCurveItem(pen=pg.mkPen("#505870", width=1.2, style=Qt.DashLine), connect="pairs")
    self.sim_trade_path_lines.setZValue(18)
    self.price_plot.addItem(self.sim_trade_path_lines)
    for m in (self.sim_taken_long_markers, self.sim_taken_short_markers,
              self.sim_exit_win_markers, self.sim_exit_loss_markers, self.sim_skipped_markers):
        m.setZValue(20)
        self.price_plot.addItem(m)

    # Price EMAs
    self.price_ema10_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["ema_10"], width=1.8))
    self.price_ema21_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["ema_21"], width=1.8))
    self.price_ema51_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["ema_51"], width=1.8))
    self.price_vwap_curve  = pg.PlotCurveItem(pen=pg.mkPen(THEME["vwap"],   width=1.8))
    for c in (self.price_ema10_curve, self.price_ema21_curve, self.price_ema51_curve, self.price_vwap_curve):
        c.setOpacity(0.82)
        self.price_plot.addItem(c)

    pen_cross = pg.mkPen((255, 255, 255, 80), width=1, style=Qt.DashLine)
    self.price_crosshair = pg.InfiniteLine(angle=90, movable=False, pen=pen_cross)
    self.price_crosshair.hide()
    self.price_plot.addItem(self.price_crosshair)
    self.price_legend = None
    root.addWidget(self.price_plot, 1)

    # ─────────────────────────────────────────────────────────────────────
    # CVD CHART
    # ─────────────────────────────────────────────────────────────────────
    self.axis = AxisItem(orientation="bottom")
    self.plot = pg.PlotWidget(axisItems={"bottom": self.axis})

    bottom_axis = self.plot.getAxis("bottom")
    bottom_axis.setHeight(30)
    bottom_axis.setStyle(showValues=True)
    bottom_axis.setTextPen(pg.mkPen(THEME["text_secondary"]))
    bottom_axis.setPen(pg.mkPen(THEME["border"]))

    cvd_y_axis = self.plot.getAxis("left")
    cvd_y_axis.setWidth(72)
    cvd_y_axis.enableAutoSIPrefix(False)

    def cvd_axis_formatter(values, scale, spacing):
        labels = []
        for v in values:
            if abs(v) >= 1_000_000:   labels.append(f"{v/1_000_000:.1f}M")
            elif abs(v) >= 1_000:     labels.append(f"{v/1_000:.0f}K")
            else:                     labels.append(f"{int(v)}")
        return labels
    cvd_y_axis.tickStrings = cvd_axis_formatter

    self.plot.setBackground(THEME["bg_surface"])
    self.plot.showGrid(x=True, y=True, alpha=0.08)
    self.plot.setMenuEnabled(False)
    self.plot.setMinimumHeight(200)
    root.addWidget(self.plot, 1)

    # ── Bottom status row ──────────────────────────────────────────────
    from PySide6.QtWidgets import QHBoxLayout
    bottom_status_row = QHBoxLayout()
    bottom_status_row.setContentsMargins(8, 2, 8, 2)
    bottom_status_row.setSpacing(16)

    self.cpr_status_label = QLabel("CPR: --")
    self.cpr_status_label.setStyleSheet(
        f"color: {THEME['text_secondary']}; font-size: 11px; font-weight: 700;"
    )
    self.priority_order_label = QLabel("Priority order: --")
    self.priority_order_label.setStyleSheet(
        f"color: {THEME['text_muted']}; font-size: 10px; font-weight: 600;"
    )
    self.ws_status_label = QLabel("Live feed: connecting…")
    self.ws_status_label.setStyleSheet(
        f"color: {THEME['status_warn']}; font-size: 10px; font-weight: 600;"
    )
    self.ws_status_label.hide()

    bottom_status_row.addWidget(self.cpr_status_label)
    bottom_status_row.addWidget(self.priority_order_label)
    bottom_status_row.addStretch()
    bottom_status_row.addWidget(self.ws_status_label)
    root.addLayout(bottom_status_row)

    # ── CVD curves ────────────────────────────────────────────────────
    zero_pen = pg.mkPen(THEME["border"], style=Qt.DashLine, width=1)
    self.plot.addItem(pg.InfiniteLine(0, angle=0, pen=zero_pen))

    self.prev_curve = pg.PlotCurveItem(pen=pg.mkPen("#4A5570", width=1.8, style=Qt.DashLine))
    self.today_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["accent_teal"], width=2.2))
    self.plot.addItem(self.prev_curve)
    self.plot.addItem(self.today_curve)

    self.today_tick_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["accent_teal"], width=1.4))
    self.plot.addItem(self.today_tick_curve)

    self.live_dot = pg.ScatterPlotItem(size=5,
        brush=pg.mkBrush(38, 198, 218, 200), pen=pg.mkPen("#FFFFFF", width=1))
    self.plot.addItem(self.live_dot)

    self.cvd_atr_above_markers = pg.ScatterPlotItem(size=9, symbol="t",
        brush=pg.mkBrush(THEME["sig_short"]), pen=pg.mkPen("#FFFFFF", width=0.8))
    self.cvd_atr_below_markers = pg.ScatterPlotItem(size=9, symbol="t1",
        brush=pg.mkBrush(THEME["sig_long"]),  pen=pg.mkPen("#FFFFFF", width=0.8))
    self.plot.addItem(self.cvd_atr_above_markers)
    self.plot.addItem(self.cvd_atr_below_markers)

    self.cvd_ema10_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["ema_10"], width=1.6))
    self.cvd_ema21_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["ema_21"], width=1.6))
    self.cvd_ema51_curve = pg.PlotCurveItem(pen=pg.mkPen(THEME["ema_51"], width=1.6))
    for c in (self.cvd_ema10_curve, self.cvd_ema21_curve, self.cvd_ema51_curve):
        c.setOpacity(0.68)
        self.plot.addItem(c)

    self.crosshair_line = pg.InfiniteLine(angle=90, movable=False, pen=pen_cross)
    self.crosshair_line.hide()
    self.plot.addItem(self.crosshair_line)

    self.x_time_label = pg.TextItem("", anchor=(0.5, 1), color=THEME["text_primary"],
        fill=pg.mkBrush(THEME["bg_card"]), border=pg.mkPen(THEME["border"]))
    self.x_time_label.hide()
    self.plot.addItem(self.x_time_label, ignoreBounds=True)
    self.cvd_legend = None

    # ── Link axes ─────────────────────────────────────────────────────
    self.price_plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
    self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
    self.price_plot.setXLink(self.plot)

    # ── Live dot blink timer ──────────────────────────────────────────
    self.dot_timer = QTimer(self)
    self.dot_timer.timeout.connect(self._blink_dot)
    self.dot_timer.start(500)
    self._dot_visible = True

    # ── Tick repaint timers ───────────────────────────────────────────
    self._tick_repaint_timer = QTimer(self)
    self._tick_repaint_timer.setSingleShot(True)
    self._tick_repaint_timer.timeout.connect(self._plot_live_ticks_only)

    self._cvd_tick_flush_timer = QTimer(self)
    self._cvd_tick_flush_timer.setSingleShot(True)
    self._cvd_tick_flush_timer.timeout.connect(self._flush_pending_cvd_tick)

    # ── Live tick pen cache ───────────────────────────────────────────
    self._live_tick_cvd_pen   = pg.mkPen(THEME["accent_teal"], width=1.4, cosmetic=True)
    self._live_tick_price_pen = pg.mkPen(THEME["accent_gold"], width=1.4, cosmetic=True)

    self._apply_visual_settings()

    # Navigator tooltips
    self.navigator.btn_back.setToolTip("Previous trading day (←)")
    self.navigator.btn_forward.setToolTip("Next trading day (→)")