import logging
import math
from datetime import date, datetime
from typing import Dict, Optional

from PySide6.QtCore import Qt, QTimer, QPoint, QEvent
from PySide6.QtGui import (QBrush, QColor, QCloseEvent, QFont, QLinearGradient, QMouseEvent, QShowEvent)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton, QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)
from scipy.stats import norm

from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

# --- Enhanced color scheme for better readability ---
PRIMARY_BACKGROUND = "#0D1117"
SECONDARY_BACKGROUND = "#161B22"
TERTIARY_BACKGROUND = "#21262D"
HOVER_BACKGROUND = "#30363D"
BORDER_COLOR = "#30363D"

# Text colors with better contrast
PRIMARY_TEXT_COLOR = "#F0F6FC"
SECONDARY_TEXT_COLOR = "#C9D1D9"
MUTED_TEXT_COLOR = "#8B949E"

# Accent colors
ACCENT_COLOR = "#58A6FF"
CALL_COLOR = "#3FB950"  # Green for calls
PUT_COLOR = "#F85149"  # Red for puts
CALL_ITM_BG = "#1A3A1F"  # Dark green for ITM calls
PUT_ITM_BG = "#3A1A1A"  # Dark red for ITM puts

# ATM strike highlighting
ATM_STRIKE_BG = "#FFA657"
ATM_STRIKE_FG = "#0D1117"

# OI heat map colors
OI_LOW_COLOR = "#1F2937"
OI_HIGH_CALL = "#065F46"  # Dark green
OI_HIGH_PUT = "#7F1D1D"  # Dark red

INDEX_SYMBOL_MAP = {
    'NIFTY': 'NIFTY 50',
    'BANKNIFTY': 'NIFTY BANK',
    'FINNIFTY': 'NIFTY FIN SERVICE',
    'MIDCPNIFTY': 'NIFTY MID SELECT'
}


# ---------------------------------------------------------------------------------
# Black-Scholes and Greeks Calculation
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
    sigma = 0.3
    for _ in range(max_iterations):
        price = black_scholes_price(S, K, T, r, sigma, is_call)
        if T <= 0: return 0
        d1_for_vega = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        vega = S * norm.pdf(d1_for_vega) * math.sqrt(T)
        diff = price - market_price

        if abs(diff) < tolerance:
            return sigma

        if vega == 0:
            return sigma

        sigma -= diff / vega
        if sigma <= 0:
            sigma = 1e-4
    return max(sigma, 1e-4)


def calculate_greeks(spot_price, strike_price, expiry_date, option_price, is_call, interest_rate=0.06):
    days_to_expiry = max((expiry_date - date.today()).days, 0)
    if days_to_expiry == 0:
        return {'iv': 0, 'delta': 0, 'theta': 0, 'gamma': 0, 'vega': 0}

    T = days_to_expiry / 365.0
    S = spot_price
    K = strike_price
    r = interest_rate

    if option_price <= 0:
        return {'iv': 0, 'delta': 0, 'theta': 0, 'gamma': 0, 'vega': 0}

    iv = implied_volatility(S, K, T, r, option_price, is_call)

    if iv * math.sqrt(T) == 0:
        return {'iv': iv * 100, 'delta': 0, 'theta': 0, 'gamma': 0, 'vega': 0}

    d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)

    delta = norm.cdf(d1) if is_call else norm.cdf(d1) - 1

    if is_call:
        theta = (
                        -S * norm.pdf(d1) * iv / (2 * math.sqrt(T))
                        - r * K * math.exp(-r * T) * norm.cdf(d2)
                ) / 365
    else:
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
    """Enhanced number formatting with better readability"""
    if n == 0:
        return "—"

    sign = ""
    abs_n = abs(n)

    if abs_n >= 1_00_00_000:  # Crores
        return f"{sign}{n / 1_00_00_000:.2f}Cr"
    elif abs_n >= 1_00_000:  # Lakhs
        return f"{sign}{n / 1_00_000:.2f}L"
    elif abs_n >= 1_000:  # Thousands
        return f"{sign}{n / 1_000:.1f}K"
    else:
        return f"{n:,.0f}"


