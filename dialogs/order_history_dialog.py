import logging
from datetime import datetime
from typing import List, Dict

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QColor, QFont, QMouseEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Order History Table
# ---------------------------------------------------------
class OrderHistoryTable(QTableWidget):
    """
    Order history table with:
    - Oldest → newest (newest at bottom)
    - Entry / Exit arrows
    - Correct BUY–SELL PnL pairing
    """

    def __init__(self):
        super().__init__()

        self.setColumnCount(10)
        self.setHorizontalHeaderLabels([
            "",                 # Arrow
            "Entry Time",
            "Exit Time",
            "Symbol",
            "Qty",
            "Entry",
            "Exit",
            "PnL",
            "Status",
            "Strategy"
        ])

        self._setup_table()

    def _setup_table(self):
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.verticalHeader().setVisible(False)
        self.setFocusPolicy(Qt.NoFocus)

        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)   # Symbol
        header.setSectionResizeMode(9, QHeaderView.Stretch)   # Strategy

        self.setColumnWidth(0, 36)
        self.setColumnWidth(6, 110)

    # -----------------------------------------------------
    def update_orders(self, orders: List[Dict]):
        """
        Zerodha / TradeLogger returns newest → oldest.
        Reverse once so newest ends at the bottom.
        """
        self.setRowCount(0)

        orders = list(reversed(orders))

        entry_stack: dict[str, list[Dict]] = {}

        for order in orders:
            row = self.rowCount()
            self.insertRow(row)
            self._populate_row(row, order, entry_stack)

        # Auto-scroll to latest
        if self.rowCount() > 0:
            self.scrollToBottom()
            self.selectRow(self.rowCount() - 1)

    # -----------------------------------------------------
    def _populate_row(
        self,
        row: int,
        order: Dict,
        entry_stack: Dict[str, list]
    ):
        symbol = order.get("tradingsymbol", "")
        txn = order.get("transaction_type", "").upper()
        qty = int(order.get("quantity", 0))
        avg_price = float(order.get("average_price", 0.0))

        # Arrow
        arrow_item = QTableWidgetItem("▲" if txn == "BUY" else "▼")
        arrow_item.setFont(QFont("Segoe UI Symbol", 14, QFont.Bold))
        arrow_item.setTextAlignment(Qt.AlignCenter)

        if txn == "BUY":
            arrow_item.setForeground(QColor("#29C7C9"))
            arrow_item.setToolTip("Entry")
            entry_stack.setdefault(symbol, []).append(order)
        else:
            arrow_item.setForeground(QColor("#F85149"))
            arrow_item.setToolTip("Exit")

        self.setItem(row, 0, arrow_item)

        self.setItem(row, 1, QTableWidgetItem(order.get("timestamp", "")))
        self.setItem(row, 2, QTableWidgetItem(symbol))
        self.setItem(row, 3, QTableWidgetItem(str(qty)))
        self.setItem(row, 4, QTableWidgetItem(f"{avg_price:.2f}"))

        # PnL
        pnl_item = QTableWidgetItem("—")
        pnl_item.setTextAlignment(Qt.AlignCenter)

        if txn == "SELL" and symbol in entry_stack and entry_stack[symbol]:
            entry = entry_stack[symbol].pop()
            entry_price = float(entry.get("average_price", 0.0))
            entry_qty = int(entry.get("quantity", qty))

            matched_qty = min(qty, entry_qty)
            pnl = (avg_price - entry_price) * matched_qty

            pnl_item.setText(f"{pnl:,.2f}")
            pnl_item.setForeground(
                QColor("#29C7C9") if pnl >= 0 else QColor("#F85149")
            )

        self.setItem(row, 5, pnl_item)

        status = order.get("status", "").upper()
        status_item = QTableWidgetItem(status)
        status_item.setForeground(
            QColor("#29C7C9") if "COMPLETE" in status else QColor("#F39C12")
        )
        self.setItem(row, 6, status_item)

        self.setItem(row, 7, QTableWidgetItem(order.get("order_id", "")))

        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                if col == 2:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                else:
                    item.setTextAlignment(Qt.AlignCenter)

    def update_trades(self, trades: list):
        self.setRowCount(0)

        trades = sorted(trades, key=lambda t: t["exit_time"])

        for trade in map(dict, trades):
            row = self.rowCount()
            self.insertRow(row)

            self.setItem(row, 0, QTableWidgetItem(""))

            def _fmt_time(raw_value: str) -> str:
                try:
                    dt = datetime.fromisoformat(raw_value)
                    return dt.strftime("%d %b %H:%M:%S")
                except Exception:
                    return raw_value or "—"

            entry_time = _fmt_time(trade.get("entry_time", ""))
            exit_time = _fmt_time(trade.get("exit_time", ""))

            self.setItem(row, 1, QTableWidgetItem(entry_time))
            self.setItem(row, 2, QTableWidgetItem(exit_time))
            self.setItem(row, 3, QTableWidgetItem(trade.get("tradingsymbol", "")))
            self.setItem(row, 4, QTableWidgetItem(str(trade.get("quantity", 0))))
            self.setItem(row, 5, QTableWidgetItem(f'{float(trade.get("entry_price", 0) or 0):.2f}'))
            self.setItem(row, 6, QTableWidgetItem(f'{float(trade.get("exit_price", 0) or 0):.2f}'))

            pnl = float(trade.get("net_pnl", 0.0) or 0.0)
            pnl_item = QTableWidgetItem(f"{pnl:,.2f}")
            pnl_item.setForeground(
                QColor("#29C7C9") if pnl >= 0 else QColor("#F85149")
            )
            pnl_item.setTextAlignment(Qt.AlignCenter)
            self.setItem(row, 7, pnl_item)

            status_text = str(trade.get("trade_status") or "MANUAL").upper()
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(QColor("#29C7C9") if status_text == "ALGO" else QColor("#F39C12"))
            self.setItem(row, 8, status_item)

            self.setItem(row, 9, QTableWidgetItem(str(trade.get("strategy_name") or "N/A")))

            for col in range(self.columnCount()):
                item = self.item(row, col)
                if item:
                    if col == 3:
                        item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                    else:
                        item.setTextAlignment(Qt.AlignCenter)

        if self.rowCount() > 0:
            self.scrollToBottom()
            self.selectRow(self.rowCount() - 1)



