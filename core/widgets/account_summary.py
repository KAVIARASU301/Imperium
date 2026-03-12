import logging
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QGridLayout, QSizePolicy, QToolTip
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QCursor

logger = logging.getLogger(__name__)


def format_indian_currency(amount: float) -> str:
    """Format number in full Indian comma style as a whole number."""
    if amount == 0:
        return "0"

    rounded_amount = int(round(amount))
    sign = "-" if rounded_amount < 0 else ""
    digits = str(abs(rounded_amount))

    if len(digits) > 3:
        prefix = digits[:-3]
        suffix = digits[-3:]
        chunks = []
        while len(prefix) > 2:
            chunks.insert(0, prefix[-2:])
            prefix = prefix[:-2]
        if prefix:
            chunks.insert(0, prefix)
        formatted_integer = f"{','.join(chunks)},{suffix}"
    else:
        formatted_integer = digits

    return f"{sign}{formatted_integer}"


class AccountSummaryWidget(QWidget):
    """A polished account summary card rendered in a compact table layout."""
    pnl_history_requested = Signal()

    def __init__(self):
        super().__init__()
        self.labels = {}
        self._setup_ui()
        self._apply_styles()

        # Timer for custom tooltip delay
        self.tooltip_timer = QTimer(self)
        self.tooltip_timer.setSingleShot(True)
        self.tooltip_timer.setInterval(10000)  # 10 seconds
        self.tooltip_timer.timeout.connect(self._show_custom_tooltip)

        self.update_summary()  # Initialize with default zero values

    def _setup_ui(self):
        """Initializes the UI components in a clean, table-style layout."""
        self.setObjectName("accountSummary")
        self.setMinimumWidth(280)
        self.setCursor(QCursor(Qt.PointingHandCursor))

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        title_label = QLabel("ACCOUNT SUMMARY")
        title_label.setObjectName("tableTitle")
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        content_frame = QFrame()
        content_frame.setObjectName("tableFrame")
        content_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout.addWidget(content_frame, 1)

        grid_layout = QGridLayout(content_frame)
        grid_layout.setContentsMargins(1, 1, 1, 1)
        grid_layout.setHorizontalSpacing(0)
        grid_layout.setVerticalSpacing(0)

        metrics = [
            ("unrealized_pnl", "Unrealized P&L"),
            ("realized_pnl", "Realized P&L"),
            ("used_margin", "Used Margin"),
            ("available_margin", "Available Margin"),
            ("win_rate", "Win Rate"),
            ("trade_count", "Total Trades"),
        ]

        for row, (key, title) in enumerate(metrics):
            self.labels[key] = self._create_metric_row(grid_layout, row, title)

        grid_layout.setColumnStretch(0, 1)
        grid_layout.setColumnStretch(1, 1)
        for row in range(len(metrics)):
            grid_layout.setRowStretch(row, 1)

    def _create_metric_row(self, layout, row, title_text):
        """Build one table row consisting of a metric label and value."""
        name_label = QLabel(title_text)
        name_label.setObjectName("metricTitleLabel")
        name_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        value_label = QLabel("0")
        value_label.setObjectName("metricValueLabel")
        value_label.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        value_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        row_style = "metricRow" if row == 0 else "metricRow metricRowBorder"
        name_label.setProperty("class", row_style)
        value_label.setProperty("class", row_style)

        layout.addWidget(name_label, row, 0)
        layout.addWidget(value_label, row, 1)
        return value_label

    def update_summary(self, unrealized_pnl=0.0, realized_pnl=0.0,
                       used_margin=0.0, available_margin=0.0,
                       win_rate=0.0, trade_count=0):
        """Public method to update all widget labels with new data."""
        MONO = "font-family:'Cascadia Code','Consolas',monospace;"
        profit_color  = "#1DB87E"
        loss_color    = "#E0424A"
        neutral_color = "#7A8799"
        hi_color      = "#C8D0DC"

        self.labels['unrealized_pnl'].setText(format_indian_currency(unrealized_pnl))
        self.labels['unrealized_pnl'].setStyleSheet(
            f"color:{profit_color if unrealized_pnl >= 0 else loss_color};{MONO}")
        self.labels['realized_pnl'].setText(format_indian_currency(realized_pnl))
        self.labels['realized_pnl'].setStyleSheet(
            f"color:{profit_color if realized_pnl >= 0 else loss_color};{MONO}")
        self.labels['used_margin'].setText(format_indian_currency(used_margin))
        self.labels['used_margin'].setStyleSheet(f"color:{hi_color};{MONO}")
        self.labels['available_margin'].setText(format_indian_currency(available_margin))
        self.labels['available_margin'].setStyleSheet(f"color:{hi_color};{MONO}")
        win_rate_color = (
            profit_color if win_rate >= 60
            else "#C89B3C" if win_rate >= 40
            else loss_color if trade_count > 0
            else neutral_color
        )
        self.labels['win_rate'].setText(f"{win_rate:.0f}%")
        self.labels['win_rate'].setStyleSheet(f"color:{win_rate_color};{MONO}")
        self.labels['trade_count'].setText(str(trade_count))
        self.labels['trade_count'].setStyleSheet(f"color:{neutral_color};{MONO}")

    def _apply_styles(self):
        self.setStyleSheet("""
            #accountSummary {
                background-color: #0C0F17;
                border: 1px solid #1C2333;
                border-radius: 2px;
            }
            #tableTitle {
                color: #7A8799;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.09em;
                padding: 2px 0 3px 0;
                background: transparent;
            }
            #tableFrame {
                background-color: #0C0F17;
                border: none;
                border-radius: 0px;
            }
            QLabel[class~="metricRow"] {
                padding: 5px 8px;
                background: transparent;
            }
            QLabel[class~="metricRowBorder"] {
                border-top: 1px solid #1C2333;
            }
            #metricTitleLabel {
                color: #7A8799;
                font-size: 11px;
                font-weight: 500;
            }
            #metricValueLabel {
                color: #C8D0DC;
                font-size: 13px;
                font-weight: 600;
                font-family: "Cascadia Code", "Consolas", monospace;
            }
        """)

    def enterEvent(self, event):
        """Start the timer when the mouse enters."""
        self.tooltip_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Stop the timer and hide the tooltip when the mouse leaves."""
        self.tooltip_timer.stop()
        QToolTip.hideText()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Emits a signal when the widget is double-clicked."""
        self.pnl_history_requested.emit()
        super().mouseDoubleClickEvent(event)

    def _show_custom_tooltip(self):
        """Displays the tooltip at the current cursor position."""
        tooltip_text = "Double-click to view P&L History"
        QToolTip.showText(QCursor.pos(), tooltip_text, self)