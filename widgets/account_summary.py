import logging
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QGridLayout, QSizePolicy, QToolTip
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QCursor

logger = logging.getLogger(__name__)


def format_indian_currency(amount: float) -> str:
    """Format number in Indian currency style (lakhs, crores) without rupee symbol."""
    if amount == 0:
        return "0"

    abs_amount = abs(amount)
    sign = "-" if amount < 0 else ""

    if abs_amount >= 10000000:  # 1 crore
        crores = abs_amount / 10000000
        return f"{sign}{crores:.1f}Cr"
    elif abs_amount >= 100000:  # 1 lakh
        lakhs = abs_amount / 100000
        return f"{sign}{lakhs:.1f}L"
    elif abs_amount >= 1000:  # 1 thousand
        thousands = abs_amount / 1000
        return f"{sign}{thousands:.1f}K"
    else:
        return f"{sign}{abs_amount:,.0f}"


class AccountSummaryWidget(QWidget):
    """
    A premium account summary widget with a semi-transparent, black and cyan theme.
    """
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
        """Initializes the UI components with a professional grid layout."""
        self.setObjectName("accountSummary")
        self.setMinimumWidth(280) # Restored to original width
        self.setCursor(QCursor(Qt.PointingHandCursor))

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15) # Restored to original margins
        main_layout.setSpacing(0)

        content_frame = QFrame()
        content_frame.setObjectName("contentFrame")
        main_layout.addWidget(content_frame)

        grid_layout = QGridLayout(content_frame)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(10) # Restored to original spacing

        # Create metrics
        self.labels['unrealized_pnl'] = self._create_metric_widget(grid_layout, "Unrealized", 0, 0)
        self.labels['realized_pnl'] = self._create_metric_widget(grid_layout, "Realized", 0, 1)
        self.labels['used_margin'] = self._create_metric_widget(grid_layout, "Used Margin", 1, 0)
        self.labels['available_margin'] = self._create_metric_widget(grid_layout, "Available", 1, 1)
        self.labels['win_rate'] = self._create_metric_widget(grid_layout, "Win Rate", 2, 0)
        self.labels['trade_count'] = self._create_metric_widget(grid_layout, "Trades", 2, 1)

    def _create_metric_widget(self, layout, title_text, row, col):
        """Factory method for creating a single metric display box."""
        frame = QFrame()
        frame.setObjectName("metricFrame")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        frame.setFixedHeight(53) # 5% height reduction on metric boxes ONLY

        metric_layout = QVBoxLayout(frame)
        metric_layout.setContentsMargins(5, 5, 5, 6)
        metric_layout.setSpacing(2)
        metric_layout.setAlignment(Qt.AlignCenter)

        value_label = QLabel("0")
        value_label.setObjectName("metricValueLabel")
        value_label.setAlignment(Qt.AlignCenter)

        title_label = QLabel(title_text.upper())
        title_label.setObjectName("metricTitleLabel")
        title_label.setAlignment(Qt.AlignCenter)

        metric_layout.addWidget(value_label)
        metric_layout.addWidget(title_label)

        layout.addWidget(frame, row, col)
        return value_label

    def update_summary(self, unrealized_pnl=0.0, realized_pnl=0.0,
                       used_margin=0.0, available_margin=0.0,
                       win_rate=0.0, trade_count=0):
        """Public method to update all widget labels with new data."""
        profit_color = "#1DE9B6"
        loss_color = "#FF4081"
        neutral_color = "#B0BEC5"
        margin_color = "#00B0FF"

        # P&L Breakdown
        self.labels['unrealized_pnl'].setText(format_indian_currency(unrealized_pnl))
        self.labels['unrealized_pnl'].setStyleSheet(f"color: {'{profit_color}' if unrealized_pnl >= 0 else '{loss_color}'};")

        self.labels['realized_pnl'].setText(format_indian_currency(realized_pnl))
        self.labels['realized_pnl'].setStyleSheet(f"color: {'{profit_color}' if realized_pnl >= 0 else '{loss_color}'};")

        # Margin Details
        self.labels['used_margin'].setText(format_indian_currency(used_margin))
        self.labels['used_margin'].setStyleSheet(f"color: {margin_color};")

        self.labels['available_margin'].setText(format_indian_currency(available_margin))
        self.labels['available_margin'].setStyleSheet(f"color: {margin_color};")

        # Performance Metrics
        win_rate_color = profit_color if win_rate >= 60 else "#FFB74D" if win_rate >= 40 else loss_color if trade_count > 0 else neutral_color
        self.labels['win_rate'].setText(f"{win_rate:.0f}%")
        self.labels['win_rate'].setStyleSheet(f"color: {win_rate_color};")

        self.labels['trade_count'].setText(str(trade_count))
        self.labels['trade_count'].setStyleSheet(f"color: {neutral_color};")

    def _apply_styles(self):
        """Applies a semi-transparent, high-contrast black and cyan theme."""
        self.setStyleSheet("""
            #accountSummary {
                background: rgba(0, 0, 0, 0.5);
                border: 1px solid rgba(13, 115, 119, 0.5);
                border-radius: 12px;
            }

            #contentFrame {
                background: transparent;
                border: none;
            }

            #metricFrame {
                border: 1px solid rgba(13, 115, 119, 0.5);
                border-radius: 6px;
            }

            #metricFrame:hover {
                border-color: rgba(29, 233, 182, 0.6);
            }

            #metricTitleLabel {
                color: #B0BEC5;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.5px;
                text-transform: uppercase;
                background: transparent;
            }

            #metricValueLabel {
                color: #FFFFFF;
                font-size: 15px; /* Scaled down to fit smaller boxes */
                font-weight: 600;
                font-family: 'Segoe UI', 'Roboto Mono', monospace;
                background: transparent;
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