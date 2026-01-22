# dialogs/order_confirmation_dialog.py
import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QScrollArea
)
from PySide6.QtCore import Qt, Signal
# --- Add imports for QMouseEvent and QShowEvent ---
from PySide6.QtGui import QMouseEvent, QShowEvent
import locale
locale.setlocale(locale.LC_ALL, 'en_IN')
try:
    from src.utils.data_models import OptionType
except ImportError:
    from enum import Enum


    class OptionType(Enum):
        CALL = "CE"
        PUT = "PE"

logger = logging.getLogger(__name__)


class OrderConfirmationDialog(QDialog):
    """
    A premium, compact dialog for order confirmation with price refresh capability,
    styled with a modern, professional dark theme.
    """
    refresh_requested = Signal()

    def __init__(self, parent, order_details: dict):
        super().__init__(parent)
        self.order_details = order_details
        self._drag_pos = None
        self.strikes_scroll_area = None
        self._setup_dialog()
        self._setup_ui()
        self._apply_styles()

    # --- ADD THIS METHOD ---
    def showEvent(self, event: QShowEvent):
        """Overrides the show event to center the dialog on its parent."""
        super().showEvent(event)
        if self.parent():
            # Center the dialog on the parent widget
            parent_geometry = self.parent().geometry()
            self.move(parent_geometry.center() - self.rect().center())

    def _setup_dialog(self):
        self.setWindowTitle("Confirm Order")
        self.setModal(True)
        self.setMinimumSize(380, 500)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        container = QWidget(self)
        container.setObjectName("mainContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(25, 15, 25, 20)
        container_layout.setSpacing(15)

        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout.addLayout(self._create_title_bar())
        container_layout.addSpacing(10)
        container_layout.addLayout(self._create_instrument_details())
        container_layout.addWidget(self._create_strikes_list_widget())
        container_layout.addWidget(self._create_cost_summary())
        container_layout.addStretch()
        container_layout.addLayout(self._create_action_buttons())

    def update_order_details(self, new_order_details: dict):
        self.order_details = new_order_details
        self._repopulate_strikes_list()
        self._update_cost_summary()
        logger.info("Order confirmation dialog refreshed with latest prices.")

    def _repopulate_strikes_list(self):
        """Efficiently redraws the list of strikes in the scroll area."""
        if not self.strikes_scroll_area:
            return

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(5, 5, 5, 5)
        scroll_layout.setSpacing(4)  # Tighter spacing for list items

        for strike_info in self.order_details.get("strikes", []):
            strike_widget = self._create_single_strike_widget(strike_info)
            scroll_layout.addWidget(strike_widget)

        self.strikes_scroll_area.setWidget(scroll_content)

    def _update_cost_summary(self):
        cost_value = self.cost_summary_widget.findChild(QLabel, "costValue")
        total_premium = int(self.order_details.get('total_premium_estimate', 0.0))
        formatted_value = locale.format_string("%d", total_premium, grouping=True)
        cost_value.setText(f"₹ {formatted_value}")

    def _create_strikes_list_widget(self):
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(8)

        header = QLabel(f"{len(self.order_details.get('strikes', []))} STRIKES SELECTED")
        header.setObjectName("listHeader")
        container_layout.addWidget(header)

        self.strikes_scroll_area = QScrollArea()
        self.strikes_scroll_area.setObjectName("strikeScrollArea")
        self.strikes_scroll_area.setWidgetResizable(True)
        self.strikes_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._repopulate_strikes_list()

        container_layout.addWidget(self.strikes_scroll_area)
        return container

    def _create_title_bar(self):
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.title_bar = QWidget()
        self.title_bar.setObjectName("titleBar")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Confirm Order")
        title.setObjectName("dialogTitle")

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(35, 35)
        self.close_btn.clicked.connect(self.reject)

        title_layout.addWidget(title)
        title_layout.addStretch()
        title_layout.addWidget(self.close_btn)

        # Enable mouse tracking for the title bar
        self.title_bar.mousePressEvent = self.mousePressEvent
        self.title_bar.mouseMoveEvent = self.mouseMoveEvent
        self.title_bar.mouseReleaseEvent = self.mouseReleaseEvent

        layout.addWidget(self.title_bar)
        return layout

    def _create_instrument_details(self):
        od = self.order_details
        layout = QVBoxLayout()
        layout.setSpacing(8)
        symbol_label = QLabel(f"{od.get('symbol', 'N/A')} {od.get('expiry', '')}")
        symbol_label.setObjectName("symbolLabel")
        layout.addWidget(symbol_label)

        tags_layout = QHBoxLayout()
        tags_layout.setSpacing(8)
        buy_tag = self._create_tag_label("BUY", "buyTag")
        tags_layout.addWidget(buy_tag)

        option_type_val = od.get("option_type")
        option_name = option_type_val.name if hasattr(option_type_val, 'name') else str(option_type_val)
        tag_object_name = "callTag" if "CALL" in option_name.upper() else "putTag"
        option_tag = self._create_tag_label(option_name, tag_object_name)
        tags_layout.addWidget(option_tag)

        # This logic appears to be missing from the original file, it has been added for completeness
        qty_per_strike = od.get('total_quantity_per_strike', od.get('lot_size', 1) * od.get('lot_quantity', 1))
        num_lots = od.get('lot_size', 1)

        qty_label = QLabel(f"QTY: {qty_per_strike} ({num_lots} LOTS)")
        qty_label.setObjectName("infoLabel")
        tags_layout.addStretch()
        tags_layout.addWidget(qty_label)
        layout.addLayout(tags_layout)
        return layout

    def _create_single_strike_widget(self, strike_info: dict) -> QWidget:
        strike_widget = QWidget()
        strike_widget.setObjectName("strikeRowWidget")
        strike_layout = QHBoxLayout(strike_widget)
        strike_layout.setContentsMargins(10, 8, 10, 8)

        strike_price = strike_info.get("strike", 0)
        option_type_val = self.order_details.get("option_type")
        option_type_char = (
            option_type_val.name[0] if hasattr(option_type_val, 'name') else str(option_type_val)[0]).upper()
        strike_label = QLabel(f"{strike_price:.0f}{option_type_char}E")
        strike_label.setObjectName("strikePriceLabel")

        ltp_label = QLabel(f"₹{strike_info.get('ltp', 0.0):.2f}")
        ltp_label.setObjectName("ltpLabel")

        strike_layout.addWidget(strike_label)
        strike_layout.addStretch()
        strike_layout.addWidget(ltp_label)
        return strike_widget

    def _create_cost_summary(self):
        od = self.order_details
        self.cost_summary_widget = QWidget()
        self.cost_summary_widget.setObjectName("summaryBox")
        layout = QVBoxLayout(self.cost_summary_widget)
        layout.setSpacing(0)
        layout.setContentsMargins(15, 12, 15, 12)

        cost_int = int(od.get('total_premium_estimate', 0.0))
        formatted_cost = locale.format_string("%d", cost_int, grouping=True)
        cost_value = QLabel(f"₹ {formatted_cost}")
        cost_value.setObjectName("costValue")
        cost_value.setAlignment(Qt.AlignCenter)

        title = QLabel("TOTAL ESTIMATED PREMIUM")
        title.setObjectName("summaryTitle")
        title.setAlignment(Qt.AlignCenter)

        layout.addWidget(cost_value)
        layout.addWidget(title)
        return self.cost_summary_widget

    @staticmethod
    def _create_tag_label(text, object_name):
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setAlignment(Qt.AlignCenter)
        return label

    def _create_action_buttons(self):
        layout = QHBoxLayout()
        layout.setSpacing(10)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)

        self.refresh_btn = QPushButton("REFRESH LTPs")
        self.refresh_btn.setObjectName("secondaryButton")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)

        self.confirm_btn = QPushButton("CONFIRM ORDER")
        self.confirm_btn.setObjectName("primaryButton")
        self.confirm_btn.clicked.connect(self.accept)

        # ✅ Enter key will trigger this
        self.confirm_btn.setDefault(True)
        self.confirm_btn.setAutoDefault(True)

        layout.addWidget(cancel_btn)
        layout.addWidget(self.refresh_btn)
        layout.addStretch()
        layout.addWidget(self.confirm_btn)
        return layout

    def _apply_styles(self):
        """Applies a premium, modern dark theme."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle { color: #E0E0E0; font-size: 16px; font-weight: 600; }
            #closeButton {
                background-color: transparent; border: none; color: #8A9BA8;
                font-size: 16px; font-weight: bold; padding: 0px;
            }
            #closeButton:hover { background-color: #3A4458; color: #f52a20; }
            #symbolLabel { color: #FFFFFF; font-size: 26px; font-weight: 300; }
            #infoLabel { color: #A9B1C3; font-size: 11px; font-weight: bold; }

            #buyTag, #callTag, #putTag {
                font-size: 10px; font-weight: bold; border-radius: 4px; padding: 4px 8px;
            }
            #buyTag { background-color: #29C7C9; color: #161A25; }
            #callTag { background-color: rgba(41, 199, 201, 0.2); color: #29C7C9; }
            #putTag { background-color: rgba(248, 81, 73, 0.2); color: #F85149; }

            #listHeader { color: #8A9BA8; font-size: 10px; font-weight: bold; text-transform: uppercase; }
            #strikeScrollArea {
                border: 1px solid #2A3140; border-radius: 6px; background-color: #212635;
            }
            #strikeRowWidget { border-bottom: 1px solid #2A3140; }
            QScrollArea > QWidget > QWidget { background-color: #212635; } /* Scroll area content widget */

            #strikePriceLabel { color: #E0E0E0; font-size: 14px; font-weight: 600; }
            #ltpLabel { color: #A9B1C3; font-size: 13px; }

            #summaryBox {
                background-color: #212635; border-radius: 8px;
            }
            #summaryTitle { font-size: 11px; color: #8A9BA8; font-weight: bold; text-transform: uppercase;}
            #costValue { font-size: 32px; font-weight: 300; color: #FFFFFF; padding-bottom: 2px; }

            QPushButton {
                font-weight: bold; border-radius: 6px; padding: 10px 16px; border: none; font-size: 12px;
            }
            #secondaryButton {
                background-color: #3A4458; color: #E0E0E0;
            }
            #secondaryButton:hover { background-color: #4A5568; }
            #primaryButton {
                background-color: #29C7C9; color: #161A25;
            }
            #primaryButton:hover { background-color: #32E0E3; }
        """)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Explicit confirmation
            self.accept()
            event.accept()
            return
        elif event.key() == Qt.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_pos:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()