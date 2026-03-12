import logging
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QVBoxLayout,QWidget, QGroupBox, QGridLayout, QLabel)
logger = logging.getLogger(__name__)

class PerformanceWidget(QGroupBox):
    """Widget showing trading performance metrics"""

    def __init__(self):
        super().__init__()
        self.setTitle("TODAY'S PERFORMANCE")
        self.setFont(QFont("Inter", 9, QFont.Weight.Bold))
        self.setStyleSheet("""
            QGroupBox {
                color: #7A8799;
                border: 1px solid #1C2333;
                border-radius: 2px;
                margin-top: 10px;
                background-color: #0C0F17;
                padding-top: 10px;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.08em;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 6px;
                background-color: #0C0F17;
            }
        """)
        self.setFixedHeight(180)
        self._setup_ui()

    def _setup_ui(self):
        """Initialize the UI"""
        layout = QGridLayout(self)
        # Reduced margins and increased top margin for title space
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        self.metrics = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'win_rate': 0.0,
            'avg_profit': 0.0,
            'avg_loss': 0.0,
            'max_profit': 0.0
        }

        self.labels = {}

        # Define metric layout
        metric_configs = [
            ('Total Trades', 'total_trades', 0, 0),
            ('Winners', 'winning_trades', 0, 2),
            ('Losers', 'losing_trades', 0, 4),
            ('Today P&L', 'total_pnl', 1, 0),
            ('Win Rate', 'win_rate', 1, 2),
            ('Avg Win', 'avg_profit', 1, 4),
            ('Avg Loss', 'avg_loss', 0, 6),
            ('Best Trade', 'max_profit', 1, 6),
        ]

        for label_text, metric_key, row, col in metric_configs:
            # Create metric container
            container = QWidget()
            container_layout = QVBoxLayout(container)
            # Reduced margins to prevent clipping
            container_layout.setContentsMargins(5, 8, 5, 8)
            container_layout.setSpacing(3)

            # Value label
            value_label = QLabel("0")
            value_label.setAlignment(Qt.AlignCenter)
            value_label.setFont(QFont("Cascadia Code", 11, QFont.Weight.Bold))
            value_label.setObjectName("value")
            value_label.setMinimumHeight(20)

            # Title label
            title_label = QLabel(label_text)
            title_label.setAlignment(Qt.AlignCenter)
            title_label.setFont(QFont("Inter", 8))
            title_label.setStyleSheet("color: #7A8799;")
            title_label.setMinimumHeight(15)

            container_layout.addWidget(value_label)
            container_layout.addWidget(title_label)

            container.setStyleSheet("""
                QWidget {
                    background-color: #111520;
                    border: 1px solid #1C2333;
                    border-radius: 2px;
                }
                QLabel#value {
                    color: #C8D0DC;
                    padding: 2px;
                }
            """)

            # Set minimum size for container to prevent clipping
            container.setMinimumHeight(50)
            container.setMinimumWidth(80)

            layout.addWidget(container, row, col, 1, 2)
            self.labels[metric_key] = value_label

        self.update_metrics(self.metrics)

    def update_metrics(self, metrics: dict):
        """Update displayed metrics"""
        self.metrics.update(metrics)

        MONO = "font-family:'Cascadia Code','Consolas',monospace;padding:2px;"
        PROFIT = "#1DB87E"
        LOSS   = "#E0424A"
        HI     = "#C8D0DC"

        for key, value in self.metrics.items():
            if key not in self.labels:
                continue
            label = self.labels[key]
            if key == 'total_pnl':
                color = PROFIT if value >= 0 else LOSS
                label.setText(f"₹{value:+,.0f}")
                label.setStyleSheet(f"color:{color};font-weight:700;{MONO}")
            elif key == 'win_rate':
                color = PROFIT if value >= 50 else LOSS
                label.setText(f"{value:.1f}%")
                label.setStyleSheet(f"color:{color};font-weight:700;{MONO}")
            elif key in ['avg_profit', 'avg_loss', 'max_profit']:
                label.setText(f"₹{value:,.0f}")
                label.setStyleSheet(f"color:{HI};font-weight:700;{MONO}")
            elif key == 'winning_trades':
                label.setText(str(int(value)))
                label.setStyleSheet(f"color:{PROFIT};font-weight:700;{MONO}")
            elif key == 'losing_trades':
                label.setText(str(int(value)))
                label.setStyleSheet(f"color:{LOSS};font-weight:700;{MONO}")
            else:
                label.setText(str(int(value)))
                label.setStyleSheet(f"color:{HI};font-weight:700;{MONO}")