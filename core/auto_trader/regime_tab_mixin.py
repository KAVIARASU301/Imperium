"""
Regime Tab Mixin — Setup Panel Extension
=========================================
Adds a "Regime" tab to the SetupPanelMixin's QTabWidget.

Drop this file into core/auto_trader/ and add the mixin to
AutoTraderDialog's inheritance chain BEFORE SetupPanelMixin:

    class AutoTraderDialog(
        RegimeTabMixin,
        SetupPanelMixin,
        ...
        QDialog,
    ):
        ...

Then in _build_setup_dialog(), after `tabs = QTabWidget(...)`, call:
    self._build_regime_tab(tabs, compact_spinbox_style, compact_combo_style)
"""

from __future__ import annotations

from datetime import time as dtime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QFrame,
    QTimeEdit,
    QGridLayout,
)
from PySide6.QtCore import QTime

# colour tokens (match setup_panel.py)
_C_BG        = "#161A25"
_C_BORDER    = "#3A4458"
_C_GRP_TITLE = "#9CCAF4"
_C_LABEL     = "#B0B0B0"
_C_NOTE      = "#8A9BA8"
_C_BTN_BG    = "#2A2F3D"
_C_BTN_TEXT  = "#E0E0E0"
_INPUT_W     = 80
_COMBO_W     = 140


