# widgets/header_toolbar.py
import logging
from datetime import date
from typing import List, Dict

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QComboBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
    QAbstractSpinBox
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, Property, QByteArray
from core.widgets.symbol_picker import SymbolPickerPopup

logger = logging.getLogger(__name__)


class AnimatedButton(QPushButton):
    """Button with smooth hover animations."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._glow_intensity = 0.0
        self._setup_animation()

    def _setup_animation(self):
        self.glow_animation = QPropertyAnimation(
            self,
            QByteArray(b"glow_intensity")
        )
        self.glow_animation.setDuration(180)
        self.glow_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_glow_intensity(self):
        return self._glow_intensity

    def set_glow_intensity(self, value):
        self._glow_intensity = value
        self.update()

    glow_intensity = Property(
        float,
        get_glow_intensity,
        set_glow_intensity,
        None,
        notify=None
    )

    def enterEvent(self, event):
        self.glow_animation.stop()
        self.glow_animation.setStartValue(self._glow_intensity)
        self.glow_animation.setEndValue(1.0)
        self.glow_animation.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.glow_animation.stop()
        self.glow_animation.setStartValue(self._glow_intensity)
        self.glow_animation.setEndValue(0.0)
        self.glow_animation.start()
        super().leaveEvent(event)


class HeaderToolbar(QFrame):
    """Enhanced header toolbar with subtle improvements."""

    settings_changed = Signal(dict)
    exit_all_clicked = Signal()
    lot_size_changed = Signal(int)
    journal_clicked = Signal()

    def __init__(self):
        super().__init__()
        self._user_selected_expiry: Dict[str, str] = {}
        self._suppress_signals = False

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        """Initialize the toolbar UI."""
        self.setFixedHeight(40)
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 0, 10, 0)
        main_layout.setSpacing(10)

        # Quick access buttons
        self.index_buttons = {}
        indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
        for symbol in indices:
            btn = AnimatedButton(symbol)
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

        self.journal_button = AnimatedButton("JOURNAL")
        self.journal_button.setObjectName("journalButton")
        self.journal_button.setToolTip("Open trading journal")
        main_layout.addWidget(self.journal_button)

        self.settings_button = AnimatedButton("⚙️")
        self.settings_button.setObjectName("iconButton")
        self.settings_button.setToolTip("Settings")
        main_layout.addWidget(self.settings_button)

        self.exit_all_button = AnimatedButton("EXIT ALL")
        self.exit_all_button.setObjectName("dangerButton")
        self.exit_all_button.setToolTip("Exit all open positions")
        main_layout.addWidget(self.exit_all_button)

        self.expiry_combo.currentTextChanged.connect(self._on_major_setting_changed)
        self.lot_size_spin.valueChanged.connect(self._on_major_setting_changed)
        self.lot_size_spin.valueChanged.connect(self.lot_size_changed.emit)
        self.exit_all_button.clicked.connect(self.exit_all_clicked.emit)
        self.journal_button.clicked.connect(self.journal_clicked.emit)

    def _create_status_layout(self) -> QVBoxLayout:
        """Compact status display with account information."""
        status_layout = QVBoxLayout()
        status_layout.setSpacing(0)
        status_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.account_label = QLabel("Account: Loading...")
        self.account_label.setObjectName("statusLabel")

        status_layout.addWidget(self.account_label)

        return status_layout

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
        """Create custom symbol picker button."""
        self.symbol_button = AnimatedButton("Select Symbol")
        self.symbol_button.setFixedWidth(118)
        self.symbol_button.setObjectName("symbolPickerButton")
        self.symbol_button.clicked.connect(self._show_symbol_picker)

        self.symbol_picker = SymbolPickerPopup(self)
        self.symbol_picker.symbol_selected.connect(self._on_symbol_picked)

        return self.symbol_button

    def _show_symbol_picker(self):
        """Show the custom symbol picker popup."""
        self.symbol_picker.show_below(self.symbol_button)

    def _on_symbol_picked(self, symbol: str):
        """Handle symbol selection from picker."""
        self.symbol_button.setText(symbol)
        self._suppress_signals = False
        self._on_major_setting_changed()

    def _create_expiry_combo(self):
        self.expiry_combo = QComboBox()
        self.expiry_combo.setFixedWidth(96)
        return self.expiry_combo

    def _create_lot_spinbox(self):
        self.lot_size_spin = QSpinBox()
        self.lot_size_spin.setRange(1, 100)
        self.lot_size_spin.setValue(1)
        self.lot_size_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.lot_size_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lot_size_spin.setFixedWidth(42)
        return self.lot_size_spin

    def _on_quick_symbol_selected(self, symbol: str):
        if self.symbol_button.text() != symbol:
            self.symbol_button.setText(symbol)
            self._on_major_setting_changed()

    def _on_major_setting_changed(self):
        if self._suppress_signals:
            return

        symbol = self.symbol_button.text()
        if not symbol:
            return

        for btn_symbol, btn in self.index_buttons.items():
            btn.setChecked(btn_symbol == symbol)

        selected_expiry = self.expiry_combo.currentText()
        if selected_expiry:
            self._user_selected_expiry[symbol] = selected_expiry

        self.settings_changed.emit(self.get_current_settings())

    def set_symbols(self, symbols: List[str]):
        """Populates the symbol picker."""
        self._suppress_signals = True
        current_selection = self.symbol_button.text()

        self.symbol_picker.set_symbols(symbols)

        if current_selection in symbols:
            self.symbol_button.setText(current_selection)
        elif symbols:
            self.symbol_button.setText(symbols[0])

        self._suppress_signals = False

    def set_active_symbol(self, symbol: str):
        """Sets the currently displayed symbol."""
        self._suppress_signals = True
        self.symbol_button.setText(symbol)
        for btn_symbol, btn in self.index_buttons.items():
            btn.setChecked(btn_symbol == symbol)
        self._suppress_signals = False
        self.settings_changed.emit(self.get_current_settings())

    def set_lot_size(self, lots: int):
        """Sets the lot size."""
        self._suppress_signals = True
        self.lot_size_spin.setValue(lots)
        self._suppress_signals = False

    def update_expiries(self, symbol: str, expiries: List[date], preserve_selection: bool):
        if not expiries:
            return
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
            'symbol': self.symbol_button.text(),
            'expiry': self.expiry_combo.currentText(),
            'lot_size': self.lot_size_spin.value()
        }

    def update_account_info(self, account_id: str, balance: float = None):
        if balance is not None:
            self.account_label.setText(f"{account_id}  |  ₹{int(round(balance)):,}")
        else:
            self.account_label.setText(f"Account: {account_id}")

    def _apply_styles(self):
        self.setStyleSheet("""
            HeaderToolbar {
                background-color: #0C0F17;
                border-bottom: 1px solid #1C2333;
                font-family: "Inter", "Segoe UI", sans-serif;
            }
            #separator {
                background-color: #1C2333;
                width: 1px;
                border: none;
                margin: 8px 2px;
            }
            #quickAccessButton {
                color: #7A8799;
                background-color: transparent;
                border: none;
                border-bottom: 2px solid transparent;
                font-weight: 700;
                font-size: 11px;
                letter-spacing: 0.04em;
                padding: 3px 8px;
                border-radius: 0px;
            }
            #quickAccessButton:hover {
                background-color: #111520;
                color: #C8D0DC;
                border-bottom: 2px solid #253047;
            }
            #quickAccessButton:checked {
                background-color: #071214;
                color: #00C4C6;
                border-bottom: 2px solid #00C4C6;
            }
            #controlLabel {
                color: #7A8799;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.08em;
                background: transparent;
            }
            QComboBox {
                background-color: #0A0D14;
                color: #C8D0DC;
                border: 1px solid #1C2333;
                border-radius: 2px;
                padding: 3px 6px;
                font-size: 12px;
                font-weight: 500;
            }
            QComboBox:hover { border-color: #253047; }
            QComboBox:focus { border-color: #00C4C6; }
            QComboBox::drop-down { border: none; width: 16px; background: transparent; }
            QComboBox::down-arrow {
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #7A8799;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background-color: #0C0F17;
                color: #C8D0DC;
                border: 1px solid #253047;
                selection-background-color: #161C28;
                selection-color: #00C4C6;
                outline: none;
            }
            QSpinBox {
                background-color: #0A0D14;
                color: #C8D0DC;
                border: 1px solid #1C2333;
                border-radius: 2px;
                padding: 3px 6px;
                font-size: 12px;
                font-weight: 600;
            }
            QSpinBox:hover { border-color: #253047; }
            QSpinBox:focus { border-color: #00C4C6; }
            #statusLabel {
                color: #7A8799;
                font-size: 11px;
                font-weight: 500;
                background: transparent;
            }
            #iconButton {
                font-size: 14px;
                color: #7A8799;
                background: transparent;
                border: none;
                padding: 2px 4px;
                border-radius: 2px;
            }
            #iconButton:hover { color: #00C4C6; background-color: #111520; }
            #journalButton {
                background-color: transparent;
                color: #7A8799;
                border: 1px solid #1C2333;
                border-radius: 2px;
                padding: 3px 10px;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.06em;
            }
            #journalButton:hover {
                border-color: #00C4C6;
                color: #00C4C6;
                background-color: #071214;
            }
            #dangerButton {
                background-color: transparent;
                color: #7A8799;
                font-weight: 700;
                border: 1px solid #1C2333;
                border-radius: 2px;
                padding: 3px 10px;
                font-size: 10px;
                letter-spacing: 0.06em;
            }
            #dangerButton:hover {
                background-color: #1A0709;
                border-color: #E0424A;
                color: #E0424A;
            }
            #dangerButton:pressed { background-color: #2A1215; }
            #symbolPickerButton {
                background-color: #0A0D14;
                color: #C8D0DC;
                border: 1px solid #1C2333;
                border-radius: 2px;
                padding: 3px 8px;
                font-size: 12px;
                font-weight: 500;
                text-align: left;
            }
            #symbolPickerButton:hover { border-color: #253047; }
            #symbolPickerButton:focus { border-color: #00C4C6; }
        """)