import logging
from typing import Dict, List, Optional, Union
from datetime import date

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMenu, QDialog, QFormLayout, QSpinBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressBar, QStyle,
    QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QTimer, QPoint
from PySide6.QtGui import QColor, QFont, QPainter, QBrush, QLinearGradient
from kiteconnect import KiteConnect

from core.utils.data_models import Contract

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
#  Palette — single source of truth
# ──────────────────────────────────────────────────────────────────────────────
CE_COLOR   = "#00C4C6"   # cyan  — calls
PE_COLOR   = "#E0424A"   # red   — puts
ATM_COLOR  = "#F0C040"   # gold  — ATM strike
DIM_COLOR  = "#5A6478"   # muted — placeholder / inactive
BG_MAIN    = "#0C0F17"
BG_HEADER  = "#07090E"
BG_ROW_ATM = "#1A1E2E"
BORDER     = "#1E2430"
TEXT_MAIN  = "#C8D0DC"
TEXT_DIM   = "#7A8799"


def format_indian(number: int) -> str:
    if not isinstance(number, int) or number == 0:
        return "—"
    s = str(number)
    if len(s) <= 3:
        return s
    last_three, other = s[-3:], s[:-3]
    res = "".join(f",{c}" if i % 2 == 0 and i > 0 else c
                  for i, c in enumerate(reversed(other)))
    return res[::-1] + "," + last_three


def format_oi_compact(oi: int) -> str:
    """Format OI as compact string: 12.5L / 980K / 450"""
    if oi <= 0:
        return "—"
    if oi >= 100_000:
        return f"{oi / 100_000:.1f}L"
    if oi >= 1_000:
        return f"{oi / 1_000:.0f}K"
    return str(oi)


# ──────────────────────────────────────────────────────────────────────────────
#  Compact OI Cell  — single-row: [███░░░░] 12.4L
# ──────────────────────────────────────────────────────────────────────────────
class OICell(QWidget):
    """
    Ultra-compact OI display: coloured bar fills from the strike-side,
    value label sits right-aligned on the opposite side.
    Fits cleanly inside a 22 px row without any stacking.
    """

    def __init__(self, is_call: bool, parent=None):
        super().__init__(parent)
        self.is_call = is_call
        self.ratio   = 0.0          # 0.0 – 1.0
        self.text    = "—"
        self.color   = CE_COLOR if is_call else PE_COLOR
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(18)

    def set_data(self, oi: int, max_oi: float):
        self.ratio = min(oi / max_oi, 1.0) if max_oi > 0 and oi > 0 else 0.0
        self.text  = format_oi_compact(oi)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        bar_h = 3
        bar_y = h - bar_h - 1          # pin bar to bottom edge
        bar_w = int(w * self.ratio)

        # ── background bar track ──────────────────────────────────────────
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor("#1E2638")))
        p.drawRoundedRect(0, bar_y, w, bar_h, 1.5, 1.5)

        # ── filled portion (fills from outer edge toward centre) ──────────
        if bar_w > 0:
            p.setBrush(QBrush(QColor(self.color)))
            if self.is_call:
                # CE bar grows right-to-left (from strike outward)
                p.drawRoundedRect(w - bar_w, bar_y, bar_w, bar_h, 1.5, 1.5)
            else:
                # PE bar grows left-to-right
                p.drawRoundedRect(0, bar_y, bar_w, bar_h, 1.5, 1.5)

        # ── value label ───────────────────────────────────────────────────
        p.setPen(QColor(TEXT_MAIN))
        f = p.font()
        f.setPointSize(8)
        f.setWeight(QFont.Medium)
        p.setFont(f)

        label_rect = self.rect().adjusted(2, 0, -2, -(bar_h + 2))
        align = Qt.AlignRight | Qt.AlignVCenter if self.is_call else Qt.AlignLeft | Qt.AlignVCenter
        p.drawText(label_rect, align, self.text)
        p.end()


