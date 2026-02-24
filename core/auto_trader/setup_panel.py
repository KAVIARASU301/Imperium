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
_DLG_MIN_W   = 1200   # dialog minimum width  (px)
_DLG_MIN_H   = 560    # dialog minimum height (px)
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
        auto_frm.addRow("Route",       self.automation_route_combo)
        c1.addWidget(auto_grp)

        # ── Stacker ───────────────────────────────────────────────────────
        stk_grp, stk_frm = _group("Stacker")
        _w(self.stacker_step_input)
        _w(self.stacker_max_input)
        stk_frm.addRow(_note("Pyramid: add a position every N favorable points."))
        stk_frm.addRow("Enable",     self.stacker_enabled_check)
        stk_frm.addRow("Step (pts)", self.stacker_step_input)
        stk_frm.addRow("Max Stacks", self.stacker_max_input)
        c1.addWidget(stk_grp)

        # ── ATR / Signal ──────────────────────────────────────────────────
        sig_grp, sig_frm = _group("ATR / Signal")
        _w(self.atr_base_ema_input)
        _w(self.atr_distance_input)
        _w(self.cvd_atr_distance_input)
        _w(self.atr_extension_threshold_input)
        _w(self.atr_flat_velocity_pct_input)
        _w(self.cvd_ema_gap_input)
        sig_frm.addRow("ATR Base EMA", self.atr_base_ema_input)
        sig_frm.addRow("ATR Distance", self.atr_distance_input)
        sig_frm.addRow("CVD ATR Dist", self.cvd_atr_distance_input)
        sig_frm.addRow("ATR Ext Min", self.atr_extension_threshold_input)
        sig_frm.addRow("ATR Flat Vel%", self.atr_flat_velocity_pct_input)
        sig_frm.addRow("CVD EMA Gap",  self.cvd_ema_gap_input)

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
        # COLUMN 2 — Range Breakout · Signal Governance · Open Drive
        # ══════════════════════════════════════════════════════════════════
        c2 = _col()

        # ── Range Breakout ────────────────────────────────────────────────
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
        c2.addStretch()

        # ══════════════════════════════════════════════════════════════════
        # COLUMN 3 — Chop Filter · Breakout Consolidation · CVD Breakout
        # ══════════════════════════════════════════════════════════════════
        c3 = _col()

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
        # ── CVD Range Breakout ────────────────────────────────────────────
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
        c3.addWidget(chop_grp)

        c3.addStretch()

        # ══════════════════════════════════════════════════════════════════
        # COLUMN 4 — Simulator · Chart Appearance
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
        general_layout.setContentsMargins(0, 0, 0, 0)
        general_layout.setSpacing(0)

        cols_row = QHBoxLayout()
        cols_row.setSpacing(_COL_SPACING)
        for col in (c1, c2, c3, c4):
            cols_row.addLayout(col, 1)
        general_layout.addLayout(cols_row, 1)

        priority_tab = QWidget()
        priority_layout_root = QHBoxLayout(priority_tab)
        priority_layout_root.setContentsMargins(6, 6, 6, 6)
        priority_layout_root.setSpacing(_COL_SPACING)
        priority_layout_root.addLayout(cpr_col, 1)
        priority_layout_root.addWidget(priority_panel, 3)

        def _strategy_tab(*widgets: QWidget, note: str = ""):
            tab = QWidget()
            lay = QVBoxLayout(tab)
            lay.setContentsMargins(6, 6, 6, 6)
            lay.setSpacing(_GRP_SPACING)
            if note:
                lay.addWidget(_note(note))
            for widget in widgets:
                lay.addWidget(widget)
            lay.addStretch()
            return tab

        tabs.addTab(general_tab, "General")
        tabs.addTab(_strategy_tab(sig_grp), self.STRATEGY_PRIORITY_LABELS["atr_reversal"])
        tabs.addTab(
            _strategy_tab(note="Uses ATR / Signal settings from ATR Reversal tab."),
            self.STRATEGY_PRIORITY_LABELS["atr_divergence"],
        )
        tabs.addTab(
            _strategy_tab(note="Uses ATR / Signal settings from ATR Reversal tab."),
            self.STRATEGY_PRIORITY_LABELS["ema_cross"],
        )
        tabs.addTab(_strategy_tab(cvdbk_grp, consol_grp), self.STRATEGY_PRIORITY_LABELS["cvd_range_breakout"])
        tabs.addTab(_strategy_tab(brk_grp), self.STRATEGY_PRIORITY_LABELS["range_breakout"])
        tabs.addTab(_strategy_tab(od_grp), self.STRATEGY_PRIORITY_LABELS["open_drive"])
        self._build_regime_tab(tabs, compact_spinbox_style, compact_combo_style)
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
