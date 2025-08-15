import logging
import json
import os
from typing import Dict, List
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QStyledItemDelegate, QMenu, QStyle, QApplication,
                               QStyleOptionButton, QAbstractItemView, QFrame)
from PySide6.QtCore import Qt, Signal, QPoint, QTimer, QStandardPaths
from PySide6.QtGui import QColor, QPalette, QPainter, QFont, QAction, QPixmap

from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class PositionsTable(QWidget):
    """
    A compound widget containing a compact, data-dense positions table with fixed symbol visibility
    """
    exit_requested = Signal(dict)
    refresh_requested = Signal()
    modify_sl_tp_requested = Signal(str)

    # Column indices
    SYMBOL_COL = 0
    QUANTITY_COL = 1
    AVG_PRICE_COL = 2
    LTP_COL = 3
    PNL_COL = 4
    SLTP_INFO_COL = 5

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.table_name = "positions_table"
        self.positions = {}
        self.position_row_map = {}  # Maps symbol to row number

        self._init_ui()
        self._apply_styles()
        self._connect_signals()

        # Load column widths after UI is initialized
        if not self._load_column_widths():
            self._set_default_column_widths()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.table = QTableWidget()
        self.table.headers = ["Symbol", "Qty", "Avg Price", "LTP", "P&L", "SL/TP/TSL"]
        self.table.setColumnCount(len(self.table.headers))
        self.table.setHorizontalHeaderLabels(self.table.headers)
        self.table.setMouseTracking(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        main_layout.addWidget(self.table, 1)

        footer_widget = QWidget()
        footer_widget.setObjectName("footer")
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(10, 5, 10, 5)

        self.total_pnl_label = QLabel("Total P&L: ₹ 0")
        self.total_pnl_label.setObjectName("footerLabel")

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("footerButton")

        footer_layout.addWidget(self.total_pnl_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.refresh_button)
        main_layout.addWidget(footer_widget)

    def _apply_styles(self):
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.SYMBOL_COL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.SLTP_INFO_COL, QHeaderView.ResizeMode.Stretch)
        for i in [self.QUANTITY_COL, self.AVG_PRICE_COL, self.LTP_COL, self.PNL_COL]:
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

        stylesheet = """
            QTableWidget {
                background-color: #161A25;
                color: #E0E0E0;
                border: none;
                font-size: 13px;
                gridline-color: transparent;
                selection-background-color: #2A3140;
                alternate-background-color: #1C212B;
            }
            QHeaderView::section {
                background-color: #2A3140;
                color: #A9B1C3;
                padding: 8px;
                border: none;
                font-weight: 600;
                font-size: 12px;
                text-transform: uppercase;
            }
            QHeaderView::section:hover {
                background-color: #3A4458;
            }
            QTableWidget::item {
                padding: 8px;
                border-bottom: 1px solid #1C212B;
            }
            QTableWidget::item:selected {
                background-color: #2A3140;
            }
            QTableWidget::item:hover {
                background-color: #252B36;
            }
            #footer {
                background-color: #212635;
                border-top: 1px solid #3A4458;
            }
            #footerLabel {
                color: #E0E0E0;
                font-size: 13px;
                font-weight: 600;
            }
            #footerButton {
                background-color: transparent;
                color: #A9B1C3;
                border: 1px solid #3A4458;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 12px;
            }
            #footerButton:hover {
                background-color: #29C7C9;
                color: #161A25;
                border-color: #29C7C9;
            }
        """
        self.setStyleSheet(stylesheet)

    def _connect_signals(self):
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        # Connect header resize signal to save column widths
        self.table.horizontalHeader().sectionResized.connect(self._on_column_resized)

    def _show_context_menu(self, pos: QPoint):
        item = self.table.itemAt(pos)
        if not item:
            return

        row = item.row()
        symbol_item = self.table.item(row, self.SYMBOL_COL)
        if not symbol_item:
            return

        # Extract symbol from the item text (remove indicators)
        symbol_text = symbol_item.text()
        symbol = symbol_text.split()[0]  # Get first word (symbol)

        if symbol not in self.positions:
            return

        pos_data = self.positions[symbol]

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2A3140;
                color: #E0E0E0;
                border: 1px solid #3A4458;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #29C7C9;
                color: #161A25;
            }
            QMenu::separator {
                height: 1px;
                background-color: #3A4458;
                margin: 4px 8px;
            }
        """)

        # Add/Modify SL/TP action
        modify_action = QAction("⚙ Add/Modify SL/TP", self)
        modify_action.triggered.connect(lambda: self.modify_sl_tp_requested.emit(symbol))
        menu.addAction(modify_action)

        menu.addSeparator()

        # Exit position action
        exit_action = QAction("✕ Exit Position", self)
        exit_action.triggered.connect(lambda: self.exit_requested.emit(pos_data))
        menu.addAction(exit_action)

        # Show menu at cursor position
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def update_positions(self, positions_data):
        self.table.setRowCount(0)
        self.positions.clear()
        self.position_row_map.clear()

        for pos in positions_data:
            self.add_position(pos)
        self._update_footer()

    def add_position(self, pos_data: dict):
        symbol = pos_data['tradingsymbol']
        self.positions[symbol] = pos_data

        row_position = self.table.rowCount()
        self.table.insertRow(row_position)
        self.position_row_map[symbol] = row_position

        self.table.setRowHeight(row_position, 40)

        # Symbol with indicators
        self._set_symbol_item(row_position, pos_data)

        # Quantity
        self._set_item(row_position, self.QUANTITY_COL, pos_data.get('quantity', 0))

        # Average Price
        self._set_item(row_position, self.AVG_PRICE_COL, pos_data.get('average_price', 0.0), is_price=True)

        # LTP
        self._set_item(row_position, self.LTP_COL, pos_data.get('last_price', 0.0), is_price=True)

        # P&L
        pnl_value = pos_data.get('pnl', 0.0)
        self._set_pnl_item(row_position, pnl_value)

        # SL/TP Info
        self._set_sltp_info_item(row_position, pos_data)

    def _update_footer(self):
        total_pnl = sum(pos.get('pnl', 0.0) for pos in self.positions.values())
        pnl_text = f"Total P&L: ₹ {total_pnl:,.0f}"
        self.total_pnl_label.setText(pnl_text)
        pnl_color = "#1DE9B6" if total_pnl >= 0 else "#F85149"
        self.total_pnl_label.setStyleSheet(f"color: {pnl_color}; font-weight: 600; font-size: 13px;")

    def _set_item(self, row, col, data, is_text=False, is_price=False):
        item = QTableWidgetItem()
        if is_text:
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            item.setText(str(data))
        else:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if is_price:
                item.setText(f"₹{data:,.2f}")
            else:
                item.setText(f"{int(data):,}")

        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, col, item)

    def _set_symbol_item(self, row, pos_data):
        symbol = pos_data.get('tradingsymbol', 'N/A')
        sl_set = pos_data.get('stop_loss_price') is not None and pos_data.get('stop_loss_price') > 0
        tp_set = pos_data.get('target_price') is not None and pos_data.get('target_price') > 0

        # Build symbol text with indicators
        display_text = symbol
        indicators = []

        if sl_set:
            indicators.append("SL")
        if tp_set:
            indicators.append("TP")

        if indicators:
            display_text += f" [{'/'.join(indicators)}]"

        item = QTableWidgetItem(display_text)
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)

        # Color coding for indicators
        if sl_set and tp_set:
            item.setForeground(QColor("#4ECDC4"))  # Cyan for both
        elif sl_set:
            item.setForeground(QColor("#FF6B6B"))  # Red for SL
        elif tp_set:
            item.setForeground(QColor("#4ECDC4"))  # Cyan for TP
        else:
            item.setForeground(QColor("#E0E0E0"))  # Default

        self.table.setItem(row, self.SYMBOL_COL, item)

    def _set_pnl_item(self, row, pnl_value):
        item = QTableWidgetItem()
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        item.setText(f"₹{pnl_value:,.0f}")
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)

        # Color based on profit/loss
        pnl_color = QColor("#1DE9B6") if pnl_value >= 0 else QColor("#F85149")
        item.setForeground(pnl_color)

        # Make font bold for emphasis
        font = QFont()
        font.setBold(True)
        item.setFont(font)

        self.table.setItem(row, self.PNL_COL, item)

    def _set_sltp_info_item(self, row, pos_data):
        sl_price = pos_data.get('stop_loss_price')
        tp_price = pos_data.get('target_price')
        tsl = pos_data.get('trailing_stop_loss')

        # Format: SL/TP/TSL - compact display
        sl_text = f"{sl_price:.0f}" if sl_price and sl_price > 0 else "-"
        tp_text = f"{tp_price:.0f}" if tp_price and tp_price > 0 else "-"
        tsl_text = f"{tsl:.0f}" if tsl and tsl > 0 else "-"

        # Compact format: SL/TP/TSL
        info_text = f"{sl_text}/{tp_text}/{tsl_text}"

        item = QTableWidgetItem(info_text)
        item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)

        # Style based on what's set
        if (sl_price and sl_price > 0) or (tp_price and tp_price > 0) or (tsl and tsl > 0):
            item.setForeground(QColor("#A9B1C3"))
            # Set monospace font for better alignment
            font = QFont("Consolas", 11)  # Monospace font
            if not font.exactMatch():
                font.setFamily("Monaco")  # Mac fallback
            if not font.exactMatch():
                font.setFamily("monospace")  # Generic fallback
            item.setFont(font)
        else:
            item.setForeground(QColor("#6B7280"))

        self.table.setItem(row, self.SLTP_INFO_COL, item)

    def _load_column_widths(self):
        """Load saved column widths from JSON file"""
        try:
            config_dir = os.path.expanduser("~/.options_scalper")
            os.makedirs(config_dir, exist_ok=True)
            config_file = os.path.join(config_dir, "positions_table_columns.json")

            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    saved_widths = json.load(f)

                logger.info(f"Loading saved column widths: {saved_widths}")

                for col_name, width in saved_widths.items():
                    if col_name in self.table.headers:
                        col_index = self.table.headers.index(col_name)
                        self.table.setColumnWidth(col_index, int(width))

                return True
            else:
                logger.info("No saved column widths found, using defaults")
                return False

        except Exception as e:
            logger.error(f"Error loading column widths: {e}")
            return False

    def _set_default_column_widths(self):
        """Set sensible default column widths"""
        default_widths = {
            self.SYMBOL_COL: 120,  # Symbol
            self.QUANTITY_COL: 80,  # Qty
            self.AVG_PRICE_COL: 100,  # Avg Price
            self.LTP_COL: 100,  # LTP
            self.PNL_COL: 100,  # P&L
            self.SLTP_INFO_COL: 120  # SL/TP/TSL
        }

        for col_index, width in default_widths.items():
            self.table.setColumnWidth(col_index, width)

        logger.info("Applied default column widths")

    def _on_column_resized(self, logical_index, old_size, new_size):
        """Called when user resizes a column - save the new widths"""
        # Use QTimer to avoid saving too frequently during drag operations
        if not hasattr(self, '_save_timer'):
            self._save_timer = QTimer()
            self._save_timer.setSingleShot(True)
            self._save_timer.timeout.connect(self._save_column_widths)

        self._save_timer.stop()
        self._save_timer.start(500)  # Save after 500ms of no resize activity

    def _save_column_widths(self):
        """Save current column widths to JSON file"""
        try:
            config_dir = os.path.expanduser("~/.options_scalper")
            os.makedirs(config_dir, exist_ok=True)
            config_file = os.path.join(config_dir, "positions_table_columns.json")

            column_widths = {}
            for i, header_name in enumerate(self.table.headers):
                column_widths[header_name] = self.table.columnWidth(i)

            with open(config_file, 'w') as f:
                json.dump(column_widths, f, indent=2)

            logger.debug(f"Saved column widths: {column_widths}")

        except Exception as e:
            logger.error(f"Error saving column widths: {e}")

    def closeEvent(self, event):
        """Save column widths when widget is closed"""
        self._save_column_widths()
        super().closeEvent(event)

    def save_state(self):
        """Public method to save state - call this when parent window closes"""
        self._save_column_widths()