# ──────────────────────────────────────────────────────────────────────────────
#  Main widget
# ──────────────────────────────────────────────────────────────────────────────
class StrikeLadderWidget(QWidget):
    """Premium compact strike ladder with table design."""

    strike_selected        = Signal(Contract)
    chart_requested        = Signal(Contract)
    visible_tokens_changed = Signal()
    interval_calculated    = Signal(str, float)
    interval_changed       = Signal(str, float)

    # ── column indices ────────────────────────────────────────────────────────
    CE_BTN, CE_CHART, CE_BID, CE_ASK, CE_LTP, CE_OI, \
        STRIKE, \
    PE_OI, PE_LTP, PE_BID, PE_ASK, PE_CHART, PE_BTN = range(13)

    # OI gets more weight; BID/ASK get less (they're secondary)
    COLUMN_WEIGHTS = {
        CE_BID: 0.75,
        CE_ASK: 0.75,
        CE_LTP: 1.10,
        CE_OI:  1.30,
        PE_OI:  1.30,
        PE_LTP: 1.10,
        PE_BID: 0.75,
        PE_ASK: 0.75,
    }

    ROW_H = 22      # pixels per row

    def __init__(self, kite_client: KiteConnect):
        super().__init__()

        self.kite = kite_client
        self.symbol, self.expiry, self.current_price = "", None, 0.0
        self.base_strike_interval, self.user_strike_interval = 75.0, 0.0
        self.num_strikes_above, self.num_strikes_below = 15, 15
        self.atm_strike = 0.0
        self.contracts: Dict[float, Dict[str, Contract]] = {}
        self.instrument_data, self.available_strikes = {}, []
        self._instrument_index:   Dict[tuple, dict]    = {}
        self._token_contract_map: Dict[int, Contract]  = {}
        self._strike_row_map:     Dict[float, int]     = {}
        self._row_strike_map:     Dict[int, float]     = {}
        self.auto_adjust_enabled  = True
        self._max_oi              = 1.0

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._check_price_movement)
        self.update_timer.start(5000)
        self._last_centered_atm: Optional[float] = None
        self._user_scrolling  = False
        self._last_atm_strike: Optional[float] = None
        self._index_ltp       = None

        self._visible_tokens_timer = QTimer(self)
        self._visible_tokens_timer.setSingleShot(True)
        self._visible_tokens_timer.setInterval(120)
        self._visible_tokens_timer.timeout.connect(self._emit_visible_tokens_changed)

        self._init_ui()
        self._apply_styles()
        self._connect_signals()

    # ──────────────────────────────────────────────────────────────────────────
    #  UI construction
    # ──────────────────────────────────────────────────────────────────────────
    def _init_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        self.table = QTableWidget()
        headers = ["CE", "↗", "BID", "ASK", "LTP", "OI", "STRIKE", "OI", "LTP", "BID", "ASK", "↗", "PE"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setMouseTracking(False)
        self.table.viewport().setMouseTracking(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        main.addWidget(self.table, 1)

        sb = self.table.verticalScrollBar()
        sb.sliderPressed.connect( lambda: setattr(self, "_user_scrolling", True))
        sb.sliderReleased.connect(lambda: setattr(self, "_user_scrolling", False))
        sb.valueChanged.connect(  lambda: setattr(self, "_user_scrolling", True))
        sb.valueChanged.connect(  self._schedule_visible_tokens_emit)
        sb.sliderReleased.connect(self._schedule_visible_tokens_emit)

    def _reset_user_scroll(self):
        self._user_scrolling = False

    # ──────────────────────────────────────────────────────────────────────────
    #  Styles
    # ──────────────────────────────────────────────────────────────────────────
    def _apply_styles(self):
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setCurrentCell(-1, -1)

        h = self.table.horizontalHeader()
        h.setFixedHeight(24)
        h.setMinimumHeight(24)
        h.setMaximumHeight(24)

        # Fixed-width columns
        for col, w in {
            self.CE_BTN:   22,
            self.CE_CHART:  8,
            self.PE_CHART:  8,
            self.PE_BTN:   22,
            self.STRIKE:   66,
        }.items():
            h.setSectionResizeMode(col, QHeaderView.Fixed)
            self.table.setColumnWidth(col, w)

        # Stretch columns (weighted)
        for col in self.COLUMN_WEIGHTS:
            h.setSectionResizeMode(col, QHeaderView.Stretch)

        self.setStyleSheet(f"""
            QTableWidget {{
                background-color: {BG_MAIN};
                color: {TEXT_MAIN};
                border: 1px solid #1C2333;
                font-size: 11px;
                gridline-color: transparent;
            }}
            QHeaderView::section {{
                background: {BG_HEADER};
                color: {TEXT_DIM};
                padding: 3px 2px;
                border: none;
                border-bottom: 1px solid #1C2333;
                font-weight: 600;
                font-size: 10px;
                letter-spacing: 0.3px;
            }}
            QTableWidget::item {{
                padding: 0px 3px;
                border-bottom: 1px solid #161A26;
                outline: 0;
            }}
            QTableWidget::item:hover      {{ background: transparent; }}
            QTableWidget::item:selected   {{ background: transparent; }}
            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: #1C2333;
                border-radius: 2px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #253047; }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0px; }}
            QMenu {{
                background: #1B2030;
                border: 1px solid #3A4458;
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 18px;
                color: #E0E0E0;
                font-size: 11px;
                border-radius: 2px;
            }}
            QMenu::item:selected {{ background: #2A3350; }}
        """)

    def _apply_weighted_column_widths(self):
        total_weight = sum(self.COLUMN_WEIGHTS.values())
        fixed_width  = sum(
            self.table.columnWidth(c)
            for c in (self.CE_BTN, self.CE_CHART, self.PE_BTN, self.PE_CHART, self.STRIKE)
        )
        scrollbar_slack = self.table.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        available = self.table.viewport().width() - fixed_width - scrollbar_slack - 4

        if available <= 0:
            return

        acc  = 0
        cols = list(self.COLUMN_WEIGHTS.items())
        for i, (col, weight) in enumerate(cols):
            if i == len(cols) - 1:
                w = available - acc
            else:
                w = int(available * (weight / total_weight))
                acc += w
            self.table.setColumnWidth(col, max(w, 32))

    # ──────────────────────────────────────────────────────────────────────────
    #  Signals
    # ──────────────────────────────────────────────────────────────────────────
    def _connect_signals(self):
        self.table.customContextMenuRequested.connect(self._show_menu)

    # ──────────────────────────────────────────────────────────────────────────
    #  Context menu
    # ──────────────────────────────────────────────────────────────────────────
    def _show_menu(self, pos: QPoint):
        item = self.table.itemAt(pos)
        if not item:
            return
        strike = self._get_strike_from_row(item.row())
        if not strike:
            return
        menu = QMenu(self)
        menu.addAction(f"Jump to {strike:.0f}").triggered.connect(
            lambda: self._jump_to_strike(strike))
        menu.addSeparator()
        menu.addAction("Trade Both CE + PE").triggered.connect(
            lambda: self._trade_both(strike))
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    # ──────────────────────────────────────────────────────────────────────────
    #  Settings dialog
    # ──────────────────────────────────────────────────────────────────────────
    def _show_settings(self):
        d = QDialog(self)
        d.setWindowTitle("Ladder Settings")
        d.setFixedWidth(300)
        layout = QVBoxLayout(d)
        layout.setSpacing(12)

        title = QLabel("Strike Ladder Settings")
        title.setStyleSheet("font-size: 13px; font-weight: 700;")
        layout.addWidget(title)

        f = QFormLayout()
        f.setSpacing(8)
        above = QSpinBox(); above.setRange(5, 30); above.setValue(self.num_strikes_above)
        below = QSpinBox(); below.setRange(5, 30); below.setValue(self.num_strikes_below)
        auto_check = QCheckBox(); auto_check.setChecked(self.auto_adjust_enabled)
        f.addRow("Strikes Above:", above)
        f.addRow("Strikes Below:", below)
        f.addRow("Auto-Adjust ATM:", auto_check)
        layout.addLayout(f)

        atm_btn = QPushButton("Jump to ATM Strike")
        atm_btn.setFixedHeight(30)
        atm_btn.clicked.connect(lambda: (self._jump_to_atm(), d.close()))
        layout.addWidget(atm_btn)

        apply_btn = QPushButton("Apply Settings")
        apply_btn.setFixedHeight(30)
        apply_btn.clicked.connect(lambda: (
            setattr(self, 'num_strikes_above', above.value()),
            setattr(self, 'num_strikes_below', below.value()),
            self.set_auto_adjust(auto_check.isChecked()),
            self._refresh_ladder(),
            d.accept()
        ))
        layout.addWidget(apply_btn)
        d.exec()

    # ──────────────────────────────────────────────────────────────────────────
    #  Table build / update
    # ──────────────────────────────────────────────────────────────────────────
    def _rebuild_table(self):
        self.table.setRowCount(0)
        self._strike_row_map.clear()
        self._row_strike_map.clear()
        all_oi = [c.oi for sc in self.contracts.values() for c in sc.values() if c and c.oi > 0]
        self._max_oi = max(all_oi) if all_oi else 1.0

        for strike in sorted(self.contracts.keys()):
            self._add_row(strike)

        QTimer.singleShot(120, self._force_center_atm)
        QTimer.singleShot(0,   self._apply_weighted_column_widths)
        self._schedule_visible_tokens_emit()

    def _add_row(self, strike: float):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, self.ROW_H)
        self._strike_row_map[strike] = row
        self._row_strike_map[row]    = strike

        ce    = self.contracts.get(strike, {}).get('CE')
        pe    = self.contracts.get(strike, {}).get('PE')
        is_atm = abs(strike - self.atm_strike) < 0.001

        # ATM row background highlight
        if is_atm:
            for col in range(self.table.columnCount()):
                bg = QTableWidgetItem()
                bg.setBackground(QColor(BG_ROW_ATM))
                self.table.setItem(row, col, bg)

        self.table.setCellWidget(row, self.CE_BTN,   self._make_btn(ce,  'CE'))
        self.table.setCellWidget(row, self.CE_CHART,  self._make_chart_btn(ce))
        self.table.setItem(      row, self.CE_BID,    self._make_bid_ask(ce, 'bid'))
        self.table.setItem(      row, self.CE_ASK,    self._make_bid_ask(ce, 'ask'))
        self.table.setItem(      row, self.CE_LTP,    self._make_ltp(ce,  True))
        self.table.setCellWidget(row, self.CE_OI,     self._make_oi(ce,   True))
        self.table.setItem(      row, self.STRIKE,    self._make_strike(strike, is_atm))
        self.table.setCellWidget(row, self.PE_OI,     self._make_oi(pe,   False))
        self.table.setItem(      row, self.PE_LTP,    self._make_ltp(pe,  False))
        self.table.setItem(      row, self.PE_BID,    self._make_bid_ask(pe, 'bid'))
        self.table.setItem(      row, self.PE_ASK,    self._make_bid_ask(pe, 'ask'))
        self.table.setCellWidget(row, self.PE_CHART,  self._make_chart_btn(pe))
        self.table.setCellWidget(row, self.PE_BTN,    self._make_btn(pe,  'PE'))

    # ──────────────────────────────────────────────────────────────────────────
    #  Cell factories
    # ──────────────────────────────────────────────────────────────────────────
    def _make_btn(self, c: Optional[Contract], ot: str) -> QPushButton:
        """
        CE button → cyan (#00C4C6)
        PE button → red  (#E0424A)
        """
        b = QPushButton()
        b.setFixedSize(20, 16)
        if not c:
            b.setEnabled(False)
            b.setStyleSheet("background: transparent; border: none;")
            return b

        col = CE_COLOR if ot == 'CE' else PE_COLOR
        b.setObjectName("strikeActionButton")
        b.setText(ot)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: self.strike_selected.emit(c))
        b.setStyleSheet(f"""
            QPushButton#strikeActionButton {{
                background: transparent;
                color: {col};
                border: 1px solid {col}55;
                border-radius: 2px;
                font-size: 7px;
                font-weight: 800;
                padding: 0px;
                min-width: 0px; max-width: 20px;
                min-height: 0px; max-height: 16px;
                letter-spacing: 0.2px;
            }}
            QPushButton#strikeActionButton:hover {{
                background: {col};
                color: #0C0F17;
                border-color: {col};
            }}
        """)
        return b

    def _make_chart_btn(self, c: Optional[Contract]) -> QPushButton:
        b = QPushButton()
        b.setFixedSize(8, 16)
        if not c:
            b.setEnabled(False)
            b.setStyleSheet("background: transparent; border: none;")
            return b
        b.setObjectName("chartBtn")
        b.setText("▲")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: self.chart_requested.emit(c))
        b.setStyleSheet("""
            QPushButton#chartBtn {
                background: transparent;
                color: #3A4458;
                border: none;
                font-size: 5px;
                padding: 0px;
                min-width: 0px; max-width: 8px;
            }
            QPushButton#chartBtn:hover {
                color: #5B9BD5;
            }
        """)
        return b

    def _make_strike(self, s: float, atm: bool) -> QTableWidgetItem:
        i = QTableWidgetItem(f"{s:.0f}")
        i.setTextAlignment(Qt.AlignCenter)
        if atm:
            i.setForeground(QColor(ATM_COLOR))
            f = QFont()
            f.setBold(True)
            f.setPointSize(9)
            i.setFont(f)
        else:
            i.setForeground(QColor(TEXT_MAIN))
        return i

    def _make_ltp(self, c: Optional[Contract], is_call: bool) -> QTableWidgetItem:
        txt = f"{c.ltp:.2f}" if c and c.ltp else "—"
        i   = QTableWidgetItem(txt)
        i.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if c and c.ltp:
            i.setForeground(QColor(CE_COLOR if is_call else PE_COLOR))
            f = QFont(); f.setBold(True)
            i.setFont(f)
        else:
            i.setForeground(QColor(DIM_COLOR))
        return i

    def _make_bid_ask(self, c: Optional[Contract], field: str) -> QTableWidgetItem:
        val = getattr(c, field, 0) if c else 0
        txt = f"{val:.1f}" if val and val > 0 else "—"   # 1 decimal → saves width
        i   = QTableWidgetItem(txt)
        i.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        i.setForeground(QColor("#6B7687"))
        return i

    def _make_oi(self, c: Optional[Contract], is_call: bool) -> OICell:
        """Return a compact OICell widget."""
        cell = OICell(is_call)
        val  = c.oi if c else 0
        cell.set_data(val, self._max_oi)
        return cell

    # ──────────────────────────────────────────────────────────────────────────
    #  Live update helpers
    # ──────────────────────────────────────────────────────────────────────────
    def _update_table(self, rows: Optional[set] = None, refresh_oi: bool = True):
        target = rows if rows is not None else set(range(self.table.rowCount()))
        for row in target:
            strike = self._get_strike_from_row(row)
            if strike is None:
                continue
            ce = self.contracts.get(strike, {}).get('CE')
            pe = self.contracts.get(strike, {}).get('PE')

            self._set_price_item_text(row, self.CE_LTP, ce.ltp if ce else 0)
            self._set_price_item_text(row, self.CE_BID, ce.bid if ce else 0)
            self._set_price_item_text(row, self.CE_ASK, ce.ask if ce else 0)
            self._set_price_item_text(row, self.PE_LTP, pe.ltp if pe else 0)
            self._set_price_item_text(row, self.PE_BID, pe.bid if pe else 0)
            self._set_price_item_text(row, self.PE_ASK, pe.ask if pe else 0)

            if refresh_oi:
                self._update_oi_widget(row, self.CE_OI, ce)
                self._update_oi_widget(row, self.PE_OI, pe)

    def _set_price_item_text(self, row: int, col: int, val: float):
        item = self.table.item(row, col)
        if not item:
            return
        # BID/ASK use 1 decimal, LTP uses 2
        if col in (self.CE_BID, self.CE_ASK, self.PE_BID, self.PE_ASK):
            new_text = f"{val:.1f}" if val and val > 0 else "—"
        else:
            new_text = f"{val:.2f}" if val and val > 0 else "—"
        if item.text() != new_text:
            item.setText(new_text)

    def _update_oi_widget(self, row: int, col: int, c: Optional[Contract]):
        w = self.table.cellWidget(row, col)
        if isinstance(w, OICell):
            w.set_data(c.oi if c else 0, self._max_oi)

    # ──────────────────────────────────────────────────────────────────────────
    #  Navigation
    # ──────────────────────────────────────────────────────────────────────────
    def _jump_to_atm(self):
        if self._user_scrolling:
            return
        for row in range(self.table.rowCount()):
            s = self._get_strike_from_row(row)
            if s and abs(s - self.atm_strike) < 0.001:
                self.table.scrollToItem(
                    self.table.item(row, self.STRIKE),
                    QTableWidget.PositionAtCenter
                )
                QTimer.singleShot(300, self._reset_user_scroll)
                return

    def _jump_to_strike(self, target: float):
        for row in range(self.table.rowCount()):
            s = self._get_strike_from_row(row)
            if s and abs(s - target) < 0.001:
                self.table.scrollToItem(
                    self.table.item(row, self.STRIKE),
                    QTableWidget.PositionAtCenter
                )
                return

    def _force_center_atm(self):
        self._user_scrolling   = False
        self._last_centered_atm = None
        self._jump_to_atm()
        self._schedule_visible_tokens_emit()

    def _get_strike_from_row(self, row: int) -> Optional[float]:
        strike = self._row_strike_map.get(row)
        if strike is not None:
            return strike
        i = self.table.item(row, self.STRIKE)
        if i:
            try:
                return float(i.text())
            except Exception:
                pass
        return None

    def _trade_both(self, strike: float):
        ce = self.contracts.get(strike, {}).get('CE')
        pe = self.contracts.get(strike, {}).get('PE')
        if ce: self.strike_selected.emit(ce)
        if pe: self.strike_selected.emit(pe)

    # ──────────────────────────────────────────────────────────────────────────
    #  Price-movement & auto-adjust
    # ──────────────────────────────────────────────────────────────────────────
    def _check_price_movement(self):
        if not self.auto_adjust_enabled or not self.current_price or not self.symbol:
            return
        try:
            INDEX_EXCHANGE_MAP = {
                "NIFTY":      ("NSE", "NIFTY 50"),
                "BANKNIFTY":  ("NSE", "NIFTY BANK"),
                "FINNIFTY":   ("NSE", "NIFTY FIN SERVICE"),
                "MIDCPNIFTY": ("NSE", "NIFTY MID SELECT"),
                "SENSEX":     ("BSE", "SENSEX"),
                "BANKEX":     ("BSE", "BANKEX"),
            }
            exchange, name = INDEX_EXCHANGE_MAP.get(self.symbol, ("NSE", self.symbol))
            sym        = f"{exchange}:{name}"
            quote_data = self.kite.quote([sym])
            underlying = quote_data.get(sym, {})
            if not underlying:
                return
            new_price = underlying.get('last_price', 0.0)
            if not new_price:
                return
            self._index_ltp = new_price
            new_atm = self._calculate_atm_strike(new_price)
            if self._user_scrolling:
                return
            if self._last_atm_strike is None:
                self._last_atm_strike = new_atm
                return
            if new_atm == self._last_atm_strike:
                return
            self._last_atm_strike = new_atm
            self.update_strikes(self.symbol, new_price, self.expiry, self.user_strike_interval)
        except Exception as e:
            logger.debug(f"Price check failed: {e}")

    def _refresh_ladder(self):
        if self.symbol and self.expiry and self.current_price:
            self.update_strikes(self.symbol, self.current_price, self.expiry, self.user_strike_interval)

    # ──────────────────────────────────────────────────────────────────────────
    #  Visible-token helpers (for WebSocket subscription management)
    # ──────────────────────────────────────────────────────────────────────────
    def _schedule_visible_tokens_emit(self):
        if self._visible_tokens_timer.isActive():
            self._visible_tokens_timer.stop()
        self._visible_tokens_timer.start()

    def _emit_visible_tokens_changed(self):
        self.visible_tokens_changed.emit()

    def get_visible_contract_tokens(self) -> set:
        table = getattr(self, "table", None)
        if table is None:
            return set()
        try:
            if table.rowCount() == 0:
                return set()
            viewport  = table.viewport()
            top_row   = table.rowAt(0)
            bottom_row = table.rowAt(max(0, viewport.height() - 1))
        except RuntimeError:
            return set()
        if top_row    < 0: top_row    = 0
        if bottom_row < 0:
            try:    bottom_row = table.rowCount() - 1
            except RuntimeError: return set()
        tokens: set = set()
        for row in range(top_row, bottom_row + 1):
            strike = self._get_strike_from_row(row)
            if strike is None:
                continue
            for contract in self.contracts.get(strike, {}).values():
                if contract and contract.instrument_token:
                    tokens.add(contract.instrument_token)
        return tokens

    def get_contract_tokens_for_strikes(self, strikes: set) -> set:
        if not strikes:
            return set()
        tokens: set = set()
        for strike in strikes:
            for contract in self.contracts.get(strike, {}).values():
                if contract and contract.instrument_token:
                    tokens.add(contract.instrument_token)
        return tokens

    # ──────────────────────────────────────────────────────────────────────────
    #  Public API
    # ──────────────────────────────────────────────────────────────────────────
    def set_instrument_data(self, data: dict):
        self.instrument_data = data
        self._build_instrument_index()

    def _build_instrument_index(self):
        index = {}
        for symbol, symbol_data in self.instrument_data.items():
            for inst in symbol_data.get('instruments', []):
                try:
                    strike = float(inst.get('strike', 0.0))
                except (TypeError, ValueError):
                    continue
                key = (symbol, inst.get('expiry'), strike, inst.get('instrument_type'))
                index[key] = inst
        self._instrument_index = index

    def calculate_strike_interval(self, symbol: str) -> float:
        if symbol not in self.instrument_data:
            return 50.0
        strikes = sorted(set(float(i['strike'])
                              for i in self.instrument_data[symbol]['instruments']))
        self.available_strikes = strikes
        if len(strikes) < 2:
            return 50.0
        intervals = [s2 - s1 for s1, s2 in zip(strikes, strikes[1:])]
        self.base_strike_interval = min(intervals) if intervals else 50.0
        if self.user_strike_interval <= 0:
            self.user_strike_interval = self.base_strike_interval
        return self.base_strike_interval

    def _calculate_atm_strike(self, price: float) -> float:
        if not self.available_strikes:
            return round(price / self.base_strike_interval) * self.base_strike_interval
        return min(self.available_strikes, key=lambda x: abs(x - price))

    def update_strikes(self, symbol: str, current_price: float,
                       expiry: date, strike_interval: float):
        self._last_centered_atm = None
        self._last_atm_strike   = None
        self.symbol, self.expiry, self.current_price = symbol, expiry, current_price
        self.user_strike_interval = strike_interval
        self.atm_strike = self._calculate_atm_strike(current_price)
        self.contracts.clear()
        self._token_contract_map.clear()
        self._fetch_and_build(symbol, expiry, self._gen_strikes())

    def _gen_strikes(self) -> List[float]:
        if not self.available_strikes:
            return []
        try:
            idx = self.available_strikes.index(self.atm_strike)
        except ValueError:
            return []
        start = max(0, idx - self.num_strikes_below)
        end   = min(len(self.available_strikes), idx + self.num_strikes_above + 1)
        return self.available_strikes[start:end]

    def _fetch_and_build(self, symbol: str, expiry: date, strikes: List[float]):
        to_fetch: List[str] = []
        tradingsymbol_contract_map: Dict[str, Contract] = {}

        for strike in strikes:
            for ot in ['CE', 'PE']:
                inst = self._instrument_index.get((symbol, expiry, strike, ot))
                if not inst:
                    continue
                c = Contract(
                    symbol=symbol,
                    tradingsymbol=inst['tradingsymbol'],
                    instrument_token=inst['instrument_token'],
                    lot_size=inst.get('lot_size', 1),
                    strike=strike,
                    option_type=ot,
                    expiry=expiry,
                )
                if strike not in self.contracts:
                    self.contracts[strike] = {}
                self.contracts[strike][ot] = c
                self._token_contract_map[c.instrument_token] = c
                tradingsymbol_contract_map[c.tradingsymbol]  = c
                to_fetch.append(f"NFO:{inst['tradingsymbol']}")

        if not to_fetch:
            return
        try:
            quotes = self.kite.quote(to_fetch)
            for k, q in quotes.items():
                ts = k.split(':')[-1]
                c  = tradingsymbol_contract_map.get(ts)
                if not c:
                    continue
                c.ltp, c.oi = q.get('last_price', 0.0), q.get('oi', 0)
                depth = q.get('depth', {})
                if depth and depth.get('buy'):
                    c.bid = depth['buy'][0]['price']
                if depth and depth.get('sell'):
                    c.ask = depth['sell'][0]['price']
            self._rebuild_table()
        except Exception as e:
            logger.error(f"Fetch failed: {e}")

    def update_prices(self, data: Union[dict, list]):
        ticks = data if isinstance(data, list) else [data]
        dirty_rows: set = set()
        oi_changed = False

        for tick in ticks:
            token = tick.get('instrument_token')
            if token is None:
                continue
            contract = self._token_contract_map.get(token)
            if not contract:
                continue
            if 'last_price' in tick and tick.get('last_price') != contract.ltp:
                contract.ltp = tick.get('last_price', contract.ltp)
            depth = tick.get('depth', {})
            if depth and depth.get('buy'):
                contract.bid = depth['buy'][0]['price']
            if depth and depth.get('sell'):
                contract.ask = depth['sell'][0]['price']
            new_oi = tick.get('oi', contract.oi)
            if new_oi != contract.oi:
                contract.oi = new_oi
                oi_changed  = True
            row = self._strike_row_map.get(contract.strike)
            if row is not None:
                dirty_rows.add(row)

        if dirty_rows:
            if oi_changed:
                all_oi       = [c.oi for sc in self.contracts.values()
                                 for c in sc.values() if c and c.oi > 0]
                prev_max     = self._max_oi
                self._max_oi = max(all_oi) if all_oi else 1.0
                refresh_rows = (set(range(self.table.rowCount()))
                                if self._max_oi != prev_max else dirty_rows)
                self._update_table(rows=refresh_rows, refresh_oi=True)
            else:
                self._update_table(rows=dirty_rows, refresh_oi=False)

    def update_index_price(self, ltp: float):
        if ltp and ltp > 0:
            self._index_ltp = ltp
            self._check_price_movement()

    def set_auto_adjust(self, enabled: bool):
        self.auto_adjust_enabled = enabled

    def get_current_contracts(self) -> Dict[float, Dict[str, Contract]]:
        return self.contracts.copy()

    def get_strike_interval(self) -> float:
        return self.user_strike_interval

    def get_base_strike_interval(self) -> float:
        return self.base_strike_interval

    def get_ltp_for_token(self, token: int) -> Optional[float]:
        contract = self._token_contract_map.get(token)
        return contract.ltp if contract else None

    def get_ladder_data(self) -> List[Dict]:
        data = []
        for strike, contracts in self.contracts.items():
            ce, pe = contracts.get('CE'), contracts.get('PE')
            data.append({
                'strike':        strike,
                'call_ltp':      getattr(ce, 'ltp', 0.0),
                'put_ltp':       getattr(pe, 'ltp', 0.0),
                'call_contract': ce,
                'put_contract':  pe,
            })
        return sorted(data, key=lambda x: x['strike'])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_weighted_column_widths()
        self._schedule_visible_tokens_emit()