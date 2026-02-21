import re

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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
)


class SetupPanelMixin:
    def _build_setup_dialog(self, compact_combo_style: str, compact_spinbox_style: str):
        self.setup_dialog = QDialog(self)
        self.setup_dialog.setWindowTitle("Auto Trader Setup")
        self.setup_dialog.setModal(False)
        self.setup_dialog.setMinimumWidth(980)
        self.setup_dialog.setMinimumHeight(620)
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
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        content_row = QHBoxLayout()
        content_row.setSpacing(8)

        # ── Shared compact widths ──────────────────────────────────────────────
        INPUT_W  = 85   # spinbox / short input
        COMBO_W  = 150  # combo boxes

        col1 = QVBoxLayout()
        col1.setSpacing(7)
        col2 = QVBoxLayout()
        col2.setSpacing(7)
        col3 = QVBoxLayout()
        col3.setSpacing(7)

        def _set_input_w(widget, w=INPUT_W):
            widget.setFixedWidth(w)

        def _set_combo_w(widget, w=COMBO_W):
            widget.setFixedWidth(w)

        def _compact_form(group_title):
            grp = QGroupBox(group_title)
            frm = QFormLayout(grp)
            frm.setLabelAlignment(Qt.AlignLeft)
            frm.setContentsMargins(8, 6, 8, 6)
            frm.setSpacing(5)
            frm.setHorizontalSpacing(8)
            return grp, frm

        # ══════════════════════════════════════════════════════════════════════
        # COL 1 — Automation  +  ATR / Signal
        # ══════════════════════════════════════════════════════════════════════

        auto_group, auto_form = _compact_form("Automation")
        _set_input_w(self.automation_stoploss_input)
        _set_combo_w(self.automation_route_combo)
        auto_form.addRow("Stop Loss", self.automation_stoploss_input)
        auto_form.addRow("Route", self.automation_route_combo)
        col1.addWidget(auto_group)

        signal_group, signal_form = _compact_form("ATR / Signal")
        _set_input_w(self.atr_base_ema_input)
        _set_input_w(self.atr_distance_input)
        _set_input_w(self.cvd_atr_distance_input)
        _set_input_w(self.cvd_ema_gap_input)
        signal_form.addRow("ATR Base EMA",    self.atr_base_ema_input)
        signal_form.addRow("ATR Distance",    self.atr_distance_input)
        signal_form.addRow("CVD ATR Dist",    self.cvd_atr_distance_input)
        signal_form.addRow("CVD EMA Gap",     self.cvd_ema_gap_input)

        self.setup_signal_filter_combo = QComboBox()
        self.setup_signal_filter_combo.setStyleSheet(compact_combo_style)
        _set_combo_w(self.setup_signal_filter_combo)
        self.setup_signal_filter_combo.addItem("All Signals",         self.SIGNAL_FILTER_ALL)
        self.setup_signal_filter_combo.addItem("ATR Reversal Only",   self.SIGNAL_FILTER_ATR_ONLY)
        self.setup_signal_filter_combo.addItem("EMA Cross Only",      self.SIGNAL_FILTER_EMA_CROSS_ONLY)
        self.setup_signal_filter_combo.addItem("Range Breakout Only", self.SIGNAL_FILTER_BREAKOUT_ONLY)
        self.setup_signal_filter_combo.addItem("ATR Divergence",      self.SIGNAL_FILTER_OTHERS)
        self.setup_signal_filter_combo.setCurrentIndex(self.signal_filter_combo.currentIndex())
        self.setup_signal_filter_combo.currentIndexChanged.connect(self._on_setup_signal_filter_changed)
        signal_form.addRow("Signal Filter", self.setup_signal_filter_combo)

        self.setup_atr_marker_filter_combo = QComboBox()
        self.setup_atr_marker_filter_combo.setStyleSheet(compact_combo_style)
        _set_combo_w(self.setup_atr_marker_filter_combo)
        self.setup_atr_marker_filter_combo.addItem("Show All",        self.ATR_MARKER_SHOW_ALL)
        self.setup_atr_marker_filter_combo.addItem("Confluence Only", self.ATR_MARKER_CONFLUENCE_ONLY)
        self.setup_atr_marker_filter_combo.addItem("Green Only",      self.ATR_MARKER_GREEN_ONLY)
        self.setup_atr_marker_filter_combo.addItem("Red Only",        self.ATR_MARKER_RED_ONLY)
        self.setup_atr_marker_filter_combo.addItem("Hide All",        self.ATR_MARKER_HIDE_ALL)
        self.setup_atr_marker_filter_combo.setCurrentIndex(self.atr_marker_filter_combo.currentIndex())
        self.setup_atr_marker_filter_combo.currentIndexChanged.connect(self._on_setup_atr_marker_filter_changed)
        signal_form.addRow("ATR Markers", self.setup_atr_marker_filter_combo)
        col1.addWidget(signal_group)

        col1.addStretch()

        # ══════════════════════════════════════════════════════════════════════
        # COL 2 — Range Breakout  +  Chop Filter  +  Consolidation Requirement
        # ══════════════════════════════════════════════════════════════════════

        breakout_group, breakout_form = _compact_form("Range Breakout")

        self.range_lookback_input = QSpinBox()
        self.range_lookback_input.setRange(10, 120)
        self.range_lookback_input.setValue(15)
        self.range_lookback_input.setSuffix(" min")
        self.range_lookback_input.setStyleSheet(compact_spinbox_style)
        _set_input_w(self.range_lookback_input)
        self.range_lookback_input.setToolTip(
            "Period to analyze for consolidation range detection.\n"
            "Breakout signals trigger when price breaks above/below this range."
        )
        self.range_lookback_input.valueChanged.connect(self._on_breakout_settings_changed)
        breakout_form.addRow("Range Lookback", self.range_lookback_input)

        self.breakout_switch_mode_combo = QComboBox()
        self.breakout_switch_mode_combo.setStyleSheet(compact_combo_style)
        _set_combo_w(self.breakout_switch_mode_combo)
        self.breakout_switch_mode_combo.addItem("Keep Breakout",  self.BREAKOUT_SWITCH_KEEP)
        self.breakout_switch_mode_combo.addItem("Prefer ATR Rev", self.BREAKOUT_SWITCH_PREFER_ATR)
        self.breakout_switch_mode_combo.addItem("Adaptive",       self.BREAKOUT_SWITCH_ADAPTIVE)
        self.breakout_switch_mode_combo.setToolTip(
            "Controls behavior when ATR reversal appears after a breakout:\n"
            "• Keep Breakout: ignore opposite ATR reversals.\n"
            "• Prefer ATR Rev: allow reversal immediately.\n"
            "• Adaptive: keep breakout only when momentum is still strong."
        )
        self.breakout_switch_mode_combo.currentIndexChanged.connect(self._on_breakout_settings_changed)
        breakout_form.addRow("Breakout vs ATR", self.breakout_switch_mode_combo)

        self.atr_skip_limit_input = QSpinBox()
        self.atr_skip_limit_input.setRange(0, 20)
        self.atr_skip_limit_input.setValue(0)
        self.atr_skip_limit_input.setSpecialValueText("Off")
        self.atr_skip_limit_input.setStyleSheet(compact_spinbox_style)
        _set_input_w(self.atr_skip_limit_input)
        self.atr_skip_limit_input.setToolTip(
            "How many ATR Reversal signals to skip while a Range Breakout trade is active\n"
            "before overriding: close the breakout and take the ATR entry.\n\n"
            "0 = Off (existing behaviour — always follow Breakout vs ATR setting).\n"
            "Example: 3 → skip first 3 ATR signals, take the 4th."
        )
        self.atr_skip_limit_input.valueChanged.connect(self._on_breakout_settings_changed)
        breakout_form.addRow("ATR Skip Limit", self.atr_skip_limit_input)
        col2.addWidget(breakout_group)

        # ── Chop Filter (per strategy) ────────────────────────────────────────
        chop_group, chop_form = _compact_form("Chop Filter")

        chop_note = QLabel("Range Breakout is never chop-filtered.")
        chop_note.setStyleSheet("color:#8A9BA8; font-size:10px;")
        chop_form.addRow(chop_note)

        self.chop_filter_atr_reversal_check = QCheckBox("ATR Reversal")
        self.chop_filter_atr_reversal_check.setChecked(True)
        self.chop_filter_atr_reversal_check.setToolTip(
            "Filter ATR Reversal signals in choppy regime (low ADX / price hugging EMA51)."
        )
        self.chop_filter_atr_reversal_check.toggled.connect(self._on_chop_filter_settings_changed)

        self.chop_filter_ema_cross_check = QCheckBox("EMA Cross")
        self.chop_filter_ema_cross_check.setChecked(True)
        self.chop_filter_ema_cross_check.setToolTip(
            "Filter EMA Cross signals in chop. Highly recommended — crosses in flat markets are false."
        )
        self.chop_filter_ema_cross_check.toggled.connect(self._on_chop_filter_settings_changed)

        self.chop_filter_atr_divergence_check = QCheckBox("ATR Divergence")
        self.chop_filter_atr_divergence_check.setChecked(True)
        self.chop_filter_atr_divergence_check.setToolTip(
            "Filter ATR Divergence signals in chop — needs a trending CVD context to work."
        )
        self.chop_filter_atr_divergence_check.toggled.connect(self._on_chop_filter_settings_changed)

        chop_checks_row = QHBoxLayout()
        chop_checks_row.setSpacing(6)
        chop_checks_row.addWidget(self.chop_filter_atr_reversal_check)
        chop_checks_row.addWidget(self.chop_filter_ema_cross_check)
        chop_checks_row.addWidget(self.chop_filter_atr_divergence_check)
        chop_checks_row.addStretch()
        chop_form.addRow(chop_checks_row)
        col2.addWidget(chop_group)

        # ── Breakout Consolidation Requirement ────────────────────────────────
        consol_group, consol_form = _compact_form("Breakout Consolidation")

        consol_note = QLabel("Require a squeeze before breakout fires. 0 = off.")
        consol_note.setStyleSheet("color:#8A9BA8; font-size:10px;")
        consol_form.addRow(consol_note)

        self.breakout_min_consol_input = QSpinBox()
        self.breakout_min_consol_input.setRange(0, 120)
        self.breakout_min_consol_input.setSingleStep(5)
        self.breakout_min_consol_input.setValue(0)
        self.breakout_min_consol_input.setSuffix(" min")
        self.breakout_min_consol_input.setStyleSheet(compact_spinbox_style)
        _set_input_w(self.breakout_min_consol_input)
        self.breakout_min_consol_input.setToolTip(
            "Require price to have been range-bound for at least this many minutes before a breakout.\n"
            "0 = disabled. Recommended: 15–30 min."
        )
        self.breakout_min_consol_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        consol_form.addRow("Min Consol", self.breakout_min_consol_input)

        self.breakout_min_consol_adx_input = QDoubleSpinBox()
        self.breakout_min_consol_adx_input.setRange(0.0, 50.0)
        self.breakout_min_consol_adx_input.setDecimals(1)
        self.breakout_min_consol_adx_input.setSingleStep(1.0)
        self.breakout_min_consol_adx_input.setValue(0.0)
        self.breakout_min_consol_adx_input.setStyleSheet(compact_spinbox_style)
        _set_input_w(self.breakout_min_consol_adx_input)
        self.breakout_min_consol_adx_input.setToolTip(
            "During the consolidation window, require ADX below this threshold.\n"
            "0 = disabled. Recommended: 20–22."
        )
        self.breakout_min_consol_adx_input.valueChanged.connect(self._on_chop_filter_settings_changed)
        consol_form.addRow("Max ADX", self.breakout_min_consol_adx_input)
        col2.addWidget(consol_group)

        col2.addStretch()

        # ══════════════════════════════════════════════════════════════════════
        # COL 3 — Simulator  +  Chart Appearance
        # ══════════════════════════════════════════════════════════════════════

        simulator_group, simulator_layout_form = _compact_form("Simulator")
        simulator_layout_inner = QVBoxLayout()
        simulator_layout_inner.setSpacing(4)
        sim_info = QLabel("Uses same entry/exit rules as live automation.")
        sim_info.setWordWrap(True)
        sim_info.setStyleSheet("color:#B0B0B0; font-size:10px;")
        simulator_layout_inner.addWidget(sim_info)
        self.hide_simulator_btn_check = QCheckBox("Hide 'Run Simulator' button")
        self.hide_simulator_btn_check.toggled.connect(self._on_setup_visual_settings_changed)
        simulator_layout_inner.addWidget(self.hide_simulator_btn_check)
        # Embed the inner layout into the group (QFormLayout doesn't take VBox directly)
        sim_wrapper = QWidget()
        sim_wrapper.setLayout(simulator_layout_inner)
        simulator_layout_form.addRow(sim_wrapper)
        col3.addWidget(simulator_group)

        appearance_group, appearance_form = _compact_form("Chart Appearance")

        self.chart_line_width_input = QDoubleSpinBox()
        self.chart_line_width_input.setRange(0.5, 8.0)
        self.chart_line_width_input.setDecimals(1)
        self.chart_line_width_input.setSingleStep(0.1)
        self.chart_line_width_input.setValue(self._chart_line_width)
        self.chart_line_width_input.setStyleSheet(compact_spinbox_style)
        _set_input_w(self.chart_line_width_input)
        self.chart_line_width_input.valueChanged.connect(self._on_setup_visual_settings_changed)
        appearance_form.addRow("CVD Line W", self.chart_line_width_input)

        self.chart_line_opacity_input = QDoubleSpinBox()
        self.chart_line_opacity_input.setRange(0.1, 1.0)
        self.chart_line_opacity_input.setDecimals(2)
        self.chart_line_opacity_input.setSingleStep(0.05)
        self.chart_line_opacity_input.setValue(self._chart_line_opacity)
        self.chart_line_opacity_input.setStyleSheet(compact_spinbox_style)
        _set_input_w(self.chart_line_opacity_input)
        self.chart_line_opacity_input.valueChanged.connect(self._on_setup_visual_settings_changed)
        appearance_form.addRow("CVD Opacity", self.chart_line_opacity_input)

        self.confluence_line_width_input = QDoubleSpinBox()
        self.confluence_line_width_input.setRange(0.5, 8.0)
        self.confluence_line_width_input.setDecimals(1)
        self.confluence_line_width_input.setSingleStep(0.1)
        self.confluence_line_width_input.setValue(self._confluence_line_width)
        self.confluence_line_width_input.setStyleSheet(compact_spinbox_style)
        _set_input_w(self.confluence_line_width_input)
        self.confluence_line_width_input.valueChanged.connect(self._on_setup_visual_settings_changed)
        appearance_form.addRow("Conf Line W", self.confluence_line_width_input)

        self.confluence_line_opacity_input = QDoubleSpinBox()
        self.confluence_line_opacity_input.setRange(0.1, 1.0)
        self.confluence_line_opacity_input.setDecimals(2)
        self.confluence_line_opacity_input.setSingleStep(0.05)
        self.confluence_line_opacity_input.setValue(self._confluence_line_opacity)
        self.confluence_line_opacity_input.setStyleSheet(compact_spinbox_style)
        _set_input_w(self.confluence_line_opacity_input)
        self.confluence_line_opacity_input.valueChanged.connect(self._on_setup_visual_settings_changed)
        appearance_form.addRow("Conf Opacity", self.confluence_line_opacity_input)

        self.ema_line_opacity_input = QDoubleSpinBox()
        self.ema_line_opacity_input.setRange(0.1, 1.0)
        self.ema_line_opacity_input.setDecimals(2)
        self.ema_line_opacity_input.setSingleStep(0.05)
        self.ema_line_opacity_input.setValue(self._ema_line_opacity)
        self.ema_line_opacity_input.setStyleSheet(compact_spinbox_style)
        _set_input_w(self.ema_line_opacity_input)
        self.ema_line_opacity_input.valueChanged.connect(self._on_setup_visual_settings_changed)
        appearance_form.addRow("EMA Opacity", self.ema_line_opacity_input)

        # Color buttons — compact row pairs
        color_btn_style = """
            QPushButton {
                background: #2A2F3D; color: #E0E0E0; border: 1px solid #3A4458;
                border-radius: 3px; padding: 2px 6px; font-size: 10px; min-height: 20px;
            }
            QPushButton:hover { border: 1px solid #5B9BD5; }
        """
        self.chart_line_color_btn = QPushButton("CVD Line")
        self.chart_line_color_btn.setStyleSheet(color_btn_style)
        self.chart_line_color_btn.clicked.connect(lambda: self._pick_color("chart_line_color_btn", "_chart_line_color"))

        self.price_line_color_btn = QPushButton("Price Line")
        self.price_line_color_btn.setStyleSheet(color_btn_style)
        self.price_line_color_btn.clicked.connect(lambda: self._pick_color("price_line_color_btn", "_price_line_color"))

        color_row1 = QHBoxLayout()
        color_row1.setSpacing(4)
        color_row1.addWidget(self.chart_line_color_btn)
        color_row1.addWidget(self.price_line_color_btn)
        appearance_form.addRow("Colors", color_row1)

        self.confluence_short_color_btn = QPushButton("Short")
        self.confluence_short_color_btn.setStyleSheet(color_btn_style)
        self.confluence_short_color_btn.clicked.connect(lambda: self._pick_color("confluence_short_color_btn", "_confluence_short_color"))

        self.confluence_long_color_btn = QPushButton("Long")
        self.confluence_long_color_btn.setStyleSheet(color_btn_style)
        self.confluence_long_color_btn.clicked.connect(lambda: self._pick_color("confluence_long_color_btn", "_confluence_long_color"))

        color_row2 = QHBoxLayout()
        color_row2.setSpacing(4)
        color_row2.addWidget(self.confluence_short_color_btn)
        color_row2.addWidget(self.confluence_long_color_btn)
        appearance_form.addRow("Conf Colors", color_row2)

        ema_defaults_row = QHBoxLayout()
        ema_defaults_row.setSpacing(6)
        self.setup_ema_default_checks = {}
        for period in (10, 21, 51):
            cb = QCheckBox(str(period))
            cb.setChecked(period == 51)
            cb.toggled.connect(self._on_setup_visual_settings_changed)
            self.setup_ema_default_checks[period] = cb
            ema_defaults_row.addWidget(cb)
        ema_defaults_row.addStretch()
        appearance_form.addRow("Default EMAs", ema_defaults_row)

        self.bg_target_combo = QComboBox()
        self.bg_target_combo.setStyleSheet(compact_combo_style)
        _set_combo_w(self.bg_target_combo)
        self.bg_target_combo.addItem("No BG Image",       self.BG_TARGET_NONE)
        self.bg_target_combo.addItem("Apply to Chart",    self.BG_TARGET_CHART)
        self.bg_target_combo.addItem("Apply to Window",   self.BG_TARGET_WINDOW)
        self.bg_target_combo.currentIndexChanged.connect(self._on_setup_visual_settings_changed)
        appearance_form.addRow("BG Target", self.bg_target_combo)

        bg_row = QHBoxLayout()
        bg_row.setSpacing(4)
        self.bg_image_label = QLabel("No image")
        self.bg_image_label.setStyleSheet("color:#8A9BA8; font-size:10px;")
        self.bg_upload_btn = QPushButton("Upload")
        self.bg_upload_btn.setStyleSheet(color_btn_style)
        self.bg_upload_btn.clicked.connect(self._on_pick_background_image)
        self.bg_clear_btn = QPushButton("Clear")
        self.bg_clear_btn.setStyleSheet(color_btn_style)
        self.bg_clear_btn.clicked.connect(self._on_clear_background_image)
        bg_row.addWidget(self.bg_image_label, 1)
        bg_row.addWidget(self.bg_upload_btn)
        bg_row.addWidget(self.bg_clear_btn)
        appearance_form.addRow("BG Image", bg_row)

        col3.addWidget(appearance_group)
        col3.addStretch()

        # ── Assemble columns ──────────────────────────────────────────────────
        content_row.addLayout(col1, 1)
        content_row.addLayout(col2, 1)
        content_row.addLayout(col3, 1)
        layout.addLayout(content_row, 1)

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



    def _set_color_button(self, btn: QPushButton, color_hex: str):
        btn.setText(color_hex.upper())
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {color_hex};
                color: #111;
                font-weight: 700;
                border: 1px solid #3A4458;
                border-radius: 4px;
                padding: 4px 8px;
            }}
            """
        )



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



    def _on_pick_background_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Background Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not file_path:
            return
        self._window_bg_image_path = file_path
        self._update_bg_image_label()
        self._apply_background_image()
        self._persist_setup_values()



    def _on_clear_background_image(self):
        self._window_bg_image_path = ""
        self._update_bg_image_label()
        self._apply_background_image()
        self._persist_setup_values()



    def _update_bg_image_label(self):
        if not self._window_bg_image_path:
            self.bg_image_label.setText("No image selected")
            return
        self.bg_image_label.setText(self._window_bg_image_path.split('/')[-1])



    def _apply_background_image(self):
        image_path = self._window_bg_image_path
        target = self.bg_target_combo.currentData() if hasattr(self, 'bg_target_combo') else self.BG_TARGET_NONE
        self._window_bg_target = target or self.BG_TARGET_NONE

        # reset to defaults
        self.setStyleSheet("")
        self.price_plot.setStyleSheet("")
        self.plot.setStyleSheet("")
        self.price_plot.setBackground("#161A25")
        self.plot.setBackground("#161A25")

        if not image_path or target == self.BG_TARGET_NONE:
            return

        normalized = image_path.replace('\\', '/')
        if target == self.BG_TARGET_WINDOW:
            self.setStyleSheet(
                f"QDialog#autoTraderWindow {{background-image: url('{normalized}'); background-position: center;}}"
            )
        elif target == self.BG_TARGET_CHART:
            chart_style = f"QWidget {{background-image: url('{normalized}'); background-position: center;}}"
            self.price_plot.setStyleSheet(chart_style)
            self.plot.setStyleSheet(chart_style)



    def _on_setup_visual_settings_changed(self, *_):
        self._apply_visual_settings()
        self._persist_setup_values()



    def _apply_visual_settings(self):
        self._chart_line_width = float(self.chart_line_width_input.value())
        self._chart_line_opacity = float(self.chart_line_opacity_input.value())
        self._confluence_line_width = float(self.confluence_line_width_input.value())
        self._confluence_line_opacity = float(self.confluence_line_opacity_input.value())
        self._ema_line_opacity = float(self.ema_line_opacity_input.value())

        self.price_prev_curve.setPen(pg.mkPen(self._price_line_color, width=max(1.0, self._chart_line_width - 0.4), style=Qt.DashLine))
        self.price_today_curve.setPen(pg.mkPen(self._price_line_color, width=self._chart_line_width))
        self.price_today_tick_curve.setPen(pg.mkPen(self._price_line_color, width=max(0.8, self._chart_line_width - 1.0)))

        self.prev_curve.setPen(pg.mkPen(self._chart_line_color, width=max(1.0, self._chart_line_width - 0.4), style=Qt.DashLine))
        self.today_curve.setPen(pg.mkPen(self._chart_line_color, width=self._chart_line_width))
        self.today_tick_curve.setPen(pg.mkPen(self._chart_line_color, width=max(0.8, self._chart_line_width - 1.0)))

        for curve in (self.price_prev_curve, self.price_today_curve, self.price_today_tick_curve, self.prev_curve, self.today_curve, self.today_tick_curve):
            curve.setOpacity(self._chart_line_opacity)

        self._set_color_button(self.chart_line_color_btn, self._chart_line_color)
        self._set_color_button(self.price_line_color_btn, self._price_line_color)
        self._set_color_button(self.confluence_short_color_btn, self._confluence_short_color)
        self._set_color_button(self.confluence_long_color_btn, self._confluence_long_color)

        for period, cb in self.setup_ema_default_checks.items():
            self.ema_checkboxes[period].setChecked(cb.isChecked())
            self._on_ema_toggled(period, cb.isChecked())

        self.simulator_run_btn.setVisible(not self.hide_simulator_btn_check.isChecked())
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


