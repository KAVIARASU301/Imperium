import logging
from typing import List

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QDialog,
    QLabel, QWidget, QFrame
)
from PySide6.QtCore import QTimer, Qt, Signal, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor

from widgets.open_positions_table import OpenPositionsTable
from utils.data_models import Position
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class OpenPositionsDialog(QDialog):
    """
    Modern, clean Open Positions dialog with improved UI/UX
    """

    position_exit_requested = Signal(str)
    refresh_requested = Signal()
    modify_sl_tp_requested = Signal(str)

    def __init__(self, parent=None, config_manager: ConfigManager = None):
        super().__init__(parent)

        self.config_manager = config_manager or ConfigManager()
        self._drag_pos = None

        self._setup_window()
        self._setup_ui()
        self._setup_timer()
        self._connect_signals()
        self._apply_styles()
        self._restore_geometry()

    def _setup_window(self):
        self.setWindowTitle("Open Positions")
        self.setMinimumSize(1000, 600)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)

    def _setup_ui(self):
        self.container = QWidget(self)
        self.container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title bar
        layout.addWidget(self._create_title_bar())

        # Main content area
        content_widget = QWidget()
        content_widget.setObjectName("contentArea")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(16)

        # Stats bar
        content_layout.addLayout(self._create_stats_bar())

        # Table container
        table_container = QFrame()
        table_container.setObjectName("tableContainer")
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)

        self.positions_table = OpenPositionsTable()
        table_layout.addWidget(self.positions_table)

        content_layout.addWidget(table_container, 1)

        # Footer
        content_layout.addLayout(self._create_footer())

        layout.addWidget(content_widget, 1)

    def _create_title_bar(self):
        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(52)

        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(20, 0, 12, 0)
        layout.setSpacing(12)

        # Title with icon indicator
        title_container = QHBoxLayout()
        title_container.setSpacing(8)

        # Pulse indicator
        pulse = QLabel("●")
        pulse.setObjectName("pulseIndicator")

        title = QLabel("Open Positions")
        title.setObjectName("dialogTitle")

        title_container.addWidget(pulse)
        title_container.addWidget(title)

        layout.addLayout(title_container)
        layout.addStretch()

        # Action buttons
        self.refresh_button = QPushButton("⟳")
        self.refresh_button.setObjectName("iconButton")
        self.refresh_button.setFixedSize(36, 36)
        self.refresh_button.setToolTip("Refresh Positions")

        minimize_btn = QPushButton("−")
        minimize_btn.setObjectName("iconButton")
        minimize_btn.setFixedSize(36, 36)
        minimize_btn.setToolTip("Minimize")
        minimize_btn.clicked.connect(self.showMinimized)

        close_btn = QPushButton("×")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(36, 36)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self.close)

        layout.addWidget(self.refresh_button)
        layout.addWidget(minimize_btn)
        layout.addWidget(close_btn)

        # Enable dragging from title bar
        title_bar.mousePressEvent = self.mousePressEvent
        title_bar.mouseMoveEvent = self.mouseMoveEvent
        title_bar.mouseReleaseEvent = self.mouseReleaseEvent

        return title_bar

    def _create_stats_bar(self):
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        # Position count card
        count_card = self._create_stat_card("Positions", "0", "#58A6FF")
        self.position_count_label = count_card.findChild(QLabel, "statValue")

        # Total P&L card
        pnl_card = self._create_stat_card("Total P&L", "₹0.00", "#3FB950")
        self.total_pnl_label = pnl_card.findChild(QLabel, "statValue")

        # Day's Range (optional - can add more stats)
        range_card = self._create_stat_card("Active", "Live", "#FFA657")

        stats_layout.addWidget(count_card)
        stats_layout.addWidget(pnl_card)
        stats_layout.addWidget(range_card)
        stats_layout.addStretch()

        return stats_layout

    def _create_stat_card(self, label: str, value: str, color: str):
        card = QFrame()
        card.setObjectName("statCard")
        card.setFixedHeight(70)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(4)

        label_widget = QLabel(label)
        label_widget.setObjectName("statLabel")

        value_widget = QLabel(value)
        value_widget.setObjectName("statValue")
        value_widget.setStyleSheet(f"color: {color};")

        layout.addWidget(label_widget)
        layout.addWidget(value_widget)
        layout.addStretch()

        return card

    def _create_footer(self):
        footer = QHBoxLayout()
        footer.setSpacing(12)

        # Info label
        self.info_label = QLabel("Live market data • Updated every second")
        self.info_label.setObjectName("infoLabel")

        footer.addWidget(self.info_label)
        footer.addStretch()

        return footer

    def _apply_styles(self):
        self.setStyleSheet("""
            /* Main Container */
            #mainContainer {
                background-color: #0D1117;
                border: 1px solid #30363D;
                border-radius: 12px;
            }

            /* Title Bar */
            #titleBar {
                background-color: #161B22;
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
                border-bottom: 1px solid #21262D;
            }

            #dialogTitle {
                color: #F0F6FC;
                font-size: 15px;
                font-weight: 700;
                letter-spacing: 0.3px;
            }

            #pulseIndicator {
                color: #3FB950;
                font-size: 12px;
            }

            /* Icon Buttons */
            #iconButton {
                background-color: #21262D;
                color: #C9D1D9;
                border: 1px solid #30363D;
                border-radius: 6px;
                font-size: 18px;
                font-weight: 600;
            }

            #iconButton:hover {
                background-color: #30363D;
                color: #F0F6FC;
                border-color: #58A6FF;
            }

            #iconButton:pressed {
                background-color: #1C2128;
            }

            #closeButton {
                background-color: #21262D;
                color: #C9D1D9;
                border: 1px solid #30363D;
                border-radius: 6px;
                font-size: 20px;
                font-weight: 600;
            }

            #closeButton:hover {
                background-color: #DA3633;
                color: #FFFFFF;
                border-color: #DA3633;
            }

            /* Content Area */
            #contentArea {
                background-color: #0D1117;
            }

            /* Stat Cards */
            #statCard {
                background-color: #161B22;
                border: 1px solid #21262D;
                border-radius: 8px;
                min-width: 160px;
            }

            #statCard:hover {
                border-color: #30363D;
                background-color: #1C2128;
            }

            #statLabel {
                color: #8B949E;
                font-size: 11px;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            #statValue {
                color: #F0F6FC;
                font-size: 20px;
                font-weight: 700;
            }

            /* Table Container */
            #tableContainer {
                background-color: #0D1117;
                border: 1px solid #21262D;
                border-radius: 8px;
            }

            /* Footer */
            #infoLabel {
                color: #8B949E;
                font-size: 11px;
                font-weight: 500;
                letter-spacing: 0.3px;
            }
        """)

    def _setup_timer(self):
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update_display)
        self.update_timer.start(1000)

    def _connect_signals(self):
        self.refresh_button.clicked.connect(self._on_refresh_clicked)
        self.positions_table.position_exit_requested.connect(
            self.position_exit_requested.emit
        )

    def _restore_geometry(self):
        geometry_str = self.config_manager.load_dialog_state("open_positions")
        if geometry_str:
            self.restoreGeometry(QByteArray.fromBase64(geometry_str.encode()))

    def _on_refresh_clicked(self):
        self.refresh_button.setEnabled(False)
        original_text = self.refresh_button.text()
        self.refresh_button.setText("⌛")
        self.refresh_requested.emit()

        # Re-enable after a short delay (will be reset when refresh completes)
        QTimer.singleShot(300, lambda: self.refresh_button.setText(original_text))

    def on_refresh_completed(self, success: bool):
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("⟳")
        logger.info("Open positions refreshed")

    def update_positions(self, positions: List[Position]):
        self.positions_table.update_positions(positions)
        self._update_display()

    def _update_display(self):
        """Update all display elements"""
        positions = self.positions_table.get_all_positions()

        # Update count
        count = len(positions)
        self.position_count_label.setText(str(count))

        # Update total P&L
        total_pnl = sum(pos.pnl for pos in positions)
        self.total_pnl_label.setText(f"₹{total_pnl:,.2f}")

        # Update P&L color
        if total_pnl > 0:
            color = "#3FB950"  # Green
        elif total_pnl < 0:
            color = "#F85149"  # Red
        else:
            color = "#8B949E"  # Gray

        self.total_pnl_label.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: 700;")

    def closeEvent(self, event):
        geometry_bytes = self.saveGeometry()
        self.config_manager.save_dialog_state(
            "open_positions",
            geometry_bytes.toBase64().data().decode()
        )
        self.update_timer.stop()
        super().closeEvent(event)

    # Drag support
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()