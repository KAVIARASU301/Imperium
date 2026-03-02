"""
atr_scanner_panel.py
====================
Minimal institutional-style UI for the multi-symbol ATR reversal scanner.

Two columns:
  LEFT  — Watchlist: symbols under watch + their status
  RIGHT — Signal feed: live ATR reversal alerts (newest on top)

No charts. No CVD plots. Pure signal intelligence.

Institutional UI principle: "Terminal-style density"
Bloomberg/Refinitiv terminals show maximum information in minimum space.
Colors are semantic: green = long, red = short, grey = inactive.
Every row is scannable in < 1 second.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import date
from datetime import timedelta
from typing import Any, Optional
import json

import numpy as np
import pandas as pd

from PySide6.QtCore import Qt, QTimer, Signal, Slot, QDate
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QDoubleSpinBox,
    QDateEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QAbstractItemView,
    QSplitter,
    QGroupBox,
    QFormLayout,
    QCheckBox,
)
from PySide6.QtCore import QSettings

from core.auto_trader.multi_symbol_engine import AtrSignalEvent, MultiSymbolEngine
from core.auto_trader.atr_signal_router import AtrSignalRouter
from core.auto_trader.indicators import calculate_atr, calculate_ema, calculate_vwap, calculate_cvd_zscore
from core.auto_trader.strategy_signal_detector import StrategySignalDetector
from core.cvd.cvd_historical import CVDHistoricalBuilder

logger = logging.getLogger(__name__)

# ── Color tokens ─────────────────────────────────────────────────────────────
C_BG        = "#0D0F17"
C_PANEL     = "#13161F"
C_BORDER    = "#1E2535"
C_TEXT      = "#D0D4E0"
C_MUTED     = "#5A6070"
C_LONG      = "#00C896"   # institutional green — profit/long
C_SHORT     = "#FF4560"   # institutional red — loss/short
C_WARN      = "#FFB800"
C_ACCENT    = "#4A9EFF"
C_CHOP      = "#FF6B35"   # orange = chop-filtered signal

STATUS_LABELS = {
    "watching": "MONITORING",
    "fetching": "FETCHING DATA",
    "warming_up": "WARMING UP",
    "data_connected": "DATA LIVE",
    "error": "⚠ ERROR",
    "no_data": "NO DATA",
    "started": "STARTING",
    "queued": "QUEUED",
    "watchdog_restart": "RESTARTING",
}

STATUS_COLORS = {
    "MONITORING": C_LONG,
    "DATA LIVE": C_ACCENT,
    "FETCHING DATA": C_ACCENT,
    "WARMING UP": C_WARN,
    "⚠ ERROR": C_SHORT,
    "NO DATA": C_MUTED,
    "STARTING": C_MUTED,
    "QUEUED": C_MUTED,
    "RESTARTING": C_WARN,
}

BASE_STYLE = f"""
    QWidget {{
        background: {C_BG};
        color: {C_TEXT};
        font-family: 'Consolas', 'JetBrains Mono', monospace;
        font-size: 12px;
    }}
    QGroupBox {{
        border: 1px solid {C_BORDER};
        border-radius: 4px;
        margin-top: 8px;
        padding: 8px 4px 4px 4px;
        font-size: 11px;
        color: {C_MUTED};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 8px;
        padding: 0 4px;
        color: {C_MUTED};
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    QTableWidget {{
        background: {C_PANEL};
        border: 1px solid {C_BORDER};
        gridline-color: {C_BORDER};
        selection-background-color: #1E2D40;
    }}
    QHeaderView::section {{
        background: {C_BG};
        color: {C_MUTED};
        border: none;
        border-bottom: 1px solid {C_BORDER};
        padding: 4px 8px;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {C_PANEL};
        border: 1px solid {C_BORDER};
        border-radius: 3px;
        color: {C_TEXT};
        padding: 3px 6px;
    }}
    QPushButton {{
        background: {C_PANEL};
        border: 1px solid {C_BORDER};
        border-radius: 3px;
        color: {C_TEXT};
        padding: 4px 12px;
    }}
    QPushButton:hover {{
        border-color: {C_ACCENT};
        color: {C_ACCENT};
    }}
    QPushButton#add_btn {{
        border-color: {C_LONG};
        color: {C_LONG};
    }}
    QPushButton#remove_btn {{
        border-color: {C_SHORT};
        color: {C_SHORT};
    }}
    QPushButton#clear_btn {{
        border-color: {C_MUTED};
        color: {C_MUTED};
    }}
