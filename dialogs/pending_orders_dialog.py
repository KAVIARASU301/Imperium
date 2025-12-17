import logging
from typing import List, Dict

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

logger = logging.getLogger(__name__)


class PendingOrdersDialog(QDialog):
    """
    Polished Pending Orders dialog
    (visual style aligned with the main application)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None

        self._setup_window()
        self._setup_ui()
        self._apply_styles()

    # ------------------------------------------------------------------
    def _setup_window(self):
        self.setWindowTitle("Pending Orders")
        self.setMinimumSize(820, 420)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    # ------------------------------------------------------------------
    def _setup_ui(self):
        self.container = QWidget(self)
        self.container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(22, 14, 22, 18)
        layout.setSpacing(14)

        layout.addLayout(self._create_header())
        layout.addWidget(self._create_divider())

        # ---- Table frame (visual containment) ----
        table_frame = QFrame()
        table_frame.setObjectName("tableFrame")
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(10, 10, 10, 10)

        self.orders_table = self._create_table()
        table_layout.addWidget(self.orders_table)

        # ---- Empty state ----
        self.no_orders_label = QLabel("No pending orders")
        self.no_orders_label.setObjectName("noOrdersLabel")
        self.no_orders_label.setAlignment(Qt.AlignCenter)
        table_layout.addWidget(self.no_orders_label)

        self.no_orders_label.hide()

        layout.addWidget(table_frame, 1)

        # Allow dragging from background
        self.container.mousePressEvent = self.mousePressEvent
        self.container.mouseMoveEvent = self.mouseMoveEvent
        self.container.mouseReleaseEvent = self.mouseReleaseEvent

    # ------------------------------------------------------------------
    def _create_header(self):
        header = QHBoxLayout()
        header.setSpacing(10)

        title = QLabel("PENDING ORDERS")
        title.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.close)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)

        return header

    # ------------------------------------------------------------------
    def _create_divider(self):
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        return divider

    # ------------------------------------------------------------------
    @staticmethod
    def _create_table():
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels([
            "Symbol", "Side", "Qty", "Price", "Status"
        ])

        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.setAlternatingRowColors(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Fixed)
        table.setColumnWidth(0, 240)
        table.setColumnWidth(1, 80)
        table.setColumnWidth(2, 80)
        table.setColumnWidth(3, 120)
        table.setColumnWidth(4, 120)

        return table

    # ------------------------------------------------------------------
    def update_orders(self, orders: List[Dict]):
        if orders:
            self.orders_table.show()
            self.no_orders_label.hide()

            self.orders_table.setRowCount(len(orders))
            for row, order in enumerate(orders):
                self._populate_row(row, order)
        else:
            self.orders_table.hide()
            self.no_orders_label.show()
            self.orders_table.setRowCount(0)

    # ------------------------------------------------------------------
    def _populate_row(self, row: int, order: Dict):
        self.orders_table.setItem(
            row, 0, QTableWidgetItem(order.get("tradingsymbol", ""))
        )

        side = order.get("transaction_type", "").upper()
        side_item = QTableWidgetItem(side)
        side_item.setForeground(
            QColor("#29C7C9") if side == "BUY" else QColor("#F85149")
        )
        self.orders_table.setItem(row, 1, side_item)

        self.orders_table.setItem(
            row, 2, QTableWidgetItem(str(order.get("quantity", 0)))
        )

        self.orders_table.setItem(
            row, 3, QTableWidgetItem(f"₹{order.get('price', 0.0):.2f}")
        )

        status_item = QTableWidgetItem(order.get("status", "").upper())
        status_item.setForeground(QColor("#F39C12"))
        self.orders_table.setItem(row, 4, status_item)

        for col in range(5):
            item = self.orders_table.item(row, col)
            if item:
                if col == 0:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                else:
                    item.setTextAlignment(Qt.AlignCenter)

    # ------------------------------------------------------------------
    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 14px;
                font-family: "Segoe UI", sans-serif;
            }

            #dialogTitle {
                color: #E6EAF2;
                font-size: 15px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }

            #divider {
                background-color: #2A3140;
            }

            #tableFrame {
                background-color: #121622;
                border: 1px solid #2A3140;
                border-radius: 10px;
            }

            #noOrdersLabel {
                color: #8A9BA8;
                font-size: 14px;
                font-weight: 500;
            }

            #closeButton {
                background-color: transparent;
                border: none;
                color: #8A9BA8;
                font-size: 16px;
                font-weight: bold;
            }

            #closeButton:hover {
                color: #FFFFFF;
            }

            QTableWidget {
                background-color: transparent;
                color: #E0E0E0;
                border: none;
                gridline-color: #2A3140;
                font-size: 13px;
            }

            QTableWidget::item {
                padding: 10px 8px;
                border-bottom: 1px solid #2A3140;
            }

            QTableWidget::item:hover {
                background-color: #212635;
            }

            QHeaderView::section {
                background-color: #212635;
                color: #A9B1C3;
                padding: 10px 8px;
                border: none;
                border-bottom: 2px solid #3A4458;
                font-weight: 600;
                font-size: 11px;
                text-transform: uppercase;
            }

            QScrollBar:vertical {
                background: #161A25;
                width: 10px;
            }

            QScrollBar::handle:vertical {
                background: #3A4458;
                border-radius: 5px;
                min-height: 30px;
            }

            QScrollBar::handle:vertical:hover {
                background: #4A5568;
            }
        """)

    # ------------------------------------------------------------------
    # Drag support
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()
