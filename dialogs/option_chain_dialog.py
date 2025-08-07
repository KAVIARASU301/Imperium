import logging
import math
from datetime import date, datetime
from typing import Dict, Optional

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import (QBrush, QColor, QCloseEvent, QFont, QLinearGradient, QMouseEvent, QShowEvent)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton, QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)
from scipy.stats import norm

from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

# --- Constants for easy theme management ---
PRIMARY_BACKGROUND = "#161A25"
SECONDARY_BACKGROUND = "#212635"
TERTIARY_BACKGROUND = "#2A3140"
HOVER_BACKGROUND = "#2C3243"
BORDER_COLOR = "#3A4458"
PRIMARY_TEXT_COLOR = "#E0E0E0"
SECONDARY_TEXT_COLOR = "#A9B1C3"
ACCENT_COLOR = "#29C7C9"
ACCENT_POSITIVE_COLOR = "#00D1B2"
ACCENT_NEGATIVE_COLOR = "#F85149"
ATM_STRIKE_BG = "#FFD700"
ATM_STRIKE_FG_BRIGHT = "#05DBF7"

INDEX_SYMBOL_MAP = {
    'NIFTY': 'NIFTY 50',
    'BANKNIFTY': 'NIFTY BANK',
    'FINNIFTY': 'NIFTY FIN SERVICE',
    'MIDCPNIFTY': 'NIFTY MID SELECT'
}


# ---------------------------------------------------------------------------------
# UPDATED: Real Black-Scholes and Greeks Calculation Logic
# ---------------------------------------------------------------------------------
def black_scholes_price(S, K, T, r, sigma, is_call):
    if T <= 0 or sigma <= 0: return 0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if is_call:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def implied_volatility(S, K, T, r, market_price, is_call, tolerance=1e-5, max_iterations=100):
    sigma = 0.3  # initial guess
    for _ in range(max_iterations):
        price = black_scholes_price(S, K, T, r, sigma, is_call)
        if T <= 0: return 0
        d1_for_vega = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        vega = S * norm.pdf(d1_for_vega) * math.sqrt(T)
        diff = price - market_price

        if abs(diff) < tolerance:
            return sigma

        if vega == 0:  # Avoid division by zero
            return sigma

        sigma -= diff / vega
        if sigma <= 0:
            sigma = 1e-4
    return max(sigma, 1e-4)


def calculate_greeks(spot_price, strike_price, expiry_date, option_price, is_call, interest_rate=0.06):
    days_to_expiry = max((expiry_date - date.today()).days, 0)
    # If option has expired, Greeks are zero
    if days_to_expiry == 0:
        return {'iv': 0, 'delta': 0, 'theta': 0, 'gamma': 0, 'vega': 0}

    T = days_to_expiry / 365.0
    S = spot_price
    K = strike_price
    r = interest_rate

    # If option price is zero, IV can't be calculated
    if option_price <= 0:
        return {'iv': 0, 'delta': 0, 'theta': 0, 'gamma': 0, 'vega': 0}

    iv = implied_volatility(S, K, T, r, option_price, is_call)

    # --- FIX: Added a check to prevent division by zero ---
    if iv * math.sqrt(T) == 0:
        return {'iv': iv * 100, 'delta': 0, 'theta': 0, 'gamma': 0, 'vega': 0}

    d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)

    delta = norm.cdf(d1) if is_call else norm.cdf(d1) - 1

    # --- FIX: Corrected the Theta calculation for Puts ---
    if is_call:
        theta = (
                        -S * norm.pdf(d1) * iv / (2 * math.sqrt(T))
                        - r * K * math.exp(-r * T) * norm.cdf(d2)
                ) / 365
    else:  # Put option
        theta = (
                        -S * norm.pdf(d1) * iv / (2 * math.sqrt(T))
                        + r * K * math.exp(-r * T) * norm.cdf(-d2)
                ) / 365

    gamma = norm.pdf(d1) / (S * iv * math.sqrt(T))
    vega = S * norm.pdf(d1) * math.sqrt(T) / 100

    return {
        'iv': iv * 100,
        'delta': delta,
        'theta': theta,
        'gamma': gamma,
        'vega': vega
    }

# ---------------------------------------------------------------------------------

def _format_large_number(n: float) -> str:
    """Formats a large number into a compact string with K, L, or Cr suffixes."""
    sign = "+" if n > 0 else ""
    if abs(n) >= 1_00_00_000:
        return f"{sign}{n / 1_00_00_000:.2f}Cr"
    elif abs(n) >= 1_00_000:
        return f"{sign}{n / 1_00_000:.2f}L"
    elif abs(n) >= 1_000:
        return f"{sign}{n / 1_000:.1f}K"
    return f"{n:+,}" if n != 0 else "0"