"""


class AtrScannerPanel(QWidget):
    """
    Drop-in replacement for AutoTraderDialog in main_window.py.

    Integrates with MultiSymbolEngine.
    The main_window creates this widget and embeds it in center_stack at index 1.

    Usage in main_window.py:
        from core.auto_trader.atr_scanner_panel import AtrScannerPanel
        panel = AtrScannerPanel(kite=self.real_kite_client, parent=self)
        self.center_stack.insertWidget(1, panel)
    """

    # Max signals to keep in the feed (oldest auto-purge)
    MAX_SIGNAL_ROWS = 200

    def __init__(self, kite, parent=None):
        super().__init__(parent)
        self.kite = kite
        self._settings = QSettings("ImperiumDesk", "AtrScannerPanel")
        self._automation_enabled = False
        self._pending_watchlist: dict[str, dict[str, float | int]] = {}

        # Engine
        self.engine = MultiSymbolEngine(kite=kite, parent=self)
        self.engine.signal_fired.connect(self._on_signal)
        self.engine.symbol_status.connect(self._on_status_update)
        self.engine.symbol_error.connect(self._on_symbol_error)

        # Track token mapping: symbol → instrument_token
        self._symbol_tokens: dict[str, int] = {}
        self._instrument_data: dict[str, dict[str, Any]] = {}
        self._signal_router: Optional[AtrSignalRouter] = None

        self.setStyleSheet(BASE_STYLE)
        self._setup_ui()

        # Uptime clock
        self._uptime_timer = QTimer(self)
        self._uptime_timer.setInterval(1000)
        self._uptime_timer.timeout.connect(self._tick_uptime)
        self._start_time = datetime.now()
        self._uptime_timer.start()
        self._load_setup_state()
        self._apply_automation_state(initial=True)

    # ── UI Construction ────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        root.addWidget(self._build_header())

        # Dedicated setup dialog (opened from header button)
        self._setup_dialog = self._build_setup_dialog()

        # Main content: signal feed only (setup moved to dialog)
        root.addWidget(self._build_signal_panel(), 1)
        root.addWidget(self._build_status_bar())

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setFixedHeight(48)
        header.setStyleSheet(f"""
            QFrame {{
                background: {C_PANEL};
                border-bottom: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 0, 16, 0)

        # Title
        title = QLabel("SIGNAL ENGINE — ATR REVERSAL")
        title.setStyleSheet(f"color: {C_TEXT}; font-size: 13px; font-weight: 600; letter-spacing: 2px;")
        lay.addWidget(title)

        # Live dot
        self._live_dot = QLabel("●")
        self._live_dot.setStyleSheet(f"color: {C_LONG}; font-size: 12px;")
        lay.addWidget(self._live_dot)

        lay.addStretch()

        setup_btn = QPushButton("SETUP")
        setup_btn.setFixedWidth(80)
        setup_btn.clicked.connect(self._open_setup_dialog)
        lay.addWidget(setup_btn)

        self._automation_toggle = QCheckBox("AUTOMATE")
        self._automation_toggle.setChecked(False)
        self._automation_toggle.setToolTip("Enable to run scanner engine on saved watchlist")
        self._automation_toggle.toggled.connect(self._on_automation_toggled)
        lay.addWidget(self._automation_toggle)

        return header

    def _build_setup_dialog(self) -> QDialog:
        dlg = QDialog(self)
        dlg.setWindowTitle("SIGNAL ENGINE — STRATEGY PARAMETERS")
        dlg.resize(460, 620)
        dlg.setModal(False)
        dlg.finished.connect(lambda _: self._clear_signals())

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        setup_grp = QGroupBox("Setup Panel")
        setup_lay = QVBoxLayout(setup_grp)
        setup_lay.setContentsMargins(6, 6, 6, 6)
        setup_lay.setSpacing(8)

        # ── Add symbol form ───────────────────────────────────────────────
        add_grp = QGroupBox("Symbol Setup")
        form_lay = QFormLayout(add_grp)
        form_lay.setLabelAlignment(Qt.AlignRight)
        form_lay.setSpacing(6)

        self._symbol_selector = QComboBox()
        self._symbol_selector.setPlaceholderText("Select symbol")
        form_lay.addRow("Symbol:", self._symbol_selector)

        # Strategy params
        self._atr_distance_spin = QDoubleSpinBox()
        self._atr_distance_spin.setRange(0.5, 5.0)
        self._atr_distance_spin.setSingleStep(0.1)
        self._atr_distance_spin.setValue(1.5)
        self._atr_distance_spin.setDecimals(1)
        form_lay.addRow("ATR Dist:", self._atr_distance_spin)

        self._cvd_zscore_spin = QDoubleSpinBox()
        self._cvd_zscore_spin.setRange(0.5, 4.0)
        self._cvd_zscore_spin.setSingleStep(0.1)
        self._cvd_zscore_spin.setValue(1.5)
        self._cvd_zscore_spin.setDecimals(1)
        form_lay.addRow("CVD Z-Score:", self._cvd_zscore_spin)

        self._tf_spin = QSpinBox()
        self._tf_spin.setRange(1, 15)
        self._tf_spin.setValue(1)
        self._tf_spin.setSuffix(" min")
        form_lay.addRow("Timeframe:", self._tf_spin)

        self._base_ema_spin = QSpinBox()
        self._base_ema_spin.setRange(9, 200)
        self._base_ema_spin.setValue(21)
        self._base_ema_spin.setSuffix(" periods")
        form_lay.addRow("Base EMA:", self._base_ema_spin)

        self._atr_extension_spin = QDoubleSpinBox()
        self._atr_extension_spin.setRange(1.0, 3.0)
        self._atr_extension_spin.setValue(1.10)
        self._atr_extension_spin.setSingleStep(0.05)
        self._atr_extension_spin.setDecimals(2)
        form_lay.addRow("ATR Extension Min:", self._atr_extension_spin)

        self._sl_atr_mult_spin = QDoubleSpinBox()
        self._sl_atr_mult_spin.setRange(0.5, 5.0)
        self._sl_atr_mult_spin.setValue(1.5)
        self._sl_atr_mult_spin.setSingleStep(0.25)
        self._sl_atr_mult_spin.setDecimals(2)
        form_lay.addRow("SL Multiplier (ATR ×):", self._sl_atr_mult_spin)

        self._tp_atr_mult_spin = QDoubleSpinBox()
        self._tp_atr_mult_spin.setRange(1.0, 10.0)
        self._tp_atr_mult_spin.setValue(2.0)
        self._tp_atr_mult_spin.setSingleStep(0.5)
        self._tp_atr_mult_spin.setDecimals(1)
        self._tp_atr_mult_spin.setToolTip("Take-profit = Entry ± (ATR × multiplier). 2.0 = 1:2 R:R")
        form_lay.addRow("TP Multiplier (ATR ×):", self._tp_atr_mult_spin)

        self._strikes_above_spin = QSpinBox()
        self._strikes_above_spin.setRange(0, 5)
        self._strikes_above_spin.setValue(1)
        form_lay.addRow("Strikes Above ATM:", self._strikes_above_spin)

        self._strikes_below_spin = QSpinBox()
        self._strikes_below_spin.setRange(0, 5)
        self._strikes_below_spin.setValue(1)
        form_lay.addRow("Strikes Below ATM:", self._strikes_below_spin)

        self._min_confidence_spin = QDoubleSpinBox()
        self._min_confidence_spin.setRange(0.0, 1.0)
        self._min_confidence_spin.setValue(0.6)
        self._min_confidence_spin.setSingleStep(0.05)
        self._min_confidence_spin.setDecimals(2)
        form_lay.addRow("Min Confidence Gate:", self._min_confidence_spin)

        self._min_adx_spin = QDoubleSpinBox()
        self._min_adx_spin.setRange(0.0, 50.0)
        self._min_adx_spin.setValue(20.0)
        self._min_adx_spin.setSingleStep(1.0)
        self._min_adx_spin.setDecimals(1)
        form_lay.addRow("Min ADX Gate:", self._min_adx_spin)

        self._session_start_spin = QSpinBox()
        self._session_start_spin.setRange(900, 1530)
        self._session_start_spin.setValue(915)
        form_lay.addRow("Session Start (HHMM):", self._session_start_spin)

        self._session_end_spin = QSpinBox()
        self._session_end_spin.setRange(900, 1530)
        self._session_end_spin.setValue(1500)
        form_lay.addRow("Session End (HHMM):", self._session_end_spin)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ ADD")
        add_btn.setObjectName("add_btn")
        add_btn.clicked.connect(self._on_add_symbol)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("− REMOVE")
        remove_btn.setObjectName("remove_btn")
        remove_btn.clicked.connect(self._on_remove_selected)
        btn_row.addWidget(remove_btn)
        form_lay.addRow(btn_row)

        setup_lay.addWidget(add_grp)

        # ── Watchlist table ───────────────────────────────────────────────
        watch_grp = QGroupBox("Watching")
        watch_lay = QVBoxLayout(watch_grp)
        watch_lay.setContentsMargins(4, 4, 4, 4)

        self._watchlist_table = QTableWidget(0, 3)
        self._watchlist_table.setHorizontalHeaderLabels(["Symbol", "Status", "Last Signal"])
        self._watchlist_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._watchlist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._watchlist_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._watchlist_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._watchlist_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._watchlist_table.setAlternatingRowColors(False)
        self._watchlist_table.verticalHeader().setVisible(False)
        self._watchlist_table.setShowGrid(False)
        watch_lay.addWidget(self._watchlist_table)

        setup_lay.addWidget(watch_grp, 1)

        note = QLabel(
            "ATR Dist: how many ATRs price must be\n"
            "from EMA to qualify as extended.\n"
            "CVD Z-Score: minimum CVD deviation."
        )
        note.setStyleSheet(f"color: {C_MUTED}; font-size: 10px; padding: 4px;")
        setup_lay.addWidget(note)

        actions_row = QHBoxLayout()
        save_btn = QPushButton("SAVE SETUP")
        save_btn.clicked.connect(self._save_setup_state)
        actions_row.addWidget(save_btn)

        load_btn = QPushButton("LOAD SETUP")
        load_btn.clicked.connect(self._load_setup_from_button)
        actions_row.addWidget(load_btn)
        setup_lay.addLayout(actions_row)

        simulator_grp = QGroupBox("Simulator Backtester")
        simulator_form = QFormLayout(simulator_grp)
        simulator_form.setLabelAlignment(Qt.AlignRight)

        self._sim_date_edit = QDateEdit()
        self._sim_date_edit.setCalendarPopup(True)
        self._sim_date_edit.setDate(QDate.currentDate())
        self._sim_date_edit.setDisplayFormat("dd-MMM-yyyy")
        simulator_form.addRow("Trade Date:", self._sim_date_edit)

        sim_run_btn = QPushButton("SIMULATOR RUN")
        sim_run_btn.clicked.connect(self._run_simulator_backtest)
        simulator_form.addRow(sim_run_btn)

        self._sim_result_label = QLabel("Net pts captured: --")
        self._sim_result_label.setStyleSheet(f"color: {C_MUTED}; font-size: 11px;")
        simulator_form.addRow("Result:", self._sim_result_label)

        setup_lay.addWidget(simulator_grp)

        lay.addWidget(setup_grp, 1)

        close_btn = QPushButton("CLOSE")
        close_btn.clicked.connect(dlg.close)
        lay.addWidget(close_btn, 0, Qt.AlignRight)

        return dlg

    def _open_setup_dialog(self):
        self._setup_dialog.show()
        self._setup_dialog.raise_()
        self._setup_dialog.activateWindow()

    def _build_signal_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(4, 8, 8, 8)
        lay.setSpacing(4)

        # Header row
        hdr = QHBoxLayout()
        sig_title = QLabel("SIGNAL FEED")
        sig_title.setStyleSheet(f"color: {C_MUTED}; font-size: 10px; letter-spacing: 1px;")
        hdr.addWidget(sig_title)
        hdr.addStretch()

        clear_btn = QPushButton("CLEAR")
        clear_btn.setObjectName("clear_btn")
        clear_btn.setFixedWidth(60)
        clear_btn.clicked.connect(self._clear_signals)
        hdr.addWidget(clear_btn)
        lay.addLayout(hdr)

        # Signal table
        self._signal_table = QTableWidget(0, 10)
        self._signal_table.setHorizontalHeaderLabels([
            "TIME", "SYMBOL", "SIGNAL", "SPOT", "ATR", "ADX", "CVD-Z", "CONFIDENCE", "SL PTS", "STATUS"
        ])
        hdr_view = self._signal_table.horizontalHeader()
        hdr_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(8, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(9, QHeaderView.Stretch)

        self._signal_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._signal_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._signal_table.verticalHeader().setVisible(False)
        self._signal_table.verticalHeader().setDefaultSectionSize(28)
        self._signal_table.setWordWrap(False)
        self._signal_table.setShowGrid(False)
        self._signal_table.setAlternatingRowColors(True)
        self._signal_table.setStyleSheet(f"""
            QTableWidget::item:alternate {{
                background: rgba(255,255,255,0.02);
            }}
        """)

        lay.addWidget(self._signal_table, 1)

        # Footer legend + telemetry
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(8)

        legend = QLabel(
            f"  <span style='color:{C_LONG}'>▲ LONG</span>"
            f"  <span style='color:{C_SHORT}'>▼ SHORT</span>"
            f"  <span style='color:{C_CHOP}'>◆ CHOP-FILTERED (alert only)</span>"
        )
        legend.setStyleSheet(f"color: {C_MUTED}; font-size: 10px; padding: 2px 4px;")
        footer_row.addWidget(legend)
        footer_row.addStretch()

        self._uptime_label = QLabel("00:00:00")
        self._uptime_label.setStyleSheet(f"color: {C_MUTED}; font-size: 10px;")
        footer_row.addWidget(self._uptime_label)

        self._signal_count_label = QLabel("0 signals")
        self._signal_count_label.setStyleSheet(f"color: {C_MUTED}; font-size: 10px;")
        footer_row.addWidget(self._signal_count_label)

        lay.addLayout(footer_row)

        return panel

    def _build_status_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(24)
        bar.setStyleSheet(f"""
            QFrame {{
                background: {C_PANEL};
                border-top: 1px solid {C_BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)

        self._status_label = QLabel("Engine ready. Add symbols to start scanning.")
        self._status_label.setStyleSheet(f"color: {C_MUTED}; font-size: 10px;")
        lay.addWidget(self._status_label)
        lay.addStretch()

        self._watched_count_label = QLabel("0 symbols")
        self._watched_count_label.setStyleSheet(f"color: {C_MUTED}; font-size: 10px;")
        lay.addWidget(self._watched_count_label)

        return bar

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_add_symbol(self):
        symbol = self._symbol_selector.currentText().strip().upper()

        if not symbol:
            self._set_status("Select a symbol from the loaded NFO list.")
            return

        token = self._resolve_futures_token(symbol)
        if token is None:
            self._set_status(f"No active futures token found for {symbol}.")
            return

        if symbol in self._symbol_tokens:
            self._set_status(f"{symbol} is already being watched.")
            return

        params = {
            "timeframe_minutes": self._tf_spin.value(),
            "atr_distance_threshold": self._atr_distance_spin.value(),
            "cvd_zscore_threshold": self._cvd_zscore_spin.value(),
            "atr_base_ema": self._base_ema_spin.value(),
            "atr_extension_min": self._atr_extension_spin.value(),
        }

        self._pending_watchlist[symbol] = params
        self._add_watchlist_row(symbol, STATUS_LABELS["queued"])
        if self._automation_enabled:
            self._start_symbol_engine(symbol)
        self._update_counts()
        self._save_setup_state()
        if self._automation_enabled:
            self._set_status(f"Added {symbol} using mapped FUT token {token}.")
        else:
            self._set_status(f"Added {symbol} to setup queue. Enable AUTOMATE to start.")

    def _on_remove_selected(self):
        selected = self._watchlist_table.selectedItems()
        if not selected:
            return
        row = self._watchlist_table.currentRow()
        symbol_item = self._watchlist_table.item(row, 0)
        if not symbol_item:
            return
        symbol = symbol_item.text()

        self.engine.remove_symbol(symbol)
        self._symbol_tokens.pop(symbol, None)
        self._pending_watchlist.pop(symbol, None)
        self._watchlist_table.removeRow(row)
        self._update_counts()
        self._save_setup_state()
        self._set_status(f"Removed {symbol} from watchlist.")

    def _clear_signals(self):
        self._signal_table.setRowCount(0)
        self._update_counts()

    # ── Engine callbacks ───────────────────────────────────────────────────

    @Slot(object)
    def _on_signal(self, event: AtrSignalEvent):
        """Insert a new signal row at the top of the signal feed."""
        self._signal_table.setUpdatesEnabled(False)
        self._signal_table.insertRow(0)

        time_str = event.timestamp.strftime("%H:%M:%S")
        color = C_LONG if event.side == "long" else C_SHORT
        if event.chop_filtered:
            color = C_CHOP

        cvd_z = getattr(event, "cvd_zscore", 0.0)
        sl_pts = event.atr * self._sl_atr_mult_spin.value()
        cells = [
            (time_str, C_MUTED),
            (event.symbol, C_TEXT),
            (event.direction_label, color),
            (f"{event.price:.2f}", C_TEXT),
            (f"{event.atr:.2f}", C_MUTED),
            (f"{event.adx:.1f}", C_MUTED),
            (f"{cvd_z:.2f}", C_MUTED),
            (event.confidence_pct, color),
            (f"{sl_pts:.2f}", C_WARN),
            ("SCANNING", C_ACCENT),
        ]

        bold_font = QFont()
        bold_font.setBold(True)

        row_bg = None
        if event.confidence >= 0.75:
            row_bg = QColor("#0D1F0D")
        elif event.confidence >= 0.60:
            row_bg = QColor("#0D1520")

        for col, (text, fg) in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setForeground(QColor(fg))
            if col == 2:  # direction column gets bold
                item.setFont(bold_font)
            if row_bg is not None:
                item.setBackground(row_bg)
            self._signal_table.setItem(0, col, item)
        self._signal_table.setUpdatesEnabled(True)

        self._update_last_signal_time(event.symbol, time_str)

        # Keep table lean
        if self._signal_table.rowCount() > self.MAX_SIGNAL_ROWS:
            self._signal_table.removeRow(self.MAX_SIGNAL_ROWS)

        self._update_counts()

        # Flash the live dot
        self._live_dot.setStyleSheet(f"color: {color}; font-size: 16px;")
        QTimer.singleShot(800, lambda: self._live_dot.setStyleSheet(
            f"color: {C_LONG}; font-size: 16px;"
        ))

        chop_note = " (CHOP-FILTERED)" if event.chop_filtered else ""
        self._set_status(
            f"Signal: {event.symbol} {event.direction_label} @ {event.price:.2f}"
            f"  conf={event.confidence_pct}{chop_note}"
        )

    @Slot(str, str)
    def _on_status_update(self, symbol: str, status: str):
        self._update_watchlist_status(symbol, STATUS_LABELS.get(status, status))

    @Slot(str, str)
    def _on_symbol_error(self, symbol: str, error: str):
        self._update_watchlist_status(symbol, "⚠ ERROR")
        self._set_status(f"Error on {symbol}: {error}")

    # ── Watchlist helpers ──────────────────────────────────────────────────

    def _add_watchlist_row(self, symbol: str, initial_status: str = "starting…"):
        if self._find_watchlist_row(symbol) >= 0:
            return
        row = self._watchlist_table.rowCount()
        self._watchlist_table.insertRow(row)

        sym_item = QTableWidgetItem(symbol)
        sym_item.setForeground(QColor(C_TEXT))
        self._watchlist_table.setItem(row, 0, sym_item)

        status_item = QTableWidgetItem(initial_status)
        status_color = STATUS_COLORS.get(initial_status, C_MUTED)
        status_item.setForeground(QColor(status_color))
        self._watchlist_table.setItem(row, 1, status_item)

        signal_item = QTableWidgetItem("—")
        signal_item.setForeground(QColor(C_MUTED))
        self._watchlist_table.setItem(row, 2, signal_item)

    def _find_watchlist_row(self, symbol: str) -> int:
        for row in range(self._watchlist_table.rowCount()):
            item = self._watchlist_table.item(row, 0)
            if item and item.text() == symbol:
                return row
        return -1

    def _update_watchlist_status(self, symbol: str, status: str):
        for row in range(self._watchlist_table.rowCount()):
            item = self._watchlist_table.item(row, 0)
            if item and item.text() == symbol:
                status_item = self._watchlist_table.item(row, 1)
                if status_item:
                    status_item.setText(status)
                    color = STATUS_COLORS.get(status, C_MUTED)
                    status_item.setForeground(QColor(color))
                break

    def _update_last_signal_time(self, symbol: str, time_str: str):
        for row in range(self._watchlist_table.rowCount()):
            item = self._watchlist_table.item(row, 0)
            if item and item.text() == symbol:
                signal_item = self._watchlist_table.item(row, 2)
                if signal_item:
                    signal_item.setText(time_str)
                break

    # ── Misc helpers ───────────────────────────────────────────────────────

    def _tick_uptime(self):
        delta = datetime.now() - self._start_time
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s = divmod(rem, 60)
        self._uptime_label.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _update_counts(self):
        n_sym = self._watchlist_table.rowCount()
        n_sig = self._signal_table.rowCount()
        self._watched_count_label.setText(f"{n_sym} symbols")
        self._signal_count_label.setText(f"{n_sig} signals")

    def _set_status(self, msg: str):
        self._status_label.setText(msg)

    def _run_simulator_backtest(self):
        if not self._pending_watchlist:
            self._set_status("No symbols in watchlist for simulator run.")
            return

        # Every simulator run starts with a clean feed so output mirrors
        # only the currently simulated session.
        self._clear_signals()

        selected_qdate = self._sim_date_edit.date()
        run_day = date(selected_qdate.year(), selected_qdate.month(), selected_qdate.day())
        from_dt = datetime.combine(run_day, datetime.min.time())
        to_dt = datetime.combine(run_day + timedelta(days=1), datetime.min.time())

        total_points = 0.0
        symbols_processed = 0
        total_signals = 0
        wins = 0
        closed_trades = 0

        for symbol, params in sorted(self._pending_watchlist.items()):
            token = self._resolve_futures_token(symbol)
            if token is None:
                logger.warning("[SIM] Skipping %s due to missing futures token", symbol)
                continue

            try:
                candles = self.kite.historical_data(token, from_dt, to_dt, interval="minute")
            except Exception as exc:
                logger.warning("[SIM] Historical fetch failed for %s: %s", symbol, exc)
                continue

            if not candles:
                continue

            df = pd.DataFrame(candles)
            if df.empty or "date" not in df.columns:
                continue

            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df = df.sort_index()

            timeframe_minutes = int(params.get("timeframe_minutes", self._tf_spin.value()))
            if timeframe_minutes > 1:
                rule = f"{timeframe_minutes}min"
                df = df.resample(rule).agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }).dropna(subset=["open", "high", "low", "close"])

            if len(df) < 30:
                continue

            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)

            price_close = df["close"].to_numpy(dtype=float)
            price_high = df["high"].to_numpy(dtype=float)
            price_low = df["low"].to_numpy(dtype=float)
            price_open = df["open"].to_numpy(dtype=float)
            volume = df.get("volume", pd.Series(np.ones(len(df)), index=df.index)).to_numpy(dtype=float)

            atr = calculate_atr(price_high, price_low, price_close, period=14)
            base_ema = int(params.get("atr_base_ema", self._base_ema_spin.value()))
            ema51 = calculate_ema(price_close, base_ema)
            session_keys = df.index.date if hasattr(df.index, "date") else None
            vwap = calculate_vwap(price_close, volume, session_keys=session_keys)

            cvd_close = (
                cvd_df["close"].to_numpy(dtype=float)
                if cvd_df is not None and not cvd_df.empty
                else np.zeros_like(price_close)
            )
            cvd_zscore, _ = calculate_cvd_zscore(cvd_close, ema_period=51, zscore_window=50)

            safe_atr = np.where(atr <= 0, np.nan, atr)
            distance = np.abs(price_close - ema51) / safe_atr

            atr_distance_threshold = float(params.get("atr_distance_threshold", self._atr_distance_spin.value()))
            cvd_zscore_threshold = float(params.get("cvd_zscore_threshold", self._cvd_zscore_spin.value()))

            price_atr_above = (distance >= atr_distance_threshold) & (price_close > ema51)
            price_atr_below = (distance >= atr_distance_threshold) & (price_close < ema51)
            cvd_atr_above = cvd_zscore >= cvd_zscore_threshold
            cvd_atr_below = cvd_zscore <= -cvd_zscore_threshold

            detector = StrategySignalDetector(timeframe_minutes=timeframe_minutes)
            short_confirmed, long_confirmed, _, _ = detector.detect_atr_reversal_strategy(
                price_atr_above=price_atr_above,
                price_atr_below=price_atr_below,
                cvd_atr_above=cvd_atr_above,
                cvd_atr_below=cvd_atr_below,
                atr_values=atr,
                timestamps=list(df.index),
                price_close=price_close,
                price_open=price_open,
                price_ema51=ema51,
                price_vwap=vwap,
                cvd_data=cvd_close,
                vwap_min_distance_atr_mult=0.3,
                exhaustion_min_score=2,
            )

            signals = np.zeros(len(df), dtype=int)
            signals[long_confirmed] = 1
            signals[short_confirmed] = -1

            symbol_points = 0.0
            position = 0
            entry_price = 0.0

            for idx, signal in enumerate(signals):
                px = price_close[idx]
                if position == 0 and signal != 0:
                    position = int(signal)
                    entry_price = px
                    total_signals += 1
                    self._emit_simulated_signal(
                        symbol=symbol,
                        token=token,
                        signal=signal,
                        signal_time=df.index[idx].to_pydatetime(),
                        price=px,
                        atr=float(atr[idx]) if np.isfinite(atr[idx]) else 0.0,
                    )
                    continue

                if position != 0 and signal == -position:
                    trade_points = (px - entry_price) * position
                    symbol_points += trade_points
                    closed_trades += 1
                    if trade_points > 0:
                        wins += 1
                    position = int(signal)
                    entry_price = px
                    total_signals += 1
                    self._emit_simulated_signal(
                        symbol=symbol,
                        token=token,
                        signal=signal,
                        signal_time=df.index[idx].to_pydatetime(),
                        price=px,
                        atr=float(atr[idx]) if np.isfinite(atr[idx]) else 0.0,
                    )

            if position != 0:
                trade_points = (price_close[-1] - entry_price) * position
                symbol_points += trade_points
                closed_trades += 1
                if trade_points > 0:
                    wins += 1

            total_points += symbol_points
            symbols_processed += 1

        win_rate = (wins / closed_trades * 100.0) if closed_trades else 0.0
        self._sim_result_label.setText(
            f"Underlying pts: {total_points:.2f} (≈ option premium: {total_points * 0.45:.2f} @ 0.45Δ) | "
            f"Signals: {total_signals} | Win rate: {win_rate:.1f}%"
        )
        self._set_status(
            f"Simulator completed for {run_day.isoformat()}: "
            f"{symbols_processed} symbols, {total_signals} entries, net {total_points:.2f} pts"
        )

    def _emit_simulated_signal(
        self,
        *,
        symbol: str,
        token: int,
        signal: int,
        signal_time: datetime,
        price: float,
        atr: float,
    ):
        """Render a simulator signal in the same feed format as live signals."""
        side = "long" if signal > 0 else "short"
        event = AtrSignalEvent(
            symbol=symbol,
            instrument_token=int(token),
            side=side,
            price=float(price),
            atr=max(float(atr), 0.0),
            adx=0.0,
            confidence=0.0,
            quality_score=0.0,
            chop_filtered=False,
            timestamp=signal_time,
        )
        self._on_signal(event)

    def _on_automation_toggled(self, checked: bool):
        self._automation_enabled = bool(checked)
        if self._automation_enabled and self._signal_router is None:
            main_window = self.window()
            if main_window is None:
                logger.error("[SCANNER] Cannot create AtrSignalRouter: no parent main window")
                return
            self._signal_router = AtrSignalRouter(main_window=main_window, parent=self)
        if self._signal_router:
            if self._automation_enabled:
                self.engine.signal_fired.connect(self._signal_router.on_signal)
            else:
                try:
                    self.engine.signal_fired.disconnect(self._signal_router.on_signal)
                except RuntimeError:
                    pass
        self._apply_automation_state(initial=False)
        self._save_setup_state()

    def _apply_automation_state(self, initial: bool):
        if self._automation_enabled:
            if not self._instrument_data:
                if not initial:
                    self._set_status("Automation enabled. Waiting for instrument data to start.")
                return
            for symbol in list(self._pending_watchlist.keys()):
                self._start_symbol_engine(symbol)
            if not initial:
                self._set_status("Automation enabled. Scanner started for queued symbols.")
        else:
            self.engine.remove_all()
            self._symbol_tokens.clear()
            for symbol in self._pending_watchlist.keys():
                self._update_watchlist_status(symbol, STATUS_LABELS["queued"])
            if not initial:
                self._set_status("Automation paused. Click AUTOMATE to run scanner.")

    def _start_symbol_engine(self, symbol: str):
        if symbol in self._symbol_tokens:
            return
        token = self._resolve_futures_token(symbol)
        if token is None:
            self._update_watchlist_status(symbol, "error")
            return
        params = self._pending_watchlist.get(symbol, {})
        self._symbol_tokens[symbol] = token
        self.engine.add_symbol(symbol, token, params)

    def _save_setup_state(self):
        payload = {
            "automation_enabled": self._automation_enabled,
            "defaults": {
                "atr_distance_threshold": self._atr_distance_spin.value(),
                "cvd_zscore_threshold": self._cvd_zscore_spin.value(),
                "timeframe_minutes": self._tf_spin.value(),
                "atr_base_ema": self._base_ema_spin.value(),
                "atr_extension_min": self._atr_extension_spin.value(),
                "sl_atr_multiplier": self._sl_atr_mult_spin.value(),
                "tp_atr_multiplier": self._tp_atr_mult_spin.value(),
                "strikes_above": self._strikes_above_spin.value(),
                "strikes_below": self._strikes_below_spin.value(),
                "min_confidence": self._min_confidence_spin.value(),
                "min_adx": self._min_adx_spin.value(),
                "session_start": self._session_start_spin.value(),
                "session_end": self._session_end_spin.value(),
            },
            "symbols": [
                {
                    "symbol": symbol,
                    "params": params,
                }
                for symbol, params in sorted(self._pending_watchlist.items())
            ],
        }
        self._settings.setValue("setup_state", json.dumps(payload))
        self._settings.sync()

    def _load_setup_from_button(self):
        self._load_setup_state()
        self._set_status("Loaded setup and symbols from saved state.")

    def _load_setup_state(self):
        raw = self._settings.value("setup_state", "")
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except Exception:
            logger.warning("Failed parsing saved setup_state payload")
            return

        defaults = payload.get("defaults", {}) if isinstance(payload, dict) else {}
        self._atr_distance_spin.setValue(float(defaults.get("atr_distance_threshold", 1.5)))
        self._cvd_zscore_spin.setValue(float(defaults.get("cvd_zscore_threshold", 1.5)))
        self._tf_spin.setValue(int(defaults.get("timeframe_minutes", 1)))
        self._base_ema_spin.setValue(int(defaults.get("atr_base_ema", 21)))
        self._atr_extension_spin.setValue(float(defaults.get("atr_extension_min", 1.1)))
        self._sl_atr_mult_spin.setValue(float(defaults.get("sl_atr_multiplier", 1.5)))
        self._tp_atr_mult_spin.setValue(float(defaults.get("tp_atr_multiplier", 2.0)))
        self._strikes_above_spin.setValue(int(defaults.get("strikes_above", 1)))
        self._strikes_below_spin.setValue(int(defaults.get("strikes_below", 1)))
        self._min_confidence_spin.setValue(float(defaults.get("min_confidence", 0.6)))
        self._min_adx_spin.setValue(float(defaults.get("min_adx", 20.0)))
        self._session_start_spin.setValue(int(defaults.get("session_start", 915)))
        self._session_end_spin.setValue(int(defaults.get("session_end", 1500)))

        self._pending_watchlist.clear()
        self._watchlist_table.setRowCount(0)

        for item in payload.get("symbols", []):
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            params = item.get("params", {}) if isinstance(item.get("params", {}), dict) else {}
            merged_params = {
                "timeframe_minutes": int(params.get("timeframe_minutes", self._tf_spin.value())),
                "atr_distance_threshold": float(params.get("atr_distance_threshold", self._atr_distance_spin.value())),
                "cvd_zscore_threshold": float(params.get("cvd_zscore_threshold", self._cvd_zscore_spin.value())),
                "atr_base_ema": int(params.get("atr_base_ema", self._base_ema_spin.value())),
                "atr_extension_min": float(params.get("atr_extension_min", self._atr_extension_spin.value())),
            }
            self._pending_watchlist[symbol] = merged_params
            self._add_watchlist_row(symbol, STATUS_LABELS["queued"])

        enabled = bool(payload.get("automation_enabled", False)) if isinstance(payload, dict) else False
        self._automation_toggle.blockSignals(True)
        self._automation_toggle.setChecked(enabled)
        self._automation_toggle.blockSignals(False)
        self._automation_enabled = enabled
        self._update_counts()

    # ── Public API — called by main_window ────────────────────────────────

    def add_symbol_programmatic(self, symbol: str, token: int):
        """Called from main_window to pre-populate symbols."""
        # Backward compatibility: allow direct token push for external callers.
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return

        mapped_token = self._resolve_futures_token(symbol)
        if mapped_token is None and isinstance(token, int) and token > 0:
            mapped_token = token

        if mapped_token is None:
            self._set_status(f"Cannot map token for {symbol}.")
            return

        if symbol in self._symbol_tokens:
            return

        params = {
            "timeframe_minutes": self._tf_spin.value(),
            "atr_distance_threshold": self._atr_distance_spin.value(),
            "cvd_zscore_threshold": self._cvd_zscore_spin.value(),
            "atr_base_ema": self._base_ema_spin.value(),
            "atr_extension_min": self._atr_extension_spin.value(),
        }
        self._pending_watchlist[symbol] = params
        self._add_watchlist_row(symbol, STATUS_LABELS["queued"])
        if self._automation_enabled:
            self._start_symbol_engine(symbol)
        self._update_counts()
        self._save_setup_state()

    def set_instrument_data(self, data: dict[str, dict[str, Any]]):
        self._instrument_data = data or {}
        self._reload_symbol_selector()
        if self._automation_enabled:
            logger.info(
                "[SCANNER] Instrument data arrived — resuming automation for %d queued symbols",
                len(self._pending_watchlist),
            )
            self._apply_automation_state(initial=True)

    def _reload_symbol_selector(self):
        current = self._symbol_selector.currentText().strip().upper()
        self._symbol_selector.blockSignals(True)
        self._symbol_selector.clear()

        symbols = [
            symbol for symbol in sorted(self._instrument_data.keys())
            if self._resolve_futures_token(symbol) is not None
        ]
        self._symbol_selector.addItems(symbols)

        if current and current in symbols:
            self._symbol_selector.setCurrentText(current)
        self._symbol_selector.blockSignals(False)

    def _resolve_futures_token(self, symbol: str) -> Optional[int]:
        symbol_info = self._instrument_data.get(symbol.upper())
        if not symbol_info:
            return None

        futures = symbol_info.get("futures") or []
        if not futures:
            return None

        today = date.today()
        valid_futures = [
            fut for fut in futures
            if fut.get("instrument_token") and fut.get("expiry") and fut["expiry"] >= today
        ]
        if not valid_futures:
            return None

        valid_futures.sort(key=lambda x: x["expiry"])
        return int(valid_futures[0]["instrument_token"])

    def cleanup(self):
        """Call this on main window close."""
        self._save_setup_state()
        self.engine.remove_all()
        self._uptime_timer.stop()
