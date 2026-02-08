"""
FII/DII Data Dialog - Viewer
Displays institutional trading data entered manually
"""

import logging
from typing import List, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QWidget, QComboBox
)
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCharts import (
    QChart, QChartView, QBarSet, QBarSeries,
    QBarCategoryAxis, QValueAxis
)

from utils.fii_dii_store import FIIDIIStore
from dialogs.fii_dii_data_entry_dialog import FIIDIIDataDialog

logger = logging.getLogger(__name__)


class FIIDIIDialog(QDialog):
    """Viewer dialog for FII/DII institutional data"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("FII/DII Data - Institutional Activity")
        self.setMinimumSize(1200, 700)
        self.setModal(False)

        self.store = FIIDIIStore()
        self.data: List[Dict] = []

        self._setup_ui()
        self._apply_dark_theme()
        self._load_data()

    # ------------------------------------------------------------------
    # UI SETUP
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        layout.addWidget(self._create_header())

        self.chart_view = QChartView()
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        layout.addWidget(self.chart_view, stretch=3)

        self.table = self._create_table()
        layout.addWidget(self.table, stretch=2)

    def _create_header(self) -> QWidget:
        header = QWidget()
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)

        title = QLabel("FII / DII Trading Data")
        title.setStyleSheet("font-size:16px; font-weight:bold; color:#6a9cff;")
        h.addWidget(title)

        h.addStretch()

        h.addWidget(QLabel("Participant:"))
        self.participant_combo = QComboBox()
        self.participant_combo.addItems(["Both FII & DII", "FII Only", "DII Only"])
        self.participant_combo.currentTextChanged.connect(self._update_chart)
        h.addWidget(self.participant_combo)

        add_btn = QPushButton("➕ Add / Edit Data")
        add_btn.clicked.connect(self._open_entry_dialog)
        h.addWidget(add_btn)

        reload_btn = QPushButton("⟳ Reload")
        reload_btn.clicked.connect(self._load_data)
        h.addWidget(reload_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        h.addWidget(close_btn)

        return header

    def _create_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels([
            "Date",
            "FII Buy (Cr)", "FII Sell (Cr)", "FII Net (Cr)",
            "DII Buy (Cr)", "DII Sell (Cr)", "DII Net (Cr)"
        ])

        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)

        return table

    # ------------------------------------------------------------------
    # DATA LOADING (NO SCRAPER)
    # ------------------------------------------------------------------

    def _load_data(self):
        raw = self.store.get_all()  # <- SAFE API
        self.data.clear()

        for date_str in sorted(raw.keys()):
            v = raw[date_str]
            self.data.append({
                "date": date_str,
                "fii_buy": v["fii"]["buy"],
                "fii_sell": v["fii"]["sell"],
                "fii_net": v["fii"]["net"],
                "dii_buy": v["dii"]["buy"],
                "dii_sell": v["dii"]["sell"],
                "dii_net": v["dii"]["net"],
            })

        self._populate_table()
        self._update_chart()

    # ------------------------------------------------------------------
    # TABLE
    # ------------------------------------------------------------------

    def _populate_table(self):
        self.table.setRowCount(len(self.data))

        for row, item in enumerate(self.data):
            self.table.setItem(row, 0, QTableWidgetItem(item["date"]))
            self.table.setItem(row, 1, self._num(item["fii_buy"]))
            self.table.setItem(row, 2, self._num(item["fii_sell"]))
            self.table.setItem(row, 3, self._net(item["fii_net"]))
            self.table.setItem(row, 4, self._num(item["dii_buy"]))
            self.table.setItem(row, 5, self._num(item["dii_sell"]))
            self.table.setItem(row, 6, self._net(item["dii_net"]))

    def _num(self, val: float):
        it = QTableWidgetItem(f"{val:,.2f}")
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return it

    def _net(self, val: float):
        it = self._num(val)
        if val > 0:
            it.setForeground(QColor("#4caf50"))
        elif val < 0:
            it.setForeground(QColor("#f44336"))
        return it

    # ------------------------------------------------------------------
    # CHART
    # ------------------------------------------------------------------

    def _update_chart(self):
        if not self.data:
            return

        chart = QChart()
        chart.setTitle("FII / DII Net Cash Flow (₹ Cr)")
        chart.setTheme(QChart.ChartThemeDark)

        display = self.data[-30:]
        participant = self.participant_combo.currentText()

        series = QBarSeries()

        if participant in ("Both FII & DII", "FII Only"):
            fii = QBarSet("FII")
            fii.setColor(QColor("#5a8be0"))
            for d in display:
                fii.append(d["fii_net"])
            series.append(fii)

        if participant in ("Both FII & DII", "DII Only"):
            dii = QBarSet("DII")
            dii.setColor(QColor("#ff9800"))
            for d in display:
                dii.append(d["dii_net"])
            series.append(dii)

        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append([d["date"][5:] for d in display])
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        chart.legend().setVisible(participant == "Both FII & DII")
        self.chart_view.setChart(chart)

    # ------------------------------------------------------------------
    # ENTRY DIALOG
    # ------------------------------------------------------------------

    def _open_entry_dialog(self):
        dlg = FIIDIIDataDialog(self)
        if dlg.exec():
            self._load_data()

    # ------------------------------------------------------------------

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QDialog { background:#121212; color:#e0e0e0; }
            QPushButton { background:#2a2a2a; border:1px solid #3a3a3a; padding:6px; }
            QPushButton:hover { border-color:#6a9cff; }
            QTableWidget { background:#1a1a1a; gridline-color:#2a2a2a; }
            QHeaderView::section { background:#2a2a2a; padding:6px; }
        """)