# ---------------------------------------------------------
# Order History Dialog (Draggable)
# ---------------------------------------------------------
class OrderHistoryDialog(QDialog):
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos: QPoint | None = None

        self._setup_window()
        self._setup_ui()
        self._apply_styles()

    def _setup_window(self):
        self.setWindowTitle("Order History")
        self.setMinimumSize(1000, 720)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        self.container = QWidget(self)
        self.container.setObjectName("mainContainer")

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(20, 10, 20, 20)
        layout.setSpacing(15)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        layout.addLayout(self._create_header())

        self.orders_table = OrderHistoryTable()
        layout.addWidget(self.orders_table, 1)

        layout.addWidget(self._create_footer())

        # Enable dragging from background too
        self.container.mousePressEvent = self.mousePressEvent
        self.container.mouseMoveEvent = self.mouseMoveEvent
        self.container.mouseReleaseEvent = self.mouseReleaseEvent

    # -----------------------------------------------------
    def _create_header(self):
        h = QHBoxLayout()

        title_box = QVBoxLayout()
        title = QLabel("Order History")
        title.setObjectName("dialogTitle")

        note = QLabel("▲ Entry   ▼ Exit   • Newest at bottom")
        note.setObjectName("noteLabel")

        title_box.addWidget(title)
        title_box.addWidget(note)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.close)

        h.addLayout(title_box)
        h.addStretch()
        h.addWidget(close_btn)
        return h

    def _create_footer(self):
        h = QHBoxLayout()

        self.trade_count_label = QLabel("0 TRADES")
        self.trade_count_label.setObjectName("footerLabel")

        refresh_btn = QPushButton("REFRESH")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setFixedHeight(28)
        refresh_btn.clicked.connect(self.refresh_requested.emit)

        # Footer container styling (scoped)
        footer_widget = QWidget()
        footer_widget.setLayout(h)
        footer_widget.setStyleSheet("""
            QLabel#footerLabel {
                color: #8F9CB2;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }

            QPushButton#secondaryButton {
                background-color: #1F2430;
                color: #9CCAF4;
                border: 1px solid #3A4458;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 600;
            }

            QPushButton#secondaryButton:hover {
                background-color: #2A3142;
                border: 1px solid #5B9BD5;
                color: #FFFFFF;
            }

            QPushButton#secondaryButton:pressed {
                background-color: #161A25;
            }
        """)

        h.addWidget(self.trade_count_label)
        h.addStretch()
        h.addWidget(refresh_btn)

        return footer_widget

    def update_trades(self, trades: list[dict]):
        self.orders_table.update_trades(trades)
        self.trade_count_label.setText(f"{len(trades)} TRADES")

    # -----------------------------------------------------
    def update_orders(self, orders: List[Dict]):
        self.orders_table.update_orders(orders)
        self.trade_count_label.setText(f"{len(orders)} TRADES")

    # -----------------------------------------------------
    # Drag logic
    # -----------------------------------------------------
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

    # -----------------------------------------------------
    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle { color: #FFFFFF; font-size: 16px; font-weight: 600; }
            #noteLabel { color: #8A9BA8; font-size: 11px; font-style: italic; }
            #footerLabel { color: #8A9BA8; font-size: 11px; font-weight: bold; }
            #closeButton {
                background: transparent; border: none; color: #8A9BA8;
                font-size: 16px; font-weight: bold;
            }
            #closeButton:hover {
                background-color: #3A4458; color: #E74C3C;
            }
            QTableWidget {
                background-color: transparent;
                border: none;
                gridline-color: #2A3140;
                color: #E0E0E0;
                font-size: 13px;
            }
            QTableWidget::item {
                padding: 10px;
                border-bottom: 1px solid #2A3140;
            }
            QTableWidget::item:selected {
                background-color: #212635;
            }
        """)
