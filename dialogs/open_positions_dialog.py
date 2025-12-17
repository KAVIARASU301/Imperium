import logging
from typing import List

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QDialog,
    QLabel, QWidget, QFrame
)
from PySide6.QtCore import QTimer, Qt, Signal, QByteArray

from widgets.open_positions_table import OpenPositionsTable
from utils.data_models import Position
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class OpenPositionsDialog(QDialog):
    """
    Polished, premium Open Positions dialog
    (aesthetics aligned with main application)
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

    # ------------------------------------------------------------------
    def _setup_window(self):
        self.setWindowTitle("Open Positions")
        self.setMinimumSize(900, 600)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    # ------------------------------------------------------------------
    def _setup_ui(self):
        self.container = QWidget(self)
        self.container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(22, 14, 22, 18)
        layout.setSpacing(14)

        layout.addLayout(self._create_header())
        layout.addWidget(self._create_divider())

        # ---- Table frame (visual containment) ----
        table_frame = QFrame()
        table_frame.setObjectName("tableFrame")
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(10, 10, 10, 10)

        self.positions_table = OpenPositionsTable()
        table_layout.addWidget(self.positions_table)

        layout.addWidget(table_frame, 1)
        layout.addLayout(self._create_footer())

        # Allow dragging from background
        self.container.mousePressEvent = self.mousePressEvent
        self.container.mouseMoveEvent = self.mouseMoveEvent
        self.container.mouseReleaseEvent = self.mouseReleaseEvent

    # ------------------------------------------------------------------
    def _create_header(self):
        header = QHBoxLayout()
        header.setSpacing(10)

        title = QLabel("ACTIVE POSITIONS")
        title.setObjectName("dialogTitle")

        self.total_pnl_label = QLabel("₹0.00")
        self.total_pnl_label.setObjectName("pnlBadge")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.close)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.total_pnl_label)
        header.addWidget(close_btn)

        return header

    # ------------------------------------------------------------------
    def _create_footer(self):
        footer = QHBoxLayout()
        footer.setSpacing(10)

        self.position_count_label = QLabel("0 ACTIVE POSITIONS")
        self.position_count_label.setObjectName("footerLabel")

        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.setObjectName("secondaryButton")

        footer.addWidget(self.position_count_label)
        footer.addStretch()
        footer.addWidget(self.refresh_button)

        return footer

    # ------------------------------------------------------------------
    def _create_divider(self):
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        return divider

    # ------------------------------------------------------------------
    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 14px;
                font-family: "Segoe UI", sans-serif;
            }

            #dialogTitle {
                color: #E6EAF2;
                font-size: 15px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }

            #divider {
                background-color: #2A3140;
            }

            #pnlBadge {
                padding: 6px 14px;
                border-radius: 14px;
                font-size: 14px;
                font-weight: 600;
                background-color: #212635;
            }

            #tableFrame {
                background-color: #121622;
                border: 1px solid #2A3140;
                border-radius: 10px;
            }

            #footerLabel {
                color: #8A9BA8;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 0.4px;
            }

            #secondaryButton {
                background-color: #2A3140;
                color: #E0E0E0;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 16px;
                border-radius: 6px;
                border: none;
            }

            #secondaryButton:hover {
                background-color: #3A4458;
            }

            #secondaryButton:disabled {
                background-color: #212635;
                color: #666666;
            }

            #closeButton {
                background-color: transparent;
                border: none;
                color: #8A9BA8;
                font-size: 16px;
                font-weight: bold;
            }

            #closeButton:hover {
                color: #FFFFFF;
            }
        """)

    # ------------------------------------------------------------------
    def _setup_timer(self):
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update_total_pnl)
        self.update_timer.start(1000)

    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.refresh_button.clicked.connect(self._on_refresh_clicked)
        self.positions_table.position_exit_requested.connect(
            self.position_exit_requested.emit
        )

    # ------------------------------------------------------------------
    def _restore_geometry(self):
        geometry_str = self.config_manager.load_dialog_state("open_positions")
        if geometry_str:
            self.restoreGeometry(QByteArray.fromBase64(geometry_str.encode()))

    # ------------------------------------------------------------------
    def _on_refresh_clicked(self):
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("REFRESHING…")
        self.refresh_requested.emit()

    def on_refresh_completed(self, success: bool):
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("REFRESH")
        logger.info("Open positions refreshed")

    # ------------------------------------------------------------------
    def update_positions(self, positions: List[Position]):
        self.positions_table.update_positions(positions)
        self._update_total_pnl()
        self._update_position_count()

    def _update_total_pnl(self):
        positions = self.positions_table.get_all_positions()
        total_pnl = sum(pos.pnl for pos in positions)

        self.total_pnl_label.setText(f"₹{total_pnl:,.2f}")
        color = "#29C7C9" if total_pnl >= 0 else "#F85149"
        self.total_pnl_label.setStyleSheet(
            f"color: {color}; background-color: #212635;"
        )

    def _update_position_count(self):
        count = len(self.positions_table.get_all_positions())
        self.position_count_label.setText(
            f"{count} ACTIVE POSITION{'S' if count != 1 else ''}"
        )

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        geometry_bytes = self.saveGeometry()
        self.config_manager.save_dialog_state(
            "open_positions",
            geometry_bytes.toBase64().data().decode()
        )
        self.update_timer.stop()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Drag support
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()
