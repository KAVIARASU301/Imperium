import logging
from typing import Dict, List, Optional, Union
from datetime import date

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMenu, QDialog, QFormLayout, QSpinBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressBar, QStyle
)
from PySide6.QtCore import Qt, Signal, QTimer, QPoint, QEvent
from PySide6.QtGui import QColor, QFont
from kiteconnect import KiteConnect

from utils.data_models import Contract

logger = logging.getLogger(__name__)


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


class StrikeLadderWidget(QWidget):
    """Premium compact strike ladder with table design."""

    strike_selected = Signal(Contract)
    chart_requested = Signal(Contract)  # New signal for chart button clicks
    visible_tokens_changed = Signal()
    interval_calculated = Signal(str, float)
    interval_changed = Signal(str, float)

    CE_BTN, CE_CHART, CE_BID, CE_ASK, CE_LTP, CE_OI, STRIKE, PE_OI, PE_LTP, PE_BID, PE_ASK, PE_CHART, PE_BTN = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
    COLUMN_WEIGHTS = {
        CE_BID: 1.0,
        CE_ASK: 1.0,
        CE_LTP: 1.1,
        CE_OI: 1.2,
        PE_OI: 1.2,
        PE_LTP: 1.1,
        PE_BID: 1.0,
        PE_ASK: 1.0,
    }

    def __init__(self, kite_client: KiteConnect):
        super().__init__()

        self.kite = kite_client
        self.symbol, self.expiry, self.current_price = "", None, 0.0
        self.base_strike_interval, self.user_strike_interval = 75.0, 0.0
        self.num_strikes_above, self.num_strikes_below = 15, 15
        self.atm_strike = 0.0
        self.contracts: Dict[float, Dict[str, Contract]] = {}
        self.instrument_data, self.available_strikes = {}, []
        self.auto_adjust_enabled = True
        self._max_oi = 1.0

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._check_price_movement)
        self.update_timer.start(5000)
        self._last_centered_atm: Optional[float] = None
        self._user_scrolling = False
        self._last_atm_strike: Optional[float] = None
        self.underlying_data = {
            'ltp': 0.0,
            'prev_close': 0.0,
            'change_pct': 0.0,
            'day_high': 0.0,
            'day_low': 0.0,
            'volume': 0,
            'vix': 0.0
        }
        self._index_ltp = None
        self._visible_tokens_timer = QTimer(self)
        self._visible_tokens_timer.setSingleShot(True)
        self._visible_tokens_timer.setInterval(120)
        self._visible_tokens_timer.timeout.connect(self._emit_visible_tokens_changed)

        self.vix_value = 0.0
        self._init_ui()
        self._apply_styles()
        self._connect_signals()

    def _init_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        self.table = QTableWidget()
        headers = ["CE", "🗠", "BID", "ASK", "LTP", "OI", "STRIKE", "OI", "LTP", "BID", "ASK", "🗠", "PE"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setMouseTracking(False)
        self.table.viewport().setMouseTracking(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        main.addWidget(self.table, 1)
        self.table.verticalScrollBar().sliderPressed.connect(
            lambda: setattr(self, "_user_scrolling", True)
        )
        self.table.verticalScrollBar().sliderReleased.connect(
            lambda: setattr(self, "_user_scrolling", False)
        )
        self.table.verticalScrollBar().valueChanged.connect(
            lambda: setattr(self, "_user_scrolling", True)
        )
        self.table.verticalScrollBar().valueChanged.connect(
            self._schedule_visible_tokens_emit
        )
        self.table.verticalScrollBar().sliderReleased.connect(
            self._schedule_visible_tokens_emit
        )

        # Create footer
        self._create_footer()
        main.addWidget(self.footer)

    def _reset_user_scroll(self):
        self._user_scrolling = False

    def _create_footer(self):
        """Institutional-grade footer with key metrics."""
        self.footer = QWidget()
        self.footer.setObjectName("ladderFooter")
        self.footer.setFixedHeight(28)

        layout = QHBoxLayout(self.footer)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        # Settings button
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.setFixedSize(21, 21)
        self.settings_btn.setToolTip("Ladder Settings")

        # --- UNDERLYING METRICS ---
        self.underlying_lbl = QLabel("—")
        self.underlying_lbl.setObjectName("underlyingLabel")
        self.underlying_lbl.setToolTip("Underlying LTP & Change")

        # self.range_lbl = QLabel("Range: —")
        # self.range_lbl.setObjectName("footerStat")
        # self.range_lbl.setToolTip("Day High/Low")

        self.vol_lbl = QLabel("Vol: —")
        self.vol_lbl.setObjectName("footerStat")
        self.vol_lbl.setToolTip("Volume Traded")

        # --- OPTIONS METRICS ---
        self.call_oi_lbl = QLabel("CE OI: —")
        self.call_oi_lbl.setObjectName("footerStat")

        self.put_oi_lbl = QLabel("PE OI: —")
        self.put_oi_lbl.setObjectName("footerStat")

        self.pcr_label = QLabel("PCR: —")
        self.pcr_label.setObjectName("pcrLabel")

        self.vix_label = QLabel("VIX: —")
        self.vix_label.setObjectName("vixLabel")

        # Alignment
        for lbl in [self.underlying_lbl, self.vol_lbl,
                    self.call_oi_lbl, self.put_oi_lbl, self.pcr_label, self.vix_label]:
            lbl.setAlignment(Qt.AlignVCenter)

        # Assemble left → right
        layout.addWidget(self.settings_btn)
        layout.addSpacing(6)

        # Underlying section
        layout.addWidget(self.underlying_lbl)
        layout.addWidget(self._footer_sep())
        layout.addWidget(self.vol_lbl)
        layout.addWidget(self._footer_sep())

        # Options section
        layout.addWidget(self.call_oi_lbl)
        layout.addWidget(self._footer_sep())
        layout.addWidget(self.put_oi_lbl)
        layout.addWidget(self._footer_sep())
        layout.addWidget(self.pcr_label)
        layout.addWidget(self._footer_sep())
        layout.addWidget(self.vix_label)

        layout.addStretch(1)

    def _footer_sep(self):
        sep = QLabel("│")
        sep.setStyleSheet("color: #3A4458; font-size: 10px;")
        return sep

    def _apply_styles(self):
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)

        self.table.setCurrentCell(-1, -1)

        h = self.table.horizontalHeader()

        # Fixed columns
        h.setSectionResizeMode(self.CE_BTN, QHeaderView.Fixed)
        h.setSectionResizeMode(self.CE_CHART, QHeaderView.Fixed)
        h.setSectionResizeMode(self.PE_CHART, QHeaderView.Fixed)
        h.setSectionResizeMode(self.PE_BTN, QHeaderView.Fixed)
        h.setSectionResizeMode(self.STRIKE, QHeaderView.Fixed)

        self.table.setColumnWidth(self.CE_BTN, 32)
        self.table.setColumnWidth(self.CE_CHART, 32)
        self.table.setColumnWidth(self.PE_CHART, 32)
        self.table.setColumnWidth(self.PE_BTN, 32)
        self.table.setColumnWidth(self.STRIKE, 75)

        # Stretchable columns
        for col in self.COLUMN_WEIGHTS:
            h.setSectionResizeMode(col, QHeaderView.Stretch)

        self.setStyleSheet("""
            QTableWidget {
                background-image: url("assets/textures/main_window_bg.png");
                color: #E0E0E0;
                border: none;
                font-size: 12px;
            }
            QHeaderView::section {
                background: #041D27;
                color: #A9B1C3;
                padding: 6px 4px;
                border: none;
                font-weight: 600;
                font-size: 11px;
            }
            QTableWidget::item {
                padding: 4px 4px;
                border-bottom: 1px solid #1E2430;
                outline: 0;
            }

            QTableWidget::item:hover {
                background: transparent;
            }

            QTableWidget::item:selected:hover {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(169, 177, 195, 0.25);
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(169, 177, 195, 0.4);
            }
            #ladderFooter {
                background: #041D27;
                border-top: 1px solid #3A4458;
            }

            #settingsBtn {
                background: transparent;
                color: #A9B1C3;
                border: 1px solid #3A4458;
                border-radius: 3px;
                padding: 0px;
            }

            #settingsBtn:hover {
                background: #3A4458;
            }

            #footerStat {
                color: #A9B1C3;
                font-size: 10.5px;
            }

            #pcrLabel {
                font-size: 10.5px;
                font-weight: 700;
            }


            QCheckBox {
                color: #A9B1C3;
                font-size: 11px;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border: 1px solid #3A4458;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background: #29C7C9;
                border-color: #29C7C9;
            }
            #pcrLabel {
                font-size: 12px;
                font-weight: 700;
            }
            #footerStat {
                color: #A9B1C3;
                font-size: 11px;
            }
            QMenu {
                background: #1B2030;
                border: 1px solid #3A4458;
                border-radius: 6px;
                padding: 6px;
            }
            QMenu::item {
                padding: 7px 20px;
                color: #E0E0E0;
                font-size: 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #2A3350;
            }
            #underlyingLabel {
                font-size: 10.5px;
                font-weight: 700;
            }

            #vixLabel {
                font-size: 10.5px;
                font-weight: 700;
            }
        """)

    from PySide6.QtWidgets import QStyle

    def _apply_weighted_column_widths(self):
        total_weight = sum(self.COLUMN_WEIGHTS.values())

        fixed_width = (
                self.table.columnWidth(self.CE_BTN)
                + self.table.columnWidth(self.PE_BTN)
                + self.table.columnWidth(self.STRIKE)
        )

        scrollbar_slack = self.table.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent
        )

        header_slack = 6  # header/grid rounding safety

        available = (
                self.table.viewport().width()
                - fixed_width
                - scrollbar_slack
                - header_slack
        )

        if available <= 0:
            return

        acc = 0
        cols = list(self.COLUMN_WEIGHTS.items())

        for i, (col, weight) in enumerate(cols):
            if i == len(cols) - 1:
                w = available - acc  # give remainder to last column
            else:
                w = int(available * (weight / total_weight))
                acc += w

            self.table.setColumnWidth(col, max(w, 40))

    def _connect_signals(self):
        self.settings_btn.clicked.connect(self._show_settings)
        self.table.customContextMenuRequested.connect(self._show_menu)

    def _show_menu(self, pos: QPoint):
        item = self.table.itemAt(pos)
        if not item:
            return
        strike = self._get_strike_from_row(item.row())
        if not strike:
            return

        menu = QMenu(self)
        jump = menu.addAction(f"Jump to {strike:.0f}")
        jump.triggered.connect(lambda: self._jump_to_strike(strike))
        menu.addSeparator()
        both = menu.addAction("Trade Both CE + PE")
        both.triggered.connect(lambda: self._trade_both(strike))
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _show_settings(self):
        """Settings dialog."""
        d = QDialog(self)
        d.setWindowTitle("Ladder Settings")
        d.setFixedWidth(320)

        layout = QVBoxLayout(d)
        layout.setSpacing(14)

        title = QLabel("Strike Ladder Settings")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(title)

        f = QFormLayout()
        f.setSpacing(10)

        above = QSpinBox()
        above.setRange(5, 30)
        above.setValue(self.num_strikes_above)

        below = QSpinBox()
        below.setRange(5, 30)
        below.setValue(self.num_strikes_below)

        auto_check = QCheckBox()
        auto_check.setChecked(self.auto_adjust_enabled)

        f.addRow("Strikes Above:", above)
        f.addRow("Strikes Below:", below)
        f.addRow("Auto-Adjust ATM:", auto_check)

        layout.addLayout(f)

        atm_btn = QPushButton("Jump to ATM Strike")
        atm_btn.setFixedHeight(32)
        atm_btn.clicked.connect(lambda: (self._jump_to_atm(), d.close()))
        layout.addWidget(atm_btn)

        apply_btn = QPushButton("Apply Settings")
        apply_btn.setFixedHeight(32)
        apply_btn.clicked.connect(lambda: (
            setattr(self, 'num_strikes_above', above.value()),
            setattr(self, 'num_strikes_below', below.value()),
            self.set_auto_adjust(auto_check.isChecked()),
            self._refresh_ladder(),
            d.accept()
        ))
        layout.addWidget(apply_btn)
        d.exec()

    def _rebuild_table(self):
        self.table.setRowCount(0)
        all_oi = [c.oi for sc in self.contracts.values() for c in sc.values() if c and c.oi > 0]
        self._max_oi = max(all_oi) if all_oi else 1.0

        # ✅ Reverse the sort - lowest strikes at top, highest at bottom
        for strike in sorted(self.contracts.keys()):  # removed reverse=True
            self._add_row(strike)
        self._update_stats()
        QTimer.singleShot(120, self._force_center_atm)
        QTimer.singleShot(0, self._apply_weighted_column_widths)
        self._schedule_visible_tokens_emit()

    def _add_row(self, strike: float):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 26)

        ce = self.contracts.get(strike, {}).get('CE')
        pe = self.contracts.get(strike, {}).get('PE')
        is_atm = abs(strike - self.atm_strike) < 0.001

        self.table.setCellWidget(row, self.CE_BTN, self._make_btn(ce))
        self.table.setCellWidget(row, self.CE_CHART, self._make_chart_btn(ce))
        self.table.setItem(row, self.CE_BID, self._make_bid_ask(ce, 'bid'))
        self.table.setItem(row, self.CE_ASK, self._make_bid_ask(ce, 'ask'))
        self.table.setItem(row, self.CE_LTP, self._make_ltp(ce, True))
        self.table.setCellWidget(row, self.CE_OI, self._make_oi(ce, True))
        self.table.setItem(row, self.STRIKE, self._make_strike(strike, is_atm))
        self.table.setCellWidget(row, self.PE_OI, self._make_oi(pe, False))
        self.table.setItem(row, self.PE_LTP, self._make_ltp(pe, False))
        self.table.setItem(row, self.PE_BID, self._make_bid_ask(pe, 'bid'))
        self.table.setItem(row, self.PE_ASK, self._make_bid_ask(pe, 'ask'))
        self.table.setCellWidget(row, self.PE_CHART, self._make_chart_btn(pe))
        self.table.setCellWidget(row, self.PE_BTN, self._make_btn(pe))

    def _make_btn(self, c: Optional[Contract]) -> QPushButton:
        b = QPushButton()
        b.setFixedSize(28, 20)
        if not c:
            b.setEnabled(False)
            b.setStyleSheet("background: transparent;")
            return b
        b.setText(c.option_type)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: self.strike_selected.emit(c))
        col = "#29C7C9" if c.option_type == "CE" else "#F85149"
        b.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {col}; 
                           border: 1px solid {col}40; border-radius: 3px;
                           font-size: 9px; font-weight: 700; }}
            QPushButton:hover {{ background: {col}; color: #161A25; }}
        """)
        return b

    def _make_chart_btn(self, c: Optional[Contract]) -> QPushButton:
        """Create chart button for opening CVD Single Chart Dialog"""
        b = QPushButton()
        b.setFixedSize(28, 20)
        if not c:
            b.setEnabled(False)
            b.setStyleSheet("background: transparent;")
            return b
        b.setText("🗠")  # Minimal line chart icon
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: self.chart_requested.emit(c))
        b.setStyleSheet("""
            QPushButton { 
                background: transparent; 
                color: #A9B1C3; 
                border: 1px solid #3A445840; 
                border-radius: 3px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover { 
                background: #5B9BD5; 
                color: #161A25; 
                border: 1px solid #5B9BD5;
            }
        """)
        return b

    def _make_strike(self, s: float, atm: bool) -> QTableWidgetItem:
        i = QTableWidgetItem(f"{s:.0f}")
        i.setTextAlignment(Qt.AlignCenter)
        if atm:
            i.setForeground(QColor("#29C7C9"))
            f = QFont()
            f.setBold(True)
            i.setFont(f)
        return i

    def _make_ltp(self, c: Optional[Contract], is_call: bool) -> QTableWidgetItem:
        txt = f"{c.ltp:.2f}" if c and c.ltp else "—"
        i = QTableWidgetItem(txt)
        i.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if c and c.ltp:
            i.setForeground(QColor("#29C7C9" if is_call else "#F85149"))
            f = QFont()
            f.setBold(True)
            i.setFont(f)
        return i

    def _make_bid_ask(self, c: Optional[Contract], field: str) -> QTableWidgetItem:
        val = getattr(c, field, 0) if c else 0
        txt = f"{val:.2f}" if val and val > 0 else "—"
        i = QTableWidgetItem(txt)
        i.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        i.setForeground(QColor("#9CA3AF"))
        return i

    def _make_oi(self, c: Optional[Contract], is_call: bool) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(4, 2, 4, 2)
        l.setSpacing(1)

        val = c.oi if c else 0
        lbl = QLabel(format_indian(val))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color: #E0E0E0; font-size: 10px;")

        bar = QProgressBar()
        bar.setMaximum(100)
        bar.setValue(int((val / self._max_oi) * 100) if self._max_oi > 0 else 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(3)
        if is_call:
            bar.setInvertedAppearance(True)

        col = "#29C7C9" if is_call else "#F85149"
        bar.setStyleSheet(f"""
            QProgressBar {{ border: none; border-radius: 1.5px; background: #2A3140; }}
            QProgressBar::chunk {{ background: {col}; border-radius: 1.5px; }}
        """)

        l.addWidget(lbl)
        l.addWidget(bar)
        return w

    def _update_table(self):
        for row in range(self.table.rowCount()):
            strike = self._get_strike_from_row(row)
            if not strike:
                continue
            ce = self.contracts.get(strike, {}).get('CE')
            pe = self.contracts.get(strike, {}).get('PE')

            if ce:
                if ce.ltp:
                    self.table.item(row, self.CE_LTP).setText(f"{ce.ltp:.2f}")
                if ce.bid and ce.bid > 0:
                    self.table.item(row, self.CE_BID).setText(f"{ce.bid:.2f}")
                if ce.ask and ce.ask > 0:
                    self.table.item(row, self.CE_ASK).setText(f"{ce.ask:.2f}")

            if pe:
                if pe.ltp:
                    self.table.item(row, self.PE_LTP).setText(f"{pe.ltp:.2f}")
                if pe.bid and pe.bid > 0:
                    self.table.item(row, self.PE_BID).setText(f"{pe.bid:.2f}")
                if pe.ask and pe.ask > 0:
                    self.table.item(row, self.PE_ASK).setText(f"{pe.ask:.2f}")

            self._update_oi_widget(row, self.CE_OI, ce)
            self._update_oi_widget(row, self.PE_OI, pe)
        self._update_stats()

    def _update_oi_widget(self, row: int, col: int, c: Optional[Contract]):
        w = self.table.cellWidget(row, col)
        if not w or not c:
            return
        lbl = w.findChild(QLabel)
        bar = w.findChild(QProgressBar)
        if lbl:
            lbl.setText(format_indian(c.oi))
        if bar:
            bar.setValue(int((c.oi / self._max_oi) * 100) if self._max_oi > 0 else 0)

    def _update_stats(self):
        ce_oi = sum(c.oi for sc in self.contracts.values() for c in [sc.get('CE')] if c)
        pe_oi = sum(c.oi for sc in self.contracts.values() for c in [sc.get('PE')] if c)

        self.call_oi_lbl.setText(f"CE OI: {self._format_oi_lakhs(ce_oi)}")
        self.put_oi_lbl.setText(f"PE OI: {self._format_oi_lakhs(pe_oi)}")

        pcr = pe_oi / ce_oi if ce_oi > 0 else 0
        col = "#1DE9B6" if pcr > 1 else "#F85149" if pcr < 0.7 else "#FBBF24"
        self.pcr_label.setText(f"PCR: {pcr:.2f}")
        self.pcr_label.setStyleSheet(f"color: {col}; font-weight: 700; font-size: 10.5px;")

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

                # 👇 THIS IS WHERE IT BELONGS
                QTimer.singleShot(300, self._reset_user_scroll)
                return

    def _jump_to_strike(self, target: float):
        for row in range(self.table.rowCount()):
            s = self._get_strike_from_row(row)
            if s and abs(s - target) < 0.001:
                self.table.scrollToItem(self.table.item(row, self.STRIKE),
                                        QTableWidget.PositionAtCenter)
                return

    def _get_strike_from_row(self, row: int) -> Optional[float]:
        i = self.table.item(row, self.STRIKE)
        if i:
            try:
                return float(i.text())
            except:
                pass
        return None

    def _trade_both(self, strike: float):
        ce = self.contracts.get(strike, {}).get('CE')
        pe = self.contracts.get(strike, {}).get('PE')
        if ce:
            self.strike_selected.emit(ce)
        if pe:
            self.strike_selected.emit(pe)

    # Public API
    def set_instrument_data(self, data: dict):
        self.instrument_data = data

    def calculate_strike_interval(self, symbol: str) -> float:
        if symbol not in self.instrument_data:
            return 50.0
        strikes = sorted(set(float(i['strike']) for i in self.instrument_data[symbol]['instruments']))
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

    def update_strikes(self, symbol: str, current_price: float, expiry: date, strike_interval: float):
        self._last_centered_atm = None
        self._last_atm_strike = None
        self.symbol, self.expiry, self.current_price = symbol, expiry, current_price
        self.user_strike_interval = strike_interval
        self.atm_strike = self._calculate_atm_strike(current_price)
        self.contracts.clear()
        self._fetch_and_build(symbol, expiry, self._gen_strikes())

    def _gen_strikes(self) -> List[float]:
        if not self.available_strikes:
            return []
        try:
            idx = self.available_strikes.index(self.atm_strike)
        except ValueError:
            return []
        start = max(0, idx - self.num_strikes_below)
        end = min(len(self.available_strikes), idx + self.num_strikes_above + 1)
        return self.available_strikes[start:end]

    def _fetch_and_build(self, symbol: str, expiry: date, strikes: List[float]):
        to_fetch = []
        for strike in strikes:
            for ot in ['CE', 'PE']:
                for inst in self.instrument_data.get(symbol, {}).get('instruments', []):
                    if inst.get('strike') == strike and inst.get('instrument_type') == ot and inst.get(
                            'expiry') == expiry:
                        c = Contract(symbol=symbol, tradingsymbol=inst['tradingsymbol'],
                                     instrument_token=inst['instrument_token'],
                                     lot_size=inst.get('lot_size', 1), strike=strike,
                                     option_type=ot, expiry=expiry)
                        if strike not in self.contracts:
                            self.contracts[strike] = {}
                        self.contracts[strike][ot] = c
                        to_fetch.append(f"NFO:{inst['tradingsymbol']}")
                        break
        if not to_fetch:
            return
        try:
            quotes = self.kite.quote(to_fetch)
            for k, q in quotes.items():
                ts = k.split(':')[-1]
                for sc in self.contracts.values():
                    for c in sc.values():
                        if c.tradingsymbol == ts:
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
        updated = False
        tick_map = {t['instrument_token']: t for t in ticks}
        for sc in self.contracts.values():
            for c in sc.values():
                if c and c.instrument_token in tick_map:
                    t = tick_map[c.instrument_token]
                    c.ltp = t.get('last_price', c.ltp)
                    depth = t.get('depth', {})
                    if depth and depth.get('buy'):
                        c.bid = depth['buy'][0]['price']
                    if depth and depth.get('sell'):
                        c.ask = depth['sell'][0]['price']
                    c.oi = t.get('oi', c.oi)
                    updated = True
        if updated:
            all_oi = [c.oi for sc in self.contracts.values() for c in sc.values() if c and c.oi > 0]
            self._max_oi = max(all_oi) if all_oi else 1.0
            self._update_table()

    def _check_price_movement(self):
        if not self.auto_adjust_enabled or not self.current_price or not self.symbol:
            return
        try:
            INDEX_EXCHANGE_MAP = {
                "NIFTY": ("NSE", "NIFTY 50"),
                "BANKNIFTY": ("NSE", "NIFTY BANK"),
                "FINNIFTY": ("NSE", "NIFTY FIN SERVICE"),
                "MIDCPNIFTY": ("NSE", "NIFTY MID SELECT"),
                "SENSEX": ("BSE", "SENSEX"),
                "BANKEX": ("BSE", "BANKEX"),
            }

            exchange, name = INDEX_EXCHANGE_MAP.get(
                self.symbol,
                ("NSE", self.symbol)
            )

            sym = f"{exchange}:{name}"

            # 🔥 Fetch full quote for underlying + VIX
            quote_data = self.kite.quote([sym, "NSE:INDIA VIX"])

            # --- UNDERLYING DATA ---
            underlying = quote_data.get(sym, {})
            if underlying:
                ohlc = underlying.get('ohlc', {})
                self.underlying_data.update({
                    'ltp': underlying.get('last_price', 0.0),
                    'prev_close': ohlc.get('close', 0.0),
                    'day_high': ohlc.get('high', 0.0),
                    'day_low': ohlc.get('low', 0.0),
                    'volume': underlying.get('volume', 0)
                })

                # Calculate % change
                if self.underlying_data['prev_close'] > 0:
                    change = self.underlying_data['ltp'] - self.underlying_data['prev_close']
                    self.underlying_data['change_pct'] = (change / self.underlying_data['prev_close']) * 100

                self._update_underlying_display()

            self._index_ltp = self.underlying_data['ltp']

            # --- VIX DATA ---
            vix_data = quote_data.get("NSE:INDIA VIX", {})
            if vix_data:
                self.underlying_data['vix'] = vix_data.get('last_price', 0.0)
                self._update_vix_display()

            # --- ATM LOGIC (existing) ---
            new_price = self.underlying_data['ltp']
            new_atm = self._calculate_atm_strike(new_price)

            if self._user_scrolling:
                return

            if self._last_atm_strike is None:
                self._last_atm_strike = new_atm
                return

            if new_atm == self._last_atm_strike:
                return

            if not self._index_ltp:
                logger.warning("Index LTP unavailable — retaining previous ATM")
                return

            self._last_atm_strike = new_atm
            self.update_strikes(self.symbol, new_price, self.expiry, self.user_strike_interval)

        except Exception as e:
            logger.debug(f"Price check failed: {e}")

    def _refresh_ladder(self):
        if self.symbol and self.expiry and self.current_price:
            self.update_strikes(self.symbol, self.current_price, self.expiry, self.user_strike_interval)

    def _update_underlying_display(self):
        """Update underlying price with color-coded change."""
        d = self.underlying_data

        # Color based on direction
        if d['change_pct'] > 0:
            color = "#1DE9B6"
            sign = "+"
        elif d['change_pct'] < 0:
            color = "#F85149"
            sign = ""
        else:
            color = "#A9B1C3"
            sign = ""

        # Format: "NIFTY 24,850 +0.45%"
        self.underlying_lbl.setText(
            f"{self.symbol} {d['ltp']:.2f} {sign}{d['change_pct']:.2f}%"
        )
        self.underlying_lbl.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 10.5px;"
        )


        # Update volume (abbreviated)
        vol_str = self._format_volume(d['volume'])
        self.vol_lbl.setText(f"Vol: {vol_str}")

    def _format_volume(self, vol: int) -> str:
        """Format volume in K/M/Cr notation."""
        if vol >= 10_000_000:  # 1 Crore
            return f"{vol / 10_000_000:.1f}Cr"
        elif vol >= 100_000:  # 1 Lakh
            return f"{vol / 100_000:.1f}L"
        elif vol >= 1_000:
            return f"{vol / 1_000:.1f}K"
        return str(vol)

    def _format_oi_lakhs(self, oi: int) -> str:
        """Format OI in Lakhs notation."""
        if oi >= 100_000:  # 1 Lakh
            return f"{oi / 100_000:.2f}L"
        elif oi >= 1_000:
            return f"{oi / 1_000:.1f}K"
        return str(oi) if oi > 0 else "—"

    def _update_vix_display(self):
        """Update VIX with color coding."""
        vix = self.underlying_data['vix']

        if vix > 20:
            color = "#F85149"  # High volatility (fear)
        elif vix < 12:
            color = "#1DE9B6"  # Low volatility (calm)
        else:
            color = "#FBBF24"  # Medium

        self.vix_label.setText(f"VIX: {vix:.2f}")
        self.vix_label.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 10.5px;"
        )

    def set_auto_adjust(self, enabled: bool):
        self.auto_adjust_enabled = enabled

    def get_current_contracts(self) -> Dict[float, Dict[str, Contract]]:
        return self.contracts.copy()

    def get_strike_interval(self) -> float:
        return self.user_strike_interval

    def get_base_strike_interval(self) -> float:
        return self.base_strike_interval

    def get_ltp_for_token(self, token: int) -> Optional[float]:
        for sc in self.contracts.values():
            for c in sc.values():
                if c.instrument_token == token:
                    return c.ltp
        return None

    def get_ladder_data(self) -> List[Dict]:
        data = []
        for strike, contracts in self.contracts.items():
            ce, pe = contracts.get('CE'), contracts.get('PE')
            data.append({
                'strike': strike,
                'call_ltp': getattr(ce, 'ltp', 0.0),
                'put_ltp': getattr(pe, 'ltp', 0.0),
                'call_contract': ce,
                'put_contract': pe
            })
        return sorted(data, key=lambda x: x['strike'])

    def update_index_price(self, ltp: float):
        if ltp and ltp > 0:
            self._index_ltp = ltp
            self._check_price_movement()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_weighted_column_widths()
        self._schedule_visible_tokens_emit()

    def _force_center_atm(self):
        self._user_scrolling = False
        self._last_centered_atm = None
        self._jump_to_atm()
        self._schedule_visible_tokens_emit()

    def _schedule_visible_tokens_emit(self):
        if self._visible_tokens_timer.isActive():
            self._visible_tokens_timer.stop()
        self._visible_tokens_timer.start()

    def _emit_visible_tokens_changed(self):
        self.visible_tokens_changed.emit()

    def get_visible_contract_tokens(self) -> set[int]:
        if not hasattr(self, "table") or self.table.rowCount() == 0:
            return set()
        viewport = self.table.viewport()
        top_row = self.table.rowAt(0)
        bottom_row = self.table.rowAt(max(0, viewport.height() - 1))
        if top_row < 0:
            top_row = 0
        if bottom_row < 0:
            bottom_row = self.table.rowCount() - 1
        tokens: set[int] = set()
        for row in range(top_row, bottom_row + 1):
            strike = self._get_strike_from_row(row)
            if strike is None:
                continue
            for contract in self.contracts.get(strike, {}).values():
                if contract and contract.instrument_token:
                    tokens.add(contract.instrument_token)
        return tokens