class RegimeTabMixin:
    """
    Adds the Regime configuration tab to the setup dialog.

    Public widgets (read by _persist_setup_values / _load_setup_values):
        self.regime_enabled_check          — master enable
        self.regime_adx_strong_input       — ADX strong trend threshold
        self.regime_adx_weak_input         — ADX weak trend threshold
        self.regime_adx_confirm_input      — confirmation bars (trend)
        self.regime_atr_window_input       — rolling ATR window
        self.regime_atr_high_input         — high vol ratio
        self.regime_atr_low_input          — low vol ratio
        self.regime_vol_confirm_input      — confirmation bars (vol)
        self.regime_open_drive_end_input   — QTimeEdit
        self.regime_morning_end_input
        self.regime_midday_end_input
        self.regime_afternoon_end_input
        self.regime_pre_close_end_input
        self.regime_matrix_checks          — dict[(trend, vol, strategy)] → QCheckBox
    """

    # Strategy keys displayed in matrix
    _REGIME_STRATEGIES = ("atr_reversal", "atr_divergence", "ema_cross", "range_breakout")
    _REGIME_STRATEGY_LABELS = {
        "atr_reversal":   "ATR Reversal",
        "atr_divergence": "ATR Divergence",
        "ema_cross":      "EMA Cross",
        "range_breakout": "Range Breakout",
    }
    _TREND_REGIMES = ("STRONG_TREND", "WEAK_TREND", "CHOP")
    _VOL_REGIMES   = ("HIGH_VOL", "NORMAL_VOL", "LOW_VOL")

    def _build_regime_tab(
        self,
        tabs: QTabWidget,
        compact_spinbox_style: str,
        compact_combo_style: str,
    ):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # ── helpers ──────────────────────────────────────────────────────────
        def _w(widget, w=_INPUT_W):
            widget.setFixedWidth(w)
            return widget

        def _note(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color:{_C_NOTE}; font-size:9px; font-weight:400;")
            lbl.setWordWrap(True)
            return lbl

        def _group(title):
            grp = QGroupBox(title)
            frm = QFormLayout(grp)
            frm.setContentsMargins(7, 5, 7, 5)
            frm.setVerticalSpacing(4)
            frm.setHorizontalSpacing(8)
            return grp, frm

        # ═════════════════════════════════════════════════════════════════
        # ROW 0 — Master enable + live indicator
        # ═════════════════════════════════════════════════════════════════
        top_row = QHBoxLayout()

        self.regime_enabled_check = QCheckBox("Enable Regime-Aware Strategy Routing")
        self.regime_enabled_check.setChecked(True)
        self.regime_enabled_check.setStyleSheet(f"color:{_C_BTN_TEXT}; font-weight:700; font-size:11px;")
        self.regime_enabled_check.setToolTip(
            "When enabled, the regime engine controls which strategies are allowed to fire.\n"
            "Disable to revert to the previous always-on behaviour."
        )
        self.regime_enabled_check.toggled.connect(self._on_regime_settings_changed)
        top_row.addWidget(self.regime_enabled_check)
        top_row.addStretch()

        # Live regime badge (updated by _update_regime_label)
        self.regime_live_badge = QLabel("◉  REGIME: —")
        self.regime_live_badge.setStyleSheet(
            f"color:#8A99B3; font-size:10px; font-weight:700; "
            f"background:#1E2535; border:1px solid #3A4458; "
            f"border-radius:4px; padding:2px 8px;"
        )
        top_row.addWidget(self.regime_live_badge)
        main_layout.addLayout(top_row)

        # ═════════════════════════════════════════════════════════════════
        # ROW 1 — three columns: Trend · Vol · Session
        # ═════════════════════════════════════════════════════════════════
        cols_row = QHBoxLayout()
        cols_row.setSpacing(8)

        # ── COLUMN 1: Trend Regime ────────────────────────────────────────
        trend_grp, trend_frm = _group("Trend Regime (ADX)")

        trend_frm.addRow(_note(
            "ADX thresholds define STRONG_TREND / WEAK_TREND / CHOP. "
            "Confirmation bars prevent single-bar flip-flop."
        ))

        self.regime_adx_strong_input = QDoubleSpinBox()
        self.regime_adx_strong_input.setRange(15.0, 50.0)
        self.regime_adx_strong_input.setDecimals(1)
        self.regime_adx_strong_input.setSingleStep(1.0)
        self.regime_adx_strong_input.setValue(28.0)
        self.regime_adx_strong_input.setStyleSheet(compact_spinbox_style)
        self.regime_adx_strong_input.setToolTip(
            "ADX ≥ this value → STRONG TREND.\n"
            "In strong trend: ATR Reversal disabled (fighting trend).\n"
            "Recommended: 25–30."
        )
        self.regime_adx_strong_input.valueChanged.connect(self._on_regime_settings_changed)
        _w(self.regime_adx_strong_input)
        trend_frm.addRow("Strong Trend ≥", self.regime_adx_strong_input)

        self.regime_adx_weak_input = QDoubleSpinBox()
        self.regime_adx_weak_input.setRange(10.0, 40.0)
        self.regime_adx_weak_input.setDecimals(1)
        self.regime_adx_weak_input.setSingleStep(1.0)
        self.regime_adx_weak_input.setValue(20.0)
        self.regime_adx_weak_input.setStyleSheet(compact_spinbox_style)
        self.regime_adx_weak_input.setToolTip(
            "ADX between weak and strong → WEAK TREND.\n"
            "Below this → CHOP.\n"
            "Recommended: 18–22."
        )
        self.regime_adx_weak_input.valueChanged.connect(self._on_regime_settings_changed)
        _w(self.regime_adx_weak_input)
        trend_frm.addRow("Weak Trend ≥", self.regime_adx_weak_input)

        self.regime_adx_confirm_input = QSpinBox()
        self.regime_adx_confirm_input.setRange(1, 10)
        self.regime_adx_confirm_input.setValue(3)
        self.regime_adx_confirm_input.setStyleSheet(compact_spinbox_style)
        self.regime_adx_confirm_input.setToolTip(
            "Number of consecutive bars the ADX must stay in a new regime\n"
            "before the regime is officially changed.\n"
            "Higher = less flip-flopping. Recommended: 3."
        )
        self.regime_adx_confirm_input.valueChanged.connect(self._on_regime_settings_changed)
        _w(self.regime_adx_confirm_input)
        trend_frm.addRow("Confirm Bars", self.regime_adx_confirm_input)

        cols_row.addWidget(trend_grp)

        # ── COLUMN 2: Volatility Regime ───────────────────────────────────
        vol_grp, vol_frm = _group("Volatility Regime (ATR Ratio)")

        vol_frm.addRow(_note(
            "ATR ratio = current ATR / rolling session ATR average. "
            "Values above/below thresholds classify HIGH/LOW vol."
        ))

        self.regime_atr_window_input = QSpinBox()
        self.regime_atr_window_input.setRange(5, 120)
        self.regime_atr_window_input.setValue(30)
        self.regime_atr_window_input.setSuffix(" bars")
        self.regime_atr_window_input.setStyleSheet(compact_spinbox_style)
        self.regime_atr_window_input.setToolTip(
            "Rolling window (bars) for ATR baseline calculation.\n"
            "Longer = smoother baseline, slower to adapt.\n"
            "Recommended: 20–40."
        )
        self.regime_atr_window_input.valueChanged.connect(self._on_regime_settings_changed)
        _w(self.regime_atr_window_input, 90)
        vol_frm.addRow("Rolling Window", self.regime_atr_window_input)

        self.regime_atr_high_input = QDoubleSpinBox()
        self.regime_atr_high_input.setRange(1.1, 3.0)
        self.regime_atr_high_input.setDecimals(2)
        self.regime_atr_high_input.setSingleStep(0.05)
        self.regime_atr_high_input.setValue(1.50)
        self.regime_atr_high_input.setStyleSheet(compact_spinbox_style)
        self.regime_atr_high_input.setToolTip(
            "ATR ratio above this → HIGH VOL.\n"
            "Breakout strategy disabled in high vol (false breakouts).\n"
            "Recommended: 1.4–1.6."
        )
        self.regime_atr_high_input.valueChanged.connect(self._on_regime_settings_changed)
        _w(self.regime_atr_high_input)
        vol_frm.addRow("High Vol Ratio ≥", self.regime_atr_high_input)

        self.regime_atr_low_input = QDoubleSpinBox()
        self.regime_atr_low_input.setRange(0.3, 1.0)
        self.regime_atr_low_input.setDecimals(2)
        self.regime_atr_low_input.setSingleStep(0.05)
        self.regime_atr_low_input.setValue(0.70)
        self.regime_atr_low_input.setStyleSheet(compact_spinbox_style)
        self.regime_atr_low_input.setToolTip(
            "ATR ratio below this → LOW VOL.\n"
            "Most strategies disabled in low vol (thin market, no follow-through).\n"
            "Recommended: 0.6–0.75."
        )
        self.regime_atr_low_input.valueChanged.connect(self._on_regime_settings_changed)
        _w(self.regime_atr_low_input)
        vol_frm.addRow("Low Vol Ratio ≤", self.regime_atr_low_input)

        self.regime_vol_confirm_input = QSpinBox()
        self.regime_vol_confirm_input.setRange(1, 8)
        self.regime_vol_confirm_input.setValue(2)
        self.regime_vol_confirm_input.setStyleSheet(compact_spinbox_style)
        self.regime_vol_confirm_input.setToolTip(
            "Consecutive bars to confirm a volatility regime change.\n"
            "Recommended: 2."
        )
        self.regime_vol_confirm_input.valueChanged.connect(self._on_regime_settings_changed)
        _w(self.regime_vol_confirm_input)
        vol_frm.addRow("Confirm Bars", self.regime_vol_confirm_input)

        cols_row.addWidget(vol_grp)

        # ── COLUMN 3: Session Phases ──────────────────────────────────────
        sess_grp, sess_frm = _group("Session Phase Boundaries (IST)")

        sess_frm.addRow(_note(
            "Defines the time boundaries for each session phase. "
            "OPEN_DRIVE: only Open Drive fires. PRE_CLOSE: most strategies suppressed."
        ))

        def _time_input(default_h, default_m):
            w = QTimeEdit()
            w.setDisplayFormat("HH:mm")
            w.setTime(QTime(default_h, default_m))
            w.setStyleSheet(compact_spinbox_style)
            w.setFixedWidth(75)
            w.timeChanged.connect(self._on_regime_settings_changed)
            return w

        self.regime_open_drive_end_input  = _time_input(9, 30)
        self.regime_morning_end_input     = _time_input(11, 30)
        self.regime_midday_end_input      = _time_input(13, 30)
        self.regime_afternoon_end_input   = _time_input(15, 0)
        self.regime_pre_close_end_input   = _time_input(15, 30)

        self.regime_open_drive_end_input.setToolTip(
            "Before this time → OPEN DRIVE phase.\n"
            "Only the Open Drive strategy fires. All others locked."
        )
        self.regime_morning_end_input.setToolTip(
            "OPEN_DRIVE end → this time = MORNING.\n"
            "Best quality signals of the day."
        )
        self.regime_midday_end_input.setToolTip("MORNING end → this time = MIDDAY.")
        self.regime_afternoon_end_input.setToolTip("MIDDAY end → this time = AFTERNOON.")
        self.regime_pre_close_end_input.setToolTip(
            "After this time → PRE_CLOSE.\n"
            "Most strategies suppressed."
        )

        sess_frm.addRow("Open Drive ends", self.regime_open_drive_end_input)
        sess_frm.addRow("Morning ends",    self.regime_morning_end_input)
        sess_frm.addRow("Midday ends",     self.regime_midday_end_input)
        sess_frm.addRow("Afternoon ends",  self.regime_afternoon_end_input)
        sess_frm.addRow("Pre-close ends",  self.regime_pre_close_end_input)

        cols_row.addWidget(sess_grp)
        main_layout.addLayout(cols_row)

        # ═════════════════════════════════════════════════════════════════
        # ROW 2 — Strategy enable matrix
        # ═════════════════════════════════════════════════════════════════
        matrix_grp = QGroupBox("Strategy Enable Matrix (per Trend + Volatility Regime)")
        matrix_layout = QVBoxLayout(matrix_grp)
        matrix_layout.setContentsMargins(7, 5, 7, 5)
        matrix_layout.addWidget(_note(
            "Controls which strategies are ALLOWED for every Trend × Volatility regime combination. "
            "These checkboxes directly power RegimeEngine.allowed_strategies."
        ))

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(6)

        # Header rows
        for block_idx, vol in enumerate(self._VOL_REGIMES):
            col_start = 1 + block_idx * len(self._TREND_REGIMES)
            vol_lbl = QLabel(vol.replace("_", " "))
            vol_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vol_lbl.setStyleSheet(f"color:{_C_GRP_TITLE}; font-size:10px; font-weight:700;")
            grid.addWidget(vol_lbl, 0, col_start, 1, len(self._TREND_REGIMES))

            for trend_offset, trend in enumerate(self._TREND_REGIMES):
                col = col_start + trend_offset
                trend_lbl = QLabel(trend.replace("_", " "))
                trend_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                trend_lbl.setStyleSheet(f"color:{_C_NOTE}; font-size:9px; font-weight:700;")
                grid.addWidget(trend_lbl, 1, col)

        self.regime_matrix_checks: dict[tuple[str, str, str], QCheckBox] = {}

        _defaults = {
            ("STRONG_TREND", "NORMAL_VOL", "atr_reversal"): False,
            ("STRONG_TREND", "NORMAL_VOL", "atr_divergence"): True,
            ("STRONG_TREND", "NORMAL_VOL", "ema_cross"): True,
            ("STRONG_TREND", "NORMAL_VOL", "range_breakout"): True,
            ("STRONG_TREND", "HIGH_VOL", "atr_reversal"): False,
            ("STRONG_TREND", "HIGH_VOL", "atr_divergence"): True,
            ("STRONG_TREND", "HIGH_VOL", "ema_cross"): True,
            ("STRONG_TREND", "HIGH_VOL", "range_breakout"): False,
            ("STRONG_TREND", "LOW_VOL", "atr_reversal"): False,
            ("STRONG_TREND", "LOW_VOL", "atr_divergence"): True,
            ("STRONG_TREND", "LOW_VOL", "ema_cross"): True,
            ("STRONG_TREND", "LOW_VOL", "range_breakout"): True,
            ("WEAK_TREND", "NORMAL_VOL", "atr_reversal"): True,
            ("WEAK_TREND", "NORMAL_VOL", "atr_divergence"): True,
            ("WEAK_TREND", "NORMAL_VOL", "ema_cross"): True,
            ("WEAK_TREND", "NORMAL_VOL", "range_breakout"): True,
            ("WEAK_TREND", "HIGH_VOL", "atr_reversal"): True,
            ("WEAK_TREND", "HIGH_VOL", "atr_divergence"): True,
            ("WEAK_TREND", "HIGH_VOL", "ema_cross"): True,
            ("WEAK_TREND", "HIGH_VOL", "range_breakout"): False,
            ("WEAK_TREND", "LOW_VOL", "atr_reversal"): True,
            ("WEAK_TREND", "LOW_VOL", "atr_divergence"): False,
            ("WEAK_TREND", "LOW_VOL", "ema_cross"): False,
            ("WEAK_TREND", "LOW_VOL", "range_breakout"): False,
            ("CHOP", "NORMAL_VOL", "atr_reversal"): True,
            ("CHOP", "NORMAL_VOL", "atr_divergence"): False,
            ("CHOP", "NORMAL_VOL", "ema_cross"): False,
            ("CHOP", "NORMAL_VOL", "range_breakout"): False,
            ("CHOP", "HIGH_VOL", "atr_reversal"): True,
            ("CHOP", "HIGH_VOL", "atr_divergence"): False,
            ("CHOP", "HIGH_VOL", "ema_cross"): False,
            ("CHOP", "HIGH_VOL", "range_breakout"): False,
            ("CHOP", "LOW_VOL", "atr_reversal"): False,
            ("CHOP", "LOW_VOL", "atr_divergence"): False,
            ("CHOP", "LOW_VOL", "ema_cross"): False,
            ("CHOP", "LOW_VOL", "range_breakout"): False,
        }

        for row, strategy in enumerate(self._REGIME_STRATEGIES, start=2):
            lbl = QLabel(self._REGIME_STRATEGY_LABELS[strategy])
            lbl.setStyleSheet(f"color:{_C_LABEL}; font-size:10px;")
            grid.addWidget(lbl, row, 0)

            for block_idx, vol in enumerate(self._VOL_REGIMES):
                col_start = 1 + block_idx * len(self._TREND_REGIMES)
                for trend_offset, trend in enumerate(self._TREND_REGIMES):
                    col = col_start + trend_offset
                    cb = QCheckBox()
                    cb.setChecked(_defaults.get((trend, vol, strategy), True))
                    cb.setStyleSheet("""
                        QCheckBox::indicator { width:14px; height:14px; }
                        QCheckBox::indicator:unchecked { background:#1B1F2B; border:1px solid #3A4458; border-radius:3px; }
                        QCheckBox::indicator:checked { background:#26A69A; border:1px solid #26A69A; border-radius:3px; }
                    """)
                    cb.toggled.connect(self._on_regime_settings_changed)
                    cb.setToolTip(f"Allow {self._REGIME_STRATEGY_LABELS[strategy]} in {trend.replace('_',' ')} + {vol.replace('_', ' ')}")
                    self.regime_matrix_checks[(trend, vol, strategy)] = cb
                    grid.addWidget(cb, row, col, alignment=Qt.AlignmentFlag.AlignCenter)

        matrix_layout.addLayout(grid)
        main_layout.addWidget(matrix_grp)

        # ═════════════════════════════════════════════════════════════════
        # ROW 3 — Reset button
        # ═════════════════════════════════════════════════════════════════
        reset_row = QHBoxLayout()
        reset_row.addStretch()
        reset_btn = QPushButton("Reset Regime Defaults")
        reset_btn.setFixedWidth(160)
        reset_btn.setStyleSheet(f"""
            QPushButton {{
                background:{_C_BTN_BG}; color:{_C_BTN_TEXT};
                border:1px solid {_C_BORDER}; border-radius:4px;
                font-size:10px; padding:4px 8px;
            }}
            QPushButton:hover {{ border-color:#5B9BD5; }}
        """)
        reset_btn.clicked.connect(self._reset_regime_defaults)
        reset_row.addWidget(reset_btn)
        main_layout.addLayout(reset_row)

        main_layout.addStretch()
        scroll.setWidget(container)
        tabs.addTab(scroll, "Regime")

    # ── Handlers called by the dialog ─────────────────────────────────────────

    def _on_regime_settings_changed(self, *_):
        """Rebuild RegimeConfig from UI and push to regime_engine."""
        if not getattr(self, "_setup_values_ready", False):
            return
        self._apply_regime_config()
        self._persist_setup_values()

    def _apply_regime_config(self):
        """Build RegimeConfig from current UI state and update engine."""
        if not hasattr(self, "regime_engine"):
            return
        from core.auto_trader.regime_engine import RegimeConfig
        from datetime import time as dtime

        def _qt_to_dtime(qtime):
            return dtime(qtime.hour(), qtime.minute())

        # Build strategy matrix from checkboxes (trend + volatility specific)
        matrix = {}
        for vol in self._VOL_REGIMES:
            for trend in self._TREND_REGIMES:
                row = {}
                for strategy in self._REGIME_STRATEGIES:
                    cb = self.regime_matrix_checks.get((trend, vol, strategy))
                    row[strategy] = cb.isChecked() if cb else True
                matrix[(trend, vol)] = row

        config = RegimeConfig(
            adx_strong_trend=float(self.regime_adx_strong_input.value()),
            adx_weak_trend=float(self.regime_adx_weak_input.value()),
            adx_confirmation_bars=int(self.regime_adx_confirm_input.value()),
            atr_rolling_window=int(self.regime_atr_window_input.value()),
            atr_high_vol_ratio=float(self.regime_atr_high_input.value()),
            atr_low_vol_ratio=float(self.regime_atr_low_input.value()),
            vol_confirmation_bars=int(self.regime_vol_confirm_input.value()),
            open_drive_end=_qt_to_dtime(self.regime_open_drive_end_input.time()),
            morning_end=_qt_to_dtime(self.regime_morning_end_input.time()),
            midday_end=_qt_to_dtime(self.regime_midday_end_input.time()),
            afternoon_end=_qt_to_dtime(self.regime_afternoon_end_input.time()),
            pre_close_end=_qt_to_dtime(self.regime_pre_close_end_input.time()),
            strategy_matrix=matrix,
        )
        self.regime_engine.update_config(config)

    def _reset_regime_defaults(self):
        """Reset all regime UI to default values."""
        self.regime_enabled_check.setChecked(True)
        self.regime_adx_strong_input.setValue(28.0)
        self.regime_adx_weak_input.setValue(20.0)
        self.regime_adx_confirm_input.setValue(3)
        self.regime_atr_window_input.setValue(30)
        self.regime_atr_high_input.setValue(1.50)
        self.regime_atr_low_input.setValue(0.70)
        self.regime_vol_confirm_input.setValue(2)

        from PySide6.QtCore import QTime
        self.regime_open_drive_end_input.setTime(QTime(9, 30))
        self.regime_morning_end_input.setTime(QTime(11, 30))
        self.regime_midday_end_input.setTime(QTime(13, 30))
        self.regime_afternoon_end_input.setTime(QTime(15, 0))
        self.regime_pre_close_end_input.setTime(QTime(15, 30))

        _defaults = {
            ("STRONG_TREND", "NORMAL_VOL", "atr_reversal"): False,
            ("STRONG_TREND", "NORMAL_VOL", "atr_divergence"): True,
            ("STRONG_TREND", "NORMAL_VOL", "ema_cross"): True,
            ("STRONG_TREND", "NORMAL_VOL", "range_breakout"): True,
            ("STRONG_TREND", "HIGH_VOL", "atr_reversal"): False,
            ("STRONG_TREND", "HIGH_VOL", "atr_divergence"): True,
            ("STRONG_TREND", "HIGH_VOL", "ema_cross"): True,
            ("STRONG_TREND", "HIGH_VOL", "range_breakout"): False,
            ("STRONG_TREND", "LOW_VOL", "atr_reversal"): False,
            ("STRONG_TREND", "LOW_VOL", "atr_divergence"): True,
            ("STRONG_TREND", "LOW_VOL", "ema_cross"): True,
            ("STRONG_TREND", "LOW_VOL", "range_breakout"): True,
            ("WEAK_TREND", "NORMAL_VOL", "atr_reversal"): True,
            ("WEAK_TREND", "NORMAL_VOL", "atr_divergence"): True,
            ("WEAK_TREND", "NORMAL_VOL", "ema_cross"): True,
            ("WEAK_TREND", "NORMAL_VOL", "range_breakout"): True,
            ("WEAK_TREND", "HIGH_VOL", "atr_reversal"): True,
            ("WEAK_TREND", "HIGH_VOL", "atr_divergence"): True,
            ("WEAK_TREND", "HIGH_VOL", "ema_cross"): True,
            ("WEAK_TREND", "HIGH_VOL", "range_breakout"): False,
            ("WEAK_TREND", "LOW_VOL", "atr_reversal"): True,
            ("WEAK_TREND", "LOW_VOL", "atr_divergence"): False,
            ("WEAK_TREND", "LOW_VOL", "ema_cross"): False,
            ("WEAK_TREND", "LOW_VOL", "range_breakout"): False,
            ("CHOP", "NORMAL_VOL", "atr_reversal"): True,
            ("CHOP", "NORMAL_VOL", "atr_divergence"): False,
            ("CHOP", "NORMAL_VOL", "ema_cross"): False,
            ("CHOP", "NORMAL_VOL", "range_breakout"): False,
            ("CHOP", "HIGH_VOL", "atr_reversal"): True,
            ("CHOP", "HIGH_VOL", "atr_divergence"): False,
            ("CHOP", "HIGH_VOL", "ema_cross"): False,
            ("CHOP", "HIGH_VOL", "range_breakout"): False,
            ("CHOP", "LOW_VOL", "atr_reversal"): False,
            ("CHOP", "LOW_VOL", "atr_divergence"): False,
            ("CHOP", "LOW_VOL", "ema_cross"): False,
            ("CHOP", "LOW_VOL", "range_breakout"): False,
        }
        for (trend, vol, strategy), cb in self.regime_matrix_checks.items():
            cb.setChecked(_defaults.get((trend, vol, strategy), True))

    def update_regime_badge(self, regime):
        """
        Called from the main dialog whenever a new regime is computed.
        `regime` is a MarketRegime instance.
        """
        if not hasattr(self, "regime_live_badge"):
            return
        if regime is None:
            self.regime_live_badge.setText("◉  REGIME: —")
            self.regime_live_badge.setStyleSheet(
                "color:#8A99B3; font-size:10px; font-weight:700; "
                "background:#1E2535; border:1px solid #3A4458; "
                "border-radius:4px; padding:2px 8px;"
            )
            return

        trend_colors = {
            "STRONG_TREND": "#00E676",
            "WEAK_TREND":   "#FFB300",
            "CHOP":         "#FF4D4D",
        }
        vol_colors = {
            "HIGH_VOL":   "#FF4D4D",
            "NORMAL_VOL": "#4D9FFF",
            "LOW_VOL":    "#8A99B3",
        }
        trend_c = trend_colors.get(regime.trend, "#8A99B3")
        vol_c   = vol_colors.get(regime.volatility, "#8A99B3")

        trend_icons = {"STRONG_TREND": "▲▲", "WEAK_TREND": "▲", "CHOP": "↔"}
        vol_icons   = {"HIGH_VOL": "🔥", "NORMAL_VOL": "●", "LOW_VOL": "❄"}

        t_icon = trend_icons.get(regime.trend, "")
        v_icon = vol_icons.get(regime.volatility, "")

        text = (
            f"◉  {t_icon} <span style='color:{trend_c}'>{regime.trend.replace('_',' ')}</span>"
            f"  {v_icon} <span style='color:{vol_c}'>{regime.volatility.replace('_',' ')}</span>"
            f"  │  <span style='color:#8A99B3'>{regime.session.replace('_',' ')}</span>"
            f"  │  ADX <span style='color:{trend_c}'>{regime.adx_value:.1f}</span>"
        )
        self.regime_live_badge.setText(text)
        self.regime_live_badge.setTextFormat(Qt.TextFormat.RichText)
        self.regime_live_badge.setStyleSheet(
            f"color:{trend_c}; font-size:10px; font-weight:700; "
            f"background:#1E2535; border:1px solid {trend_c}40; "
            f"border-radius:4px; padding:2px 10px;"
        )

    # ── Persistence helpers (called from _persist_setup_values) ───────────────

    def _regime_settings_to_dict(self) -> dict:
        """Return all regime UI values as a flat dict for QSettings persistence."""
        from PySide6.QtCore import QTime

        d = {
            "regime_enabled":          self.regime_enabled_check.isChecked(),
            "regime_adx_strong":       float(self.regime_adx_strong_input.value()),
            "regime_adx_weak":         float(self.regime_adx_weak_input.value()),
            "regime_adx_confirm":      int(self.regime_adx_confirm_input.value()),
            "regime_atr_window":       int(self.regime_atr_window_input.value()),
            "regime_atr_high":         float(self.regime_atr_high_input.value()),
            "regime_atr_low":          float(self.regime_atr_low_input.value()),
            "regime_vol_confirm":      int(self.regime_vol_confirm_input.value()),
            "regime_open_drive_end":   self.regime_open_drive_end_input.time().toString("HH:mm"),
            "regime_morning_end":      self.regime_morning_end_input.time().toString("HH:mm"),
            "regime_midday_end":       self.regime_midday_end_input.time().toString("HH:mm"),
            "regime_afternoon_end":    self.regime_afternoon_end_input.time().toString("HH:mm"),
            "regime_pre_close_end":    self.regime_pre_close_end_input.time().toString("HH:mm"),
        }
        for (trend, vol, strategy), cb in self.regime_matrix_checks.items():
            d[f"regime_matrix_{trend}_{vol}_{strategy}"] = cb.isChecked()
        return d

    def _regime_settings_from_dict(self, d: dict):
        """Restore regime UI from a flat dict (loaded from QSettings)."""
        from PySide6.QtCore import QTime

        def _qtime(s, default_h, default_m):
            try:
                parts = str(s).split(":")
                return QTime(int(parts[0]), int(parts[1]))
            except Exception:
                return QTime(default_h, default_m)

        self.regime_enabled_check.setChecked(bool(d.get("regime_enabled", True)))
        self.regime_adx_strong_input.setValue(float(d.get("regime_adx_strong", 28.0)))
        self.regime_adx_weak_input.setValue(float(d.get("regime_adx_weak", 20.0)))
        self.regime_adx_confirm_input.setValue(int(d.get("regime_adx_confirm", 3)))
        self.regime_atr_window_input.setValue(int(d.get("regime_atr_window", 30)))
        self.regime_atr_high_input.setValue(float(d.get("regime_atr_high", 1.5)))
        self.regime_atr_low_input.setValue(float(d.get("regime_atr_low", 0.70)))
        self.regime_vol_confirm_input.setValue(int(d.get("regime_vol_confirm", 2)))

        self.regime_open_drive_end_input.setTime(_qtime(d.get("regime_open_drive_end", "09:30"), 9, 30))
        self.regime_morning_end_input.setTime(_qtime(d.get("regime_morning_end", "11:30"), 11, 30))
        self.regime_midday_end_input.setTime(_qtime(d.get("regime_midday_end", "13:30"), 13, 30))
        self.regime_afternoon_end_input.setTime(_qtime(d.get("regime_afternoon_end", "15:00"), 15, 0))
        self.regime_pre_close_end_input.setTime(_qtime(d.get("regime_pre_close_end", "15:30"), 15, 30))

        for (trend, vol, strategy), cb in self.regime_matrix_checks.items():
            key = f"regime_matrix_{trend}_{vol}_{strategy}"
            legacy_key = f"regime_matrix_{trend}_{strategy}"
            value = d.get(key, d.get(legacy_key, None))
            if value is not None:
                cb.blockSignals(True)
                cb.setChecked(bool(value))
                cb.blockSignals(False)