class OptionChainDialog(QDialog):
    """
    A rewritten, premium Option Chain dialog featuring a compact title bar,
    enhanced color scheme, and improved readability.
    """

    def __init__(self, real_kite_client: KiteConnect, instrument_data: Dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.kite = real_kite_client
        self.instrument_data = instrument_data
        self.contracts_data: Dict[float, Dict[str, dict]] = {}
        self.underlying_instrument = ""
        self.underlying_ltp = 0.0
        self.lot_size = 1
        self._drag_pos: Optional[QPoint] = None
        self._is_initialized = False

        self._setup_window()
        self._setup_ui()
        self._connect_signals()
        self._apply_styles()

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._fetch_market_data)

    def showEvent(self, event: QShowEvent):
        if not self._is_initialized:
            logger.info("Option Chain dialog opened. Initializing data fetch...")
            self._populate_controls()
            self.update_timer.start(2000)
            self._is_initialized = True
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent):
        logger.info("Closing Option Chain dialog. Stopping update timer.")
        self.update_timer.stop()
        super().closeEvent(event)

    def _setup_window(self):
        self.setWindowTitle("Live Option Chain")
        self.setFixedSize(1200, 640)
        self.setModal(False)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowMinimizeButtonHint)

    def _setup_ui(self):
        container = QWidget(self)
        container.setObjectName("mainContainer")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(1, 1, 1, 1)
        container_layout.setSpacing(0)
        container_layout.addWidget(self._create_compact_title_bar())
        self.chain_widget = OptionChainWidget(self)
        container_layout.addWidget(self.chain_widget, 1)

    def _create_compact_title_bar(self) -> QWidget:
        title_bar = QWidget()
        title_bar.setObjectName("compactTitleBar")
        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(15, 5, 5, 5)
        layout.setSpacing(15)

        title = QLabel("Live Option Chain")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.VLine)
        separator1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator1)

        layout.addWidget(QLabel("Symbol:"))
        self.symbol_combo = QComboBox()
        layout.addWidget(self.symbol_combo)
        layout.addWidget(QLabel("Expiry:"))
        self.expiry_combo = QComboBox()
        layout.addWidget(self.expiry_combo)

        self.lot_size_checkbox = QCheckBox("Show Per Lot")
        self.lot_size_checkbox.setChecked(True)
        layout.addWidget(self.lot_size_checkbox)

        self.lot_size_label = QLabel("Lot: -")
        layout.addWidget(self.lot_size_label)

        layout.addStretch(1)

        self.ltp_label = QLabel("LTP: 0.00")
        self.ltp_label.setObjectName("ltpLabel")
        layout.addWidget(self.ltp_label)
        layout.addStretch(1)

        self.minimize_btn = QPushButton("—")
        self.minimize_btn.setObjectName("windowControlButton")
        self.minimize_btn.setFixedSize(30, 30)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(30, 30)

        layout.addWidget(self.minimize_btn)
        layout.addWidget(self.close_btn)

        return title_bar

    def _apply_styles(self):
        self.setStyleSheet(f"""
            #mainContainer {{
                background-color: {PRIMARY_BACKGROUND}; border: 1px solid {BORDER_COLOR};
                border-radius: 12px; font-family: "Segoe UI", sans-serif;
            }}
            #compactTitleBar {{
                background-color: {SECONDARY_BACKGROUND}; border-bottom: 1px solid {BORDER_COLOR};
                border-top-left-radius: 11px; border-top-right-radius: 11px;
            }}
            #dialogTitle {{ color: {PRIMARY_TEXT_COLOR}; font-size: 16px; font-weight: 600; }}
            #compactTitleBar > QLabel {{ color: {SECONDARY_TEXT_COLOR}; font-weight: 500; }}
            #ltpLabel {{
                color: {PRIMARY_TEXT_COLOR}; font-size: 14px; font-weight: bold; padding: 4px 8px;
                background-color: {TERTIARY_BACKGROUND}; border-radius: 6px;
            }}
            QFrame[frameShape="5"] {{ border: 1px solid {TERTIARY_BACKGROUND}; }}
            #windowControlButton, #closeButton {{
                background-color: transparent; border: none; color: {SECONDARY_TEXT_COLOR};
                font-size: 16px; border-radius: 6px;
            }}
            #windowControlButton:hover {{ background-color: {TERTIARY_BACKGROUND}; color: {PRIMARY_TEXT_COLOR}; }}
            #closeButton:hover {{ background-color: {ACCENT_NEGATIVE_COLOR}; color: white; }}
            QComboBox {{
                background-color: {TERTIARY_BACKGROUND}; color: {PRIMARY_TEXT_COLOR};
                border: 1px solid {BORDER_COLOR}; border-radius: 6px; padding: 6px 10px; min-width: 120px;
            }}
            QComboBox:focus {{ border: 1px solid {ACCENT_COLOR}; }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {SECONDARY_BACKGROUND}; border: 1px solid {BORDER_COLOR};
                color: {PRIMARY_TEXT_COLOR}; selection-background-color: {ACCENT_COLOR};
                selection-color: {SECONDARY_BACKGROUND};
            }}
            QCheckBox {{
                color: {SECONDARY_TEXT_COLOR}; font-weight: 500; spacing: 5px;
            }}
            QCheckBox::indicator {{
                width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER_COLOR};
                background-color: {TERTIARY_BACKGROUND};
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT_COLOR};
                image: url(none);
            }}
        """)

    def _connect_signals(self):
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_change)
        self.expiry_combo.currentTextChanged.connect(self._fetch_and_build_chain)
        self.lot_size_checkbox.toggled.connect(self._fetch_market_data)
        self.minimize_btn.clicked.connect(self.showMinimized)
        self.close_btn.clicked.connect(self.close)

    def _populate_controls(self):
        if self.instrument_data:
            symbols = sorted(self.instrument_data.keys())
            self.symbol_combo.addItems(symbols)
            if "NIFTY" in symbols:
                self.symbol_combo.setCurrentText("NIFTY")
            else:
                self.symbol_combo.setCurrentIndex(0)

    def _on_symbol_change(self):
        symbol = self.symbol_combo.currentText()
        if not symbol: return

        symbol_info = self.instrument_data.get(symbol, {})
        self.lot_size = symbol_info.get('lot_size', 1)
        self.lot_size_label.setText(f"Lot: {self.lot_size}")

        self.underlying_instrument = f"NSE:{INDEX_SYMBOL_MAP.get(symbol, symbol)}"
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        if symbol_info:
            expiries = [exp.strftime('%d-%b-%Y') for exp in symbol_info.get('expiries', [])]
            self.expiry_combo.addItems(expiries)
        self.expiry_combo.blockSignals(False)
        self._fetch_and_build_chain()

    def _fetch_and_build_chain(self, expiry_str_arg=None):
        symbol = self.symbol_combo.currentText()
        expiry_str = self.expiry_combo.currentText()
        if not symbol or not expiry_str: return
        self.contracts_data = {}
        try:
            expiry_date = datetime.strptime(expiry_str, '%d-%b-%Y').date()
            if symbol_data := self.instrument_data.get(symbol):
                for inst in symbol_data.get('instruments', []):
                    if inst.get('expiry') == expiry_date:
                        strike, opt_type = inst.get('strike'), inst.get('instrument_type')
                        if strike not in self.contracts_data: self.contracts_data[strike] = {}
                        self.contracts_data[strike][opt_type] = inst
            self._fetch_market_data(is_initial_load=True)
        except ValueError:
            logger.warning(f"Could not parse date: {expiry_str}")

    def _fetch_market_data(self, is_initial_load=False):
        tokens_to_fetch = [self.underlying_instrument]
        for strike_map in self.contracts_data.values():
            for contract in strike_map.values():
                tokens_to_fetch.append(f"NFO:{contract['tradingsymbol']}")
        if not tokens_to_fetch: return
        try:
            market_data = self.kite.quote(tokens_to_fetch)
            if self.underlying_instrument in market_data:
                self.underlying_ltp = market_data[self.underlying_instrument].get('last_price', 0.0)
                self.ltp_label.setText(f"LTP: <b>₹{self.underlying_ltp:,.2f}</b>")

            expiry_str = self.expiry_combo.currentText()
            expiry_date = datetime.strptime(expiry_str, '%d-%b-%Y').date() if expiry_str else None

            show_per_lot = self.lot_size_checkbox.isChecked()
            self.chain_widget.update_chain(
                self.contracts_data, market_data, self.underlying_ltp, expiry_date,
                self.lot_size, show_per_lot
            )

            if is_initial_load:
                self.update_timer.singleShot(150, self.chain_widget.center_on_atm)
        except Exception as e:
            logger.error(f"Failed to fetch option chain market data: {e}", exc_info=True)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()


