# dialogs/open_positions_dialog.py
import logging
from typing import List
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QDialog, QLabel, QWidget
)
from PySide6.QtCore import QTimer, Qt, Signal,QByteArray
from widgets.open_positions_table import OpenPositionsTable
from utils.data_models import Position
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class OpenPositionsDialog(QDialog):
    """
    A premium, redesigned dialog for managing active trading positions,
    now styled with the consistent rich and modern dark theme.
    """
    position_exit_requested = Signal(str)
    refresh_requested = Signal()
    modify_sl_tp_requested = Signal(str) # Added missing signal

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
        """Configure window properties for the custom frameless design."""
        self.setWindowTitle("Open Positions")
        self.setMinimumSize(800, 600)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        """Initialize the main UI components with the new premium layout."""
        container = QWidget(self)
        container.setObjectName("mainContainer")

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 10, 20, 20)
        container_layout.setSpacing(15)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout.addLayout(self._create_header())
        self.positions_table = OpenPositionsTable()
        container_layout.addWidget(self.positions_table, 1)
        container_layout.addLayout(self._create_footer())

    def _create_header(self):
        """Creates a custom header with title, P&L, and close button."""
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Active Positions")
        title.setObjectName("dialogTitle")
        self.total_pnl_label = QLabel("₹0.00")
        self.total_pnl_label.setObjectName("totalPnlLabel")
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.clicked.connect(self.close)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.total_pnl_label)
        header_layout.addWidget(self.close_btn)
        return header_layout

    def _create_footer(self):
        """Creates a custom footer with position count and action buttons."""
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 5, 0, 0)
        self.position_count_label = QLabel("0 Positions")
        self.position_count_label.setObjectName("footerLabel")
        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.setObjectName("secondaryButton") # Changed for consistent styling
        footer_layout.addWidget(self.position_count_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.refresh_button)
        return footer_layout

    def _apply_styles(self):
        """Apply the QSS stylesheet for the dialog's premium dark theme."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle { color: #FFFFFF; font-size: 16px; font-weight: 600; }
            #closeButton {
                background-color: transparent; border: none; color: #8A9BA8;
                font-size: 16px; font-weight: bold;
            }
            #closeButton:hover { color: #FFFFFF; }
            #totalPnlLabel { font-size: 22px; font-weight: 600; }
            #footerLabel {
                color: #A9B1C3; font-size: 11px; font-weight: bold; text-transform: uppercase;
            }
            #secondaryButton {
                font-weight: bold; border-radius: 6px; padding: 10px 16px;
                border: none; font-size: 12px;
                background-color: #3A4458; color: #E0E0E0;
            }
            #secondaryButton:hover { background-color: #4A5568; }
            #secondaryButton:disabled { background-color: #2A3140; color: #666; }
        """)


    def _setup_timer(self):
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update_total_pnl)
        self.update_timer.start(1000)

    def _connect_signals(self):
        self.refresh_button.clicked.connect(self._on_refresh_clicked)
        self.positions_table.position_exit_requested.connect(self.position_exit_requested.emit)
        # self.positions_table.modify_sl_tp_requested.connect(self.modify_sl_tp_requested.emit) # Connect the signal

    def _restore_geometry(self):
        # FIX: Use the ConfigManager
        geometry_str = self.config_manager.load_dialog_state('open_positions')
        if geometry_str:
            self.restoreGeometry(QByteArray.fromBase64(geometry_str.encode('utf-8')))


    def _on_refresh_clicked(self):
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("REFRESHING...")
        self.refresh_requested.emit()

    def on_refresh_completed(self, success: bool):
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("REFRESH")
        logger.info(f"Dialog UI updated after refresh status: {success}")

    def update_positions(self, positions: List[Position]):
        self.positions_table.update_positions(positions)
        self._update_total_pnl()
        self._update_position_count()

    def _update_total_pnl(self):
        positions = self.positions_table.get_all_positions()
        total_pnl = sum(pos.pnl for pos in positions)
        self.total_pnl_label.setText(f"₹{total_pnl:,.2f}")
        color = "#29C7C9" if total_pnl >= 0 else "#F85149" if total_pnl < 0 else "#a0a0a0"
        self.total_pnl_label.setStyleSheet(f"color: {color};")

    def _update_position_count(self):
        count = len(self.positions_table.get_all_positions())
        self.position_count_label.setText(f"{count} ACTIVE POSITION{'S' if count != 1 else ''}")

    def closeEvent(self, event):
        # FIX: Use the ConfigManager
        geometry_bytes = self.saveGeometry()
        self.config_manager.save_dialog_state('open_positions', geometry_bytes.toBase64().data().decode('utf-8'))
        self.update_timer.stop()
        super().closeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()
