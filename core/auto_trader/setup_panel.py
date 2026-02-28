import re

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap, QTransform
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QHBoxLayout,
    QPushButton,
    QWidget,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QColorDialog,
    QFileDialog,
    QGraphicsPixmapItem,
    QTabWidget,
    QGridLayout,
)


# ══════════════════════════════════════════════════════════════════════════════
# LAYOUT CONSTANTS  ←  tweak everything here, nowhere else
# ══════════════════════════════════════════════════════════════════════════════
_DLG_MIN_W   = 1320   # dialog minimum width  (px)
_DLG_MIN_H   = 640    # dialog minimum height (px)
_COL_SPACING = 8      # gap between columns   (px)
_GRP_SPACING = 6      # gap between groups    (px)
_FORM_MARGIN = (7, 5, 7, 5)  # L,T,R,B inside each group
_FORM_VSPACE = 4      # vertical row spacing in forms
_FORM_HSPACE = 8      # label-to-widget horizontal gap
_INPUT_W     = 80     # spinbox / short inputs
_COMBO_W     = 140    # combo boxes
_DLG_MARGIN  = (10, 8, 10, 8)  # dialog outer margins L,T,R,B

# ── Colours (change here to re-theme) ─────────────────────────────────────
_C_BG        = "#161A25"
_C_BORDER    = "#3A4458"
_C_GRP_TITLE = "#9CCAF4"
_C_LABEL     = "#B0B0B0"
_C_NOTE      = "#8A9BA8"
_C_BTN_BG    = "#2A2F3D"
_C_BTN_TEXT  = "#E0E0E0"
_C_HOVER     = "#5B9BD5"