class OptionChainDelegate(QStyledItemDelegate):
    """
    A custom delegate to take full control of painting table cells,
    bypassing stylesheet conflicts for background colors.
    """

    def paint(self, painter, option, index):
        style_data = index.data(Qt.ItemDataRole.UserRole)

        if isinstance(style_data, dict):
            cell_type = style_data.get('cell_type')
            is_itm = style_data.get('is_itm', False)
            is_atm = style_data.get('is_atm', False)
            side = style_data.get('side')
            value = style_data.get('value')
            max_value = style_data.get('max_value')

            bg_brush = QBrush(QColor(SECONDARY_BACKGROUND))
            fg_color = QColor(SECONDARY_TEXT_COLOR)

            if is_itm and not is_atm:
                itm_bg = QColor(ACCENT_COLOR if side == 'call' else ACCENT_NEGATIVE_COLOR)
                itm_bg.setAlpha(45)
                bg_brush = QBrush(itm_bg)
                fg_color = QColor(PRIMARY_TEXT_COLOR)

            if cell_type == 'oi' and value is not None and max_value is not None:
                oi_bar_color = QColor(ACCENT_COLOR if side == 'call' else ACCENT_NEGATIVE_COLOR)
                ratio = value / max_value if max_value > 0 else 0
                alpha = int(60 + (160 * ratio))
                oi_bar_color.setAlpha(alpha)
                bg_brush = QBrush(oi_bar_color)
                fg_color = QColor(PRIMARY_TEXT_COLOR)
            elif cell_type == 'ltp':
                fg_color = QColor(ACCENT_COLOR)
            elif cell_type == 'strike':
                fg_color = QColor(PRIMARY_TEXT_COLOR)
                if is_atm:
                    gradient = QLinearGradient(option.rect.topLeft(), option.rect.topRight())

                    call_color = QColor(ACCENT_COLOR)
                    call_color.setAlpha(40)

                    put_color = QColor(ACCENT_NEGATIVE_COLOR)
                    put_color.setAlpha(40)

                    gradient.setColorAt(0.1, call_color)
                    gradient.setColorAt(0.9, put_color)

                    bg_brush = QBrush(gradient)
                    fg_color = QColor(ATM_STRIKE_FG_BRIGHT)
            elif cell_type == 'greek':
                fg_color = QColor("#8A9BA8")

            painter.save()
            painter.fillRect(option.rect, bg_brush)
            painter.setPen(fg_color)
            painter.drawText(option.rect, Qt.AlignCenter, index.data())
            painter.restore()
        else:
            super().paint(painter, option, index)

