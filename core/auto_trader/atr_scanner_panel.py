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
from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QAbstractItemView,
    QSplitter,
    QGroupBox,
    QFormLayout,
)

from core.auto_trader.multi_symbol_engine import AtrSignalEvent, MultiSymbolEngine

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

STATUS_COLORS = {
    "watching":   C_LONG,
    "fetching":   C_ACCENT,
    "warming_up": C_WARN,
    "error":      C_SHORT,
    "started":    C_MUTED,
    "no_data":    C_MUTED,
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

        # Engine
        self.engine = MultiSymbolEngine(kite=kite, parent=self)
        self.engine.signal_fired.connect(self._on_signal)
        self.engine.symbol_status.connect(self._on_status_update)
        self.engine.symbol_error.connect(self._on_symbol_error)

        # Track token mapping: symbol → instrument_token
        self._symbol_tokens: dict[str, int] = {}
        self._instrument_data: dict[str, dict[str, Any]] = {}

        self.setStyleSheet(BASE_STYLE)
        self._setup_ui()

        # Uptime clock
        self._uptime_timer = QTimer(self)
        self._uptime_timer.setInterval(1000)
        self._uptime_timer.timeout.connect(self._tick_uptime)
        self._start_time = datetime.now()
        self._uptime_timer.start()

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
        title = QLabel("ATR REVERSAL  SCANNER")
        title.setStyleSheet(f"color: {C_TEXT}; font-size: 13px; font-weight: 600; letter-spacing: 2px;")
        lay.addWidget(title)

        # Live dot
        self._live_dot = QLabel("●")
        self._live_dot.setStyleSheet(f"color: {C_LONG}; font-size: 16px;")
        lay.addWidget(self._live_dot)

        lay.addStretch()

        setup_btn = QPushButton("SETUP")
        setup_btn.setFixedWidth(80)
        setup_btn.clicked.connect(self._open_setup_dialog)
        lay.addWidget(setup_btn)

        # Uptime
        self._uptime_label = QLabel("00:00:00")
        self._uptime_label.setStyleSheet(f"color: {C_MUTED}; font-size: 11px;")
        lay.addWidget(self._uptime_label)

        # Signal count badge
        self._signal_count_label = QLabel("0 signals")
        self._signal_count_label.setStyleSheet(f"color: {C_MUTED}; font-size: 11px; margin-left: 16px;")
        lay.addWidget(self._signal_count_label)

        return header

    def _build_setup_dialog(self) -> QDialog:
        dlg = QDialog(self)
        dlg.setWindowTitle("Auto Trader Setup")
        dlg.resize(460, 620)
        dlg.setModal(False)

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

        self._watchlist_table = QTableWidget(0, 2)
        self._watchlist_table.setHorizontalHeaderLabels(["Symbol", "Status"])
        self._watchlist_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._watchlist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
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
        self._signal_table = QTableWidget(0, 7)
        self._signal_table.setHorizontalHeaderLabels([
            "Time", "Symbol", "Direction", "Price", "ATR", "ADX", "Conf%"
        ])
        hdr_view = self._signal_table.horizontalHeader()
        hdr_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr_view.setSectionResizeMode(6, QHeaderView.Stretch)

        self._signal_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._signal_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._signal_table.verticalHeader().setVisible(False)
        self._signal_table.setShowGrid(False)
        self._signal_table.setAlternatingRowColors(True)
        self._signal_table.setStyleSheet(f"""
            QTableWidget::item:alternate {{
                background: rgba(255,255,255,0.02);
            }}
        """)

        lay.addWidget(self._signal_table, 1)

        # Legend
        legend = QLabel(
            f"  <span style='color:{C_LONG}'>▲ LONG</span>"
            f"  <span style='color:{C_SHORT}'>▼ SHORT</span>"
            f"  <span style='color:{C_CHOP}'>◆ CHOP-FILTERED (alert only)</span>"
        )
        legend.setStyleSheet(f"color: {C_MUTED}; font-size: 10px; padding: 2px 4px;")
        lay.addWidget(legend)

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
        }

        self._symbol_tokens[symbol] = token
        self.engine.add_symbol(symbol, token, params)
        self._add_watchlist_row(symbol)
        self._update_counts()
        self._set_status(f"Added {symbol} using mapped FUT token {token}.")

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
        self._watchlist_table.removeRow(row)
        self._update_counts()
        self._set_status(f"Removed {symbol} from watchlist.")

    def _clear_signals(self):
        self._signal_table.setRowCount(0)
        self._update_counts()

    # ── Engine callbacks ───────────────────────────────────────────────────

    @Slot(object)
    def _on_signal(self, event: AtrSignalEvent):
        """Insert a new signal row at the top of the signal feed."""
        self._signal_table.insertRow(0)

        time_str = event.timestamp.strftime("%H:%M:%S")
        color = C_LONG if event.side == "long" else C_SHORT
        if event.chop_filtered:
            color = C_CHOP

        cells = [
            (time_str, C_MUTED),
            (event.symbol, C_TEXT),
            (event.direction_label, color),
            (f"{event.price:.2f}", C_TEXT),
            (f"{event.atr:.2f}", C_MUTED),
            (f"{event.adx:.1f}", C_MUTED),
            (event.confidence_pct, color),
        ]

        bold_font = QFont()
        bold_font.setBold(True)

        for col, (text, fg) in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setForeground(QColor(fg))
            if col == 2:  # direction column gets bold
                item.setFont(bold_font)
            self._signal_table.setItem(0, col, item)

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
        self._update_watchlist_status(symbol, status)

    @Slot(str, str)
    def _on_symbol_error(self, symbol: str, error: str):
        self._update_watchlist_status(symbol, "error")
        self._set_status(f"Error on {symbol}: {error}")

    # ── Watchlist helpers ──────────────────────────────────────────────────

    def _add_watchlist_row(self, symbol: str):
        row = self._watchlist_table.rowCount()
        self._watchlist_table.insertRow(row)

        sym_item = QTableWidgetItem(symbol)
        sym_item.setForeground(QColor(C_TEXT))
        self._watchlist_table.setItem(row, 0, sym_item)

        status_item = QTableWidgetItem("starting…")
        status_item.setForeground(QColor(C_MUTED))
        self._watchlist_table.setItem(row, 1, status_item)

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
        }
        self._symbol_tokens[symbol] = mapped_token
        self.engine.add_symbol(symbol, mapped_token, params)
        self._add_watchlist_row(symbol)
        self._update_counts()

    def set_instrument_data(self, data: dict[str, dict[str, Any]]):
        self._instrument_data = data or {}
        self._reload_symbol_selector()

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
        self.engine.remove_all()
        self._uptime_timer.stop()
