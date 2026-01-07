import logging
import json
import os
from typing import Dict, List
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMenu, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal, QPoint, QTimer, QEvent
from PySide6.QtGui import QColor, QFont

from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class PositionsTable(QWidget):
    """
    A compound widget containing a compact, data-dense positions table with two-row display
    """

    exit_requested = Signal(dict)
    refresh_requested = Signal()
    modify_sl_tp_requested = Signal(str)

    SYMBOL_COL = 0
    QUANTITY_COL = 1
    AVG_PRICE_COL = 2
    LTP_COL = 3
    PNL_COL = 4

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)

        self.config_manager = config_manager
        self.table_name = "positions_table"
        self.positions: Dict[str, dict] = {}
        self.position_row_map: Dict[str, int] = {}

        self._hovered_row = -1

        self._init_ui()
        self._apply_styles()
        self._connect_signals()

        if not self._load_column_widths():
            self._set_default_column_widths()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.table = QTableWidget()
        self.table.headers = ["Symbol", "Qty", "Avg", "LTP", "P&L"]
        self.table.setColumnCount(len(self.table.headers))
        self.table.setHorizontalHeaderLabels(self.table.headers)

        self.table.setMouseTracking(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)

        main_layout.addWidget(self.table, 1)

        footer = QWidget()
        footer.setObjectName("footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 5, 10, 5)

        self.total_pnl_label = QLabel("Total P&L: ₹ 0")
        self.total_pnl_label.setObjectName("footerLabel")

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("footerButton")

        footer_layout.addWidget(self.total_pnl_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.refresh_button)

        main_layout.addWidget(footer)

    def _apply_styles(self):
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)

        # IMPORTANT: enable row selection (used for hover)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setCurrentCell(-1, -1)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.SYMBOL_COL, QHeaderView.Stretch)
        for col in (self.QUANTITY_COL, self.AVG_PRICE_COL, self.LTP_COL, self.PNL_COL):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        self.setStyleSheet("""
            QTableWidget {
                background-color: #161A25;
                color: #E0E0E0;
                border: none;
                font-size: 13px;
            }

            QHeaderView::section {
                background-color: #2A3140;
                color: #A9B1C3;
                padding: 8px;
                border: none;
                font-weight: 600;
                font-size: 12px;
            }

            /* MAIN ROW SEPARATOR */
            QTableWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid #1E2430;
            }

            /* ROW HOVER (via selection) */
            /* ROW SELECTION — uniform, no cell emphasis */
QTableWidget::item:selected,
QTableWidget::item:selected:active,
QTableWidget::item:selected:!active {
    background-color: #202742;
    color: #E0E0E0;
}

/* REMOVE current-cell focus rectangle */
QTableWidget::item:selected:!active {
    outline: 0;
}

/* KILL any per-cell hover completely */
QTableWidget::item:hover {
    background-color: transparent;
}


            /* REMOVE CELL HOVER COMPLETELY */
            QTableWidget::item:hover {
                background-color: transparent;
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
            /* ===== CONTEXT MENU ===== */
QMenu {
    background-color: #1B2030;
    border: 1px solid #3A4458;
    border-radius: 6px;
    padding: 6px;
}

QMenu::item {
    padding: 8px 22px 8px 18px;
    color: #E0E0E0;
    font-size: 13px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #2A3350;
}

QMenu::separator {
    height: 1px;
    background: #3A4458;
    margin: 6px 4px;
}

/* Exit action — danger semantics */
QMenu::item#exitAction {
    color: #F85149;
    font-weight: 600;
}

QMenu::item#exitAction:selected {
    background-color: rgba(248, 81, 73, 0.15);
}
        """)

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _connect_signals(self):
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.horizontalHeader().sectionResized.connect(self._on_column_resized)
        self.table.viewport().installEventFilter(self)

    # ------------------------------------------------------------------
    # Row-hover handling (THIS IS THE KEY FIX)
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj == self.table.viewport():
            if event.type() == QEvent.Type.MouseMove:
                row = self.table.rowAt(event.position().toPoint().y())
                if row != self._hovered_row:
                    self._hovered_row = row
                    if row >= 0:
                        self.table.selectRow(row)

            elif event.type() == QEvent.Type.Leave:
                self._hovered_row = -1
                self.table.clearSelection()

        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Context menu (UNCHANGED)
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos: QPoint):
        item = self.table.itemAt(pos)
        if not item:
            return

        row = item.row()

        # ✅ Context menu ONLY for main position rows
        if row not in self.position_row_map.values():
            return

        symbol_item = self.table.item(row, self.SYMBOL_COL)
        if not symbol_item:
            return

        symbol = symbol_item.text().split()[0]
        if symbol not in self.positions:
            return

        pos_data = self.positions[symbol]

        menu = QMenu(self)

        # --- Modify SL / TP ---
        modify_action = menu.addAction("Modify SL / Target")
        modify_action.triggered.connect(
            lambda: self.modify_sl_tp_requested.emit(symbol)
        )

        menu.addSeparator()

        # --- Exit Position (danger action) ---
        exit_action = menu.addAction("Exit Position")
        exit_action.setObjectName("exitAction")
        exit_action.triggered.connect(
            lambda: self.exit_requested.emit(pos_data)
        )

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Data population (UNCHANGED LOGIC)
    # ------------------------------------------------------------------

    def update_positions(self, positions_data: List[dict]):
        self.table.setRowCount(0)
        self.positions.clear()
        self.position_row_map.clear()

        for pos in positions_data:
            self.add_position(pos)

        self._update_footer()

    def add_position(self, pos_data: dict):
        symbol = pos_data['tradingsymbol']
        self.positions[symbol] = pos_data

        main_row = self.table.rowCount()
        self.table.insertRow(main_row)
        self.position_row_map[symbol] = main_row
        self.table.setRowHeight(main_row, 32)

        self._set_symbol_item(main_row, pos_data)
        self._set_item(main_row, self.QUANTITY_COL, pos_data.get('quantity', 0))
        self._set_item(main_row, self.AVG_PRICE_COL, pos_data.get('average_price', 0.0), is_price=True)
        self._set_item(main_row, self.LTP_COL, pos_data.get('last_price', 0.0), is_price=True)
        self._set_pnl_item(main_row, pos_data.get('pnl', 0.0))

        sl = pos_data.get('stop_loss_price')
        tp = pos_data.get('target_price')
        tsl = pos_data.get('trailing_stop_loss')

        if (sl and sl > 0) or (tp and tp > 0) or (tsl and tsl > 0):
            sltp_row = self.table.rowCount()
            self.table.insertRow(sltp_row)
            self.table.setRowHeight(sltp_row, 32)
            self._set_sltp_row(sltp_row, pos_data)

    # ------------------------------------------------------------------
    # Helpers (UNCHANGED)
    # ------------------------------------------------------------------

    def _update_footer(self):
        total_pnl = sum(pos.get('pnl', 0.0) for pos in self.positions.values())
        self.total_pnl_label.setText(f"Total P&L: ₹ {total_pnl:,.0f}")
        color = "#1DE9B6" if total_pnl >= 0 else "#F85149"
        self.total_pnl_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _set_item(self, row, col, data, is_price=False):
        item = QTableWidgetItem()
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        item.setText(f"{data:,.2f}" if is_price else f"{int(data):,}")
        self.table.setItem(row, col, item)

    def _set_symbol_item(self, row, pos_data):
        item = QTableWidgetItem(pos_data.get('tradingsymbol', 'N/A'))
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setItem(row, self.SYMBOL_COL, item)

    def _set_pnl_item(self, row, pnl):
        item = QTableWidgetItem(f"{pnl:,.0f}")
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        item.setForeground(QColor("#1DE9B6") if pnl >= 0 else QColor("#F85149"))
        font = QFont()
        font.setBold(True)
        item.setFont(font)
        self.table.setItem(row, self.PNL_COL, item)

    def _set_sltp_row(self, row, pos_data):
        sl = pos_data.get('stop_loss_price')
        tp = pos_data.get('target_price')
        tsl = pos_data.get('trailing_stop_loss')
        avg = pos_data.get('average_price', 0.0)
        qty = abs(pos_data.get('quantity', 0))

        parts = []

        if sl and sl > 0:
            sl_pnl = abs(avg - sl) * qty
            parts.append(
                f"<span style='color:#F87171;'>SL</span> "
                f"<span style='color:#E5E7EB;'>₹{sl_pnl:,.0f}</span> "
                f"<span style='color:#9CA3AF;'>@ {sl:.2f}</span>"
            )

        if tp and tp > 0:
            tp_pnl = abs(tp - avg) * qty
            parts.append(
                f"<span style='color:#34D399;'>Target</span> "
                f"<span style='color:#E5E7EB;'>₹{tp_pnl:,.0f}</span> "
                f"<span style='color:#9CA3AF;'>@ {tp:.2f}</span>"
            )

        if tsl and tsl > 0:
            parts.append(
                f"<span style='color:#60A5FA;'>TSL</span> "
                f"<span style='color:#E5E7EB;'>{tsl:.0f}</span>"
            )

        # ---- QLabel ----
        label = QLabel("  •  ".join(parts))
        label.setTextFormat(Qt.RichText)
        label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        label.setStyleSheet("""
            QLabel {
                font-family: Segoe UI;
                font-size: 12px;
                font-weight: 500;
                color: #9CA3AF;
            }
        """)

        # ---- Wrapper widget (THIS IS THE KEY) ----
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 2, 6, 0)  # top aligned visually
        layout.addWidget(label)
        layout.setAlignment(Qt.AlignTop | Qt.AlignRight)

        self.table.setCellWidget(row, self.SYMBOL_COL, container)
        self.table.setSpan(row, self.SYMBOL_COL, 1, self.table.columnCount())

    # ------------------------------------------------------------------
    # Column persistence (UNCHANGED)
    # ------------------------------------------------------------------

    def _load_column_widths(self):
        try:
            path = os.path.expanduser("~/.options_scalper/positions_table_columns.json")
            if not os.path.exists(path):
                return False
            with open(path, "r") as f:
                widths = json.load(f)
            for name, w in widths.items():
                if name in self.table.headers:
                    self.table.setColumnWidth(self.table.headers.index(name), int(w))
            return True
        except Exception:
            return False

    def _set_default_column_widths(self):
        self.table.setColumnWidth(self.PNL_COL, 100)

    def _on_column_resized(self, *_):
        if not hasattr(self, "_save_timer"):
            self._save_timer = QTimer(self)
            self._save_timer.setSingleShot(True)
            self._save_timer.timeout.connect(self._save_column_widths)
        self._save_timer.start(500)

    def _save_column_widths(self):
        try:
            path = os.path.expanduser("~/.options_scalper/positions_table_columns.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {h: self.table.columnWidth(i) for i, h in enumerate(self.table.headers)}
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_column_widths()
        super().closeEvent(event)

    def save_state(self):
        self._save_column_widths()