class OptionChainWidget(QWidget):
    """The core table widget with programmatic row highlighting and OI heat map."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.atm_strike = 0.0
        self.underlying_ltp = 0.0
        self.expiry_date = None
        self.lot_size = 1
        self.show_per_lot = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 10)
        self.table = QTableWidget()
        self._setup_table()
        layout.addWidget(self.table)
        self._apply_styles()

    def _setup_table(self):
        self.table.setColumnCount(15)
        headers = [
            "OI", "LTP", "IV", "Delta", "Theta", "Vega", "Gamma",
            "Strike",
            "Gamma", "Vega", "Theta", "Delta", "IV", "LTP", "OI"
        ]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setMouseTracking(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(7, 110)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)

        delegate = OptionChainDelegate(self.table)
        self.table.setItemDelegate(delegate)

    def update_chain(self, contracts_data: Dict, market_data: Dict, underlying_ltp: float, expiry_date,
                     lot_size: int, show_per_lot: bool):
        self.table.setUpdatesEnabled(False)
        if not underlying_ltp:
            self.table.setUpdatesEnabled(True)
            return

        self.underlying_ltp = underlying_ltp
        self.expiry_date = expiry_date
        self.lot_size = lot_size
        self.show_per_lot = show_per_lot
        self.table.setRowCount(0)

        all_strikes = sorted(list(contracts_data.keys()))
        if not all_strikes:
            self.table.setUpdatesEnabled(True)
            return

        if len(all_strikes) > 1:
            strike_step = all_strikes[1] - all_strikes[0]
            self.atm_strike = round(underlying_ltp / strike_step) * strike_step
        else:
            self.atm_strike = all_strikes[0]

        try:
            atm_index = all_strikes.index(self.atm_strike)
        except ValueError:
            atm_index = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - self.atm_strike))

        start_index = max(0, atm_index - 7)
        end_index = min(len(all_strikes), atm_index + 8)
        display_strikes = all_strikes[start_index:end_index]

        max_call_oi, max_put_oi = 0, 0
        for strike in display_strikes:
            if call_contract := contracts_data.get(strike, {}).get('CE'):
                if quote := market_data.get(f"NFO:{call_contract['tradingsymbol']}"):
                    max_call_oi = max(max_call_oi, quote.get('oi', 0))
            if put_contract := contracts_data.get(strike, {}).get('PE'):
                if quote := market_data.get(f"NFO:{put_contract['tradingsymbol']}"):
                    max_put_oi = max(max_put_oi, quote.get('oi', 0))

        for strike in display_strikes:
            row_pos = self.table.rowCount()
            self.table.insertRow(row_pos)
            self.table.setRowHeight(row_pos, 36)
            is_atm_strike = (strike == self.atm_strike)

            strike_item = self._create_item(f"{strike:,.0f}", 'strike', is_atm=is_atm_strike)
            self.table.setItem(row_pos, 7, strike_item)

            if call_contract := contracts_data.get(strike, {}).get('CE'):
                self._populate_side(row_pos, 'call', call_contract, market_data, strike < underlying_ltp, is_atm_strike,
                                    max_call_oi)
            if put_contract := contracts_data.get(strike, {}).get('PE'):
                self._populate_side(row_pos, 'put', put_contract, market_data, strike > underlying_ltp, is_atm_strike,
                                    max_put_oi)

        self.table.setUpdatesEnabled(True)
    def _populate_side(self, row, side, contract, market_data, is_itm, is_atm, max_oi):
        quote_key = f"NFO:{contract.get('tradingsymbol')}"
        data = market_data.get(quote_key, {})
        ltp = data.get('last_price', 0)

        greeks = calculate_greeks(self.underlying_ltp, contract['strike'], self.expiry_date, ltp, side == 'call')

        iv, delta, theta, gamma, vega = [greeks.get(k, 0.0) for k in ['iv', 'delta', 'theta', 'gamma', 'vega']]
        oi = data.get('oi', 0)

        display_ltp = ltp * self.lot_size if self.show_per_lot else ltp
        display_theta = theta * self.lot_size if self.show_per_lot else theta

        cols = [
            (f"{gamma:.4f}", 'greek', {}),
            (f"{vega:.2f}", 'greek', {}),
            (f"{display_theta:,.2f}", 'greek', {}),
            (f"{delta:.2f}", 'greek', {}),
            (f"{iv:.1f}%", 'greek', {}),
            (f"{display_ltp:,.2f}", 'ltp', {}),
            (_format_large_number(oi).replace('+', ''), 'oi', {'value': oi, 'max_value': max_oi})
        ]
        if side == 'call':
            cols.reverse()

        start_col = 0 if side == 'call' else 8

        for i, (text, c_type, c_data) in enumerate(cols):
            item = self._create_item(text, c_type, is_itm, is_atm, side, **c_data)
            self.table.setItem(row, start_col + i, item)

    def _create_item(self, text: str, cell_type: str, is_itm: bool = False, is_atm: bool = False,
                     side: Optional[str] = None, **extra_data):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)

        style_data = {
            'cell_type': cell_type,
            'is_itm': is_itm,
            'is_atm': is_atm,
            'side': side,
            'value': extra_data.get('value'),
            'max_value': extra_data.get('max_value')
        }
        item.setData(Qt.ItemDataRole.UserRole, style_data)

        if is_atm or cell_type == 'oi':
            font = QFont()
            font.setBold(True)
            item.setFont(font)

        return item

    def center_on_atm(self):
        for row in range(self.table.rowCount()):
            strike_item = self.table.item(row, 7)
            try:
                if strike_item and float(strike_item.text().replace(",", "")) == self.atm_strike:
                    self.table.scrollToItem(strike_item, QAbstractItemView.ScrollHint.PositionAtCenter)
                    return
            except (ValueError, AttributeError):
                continue
    def _apply_styles(self):
        self.setStyleSheet(f"""
            QTableWidget {{
                background-color: {PRIMARY_BACKGROUND};
                color: {SECONDARY_TEXT_COLOR};
                gridline-color: {TERTIARY_BACKGROUND};
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
                font-size: 12px;
                font-weight: 500;
            }}
            QHeaderView::section {{
                background-color: {SECONDARY_BACKGROUND};
                color: {SECONDARY_TEXT_COLOR};
                padding: 8px 4px;
                border: none;
                border-bottom: 1px solid {BORDER_COLOR};
                font-weight: bold;
                font-size: 10px;
                text-transform: uppercase;
            }}
            QTableWidget::item {{
                padding: 0px 4px;
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                border: none;
                background-color: {PRIMARY_BACKGROUND};
                width: 12px;
                height: 12px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                background-color: {TERTIARY_BACKGROUND};
                min-width: 20px;
                min-height: 20px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
                background-color: {BORDER_COLOR};
            }}
            QScrollBar::add-line, QScrollBar::sub-line {{
                height: 0px;
                width: 0px;
            }}
        """)