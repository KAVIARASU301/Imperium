from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


# ---------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------

WEEKDAY_LABELS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


# ---------------------------------------------------------
# DATA MODELS
# ---------------------------------------------------------

@dataclass(frozen=True)
class ExpiryRule:
    name: str
    symbol: str
    weekly_weekday: int | None
    monthly_weekday: int


EXPIRY_RULES = (
    ExpiryRule("Nifty 50", "NIFTY", 1, 1),
    ExpiryRule("Sensex", "SENSEX", 3, 3),
    ExpiryRule("Nifty Bank", "BANKNIFTY", None, 1),
    ExpiryRule("BSE Bankex", "BANKEX", None, 3),
    ExpiryRule("Nifty Midcap Select", "MIDCPNIFTY", None, 1),
    ExpiryRule("Nifty Financial Services", "FINNIFTY", None, 1),
    ExpiryRule("BSE Sensex 50", "SENSEX50", None, 3),
    ExpiryRule("Nifty Next 50", "NIFTYNXT50", None, 1),
)


# ---------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------

def _next_weekday(today: date, weekday: int) -> date:
    days_ahead = (weekday - today.weekday()) % 7
    return today + timedelta(days=days_ahead)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _next_monthly_expiry(today: date, weekday: int) -> date:
    expiry = _last_weekday_of_month(today.year, today.month, weekday)
    if expiry < today:
        month = today.month + 1
        year = today.year
        if month > 12:
            month = 1
            year += 1
        expiry = _last_weekday_of_month(year, month, weekday)
    return expiry


# ---------------------------------------------------------
# UI COMPONENTS
# ---------------------------------------------------------

class NoSelectLabel(QLabel):
    """Label with hard-disabled text selection."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setCursor(Qt.ArrowCursor)


class ExpiryDaysDialog(QDialog):
    """
    Institutional-style expiry calendar dialog.
    Zero text selection, read-only, fast visual scan.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Expiry Days")
        self.setModal(True)
        self.resize(840, 440)

        self._setup_ui()
        self._populate_table()

    # -----------------------------------------------------
    # UI SETUP
    # -----------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 12)
        root.setSpacing(12)

        title = NoSelectLabel("Index Expiry Days")
        title.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        title.setStyleSheet("color: #E6EAF2;")

        subtitle = NoSelectLabel(
            "Weekly and monthly expiry schedule with remaining days"
        )
        subtitle.setFont(QFont("Segoe UI", 10))
        subtitle.setStyleSheet("color: #9AA4B2;")

        self.table = QTableWidget(len(EXPIRY_RULES), 6)
        self.table.setHorizontalHeaderLabels(
            [
                "Index",
                "Symbol",
                "Weekly Expiry",
                "DTE",
                "Monthly Expiry",
                "DTE",
            ]
        )

        # 🔒 Institutional hardening
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #161B26;
                color: #E6EAF2;
                gridline-color: #2A3140;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #202637;
                color: #E6EAF2;
                padding: 6px;
                border: 1px solid #2A3140;
                font-weight: 600;
            }
            QTableWidget::item {
                padding: 6px;
            }
            QTableWidget::item:alternate {
                background-color: #1C2230;
            }
        """)

        footer = QHBoxLayout()
        footer.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.accept)

        footer.addWidget(close_btn)

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(self.table, 1)
        root.addLayout(footer)

    # -----------------------------------------------------
    # DATA POPULATION
    # -----------------------------------------------------

    def _populate_table(self):
        today = date.today()

        for row, rule in enumerate(EXPIRY_RULES):
            weekly_date = (
                _next_weekday(today, rule.weekly_weekday)
                if rule.weekly_weekday is not None
                else None
            )
            monthly_date = _next_monthly_expiry(today, rule.monthly_weekday)

            weekly_label = (
                WEEKDAY_LABELS[rule.weekly_weekday]
                if rule.weekly_weekday is not None
                else "—"
            )

            weekly_dte = (
                (weekly_date - today).days
                if weekly_date
                else None
            )
            monthly_dte = (monthly_date - today).days

            values = (
                rule.name,
                rule.symbol,
                weekly_label,
                "—" if weekly_dte is None else str(weekly_dte),
                f"Last {WEEKDAY_LABELS[rule.monthly_weekday]}",
                str(monthly_dte),
            )

            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter if col in {3, 5} else Qt.AlignLeft)
                item.setFlags(Qt.ItemIsEnabled)

                # 🎯 Color-code DTE
                if col in {3, 5} and value != "—":
                    dte = int(value)
                    if dte <= 1:
                        item.setForeground(Qt.red)
                    elif dte <= 3:
                        item.setForeground(Qt.yellow)
                    else:
                        item.setForeground(Qt.green)

                self.table.setItem(row, col, item)

        self.table.resizeColumnsToContents()


# ---------------------------------------------------------
# PUBLIC ENTRY POINT
# ---------------------------------------------------------

def show_expiry_days(parent=None) -> None:
    dialog = ExpiryDaysDialog(parent)
    dialog.exec()
