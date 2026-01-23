import logging
from typing import Dict, List, Optional, Union
from datetime import date

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QProgressBar)
from PySide6.QtCore import Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve, QByteArray, QAbstractAnimation
from kiteconnect import KiteConnect

from utils.data_models import Contract

logger = logging.getLogger(__name__)


# --- Helper Function for Indian Number Formatting (from your original file) ---
def format_indian(number: int) -> str:
    """Formats a number into the Indian numbering system (lakhs, crores)."""
    if not isinstance(number, int) or number == 0:
        return "-"
    s = str(number)
    if len(s) <= 3:
        return s
    last_three = s[-3:]
    other_digits = s[:-3]
    res = "".join(f",{c}" if i % 2 == 0 and i > 0 else c for i, c in enumerate(reversed(other_digits)))
    return res[::-1] + "," + last_three


class StrikeLadderWidget(QWidget):
    """Strike ladder with dynamic interval calculation and a premium UI."""

    strike_selected = Signal(Contract)
    interval_calculated = Signal(str, float)
    interval_changed = Signal(str, float)
    COLUMN_WIDTHS = [35, 100, 60, 80, 75, 80, 60, 100, 35]  # Adjusted for new layout

    def __init__(self, kite_client: KiteConnect):
        super().__init__()
        # --- All original attributes are preserved ---
        self.kite = kite_client
        self.symbol = ""
        self.expiry = None
        self.current_price = 0.0
        self.base_strike_interval = 75.0
        self.user_strike_interval = 0.0
        self.num_strikes_above = 15
        self.num_strikes_below = 15
        self.atm_strike = 0.0
        self.contracts: Dict[float, Dict[str, Contract]] = {}
        self.instrument_data = {}
        self.auto_adjust_enabled = True
        self.available_strikes = []
        self.atm_row_widget = None
        self._max_oi = 1.0
        self.scroll_animation = None
        self.row_widgets: Dict[float, QWidget] = {}

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._check_price_movement)
        self.update_timer.start(5000)

        # --- UI Setup ---
        self._setup_styles()
        self._setup_ui()

    def _setup_styles(self):
        """Defines the color palette for the widget for a consistent look."""
        self.colors = {
            'bg': '#161A25',
            'bg_header': '#212635',
            'bg_itm_call': 'rgba(41, 199, 201, 0.05)',
            'bg_itm_put': 'rgba(248, 81, 73, 0.05)',
            'bg_atm_strike': '#2A3140',
            'text_header': '#A9B1C3',
            'text_primary': '#E0E0E0',
            'text_secondary': '#8A9BA8',
            'border': '#2A3140',
            'call': '#29C7C9',
            'put': '#F85149',
            'oi_call_bar': '#29C7C9',
            'oi_put_bar': '#F85149',
            'accent': '#3A4458'
        }

    def _setup_ui(self):
        """Initialize UI with the new premium styling."""
        self.setStyleSheet(f"background-color: {self.colors['bg']}; border: none;")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._create_header_row())

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("""
        QScrollArea {
            background: transparent;
        }
        QScrollArea::viewport {
            background: transparent;
        }
        """)

        self.strikes_container = QWidget()
        self.strikes_container.setStyleSheet("background: transparent;")
        self.ladder_layout = QVBoxLayout(self.strikes_container)
        self.ladder_layout.setContentsMargins(0, 0, 0, 0)
        self.ladder_layout.setSpacing(1)  # Tighter rows
        self.ladder_layout.addStretch()

        self.scroll_area.setWidget(self.strikes_container)
        main_layout.addWidget(self.scroll_area)

        self.background = QWidget(self)
        self.background.setObjectName("ladderBg")
        self.background.lower()  # push behind everything

        self.background.setStyleSheet("""
        #ladderBg {
            background-image: url("assets/textures/strike_ladder_bg.png");            
            background-repeat: no-repeat;
            background-position: center;
            background-color: #161A25;
        }
        """)

    def _create_header_row(self) -> QWidget:
        """Creates the styled header for the ladder."""
        header_row = QWidget()
        header_row.setFixedHeight(35)
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(5, 0, 5, 0)
        header_layout.setSpacing(1)
        headers = ["CE", "BID/ASK", "LTP", "OI", "STRIKE", "OI", "LTP", "BID/ASK", "PE"]

        for i, (header, width) in enumerate(zip(headers, self.COLUMN_WIDTHS)):
            label = QLabel(header)
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"""
                background-image: url("assets/textures/cross_stripes.png");            
                background-color: {self.colors['bg_header']};
                color: {self.colors['text_header']};
                padding: 4px 0;
                font-family: 'Segoe UI';
                font-size: 11px;
                font-weight: bold;
            """)
            header_layout.addWidget(label)
        return header_row

    def _create_strike_row_widget(self, strike: float, is_atm: bool) -> QWidget:
        """Creates a single, fully styled row for a strike price."""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(5, 0, 5, 0)
        row_layout.setSpacing(1)

        ce_contract = self.contracts.get(strike, {}).get('CE')
        pe_contract = self.contracts.get(strike, {}).get('PE')

        is_call_itm = strike < self.current_price
        is_put_itm = strike > self.current_price

        # Create and add widgets for the row
        row_layout.addWidget(self._create_option_button(ce_contract))
        row_layout.addWidget(self._create_data_label(f"call_ba_{strike}", ce_contract, 'ba', is_call_itm))
        row_layout.addWidget(self._create_data_label(f"call_ltp_{strike}", ce_contract, 'ltp', is_call_itm))
        row_layout.addWidget(self._create_oi_widget(f"call_oi_{strike}", ce_contract, is_call_itm))

        row_layout.addWidget(self._create_strike_label(strike, is_atm))

        row_layout.addWidget(self._create_oi_widget(f"put_oi_{strike}", pe_contract, is_put_itm))
        row_layout.addWidget(self._create_data_label(f"put_ltp_{strike}", pe_contract, 'ltp', is_put_itm))
        row_layout.addWidget(self._create_data_label(f"put_ba_{strike}", pe_contract, 'ba', is_put_itm))
        row_layout.addWidget(self._create_option_button(pe_contract))

        for i in range(row_layout.count()):
            row_layout.itemAt(i).widget().setFixedWidth(self.COLUMN_WIDTHS[i])
        return row_widget

    @staticmethod
    def _create_strike_label(strike: float, is_atm: bool) -> QLabel:
        """Creates the strike price label with styling from the old design."""
        label = QLabel(f"{strike:.0f}")
        label.setAlignment(Qt.AlignCenter)

        if is_atm:
            # Style for the ATM (At-The-Money) strike row
            label.setStyleSheet("""
                            QLabel {
                                color: #05dbf7;
                                background-color: #161A25;
                                border: 1px solid #444444;
                                border-radius: 4px;
                                padding: 6px 0;
                                font-size: 14px;
                                font-weight: bold;
                                font-family: 'Segoe UI', 'Arial', monospace;
                            }
                        """)
        else:
            # Style for all other non-ATM strike rows
            label.setStyleSheet("""
                QLabel {
                    color: #FFFFFF;
                    background-color: #000614;
                    border: 1px solid #444444;
                    border-radius: 3px;
                    padding: 6px 0;
                    font-size: 13px;
                    font-weight: bold;
                    font-family: 'Segoe UI', 'Arial', monospace;
                }
            """)
        return label


    def _create_data_label(self, name: str, contract: Optional[Contract], type: str, is_itm: bool) -> QLabel:
        text, color = "-", self.colors['text_secondary']
        if contract:
            if type == 'ltp':
                text = f"{contract.ltp:.2f}"
                color = self.colors['call'] if contract.option_type == 'CE' else self.colors['put']
            # --- MODIFICATION START ---
            elif type == 'ba' and contract.bid > 0 and contract.ask > 0:
                text = f"{contract.bid:.2f} / {contract.ask:.2f}"
            # --- MODIFICATION END ---

        label = QLabel(text)
        label.setObjectName(name)
        label.setAlignment(Qt.AlignCenter)
        bg = self.colors['bg_itm_call'] if is_itm and contract and contract.option_type == 'CE' else \
            self.colors['bg_itm_put'] if is_itm and contract and contract.option_type == 'PE' else 'transparent'
        label.setStyleSheet(f"background-color: {bg}; color: {color}; font-size: 13px; font-weight: 500;")
        return label

    def _create_oi_widget(self, name: str, contract: Optional[Contract], is_itm: bool) -> QWidget:
        container = QWidget()
        container.setObjectName(name)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(2)

        oi_value = contract.oi if contract else 0
        oi_label = QLabel(format_indian(oi_value))
        oi_label.setAlignment(Qt.AlignCenter)

        oi_bar = QProgressBar()
        oi_bar.setMaximum(100)
        oi_bar.setValue(int((oi_value / self._max_oi) * 100) if self._max_oi > 0 else 0)
        oi_bar.setTextVisible(False)
        oi_bar.setFixedHeight(3)

        is_call_option = contract and contract.option_type == 'CE'

        # --- MODIFICATION START ---
        # Invert the progress bar direction for Call options
        if is_call_option:
            oi_bar.setInvertedAppearance(True)
        # --- MODIFICATION END ---

        bar_color = self.colors['oi_call_bar'] if is_call_option else self.colors['oi_put_bar']
        bg = self.colors['bg_itm_call'] if is_itm and is_call_option else \
            self.colors['bg_itm_put'] if is_itm and not is_call_option else 'transparent'

        container.setStyleSheet(f"background-color: {bg};")
        oi_label.setStyleSheet("color: #E0E0E0; font-size: 11px;")
        oi_bar.setStyleSheet(
            f"QProgressBar {{ border: none; border-radius: 1.5px; background-color: {self.colors['accent']}; }} "
            f"QProgressBar::chunk {{ background-color: {bar_color}; border-radius: 1.5px; }}")

        layout.addWidget(oi_label)
        layout.addWidget(oi_bar)
        return container

    def _create_option_button(self, contract: Optional[Contract]) -> QPushButton:
        btn = QPushButton()
        btn.setCursor(Qt.PointingHandCursor)
        if not contract:
            btn.setEnabled(False)
            btn.setStyleSheet("background-color: transparent; border: none;")
            return btn

        btn.setText(contract.option_type)
        btn.clicked.connect(lambda: self.strike_selected.emit(contract))

        color_map = {'CE': {'bg': self.colors['call'], 'hover': '#32E0E3'},
                     'PE': {'bg': self.colors['put'], 'hover': '#FA6B64'}}
        style = color_map.get(contract.option_type, color_map['CE'])
        btn.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {style['bg']}; border: 1px solid {self.colors['accent']}; border-radius: 4px; font-size: 11px; font-weight: bold; }}
            QPushButton:hover {{ background-color: {style['bg']}; color: {self.colors['bg']}; }}
        """)
        return btn


    def _update_ladder_ui(self):
        """Update the text/values of existing widgets. Preserves original logic."""
        for strike, row_widget in self.row_widgets.items():
            if not row_widget: continue
            ce_contract = self.contracts.get(strike, {}).get('CE')
            pe_contract = self.contracts.get(strike, {}).get('PE')
            if ce_contract:
                # --- MODIFICATION START (Call side) ---
                row_widget.findChild(QLabel, f"call_ba_{strike}").setText(
                    f"{ce_contract.bid:.2f} / {ce_contract.ask:.2f}" if ce_contract.bid and ce_contract.ask else "-")
                # --- MODIFICATION END ---
                row_widget.findChild(QLabel, f"call_ltp_{strike}").setText(
                    f"{ce_contract.ltp:.2f}" if ce_contract.ltp else "-")
                oi_container = row_widget.findChild(QWidget, f"call_oi_{strike}")
                if oi_container:
                    oi_container.findChild(QLabel).setText(format_indian(ce_contract.oi))
                    oi_container.findChild(QProgressBar).setValue(
                        int((ce_contract.oi / self._max_oi) * 100) if self._max_oi > 0 else 0)
            if pe_contract:
                # --- MODIFICATION START (Put side) ---
                row_widget.findChild(QLabel, f"put_ba_{strike}").setText(
                    f"{pe_contract.bid:.2f} / {pe_contract.ask:.2f}" if pe_contract.bid and pe_contract.ask else "-")
                # --- MODIFICATION END ---
                row_widget.findChild(QLabel, f"put_ltp_{strike}").setText(
                    f"{pe_contract.ltp:.2f}" if pe_contract.ltp else "-")
                oi_container = row_widget.findChild(QWidget, f"put_oi_{strike}")
                if oi_container:
                    oi_container.findChild(QLabel).setText(format_indian(pe_contract.oi))
                    oi_container.findChild(QProgressBar).setValue(
                        int((pe_contract.oi / self._max_oi) * 100) if self._max_oi > 0 else 0)
    # --- ALL BACKEND AND LOGIC METHODS FROM YOUR FILE ARE PRESERVED BELOW ---

    def set_instrument_data(self, data: dict):
        self.instrument_data = data

    def calculate_strike_interval(self, symbol: str) -> float:
        if symbol not in self.instrument_data: return 50.0
        try:
            strikes = sorted(set(float(inst['strike']) for inst in self.instrument_data[symbol]['instruments']))
            self.available_strikes = strikes
            if len(strikes) < 2: return 50.0
            intervals = [s2 - s1 for s1, s2 in zip(strikes, strikes[1:]) if s2 - s1 > 0]
            calculated_interval = float(min(intervals)) if intervals else 50.0
            self.base_strike_interval = calculated_interval
            if self.user_strike_interval <= 0:
                self.user_strike_interval = calculated_interval
            return calculated_interval
        except Exception as e:
            logger.error(f"Error calculating strike interval: {e}")
            return 50.0

    def _calculate_atm_strike(self, price: float) -> float:
        if not self.available_strikes:
            logger.warning("available_strikes is empty. Falling back to interval-based calculation.")
            interval = self.base_strike_interval or 50.0
            return round(price / interval) * interval
        return min(self.available_strikes, key=lambda x: abs(x - price))

    def update_strikes(self, symbol: str, current_price: float, expiry: date, strike_interval: float):
        self.symbol, self.expiry, self.current_price, self.user_strike_interval = symbol, expiry, current_price, strike_interval
        self.atm_strike = self._calculate_atm_strike(current_price)
        self._clear_ladder()
        self.contracts.clear()
        self._fetch_and_build_ladder(symbol, expiry, self._generate_strikes())

    def _generate_strikes(self) -> List[float]:
        if not self.available_strikes:
            logger.warning("Cannot generate strikes, available_strikes list is empty.")
            return []
        try:
            atm_index = self.available_strikes.index(self.atm_strike)
        except ValueError:
            logger.error(f"Calculated ATM strike {self.atm_strike} not in available strikes list. Cannot build ladder.")
            return []
        start_index = max(0, atm_index - self.num_strikes_below)
        end_index = min(len(self.available_strikes), atm_index + self.num_strikes_above + 1)
        return self.available_strikes[start_index:end_index]

    def _clear_ladder(self):
        while self.ladder_layout.count() > 1:
            item = self.ladder_layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        self.row_widgets.clear()
        self.atm_row_widget = None

    def _fetch_and_build_ladder(self, symbol: str, expiry: date, strikes: List[float]):
        instruments_to_fetch = []
        for strike in strikes:
            for opt_type in ['CE', 'PE']:
                if symbol in self.instrument_data:
                    for inst in self.instrument_data[symbol]['instruments']:
                        if inst.get('strike') == strike and inst.get('instrument_type') == opt_type and inst.get(
                                'expiry') == expiry:
                            contract = Contract(symbol=symbol, tradingsymbol=inst['tradingsymbol'],
                                                instrument_token=inst['instrument_token'],
                                                lot_size=inst.get('lot_size', 1), strike=strike, option_type=opt_type,
                                                expiry=expiry)
                            if strike not in self.contracts: self.contracts[strike] = {}
                            self.contracts[strike][opt_type] = contract
                            instruments_to_fetch.append(f"NFO:{inst['tradingsymbol']}")
                            break
        if not instruments_to_fetch: return
        try:
            quotes = self.kite.quote(instruments_to_fetch)
            for instrument, quote in quotes.items():
                tradingsymbol = instrument.split(':')[-1]
                for strike_contracts in self.contracts.values():
                    for contract in strike_contracts.values():
                        if contract.tradingsymbol == tradingsymbol:
                            contract.ltp, contract.oi = quote.get('last_price', 0.0), quote.get('oi', 0)
                            depth = quote.get('depth', {})
                            if depth and depth.get('buy'): contract.bid = depth['buy'][0]['price']
                            if depth and depth.get('sell'): contract.ask = depth['sell'][0]['price']
                            break
            self._redisplay_ladder()
        except Exception as e:
            logger.error(f"Failed to fetch initial quotes for {symbol}: {e}")

    def _redisplay_ladder(self):
        self._clear_ladder()
        all_oi = [c.oi for sc in self.contracts.values() for c in sc.values() if c and c.oi > 0]
        self._max_oi = max(all_oi) if all_oi else 1
        sorted_strikes = sorted(self.contracts.keys())
        for strike in sorted_strikes:
            is_atm = abs(strike - self.atm_strike) < 0.001
            row_widget = self._create_strike_row_widget(strike, is_atm)
            self.row_widgets[strike] = row_widget
            if is_atm: self.atm_row_widget = row_widget
            self.ladder_layout.insertWidget(self.ladder_layout.count() - 1, row_widget)
        if self.atm_row_widget:
            QTimer.singleShot(150, self._center_on_row)

    def update_prices(self, data: Union[dict, list]):
        ticks = data if isinstance(data, list) else [data]
        updated_contracts = False
        ticks_by_token = {tick['instrument_token']: tick for tick in ticks}
        for strike, strike_contracts in self.contracts.items():
            for contract in strike_contracts.values():
                if contract and contract.instrument_token in ticks_by_token:
                    updated_contracts = True
                    tick = ticks_by_token[contract.instrument_token]
                    contract.ltp = tick.get('last_price', contract.ltp)
                    depth = tick.get('depth', {})
                    if depth and depth.get('buy'): contract.bid = depth['buy'][0]['price']
                    if depth and depth.get('sell'): contract.ask = depth['sell'][0]['price']
                    contract.oi = tick.get('oi', contract.oi)
        if updated_contracts:
            all_oi = [c.oi for sc in self.contracts.values() for c in sc.values() if c and c.oi > 0]
            self._max_oi = max(all_oi) if all_oi else 1
            self._update_ladder_ui()

    def _center_on_row(self):
        try:
            if self.atm_row_widget:
                scroll_bar = self.scroll_area.verticalScrollBar()
                widget_y_pos = self.atm_row_widget.y()
                viewport_height = self.scroll_area.viewport().height()
                widget_height = self.atm_row_widget.height()
                target_scroll_value = widget_y_pos - (viewport_height / 2) + (widget_height / 2)
                self.scroll_animation = QPropertyAnimation(scroll_bar, QByteArray(b"value"))
                self.scroll_animation.setDuration(250)
                self.scroll_animation.setStartValue(scroll_bar.value())
                self.scroll_animation.setEndValue(int(target_scroll_value))
                self.scroll_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
                self.scroll_animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        except Exception as e:
            logger.error(f"Failed to center scroll position: {e}")

    def _check_price_movement(self):
        if not self.auto_adjust_enabled or not self.current_price or not self.symbol: return
        try:
            index_map = {'NIFTY': 'NIFTY 50', 'BANKNIFTY': 'NIFTY BANK', 'FINNIFTY': 'NIFTY FIN SERVICE',
                         'MIDCPNIFTY': 'NIFTY MID SELECT'}
            index_symbol = f"NSE:{index_map.get(self.symbol, self.symbol)}"
            ltp_data = self.kite.ltp(index_symbol)
            new_price = ltp_data[index_symbol]['last_price']
            new_atm = self._calculate_atm_strike(new_price)
            if abs(new_atm - self.atm_strike) > 0.001:
                logger.info(f"ATM changed from {self.atm_strike} to {new_atm}")
                self.update_strikes(self.symbol, new_price, self.expiry, self.user_strike_interval)
        except Exception as e:
            logger.debug(f"Price check failed: {e}")

    @staticmethod
    def _format_strike(strike: float) -> str:
        return f"{strike:.0f}"

    def get_ltp_for_token(self, token: int) -> Optional[float]:
        for strike_contracts in self.contracts.values():
            for contract in strike_contracts.values():
                if contract.instrument_token == token:
                    return contract.ltp
        return None

    def set_auto_adjust(self, enabled: bool):
        self.auto_adjust_enabled = enabled

    def get_current_contracts(self) -> Dict[float, Dict[str, Contract]]:
        return self.contracts.copy()

    def get_strike_interval(self) -> float:
        return self.user_strike_interval

    def get_base_strike_interval(self) -> float:
        return self.base_strike_interval

    def get_ladder_data(self) -> List[Dict]:
        data = []
        for strike, contracts_by_type in self.contracts.items():
            call, put = contracts_by_type.get('CE'), contracts_by_type.get('PE')
            data.append({'strike': strike, 'call_ltp': getattr(call, 'ltp', 0.0), 'put_ltp': getattr(put, 'ltp', 0.0),
                         'call_contract': call, 'put_contract': put})
        return sorted(data, key=lambda x: x['strike'])

    def resizeEvent(self, event):
        if hasattr(self, "background"):
            self.background.setGeometry(self.rect())
        super().resizeEvent(event)
