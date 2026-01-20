import logging
import sys

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QApplication, \
    QGraphicsDropShadowEffect
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QByteArray, QTimer, QParallelAnimationGroup, \
    QPoint, Property
from PySide6.QtGui import QColor

logger = logging.getLogger(__name__)


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
        self._offset = 0

        self._setup_ui()
        self._apply_styles()
        self._add_shadow()
        self.show()
        self.animate_in()


    def _setup_ui(self):
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(320, 120)

        # Main container with glassmorphism
        container = QFrame(self)
        container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(8)

        # Status indicator line
        status_line = QFrame()
        status_line.setObjectName("statusLine")
        status_line.setFixedHeight(3)
        layout.addWidget(status_line)

        status = self.order_data.get("status", "").lower()
        txn = self.order_data.get("transaction_type", "").upper()

        # Default (BUY / pending / open)
        line_color = "#29C7C9"  # teal

        if status in ("rejected", "cancelled", "canceled"):
            line_color = "#F85149"  # red
        elif status in ("complete", "filled"):
            line_color = "#00D1B2"  # success green
        elif txn == "SELL":
            line_color = "#F85149"  # sell = red intent

        status_line.setStyleSheet(f"background-color: {line_color};")

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

        # Quantity and price
        info_label = QLabel(f"{qty} qty @ ₹{price:.2f}")
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

    def animate_in(self):
        """Slide in from right with fade"""
        # Opacity animation
        opacity_anim = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        opacity_anim.setDuration(400)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Slide animation
        slide_anim = QPropertyAnimation(self, b"offset")
        slide_anim.setDuration(400)
        slide_anim.setStartValue(50)
        slide_anim.setEndValue(0)
        slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Run both animations together
        group = QParallelAnimationGroup(self)
        group.addAnimation(opacity_anim)
        group.addAnimation(slide_anim)
        group.start()

    def close_widget(self):
        """Slide out with fade"""
        opacity_anim = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        opacity_anim.setDuration(300)
        opacity_anim.setStartValue(1.0)
        opacity_anim.setEndValue(0.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        slide_anim = QPropertyAnimation(self, b"offset")
        slide_anim.setDuration(300)
        slide_anim.setStartValue(0)
        slide_anim.setEndValue(50)
        slide_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(opacity_anim)
        group.addAnimation(slide_anim)
        group.finished.connect(self.close)
        group.start()

    @Property(int)
    def offset(self):
        return self._offset

    @offset.setter
    def offset(self, value):
        self._offset = value
        pos = self.pos()
        self.move(pos.x() + value - self._offset, pos.y())

    def _apply_styles(self):
        self.setStyleSheet("""
        /* =========================
           MAIN CONTAINER
           ========================= */

        #mainContainer {
            background-color: #161A25;
            border: 1px solid #3A4458;
            border-radius: 6px;
        }

        /* =========================
           STATUS INDICATOR LINE
           (color is overridden in code based on status)
           ========================= */

        #statusLine {
            background-color: #29C7C9;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }

        /* =========================
           HEADER
           ========================= */

        #symbolLabel {
            color: #FFFFFF;
            font-size: 15px;
            font-weight: 600;
            letter-spacing: 0.2px;
        }

        #statusBadge {
            background-color: #212635;
            color: #A9B1C3;
            border: 1px solid #3A4458;
            font-size: 10px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 6px;
            letter-spacing: 0.4px;
        }

        /* =========================
           ORDER DETAILS
           ========================= */

        #buyLabel {
            color: #29C7C9;
            font-size: 12px;
            font-weight: 600;
        }

        #sellLabel {
            color: #F85149;
            font-size: 12px;
            font-weight: 600;
        }

        #infoLabel {
            color: #A9B1C3;
            font-size: 12px;
            font-weight: 500;
        }

        /* =========================
           BUTTONS (TERMINAL STYLE)
           ========================= */

        QPushButton {
            font-family: "Segoe UI", system-ui;
            font-weight: 600;
            border-radius: 3px;
            padding: 5px 12px;
            font-size: 10px;
            border: none;
            letter-spacing: 0.3px;
            min-height: 22px;
        }


        /* Secondary action */
        #modifyButton {
            background-color: #212635;
            color: #A9B1C3;
            border: none;
        }
        
        #modifyButton:hover {
            background-color: #2A3140;
            color: #E0E0E0;
        }
        
        #modifyButton:pressed {
            background-color: #1E2433;
        }

        /* Danger action */
        #cancelButton {
            background-color: rgba(248, 81, 73, 0.9);
            color: #161A25;
            font-weight: 700;
            padding: 6px 14px;
        }
        
        #cancelButton:hover {
            background-color: #FA6B64;
        }
        
        #cancelButton:pressed {
            background-color: #E6453E;
        }

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
        QTimer.singleShot(i * 150, widget.animate_in)

    sys.exit(app.exec())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    usage()