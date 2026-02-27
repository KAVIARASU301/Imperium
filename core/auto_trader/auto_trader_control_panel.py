"""
auto_trader_control_panel.py
─────────────────────────────
Institutional-grade control panel mixin for AutoTraderDialog.

Replaces the scattered rows of controls with a single dense,
three-band horizontal toolbar:

  BAND A — Navigation + Chart Controls
  BAND B — Quick Params (most-used signal params surfaced from Setup)
  BAND C — Automation + Execution + Status

Usage: mix into AutoTraderDialog *before* SetupPanelMixin in the MRO,
       or call _build_control_panel() from _setup_ui().
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFontMetrics,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.auto_trader.auto_trader_theme import DIMS, THEME, Styles


# ─── Utility factories ────────────────────────────────────────────────────────

def _vline() -> QFrame:
    """Thin vertical separator."""
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setStyleSheet(f"color: {THEME['border']}; background: {THEME['border']};")
    f.setFixedWidth(1)
    f.setFixedHeight(18)
    return f


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(Styles.DIVIDER_LABEL)
    return lbl


def _param_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(Styles.LABEL_MUTED)
    return lbl


def _compact_spin(min_: int | float, max_: int | float, default,
                  decimals: int = 0, step=None, suffix: str = "",
                  width: int = DIMS["input_w_m"]) -> QSpinBox | QDoubleSpinBox:
    if decimals > 0:
        w: QDoubleSpinBox = QDoubleSpinBox()
        w.setDecimals(decimals)
        if step:
            w.setSingleStep(step)
        w.setRange(float(min_), float(max_))
        w.setValue(float(default))
    else:
        w: QSpinBox = QSpinBox()
        w.setRange(int(min_), int(max_))
        w.setValue(int(default))
        if step:
            w.setSingleStep(int(step))
    w.setFixedWidth(width)
    if suffix:
        w.setSuffix(suffix)
    w.setStyleSheet(Styles.SPINBOX)
    w.setFixedHeight(DIMS["input_h"])
    return w


def _param_group(label: str, widget: QWidget,
                 gap: int = 3) -> QHBoxLayout:
    """Label + widget pair in a tight HBox."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(gap)
    row.addWidget(_param_label(label))
    row.addWidget(widget)
    return row


# ═════════════════════════════════════════════════════════════════════════════
# MIXIN
# ═════════════════════════════════════════════════════════════════════════════

