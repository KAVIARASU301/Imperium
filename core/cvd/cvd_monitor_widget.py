# core/cvd/cvd_monitor_widget.py

import logging
from typing import Dict

from PySide6.QtWidgets import QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor

from core.cvd.cvd_engine import CVDEngine

logger = logging.getLogger(__name__)


class CVDMonitorWidget(QWidget):
    """
    Lightweight CVD monitor similar to Market Monitor.
    """

    def __init__(self, cvd_engine: CVDEngine, parent=None):
        super().__init__(parent)
        self.cvd_engine = cvd_engine

        self._setup_ui()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(500)  # UI throttle

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Symbol", "CVD"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)

        self.table.horizontalHeader().setStretchLastSection(True)

        layout.addWidget(self.table)

        self.setStyleSheet("""
            QTableWidget {
                background-color: #161A25;
                color: #E0E0E0;
                gridline-color: #2F3447;
                font-size: 13px;
            }
            QHeaderView::section {
                background-color: #1E2333;
                color: #A9B1C3;
                padding: 6px;
                border: none;
            }
        """)

    def refresh(self):
        snapshot = self.cvd_engine.snapshot()

        self.table.setRowCount(len(snapshot))

        for row, (symbol, cvd) in enumerate(snapshot.items()):
            symbol_item = QTableWidgetItem(symbol)
            cvd_item = QTableWidgetItem(f"{cvd:,.0f}")

            cvd_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            # Color logic
            if cvd > 0:
                cvd_item.setForeground(QColor("#26A69A"))  # teal
            elif cvd < 0:
                cvd_item.setForeground(QColor("#EF5350"))  # red
            else:
                cvd_item.setForeground(QColor("#A9B1C3"))

            self.table.setItem(row, 0, symbol_item)
            self.table.setItem(row, 1, cvd_item)