class SetupPanelMixin:

    def _build_setup_dialog(self, compact_combo_style: str, compact_spinbox_style: str):
        # ── Dialog shell ───────────────────────────────────────────────────
        self.setup_dialog = QDialog(self)
        self.setup_dialog.setWindowTitle("Auto Trader Setup")
        self.setup_dialog.setModal(False)
        self.setup_dialog.setMinimumWidth(_DLG_MIN_W)
        self.setup_dialog.setMinimumHeight(_DLG_MIN_H)
        self.setup_dialog.setStyleSheet(f"""
            QDialog       {{ background: {_C_BG}; color: {_C_BTN_TEXT}; }}
            QGroupBox     {{
                border: 1px solid {_C_BORDER}; border-radius: 6px;
                margin-top: 8px; padding-top: 8px;
                font-weight: 600; color: {_C_GRP_TITLE}; font-size: 11px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: 10px; padding: 0 5px;
            }}
            QLabel        {{ color: {_C_LABEL}; font-size: 10px; font-weight: 600; }}
            QCheckBox     {{ color: {_C_LABEL}; font-size: 10px; }}
        """)

        root = QVBoxLayout(self.setup_dialog)
        root.setContentsMargins(*_DLG_MARGIN)
        root.setSpacing(6)

        # ── Helpers ────────────────────────────────────────────────────────
        def _w(widget, w=_INPUT_W):
            widget.setFixedWidth(w)
            return widget

        def _wc(widget, w=_COMBO_W):
            widget.setFixedWidth(w)
            return widget

        def _note(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color:{_C_NOTE}; font-size:9px; font-weight:400;")
            lbl.setWordWrap(True)
            return lbl

        def _group(title):
            """Return (QGroupBox, QFormLayout) pair, pre-configured."""
            grp = QGroupBox(title)
            frm = QFormLayout(grp)
            frm.setLabelAlignment(Qt.AlignLeft)
            frm.setContentsMargins(*_FORM_MARGIN)
            frm.setSpacing(_FORM_VSPACE)
            frm.setHorizontalSpacing(_FORM_HSPACE)
            return grp, frm

        def _col():
            v = QVBoxLayout()
            v.setSpacing(_GRP_SPACING)
            return v

        _color_btn_style = f"""
            QPushButton {{
                background: {_C_BTN_BG}; color: {_C_BTN_TEXT};
                border: 1px solid {_C_BORDER}; border-radius: 3px;
                padding: 2px 5px; font-size: 10px; min-height: 18px;
            }}
            QPushButton:hover {{ border: 1px solid {_C_HOVER}; }}
        """

        # ══════════════════════════════════════════════════════════════════
        # COLUMN 1 — Automation · Stacker · ATR/Signal
        # ══════════════════════════════════════════════════════════════════
        c1 = _col()

        # ── Automation ────────────────────────────────────────────────────
        auto_grp, auto_frm = _group("Automation")
        _w(self.automation_stoploss_input)
        _w(self.max_profit_giveback_input)
        _wc(self.automation_route_combo)
        _wc(self.automation_order_type_combo)
        auto_frm.addRow("Stop Loss",         self.automation_stoploss_input)
        auto_frm.addRow("Max Giveback",      self.max_profit_giveback_input)

        gb_row = QWidget()
        gb_lay = QHBoxLayout(gb_row)
        gb_lay.setContentsMargins(0, 0, 0, 0)
        gb_lay.setSpacing(5)
        for cb in (self.max_giveback_atr_reversal_check,
                   self.max_giveback_ema_cross_check,
                   self.max_giveback_atr_divergence_check,
                   self.max_giveback_range_breakout_check):
            gb_lay.addWidget(cb)
        gb_lay.addStretch()
        auto_frm.addRow("Giveback On", gb_row)

        trend_exit_row = QWidget()
        trend_exit_lay = QVBoxLayout(trend_exit_row)
        trend_exit_lay.setContentsMargins(0, 0, 0, 0)
        trend_exit_lay.setSpacing(3)
        for cb in (
            self.dynamic_exit_atr_reversal_check,
            self.dynamic_exit_ema_cross_check,
            self.dynamic_exit_atr_divergence_check,
            self.dynamic_exit_range_breakout_check,
            self.dynamic_exit_cvd_range_breakout_check,
            self.dynamic_exit_open_drive_check,
        ):
            trend_exit_lay.addWidget(cb)
        auto_frm.addRow("Trend Exit On", trend_exit_row)

        # ── Trend-ride ENTRY conditions ───────────────────────────────────
        trend_entry_widget = QWidget()
        trend_entry_lay = QFormLayout(trend_entry_widget)
        trend_entry_lay.setContentsMargins(0, 2, 0, 2)
        trend_entry_lay.setSpacing(3)
        trend_entry_lay.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        _w(self.trend_exit_adx_min_input)
        _w(self.trend_exit_atr_ratio_min_input)
        _w(self.trend_entry_consecutive_bars_input)
        _w(self.trend_exit_confirm_bars_input)
        _w(self.trend_exit_min_profit_input)

        trend_entry_lay.addRow("ADX Min",          self.trend_exit_adx_min_input)
        trend_entry_lay.addRow("ATR Ratio Min",    self.trend_exit_atr_ratio_min_input)
        trend_entry_lay.addRow("Consec Bars ↑",    self.trend_entry_consecutive_bars_input)
        trend_entry_lay.addRow("Confirm Bars",     self.trend_exit_confirm_bars_input)
        trend_entry_lay.addRow("Min Profit (pts)", self.trend_exit_min_profit_input)

        # Slope gate toggles on one row
        slope_row = QWidget()
        slope_lay = QHBoxLayout(slope_row)
        slope_lay.setContentsMargins(0, 0, 0, 0)
        slope_lay.setSpacing(6)
        slope_lay.addWidget(self.trend_entry_require_adx_slope_check)
        slope_lay.addWidget(self.trend_entry_require_vol_slope_check)
        slope_lay.addStretch()
        trend_entry_lay.addRow("Slope Gates", slope_row)

        auto_frm.addRow("Trend Entry", trend_entry_widget)

        # ── Trend-ride EXIT (regime breakdown) conditions ─────────────────
        trend_exit_thresh_widget = QWidget()
        trend_exit_thresh_lay = QFormLayout(trend_exit_thresh_widget)
        trend_exit_thresh_lay.setContentsMargins(0, 2, 0, 2)
        trend_exit_thresh_lay.setSpacing(3)
        trend_exit_thresh_lay.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        _w(self.trend_exit_breakdown_bars_input)
        _w(self.trend_exit_breakdown_lookback_input)
        _w(self.trend_exit_vol_drop_pct_input)

        trend_exit_thresh_lay.addRow("Breakdown Bars",     self.trend_exit_breakdown_bars_input)
        trend_exit_thresh_lay.addRow("Breakdown Lookback", self.trend_exit_breakdown_lookback_input)
        trend_exit_thresh_lay.addRow("Vol Drop %",         self.trend_exit_vol_drop_pct_input)

        auto_frm.addRow("Trend Exit", trend_exit_thresh_widget)

        auto_time_row = QWidget()
        auto_time_lay = QHBoxLayout(auto_time_row)
        auto_time_lay.setContentsMargins(0, 0, 0, 0)
        auto_time_lay.setSpacing(4)
        auto_time_lay.addWidget(self.automation_start_time_hour_input)
        auto_time_lay.addWidget(QLabel(":"))
        auto_time_lay.addWidget(self.automation_start_time_minute_input)
        auto_time_lay.addSpacing(8)
        auto_time_lay.addWidget(QLabel("to"))
        auto_time_lay.addSpacing(8)
        auto_time_lay.addWidget(self.automation_cutoff_time_hour_input)
        auto_time_lay.addWidget(QLabel(":"))
        auto_time_lay.addWidget(self.automation_cutoff_time_minute_input)
        auto_time_lay.addStretch()

        auto_frm.addRow("Route",       self.automation_route_combo)
        auto_frm.addRow("Order Type",  self.automation_order_type_combo)
        auto_frm.addRow("Time Window", auto_time_row)
        c1.addWidget(auto_grp)

        # ── Stacker (built here, placed at top of column 2) ───────────────
        stk_grp, stk_frm = _group("Stacker")
        _w(self.stacker_step_input)
        _w(self.stacker_max_input)
        _w(self.harvest_threshold_input)
        stk_frm.addRow(_note("Pyramid: add a position every N favorable points."))
        stk_frm.addRow("Enable",        self.stacker_enabled_check)
        stk_frm.addRow("Step (pts)",    self.stacker_step_input)
        stk_frm.addRow("Max Stacks",    self.stacker_max_input)
        stk_frm.addRow(_note("FIFO harvest: exit oldest stack each time total PnL crosses the threshold."))
        stk_frm.addRow("Harvest",       self.harvest_enabled_check)
        stk_frm.addRow("Harvest (₹)",   self.harvest_threshold_input)

        # ── ATR / Signal ──────────────────────────────────────────────────
        sig_grp, sig_frm = _group("ATR / Signal")
        _w(self.atr_base_ema_input)
        _w(self.atr_distance_input)
        _w(self.cvd_atr_distance_input)
        _w(self.atr_extension_threshold_input)
        _w(self.atr_flat_velocity_pct_input)
        sig_frm.addRow("ATR Base EMA", self.atr_base_ema_input)
        sig_frm.addRow("ATR Distance", self.atr_distance_input)
        sig_frm.addRow("CVD Z-Score Min", self.cvd_atr_distance_input)
        sig_frm.addRow("ATR Ext Min", self.atr_extension_threshold_input)
        sig_frm.addRow("ATR Flat Vel%", self.atr_flat_velocity_pct_input)

        self.ema_cross_use_parent_mask_check = QCheckBox("Require 5m Parent Trend")
        self.ema_cross_use_parent_mask_check.setChecked(True)
        self.ema_cross_use_parent_mask_check.setToolTip(
            "Use 5-minute parent trend as a confluence gate for EMA+CVD Cross signals.\n"
            "Disable to allow EMA Cross signals without higher-timeframe trend confirmation."
        )
        self.ema_cross_use_parent_mask_check.toggled.connect(self._on_breakout_settings_changed)

        self.setup_signal_filter_combo = QComboBox()
        self.setup_signal_filter_combo.setStyleSheet(compact_combo_style)
        _wc(self.setup_signal_filter_combo)
        self._init_signal_filter_combo(self.setup_signal_filter_combo)
        self._set_checked_signal_filters(
            self.setup_signal_filter_combo,
            self._checked_signal_filters(self.signal_filter_combo),
        )
        sig_frm.addRow("Signal Filter", self.setup_signal_filter_combo)

        self.setup_cvd_value_mode_combo = QComboBox()
        self.setup_cvd_value_mode_combo.setStyleSheet(compact_combo_style)
        _wc(self.setup_cvd_value_mode_combo)
        self.setup_cvd_value_mode_combo.addItem("Raw CVD",        self.CVD_VALUE_MODE_RAW)
        self.setup_cvd_value_mode_combo.addItem("Normalized CVD", self.CVD_VALUE_MODE_NORMALIZED)
        self.setup_cvd_value_mode_combo.setToolTip(
            "CVD feed mode.\n• Raw CVD: absolute cumulative delta.\n"
            "• Normalized CVD: CVD ÷ cumulative session volume."
        )
        self.setup_cvd_value_mode_combo.currentIndexChanged.connect(self._on_cvd_value_mode_changed)
        sig_frm.addRow("CVD Mode", self.setup_cvd_value_mode_combo)

        self.setup_atr_marker_filter_combo = QComboBox()
        self.setup_atr_marker_filter_combo.setStyleSheet(compact_combo_style)
        _wc(self.setup_atr_marker_filter_combo)
        for label, data in (
            ("Show All",        self.ATR_MARKER_SHOW_ALL),
            ("Confluence Only", self.ATR_MARKER_CONFLUENCE_ONLY),
            ("Green Only",      self.ATR_MARKER_GREEN_ONLY),
            ("Red Only",        self.ATR_MARKER_RED_ONLY),
            ("Hide All",        self.ATR_MARKER_HIDE_ALL),
        ):
            self.setup_atr_marker_filter_combo.addItem(label, data)
        self.setup_atr_marker_filter_combo.setCurrentIndex(self.atr_marker_filter_combo.currentIndex())
        self.setup_atr_marker_filter_combo.currentIndexChanged.connect(self._on_setup_atr_marker_filter_changed)
        sig_frm.addRow("ATR Markers", self.setup_atr_marker_filter_combo)
        c1.addStretch()

        # ══════════════════════════════════════════════════════════════════
        # COLUMN 2 — Stacker · Signal Governance · Chop Filter
        # ══════════════════════════════════════════════════════════════════
        c2 = _col()
        c2.addWidget(stk_grp)

        # ── Range Breakout (built here, shown in its own strategy tab) ────
        brk_grp, brk_frm = _group("Range Breakout")

        self.range_lookback_input = QSpinBox()
        self.range_lookback_input.setRange(10, 120)
        self.range_lookback_input.setValue(15)
        self.range_lookback_input.setSuffix(" min")
        self.range_lookback_input.setStyleSheet(compact_spinbox_style)
        _w(self.range_lookback_input)
        self.range_lookback_input.setToolTip(
            "Period to analyze for consolidation range detection.\n"
            "Breakout signals trigger when price breaks above/below this range."
        )
        self.range_lookback_input.valueChanged.connect(self._on_breakout_settings_changed)
        brk_frm.addRow("Range Lookback", self.range_lookback_input)

        self.breakout_switch_mode_combo = QComboBox()
        self.breakout_switch_mode_combo.setStyleSheet(compact_combo_style)
        _wc(self.breakout_switch_mode_combo)
        for label, data in (
            ("Keep Breakout",  self.BREAKOUT_SWITCH_KEEP),
            ("Prefer ATR Rev", self.BREAKOUT_SWITCH_PREFER_ATR),
            ("Adaptive",       self.BREAKOUT_SWITCH_ADAPTIVE),
        ):
            self.breakout_switch_mode_combo.addItem(label, data)
        self.breakout_switch_mode_combo.setToolTip(
            "Controls behavior when ATR reversal appears after a breakout:\n"
            "• Keep Breakout: ignore opposite ATR reversals.\n"
            "• Prefer ATR Rev: allow reversal immediately.\n"
            "• Adaptive: keep breakout only when momentum is still strong."
        )
        self.breakout_switch_mode_combo.currentIndexChanged.connect(self._on_breakout_settings_changed)
        brk_frm.addRow("Breakout vs ATR", self.breakout_switch_mode_combo)

        self.atr_skip_limit_input = QSpinBox()
        self.atr_skip_limit_input.setRange(0, 20)
        self.atr_skip_limit_input.setValue(0)
        self.atr_skip_limit_input.setSpecialValueText("Off")
        self.atr_skip_limit_input.setStyleSheet(compact_spinbox_style)
        _w(self.atr_skip_limit_input)
        self.atr_skip_limit_input.setToolTip(
            "ATR signals to skip while a Range Breakout trade is active.\n"
            "0 = Off. Example: 3 → skip first 3, take the 4th."
        )
        self.atr_skip_limit_input.valueChanged.connect(self._on_breakout_settings_changed)
        brk_frm.addRow("ATR Skip Limit", self.atr_skip_limit_input)

        self.atr_trailing_step_input = QDoubleSpinBox()
        self.atr_trailing_step_input.setRange(0.5, 200.0)
        self.atr_trailing_step_input.setDecimals(1)
        self.atr_trailing_step_input.setSingleStep(0.5)
        self.atr_trailing_step_input.setValue(10.0)
        self.atr_trailing_step_input.setSuffix(" pts")
        self.atr_trailing_step_input.setStyleSheet(compact_spinbox_style)
        _w(self.atr_trailing_step_input)
        self.atr_trailing_step_input.setToolTip(
            "Base trailing step for ATR reversal exits in points.\n"
            "Effective step expands with ATR (current ATR / entry ATR),\n"
            "so fast breakouts widen the trail automatically."
        )
        self.atr_trailing_step_input.valueChanged.connect(self._on_breakout_settings_changed)
        brk_frm.addRow("ATR Trail Base", self.atr_trailing_step_input)
        # ── Signal Governance ─────────────────────────────────────────────
        gov_grp, gov_frm = _group("Signal Governance")

        self.deploy_mode_combo = QComboBox()
        self.deploy_mode_combo.setStyleSheet(compact_combo_style)
        _wc(self.deploy_mode_combo)
        for label, data in (("Shadow", "shadow"), ("Canary", "canary"), ("Live", "live")):
            self.deploy_mode_combo.addItem(label, data)
        self.deploy_mode_combo.setToolTip("Deployment guardrail mode for auto signals.")
        self.deploy_mode_combo.currentIndexChanged.connect(self._on_governance_settings_changed)
        gov_frm.addRow("Deploy Mode", self.deploy_mode_combo)

        self.min_confidence_input = QDoubleSpinBox()
        self.min_confidence_input.setRange(0.0, 1.0)
        self.min_confidence_input.setDecimals(2)
        self.min_confidence_input.setSingleStep(0.05)
        self.min_confidence_input.setValue(0.55)
        self.min_confidence_input.setStyleSheet(compact_spinbox_style)
        _w(self.min_confidence_input)
        self.min_confidence_input.setToolTip("Minimum confidence needed before signal can go live.")
        self.min_confidence_input.valueChanged.connect(self._on_governance_settings_changed)
        gov_frm.addRow("Min Confidence", self.min_confidence_input)

        self.canary_ratio_input = QDoubleSpinBox()
        self.canary_ratio_input.setRange(0.0, 1.0)
        self.canary_ratio_input.setDecimals(2)
        self.canary_ratio_input.setSingleStep(0.05)
        self.canary_ratio_input.setValue(0.25)
        self.canary_ratio_input.setStyleSheet(compact_spinbox_style)
        _w(self.canary_ratio_input)
        self.canary_ratio_input.setToolTip("Fraction of qualified signals allowed live in canary mode.")
        self.canary_ratio_input.valueChanged.connect(self._on_governance_settings_changed)
        gov_frm.addRow("Canary Ratio", self.canary_ratio_input)

        # ── 4 new knobs ───────────────────────────────────────────────────
        self.health_alert_threshold_input = QDoubleSpinBox()
        self.health_alert_threshold_input.setRange(0.10, 0.70)
        self.health_alert_threshold_input.setDecimals(2)
        self.health_alert_threshold_input.setSingleStep(0.05)
        self.health_alert_threshold_input.setValue(0.40)
        self.health_alert_threshold_input.setStyleSheet(compact_spinbox_style)
        _w(self.health_alert_threshold_input)
        self.health_alert_threshold_input.setToolTip(
            "Strategy health score below this flags it as 'degraded' and adds a warning reason.\n"
            "Health = win-rate weighted 65% + edge 35%, computed over last 80 bars.\n"
            "Lower = more tolerant of drawdown; Higher = cuts strategies faster."
        )
        self.health_alert_threshold_input.valueChanged.connect(self._on_governance_settings_changed)
        gov_frm.addRow("Health Alert", self.health_alert_threshold_input)

        self.strategy_weight_decay_input = QDoubleSpinBox()
        self.strategy_weight_decay_input.setRange(0.50, 0.99)
        self.strategy_weight_decay_input.setDecimals(2)
        self.strategy_weight_decay_input.setSingleStep(0.01)
        self.strategy_weight_decay_input.setValue(0.90)
        self.strategy_weight_decay_input.setStyleSheet(compact_spinbox_style)
        _w(self.strategy_weight_decay_input)
        self.strategy_weight_decay_input.setToolTip(
            "Exponential decay λ for adaptive strategy weights.\n"
            "Higher (0.95) = longer memory, slower to react to recent results.\n"
            "Lower (0.80) = highly reactive, forgets old wins/losses quickly.\n"
            "Institutional default: 0.90 (≈ 10-bar half-life on signal edge)."
        )
        self.strategy_weight_decay_input.valueChanged.connect(self._on_governance_settings_changed)
        gov_frm.addRow("Weight Decay λ", self.strategy_weight_decay_input)

        self.strategy_weight_floor_input = QDoubleSpinBox()
        self.strategy_weight_floor_input.setRange(0.01, 0.25)
        self.strategy_weight_floor_input.setDecimals(2)
        self.strategy_weight_floor_input.setSingleStep(0.01)
        self.strategy_weight_floor_input.setValue(0.05)
        self.strategy_weight_floor_input.setStyleSheet(compact_spinbox_style)
        _w(self.strategy_weight_floor_input)
        self.strategy_weight_floor_input.setToolTip(
            "Minimum weight any strategy can be assigned in the adaptive weighting system.\n"
            "Prevents a losing strategy from being zeroed out entirely — keeps it in 'reserve'.\n"
            "0.05 = floor at 5%%. Set higher (0.10) for more equal allocation."
        )
        self.strategy_weight_floor_input.valueChanged.connect(self._on_governance_settings_changed)
        gov_frm.addRow("Weight Floor", self.strategy_weight_floor_input)

        self.drift_window_input = QSpinBox()
        self.drift_window_input.setRange(20, 480)
        self.drift_window_input.setSingleStep(10)
        self.drift_window_input.setValue(120)
        self.drift_window_input.setStyleSheet(compact_spinbox_style)
        _w(self.drift_window_input)
        self.drift_window_input.setToolTip(
            "Feature drift detection window (bars).\n"
            "Compares recent N-bar feature distribution vs. baseline to detect regime shift.\n"
            "Shorter (60) = catches drift faster but more noisy.\n"
            "Longer (240) = smoother, catches structural drift only."
        )
        self.drift_window_input.valueChanged.connect(self._on_governance_settings_changed)
        gov_frm.addRow("Drift Window", self.drift_window_input)

        c2.addWidget(gov_grp)

        # ── Open Drive Model ──────────────────────────────────────────────
        od_grp, od_frm = _group("Open Drive")

        self.open_drive_enabled_check = QCheckBox("Enable Open Drive")
        self.open_drive_enabled_check.setChecked(False)
        self.open_drive_enabled_check.setToolTip(
            "Fires only at configured time when Price/EMA/VWAP/CVD conditions align."
        )
        self.open_drive_enabled_check.toggled.connect(self._on_open_drive_settings_changed)
        od_frm.addRow("Enable", self.open_drive_enabled_check)

        od_time_row = QWidget()
        od_time_lay = QHBoxLayout(od_time_row)
        od_time_lay.setContentsMargins(0, 0, 0, 0)
        od_time_lay.setSpacing(4)

        self.open_drive_time_hour_input = QSpinBox()
        self.open_drive_time_hour_input.setRange(0, 23)
        self.open_drive_time_hour_input.setValue(9)
        self.open_drive_time_hour_input.setStyleSheet(compact_spinbox_style)
        _w(self.open_drive_time_hour_input, w=48)
        self.open_drive_time_hour_input.valueChanged.connect(self._on_open_drive_settings_changed)

        self.open_drive_time_minute_input = QSpinBox()
        self.open_drive_time_minute_input.setRange(0, 59)
        self.open_drive_time_minute_input.setValue(17)
        self.open_drive_time_minute_input.setStyleSheet(compact_spinbox_style)
        _w(self.open_drive_time_minute_input, w=48)
        self.open_drive_time_minute_input.valueChanged.connect(self._on_open_drive_settings_changed)

        od_time_lay.addWidget(self.open_drive_time_hour_input)
        od_time_lay.addWidget(QLabel(":"))
        od_time_lay.addWidget(self.open_drive_time_minute_input)
        od_time_lay.addStretch()
        od_frm.addRow("Entry Time", od_time_row)

        self.open_drive_stack_enabled_check = QCheckBox("Stack continuation")
        self.open_drive_stack_enabled_check.setChecked(True)
        self.open_drive_stack_enabled_check.setToolTip("Allow Stacker continuation for Open Drive entries.")
        self.open_drive_stack_enabled_check.toggled.connect(self._on_open_drive_settings_changed)
        od_frm.addRow("Stack", self.open_drive_stack_enabled_check)

        self.open_drive_max_profit_giveback_input = QSpinBox()
        self.open_drive_max_profit_giveback_input.setRange(0, 5000)
        self.open_drive_max_profit_giveback_input.setValue(0)
        self.open_drive_max_profit_giveback_input.setSingleStep(5)
        self.open_drive_max_profit_giveback_input.setSpecialValueText("Off")
        self.open_drive_max_profit_giveback_input.setStyleSheet(compact_spinbox_style)
        _w(self.open_drive_max_profit_giveback_input)
        self.open_drive_max_profit_giveback_input.setToolTip(
            "Open Drive-only max profit giveback (pts).\nOverrides global giveback for OD trades. 0 = Off."
        )
        self.open_drive_max_profit_giveback_input.valueChanged.connect(self._on_open_drive_settings_changed)
        od_frm.addRow("OD Giveback", self.open_drive_max_profit_giveback_input)

        self.open_drive_tick_drawdown_limit_input = QSpinBox()
        self.open_drive_tick_drawdown_limit_input.setRange(0, 5000)
        self.open_drive_tick_drawdown_limit_input.setValue(100)
        self.open_drive_tick_drawdown_limit_input.setSingleStep(5)
        self.open_drive_tick_drawdown_limit_input.setSpecialValueText("Off")
        self.open_drive_tick_drawdown_limit_input.setStyleSheet(compact_spinbox_style)
        _w(self.open_drive_tick_drawdown_limit_input)
        self.open_drive_tick_drawdown_limit_input.setToolTip(
            "Open Drive-only live tick drawdown limit (pts).\n"
            "Exits immediately when adverse move from entry reaches this value. 0 = Off."
        )
        self.open_drive_tick_drawdown_limit_input.valueChanged.connect(self._on_open_drive_settings_changed)
        od_frm.addRow("OD Tick DD", self.open_drive_tick_drawdown_limit_input)

        # ── Chop Filter ───────────────────────────────────────────────────
        chop_grp, chop_frm = _group("Chop Filter")
        chop_frm.addRow(_note("Range Breakout & Open Drive are never chop-filtered."))

        self.chop_filter_atr_reversal_check = QCheckBox("ATR Reversal")
        self.chop_filter_atr_reversal_check.setChecked(True)
        self.chop_filter_atr_reversal_check.setToolTip(
            "Filter ATR Reversal signals in choppy regime (low ADX / price hugging EMA51)."
        )
        self.chop_filter_atr_reversal_check.toggled.connect(self._on_chop_filter_settings_changed)

        self.chop_filter_ema_cross_check = QCheckBox("EMA Cross")
        self.chop_filter_ema_cross_check.setChecked(True)
        self.chop_filter_ema_cross_check.setToolTip(
            "Filter EMA Cross signals in chop. Recommended — crosses in flat markets are false."
        )
        self.chop_filter_ema_cross_check.toggled.connect(self._on_chop_filter_settings_changed)

        self.chop_filter_atr_divergence_check = QCheckBox("ATR Div")
        self.chop_filter_atr_divergence_check.setChecked(True)
        self.chop_filter_atr_divergence_check.setToolTip(
            "Filter ATR Divergence signals in chop — needs a trending CVD context."
        )
        self.chop_filter_atr_divergence_check.toggled.connect(self._on_chop_filter_settings_changed)

        chop_checks = QWidget()
        chop_checks_lay = QHBoxLayout(chop_checks)
        chop_checks_lay.setContentsMargins(0, 0, 0, 0)
        chop_checks_lay.setSpacing(5)
        chop_checks_lay.addWidget(self.chop_filter_atr_reversal_check)
        chop_checks_lay.addWidget(self.chop_filter_ema_cross_check)
        chop_checks_lay.addWidget(self.chop_filter_atr_divergence_check)
        chop_checks_lay.addStretch()
        chop_frm.addRow(chop_checks)

        # CVD Range Breakout chop filter — separate row, OFF by default.
        # Low-ADX consolidation IS the precondition for CVD breakout signals,
        # so filtering on chop would eat valid setups. Only enable if you want
        # to require a trending market before taking CVD breakouts.
        self.chop_filter_cvd_range_breakout_check = QCheckBox("CVD Range Breakout")
        self.chop_filter_cvd_range_breakout_check.setChecked(False)  # ← default OFF
        self.chop_filter_cvd_range_breakout_check.setToolTip(
            "Apply chop filter to CVD Range Breakout signals.\n\n"
            "DEFAULT: OFF — because low-ADX consolidation is the setup\n"
            "condition for CVD breakouts. Enabling this will suppress many\n"
            "valid signals in quiet, compressing markets.\n\n"
            "Enable only if you want to require a trending (high-ADX) market\n"
            "before accepting CVD breakout entries."
        )
        self.chop_filter_cvd_range_breakout_check.toggled.connect(self._on_chop_filter_settings_changed)

        cvd_chop_row = QWidget()
        cvd_chop_lay = QHBoxLayout(cvd_chop_row)
        cvd_chop_lay.setContentsMargins(0, 0, 0, 0)
        cvd_chop_lay.setSpacing(5)
        cvd_chop_lay.addWidget(self.chop_filter_cvd_range_breakout_check)
        cvd_chop_lay.addStretch()
        chop_frm.addRow("CVD Bkt", cvd_chop_row)
        c2.addWidget(chop_grp)
        c2.addStretch()

        # ── Breakout Consolidation ────────────────────────────────────────
        consol_grp, consol_frm = _group("Breakout Consolidation")
        consol_frm.addRow(_note("Require a squeeze before breakout fires. 0 = off."))

        self.breakout_min_consol_input = QSpinBox()
        self.breakout_min_consol_input.setRange(0, 120)
        self.breakout_min_consol_input.setSingleStep(5)
        self.breakout_min_consol_input.setValue(0)
        self.breakout_min_consol_input.setSuffix(" min")
        self.breakout_min_consol_input.setStyleSheet(compact_spinbox_style)
        _w(self.breakout_min_consol_input)
        self.breakout_min_consol_input.setToolTip(
            "Require price to be range-bound for at least N minutes before a breakout.\n"
            "0 = disabled. Recommended: 15–30 min."
        )
        self.breakout_min_consol_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        consol_frm.addRow("Min Consol", self.breakout_min_consol_input)

        self.breakout_min_consol_adx_input = QDoubleSpinBox()
        self.breakout_min_consol_adx_input.setRange(0.0, 50.0)
        self.breakout_min_consol_adx_input.setDecimals(1)
        self.breakout_min_consol_adx_input.setSingleStep(1.0)
        self.breakout_min_consol_adx_input.setValue(0.0)
        self.breakout_min_consol_adx_input.setStyleSheet(compact_spinbox_style)
        _w(self.breakout_min_consol_adx_input)
        self.breakout_min_consol_adx_input.setToolTip(
            "During consolidation, require ADX below this threshold.\n0 = disabled. Recommended: 20–22."
        )
        self.breakout_min_consol_adx_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        consol_frm.addRow("Max ADX", self.breakout_min_consol_adx_input)
        # ── CVD Range Breakout — LEFT COLUMN (all knobs) ──────────────────
        cvdbk_grp, cvdbk_frm = _group("CVD Range Breakout")
        cvdbk_frm.addRow(_note("CVD breaks its own range first; price slope confirms."))

        self.cvd_range_lookback_input = QSpinBox()
        self.cvd_range_lookback_input.setRange(5, 120)
        self.cvd_range_lookback_input.setSingleStep(1)
        self.cvd_range_lookback_input.setValue(30)
        self.cvd_range_lookback_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_range_lookback_input)
        self.cvd_range_lookback_input.setToolTip("Lookback bars used to build the CVD consolidation range.")
        self.cvd_range_lookback_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("Lookback", self.cvd_range_lookback_input)

        self.cvd_breakout_buffer_input = QDoubleSpinBox()
        self.cvd_breakout_buffer_input.setRange(0.0, 1.0)
        self.cvd_breakout_buffer_input.setDecimals(2)
        self.cvd_breakout_buffer_input.setSingleStep(0.01)
        self.cvd_breakout_buffer_input.setValue(0.10)
        self.cvd_breakout_buffer_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_breakout_buffer_input)
        self.cvd_breakout_buffer_input.setToolTip(
            "Extra breakout extension beyond CVD range edge (fraction of range size)."
        )
        self.cvd_breakout_buffer_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("Buffer", self.cvd_breakout_buffer_input)

        self.cvd_min_consol_bars_input = QSpinBox()
        self.cvd_min_consol_bars_input.setRange(1, 120)
        self.cvd_min_consol_bars_input.setSingleStep(1)
        self.cvd_min_consol_bars_input.setValue(15)
        self.cvd_min_consol_bars_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_min_consol_bars_input)
        self.cvd_min_consol_bars_input.setToolTip("Min consecutive CVD compression bars before breakout is valid.")
        self.cvd_min_consol_bars_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("Min Consol", self.cvd_min_consol_bars_input)

        self.cvd_max_range_ratio_input = QDoubleSpinBox()
        self.cvd_max_range_ratio_input.setRange(0.05, 3.0)
        self.cvd_max_range_ratio_input.setDecimals(2)
        self.cvd_max_range_ratio_input.setSingleStep(0.05)
        self.cvd_max_range_ratio_input.setValue(0.80)
        self.cvd_max_range_ratio_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_max_range_ratio_input)
        self.cvd_max_range_ratio_input.setToolTip(
            "Compression threshold: CVD range must be <= avg_range × this ratio."
        )
        self.cvd_max_range_ratio_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("Max Ratio", self.cvd_max_range_ratio_input)

        self.cvd_breakout_min_adx_input = QDoubleSpinBox()
        self.cvd_breakout_min_adx_input.setRange(0.0, 60.0)
        self.cvd_breakout_min_adx_input.setDecimals(1)
        self.cvd_breakout_min_adx_input.setSingleStep(0.5)
        self.cvd_breakout_min_adx_input.setValue(15.0)
        self.cvd_breakout_min_adx_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_breakout_min_adx_input)
        self.cvd_breakout_min_adx_input.setToolTip(
            "Require ADX > this level OR enough CVD consolidation bars. 0 disables ADX gate."
        )
        self.cvd_breakout_min_adx_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("Min ADX", self.cvd_breakout_min_adx_input)

        # ── Conviction Scoring (Institutional Upgrade) ────────────────────
        cvdbk_frm.addRow(_note("Conviction Scoring: score 1 pt per filter, fire when score ≥ Min Score."))

        self.cvd_conviction_score_input = QSpinBox()
        self.cvd_conviction_score_input.setRange(1, 5)
        self.cvd_conviction_score_input.setValue(3)
        self.cvd_conviction_score_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_conviction_score_input)
        self.cvd_conviction_score_input.setToolTip(
            "Minimum institutional conviction score to fire (1–5).\n"
            "Each filter adds 1 pt: ADX rising, ATR expanding, Volume spike,\n"
            "HTF trend aligned, Regime not opposing.\n"
            "3 = need 3-of-5.  Raise to 4 for higher quality, lower to 2 for more signals."
        )
        self.cvd_conviction_score_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("Min Score", self.cvd_conviction_score_input)

        self.cvd_vol_expansion_mult_input = QDoubleSpinBox()
        self.cvd_vol_expansion_mult_input.setRange(1.0, 3.0)
        self.cvd_vol_expansion_mult_input.setDecimals(2)
        self.cvd_vol_expansion_mult_input.setSingleStep(0.05)
        self.cvd_vol_expansion_mult_input.setValue(1.15)
        self.cvd_vol_expansion_mult_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_vol_expansion_mult_input)
        self.cvd_vol_expansion_mult_input.setToolTip(
            "Volume expansion filter: bar volume must exceed avg × this multiplier.\n"
            "1.15 = 15% above average. Raise to 1.3+ to require a strong volume spike.\n"
            "Catches real institutional participation vs ghost breakouts."
        )
        self.cvd_vol_expansion_mult_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("Vol Mult", self.cvd_vol_expansion_mult_input)

        self.cvd_atr_expansion_pct_input = QDoubleSpinBox()
        self.cvd_atr_expansion_pct_input.setRange(0.0, 0.5)
        self.cvd_atr_expansion_pct_input.setDecimals(2)
        self.cvd_atr_expansion_pct_input.setSingleStep(0.01)
        self.cvd_atr_expansion_pct_input.setValue(0.05)
        self.cvd_atr_expansion_pct_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_atr_expansion_pct_input)
        self.cvd_atr_expansion_pct_input.setToolTip(
            "Volatility expansion filter: ATR at breakout bar must exceed squeeze avg ATR\n"
            "by at least this fraction (0.05 = 5% larger).\n"
            "Prevents signals when the squeeze never releases energy."
        )
        self.cvd_atr_expansion_pct_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("ATR Exp %", self.cvd_atr_expansion_pct_input)

        self.cvd_htf_bars_input = QSpinBox()
        self.cvd_htf_bars_input.setRange(2, 30)
        self.cvd_htf_bars_input.setValue(5)
        self.cvd_htf_bars_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_htf_bars_input)
        self.cvd_htf_bars_input.setToolTip(
            "Higher-timeframe alignment: compare price now vs N bars ago.\n"
            "Long breakout scores 1 pt if price is higher than N bars ago (HTF trend up).\n"
            "5 bars = recent 5-bar slope. Raise to 10–15 for a stronger HTF filter."
        )
        self.cvd_htf_bars_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("HTF Bars", self.cvd_htf_bars_input)

        self.cvd_regime_adx_block_input = QDoubleSpinBox()
        self.cvd_regime_adx_block_input.setRange(0.0, 60.0)
        self.cvd_regime_adx_block_input.setDecimals(1)
        self.cvd_regime_adx_block_input.setSingleStep(1.0)
        self.cvd_regime_adx_block_input.setValue(30.0)
        self.cvd_regime_adx_block_input.setStyleSheet(compact_spinbox_style)
        _w(self.cvd_regime_adx_block_input)
        self.cvd_regime_adx_block_input.setToolTip(
            "Regime block: when ADX exceeds this AND price micro-trend opposes signal,\n"
            "score is reduced by 1 pt (opposing strong trend detected).\n"
            "0 = disabled. 30 = only block in very strong opposing regimes."
        )
        self.cvd_regime_adx_block_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        cvdbk_frm.addRow("Regime Block", self.cvd_regime_adx_block_input)

        # ── CVD Range Breakout — RIGHT COLUMN (info panel) ────────────────
        _PANEL_BG      = "#1A1F2E"
        _PANEL_BORDER  = "#2A3348"
        _HEAD_COLOR    = "#9CCAF4"
        _BODY_COLOR    = "#B8C8D8"
        _ACCENT_LONG   = "#4CAF82"
        _ACCENT_SHORT  = "#E05C5C"
        _ACCENT_EXIT   = "#F0A040"
        _GUIDE_COLOR   = "#A8BCC8"

        def _info_panel_style():
            return f"""
                QWidget#cvdInfoPanel {{
                    background: {_PANEL_BG};
                    border: 1px solid {_PANEL_BORDER};
                    border-radius: 6px;
                }}
            """

        def _section_label(text, color=_HEAD_COLOR):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{color}; font-size:10px; font-weight:700; "
                f"border:none; padding: 2px 0px 1px 0px;"
            )
            return lbl

        def _body_label(text, color=_BODY_COLOR):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{color}; font-size:9px; font-weight:400; border:none;"
            )
            lbl.setWordWrap(True)
            return lbl

        def _divider():
            line = QWidget()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background:{_PANEL_BORDER}; border:none;")
            return line

        cvd_info_panel = QWidget()
        cvd_info_panel.setObjectName("cvdInfoPanel")
        cvd_info_panel.setStyleSheet(_info_panel_style())
        cvd_info_layout = QVBoxLayout(cvd_info_panel)
        cvd_info_layout.setContentsMargins(12, 10, 12, 10)
        cvd_info_layout.setSpacing(5)

        # ── Entry Trigger ──────────────────────────────────────────────────
        cvd_info_layout.addWidget(_section_label("⚡  Entry Triggers"))
        cvd_info_layout.addWidget(_body_label(
            "CVD compresses for ≥ Min Consol bars (range ≤ avg × Max Ratio).",
            _BODY_COLOR
        ))
        entry_rows = [
            (_ACCENT_LONG,  "LONG  — CVD breaks above range high + buffer,"),
            ("",            "         price slope ↑, close > EMA10, CVD EMA ↑"),
            (_ACCENT_SHORT, "SHORT — CVD breaks below range low − buffer,"),
            ("",            "         price slope ↓, close < EMA10, CVD EMA ↓"),
        ]
        for color, text in entry_rows:
            lbl = _body_label(text, color if color else _BODY_COLOR)
            cvd_info_layout.addWidget(lbl)

        cvd_info_layout.addWidget(_body_label(
            "Then each conviction filter scores +1 pt. Signal fires when score ≥ Min Score.",
            _BODY_COLOR
        ))

        cvd_info_layout.addSpacing(4)
        cvd_info_layout.addWidget(_divider())
        cvd_info_layout.addSpacing(4)

        # ── Exit Trigger ───────────────────────────────────────────────────
        cvd_info_layout.addWidget(_section_label("🚪  Exit Triggers", _ACCENT_EXIT))
        exit_rows = [
            "LONG  exit  — price closes below EMA10",
            "SHORT exit  — price closes above EMA10",
            "Regime Trend Exit (if enabled) — ADX + ATR regime flip detected",
            "Stop Loss hit — from Automation stop loss setting",
            "Max Giveback — if Range Breakout giveback is enabled",
        ]
        for text in exit_rows:
            cvd_info_layout.addWidget(_body_label(f"• {text}"))

        cvd_info_layout.addSpacing(4)
        cvd_info_layout.addWidget(_divider())
        cvd_info_layout.addSpacing(4)

        # ── Quick Tuning Guide ─────────────────────────────────────────────
        cvd_info_layout.addWidget(_section_label("🎯  Quick Tuning Guide"))
        guide_entries = [
            ("Getting zero signals?",          "Drop Min Score → 2"),
            ("Too many weak signals?",          "Raise Min Score → 4,  Vol Mult → 1.30"),
            ("Choppy day, want fewer trades?",  "Raise Regime Block → 25"),
            ("Want pure momentum entries?",     "Raise HTF Bars → 10"),
            ("Ghost breakouts (no follow-thru)?","Raise Vol Mult → 1.30,  ATR Exp % → 0.10"),
            ("Signals firing too early?",       "Raise Min Consol bars,  lower Max Ratio"),
            ("Missing big moves?",              "Lower Buffer → 0.05,  Min Score → 2"),
        ]
        for problem, fix in guide_entries:
            row_w = QWidget()
            row_w.setStyleSheet("border:none;")
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 1, 0, 1)
            row_l.setSpacing(6)
            prob_lbl = QLabel(f"• {problem}")
            prob_lbl.setStyleSheet(f"color:{_GUIDE_COLOR}; font-size:9px; font-weight:400; border:none;")
            prob_lbl.setMinimumWidth(175)
            fix_lbl = QLabel(fix)
            fix_lbl.setStyleSheet(f"color:{_ACCENT_LONG}; font-size:9px; font-weight:600; border:none;")
            row_l.addWidget(prob_lbl)
            row_l.addWidget(fix_lbl)
            row_l.addStretch()
            cvd_info_layout.addWidget(row_w)

        cvd_info_layout.addStretch()

        # ── Reset to Defaults button ───────────────────────────────────────
        cvd_info_layout.addWidget(_divider())
        cvd_info_layout.addSpacing(4)

        self.cvd_reset_defaults_btn = QPushButton("↺  Reset to Defaults")
        self.cvd_reset_defaults_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_C_BTN_BG}; color: {_C_BTN_TEXT};
                border: 1px solid {_C_BORDER}; border-radius: 4px;
                padding: 4px 10px; font-size: 10px; font-weight: 600;
                min-height: 22px;
            }}
            QPushButton:hover {{ border: 1px solid {_C_HOVER}; color: #FFFFFF; }}
            QPushButton:pressed {{ background: #1A2030; }}
        """)
        self.cvd_reset_defaults_btn.setToolTip(
            "Reset all CVD Range Breakout parameters to their default values."
        )
        self.cvd_reset_defaults_btn.clicked.connect(self._on_cvd_reset_defaults)
        reset_row = QHBoxLayout()
        reset_row.addStretch()
        reset_row.addWidget(self.cvd_reset_defaults_btn)
        cvd_info_layout.addLayout(reset_row)

        # ══════════════════════════════════════════════════════════════════
        # COLUMN 3 — Simulator · Chart Appearance
        # ══════════════════════════════════════════════════════════════════
        c4 = _col()

        # ── Simulator ─────────────────────────────────────────────────────
        sim_grp, sim_frm = _group("Simulator")
        sim_info = QLabel("Uses same entry/exit rules as live automation.")
        sim_info.setStyleSheet(f"color:{_C_NOTE}; font-size:9px; font-weight:400;")
        sim_frm.addRow(sim_info)
        self.hide_simulator_btn_check = QCheckBox("Hide 'Run Simulator' button")
        self.hide_simulator_btn_check.toggled.connect(self._on_setup_visual_settings_changed)
        sim_frm.addRow(self.hide_simulator_btn_check)
        self.hide_tick_backtest_controls_check = QCheckBox("Hide tick backtest controls")
        self.hide_tick_backtest_controls_check.setToolTip(
            "Hide 'Upload Tick CSV' and 'Use Live' buttons from top bar.\n"
            "Live data feed remains active by default."
        )
        self.hide_tick_backtest_controls_check.toggled.connect(self._on_setup_visual_settings_changed)
        sim_frm.addRow(self.hide_tick_backtest_controls_check)
        c4.addWidget(sim_grp)

        # ── Chart Appearance ──────────────────────────────────────────────
        app_grp, app_frm = _group("Chart Appearance")

        # Line width / opacity
        for attr, label, lo, hi, step, default in (
            ("chart_line_width_input",      "CVD Line W",   0.5, 8.0, 0.1, self._chart_line_width),
            ("chart_line_opacity_input",    "CVD Opacity",  0.1, 1.0, 0.05, self._chart_line_opacity),
            ("confluence_line_width_input", "Conf Line W",  0.5, 8.0, 0.1, self._confluence_line_width),
            ("confluence_line_opacity_input","Conf Opacity", 0.1, 1.0, 0.05, self._confluence_line_opacity),
            ("ema_line_opacity_input",      "EMA Opacity",  0.1, 1.0, 0.05, self._ema_line_opacity),
        ):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(2 if step < 0.1 else 1)
            sp.setSingleStep(step)
            sp.setValue(default)
            sp.setStyleSheet(compact_spinbox_style)
            _w(sp)
            sp.valueChanged.connect(self._on_setup_visual_settings_changed)
            setattr(self, attr, sp)
            app_frm.addRow(label, sp)

        # Color buttons
        self.chart_line_color_btn = QPushButton("CVD Line")
        self.chart_line_color_btn.setStyleSheet(_color_btn_style)
        self.chart_line_color_btn.clicked.connect(
            lambda: self._pick_color("chart_line_color_btn", "_chart_line_color"))

        self.price_line_color_btn = QPushButton("Price Line")
        self.price_line_color_btn.setStyleSheet(_color_btn_style)
        self.price_line_color_btn.clicked.connect(
            lambda: self._pick_color("price_line_color_btn", "_price_line_color"))

        clr_row1 = QHBoxLayout()
        clr_row1.setSpacing(4)
        clr_row1.addWidget(self.chart_line_color_btn)
        clr_row1.addWidget(self.price_line_color_btn)
        app_frm.addRow("Colors", clr_row1)

        self.confluence_short_color_btn = QPushButton("Short")
        self.confluence_short_color_btn.setStyleSheet(_color_btn_style)
        self.confluence_short_color_btn.clicked.connect(
            lambda: self._pick_color("confluence_short_color_btn", "_confluence_short_color"))

        self.confluence_long_color_btn = QPushButton("Long")
        self.confluence_long_color_btn.setStyleSheet(_color_btn_style)
        self.confluence_long_color_btn.clicked.connect(
            lambda: self._pick_color("confluence_long_color_btn", "_confluence_long_color"))

        clr_row2 = QHBoxLayout()
        clr_row2.setSpacing(4)
        clr_row2.addWidget(self.confluence_short_color_btn)
        clr_row2.addWidget(self.confluence_long_color_btn)
        app_frm.addRow("Conf Colors", clr_row2)

        # EMA defaults
        ema_row = QHBoxLayout()
        ema_row.setSpacing(5)
        self.setup_ema_default_checks = {}
        for period in (10, 21, 51):
            cb = QCheckBox(str(period))
            cb.setChecked(period == 51)
            cb.toggled.connect(self._on_setup_visual_settings_changed)
            self.setup_ema_default_checks[period] = cb
            ema_row.addWidget(cb)
        ema_row.addStretch()
        app_frm.addRow("Default EMAs", ema_row)

        self.show_grid_lines_check = QCheckBox("Show grid lines")
        self.show_grid_lines_check.setChecked(True)
        self.show_grid_lines_check.toggled.connect(self._on_setup_visual_settings_changed)
        app_frm.addRow("Grid", self.show_grid_lines_check)

        self.show_trend_change_markers_check = QCheckBox("Show trend change markers")
        self.show_trend_change_markers_check.setChecked(False)
        self.show_trend_change_markers_check.setToolTip(
            "Mark every regime transition (trend/vol change) with a vertical line.\n"
            "Shows: Trend, Volatility, Session, ADX, Vol ratio. Requires Regime Engine enabled."
        )
        self.show_trend_change_markers_check.toggled.connect(self._on_trend_change_markers_toggled)
        app_frm.addRow("Trend Change", self.show_trend_change_markers_check)

        # Background images
        def _bg_row(target, label_attr, upload_attr, clear_attr):
            row = QHBoxLayout()
            row.setSpacing(4)
            lbl = QLabel("No image selected")
            lbl.setStyleSheet(f"color:{_C_NOTE}; font-size:9px;")
            setattr(self, label_attr, lbl)
            btn_up = QPushButton("Upload")
            btn_up.setStyleSheet(_color_btn_style)
            btn_up.clicked.connect(lambda: self._on_pick_background_image(target))
            setattr(self, upload_attr, btn_up)
            btn_cl = QPushButton("Clear")
            btn_cl.setStyleSheet(_color_btn_style)
            btn_cl.clicked.connect(lambda: self._on_clear_background_image(target))
            setattr(self, clear_attr, btn_cl)
            row.addWidget(lbl, 1)
            row.addWidget(btn_up)
            row.addWidget(btn_cl)
            return row

        app_frm.addRow("Window BG", _bg_row(
            "window", "window_bg_image_label", "window_bg_upload_btn", "window_bg_clear_btn"))
        app_frm.addRow("Chart BG", _bg_row(
            "chart", "chart_bg_image_label", "chart_bg_upload_btn", "chart_bg_clear_btn"))

        c4.addWidget(app_grp)
        c4.addStretch()

        # ══════════════════════════════════════════════════════════════════
        # CPR
        # ══════════════════════════════════════════════════════════════════
        cpr_col = _col()
        cpr_grp, cpr_frm = _group("Central Pivot Range (CPR)")
        cpr_frm.addRow(_note("CPR for each session is calculated using previous-day OHLC."))

        self.show_cpr_lines_check = QCheckBox("Show CPR lines on price chart")
        self.show_cpr_lines_check.setChecked(True)
        self.show_cpr_lines_check.toggled.connect(self._on_cpr_settings_changed)
        cpr_frm.addRow("Lines", self.show_cpr_lines_check)

        self.show_cpr_labels_check = QCheckBox("Show CPR labels on price chart")
        self.show_cpr_labels_check.setChecked(True)
        self.show_cpr_labels_check.toggled.connect(self._on_cpr_settings_changed)
        cpr_frm.addRow("Labels", self.show_cpr_labels_check)

        self.cpr_narrow_threshold_input = QDoubleSpinBox()
        self.cpr_narrow_threshold_input.setRange(0.1, 10000.0)
        self.cpr_narrow_threshold_input.setDecimals(2)
        self.cpr_narrow_threshold_input.setSingleStep(1.0)
        self.cpr_narrow_threshold_input.setValue(150.0)
        self.cpr_narrow_threshold_input.setStyleSheet(compact_spinbox_style)
        _w(self.cpr_narrow_threshold_input)
        self.cpr_narrow_threshold_input.setToolTip(
            "If CPR width (TC - BC) is < this value, it is marked as Narrow CPR."
        )
        self.cpr_narrow_threshold_input.valueChanged.connect(self._on_cpr_settings_changed)
        cpr_frm.addRow("Narrow Width", self.cpr_narrow_threshold_input)

        self.cpr_wide_threshold_input = QDoubleSpinBox()
        self.cpr_wide_threshold_input.setRange(0.1, 10000.0)
        self.cpr_wide_threshold_input.setDecimals(2)
        self.cpr_wide_threshold_input.setSingleStep(1.0)
        self.cpr_wide_threshold_input.setValue(200.0)
        self.cpr_wide_threshold_input.setStyleSheet(compact_spinbox_style)
        _w(self.cpr_wide_threshold_input)
        self.cpr_wide_threshold_input.setToolTip(
            "If CPR width (TC - BC) is > this value, it is marked as Wide CPR."
        )
        self.cpr_wide_threshold_input.valueChanged.connect(self._on_cpr_settings_changed)
        cpr_frm.addRow("Wide Width", self.cpr_wide_threshold_input)
        cpr_col.addWidget(cpr_grp)
        cpr_col.addStretch()

        # ══════════════════════════════════════════════════════════════════
        # CPR Strategy Priorities
        # ══════════════════════════════════════════════════════════════════
        priority_panel = QWidget()
        priority_layout = QVBoxLayout(priority_panel)
        priority_layout.setContentsMargins(0, 0, 0, 0)
        priority_layout.setSpacing(6)
        priority_layout.addWidget(_note("Lower number = higher strategy priority (1 is highest). Auto trader uses Narrow/Neutral/Wide list based on CPR width, and Fallback when CPR is unavailable."))

        columns_grid = QGridLayout()
        columns_grid.setHorizontalSpacing(_COL_SPACING)
        columns_grid.setVerticalSpacing(_GRP_SPACING)
        self.cpr_priority_inputs = {}

        for col_idx, list_key in enumerate(("narrow", "neutral", "wide", "fallback")):
            grp, frm = _group(f"{self.CPR_PRIORITY_LIST_LABELS.get(list_key, list_key.title())} Priority")
            for strategy_key in self.STRATEGY_PRIORITY_KEYS:
                spin = QSpinBox()
                spin.setRange(0, 99)
                spin.setStyleSheet(compact_spinbox_style)
                _w(spin)
                spin.valueChanged.connect(self._on_cpr_priorities_changed)
                frm.addRow(self.STRATEGY_PRIORITY_LABELS.get(strategy_key, strategy_key), spin)
                self.cpr_priority_inputs[(list_key, strategy_key)] = spin
            columns_grid.addWidget(grp, 0, col_idx)

        priority_layout.addLayout(columns_grid, 1)

        # ══════════════════════════════════════════════════════════════════
        # Assemble (Tabbed)
        # ══════════════════════════════════════════════════════════════════
        tabs = QTabWidget(self.setup_dialog)
        tabs.setDocumentMode(True)

        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_layout.setContentsMargins(0, 12, 0, 0)
        general_layout.setSpacing(0)

        cols_row = QHBoxLayout()
        cols_row.setSpacing(_COL_SPACING)
        for col in (c1, c2, c4):
            cols_row.addLayout(col, 1)
        general_layout.addLayout(cols_row, 1)

        priority_tab = QWidget()
        priority_layout_root = QHBoxLayout(priority_tab)
        priority_layout_root.setContentsMargins(6, 12, 6, 6)
        priority_layout_root.setSpacing(_COL_SPACING)
        priority_layout_root.addLayout(cpr_col, 1)
        priority_layout_root.addWidget(priority_panel, 3)

        # ══════════════════════════════════════════════════════════════════
        # SHARED INFO PANEL FACTORY
        # Reuses the same visual language as the CVD Range Breakout panel.
        # ══════════════════════════════════════════════════════════════════
        _PANEL_BG_S      = "#1A1F2E"
        _PANEL_BORDER_S  = "#2A3348"
        _HEAD_COLOR_S    = "#9CCAF4"
        _BODY_COLOR_S    = "#B8C8D8"
        _ACCENT_LONG_S   = "#4CAF82"
        _ACCENT_SHORT_S  = "#E05C5C"
        _ACCENT_EXIT_S   = "#F0A040"
        _GUIDE_COLOR_S   = "#A8BCC8"
        _ACCENT_NOTE_S   = "#C8A8E0"

        def _make_info_panel() -> tuple:
            """Return (panel_widget, layout) pre-styled."""
            panel = QWidget()
            panel.setObjectName("stratInfoPanel")
            panel.setStyleSheet(f"""
                QWidget#stratInfoPanel {{
                    background: {_PANEL_BG_S};
                    border: 1px solid {_PANEL_BORDER_S};
                    border-radius: 6px;
                }}
            """)
            lay = QVBoxLayout(panel)
            lay.setContentsMargins(12, 10, 12, 10)
            lay.setSpacing(5)
            return panel, lay

        def _sh(text, color=_HEAD_COLOR_S):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{color}; font-size:10px; font-weight:700; border:none; padding:2px 0 1px 0;")
            return lbl

        def _sb(text, color=_BODY_COLOR_S):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{color}; font-size:9px; font-weight:400; border:none;")
            lbl.setWordWrap(True)
            return lbl

        def _sdiv():
            line = QWidget()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background:{_PANEL_BORDER_S}; border:none;")
            return line

        def _sguide(entries: list[tuple[str, str]], lay: QVBoxLayout):
            for problem, fix in entries:
                rw = QWidget(); rw.setStyleSheet("border:none;")
                rl = QHBoxLayout(rw); rl.setContentsMargins(0,1,0,1); rl.setSpacing(6)
                pl = QLabel(f"• {problem}")
                pl.setStyleSheet(f"color:{_GUIDE_COLOR_S}; font-size:9px; font-weight:400; border:none;")
                pl.setMinimumWidth(200)
                fl = QLabel(fix)
                fl.setStyleSheet(f"color:{_ACCENT_LONG_S}; font-size:9px; font-weight:600; border:none;")
                rl.addWidget(pl); rl.addWidget(fl); rl.addStretch()
                lay.addWidget(rw)

        def _two_col_tab(left_widgets: list, info_panel: QWidget) -> QWidget:
            tab = QWidget()
            tl = QHBoxLayout(tab)
            tl.setContentsMargins(6, 12, 6, 6)
            tl.setSpacing(10)
            left = QVBoxLayout(); left.setSpacing(_GRP_SPACING)
            for w in left_widgets:
                left.addWidget(w)
            left.addStretch()
            right = QVBoxLayout(); right.setSpacing(0)
            right.addWidget(info_panel)
            tl.addLayout(left, 0)
            tl.addLayout(right, 1)
            return tab

        # ── ATR Reversal info panel ────────────────────────────────────────
        atr_rev_panel, atr_rev_lay = _make_info_panel()

        atr_rev_lay.addWidget(_sh("⚡  Entry Triggers"))
        atr_rev_lay.addWidget(_sb(
            "Confluence of ATR reversal in both Price AND CVD simultaneously. "
            "CVD confirmation is valid within a 5-bar rolling window.", _BODY_COLOR_S))
        for color, text in [
            (_ACCENT_LONG_S,  "LONG  — Price ATR reversal below EMA  +  CVD ATR reversal below EMA51"),
            (_ACCENT_SHORT_S, "SHORT — Price ATR reversal above EMA  +  CVD ATR reversal above EMA51"),
        ]:
            atr_rev_lay.addWidget(_sb(text, color))

        atr_rev_lay.addWidget(_sb(
            "Volatility gate: normalized ATR must be > 1.10× baseline AND ATR "
            "velocity must be flat/contracting — OR both price & CVD fire on the exact same bar.", _ACCENT_NOTE_S))

        atr_rev_lay.addSpacing(4); atr_rev_lay.addWidget(_sdiv()); atr_rev_lay.addSpacing(4)
        atr_rev_lay.addWidget(_sh("🚪  Exit Triggers", _ACCENT_EXIT_S))
        for txt in [
            "LONG  exit — Stop Loss hit  |  Max Giveback triggered  |  Trend Exit if enabled",
            "SHORT exit — Stop Loss hit  |  Max Giveback triggered  |  Trend Exit if enabled",
            "Breakout active: opposing ATR reversal signals suppressed (Adaptive mode)",
        ]:
            atr_rev_lay.addWidget(_sb(f"• {txt}"))

        atr_rev_lay.addSpacing(4); atr_rev_lay.addWidget(_sdiv()); atr_rev_lay.addSpacing(4)
        atr_rev_lay.addWidget(_sh("🎯  Quick Tuning Guide"))
        _sguide([
            ("Getting zero signals?",              "Check ATR Ext Min — lower to 1.05 or 0"),
            ("Too many bad reversal signals?",     "Raise ATR Ext Min → 1.20,  ATR Flat Vel% → 0.03"),
            ("Signals fire too early (not peaked)?","Raise ATR Flat Vel% → 0.05"),
            ("ATR firing during breakout trends?", "Set Breakout vs ATR → Keep Breakout"),
            ("Missing big mean-reversion moves?",  "Lower ATR Base EMA,  raise CVD Z-Score Min"),
            ("Signals cancelled by regime block?", "Check chop filter — disable ATR Reversal filter"),
        ], atr_rev_lay)
        atr_rev_lay.addStretch()

        # ── ATR Divergence info panel ──────────────────────────────────────
        atr_div_panel, atr_div_lay = _make_info_panel()

        atr_div_lay.addWidget(_sh("⚡  Entry Triggers"))
        atr_div_lay.addWidget(_sb(
            "Price makes an ATR reversal while CVD is ALREADY trending strongly "
            "in the trade direction — no reversal in CVD expected.", _BODY_COLOR_S))
        for color, text in [
            (_ACCENT_LONG_S,  "LONG  — Price ATR reversal below EMA  +  CVD above BOTH EMA10 & EMA51  +  CVD slope up"),
            (_ACCENT_SHORT_S, "SHORT — Price ATR reversal above EMA  +  CVD below BOTH EMA10 & EMA51  +  CVD slope down"),
        ]:
            atr_div_lay.addWidget(_sb(text, color))
        atr_div_lay.addWidget(_sb(
            "Key difference from ATR Reversal: CVD is not reversing — it is in a "
            "persistent trend. Price dips/pops are counter-trend moves to fade.", _ACCENT_NOTE_S))
        atr_div_lay.addWidget(_sb(
            "EMA Cross signals are excluded to avoid double-firing on the same bar.", _BODY_COLOR_S))

        atr_div_lay.addSpacing(4); atr_div_lay.addWidget(_sdiv()); atr_div_lay.addSpacing(4)
        atr_div_lay.addWidget(_sh("🚪  Exit Triggers", _ACCENT_EXIT_S))
        for txt in [
            "LONG  exit — Stop Loss hit  |  Max Giveback  |  Trend Exit if enabled",
            "SHORT exit — Stop Loss hit  |  Max Giveback  |  Trend Exit if enabled",
            "CVD slope flip (if Trend Exit enabled) — divergence assumption invalidated",
        ]:
            atr_div_lay.addWidget(_sb(f"• {txt}"))

        atr_div_lay.addSpacing(4); atr_div_lay.addWidget(_sdiv()); atr_div_lay.addSpacing(4)
        atr_div_lay.addWidget(_sh("🎯  Quick Tuning Guide"))
        _sguide([
            ("Getting zero signals?",               "CVD Z-Score Min too high — lower to 0.3"),
            ("Too many signals in sideways market?", "Enable Chop Filter for ATR Divergence"),
            ("Signals in wrong direction?",          "CVD may be oscillating — raise CVD Z-Score Min"),
            ("Want only strongest trend fades?",     "Raise ATR Ext Min → 1.25"),
            ("Regime blocks too aggressive?",        "Disable chop filter for this strategy"),
            ("Missing entries in strong trends?",    "Lower ATR Flat Vel% → 0.01"),
        ], atr_div_lay)
        atr_div_lay.addStretch()

        # ── EMA Cross info panel ───────────────────────────────────────────
        ema_cross_panel, ema_cross_panel_lay = _make_info_panel()

        ema_cross_panel_lay.addWidget(_sh("⚡  Entry Triggers"))
        ema_cross_panel_lay.addWidget(_sb(
            "Momentum continuation: price is already committed to a side, and CVD "
            "crossing EMA51 confirms institutional order flow is aligned.", _BODY_COLOR_S))
        for color, text in [
            (_ACCENT_LONG_S,  "LONG  — Price > EMA10 & EMA51  +  CVD > EMA10  +  CVD crosses ABOVE EMA51  +  price & CVD slope up"),
            (_ACCENT_SHORT_S, "SHORT — Price < EMA10 & EMA51  +  CVD < EMA10  +  CVD crosses BELOW EMA51  +  price & CVD slope down"),
        ]:
            ema_cross_panel_lay.addWidget(_sb(text, color))
        ema_cross_panel_lay.addWidget(_sb(
            "Anti-hug filter: CVD must be meaningfully away from EMA51 (gap > CVD EMA Gap × 0.5) "
            "to avoid false crosses in flat/hugging CVD.", _ACCENT_NOTE_S))
        ema_cross_panel_lay.addWidget(_sb(
            "Parent Trend (optional): 5-minute EMA10 must be above/below EMA51 "
            "with both slopes confirming — acts as a higher-timeframe gate.", _BODY_COLOR_S))

        ema_cross_panel_lay.addSpacing(4); ema_cross_panel_lay.addWidget(_sdiv()); ema_cross_panel_lay.addSpacing(4)
        ema_cross_panel_lay.addWidget(_sh("🚪  Exit Triggers", _ACCENT_EXIT_S))
        for txt in [
            "LONG  exit — Price closes below EMA10  OR  CVD crosses below EMA10",
            "SHORT exit — Price closes above EMA10  OR  CVD crosses above EMA10",
            "Stop Loss hit  |  Max Giveback  |  Trend Exit if enabled",
        ]:
            ema_cross_panel_lay.addWidget(_sb(f"• {txt}"))

        ema_cross_panel_lay.addSpacing(4); ema_cross_panel_lay.addWidget(_sdiv()); ema_cross_panel_lay.addSpacing(4)
        ema_cross_panel_lay.addWidget(_sh("🎯  Quick Tuning Guide"))
        _sguide([
            ("Too many false crosses in chop?",     "Raise CVD EMA Gap threshold → 2.0+"),
            ("Missing signals in strong trends?",   "Lower CVD EMA Gap → 0.3,  disable Parent Trend"),
            ("Parent Trend blocking valid signals?","Disable 'Require 5m Parent Trend'"),
            ("Signals too late (momentum faded)?",  "Lower CVD EMA Gap so crosses fire earlier"),
            ("Want higher-quality trend rides only?","Keep Parent Trend ON,  raise CVD EMA Gap"),
            ("CVD hugging EMA51 giving phantom signals?", "Raise CVD EMA Gap → 1.5"),
        ], ema_cross_panel_lay)
        ema_cross_panel_lay.addStretch()

        # ── Range Breakout info panel ──────────────────────────────────────
        brk_panel, brk_panel_lay = _make_info_panel()

        brk_panel_lay.addWidget(_sh("⚡  Entry Triggers"))
        brk_panel_lay.addWidget(_sb(
            "Price consolidates within a rolling range window, then breaks out with "
            "volume and CVD confirmation. Squeeze tightness adjusts breakout threshold dynamically.", _BODY_COLOR_S))
        for color, text in [
            (_ACCENT_LONG_S,  "LONG  — Price closes ABOVE range high  +  CVD bullish or rising  +  volume ≥ avg × 1.05  +  breakout strength ≥ threshold"),
            (_ACCENT_SHORT_S, "SHORT — Price closes BELOW range low   +  CVD bearish or falling +  volume ≥ avg × 1.05  +  breakout strength ≥ threshold"),
        ]:
            brk_panel_lay.addWidget(_sb(text, color))
        brk_panel_lay.addWidget(_sb(
            "Min Consolidation: if set, price must have been range-bound for at least N minutes "
            "before breakout fires — prevents chasing extended moves.", _ACCENT_NOTE_S))
        brk_panel_lay.addWidget(_sb(
            "ATR Trail Base: trailing stop expands with volatility (current ATR / entry ATR). "
            "Fast breakouts widen automatically so you stay in.", _BODY_COLOR_S))

        brk_panel_lay.addSpacing(4); brk_panel_lay.addWidget(_sdiv()); brk_panel_lay.addSpacing(4)
        brk_panel_lay.addWidget(_sh("🚪  Exit Triggers", _ACCENT_EXIT_S))
        for txt in [
            "LONG  exit — Price closes below EMA10",
            "SHORT exit — Price closes above EMA10",
            "ATR Trailing Stop hit (ATR Trail Base, scales with volatility)",
            "Stop Loss hit  |  Max Giveback  |  Trend Exit if enabled",
            "Breakout vs ATR mode: 'Keep Breakout' suppresses opposing ATR reversals",
        ]:
            brk_panel_lay.addWidget(_sb(f"• {txt}"))

        brk_panel_lay.addSpacing(4); brk_panel_lay.addWidget(_sdiv()); brk_panel_lay.addSpacing(4)
        brk_panel_lay.addWidget(_sh("🎯  Quick Tuning Guide"))
        _sguide([
            ("No signals firing?",                  "Lower Range Lookback → 10,  check volume data"),
            ("Too many false breakouts?",            "Enable Min Consol → 15 min,  Max ADX → 20"),
            ("Breakout reverses immediately?",       "Raise ATR Trail Base → 15,  add Min Consol"),
            ("ATR reversals killing breakout ride?", "Set Breakout vs ATR → Keep Breakout"),
            ("Want only strong squeeze breakouts?",  "Set Min Consol → 20,  Max ADX → 18"),
            ("Missing moves after ATR signals?",     "Raise ATR Skip Limit → 2–3"),
        ], brk_panel_lay)
        brk_panel_lay.addStretch()

        # ── Open Drive info panel ──────────────────────────────────────────
        od_panel, od_panel_lay = _make_info_panel()

        od_panel_lay.addWidget(_sh("⚡  Entry Triggers"))
        od_panel_lay.addWidget(_sb(
            "Fires ONCE per session, at the exact configured time (default 09:17). "
            "Missed window = no signal for that day. Never chases later bars.", _BODY_COLOR_S))
        for color, text in [
            (_ACCENT_LONG_S,  "LONG  — Price > EMA10 AND Price > EMA51  +  CVD > CVD EMA10"),
            (_ACCENT_SHORT_S, "SHORT — Price < EMA10 AND Price < EMA51  +  CVD < CVD EMA10"),
        ]:
            od_panel_lay.addWidget(_sb(text, color))
        od_panel_lay.addWidget(_sb(
            "Price must be cleanly above or below BOTH EMAs — EMA ordering is not required. "
            "This captures clear directional alignment early in the session.", _ACCENT_NOTE_S))
        od_panel_lay.addWidget(_sb(
            "VWAP is monitored but not a hard gate — use as additional confluence context.", _BODY_COLOR_S))

        od_panel_lay.addSpacing(4); od_panel_lay.addWidget(_sdiv()); od_panel_lay.addSpacing(4)
        od_panel_lay.addWidget(_sh("🚪  Exit Triggers", _ACCENT_EXIT_S))
        for txt in [
            "OD Tick Drawdown — exits if adverse move from entry exceeds limit (live ticks)",
            "OD Giveback — per-trade max profit giveback, overrides global setting",
            "Stop Loss hit (global Automation stop loss)",
            "Stacker continuation if 'Stack' is enabled — pyramid the open drive move",
            "No time-based exit — holds until stop/giveback/manual close",
        ]:
            od_panel_lay.addWidget(_sb(f"• {txt}"))

        od_panel_lay.addSpacing(4); od_panel_lay.addWidget(_sdiv()); od_panel_lay.addSpacing(4)
        od_panel_lay.addWidget(_sh("🎯  Quick Tuning Guide"))
        _sguide([
            ("Signal never fires?",                 "Check Entry Time matches your session open"),
            ("Firing on wrong direction days?",      "Lower OD Tick DD → 50 pts for quick exit"),
            ("Getting stopped out too early?",       "Raise OD Tick DD → 150,  lower Stop Loss"),
            ("Want to pyramid the open drive?",      "Enable Stack,  set Stacker step → 15–20 pts"),
            ("OD runs far but no stacking?",         "Enable Stack in OD tab + Stacker in General"),
            ("OD giveback too generous?",            "Set OD Giveback → 30–50 pts"),
        ], od_panel_lay)
        od_panel_lay.addStretch()

        # ══════════════════════════════════════════════════════════════════
        # ASSEMBLE TABS
        # ══════════════════════════════════════════════════════════════════
        def _strategy_tab(*widgets: QWidget):
            tab = QWidget()
            lay = QVBoxLayout(tab)
            lay.setContentsMargins(6, 12, 6, 6)
            lay.setSpacing(_GRP_SPACING)
            for widget in widgets:
                lay.addWidget(widget)
            lay.addStretch()
            return tab

        tabs.addTab(general_tab, "General")

        # ATR Reversal — knobs (sig_grp) left, info panel right
        tabs.addTab(
            _two_col_tab([sig_grp], atr_rev_panel),
            self.STRATEGY_PRIORITY_LABELS["atr_reversal"],
        )

        # ATR Divergence — no dedicated knobs yet, info panel full-width
        atr_div_tab = QWidget()
        atr_div_lay_root = QHBoxLayout(atr_div_tab)
        atr_div_lay_root.setContentsMargins(6, 12, 6, 6)
        atr_div_lay_root.setSpacing(0)
        atr_div_lay_root.addWidget(atr_div_panel)
        tabs.addTab(atr_div_tab, self.STRATEGY_PRIORITY_LABELS["atr_divergence"])

        # EMA Cross — knobs left, info panel right
        ema_cross_grp, ema_cross_frm = _group("EMA & CVD Cross")
        _w(self.cvd_ema_gap_input)
        ema_cross_frm.addRow(_note("Configure EMA+CVD cross confluence behavior."))
        ema_cross_frm.addRow("CVD EMA Gap", self.cvd_ema_gap_input)
        ema_cross_frm.addRow("Parent Trend", self.ema_cross_use_parent_mask_check)
        tabs.addTab(
            _two_col_tab([ema_cross_grp], ema_cross_panel),
            self.STRATEGY_PRIORITY_LABELS["ema_cross"],
        )

        # CVD Range Breakout — consol_grp removed; it belongs to Range Breakout tab
        cvd_tab = QWidget()
        cvd_tab_lay = QHBoxLayout(cvd_tab)
        cvd_tab_lay.setContentsMargins(6, 12, 6, 6)
        cvd_tab_lay.setSpacing(10)
        cvd_left = QVBoxLayout()
        cvd_left.setSpacing(_GRP_SPACING)
        cvd_left.addWidget(cvdbk_grp)
        cvd_left.addStretch()
        cvd_right = QVBoxLayout()
        cvd_right.setSpacing(0)
        cvd_right.addWidget(cvd_info_panel)
        cvd_tab_lay.addLayout(cvd_left, 0)
        cvd_tab_lay.addLayout(cvd_right, 1)
        tabs.addTab(cvd_tab, self.STRATEGY_PRIORITY_LABELS["cvd_range_breakout"])

        # Range Breakout — brk_grp + consol_grp (pre-squeeze gate) left, info panel right
        tabs.addTab(
            _two_col_tab([brk_grp, consol_grp], brk_panel),
            self.STRATEGY_PRIORITY_LABELS["range_breakout"],
        )

        # Open Drive — knobs left, info panel right
        tabs.addTab(
            _two_col_tab([od_grp], od_panel),
            self.STRATEGY_PRIORITY_LABELS["open_drive"],
        )
        self._build_regime_tab(tabs, compact_spinbox_style, compact_combo_style)

        # ── Hybrid Exit Tab ───────────────────────────────────────────────
        hybrid_tab = QWidget()
        hybrid_root = QVBoxLayout(hybrid_tab)
        hybrid_root.setContentsMargins(6, 12, 6, 6)
        hybrid_root.setSpacing(_GRP_SPACING)

        # ── Enable toggle ─────────────────────────────────────────────────
        enable_grp, enable_frm = _group("Phase Exit Engine")
        enable_frm.addRow(_note(
            "Replaces flat giveback with a 3-phase momentum state machine.\n"
            "EARLY → EXPANSION (ride full premium spike) → DISTRIBUTION (convex trail).\n"
            "Built for options scalping — premium is non-linear with momentum."
        ))
        enable_frm.addRow(_note("Exit Mode is available on the Auto Trader toolbar next to Harvest."))
        hybrid_root.addWidget(enable_grp)

        # ── Unlock thresholds ─────────────────────────────────────────────
        unlock_grp, unlock_frm = _group("EXPANSION Unlock  (EARLY → EXPANSION)")
        _w(self.hybrid_adx_unlock_input)
        _w(self.hybrid_atr_ratio_input)
        _w(self.hybrid_adx_rising_input)
        unlock_frm.addRow(_note(
            "All three must be met to transition from EARLY (hard stop only)\n"
            "into EXPANSION (ride the premium, no trailing)."
        ))
        unlock_frm.addRow("ADX Unlock Floor",    self.hybrid_adx_unlock_input)
        unlock_frm.addRow("ATR Ratio Min",        self.hybrid_atr_ratio_input)
        unlock_frm.addRow("ADX Rising Bars",      self.hybrid_adx_rising_input)
        hybrid_root.addWidget(unlock_grp)

        # ── Distribution triggers ─────────────────────────────────────────
        dist_grp, dist_frm = _group("DISTRIBUTION Triggers  (EXPANSION → DISTRIBUTION)")
        _w(self.hybrid_vel_thresh_input)
        _w(self.hybrid_vel_collapse_input)
        _w(self.hybrid_ext_mult_input)
        dist_frm.addRow(_note(
            "Any ONE trigger fires → enter DISTRIBUTION (convex giveback activates).\n"
            "Triggers: ADX slope turns negative · Vol acceleration collapses\n"
            "· Velocity impulse halves · Extreme extension from EMA51."
        ))
        dist_frm.addRow("Velocity Threshold",   self.hybrid_vel_thresh_input)
        dist_frm.addRow("Velocity Collapse ×",  self.hybrid_vel_collapse_input)
        dist_frm.addRow("Extension (× ATR)",    self.hybrid_ext_mult_input)
        hybrid_root.addWidget(dist_grp)

        # ── Giveback formula ──────────────────────────────────────────────
        gb_grp, gb_frm = _group("Convex Giveback  (DISTRIBUTION phase)")
        _w(self.hybrid_profit_ratio_input)
        _w(self.hybrid_atr_giveback_input)
        _w(self.hybrid_base_pct_input)
        gb_frm.addRow(_note(
            "giveback = max(base_floor, profit_ratio × peak_profit, atr_mult × ATR)\n"
            "Exit when pullback from peak ≥ giveback threshold."
        ))
        gb_frm.addRow("Profit Giveback %",  self.hybrid_profit_ratio_input)
        gb_frm.addRow("ATR Giveback ×",     self.hybrid_atr_giveback_input)
        gb_frm.addRow("Base Floor %",       self.hybrid_base_pct_input)
        hybrid_root.addWidget(gb_grp)

        # ── Structural breakdown ──────────────────────────────────────────
        bd_grp, bd_frm = _group("Structural Breakdown  (Immediate Exit Override)")
        _w(self.hybrid_breakdown_lb_input)
        _w(self.hybrid_atr_bdown_input)
        bd_frm.addRow(_note(
            "Overrides giveback wait with immediate exit on late-stage trend death.\n"
            "Fires when ADX falls below its own N-bar low AND ATR contracts."
        ))
        bd_frm.addRow("ADX Lookback Bars",  self.hybrid_breakdown_lb_input)
        bd_frm.addRow("ATR Contract Ratio", self.hybrid_atr_bdown_input)
        bd_frm.addRow("",                  self.hybrid_ema_bdown_check)
        hybrid_root.addWidget(bd_grp)

        hybrid_root.addStretch()
        tabs.addTab(hybrid_tab, "Hybrid Exit")
        tabs.addTab(priority_tab, "Priority Order")
        root.addWidget(tabs, 1)

        # ── Close bar ─────────────────────────────────────────────────────
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.setup_dialog.hide)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)



    def _on_cvd_reset_defaults(self):
        """Reset all CVD Range Breakout parameters to their default values."""
        _CVD_DEFAULTS = {
            "cvd_range_lookback_input":      30,
            "cvd_breakout_buffer_input":     0.10,
            "cvd_min_consol_bars_input":     15,
            "cvd_max_range_ratio_input":     0.80,
            "cvd_breakout_min_adx_input":    15.0,
            "cvd_conviction_score_input":    3,
            "cvd_vol_expansion_mult_input":  1.15,
            "cvd_atr_expansion_pct_input":   0.05,
            "cvd_htf_bars_input":            5,
            "cvd_regime_adx_block_input":    30.0,
            "breakout_min_consol_input":     0,
            "breakout_min_consol_adx_input": 0.0,
        }
        for attr, default in _CVD_DEFAULTS.items():
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.blockSignals(True)
                widget.setValue(default)
                widget.blockSignals(False)
        # Fire one consolidated update after all resets
        self._on_chop_filter_settings_changed()

    def _open_setup_dialog(self):
        self.setup_dialog.show()
        self.setup_dialog.raise_()
        self.setup_dialog.activateWindow()



    def _set_color_button(self, btn: QPushButton, color_hex: str):
        btn.setText(color_hex.upper())
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {color_hex}; color: #111; font-weight: 700;
                border: 1px solid {_C_BORDER}; border-radius: 4px; padding: 3px 7px;
            }}
        """)



    def _pick_color(self, button_attr: str, color_attr: str):
        current = getattr(self, color_attr, "#26A69A")
        picked = QColorDialog.getColor(QColor(current), self, "Select Color")
        if not picked.isValid():
            return
        new_color = picked.name()
        setattr(self, color_attr, new_color)
        self._set_color_button(getattr(self, button_attr), new_color)
        self._apply_visual_settings()
        self._persist_setup_values()



    def _on_pick_background_image(self, target: str):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Background Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not file_path:
            return
        if target == "window":
            self._window_bg_image_path = file_path
        elif target == "chart":
            self._chart_bg_image_path = file_path
        else:
            return
        self._update_bg_image_labels()
        self._apply_background_image()
        self._persist_setup_values()



    def _on_clear_background_image(self, target: str):
        if target == "window":
            self._window_bg_image_path = ""
        elif target == "chart":
            self._chart_bg_image_path = ""
        else:
            return
        self._update_bg_image_labels()
        self._apply_background_image()
        self._persist_setup_values()



    def _update_bg_image_labels(self):
        window_name = self._window_bg_image_path.split('/')[-1] if self._window_bg_image_path else "No image selected"
        chart_name  = self._chart_bg_image_path.split('/')[-1]  if self._chart_bg_image_path  else "No image selected"
        self.window_bg_image_label.setText(window_name)
        self.chart_bg_image_label.setText(chart_name)



    def _apply_background_image(self):
        window_image_path = self._window_bg_image_path
        chart_image_path  = self._chart_bg_image_path

        self.setStyleSheet("")
        self.price_plot.setStyleSheet("")
        self.plot.setStyleSheet("")

        if window_image_path:
            normalized = window_image_path.replace('\\', '/')
            self.setStyleSheet(
                f"QDialog#autoTraderWindow {{background-image: url('{normalized}'); background-position: center;}}"
            )

        chart_image_applied = False
        if chart_image_path:
            chart_pixmap = QPixmap(chart_image_path)
            if not chart_pixmap.isNull():
                self._ensure_chart_bg_items()
                self._price_bg_item.setPixmap(chart_pixmap)
                self._cvd_bg_item.setPixmap(chart_pixmap)
                self._price_bg_item.show()
                self._cvd_bg_item.show()
                self._sync_chart_bg_item_geometry(self.price_plot, self._price_bg_item)
                self._sync_chart_bg_item_geometry(self.plot, self._cvd_bg_item)
                chart_image_applied = True

        if chart_image_applied:
            self.price_plot.setBackground(None)
            self.plot.setBackground(None)
        else:
            self._clear_chart_bg_items()
            self.price_plot.setBackground(_C_BG)
            self.plot.setBackground(_C_BG)

        show_grid = self.show_grid_lines_check.isChecked() if hasattr(self, 'show_grid_lines_check') else True
        self.price_plot.showGrid(x=show_grid, y=show_grid, alpha=0.12)
        self.plot.showGrid(x=show_grid, y=show_grid, alpha=0.12)



    def _ensure_chart_bg_items(self):
        if not hasattr(self, '_price_bg_item'):
            self._price_bg_item = QGraphicsPixmapItem()
            self._price_bg_item.setZValue(-1e9)
            self.price_plot.plotItem.vb.addItem(self._price_bg_item, ignoreBounds=True)
            self.price_plot.plotItem.vb.sigRangeChanged.connect(
                lambda *_: self._sync_chart_bg_item_geometry(self.price_plot, self._price_bg_item)
            )
        if not hasattr(self, '_cvd_bg_item'):
            self._cvd_bg_item = QGraphicsPixmapItem()
            self._cvd_bg_item.setZValue(-1e9)
            self.plot.plotItem.vb.addItem(self._cvd_bg_item, ignoreBounds=True)
            self.plot.plotItem.vb.sigRangeChanged.connect(
                lambda *_: self._sync_chart_bg_item_geometry(self.plot, self._cvd_bg_item)
            )



    def _clear_chart_bg_items(self):
        if hasattr(self, '_price_bg_item'):
            self._price_bg_item.hide()
        if hasattr(self, '_cvd_bg_item'):
            self._cvd_bg_item.hide()



    @staticmethod
    def _sync_chart_bg_item_geometry(plot_widget, pixmap_item: QGraphicsPixmapItem):
        pixmap = pixmap_item.pixmap()
        if pixmap.isNull():
            return
        x_range, y_range = plot_widget.plotItem.vb.viewRange()
        x_min, x_max = float(x_range[0]), float(x_range[1])
        y_min, y_max = float(y_range[0]), float(y_range[1])
        width  = max(1.0, float(pixmap.width()))
        height = max(1.0, float(pixmap.height()))
        sx = (x_max - x_min) / width
        sy = (y_max - y_min) / height
        transform = QTransform()
        transform.translate(x_min, y_max)
        transform.scale(sx, -sy)
        pixmap_item.setTransform(transform)



    def _on_setup_visual_settings_changed(self, *_):
        self._apply_visual_settings()
        self._persist_setup_values()



    def _apply_visual_settings(self):
        self._chart_line_width       = float(self.chart_line_width_input.value())
        self._chart_line_opacity     = float(self.chart_line_opacity_input.value())
        self._confluence_line_width  = float(self.confluence_line_width_input.value())
        self._confluence_line_opacity = float(self.confluence_line_opacity_input.value())
        self._ema_line_opacity       = float(self.ema_line_opacity_input.value())

        self.price_prev_curve.setPen(
            pg.mkPen(self._price_line_color,
                     width=max(1.0, self._chart_line_width - 0.4), style=Qt.DashLine))
        self.price_today_curve.setPen(
            pg.mkPen(self._price_line_color, width=self._chart_line_width))
        self.price_today_tick_curve.setPen(
            pg.mkPen(self._price_line_color, width=max(0.8, self._chart_line_width - 1.0)))

        self.prev_curve.setPen(
            pg.mkPen(self._chart_line_color,
                     width=max(1.0, self._chart_line_width - 0.4), style=Qt.DashLine))
        self.today_curve.setPen(
            pg.mkPen(self._chart_line_color, width=self._chart_line_width))
        self.today_tick_curve.setPen(
            pg.mkPen(self._chart_line_color, width=max(0.8, self._chart_line_width - 1.0)))

        for curve in (self.price_prev_curve, self.price_today_curve, self.price_today_tick_curve,
                      self.prev_curve, self.today_curve, self.today_tick_curve):
            curve.setOpacity(self._chart_line_opacity)

        self._set_color_button(self.chart_line_color_btn,       self._chart_line_color)
        self._set_color_button(self.price_line_color_btn,       self._price_line_color)
        self._set_color_button(self.confluence_short_color_btn, self._confluence_short_color)
        self._set_color_button(self.confluence_long_color_btn,  self._confluence_long_color)

        for period, cb in self.setup_ema_default_checks.items():
            self.ema_checkboxes[period].setChecked(cb.isChecked())
            self._on_ema_toggled(period, cb.isChecked())

        show_simulator_controls = not self.hide_simulator_btn_check.isChecked()
        self.simulator_run_btn.setVisible(show_simulator_controls)
        self.simulator_summary_label.setVisible(show_simulator_controls)
        show_tick_backtest_controls = not self.hide_tick_backtest_controls_check.isChecked()
        self.tick_upload_btn.setVisible(show_tick_backtest_controls)
        self.tick_clear_btn.setVisible(show_tick_backtest_controls)
        self._apply_background_image()
        self._recolor_existing_confluence_lines()



    def _recolor_existing_confluence_lines(self):
        line_map = getattr(self, "_confluence_line_map", {})
        for key, pairs in line_map.items():
            is_short = key.startswith("S:")
            color = self._confluence_short_color if is_short else self._confluence_long_color
            for _, line in pairs:
                line.setPen(pg.mkPen(color, width=self._confluence_line_width))
                line.setOpacity(self._confluence_line_opacity)