class ControlPanelMixin:
    """
    Builds the three-band institutional control panel.

    Requires the host dialog to have already constructed:
      • self.navigator          (DateNavigator)
      • self.timeframe_combo    (QComboBox)
      • self.btn_focus          (QPushButton, checkable)
      • all automation / signal spinboxes from _setup_ui
      • self.regime_indicator   (RegimeIndicator)
      • self.simulator_run_btn, self.tick_upload_btn, self.tick_clear_btn
      • self.signal_filter_combo
      • self.atr_marker_filter_combo
      • self.automate_toggle
      • self.stacker_enabled_check, stacker_step/max inputs
      • self.setup_btn

    Call `_build_control_panel(root_layout)` from `_setup_ui` AFTER those
    widgets exist and BEFORE adding the chart widgets.
    """

    def _build_control_panel(self, root_layout: QVBoxLayout) -> None:
        """Assemble the full three-band panel and add it to root_layout."""

        panel = QWidget()
        panel.setObjectName("controlPanel")
        panel.setStyleSheet(f"""
            QWidget#controlPanel {{
                background: {THEME['bg_panel']};
                border-bottom: 2px solid {THEME['separator']};
            }}
        """)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(8, 3, 8, 3)
        panel_layout.setSpacing(2)

        # ── Band A ────────────────────────────────────────────────────────
        band_a = self._build_band_a()
        panel_layout.addLayout(band_a)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setStyleSheet(f"color: {THEME['separator']};")
        sep1.setFixedHeight(1)
        panel_layout.addWidget(sep1)

        # ── Band B ────────────────────────────────────────────────────────
        band_b = self._build_band_b()
        panel_layout.addLayout(band_b)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color: {THEME['separator']};")
        sep2.setFixedHeight(1)
        panel_layout.addWidget(sep2)

        # ── Band C ────────────────────────────────────────────────────────
        band_c = self._build_band_c()
        panel_layout.addLayout(band_c)

        root_layout.addWidget(panel)

    # ─────────────────────────────────────────────────────────────────────
    # BAND A — Navigation · Chart View · Overlays · Signal Filters
    # ─────────────────────────────────────────────────────────────────────

    def _build_band_a(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)

        # ── Left: TF + focus + navigator ──
        row.addWidget(_section_label("TF"))
        self.timeframe_combo.setFixedHeight(DIMS["btn_h"])
        self.timeframe_combo.setStyleSheet(Styles.COMBO)
        row.addWidget(self.timeframe_combo)

        self.btn_focus.setFixedHeight(DIMS["btn_h"])
        self.btn_focus.setFixedWidth(40)
        self.btn_focus.setStyleSheet(Styles.BTN_TOGGLE)
        row.addWidget(self.btn_focus)

        row.addWidget(_vline())
        row.addWidget(self.navigator)
        row.addWidget(_vline())

        # ── EMAs ──
        row.addWidget(_section_label("EMA"))
        ema_configs = [(10, THEME["ema_10"]), (21, THEME["ema_21"]), (51, THEME["ema_51"])]
        for period, color in ema_configs:
            cb = self.ema_checkboxes[period]
            cb.setStyleSheet(f"""
                QCheckBox {{
                    color: {color};
                    font-weight: 700;
                    font-size: 11px;
                    spacing: 3px;
                }}
                QCheckBox::indicator {{
                    width: 13px; height: 13px;
                    border: 1px solid {color}55;
                    border-radius: 2px;
                    background: {THEME['bg_input']};
                }}
                QCheckBox::indicator:checked {{
                    background: {color};
                    border-color: {color};
                }}
            """)
            row.addWidget(cb)

        self.vwap_checkbox.setStyleSheet(f"""
            QCheckBox {{
                color: {THEME['vwap']};
                font-weight: 700;
                font-size: 11px;
                spacing: 3px;
            }}
            QCheckBox::indicator {{
                width: 13px; height: 13px;
                border: 1px solid {THEME['vwap']}55;
                border-radius: 2px;
                background: {THEME['bg_input']};
            }}
            QCheckBox::indicator:checked {{
                background: {THEME['vwap']};
                border-color: {THEME['vwap']};
            }}
        """)
        row.addWidget(self.vwap_checkbox)

        row.addWidget(_vline())

        # ── Signal filter ──
        row.addWidget(_section_label("SIG"))
        self.signal_filter_combo.setFixedHeight(DIMS["btn_h"])
        self.signal_filter_combo.setStyleSheet(Styles.COMBO)
        row.addWidget(self.signal_filter_combo)

        # ── ATR marker filter ──
        row.addWidget(_section_label("MRK"))
        self.atr_marker_filter_combo.setFixedHeight(DIMS["btn_h"])
        self.atr_marker_filter_combo.setStyleSheet(Styles.COMBO)
        row.addWidget(self.atr_marker_filter_combo)

        row.addWidget(_vline())

        # ── Right: icon buttons ──
        self.btn_refresh_plot.setFixedSize(DIMS["btn_h"], DIMS["btn_h"])
        self.btn_refresh_plot.setStyleSheet(Styles.BTN_ICON)
        row.addWidget(self.btn_refresh_plot)

        self.btn_export.setFixedSize(DIMS["btn_h"], DIMS["btn_h"])
        self.btn_export.setStyleSheet(Styles.BTN_ICON)
        row.addWidget(self.btn_export)

        self.setup_btn.setFixedHeight(DIMS["btn_h"])
        self.setup_btn.setStyleSheet(f"""
            QPushButton {{
                background: {THEME['bg_card']};
                color: {THEME['accent_blue']};
                font-weight: 700;
                font-size: 11px;
                border: 1px solid {THEME['border_focus']};
                border-radius: 2px;
                padding: 2px 12px;
            }}
            QPushButton:hover {{
                background: {THEME['bg_hover']};
                border-color: {THEME['border_active']};
            }}
        """)
        row.addWidget(self.setup_btn)

        row.addStretch()
        return row

    # ─────────────────────────────────────────────────────────────────────
    # BAND B — Quick Params (surfaced from Setup dialog for 1-click tuning)
    # ─────────────────────────────────────────────────────────────────────

    def _build_band_b(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(4)

        # ── ATR params ──────────────────────────────────────────────────
        row.addWidget(_section_label("ATR"))
        row.addSpacing(2)

        row.addLayout(_param_group("Base EMA", self.atr_base_ema_input))
        self.atr_base_ema_input.setFixedWidth(DIMS["input_w_s"])
        self.atr_base_ema_input.setFixedHeight(DIMS["input_h"])
        self.atr_base_ema_input.setStyleSheet(Styles.SPINBOX)

        row.addLayout(_param_group("Dist", self.atr_distance_input))
        self.atr_distance_input.setFixedWidth(DIMS["input_w_s"])
        self.atr_distance_input.setFixedHeight(DIMS["input_h"])
        self.atr_distance_input.setStyleSheet(Styles.SPINBOX)

        row.addLayout(_param_group("Ext", self.atr_extension_threshold_input))
        self.atr_extension_threshold_input.setFixedWidth(DIMS["input_w_s"])
        self.atr_extension_threshold_input.setFixedHeight(DIMS["input_h"])
        self.atr_extension_threshold_input.setStyleSheet(Styles.SPINBOX)

        row.addWidget(_vline())

        # ── CVD params ──────────────────────────────────────────────────
        row.addWidget(_section_label("CVD"))
        row.addSpacing(2)

        row.addLayout(_param_group("Z-Score", self.cvd_atr_distance_input))
        self.cvd_atr_distance_input.setFixedWidth(DIMS["input_w_s"])
        self.cvd_atr_distance_input.setFixedHeight(DIMS["input_h"])
        self.cvd_atr_distance_input.setStyleSheet(Styles.SPINBOX)

        row.addLayout(_param_group("Gap", self.cvd_ema_gap_input))
        self.cvd_ema_gap_input.setFixedWidth(DIMS["input_w_m"])
        self.cvd_ema_gap_input.setFixedHeight(DIMS["input_h"])
        self.cvd_ema_gap_input.setStyleSheet(Styles.SPINBOX)

        row.addWidget(_vline())

        # ── Risk params ──────────────────────────────────────────────────
        row.addWidget(_section_label("RISK"))
        row.addSpacing(2)

        row.addLayout(_param_group("SL", self.automation_stoploss_input))
        self.automation_stoploss_input.setFixedWidth(DIMS["input_w_s"])
        self.automation_stoploss_input.setFixedHeight(DIMS["input_h"])
        self.automation_stoploss_input.setStyleSheet(Styles.SPINBOX)

        row.addLayout(_param_group("GvB", self.max_profit_giveback_input))
        self.max_profit_giveback_input.setFixedWidth(DIMS["input_w_s"])
        self.max_profit_giveback_input.setFixedHeight(DIMS["input_h"])
        self.max_profit_giveback_input.setStyleSheet(Styles.SPINBOX)

        row.addLayout(_param_group("Trail", self.atr_trailing_step_input))
        self.atr_trailing_step_input.setFixedWidth(DIMS["input_w_s"])
        self.atr_trailing_step_input.setFixedHeight(DIMS["input_h"])
        self.atr_trailing_step_input.setStyleSheet(Styles.SPINBOX)

        row.addWidget(_vline())

        # ── Governance params ────────────────────────────────────────────
        row.addWidget(_section_label("GOV"))
        row.addSpacing(2)

        self.deploy_mode_combo.setFixedHeight(DIMS["input_h"])
        self.deploy_mode_combo.setFixedWidth(84)
        self.deploy_mode_combo.setStyleSheet(Styles.COMBO)
        row.addLayout(_param_group("Mode", self.deploy_mode_combo))

        self.min_confidence_input.setFixedWidth(DIMS["input_w_s"])
        self.min_confidence_input.setFixedHeight(DIMS["input_h"])
        self.min_confidence_input.setStyleSheet(Styles.SPINBOX)
        row.addLayout(_param_group("Conf≥", self.min_confidence_input))

        row.addWidget(_vline())

        # ── Trend Exit config ────────────────────────────────────────────
        row.addWidget(_section_label("TREND EXIT"))
        row.addSpacing(2)

        self.trend_exit_adx_min_input.setFixedWidth(DIMS["input_w_s"])
        self.trend_exit_adx_min_input.setFixedHeight(DIMS["input_h"])
        self.trend_exit_adx_min_input.setStyleSheet(Styles.SPINBOX)
        row.addLayout(_param_group("ADX≥", self.trend_exit_adx_min_input))

        self.trend_exit_confirm_bars_input.setFixedWidth(DIMS["input_w_s"])
        self.trend_exit_confirm_bars_input.setFixedHeight(DIMS["input_h"])
        self.trend_exit_confirm_bars_input.setStyleSheet(Styles.SPINBOX)
        row.addLayout(_param_group("Bars", self.trend_exit_confirm_bars_input))

        row.addStretch()
        return row

    # ─────────────────────────────────────────────────────────────────────
    # BAND C — Automation · Order · Stacker · Timing · Status
    # ─────────────────────────────────────────────────────────────────────

    def _build_band_c(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)

        # ── AUTOMATE toggle (prominent) ──────────────────────────────────
        self._automate_btn = QPushButton("● AUTOMATE")
        self._automate_btn.setCheckable(True)
        self._automate_btn.setFixedHeight(DIMS["btn_h"])
        self._automate_btn.setMinimumWidth(110)
        self._automate_btn.setStyleSheet(Styles.BTN_AUTOMATE_OFF)
        self._automate_btn.toggled.connect(self._on_automate_btn_toggled)
        # Sync with existing automate_toggle
        self._automate_btn.setChecked(self.automate_toggle.isChecked())
        self.automate_toggle.toggled.connect(
            lambda c: self._automate_btn.setChecked(c)
        )
        row.addWidget(self._automate_btn)

        row.addWidget(_vline())

        # ── Order routing ────────────────────────────────────────────────
        row.addWidget(_section_label("ROUTE"))
        self.automation_route_combo.setFixedHeight(DIMS["input_h"])
        self.automation_route_combo.setFixedWidth(130)
        self.automation_route_combo.setStyleSheet(Styles.COMBO)
        row.addWidget(self.automation_route_combo)

        self.automation_order_type_combo.setFixedHeight(DIMS["input_h"])
        self.automation_order_type_combo.setFixedWidth(80)
        self.automation_order_type_combo.setStyleSheet(Styles.COMBO)
        row.addWidget(self.automation_order_type_combo)

        row.addWidget(_vline())

        # ── Trading time window ──────────────────────────────────────────
        row.addWidget(_section_label("WINDOW"))

        for spin in (
            self.automation_start_time_hour_input,
            self.automation_start_time_minute_input,
            self.automation_cutoff_time_hour_input,
            self.automation_cutoff_time_minute_input,
        ):
            spin.setFixedWidth(44)
            spin.setFixedHeight(DIMS["input_h"])
            spin.setStyleSheet(Styles.SPINBOX)

        row.addWidget(self.automation_start_time_hour_input)
        colon1 = QLabel(":")
        colon1.setStyleSheet(f"color:{THEME['text_secondary']}; font-weight:700;")
        row.addWidget(colon1)
        row.addWidget(self.automation_start_time_minute_input)

        dash = QLabel("—")
        dash.setStyleSheet(f"color:{THEME['text_muted']}; font-weight:400;")
        row.addWidget(dash)

        row.addWidget(self.automation_cutoff_time_hour_input)
        colon2 = QLabel(":")
        colon2.setStyleSheet(f"color:{THEME['text_secondary']}; font-weight:700;")
        row.addWidget(colon2)
        row.addWidget(self.automation_cutoff_time_minute_input)

        row.addWidget(_vline())

        # ── Stacker ──────────────────────────────────────────────────────
        self.stacker_enabled_check.setStyleSheet(f"""
            QCheckBox {{
                color: {THEME['accent_gold']};
                font-weight: 700;
                font-size: 11px;
                spacing: 4px;
            }}
            QCheckBox::indicator {{
                width: 13px; height: 13px;
                border: 1px solid {THEME['accent_gold']}66;
                border-radius: 2px;
                background: {THEME['bg_input']};
            }}
            QCheckBox::indicator:checked {{
                background: {THEME['accent_gold']};
                border-color: {THEME['accent_gold']};
            }}
        """)
        row.addWidget(self.stacker_enabled_check)

        self.stacker_step_input.setFixedWidth(72)
        self.stacker_step_input.setFixedHeight(DIMS["input_h"])
        self.stacker_step_input.setStyleSheet(Styles.SPINBOX)
        row.addLayout(_param_group("Step", self.stacker_step_input))

        self.stacker_max_input.setFixedWidth(52)
        self.stacker_max_input.setFixedHeight(DIMS["input_h"])
        self.stacker_max_input.setStyleSheet(Styles.SPINBOX)
        row.addLayout(_param_group("Max", self.stacker_max_input))

        row.addWidget(_vline())

        # ── Harvest ──────────────────────────────────────────────────────
        self.harvest_enabled_check.setStyleSheet(f"""
            QCheckBox {{
                color: {THEME['accent_green']};
                font-weight: 700;
                font-size: 11px;
                spacing: 4px;
            }}
            QCheckBox::indicator {{
                width: 13px; height: 13px;
                border: 1px solid {THEME['accent_green']}66;
                border-radius: 2px;
                background: {THEME['bg_input']};
            }}
            QCheckBox::indicator:checked {{
                background: {THEME['accent_green']};
                border-color: {THEME['accent_green']};
            }}
        """)
        row.addWidget(self.harvest_enabled_check)

        self.harvest_threshold_input.setFixedWidth(84)
        self.harvest_threshold_input.setFixedHeight(DIMS["input_h"])
        self.harvest_threshold_input.setStyleSheet(Styles.SPINBOX)
        row.addWidget(self.harvest_threshold_input)

        row.addWidget(_vline())

        # ── Simulator + CSV ──────────────────────────────────────────────
        self.simulator_run_btn.setFixedHeight(DIMS["btn_h"])
        self.simulator_run_btn.setStyleSheet(Styles.BTN)
        row.addWidget(self.simulator_run_btn)

        self.tick_upload_btn.setFixedHeight(DIMS["btn_h"])
        self.tick_upload_btn.setStyleSheet(Styles.BTN)
        row.addWidget(self.tick_upload_btn)

        self.tick_clear_btn.setFixedHeight(DIMS["btn_h"])
        self.tick_clear_btn.setStyleSheet(Styles.BTN)
        row.addWidget(self.tick_clear_btn)

        row.addWidget(_vline())

        # ── Live regime + status ─────────────────────────────────────────
        row.addWidget(self.regime_indicator)

        row.addStretch()

        # ── Simulator summary (right-aligned) ────────────────────────────
        self.simulator_summary_label.setStyleSheet(
            f"color: {THEME['text_secondary']}; font-size: 10px; font-weight: 600;"
        )
        row.addWidget(self.simulator_summary_label)

        return row

    # ─────────────────────────────────────────────────────────────────────
    # Automate button toggle handler
    # ─────────────────────────────────────────────────────────────────────

    def _on_automate_btn_toggled(self, checked: bool) -> None:
        """Keep the big Automate button and the legacy hidden checkbox in sync."""
        if checked:
            self._automate_btn.setStyleSheet(Styles.BTN_AUTOMATE_ON)
            self._automate_btn.setText("● LIVE  ")
        else:
            self._automate_btn.setStyleSheet(Styles.BTN_AUTOMATE_OFF)
            self._automate_btn.setText("● AUTOMATE")

        if self.automate_toggle.isChecked() != checked:
            self.automate_toggle.setChecked(checked)