def _format_price(price: float) -> str:
    """Format price with appropriate decimal places"""
    if price == 0:
        return "—"
    elif price < 1:
        return f"{price:.3f}"
    elif price < 10:
        return f"{price:.2f}"
    else:
        return f"{price:,.2f}"


def _format_greek(value: float, decimal_places: int = 2) -> str:
    """Format greek values for better readability"""
    if value == 0:
        return "—"
    return f"{value:.{decimal_places}f}"


class OptionChainDialog(QDialog):
    """Premium Option Chain dialog with enhanced readability"""

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

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                if self._is_initialized and not self.update_timer.isActive():
                    self.update_timer.start(2000)
            else:
                self.update_timer.stop()
        super().changeEvent(event)

    def _setup_window(self):
        self.setWindowTitle("Live Option Chain")
        self.setFixedSize(1300, 650)  # Reduced size
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
        layout.setContentsMargins(15, 8, 8, 8)
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
        self.symbol_combo.setObjectName("controlCombo")
        self.symbol_combo.setMinimumWidth(150)
        layout.addWidget(self.symbol_combo)

        layout.addWidget(QLabel("Expiry:"))
        self.expiry_combo = QComboBox()
        self.expiry_combo.setObjectName("controlCombo")
        self.expiry_combo.setMinimumWidth(130)
        layout.addWidget(self.expiry_combo)

        self.per_lot_checkbox = QCheckBox("Show Per Lot")
        self.per_lot_checkbox.setObjectName("controlCheckbox")
        layout.addWidget(self.per_lot_checkbox)

        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.VLine)
        separator2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator2)

        self.spot_label = QLabel("Spot: —")
        self.spot_label.setObjectName("spotLabel")
        layout.addWidget(self.spot_label)

        layout.addStretch()

        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("iconButton")
        self.refresh_btn.setFixedSize(32, 32)
        self.refresh_btn.setToolTip("Refresh Data")
        layout.addWidget(self.refresh_btn)

        self.minimize_btn = QPushButton("−")
        self.minimize_btn.setObjectName("iconButton")
        self.minimize_btn.setFixedSize(32, 32)
        self.minimize_btn.setToolTip("Minimize")
        layout.addWidget(self.minimize_btn)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(32, 32)
        self.close_btn.setToolTip("Close")
        layout.addWidget(self.close_btn)

        return title_bar

    def _connect_signals(self):
        self.close_btn.clicked.connect(self.close)
        self.minimize_btn.clicked.connect(self.showMinimized)
        self.refresh_btn.clicked.connect(self._fetch_market_data)
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_changed)
        self.expiry_combo.currentTextChanged.connect(self._fetch_market_data)
        self.per_lot_checkbox.toggled.connect(self._on_per_lot_changed)

    def _populate_controls(self):
        self.symbol_combo.blockSignals(True)

        if self.instrument_data:
            symbols = sorted(self.instrument_data.keys())
            self.symbol_combo.addItems(symbols)
            # Default to NIFTY or first symbol
            if "NIFTY" in symbols:
                self.symbol_combo.setCurrentText("NIFTY")
            else:
                self.symbol_combo.setCurrentIndex(0)

        self.symbol_combo.blockSignals(False)
        self._on_symbol_changed(self.symbol_combo.currentText())

    def _on_symbol_changed(self, symbol: str):
        if not symbol:
            return

        self.expiry_combo.clear()

        # Get symbol data from the nested structure
        symbol_info = self.instrument_data.get(symbol, {})

        if not symbol_info:
            logger.warning(f"No data found for symbol: {symbol}")
            return

        # Extract lot size and expiries
        self.lot_size = symbol_info.get('lot_size', 1)
        expiries = symbol_info.get('expiries', [])

        # Set underlying instrument
        self.underlying_instrument = INDEX_SYMBOL_MAP.get(symbol, symbol)

        logger.info(f"Symbol: {symbol}, Lot Size: {self.lot_size}, Expiries: {len(expiries)}")

        # Populate expiry dropdown
        if expiries:
            self.expiry_combo.addItems([exp.strftime('%d-%b-%Y') for exp in expiries])

        self._fetch_market_data()

    def _on_per_lot_changed(self, checked: bool):
        self._fetch_market_data()

    def _fetch_market_data(self):
        if not self.expiry_combo.currentText():
            return

        try:
            symbol = self.symbol_combo.currentText()
            expiry_str = self.expiry_combo.currentText()

            if not symbol or not expiry_str:
                return

            expiry_date = datetime.strptime(expiry_str, '%d-%b-%Y').date()

            # Get instruments for this symbol
            symbol_data = self.instrument_data.get(symbol, {})
            instruments = symbol_data.get('instruments', [])

            if not instruments:
                logger.warning(f"No instruments available for {symbol}")
                return

            # Build contracts data for the selected expiry
            self.contracts_data.clear()
            for inst in instruments:
                if inst.get('expiry') == expiry_date:
                    strike = inst.get('strike')
                    opt_type = inst.get('instrument_type')
                    if strike and opt_type:
                        if strike not in self.contracts_data:
                            self.contracts_data[strike] = {}
                        self.contracts_data[strike][opt_type] = inst

            logger.info(f"Loaded {len(self.contracts_data)} strikes for {symbol} {expiry_str}")

            # Fetch underlying spot price
            if self.underlying_instrument:
                try:
                    underlying_quote = self.kite.ltp([f"NSE:{self.underlying_instrument}"])
                    self.underlying_ltp = underlying_quote.get(f"NSE:{self.underlying_instrument}", {}).get(
                        'last_price', 0)
                    self.spot_label.setText(f"Spot: {self.underlying_ltp:,.2f}")
                except Exception as e:
                    logger.warning(f"Could not fetch underlying LTP: {e}")

            # Fetch market data for all option contracts
            all_symbols = []
            for strike_data in self.contracts_data.values():
                for contract in strike_data.values():
                    if 'tradingsymbol' in contract:
                        all_symbols.append(f"NFO:{contract['tradingsymbol']}")

            market_data = {}
            if all_symbols:
                try:
                    market_data = self.kite.quote(all_symbols)
                except Exception as e:
                    logger.error(f"Error fetching market data: {e}")

            # Update the chain widget
            show_per_lot = self.per_lot_checkbox.isChecked()
            self.chain_widget.update_chain(
                self.contracts_data,
                market_data,
                self.underlying_ltp,
                expiry_date,
                self.lot_size,
                show_per_lot
            )
            self.chain_widget.center_on_atm()

        except Exception as e:
            logger.error(f"Error in _fetch_market_data: {e}", exc_info=True)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos and event.buttons() == Qt.LeftButton:
            self.move(self.pos() + event.globalPosition().toPoint() - self._drag_pos)
            self._drag_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {PRIMARY_BACKGROUND};
                border: 1px solid {BORDER_COLOR};
                border-radius: 10px;
            }}
            #mainContainer {{
                background-color: {PRIMARY_BACKGROUND};
                border-radius: 10px;
            }}
            #compactTitleBar {{
                background-color: {SECONDARY_BACKGROUND};
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                border-bottom: 1px solid {BORDER_COLOR};
                min-height: 48px;
            }}
            #dialogTitle {{
                color: {PRIMARY_TEXT_COLOR};
                font-size: 15px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            QLabel {{
                color: {SECONDARY_TEXT_COLOR};
                font-size: 13px;
                font-weight: 500;
            }}
            #spotLabel {{
                color: {ACCENT_COLOR};
                font-size: 14px;
                font-weight: 700;
                padding: 4px 12px;
                background-color: {TERTIARY_BACKGROUND};
                border-radius: 4px;
            }}
            #controlCombo {{
                background-color: {TERTIARY_BACKGROUND};
                color: {PRIMARY_TEXT_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                font-weight: 500;
            }}
            #controlCombo::drop-down {{
                border: none;
                padding-right: 8px;
            }}
            #controlCombo::down-arrow {{
                image: url(down_arrow.png);
                width: 12px;
                height: 12px;
            }}
            #controlCombo:hover {{
                border-color: {ACCENT_COLOR};
                background-color: {HOVER_BACKGROUND};
            }}
            #controlCombo QAbstractItemView {{
                background-color: {SECONDARY_BACKGROUND};
                color: {PRIMARY_TEXT_COLOR};
                selection-background-color: {HOVER_BACKGROUND};
                selection-color: {ACCENT_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 6px;
                padding: 4px;
            }}
            #controlCheckbox {{
                color: {SECONDARY_TEXT_COLOR};
                font-size: 13px;
                font-weight: 500;
                spacing: 8px;
            }}
            #controlCheckbox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {BORDER_COLOR};
                border-radius: 4px;
                background-color: {TERTIARY_BACKGROUND};
            }}
            #controlCheckbox::indicator:checked {{
                background-color: {ACCENT_COLOR};
                border-color: {ACCENT_COLOR};
            }}
            #controlCheckbox::indicator:hover {{
                border-color: {ACCENT_COLOR};
            }}
            #iconButton, #closeButton {{
                background-color: {TERTIARY_BACKGROUND};
                color: {SECONDARY_TEXT_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 6px;
                font-size: 18px;
                font-weight: bold;
            }}
            #iconButton:hover {{
                background-color: {HOVER_BACKGROUND};
                color: {PRIMARY_TEXT_COLOR};
                border-color: {ACCENT_COLOR};
            }}
            #closeButton {{
                font-size: 20px;
            }}
            #closeButton:hover {{
                background-color: {PUT_COLOR};
                color: white;
                border-color: {PUT_COLOR};
            }}
            QFrame[frameShape="5"] {{
                color: {BORDER_COLOR};
                max-width: 1px;
            }}
        """)


class OptionChainDelegate(QStyledItemDelegate):
    """Enhanced delegate with better visual hierarchy and color coding"""

    def paint(self, painter, option, index):
        style_data = index.data(Qt.ItemDataRole.UserRole)
        if not style_data:
            super().paint(painter, option, index)
            return

        painter.save()
        cell_type = style_data.get('cell_type')
        is_itm = style_data.get('is_itm', False)
        is_atm = style_data.get('is_atm', False)
        side = style_data.get('side')

        bg_color = QColor(PRIMARY_BACKGROUND)
        text_color = QColor(PRIMARY_TEXT_COLOR)

        # Strike column - ATM highlighting
        if cell_type == 'strike':
            if is_atm:
                bg_color = QColor(ATM_STRIKE_BG)
                text_color = QColor(ATM_STRIKE_FG)
            else:
                bg_color = QColor(SECONDARY_BACKGROUND)
                text_color = QColor(PRIMARY_TEXT_COLOR)

        # OI columns with heat map
        elif cell_type == 'oi':
            oi_value = style_data.get('value', 0)
            max_oi = style_data.get('max_value', 1)
            if max_oi > 0 and oi_value > 0:
                intensity = min(oi_value / max_oi, 1.0)
                if side == 'call':
                    base = QColor(OI_HIGH_CALL)
                else:
                    base = QColor(OI_HIGH_PUT)

                bg_color = QColor(
                    int(OI_LOW_COLOR[1:3], 16) + int((base.red() - int(OI_LOW_COLOR[1:3], 16)) * intensity),
                    int(OI_LOW_COLOR[3:5], 16) + int((base.green() - int(OI_LOW_COLOR[3:5], 16)) * intensity),
                    int(OI_LOW_COLOR[5:7], 16) + int((base.blue() - int(OI_LOW_COLOR[5:7], 16)) * intensity)
                )
                text_color = QColor(PRIMARY_TEXT_COLOR)
            else:
                bg_color = QColor(TERTIARY_BACKGROUND)

        # LTP columns
        elif cell_type == 'ltp':
            if side == 'call':
                text_color = QColor(CALL_COLOR)
                bg_color = QColor(TERTIARY_BACKGROUND)
            else:
                text_color = QColor(PUT_COLOR)
                bg_color = QColor(TERTIARY_BACKGROUND)

        # Greeks columns
        elif cell_type == 'greek':
            if is_itm:
                if side == 'call':
                    bg_color = QColor(CALL_ITM_BG)
                else:
                    bg_color = QColor(PUT_ITM_BG)
            else:
                bg_color = QColor(TERTIARY_BACKGROUND)
            text_color = QColor(SECONDARY_TEXT_COLOR)

        # Draw background
        painter.fillRect(option.rect, QBrush(bg_color))

        # Draw text with enhanced contrast
        painter.setPen(text_color)
        font = QFont()

        # Bold for important values
        if cell_type in ['oi', 'ltp'] or is_atm:
            font.setWeight(QFont.Weight.Bold)
            font.setPointSize(11)
        else:
            font.setWeight(QFont.Weight.Medium)
            font.setPointSize(10)

        painter.setFont(font)
        painter.drawText(option.rect, Qt.AlignCenter, index.data())
        painter.restore()


class OptionChainWidget(QWidget):
    """Enhanced table widget with improved readability"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.atm_strike = 0.0
        self.underlying_ltp = 0.0
        self.expiry_date = None
        self.lot_size = 1
        self.show_per_lot = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 12)
        self.table = QTableWidget()
        self._setup_table()
        layout.addWidget(self.table)
        self._apply_styles()

    def _setup_table(self):
        self.table.setColumnCount(15)
        headers = [
            "OI", "LTP", "IV", "Δ", "Θ", "ν", "Γ",
            "Strike",
            "Γ", "ν", "Θ", "Δ", "IV", "LTP", "OI"
        ]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setMouseTracking(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(7, 120)

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
            self.table.setRowHeight(row_pos, 42)  # Increased for better spacing
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
            (_format_greek(gamma, 4), 'greek', {}),
            (_format_greek(vega, 2), 'greek', {}),
            (_format_greek(display_theta, 2), 'greek', {}),
            (_format_greek(delta, 2), 'greek', {}),
            (f"{iv:.1f}%" if iv > 0 else "—", 'greek', {}),
            (_format_price(display_ltp), 'ltp', {}),
            (_format_large_number(oi), 'oi', {'value': oi, 'max_value': max_oi})
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
                color: {PRIMARY_TEXT_COLOR};
                gridline-color: {BORDER_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
                font-size: 13px;
                font-weight: 500;
            }}
            QHeaderView::section {{
                background-color: {SECONDARY_BACKGROUND};
                color: {PRIMARY_TEXT_COLOR};
                padding: 10px 6px;
                border: none;
                border-bottom: 2px solid {BORDER_COLOR};
                font-weight: 700;
                font-size: 12px;
                letter-spacing: 0.5px;
            }}
            QTableWidget::item {{
                padding: 8px 6px;
                border: none;
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                border: none;
                background-color: {PRIMARY_BACKGROUND};
                width: 14px;
                height: 14px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                background-color: {TERTIARY_BACKGROUND};
                min-width: 24px;
                min-height: 24px;
                border-radius: 7px;
            }}
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
                background-color: {HOVER_BACKGROUND};
            }}
            QScrollBar::add-line, QScrollBar::sub-line {{
                height: 0px;
                width: 0px;
            }}
        """)
