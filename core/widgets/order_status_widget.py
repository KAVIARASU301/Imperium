import logging
import sys

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QApplication, \
    QGraphicsDropShadowEffect
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QByteArray, QTimer, QParallelAnimationGroup, \
    QPoint
from PySide6.QtGui import QColor

logger = logging.getLogger(__name__)


ENTRY_EXIT_NOTIFICATIONS = {
    "entry": {
        "pending": "Entry order sent — waiting for fill",
        "open": "Entry order is live in the market",
        "complete": "Position entered successfully",
        "rejected": "Entry rejected — review quantity/price",
        "cancelled": "Entry cancelled",
    },
    "exit": {
        "pending": "Exit order sent — reducing exposure",
        "open": "Exit order is live in the market",
        "complete": "Position exited successfully",
        "rejected": "Exit rejected — manage risk manually",
        "cancelled": "Exit cancelled — position still open",
    },
}


class OrderStatusWidget(QWidget):
    """
    Premium toast notification for order status with glassmorphism design,
    smooth animations, and modern interactions.
    """
    cancel_requested = Signal(str)
    modify_requested = Signal(dict)

    def __init__(self, order_data: dict, parent=None):
        super().__init__(parent)
        self.order_data = order_data
        self.order_id = order_data.get("order_id")
        self._position_anim_group = None
        self._stack_anim = None

        self._setup_ui()
        self._apply_styles()
        self._add_shadow()
        self.show()

    @staticmethod
    def _normalize_status(raw_status: str) -> str:
        status = (raw_status or "").lower()
        if status in {"cancelled", "canceled"}:
            return "cancelled"
        return status

    def _resolve_notification_message(self) -> str:
        transaction_type = (self.order_data.get("transaction_type") or "").upper()
        status = self._normalize_status(self.order_data.get("status", ""))

        flow = "exit" if transaction_type == "SELL" else "entry"
        return ENTRY_EXIT_NOTIFICATIONS.get(flow, {}).get(
            status,
            "Order update received",
        )

    def _setup_ui(self):
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(400, 182)

        # Main container with glassmorphism
        container = QFrame(self)
        container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(10)

        # Status indicator line
        status_line = QFrame()
        status_line.setObjectName("statusLine")
        status_line.setFixedHeight(4)
        layout.addWidget(status_line)

        status = self.order_data.get("status", "").lower()
        txn = self.order_data.get("transaction_type", "").upper()

        # Default (BUY / pending / open)
        line_color = "#00C4C6"  # teal

        if status in ("rejected", "cancelled", "canceled"):
            line_color = "#E0424A"  # red
        elif status in ("complete", "filled"):
            line_color = "#1DB87E"  # success green
        elif txn == "SELL":
            line_color = "#E0424A"  # sell = danger

        status_line.setStyleSheet(f"background-color: {line_color};")

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        indicator_dot = QLabel("●")
        indicator_dot.setObjectName("toneDot")
        indicator_dot.setStyleSheet(f"color: {line_color};")

        headline_label = QLabel(self._resolve_notification_message())
        headline_label.setObjectName("headlineLabel")
        headline_label.setWordWrap(True)

        title_row.addWidget(indicator_dot)
        title_row.addWidget(headline_label, 1)
        layout.addLayout(title_row)

        # Header with symbol and status badge
        header = QHBoxLayout()
        header.setSpacing(10)

        symbol = self.order_data.get('tradingsymbol', 'N/A')
        symbol_label = QLabel(symbol)
        symbol_label.setObjectName("symbolLabel")

        status = self.order_data.get('status', 'N/A').replace("_", " ").title()
        status_badge = QLabel(status)
        status_badge.setObjectName("statusBadge")

        header.addWidget(symbol_label)
        header.addStretch()
        header.addWidget(status_badge)
        layout.addLayout(header)

        # Order details
        transaction_type = self.order_data.get('transaction_type', '')
        qty = self.order_data.get('quantity', 0)
        price = self.order_data.get('price', 0.0)

        details = QHBoxLayout()
        details.setSpacing(15)

        # Transaction type indicator
        type_label = QLabel(f"● {transaction_type}")
        type_label.setObjectName("buyLabel" if transaction_type == "BUY" else "sellLabel")

        # Quantity and price - handle orders without price (SL-M, etc)
        if price is not None:
            info_label = QLabel(f"{qty} qty @ ₹{price:.2f}")
        else:
            # For SL-M orders, show trigger price if available
            trigger = self.order_data.get('trigger_price')
            if trigger:
                info_label = QLabel(f"{qty} qty @ trigger ₹{trigger:.2f}")
            else:
                info_label = QLabel(f"{qty} qty @ market")
        info_label.setObjectName("infoLabel")

        details.addWidget(type_label)
        details.addWidget(info_label)
        details.addStretch()
        layout.addLayout(details)

        layout.addStretch()

        # Action buttons
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.setContentsMargins(0, 6, 0, 0)

        self.modify_btn = QPushButton("Modify")
        self.modify_btn.setObjectName("modifyButton")
        self.modify_btn.setCursor(Qt.PointingHandCursor)
        self.modify_btn.clicked.connect(lambda: self.modify_requested.emit(self.order_data))

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancelButton")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(lambda: self.cancel_requested.emit(self.order_id))

        buttons.addWidget(self.modify_btn)
        buttons.addWidget(self.cancel_btn)
        layout.addLayout(buttons)

    def _add_shadow(self):
        """Add premium drop shadow effect"""
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 100))
        shadow.setOffset(0, 8)
        self.setGraphicsEffect(shadow)

    def animate_in(self, final_pos: QPoint, delay_ms: int = 0):
        """Slide in from bottom with fade."""
        self.setWindowOpacity(0.0)
        self.move(final_pos.x(), final_pos.y() + 36)

        # Opacity animation
        opacity_anim = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        opacity_anim.setDuration(420)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutQuart)

        # Vertical slide animation
        slide_anim = QPropertyAnimation(self, b"pos")
        slide_anim.setDuration(420)
        slide_anim.setStartValue(QPoint(final_pos.x(), final_pos.y() + 36))
        slide_anim.setEndValue(final_pos)
        slide_anim.setEasingCurve(QEasingCurve.Type.OutQuart)

        # Run both animations together
        self._position_anim_group = QParallelAnimationGroup(self)
        self._position_anim_group.addAnimation(opacity_anim)
        self._position_anim_group.addAnimation(slide_anim)

        if delay_ms > 0:
            QTimer.singleShot(delay_ms, self._position_anim_group.start)
        else:
            self._position_anim_group.start()

    def animate_stack_to(self, final_pos: QPoint):
        """Animate widget to its next stacked position."""
        self._stack_anim = QPropertyAnimation(self, b"pos")
        self._stack_anim.setDuration(320)
        self._stack_anim.setStartValue(self.pos())
        self._stack_anim.setEndValue(final_pos)
        self._stack_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._stack_anim.start()

    def close_widget(self):
        """Slide out with fade"""
        opacity_anim = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        opacity_anim.setDuration(300)
        opacity_anim.setStartValue(1.0)
        opacity_anim.setEndValue(0.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        slide_anim = QPropertyAnimation(self, b"pos")
        slide_anim.setDuration(300)
        start_pos = self.pos()
        slide_anim.setStartValue(start_pos)
        slide_anim.setEndValue(QPoint(start_pos.x(), start_pos.y() + 30))
        slide_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        self._position_anim_group = QParallelAnimationGroup(self)
        self._position_anim_group.addAnimation(opacity_anim)
        self._position_anim_group.addAnimation(slide_anim)
        self._position_anim_group.finished.connect(self.close)
        self._position_anim_group.start()

    def _apply_styles(self):
        self.setStyleSheet("""
        #mainContainer {
            background-color: #0C0F17;
            border: 1px solid #1C2333;
            border-radius: 2px;
        }
        #statusLine {
            background-color: #00C4C6;
            border-radius: 0px;
        }
        #symbolLabel {
            color: #C8D0DC;
            font-size: 14px;
            font-weight: 600;
            letter-spacing: 0.2px;
        }
        #statusBadge {
            background-color: #111520;
            color: #7A8799;
            border: 1px solid #1C2333;
            font-size: 10px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 0px;
            letter-spacing: 0.4px;
        }
        #buyLabel {
            color: #00C4C6;
            font-size: 12px;
            font-weight: 600;
        }
        #sellLabel {
            color: #E0424A;
            font-size: 12px;
            font-weight: 600;
        }
        #infoLabel { color: #7A8799; font-size: 12px; font-weight: 500; }
        #toneDot { font-size: 13px; font-weight: 700; }
        #headlineLabel { color: #C8D0DC; font-size: 13px; font-weight: 700; letter-spacing: 0.1px; }
        QPushButton {
            font-family: "Segoe UI", system-ui;
            font-weight: 600;
            border-radius: 2px;
            padding: 6px 12px;
            font-size: 10px;
            border: 1px solid transparent;
            letter-spacing: 0.3px;
            min-height: 24px;
        }
        #modifyButton {
            background-color: #111520;
            color: #C8D0DC;
            border-color: #1C2333;
        }
        #modifyButton:hover { background-color: #161C28; color: #C8D0DC; border-color: #253047; }
        #modifyButton:pressed { background-color: #0A0D14; }
        #cancelButton {
            background-color: #1A0709;
            color: #E0424A;
            font-weight: 700;
            padding: 6px 14px;
            border: 1px solid #E0424A;
        }
        #cancelButton:hover { background-color: #2A1215; }
        #cancelButton:pressed { background-color: #0F0305; }
        """)


def usage():
    """Demo with multiple order types"""
    app = QApplication(sys.argv)

    orders = [
        {
            "order_id": "ORD12345",
            "tradingsymbol": "RELIANCE",
            "status": "pending",
            "transaction_type": "BUY",
            "quantity": 50,
            "price": 2456.75,
        },
        {
            "order_id": "ORD12346",
            "tradingsymbol": "TCS",
            "status": "open",
            "transaction_type": "SELL",
            "quantity": 25,
            "price": 3890.50,
        }
    ]

    widgets = []
    screen_geometry = app.primaryScreen().availableGeometry()

    for i, order in enumerate(orders):
        widget = OrderStatusWidget(order)
        widget.cancel_requested.connect(lambda oid: print(f"Cancel: {oid}"))
        widget.modify_requested.connect(lambda od: print(f"Modify: {od}"))

        # Stack widgets vertically
        x = screen_geometry.width() - widget.width() - 20
        y = screen_geometry.height() - (widget.height() + 15) * (i + 1) - 20
        widget.move(x, y)
        widgets.append(widget)

        # Stagger animations
        QTimer.singleShot(i * 150, lambda w=widget, px=x, py=y: w.animate_in(QPoint(px, py)))

    sys.exit(app.exec())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    usage()
