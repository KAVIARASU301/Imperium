# dialogs/quick_order_dialog.py
import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QDoubleSpinBox, QPushButton, QWidget, QFrame,
    QRadioButton, QAbstractSpinBox, QCheckBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent

from utils.pricing_utils import calculate_smart_limit_price
from utils.data_models import Contract, Position
import locale

try:
    locale.setlocale(locale.LC_ALL, 'en_IN.UTF-8')
except locale.Error:
    locale.setlocale(locale.LC_ALL, '')

logger = logging.getLogger(__name__)


class QuickOrderDialog(QDialog):
    """
    A premium, compact quick order dialog with real-time price refresh
    and functionality to be pre-filled for modifying existing orders.
    """
    order_placed = Signal(dict)
    refresh_requested = Signal(str)

    def __init__(self, parent, contract: Contract, default_lots: int):
        super().__init__(parent)
        self.contract = contract
        self._drag_pos = None
        self._last_ltp = None

        self._setup_window(parent)
        self._setup_ui(default_lots)
        self._apply_styles()
        self.show()
        self.activateWindow()

    def _setup_window(self, parent):
        """Fixed Windows-compatible positioning"""
        self.setWindowTitle("Quick Order")
        self.setModal(False)
        self.setMinimumSize(340, 550)  # Increased height for SL/TP fields
        self.setMaximumWidth(680)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # Windows-safe positioning
        from PySide6.QtGui import QCursor, QGuiApplication
        import platform

        cursor_pos = QCursor.pos()
        x_offset = 20
        y_offset = 10

        # Get the screen containing the cursor (Windows multi-monitor fix)
        screen = QGuiApplication.screenAt(cursor_pos)
        if not screen:
            screen = QGuiApplication.primaryScreen()

        screen_rect = screen.availableGeometry()

        # Get dialog size with fallback
        dialog_size = self.sizeHint()
        if dialog_size.width() <= 0 or dialog_size.height() <= 0:
            dialog_size = self.minimumSize()

        # Calculate position
        x = cursor_pos.x() + x_offset
        y = cursor_pos.y() + y_offset

        # Windows-specific DPI handling
        if platform.system() == "Windows":
            device_ratio = screen.devicePixelRatio()
            if device_ratio > 1.0:
                x = int(x / device_ratio) * device_ratio
                y = int(y / device_ratio) * device_ratio

        # Ensure dialog stays within screen bounds
        if x + dialog_size.width() > screen_rect.right():
            x = screen_rect.right() - dialog_size.width() - 10
        if y + dialog_size.height() > screen_rect.bottom():
            y = screen_rect.bottom() - dialog_size.height() - 10
        if x < screen_rect.left():
            x = screen_rect.left() + 10
        if y < screen_rect.top():
            y = screen_rect.top() + 10

        self.move(int(x), int(y))

    def _setup_ui(self, default_lots):
        container = QWidget(self)
        container.setObjectName("mainContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 10, 20, 20)
        container_layout.setSpacing(15)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout.addLayout(self._create_title_bar())
        container_layout.addSpacing(5)
        self._create_instrument_info(container_layout)
        self._create_form_controls(container_layout, default_lots)
        self._create_action_buttons(container_layout)

    def _create_title_bar(self):
        title_layout = QHBoxLayout()
        title = QLabel("Quick Order")
        title.setObjectName("dialogTitle")
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(35, 35)
        self.close_btn.clicked.connect(self.reject)
        title_layout.addWidget(title)
        title_layout.addStretch()
        title_layout.addWidget(self.close_btn)
        return title_layout

    def _create_instrument_info(self, parent_layout):
        self.symbol_label = QLabel(self.contract.tradingsymbol)
        self.symbol_label.setObjectName("symbolLabel")
        parent_layout.addWidget(self.symbol_label)

        self.info_label = QLabel()
        self.info_label.setObjectName("infoLabel")
        self.info_label.setText(
            f"""
            <span class="badge strike">
                STRIKE ₹{int(self.contract.strike):,}
            </span>
            &nbsp;&nbsp;
            <span class="badge ltp">
                LTP ₹{self.contract.ltp:.2f}
            </span>
            """
        )

        parent_layout.addWidget(self.info_label)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        parent_layout.addWidget(divider)

    def _create_form_controls(self, parent_layout, default_lots):
        form_layout = QGridLayout()
        form_layout.setHorizontalSpacing(15)
        form_layout.setVerticalSpacing(12)

        self.buy_radio = QRadioButton("BUY")
        self.buy_radio.setObjectName("buyRadio")
        self.buy_radio.setChecked(True)
        self.sell_radio = QRadioButton("SELL")
        self.sell_radio.setObjectName("sellRadio")

        transaction_layout = QHBoxLayout()
        transaction_layout.addWidget(self.buy_radio)
        transaction_layout.addSpacing(20)
        transaction_layout.addWidget(self.sell_radio)
        transaction_layout.addStretch()
        form_layout.addLayout(transaction_layout, 0, 0, 1, 2)

        form_layout.addWidget(QLabel("Lots"), 1, 0)
        self.lots_spinbox = QDoubleSpinBox()
        self.lots_spinbox.setRange(1, 1000)
        self.lots_spinbox.setDecimals(0)
        self.lots_spinbox.setValue(default_lots)
        form_layout.addWidget(self.lots_spinbox, 1, 1)

        form_layout.addWidget(QLabel("Limit Price"), 2, 0)
        self.price_spinbox = QDoubleSpinBox()
        self.price_spinbox.setRange(0.05, 50000.0)
        self.price_spinbox.setDecimals(2)
        self.price_spinbox.setSingleStep(0.05)
        self.price_spinbox.setValue(calculate_smart_limit_price(self.contract))
        self.price_spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        form_layout.addWidget(self.price_spinbox, 2, 1)

        # --- UPDATED SL/TP/TSL FIELDS ---
        # Stop Loss
        self.sl_checkbox = QCheckBox("Stop Loss (₹)")
        self.sl_spinbox = QDoubleSpinBox()
        self.sl_spinbox.setRange(0, 10000000)
        self.sl_spinbox.setDecimals(0)
        self.sl_spinbox.setSingleStep(500)
        self.sl_spinbox.setValue(1000)
        self.sl_spinbox.setEnabled(False)
        self.sl_checkbox.toggled.connect(self.sl_spinbox.setEnabled)
        form_layout.addWidget(self.sl_checkbox, 3, 0)
        form_layout.addWidget(self.sl_spinbox, 3, 1)

        # Take Profit
        self.tp_checkbox = QCheckBox("Take Profit (₹)")
        self.tp_spinbox = QDoubleSpinBox()
        self.tp_spinbox.setRange(0, 10000000)
        self.tp_spinbox.setDecimals(0)
        self.tp_spinbox.setSingleStep(500)
        self.tp_spinbox.setValue(1500)
        self.tp_spinbox.setEnabled(False)
        self.tp_checkbox.toggled.connect(self.tp_spinbox.setEnabled)
        form_layout.addWidget(self.tp_checkbox, 4, 0)
        form_layout.addWidget(self.tp_spinbox, 4, 1)

        # Trailing SL
        self.tsl_checkbox = QCheckBox("Trailing SL (₹)")
        self.tsl_spinbox = QDoubleSpinBox()
        self.tsl_spinbox.setRange(0, 10000000)
        self.tsl_spinbox.setDecimals(0)
        self.tsl_spinbox.setSingleStep(500)
        self.tsl_spinbox.setValue(1000)
        self.tsl_spinbox.setEnabled(False)
        self.tsl_checkbox.toggled.connect(self.tsl_spinbox.setEnabled)
        form_layout.addWidget(self.tsl_checkbox, 5, 0)
        form_layout.addWidget(self.tsl_spinbox, 5, 1)

        self.total_value_label = QLabel()
        self.total_value_label.setObjectName("totalValueLabel")
        self.total_value_label.setAlignment(Qt.AlignCenter)
        form_layout.addWidget(self.total_value_label, 6, 0, 1, 2)
        parent_layout.addLayout(form_layout)
        parent_layout.addStretch()

        self.lots_spinbox.valueChanged.connect(self._update_summary)
        self.price_spinbox.valueChanged.connect(self._update_summary)
        self._update_summary()
        self.buy_radio.toggled.connect(self._update_summary)
        self.sell_radio.toggled.connect(self._update_summary)

    def _create_action_buttons(self, parent_layout):
        action_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh Price")
        self.refresh_btn.setObjectName("refreshButton")
        self.refresh_btn.clicked.connect(lambda: self.refresh_requested.emit(self.contract.tradingsymbol))

        self.place_order_btn = QPushButton("PLACE ORDER")
        self.place_order_btn.setObjectName("confirmButton")
        self.place_order_btn.clicked.connect(self._accept_dialog)

        action_layout.addWidget(self.refresh_btn)
        action_layout.addWidget(self.place_order_btn)
        parent_layout.addLayout(action_layout)

    def _accept_dialog(self):
        """
        Gathers order parameters, closes the dialog immediately, and then
        emits the signal for the main window to process the order.
        """
        from kiteconnect import KiteConnect

        avg_price = self.price_spinbox.value()
        quantity = int(self.lots_spinbox.value()) * self.contract.lot_size

        stop_loss_value = self.sl_spinbox.value() if self.sl_checkbox.isChecked() else 0
        take_profit_value = self.tp_spinbox.value() if self.tp_checkbox.isChecked() else 0

        stop_loss_price = avg_price - (stop_loss_value / quantity) if stop_loss_value > 0 else None
        take_profit_price = avg_price + (take_profit_value / quantity) if take_profit_value > 0 else None

        order_parameters = {
            'contract': self.contract,
            'quantity': quantity,
            'price': avg_price,
            'order_type': 'LIMIT',
            'transaction_type': KiteConnect.TRANSACTION_TYPE_BUY if self.buy_radio.isChecked() else KiteConnect.TRANSACTION_TYPE_SELL,
            'stop_loss_price': stop_loss_price,
            'target_price': take_profit_price,
            'trailing_stop_loss': self.tsl_spinbox.value() if self.tsl_checkbox.isChecked() else 0,
        }
        # FIX: Close the dialog *before* emitting the signal
        self.accept()
        # Now emit the signal for the main window to handle the order execution
        self.order_placed.emit(order_parameters)

    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1A1F2E, stop:1 #151923);
                border: 1px solid #2D3548;
                border-radius: 16px;
                font-family: "Inter", "Segoe UI", sans-serif;
            }

            #dialogTitle { 
                color: #F0F2F5; 
                font-size: 14px; 
                font-weight: 600;
                letter-spacing: 0.3px;
            }

            #closeButton {
                background-color: transparent; 
                border: none; 
                color: #6B7280;
                font-size: 18px; 
                font-weight: bold;
                padding: 6px;
            }
            #closeButton:hover { 
                background-color: rgba(248, 81, 73, 0.15);
                color: #FF6B6B;
                border-radius: 6px;
            }

            #symbolLabel { 
                color: #FFFFFF; 
                font-size: 26px; 
                font-weight: 600;
                letter-spacing: -0.5px;
            }

            #infoLabel { 
                font-size: 12.5px;
                color: #9CA3AF;
                font-weight: 500;
            }

            #divider { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 transparent, stop:0.5 #2D3548, stop:1 transparent);
                height: 1px; 
                border: none;
                margin: 4px 0px;
            }

            QLabel { 
                color: #D1D5DB; 
                font-size: 13px;
                font-weight: 500;
            }

            QRadioButton { 
                spacing: 10px; 
                color: #D1D5DB; 
                font-weight: 600;
                font-size: 13px;
            }
            QRadioButton::indicator {
                width: 20px; 
                height: 20px; 
                border-radius: 10px;
                background-color: #1F2937; 
                border: 2px solid #374151;
            }
            #buyRadio::indicator:checked { 
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
                    fx:0.5, fy:0.5, stop:0 #3DFFDC, stop:0.6 #00D4AA, stop:1 #00B894);
                border-color: #00D4AA;
            }
            #sellRadio::indicator:checked { 
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
                    fx:0.5, fy:0.5, stop:0 #FF6B9D, stop:0.6 #FF4757, stop:1 #EE5A6F);
                border-color: #FF4757;
            }

            QCheckBox { 
                color: #D1D5DB; 
                spacing: 10px; 
                font-weight: 600;
                font-size: 13px;
            }
            QCheckBox::indicator {
                width: 20px; 
                height: 20px; 
                border-radius: 5px;
                background-color: #1F2937; 
                border: 2px solid #374151;
            }
            QCheckBox::indicator:checked { 
                background-color: #8B5CF6;
                border-color: #8B5CF6;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iMTIiIHZpZXdCb3g9IjAgMCAxMiAxMiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNMTAgM0w0LjUgOC41TDIgNiIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48L3N2Zz4=);
            }

            QDoubleSpinBox {
                background-color: #1F2937; 
                border: 2px solid #374151;
                color: #F9FAFB; 
                font-size: 15px; 
                font-weight: 600;
                padding: 8px 8px 8px 8px; 
                border-radius: 8px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 20px;
                border: none;
                background-color: transparent;
            }
            QDoubleSpinBox:focus { 
                border: 2px solid #6366F1;
                background-color: #222938;
            }
            QDoubleSpinBox:hover {
                border: 2px solid #4B5563;
            }

            #totalValueLabel {
                color: #FFFFFF;
                font-size: 18px;
                font-weight: 500;
                letter-spacing: -0.3px;
                padding: 8px 16px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(99, 102, 241, 0.08), 
                    stop:0.5 rgba(139, 92, 246, 0.08), 
                    stop:1 rgba(168, 85, 247, 0.08));
                border: 1px solid rgba(139, 92, 246, 0.2);
                border-radius: 10px;
            }

            QPushButton {
                font-weight: 700; 
                border-radius: 8px; 
                padding: 14px 24px; 
                font-size: 14px;
                letter-spacing: 0.3px;
            }

            #secondaryButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #374151, stop:1 #2D3748);
                color: #E5E7EB; 
                border: 1px solid #4B5563;
            }
            #secondaryButton:hover { 
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4B5563, stop:1 #374151);
                border: 1px solid #6B7280;
            }

            #primaryButton { 
                border: none;
                font-weight: 700;
            }
            #primaryButton[transaction_type="BUY"] { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #10B981, stop:1 #059669);
                color: #FFFFFF;
            }
            #primaryButton[transaction_type="BUY"]:hover { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #34D399, stop:1 #10B981);
            }
            #primaryButton[transaction_type="SELL"] { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #EF4444, stop:1 #DC2626);
                color: #FFFFFF;
            }
            #primaryButton[transaction_type="SELL"]:hover { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #F87171, stop:1 #EF4444);
            }

            .badge {
                padding: 6px 12px;
                border-radius: 12px;
                font-weight: 700;
                font-size: 12px;
                letter-spacing: 0.5px;
            }

            .badge.strike {
                background: rgba(107, 114, 128, 0.15);
                color: #9CA3AF;
                border: 1px solid rgba(107, 114, 128, 0.25);
            }

            .badge.ltp {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(16, 185, 129, 0.2), 
                    stop:1 rgba(5, 150, 105, 0.2));
                color: #34D399;
                border: 1px solid rgba(16, 185, 129, 0.4);
            }

            #refreshButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #374151, stop:1 #2D3748);
                color: #E5E7EB; 
                border: 1px solid #4B5563;
            }
            #refreshButton:hover { 
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #6366F1, stop:1 #4F46E5);
                color: #FFFFFF;
                border: 1px solid #6366F1;
            }
            #refreshButton:pressed {
                background: #4338CA;
            }

            #confirmButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #8B5CF6, stop:1 #7C3AED);
                color: #FFFFFF;
                font-weight: 700;
                border: 1px solid rgba(139, 92, 246, 0.3);
            }
            #confirmButton:hover { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #A78BFA, stop:1 #8B5CF6);
            }
            #confirmButton:pressed {
                background: #6D28D9;
            }
        """)
    def _update_summary(self):
        qty = self.lots_spinbox.value() * self.contract.lot_size
        price = self.price_spinbox.value()

        if self.buy_radio.isChecked():
            total_value = int(qty * price)
            formatted_value = locale.format_string("%d", total_value, grouping=True)
            self.total_value_label.setText(
                f"Required Premium: ₹ {formatted_value}"
            )
        else:
            self.total_value_label.setText(
                """
                <div style="text-align:center;">
                    <div style="font-size:16px; font-weight:600;">
                        SPAN + EXPOSURE MARGIN
                    </div>
                    <div style="font-size:12px; color:#8A9BA8;">
                        Margin reduces when BUY legs are added
                    </div>
                </div>
                """
            )

    def update_contract_data(self, new_contract: Contract):
        self.contract = new_contract

        # Determine ATM status
        is_atm = (
                hasattr(self.contract, "atm_strike")
                and self.contract.strike == self.contract.atm_strike
        )
        atm_class = " atm" if is_atm else ""

        # Determine LTP direction
        if self._last_ltp is None:
            ltp_class = "ltp"
        elif self.contract.ltp > self._last_ltp:
            ltp_class = "ltp up"
        elif self.contract.ltp < self._last_ltp:
            ltp_class = "ltp down"
        else:
            ltp_class = "ltp"

        # Update label text (DO NOT recreate QLabel)
        self.info_label.setText(
            f"""
            <span class="badge strike{atm_class}">
                STRIKE ₹{int(self.contract.strike):,}
            </span>
            &nbsp;&nbsp;
            <span class="badge {ltp_class}">
                LTP ₹{self.contract.ltp:.2f}
            </span>
            """
        )

        # Update internal state
        self._last_ltp = self.contract.ltp

        # Update price input intelligently
        self.price_spinbox.setValue(
            calculate_smart_limit_price(self.contract)
        )

        self._update_summary()

        logger.info(
            f"Quick Order Dialog refreshed for {self.contract.tradingsymbol}"
        )

    def populate_from_order(self, data):
        is_position = isinstance(data, Position)

        if is_position:
            # It's a Position object from an open position
            position = data
            if position.quantity > 0:
                self.buy_radio.setChecked(True)
            else:
                self.sell_radio.setChecked(True)

            lot_size = self.contract.lot_size if self.contract and self.contract.lot_size > 0 else 1
            lots = int(abs(position.quantity) / lot_size)
            self.lots_spinbox.setValue(lots)

            self.price_spinbox.setValue(position.average_price)

            # Disable editing of transaction type and quantity when modifying
            self.buy_radio.setEnabled(False)
            self.sell_radio.setEnabled(False)
            self.lots_spinbox.setEnabled(False)
            self.price_spinbox.setEnabled(False)  # Also disable price, as we are only modifying SL/TP

            # Populate SL
            if position.stop_loss_price is not None:
                self.sl_checkbox.setChecked(True)
                sl_amount = abs(position.average_price - position.stop_loss_price) * abs(position.quantity)
                self.sl_spinbox.setValue(sl_amount)

            # Populate TP
            if position.target_price is not None:
                self.tp_checkbox.setChecked(True)
                tp_amount = abs(position.target_price - position.average_price) * abs(position.quantity)
                self.tp_spinbox.setValue(tp_amount)

            # Populate TSL
            if position.trailing_stop_loss is not None and position.trailing_stop_loss > 0:
                self.tsl_checkbox.setChecked(True)
                self.tsl_spinbox.setValue(position.trailing_stop_loss)

            logger.info(f"Dialog populated for modifying SL/TP for order {position.order_id}")

        else:
            # It's an order_data dict from a pending order
            order_data = data
            if order_data.get('transaction_type') == 'SELL':
                self.sell_radio.setChecked(True)
            else:
                self.buy_radio.setChecked(True)

            quantity = order_data.get('quantity', 0)
            lot_size = self.contract.lot_size if self.contract and self.contract.lot_size > 0 else 1
            lots = int(quantity / lot_size)
            self.lots_spinbox.setValue(lots)

            price = order_data.get('price', self.contract.ltp)
            self.price_spinbox.setValue(price)
            logger.info(f"Dialog populated for modifying order {order_data.get('order_id')}")

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