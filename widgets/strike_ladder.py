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
        self._instrument_index: Dict[tuple, dict] = {}
        self._token_contract_map: Dict[int, Contract] = {}
        self._strike_row_map: Dict[float, int] = {}
        self._row_strike_map: Dict[int, float] = {}
        self.auto_adjust_enabled = True
        self._max_oi = 1.0

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._check_price_movement)
        self.update_timer.start(5000)
        self._last_centered_atm: Optional[float] = None
        self._user_scrolling = False
        self._last_atm_strike: Optional[float] = None
        self._index_ltp = None
        self._visible_tokens_timer = QTimer(self)
        self._visible_tokens_timer.setSingleShot(True)
        self._visible_tokens_timer.setInterval(120)
        self._visible_tokens_timer.timeout.connect(self._emit_visible_tokens_changed)

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


    def _reset_user_scroll(self):
        self._user_scrolling = False

    def _apply_styles(self):
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)

        self.table.setCurrentCell(-1, -1)

        h = self.table.horizontalHeader()
        h.setFixedHeight(30)
        h.setMinimumHeight(30)
        h.setMaximumHeight(30)

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
        self._strike_row_map.clear()
        self._row_strike_map.clear()
        all_oi = [c.oi for sc in self.contracts.values() for c in sc.values() if c and c.oi > 0]
        self._max_oi = max(all_oi) if all_oi else 1.0

        # ✅ Reverse the sort - lowest strikes at top, highest at bottom
        for strike in sorted(self.contracts.keys()):  # removed reverse=True
            self._add_row(strike)
        QTimer.singleShot(120, self._force_center_atm)
        QTimer.singleShot(0, self._apply_weighted_column_widths)
        self._schedule_visible_tokens_emit()

    def _add_row(self, strike: float):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 26)
        self._strike_row_map[strike] = row
        self._row_strike_map[row] = strike

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

    def _update_table(self, rows: Optional[set[int]] = None, refresh_oi: bool = True):
        target_rows = rows if rows is not None else set(range(self.table.rowCount()))

        for row in target_rows:
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
        new_text = f"{val:.2f}" if val and val > 0 else "—"
        if item.text() != new_text:
            item.setText(new_text)

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
        strike = self._row_strike_map.get(row)
        if strike is not None:
            return strike
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
        self._build_instrument_index()

    def _build_instrument_index(self):
        index = {}
        for symbol, symbol_data in self.instrument_data.items():
            for inst in symbol_data.get('instruments', []):
                try:
                    strike = float(inst.get('strike', 0.0))
                except (TypeError, ValueError):
                    continue

                key = (
                    symbol,
                    inst.get('expiry'),
                    strike,
                    inst.get('instrument_type')
                )
                index[key] = inst
        self._instrument_index = index

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
        end = min(len(self.available_strikes), idx + self.num_strikes_above + 1)
        return self.available_strikes[start:end]

    def _fetch_and_build(self, symbol: str, expiry: date, strikes: List[float]):
        to_fetch = []
        tradingsymbol_contract_map: Dict[str, Contract] = {}

        for strike in strikes:
            for ot in ['CE', 'PE']:
                inst = self._instrument_index.get((symbol, expiry, strike, ot))
                if not inst:
                    continue

                c = Contract(symbol=symbol, tradingsymbol=inst['tradingsymbol'],
                             instrument_token=inst['instrument_token'],
                             lot_size=inst.get('lot_size', 1), strike=strike,
                             option_type=ot, expiry=expiry)
                if strike not in self.contracts:
                    self.contracts[strike] = {}
                self.contracts[strike][ot] = c
                self._token_contract_map[c.instrument_token] = c
                tradingsymbol_contract_map[c.tradingsymbol] = c
                to_fetch.append(f"NFO:{inst['tradingsymbol']}")

        if not to_fetch:
            return
        try:
            quotes = self.kite.quote(to_fetch)
            for k, q in quotes.items():
                ts = k.split(':')[-1]
                c = tradingsymbol_contract_map.get(ts)
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
        dirty_rows: set[int] = set()
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
                oi_changed = True

            row = self._strike_row_map.get(contract.strike)
            if row is not None:
                dirty_rows.add(row)

        if dirty_rows:
            if oi_changed:
                all_oi = [c.oi for sc in self.contracts.values() for c in sc.values() if c and c.oi > 0]
                prev_max_oi = self._max_oi
                self._max_oi = max(all_oi) if all_oi else 1.0
                max_oi_changed = self._max_oi != prev_max_oi
                refresh_rows = set(range(self.table.rowCount())) if max_oi_changed else dirty_rows
                self._update_table(rows=refresh_rows, refresh_oi=True)
                return

            self._update_table(rows=dirty_rows, refresh_oi=False)

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

    def _format_oi_lakhs(self, oi: int) -> str:
        """Format OI in Lakhs notation."""
        if oi >= 100_000:  # 1 Lakh
            return f"{oi / 100_000:.2f}L"
        elif oi >= 1_000:
            return f"{oi / 1_000:.1f}K"
        return str(oi) if oi > 0 else "—"

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