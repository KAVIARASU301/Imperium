from datetime import datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class DateNavigator(QWidget):
    date_changed = Signal(datetime, datetime)  # current_date, previous_date

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.btn_back = QPushButton("◀")
        nav_btn_style = """
            QPushButton {
                background: #212635;
                color: #E0E0E0;
                border: 1px solid #3A4458;
                border-radius: 0px;
                padding: 0;
            }
            QPushButton:hover {
                border: 1px solid #5B9BD5;
                background: #2A3142;
            }
            QPushButton:pressed {
                background: #1B1F2B;
            }
            QPushButton:disabled {
                color: #6A7284;
                border: 1px solid #2A3142;
            }
        """
        self.btn_back.setStyleSheet(nav_btn_style)
        self.btn_back.setFixedSize(32, 24)
        self.btn_back.clicked.connect(self._go_backward)

        self.lbl_dates = QLabel()
        self.lbl_dates.setAlignment(Qt.AlignCenter)
        self.lbl_dates.setMinimumWidth(500)
        self.lbl_dates.setStyleSheet(
            """
            QLabel {
                color: #E0E0E0;
                font-size: 13px;
                font-weight: 600;
            }
        """
        )

        self.btn_forward = QPushButton("▶")
        self.btn_forward.setStyleSheet(nav_btn_style)
        self.btn_forward.setFixedSize(32, 24)
        self.btn_forward.clicked.connect(self._go_forward)

        layout.addStretch()
        layout.addWidget(self.btn_back)
        layout.addWidget(self.lbl_dates)
        layout.addWidget(self.btn_forward)
        layout.addStretch()

    def _get_previous_trading_day(self, date: datetime) -> datetime:
        prev = date - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev

    def _get_next_trading_day(self, date: datetime) -> datetime:
        nxt = date + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt

    def _update_display(self):
        prev = self._get_previous_trading_day(self._current_date)
        cur_str = self._current_date.strftime("%A, %b %d, %Y")
        prev_str = prev.strftime("%A, %b %d, %Y")

        self.lbl_dates.setText(
            f"<span style='color:#5B9BD5;'>Previous: {prev_str}</span>"
            f"  |  "
            f"<span style='color:#26A69A;'>Current: {cur_str}</span>"
        )

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.btn_forward.setEnabled(self._current_date < today)

    def _go_backward(self):
        self._current_date = self._get_previous_trading_day(self._current_date)
        self._update_display()
        self.date_changed.emit(
            self._current_date,
            self._get_previous_trading_day(self._current_date),
        )

    def _go_forward(self):
        self._current_date = self._get_next_trading_day(self._current_date)
        self._update_display()
        self.date_changed.emit(
            self._current_date,
            self._get_previous_trading_day(self._current_date),
        )

    def get_dates(self):
        return (
            self._current_date,
            self._get_previous_trading_day(self._current_date),
        )
