# widgets/header_toolbar.py
import logging
from datetime import datetime, date
from typing import List, Dict

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QComboBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
    QAbstractSpinBox
)
from PySide6.QtCore import Qt, Signal

logger = logging.getLogger(__name__)


class HeaderToolbar(QFrame):
    """
    A redesigned header toolbar that is now a passive component. It receives
    its state from the main window and does not manage its own settings file.
    """
    settings_changed = Signal(dict)
    exit_all_clicked = Signal()
    lot_size_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self._user_selected_expiry: Dict[str, str] = {}
        self._suppress_signals = False

        self._setup_ui()
        self._apply_styles()
        self._update_market_status()

    def _setup_ui(self):
        """Initialize the toolbar UI components."""
        self.setFixedHeight(46)
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(15, 0, 15, 0)
        main_layout.setSpacing(15)

        self.index_buttons = {}
        indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
        for symbol in indices:
            btn = QPushButton(symbol)
            btn.setCheckable(True)
            btn.setObjectName("quickAccessButton")
            btn.clicked.connect(lambda checked, s=symbol: self._on_quick_symbol_selected(s))
            self.index_buttons[symbol] = btn
            main_layout.addWidget(btn)

        main_layout.addWidget(self._create_separator())
        main_layout.addWidget(self._create_control_group("Symbol", self._create_symbol_combo()))
        main_layout.addWidget(self._create_control_group("Expiry", self._create_expiry_combo()))
        main_layout.addWidget(self._create_control_group("Lots", self._create_lot_spinbox()))

        main_layout.addStretch()
        main_layout.addWidget(self._create_separator())
        main_layout.addLayout(self._create_status_layout())

        self.settings_button = QPushButton("⚙️")
        self.settings_button.setObjectName("iconButton")
        self.settings_button.setToolTip("Settings")
        main_layout.addWidget(self.settings_button)

        self.exit_all_button = QPushButton("EXIT ALL")
        self.exit_all_button.setObjectName("dangerButton")
        self.exit_all_button.setToolTip("Exit all open positions")
        main_layout.addWidget(self.exit_all_button)

        self.symbol_combo.currentTextChanged.connect(self._on_major_setting_changed)
        self.expiry_combo.currentTextChanged.connect(self._on_major_setting_changed)
        self.lot_size_spin.valueChanged.connect(self._on_major_setting_changed)
        self.lot_size_spin.valueChanged.connect(self.lot_size_changed.emit)
        self.exit_all_button.clicked.connect(self.exit_all_clicked.emit)

    def _create_status_layout(self) -> QVBoxLayout:
        status_layout = QVBoxLayout()
        status_layout.setSpacing(2)
        status_layout.setAlignment(Qt.AlignCenter)
        self.account_label = QLabel("Account: Loading...")
        self.account_label.setObjectName("statusLabel")
        self.market_status_label = QLabel("● MARKET CLOSED")
        self.market_status_label.setObjectName("marketStatusLabel")
        status_layout.addWidget(self.account_label)
        status_layout.addWidget(self.market_status_label)
        return status_layout

    def _on_major_setting_changed(self):
        """
        Handles internal UI updates and emits a signal about the new state.
        This method does not save settings.
        """
        if self._suppress_signals:
            return

        symbol = self.symbol_combo.currentText()
        if not symbol:
            return

        for btn_symbol, btn in self.index_buttons.items():
            btn.setChecked(btn_symbol == symbol)

        selected_expiry = self.expiry_combo.currentText()
        if selected_expiry:
            self._user_selected_expiry[symbol] = selected_expiry

        self.settings_changed.emit(self.get_current_settings())

    @staticmethod
    def _create_separator():
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName("separator")
        return sep

    @staticmethod
    def _create_control_group(label_text, widget):
        group = QWidget()
        layout = QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QLabel(label_text.upper())
        label.setObjectName("controlLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return group

    def _create_symbol_combo(self):
        self.symbol_combo = QComboBox()
        self.symbol_combo.setFixedWidth(130)
        return self.symbol_combo

    def _create_expiry_combo(self):
        self.expiry_combo = QComboBox()
        self.expiry_combo.setFixedWidth(110)
        return self.expiry_combo

    def _create_lot_spinbox(self):
        self.lot_size_spin = QSpinBox()
        self.lot_size_spin.setRange(1, 100)
        self.lot_size_spin.setValue(1)  # Start with a default of 1
        self.lot_size_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.lot_size_spin.setAlignment(Qt.AlignCenter)
        self.lot_size_spin.setFixedWidth(50)
        return self.lot_size_spin

    def _on_quick_symbol_selected(self, symbol: str):
        if self.symbol_combo.currentText() != symbol:
            self.symbol_combo.setCurrentText(symbol)

    def set_symbols(self, symbols: List[str]):
        """Populates the symbol dropdown. Called externally by the main window."""
        self._suppress_signals = True
        current_selection = self.symbol_combo.currentText()
        self.symbol_combo.clear()
        self.symbol_combo.addItems(symbols)
        if current_selection in symbols:
            self.symbol_combo.setCurrentText(current_selection)
        self._suppress_signals = False

    def set_active_symbol(self, symbol: str):
        """Sets the currently displayed symbol. Called externally by the main window."""
        self._suppress_signals = True
        self.symbol_combo.setCurrentText(symbol)
        for btn_symbol, btn in self.index_buttons.items():
            btn.setChecked(btn_symbol == symbol)
        self._suppress_signals = False
        self.settings_changed.emit(self.get_current_settings())

    def set_lot_size(self, lots: int):
        """Sets the lot size. Called externally by the main window."""
        self._suppress_signals = True
        self.lot_size_spin.setValue(lots)
        self._suppress_signals = False

    def update_expiries(self, symbol: str, expiries: List[date], preserve_selection: bool):
        if not expiries: return
        self._suppress_signals = True
        expiry_strings = [exp.strftime('%d%b%y').upper() for exp in expiries]
        current_selection = self.expiry_combo.currentText()
        self.expiry_combo.clear()
        self.expiry_combo.addItems(expiry_strings)
        if preserve_selection and current_selection in expiry_strings:
            self.expiry_combo.setCurrentText(current_selection)
        elif expiry_strings:
            self.expiry_combo.setCurrentIndex(0)
        self._suppress_signals = False
        self.settings_changed.emit(self.get_current_settings())

    def get_current_settings(self) -> Dict[str, any]:
        """Returns the current state of the UI controls."""
        return {
            'symbol': self.symbol_combo.currentText(),
            'expiry': self.expiry_combo.currentText(),
            'lot_size': self.lot_size_spin.value()
        }

    def update_account_info(self, account_id: str, balance: float = None):
        if balance is not None:
            self.account_label.setText(f"{account_id}  |  ₹{int(round(balance)):,}")
        else:
            self.account_label.setText(f"Account: {account_id}")

    def _update_market_status(self):
        now = datetime.now().time()
        market_open_time = datetime.strptime("09:15", "%H:%M").time()
        market_close_time = datetime.strptime("15:30", "%H:%M").time()
        is_weekday = date.today().weekday() < 5
        if is_weekday and market_open_time <= now <= market_close_time:
            self.market_status_label.setText("MARKET OPEN")
            self.market_status_label.setStyleSheet("color: #29C7C9;")
        else:
            self.market_status_label.setText("MARKET CLOSED")
            self.market_status_label.setStyleSheet("color: #F85149;")

    def _apply_styles(self):
        """Applies a premium, modern dark theme stylesheet."""
        self.setStyleSheet("""
            HeaderToolbar {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1F2533,
                    stop:1 #141925
                );
                background-image: url("assets/textures/texture_darker.png");
                border-top: 1px solid #0D0F15;      /* visually connects to menu bar */
                border-bottom: 1px solid #0B0E14;   /* anchors content below */
                font-family: "Segoe UI";
            }

            #separator {
                width: 1px;
                background-color: #3A4458;
                border: none;
                margin: 8px 0px;
            }
            #quickAccessButton {
                color: #8A9BA8;
                background-color: transparent;
                border: none;
                font-weight: 600;
                font-size: 13px;
                padding: 6px 10px;
                border-radius: 6px;
            }
            #quickAccessButton:hover {
                background-color: rgba(255, 255, 255, 0.04);
                color: #E0E0E0;
            }
            #quickAccessButton:checked {
                background-color: rgba(41, 199, 201, 0.15);
                color: #FFFFFF;
            }

            #controlLabel {
                color: #A9B1C3; font-size: 11px; font-weight: bold;
            }
            QComboBox, QSpinBox {
                background-color: #212635;
                color: #E0E0E0;
                border: 1px solid #3A4458;
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 13px;
                font-weight: 500;
            }

            QComboBox:focus, QSpinBox:focus { border-color: #29C7C9; }
            QComboBox::drop-down { border: none; }
            #statusLabel {
                color: #A9B1C3; font-size: 11px; font-weight: 600;
            }
            #marketStatusLabel {
                font-size: 9px;
                font-weight: 500;
            }
            #iconButton {
                font-size: 18px; color: #A9B1C3; background-color: transparent;
                border: none; padding: 5px;
            }
            #iconButton:hover { color: #FFFFFF; }
            #dangerButton {
            background-color: rgba(0, 0, 0, 0); /* Transparent but valid in QSS */
            color: #F85149;
            font-weight: bold;
            border: 1px solid #F85149;
            border-radius: 3px;
            padding: 5px 14px;
            font-size: 11px;
        }
        
        #dangerButton:hover {
            background-color: #F85149;
            color: #161A25;
        }
        
        
                """)