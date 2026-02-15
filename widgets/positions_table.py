import logging
import json
import os
from typing import Dict, List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QApplication,
    QMenu, QAbstractItemView, QDialog, QFormLayout, QDoubleSpinBox, QPushButton, QInputDialog
)
from PySide6.QtCore import Qt, Signal, QPoint, QTimer, QEvent
from PySide6.QtGui import QColor, QFont, QFontMetrics

from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class PositionsTable(QWidget):
    """
    A compound widget containing a compact, data-dense positions table with two-row display
    """

    exit_requested = Signal(dict)
    refresh_requested = Signal()
    modify_sl_tp_requested = Signal(str)
    portfolio_sl_tp_requested = Signal(float, float)
    portfolio_sl_tp_cleared = Signal()

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
        self.group_row_map: Dict[str, int] = {}
        self.group_members: Dict[str, List[str]] = {}
        self.group_order: List[str] = []
        self.group_sl_tp: Dict[str, Dict[str, float]] = {}

        self._hovered_row = -1

        self._init_ui()
        self._apply_styles()
        self._connect_signals()

        if not self._load_column_widths():
            self._set_default_column_widths()

        self._portfolio_sl = None
        self._portfolio_tp = None
        self._drag_active = False
        self.visual_order: List[str] = []
        self._load_table_state()

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
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropMode(QAbstractItemView.InternalMove)

        self.table.setMouseTracking(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)

        main_layout.addWidget(self.table, 1)

        # 🔥 REDESIGNED FOOTER
        self.footer = QWidget()
        self.footer.setFixedHeight(28)
        self.footer.setObjectName("footer")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(8, 0, 8, 0)
        footer_layout.setSpacing(8)
        self.footer.setContextMenuPolicy(Qt.CustomContextMenu)
        self.footer.customContextMenuRequested.connect(
            self._show_footer_context_menu
        )

        # --- LEFT: Refresh button ---
        self.refresh_button = QPushButton("⟳")
        self.refresh_button.setObjectName("footerIconButton")
        self.refresh_button.setFixedSize(20, 20)
        self.refresh_button.setToolTip("Refresh Positions")

        # --- CENTER: Portfolio SL/TP ---
        self.portfolio_sl_tp_label = QLabel("SL/TP: —")
        self.portfolio_sl_tp_label.setObjectName("portfolioSLTPLabel")
        self.portfolio_sl_tp_label.setAlignment(Qt.AlignCenter)

        # --- RIGHT: Total P&L ---
        pnl_container = QWidget()
        pnl_layout = QHBoxLayout(pnl_container)
        pnl_layout.setContentsMargins(0, 0, 0, 0)
        pnl_layout.setSpacing(6)

        self.total_pnl_title = QLabel("Total P&L")
        self.total_pnl_title.setObjectName("footerTitleLabel")

        self.total_pnl_value = QLabel("₹ 0")
        self.total_pnl_value.setObjectName("footerValueLabel")
        self.total_pnl_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Reserve width for larger numbers
        fm = QFontMetrics(self.total_pnl_value.font())
        reserved_width = fm.horizontalAdvance("₹ -9,99,999")
        self.total_pnl_value.setFixedWidth(reserved_width + 8)

        pnl_layout.addWidget(self.total_pnl_title)
        pnl_layout.addWidget(self.total_pnl_value)

        # --- ASSEMBLE ---
        footer_layout.addWidget(self.refresh_button)
        footer_layout.addWidget(self._footer_separator())
        footer_layout.addWidget(self.portfolio_sl_tp_label, 1)  # takes available space
        footer_layout.addWidget(self._footer_separator())
        footer_layout.addWidget(pnl_container)

        main_layout.addWidget(self.footer)

    def _footer_separator(self):
        """Visual separator line."""
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: #3A4458;")
        return sep

    def _update_footer(self):
        total_pnl = sum(pos.get('pnl', 0.0) for pos in self.positions.values())

        # Format with proper Indian notation
        sign = "" if total_pnl >= 0 else "-"
        formatted = f"₹ {sign}{abs(total_pnl):,.0f}"
        self.total_pnl_value.setText(formatted)

        color = "#1DE9B6" if total_pnl >= 0 else "#F85149"
        self.total_pnl_value.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 13px;"
        )
    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _connect_signals(self):
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.horizontalHeader().sectionResized.connect(self._on_column_resized)
        self.table.viewport().installEventFilter(self)
        self.table.itemPressed.connect(self._on_item_pressed)

    # ------------------------------------------------------------------
    # Row-hover handling (THIS IS THE KEY FIX)
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj == self.table.viewport():

            if event.type() == QEvent.Type.Drop:
                self._handle_drop(event)
                return True

            if event.type() == QEvent.Type.MouseMove:
                if self._drag_active or event.buttons() != Qt.MouseButton.NoButton:
                    return False

                row = self.table.rowAt(event.position().toPoint().y())
                if row < 0:
                    return False

                row_kind = self._row_kind(row)
                if row_kind in {"SLTP", "GROUP_SLTP", "DIVIDER"}:
                    return False

                if row != self._hovered_row:
                    self._hovered_row = row
                    self.table.setCurrentCell(row, self.SYMBOL_COL)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                # A simple click should not keep the table in drag mode;
                # reset so hover highlighting can continue to update.
                self._drag_active = False

                # Prevent persistent click-selection; this table uses hover-style highlighting.
                self.table.clearSelection()
                if self._hovered_row >= 0 and not self._is_sltp_row(self._hovered_row):
                    self.table.setCurrentCell(self._hovered_row, self.SYMBOL_COL)
                else:
                    self.table.setCurrentCell(-1, -1)

            elif event.type() == QEvent.Type.MouseButtonRelease:
                # A simple click should not keep the table in drag mode;
                # reset so hover highlighting can continue to update.
                self._drag_active = False

            elif event.type() == QEvent.Type.Leave:
                # Explicitly clear visual hover state when mouse exits viewport.
                self._hovered_row = -1
                if not self._drag_active:
                    self.table.clearSelection()
                    self.table.setCurrentCell(-1, -1)

            elif event.type() == QEvent.Type.DragLeave:
                self._hovered_row = -1
                self._drag_active = False
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)

        return super().eventFilter(obj, event)
    # ------------------------------------------------------------------
    # Context menu (UNCHANGED)
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos: QPoint):
        item = self.table.itemAt(pos)
        if not item:
            return

        row = item.row()
        row_kind = self._row_kind(row)
        if row_kind == "GROUP_SLTP":
            row_kind = "GROUP"
        if row_kind == "SLTP":
            row_kind = "POSITION"
        selected_symbols = self._selected_position_symbols()

        if not row_kind:
            return

        menu = QMenu(self)

        if selected_symbols:
            create_group_action = menu.addAction("Create Group from Selected")
            create_group_action.triggered.connect(
                lambda: self._create_group_from_selection(selected_symbols)
            )

            if self.group_order:
                add_to_group = menu.addMenu("Add Selected to Group")
                for group_name in self.group_order:
                    action = add_to_group.addAction(group_name)
                    action.triggered.connect(
                        lambda _, g=group_name: self._add_symbols_to_group(selected_symbols, g)
                    )
            menu.addSeparator()

        if row_kind == "GROUP":
            group_name = self._row_group(row)
            if not group_name:
                return
            alter = group_name in self.group_sl_tp
            set_text = "Alter Group SL / Target" if alter else "Set Group SL / Target"
            set_action = menu.addAction(set_text)
            set_action.triggered.connect(
                lambda: self._open_group_sl_tp_dialog(group_name, alter=alter)
            )
            clear_action = menu.addAction("Clear Group SL / Target")
            clear_action.setEnabled(group_name in self.group_sl_tp)
            clear_action.triggered.connect(lambda: self._clear_group_sl_tp(group_name))

            menu.addSeparator()
            ungroup_action = menu.addAction("Ungroup Positions")
            ungroup_action.triggered.connect(lambda: self._ungroup_group(group_name))

        if row_kind == "POSITION":
            symbol = self._row_symbol(row)
            if not symbol or symbol not in self.positions:
                return

            pos_data = self.positions[symbol]

            modify_action = menu.addAction("Modify SL / Target")
            modify_action.triggered.connect(
                lambda: self.modify_sl_tp_requested.emit(symbol)
            )

            menu.addSeparator()

            exit_action = menu.addAction("Exit Position")
            exit_action.setObjectName("exitAction")
            exit_action.triggered.connect(
                lambda: self.exit_requested.emit(pos_data)
            )

            group_name = self._symbol_group(symbol)
            if group_name:
                menu.addSeparator()
                remove_group_action = menu.addAction("Remove from Group")
                remove_group_action.triggered.connect(
                    lambda: self._remove_symbol_from_group(symbol, group_name)
                )

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _show_footer_context_menu(self, pos: QPoint):
        menu = QMenu(self)

        is_set = self._is_portfolio_sl_tp_set()

        set_or_alter_text = (
            "Alter Portfolio SL / Target"
            if is_set
            else "Set Portfolio SL / Target"
        )

        set_sl_tp = menu.addAction(set_or_alter_text)
        clear_sl_tp = menu.addAction("Clear Portfolio SL / Target")

        # 🔒 HARD GUARD: no positions → cannot set/alter
        if not self._has_open_positions():
            set_sl_tp.setEnabled(False)
            set_sl_tp.setText(f"{set_or_alter_text} (No positions)")

        action = menu.exec(self.footer.mapToGlobal(pos))

        if action == set_sl_tp and self._has_open_positions():
            self._open_portfolio_sl_tp_dialog(alter=is_set)

        elif action == clear_sl_tp:
            self._clear_portfolio_sl_tp()
            self.portfolio_sl_tp_cleared.emit()

    def _clear_portfolio_sl_tp(self):
        self._portfolio_sl = None
        self._portfolio_tp = None
        self._update_portfolio_sl_tp_label()

    def _update_portfolio_sl_tp_label(self):
        if not self._has_open_positions() or (
                self._portfolio_sl is None and self._portfolio_tp is None
        ):
            self.portfolio_sl_tp_label.setText("SL/TP: —")
            self.portfolio_sl_tp_label.setStyleSheet("color: #A9B1C3;")
            return

        parts = []
        if self._portfolio_sl is not None:
            parts.append(f"SL ₹{abs(self._portfolio_sl):,.0f}")
        if self._portfolio_tp is not None:
            parts.append(f"TP ₹{self._portfolio_tp:,.0f}")

        text = "  •  ".join(parts)
        self.portfolio_sl_tp_label.setText(f"{text}")

        # Risk-aware coloring
        self.portfolio_sl_tp_label.setStyleSheet(
            "color: #FBBF24; font-weight: 600;"  # amber = armed
        )

    # ------------------------------------------------------------------
    # Data population (UNCHANGED LOGIC)
    # ------------------------------------------------------------------

    def update_positions(self, positions_data: List[dict]):
        self.positions = {p['tradingsymbol']: p for p in positions_data}
        live_symbols = set(self.positions.keys())

        self._sync_group_memberships(positions_data, live_symbols)

        if not self.visual_order:
            self.visual_order = [s for s in self.positions.keys() if not self._symbol_group(s)]
        else:
            self.visual_order = [s for s in self.visual_order if s in live_symbols and not self._symbol_group(s)]
            for s in self.positions.keys():
                if s not in self.visual_order and not self._symbol_group(s):
                    self.visual_order.append(s)

        self._prune_empty_groups()
        self._rebuild_table_from_order()

    def _rebuild_table_from_order(self):
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0)
        self.position_row_map.clear()
        self.group_row_map.clear()

        rendered_groups = [g for g in self.group_order if self.group_members.get(g)]

        for index, group_name in enumerate(rendered_groups):
            members = self.group_members.get(group_name, [])
            self._add_group_rows(group_name, members)

            if index < len(rendered_groups) - 1 or self.visual_order:
                self._insert_group_divider_row()

        for symbol in self.visual_order:
            if symbol in self.positions:
                self._add_position_rows(self.positions[symbol])

        self._update_footer()
        self.table.setUpdatesEnabled(True)

    def _add_position_rows(self, pos_data: dict):
        symbol = pos_data['tradingsymbol']
        self.positions[symbol] = pos_data

        main_row = self.table.rowCount()
        self.table.insertRow(main_row)
        self.position_row_map[symbol] = main_row
        self.table.setRowHeight(main_row, 30)
        self.table.setProperty(f"row_pid_{main_row}", symbol)
        self.table.setProperty(f"row_role_{main_row}", "MAIN")
        self.table.setProperty(f"row_kind_{main_row}", "POSITION")
        self.table.setProperty(f"row_group_{main_row}", self._symbol_group(symbol))

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
            self.table.setRowHeight(sltp_row, 24)
            self.table.setProperty(f"row_type_{sltp_row}", "SLTP")
            self._set_sltp_row(sltp_row, pos_data)
            self.table.setProperty(f"row_pid_{sltp_row}", symbol)
            self.table.setProperty(f"row_role_{sltp_row}", "SLTP")
            self.table.setProperty(f"row_kind_{sltp_row}", "SLTP")

    def _insert_group_divider_row(self):
        """Insert a bright thin row to clearly mark the end of a group."""
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 3)
        self.table.setProperty(f"row_kind_{row}", "DIVIDER")

        divider_item = QTableWidgetItem("")
        divider_item.setFlags(Qt.ItemFlag.NoItemFlags)
        divider_item.setBackground(QColor("#2A3350"))
        self.table.setItem(row, 0, divider_item)
        self.table.setSpan(row, 0, 1, self.table.columnCount())

    def _add_group_rows(self, group_name: str, members: List[str]):
        group_row = self.table.rowCount()
        self.table.insertRow(group_row)
        self.table.setRowHeight(group_row, 30)
        self.group_row_map[group_name] = group_row
        self.table.setProperty(f"row_kind_{group_row}", "GROUP")
        self.table.setProperty(f"row_group_{group_row}", group_name)

        group_pnl = sum(self.positions.get(symbol, {}).get('pnl', 0.0) for symbol in members)
        self._set_group_header_items(group_row, group_name, group_pnl)

        if group_name in self.group_sl_tp:
            sltp_row = self.table.rowCount()
            self.table.insertRow(sltp_row)
            self.table.setRowHeight(sltp_row, 30)
            self.table.setProperty(f"row_kind_{sltp_row}", "GROUP_SLTP")
            self.table.setProperty(f"row_group_{sltp_row}", group_name)
            self._set_group_sltp_row(sltp_row, self.group_sl_tp[group_name])

        for symbol in members:
            pos = self.positions.get(symbol)
            if not pos:
                continue
            self._add_position_rows(pos)

    # ------------------------------------------------------------------
    # Helpers (UNCHANGED)
    # ------------------------------------------------------------------

    def _update_footer(self):
        total_pnl = sum(pos.get('pnl', 0.0) for pos in self.positions.values())
        sign = "" if total_pnl >= 0 else "-"
        formatted = f"₹ {sign}{abs(total_pnl):,.0f}"
        self.total_pnl_value.setText(formatted)

        color = "#1DE9B6" if total_pnl >= 0 else "#F85149"
        self.total_pnl_value.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 13px;"
        )

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
                f"<span style='color:#F87171;'>Stop Loss</span> "
                f"<span style='color:#E5E7EB;'>₹{sl_pnl:,.0f}</span> "
                f"<span style='color:#9CA3AF;'>@ {sl:.2f}</span>"
            )

        if tp and tp > 0:
            tp_pnl = abs(tp - avg) * qty
            parts.append(
                f"<span style='color:#34D399;'>Take Profit</span> "
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
                font-size: 11px;
                font-weight: 500;
                color: #9CA3AF;
            }
        """)

        # ---- Wrapper widget (THIS IS THE KEY) ----
        container = QWidget()
        layout = QVBoxLayout(container)
        # Keep SL/TP text clear of row clipping with near-zero cell spacing.
        layout.setContentsMargins(0, 0, 2, 0)
        layout.setSpacing(0)
        layout.addWidget(label)
        layout.setAlignment(Qt.AlignTop | Qt.AlignRight)

        self.table.setCellWidget(row, self.SYMBOL_COL, container)
        self.table.setSpan(row, self.SYMBOL_COL, 1, self.table.columnCount())

    def _set_group_header_items(self, row: int, group_name: str, group_pnl: float):
        label = f"📦 {group_name}"
        symbol_item = QTableWidgetItem(label)
        symbol_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        symbol_font = QFont()
        symbol_font.setBold(True)
        symbol_item.setFont(symbol_font)
        symbol_item.setForeground(QColor("#E5E7EB"))
        symbol_item.setBackground(QColor("#0E2533"))
        self.table.setItem(row, self.SYMBOL_COL, symbol_item)

        for col in (self.QUANTITY_COL, self.AVG_PRICE_COL, self.LTP_COL):
            placeholder = QTableWidgetItem("")
            placeholder.setFlags(placeholder.flags() ^ Qt.ItemIsEditable)
            placeholder.setBackground(QColor("#0E2533"))
            self.table.setItem(row, col, placeholder)

        pnl_item = QTableWidgetItem(f"{group_pnl:,.0f}")
        pnl_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pnl_item.setForeground(QColor("#1DE9B6") if group_pnl >= 0 else QColor("#F85149"))
        pnl_font = QFont()
        pnl_font.setBold(True)
        pnl_item.setFont(pnl_font)
        pnl_item.setBackground(QColor("#0E2533"))
        self.table.setItem(row, self.PNL_COL, pnl_item)

    def _set_group_sltp_row(self, row: int, sltp_data: Dict[str, float]):
        sl = sltp_data.get("sl")
        tp = sltp_data.get("tp")

        parts = []
        if sl is not None:
            parts.append(
                f"<span style='color:#F87171;'>Group SL</span> "
                f"<span style='color:#E5E7EB;'>₹{abs(sl):,.0f}</span>"
            )
        if tp is not None:
            parts.append(
                f"<span style='color:#34D399;'>Group TP</span> "
                f"<span style='color:#E5E7EB;'>₹{tp:,.0f}</span>"
            )

        label = QLabel("  •  ".join(parts) if parts else "Group SL/TP: —")
        label.setTextFormat(Qt.RichText)
        label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        label.setStyleSheet("""
            QLabel {
                font-family: Segoe UI;
                font-size: 11px;
                font-weight: 500;
                color: #9CA3AF;
            }
        """)

        container = QWidget()
        layout = QVBoxLayout(container)
        # Keep SL/TP text clear of row clipping with near-zero cell spacing.
        layout.setContentsMargins(0, 0, 2, 0)
        layout.setSpacing(0)
        layout.addWidget(label)
        layout.setAlignment(Qt.AlignTop | Qt.AlignRight)

        self.table.setCellWidget(row, self.SYMBOL_COL, container)
        self.table.setSpan(row, self.SYMBOL_COL, 1, self.table.columnCount())

    def _row_kind(self, row: int) -> Optional[str]:
        return self.table.property(f"row_kind_{row}")

    def _row_group(self, row: int) -> Optional[str]:
        return self.table.property(f"row_group_{row}")

    def _row_symbol(self, row: int) -> Optional[str]:
        return self.table.property(f"row_pid_{row}")

    def _selected_position_symbols(self) -> List[str]:
        selected_symbols = []
        for selection_range in self.table.selectedRanges():
            for row in range(selection_range.topRow(), selection_range.bottomRow() + 1):
                if self._row_kind(row) != "POSITION":
                    continue
                symbol = self._row_symbol(row)
                if symbol and symbol not in selected_symbols:
                    selected_symbols.append(symbol)
        return selected_symbols

    def _create_group_from_selection(self, symbols: List[str]):
        if not symbols:
            return
        group_name, ok = QInputDialog.getText(self, "Create Group", "Group name:")
        if not ok or not group_name.strip():
            return
        group_name = group_name.strip()
        self._add_symbols_to_group(symbols, group_name)

    def _add_symbols_to_group(self, symbols: List[str], group_name: str):
        for symbol in symbols:
            self._assign_symbol_to_group(symbol, group_name, append=True)
        self._rebuild_table_from_order()
        self._save_table_state()

    def _remove_symbol_from_group(self, symbol: str, group_name: str):
        if group_name not in self.group_members:
            return
        self.group_members[group_name] = [s for s in self.group_members[group_name] if s != symbol]
        if symbol not in self.visual_order:
            self.visual_order.append(symbol)
        if not self.group_members[group_name]:
            self.group_members.pop(group_name, None)
            self.group_sl_tp.pop(group_name, None)
            self.group_order = [g for g in self.group_order if g != group_name]
        self._rebuild_table_from_order()
        self._save_table_state()

    def _ungroup_group(self, group_name: str):
        members = self.group_members.pop(group_name, [])
        self.group_sl_tp.pop(group_name, None)
        self.group_order = [g for g in self.group_order if g != group_name]
        for symbol in members:
            if symbol not in self.visual_order:
                self.visual_order.append(symbol)
        self._rebuild_table_from_order()
        self._save_table_state()

    def _open_group_sl_tp_dialog(self, group_name: str, alter: bool = False):
        if not group_name:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Alter Group Risk" if alter else "Set Group Risk")
        dialog.setFixedWidth(360)

        existing = self.group_sl_tp.get(group_name, {})
        default_sl = abs(existing.get("sl")) if alter and existing.get("sl") is not None else 0
        default_tp = abs(existing.get("tp")) if alter and existing.get("tp") is not None else 0

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(14)

        title = QLabel(f"{group_name} SL / TP")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        main_layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        sl_spin = QDoubleSpinBox()
        sl_spin.setRange(0, 1_000_000)
        sl_spin.setDecimals(0)
        sl_spin.setSingleStep(500)
        sl_spin.setValue(default_sl)
        sl_spin.setPrefix("₹ ")

        tp_spin = QDoubleSpinBox()
        tp_spin.setRange(0, 5_000_000)
        tp_spin.setDecimals(0)
        tp_spin.setSingleStep(500)
        tp_spin.setValue(default_tp)
        tp_spin.setPrefix("₹ ")

        form.addRow("Stop Loss (₹)", sl_spin)
        form.addRow("Target (₹)", tp_spin)

        main_layout.addLayout(form)

        btn = QPushButton("SAVE GROUP SL / TP")
        btn.setFixedHeight(34)
        btn.clicked.connect(
            lambda: (
                self._set_group_sl_tp(group_name, sl_spin.value(), tp_spin.value()),
                dialog.accept()
            )
        )

        main_layout.addWidget(btn)
        self._position_dialog_above_footer(dialog)
        dialog.exec()

    def _set_group_sl_tp(self, group_name: str, sl: float, tp: float):
        sl_value = -abs(sl) if sl > 0 else None
        tp_value = abs(tp) if tp > 0 else None
        if sl_value is None and tp_value is None:
            self.group_sl_tp.pop(group_name, None)
        else:
            self.group_sl_tp[group_name] = {"sl": sl_value, "tp": tp_value}
        self._rebuild_table_from_order()
        self._save_table_state()

    def _clear_group_sl_tp(self, group_name: str):
        if group_name in self.group_sl_tp:
            self.group_sl_tp.pop(group_name, None)
            self._rebuild_table_from_order()
            self._save_table_state()

    def _symbol_group(self, symbol: str) -> Optional[str]:
        for group_name, members in self.group_members.items():
            if symbol in members:
                return group_name
        return None

    def _sync_group_memberships(self, positions_data: List[dict], live_symbols: set):
        for group_name, members in list(self.group_members.items()):
            self.group_members[group_name] = [s for s in members if s in live_symbols]
            if not self.group_members[group_name]:
                self.group_members.pop(group_name, None)
                self.group_sl_tp.pop(group_name, None)

        self.group_order = [g for g in self.group_order if g in self.group_members]

        for pos in positions_data:
            symbol = pos.get('tradingsymbol')
            group_name = pos.get('group_name')
            if not symbol or not group_name:
                continue
            self._assign_symbol_to_group(symbol, group_name, append=True)

        self._prune_empty_groups()

    def _assign_symbol_to_group(self, symbol: str, group_name: str, append: bool = True):
        current_group = self._symbol_group(symbol)
        if current_group == group_name:
            return

        if current_group and symbol in self.group_members.get(current_group, []):
            self.group_members[current_group] = [s for s in self.group_members[current_group] if s != symbol]
            if not self.group_members[current_group]:
                self.group_members.pop(current_group, None)
                self.group_sl_tp.pop(current_group, None)
                self.group_order = [g for g in self.group_order if g != current_group]

        if group_name not in self.group_members:
            self.group_members[group_name] = []
            self.group_order.append(group_name)

        if symbol in self.visual_order:
            self.visual_order.remove(symbol)

        if symbol in self.group_members[group_name]:
            self.group_members[group_name].remove(symbol)

        if append:
            self.group_members[group_name].append(symbol)
        else:
            self.group_members[group_name].insert(0, symbol)

    def _prune_empty_groups(self):
        for group_name in list(self.group_members.keys()):
            if not self.group_members[group_name]:
                self.group_members.pop(group_name, None)
                self.group_sl_tp.pop(group_name, None)
        self.group_order = [g for g in self.group_order if g in self.group_members]

    # ------------------------------------------------------------------
    # Column persistence (UNCHANGED)
    # ------------------------------------------------------------------
    def _load_table_state(self):
        try:
            path = os.path.expanduser("~/.options_scalper/positions_table_order.json")
            if not os.path.exists(path):
                return

            with open(path, "r") as f:
                data = json.load(f)

            if isinstance(data, list):
                self.visual_order = data
                return

            if isinstance(data, dict):
                self.visual_order = data.get("ungrouped", [])
                self.group_members = data.get("groups", {})
                self.group_order = data.get("group_order", list(self.group_members.keys()))
                self.group_sl_tp = data.get("group_sl_tp", {})
        except Exception as e:
            logger.warning(f"Failed to load position order: {e}")

    def _save_table_state(self):
        try:
            path = os.path.expanduser("~/.options_scalper/positions_table_order.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)

            payload = {
                "ungrouped": self.visual_order,
                "groups": self.group_members,
                "group_order": self.group_order,
                "group_sl_tp": self.group_sl_tp,
            }

            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save position order: {e}")

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

    def _open_portfolio_sl_tp_dialog(self, alter: bool = False):
        if not self._has_open_positions():
            return

        position_count = len(self.positions)

        RISK_PER_POSITION = 1000
        DEFAULT_RR = 1.5

        if alter and self._portfolio_sl and self._portfolio_tp:
            # Prefill from existing values
            default_sl = abs(self._portfolio_sl)
            default_tp = abs(self._portfolio_tp)
            default_rr = round(default_tp / default_sl, 2) if default_sl else DEFAULT_RR
        else:
            # Fresh defaults
            default_sl = position_count * RISK_PER_POSITION
            default_rr = DEFAULT_RR
            default_tp = int(default_sl * default_rr)

        dialog = QDialog(self)
        dialog.setWindowTitle("Alter Portfolio Risk" if alter else "Set Portfolio Risk")
        dialog.setFixedWidth(360)

        self._updating = False  # guard against signal loops

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(14)

        # ---- Header ----
        title = QLabel("Portfolio Stop Loss & Target")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        subtitle = QLabel(f"{position_count} open positions")
        subtitle.setStyleSheet("font-size: 11px; color: #9CA3AF;")

        main_layout.addWidget(title)
        main_layout.addWidget(subtitle)

        # ---- Form ----
        form = QFormLayout()
        form.setSpacing(10)

        # Stop Loss (ANCHOR)
        sl_spin = QDoubleSpinBox()
        sl_spin.setRange(0, 1_000_000)
        sl_spin.setDecimals(0)
        sl_spin.setSingleStep(500)
        sl_spin.setValue(default_sl)
        sl_spin.setPrefix("₹ ")

        # Risk Reward
        rr_spin = QDoubleSpinBox()
        rr_spin.setRange(0.5, 5.0)
        rr_spin.setDecimals(2)
        rr_spin.setSingleStep(0.1)
        rr_spin.setValue(default_rr)

        # Target
        tp_spin = QDoubleSpinBox()
        tp_spin.setRange(0, 5_000_000)
        tp_spin.setDecimals(0)
        tp_spin.setSingleStep(500)
        tp_spin.setValue(default_tp)
        tp_spin.setPrefix("₹ ")

        form.addRow("Stop Loss (₹)", sl_spin)
        form.addRow("Risk–Reward", rr_spin)
        form.addRow("Target (₹)", tp_spin)

        main_layout.addLayout(form)

        # ---- Reactive logic ----
        def on_sl_changed(value):
            if self._updating:
                return
            self._updating = True
            tp_spin.setValue(int(value * rr_spin.value()))
            self._updating = False

        def on_rr_changed(value):
            if self._updating:
                return
            self._updating = True
            tp_spin.setValue(int(sl_spin.value() * value))
            self._updating = False

        def on_tp_changed(value):
            if self._updating or sl_spin.value() == 0:
                return
            self._updating = True
            rr_spin.setValue(round(value / sl_spin.value(), 2))
            self._updating = False

        sl_spin.valueChanged.connect(on_sl_changed)
        rr_spin.valueChanged.connect(on_rr_changed)
        tp_spin.valueChanged.connect(on_tp_changed)

        # ---- Action button ----
        btn = QPushButton("ARM PORTFOLIO SL / TP")
        btn.setFixedHeight(34)
        btn.clicked.connect(
            lambda: (
                self._set_portfolio_sl_tp(
                    sl_spin.value(),
                    tp_spin.value()
                ),
                self.portfolio_sl_tp_requested.emit(
                    -sl_spin.value(),
                    tp_spin.value()
                ),
                dialog.accept()
            )
        )

        main_layout.addWidget(btn)
        self._position_dialog_above_footer(dialog)
        dialog.exec()

    def _set_portfolio_sl_tp(self, sl: float, tp: float):
        # User provides positive numbers
        self._portfolio_sl = -abs(sl) if sl > 0 else None
        self._portfolio_tp = abs(tp) if tp > 0 else None
        self._update_portfolio_sl_tp_label()

    def _is_portfolio_sl_tp_set(self) -> bool:
        return self._portfolio_sl is not None or self._portfolio_tp is not None

    def _has_open_positions(self) -> bool:
        return bool(self.positions)

    def _position_dialog_above_footer(self, dialog: QDialog):
        dialog.adjustSize()
        dialog_size = dialog.sizeHint()

        # Footer position in global coordinates
        footer_global = self.footer.mapToGlobal(QPoint(0, 0))

        # Default: align dialog left with footer
        x = footer_global.x()
        y = footer_global.y() - dialog_size.height() - 40  # small gap above footer

        # Screen bounds safety
        screen = QApplication.primaryScreen().availableGeometry()

        # Clamp horizontally
        if x + dialog_size.width() > screen.right():
            x = screen.right() - dialog_size.width() - 6
        if x < screen.left():
            x = screen.left() + 20

        # Clamp vertically (if footer is too low)
        if y < screen.top():
            y = footer_global.y() + self.footer.height() + 6  # open below footer instead

        dialog.move(x, y)

    def _is_main_position_row(self, row: int) -> bool:
        return self._row_kind(row) == "POSITION"

    def _is_sltp_row(self, row):
        return self._row_kind(row) in {"SLTP", "GROUP_SLTP"}


    def _on_item_pressed(self, item: QTableWidgetItem):
        row = item.row()
        row_kind = self._row_kind(row)

        # Prevent dragging non-draggable structural rows.
        if row_kind in {"SLTP", "GROUP_SLTP", "DIVIDER"}:
            self._drag_active = False
            return

        self._drag_active = True

    def _handle_drop(self, event):
        self._drag_active = False

        source_row = self.table.currentRow()
        if source_row < 0:
            self.table.setCurrentCell(-1, -1)  # 🔥 Clear here
            return

        source_kind = self._row_kind(source_row)
        if source_kind in {None, "SLTP", "GROUP_SLTP"}:
            self.table.setCurrentCell(-1, -1)
            return

        pos = event.position().toPoint()
        target_row = self.table.rowAt(pos.y())
        if target_row < 0:
            self.table.setCurrentCell(-1, -1)  # 🔥 Clear here
            return

        target_kind = self._row_kind(target_row)
        if target_kind in {None, "SLTP", "GROUP_SLTP"}:
            self.table.setCurrentCell(-1, -1)
            return

        if source_kind == "GROUP":
            source_group = self._row_group(source_row)
            if target_kind == "GROUP":
                target_group = self._row_group(target_row)
            elif target_kind == "POSITION":
                target_symbol = self._row_symbol(target_row)
                target_group = self._symbol_group(target_symbol) if target_symbol else None
            else:
                target_group = None

            if not source_group or not target_group or source_group == target_group:
                self.table.setCurrentCell(-1, -1)
                return

            if source_group not in self.group_order or target_group not in self.group_order:
                self.table.setCurrentCell(-1, -1)
                return

            src_idx = self.group_order.index(source_group)
            tgt_idx = self.group_order.index(target_group)
            self.group_order.remove(source_group)
            if src_idx < tgt_idx:
                insert_at = self.group_order.index(target_group) + 1
            else:
                insert_at = self.group_order.index(target_group)
            self.group_order.insert(insert_at, source_group)

        if source_kind == "POSITION":
            symbol = self._row_symbol(source_row)
            if not symbol:
                self.table.setCurrentCell(-1, -1)
                return

            source_group = self._symbol_group(symbol)

            if target_kind == "GROUP":
                target_group = self._row_group(target_row)
                if not target_group:
                    self.table.setCurrentCell(-1, -1)
                    return
                if source_group != target_group:
                    self._assign_symbol_to_group(symbol, target_group, append=True)
                else:
                    if symbol in self.group_members.get(target_group, []):
                        self.group_members[target_group].remove(symbol)
                        self.group_members[target_group].append(symbol)

            elif target_kind == "POSITION":
                target_symbol = self._row_symbol(target_row)
                if not target_symbol or target_symbol == symbol:
                    self.table.setCurrentCell(-1, -1)
                    return
                target_group = self._symbol_group(target_symbol)

                if source_group == target_group:
                    order_list = self.group_members.get(source_group, []) if source_group else self.visual_order
                    if symbol not in order_list or target_symbol not in order_list:
                        self.table.setCurrentCell(-1, -1)
                        return
                    src_idx = order_list.index(symbol)
                    tgt_idx = order_list.index(target_symbol)
                    order_list.remove(symbol)
                    if src_idx < tgt_idx:
                        insert_at = order_list.index(target_symbol) + 1
                    else:
                        insert_at = order_list.index(target_symbol)
                    order_list.insert(insert_at, symbol)
                else:
                    if source_group:
                        self.group_members[source_group] = [
                            s for s in self.group_members.get(source_group, []) if s != symbol
                        ]
                    else:
                        if symbol in self.visual_order:
                            self.visual_order.remove(symbol)
                    if target_group:
                        if target_group not in self.group_members:
                            self.group_members[target_group] = []
                            self.group_order.append(target_group)
                        target_list = self.group_members[target_group]
                        insert_at = target_list.index(target_symbol) if target_symbol in target_list else len(target_list)
                        target_list.insert(insert_at, symbol)
                    else:
                        insert_at = self.visual_order.index(target_symbol) if target_symbol in self.visual_order else len(self.visual_order)
                        self.visual_order.insert(insert_at, symbol)

        self._rebuild_table_from_order()
        self._save_table_state()

        self.table.setCurrentCell(-1, -1)  # 🔥 Clear selection after successful drop

    def save_state(self):
        self._save_column_widths()

    def closeEvent(self, event):
        self._save_column_widths()
        self._save_table_state()
        super().closeEvent(event)

    def _apply_styles(self):
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.setTabKeyNavigation(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)

        # IMPORTANT: enable row selection (used for hover)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setCurrentCell(-1, -1)

        header = self.table.horizontalHeader()
        header.setFixedHeight(30)
        header.setMinimumHeight(30)
        header.setMaximumHeight(30)

        self.table.setStyleSheet("""
        QHeaderView::section {
            padding: 4px 8px;
            height: 28px;
            font-size: 12px;
            font-weight: 600;
        }
        """)
        header.setSectionResizeMode(self.SYMBOL_COL, QHeaderView.Stretch)
        for col in (self.QUANTITY_COL, self.AVG_PRICE_COL, self.LTP_COL, self.PNL_COL):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        self.setStyleSheet("""
            QTableWidget {
                background-image: url("assets/textures/main_window_bg.png");
                color: #E0E0E0;
                border: none;
                font-size: 13px;
                selection-background-color: #184540;
            }

            QHeaderView::section {
                background: #041D27;               
                color: #A9B1C3;
                padding: 8px;
                border: none;
                font-weight: 600;
                font-size: 12px;
            }

            /* ===== PREMIUM SCROLLBAR ===== */
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0;
                border: none;
            }

            QScrollBar::handle:vertical {
                background: rgba(169, 177, 195, 0.25);
                border-radius: 4px;
                min-height: 30px;
            }

            QScrollBar::handle:vertical:hover {
                background: rgba(169, 177, 195, 0.4);
            }

            QScrollBar::handle:vertical:pressed {
                background: rgba(41, 199, 201, 0.5);
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }

            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }

            /* Horizontal scrollbar (if needed) */
            QScrollBar:horizontal {
                background: transparent;
                height: 8px;
                margin: 0;
                border: none;
            }

            QScrollBar::handle:horizontal {
                background: rgba(169, 177, 195, 0.25);
                border-radius: 4px;
                min-width: 30px;
            }

            QScrollBar::handle:horizontal:hover {
                background: rgba(169, 177, 195, 0.4);
            }

            QScrollBar::handle:horizontal:pressed {
                background: rgba(41, 199, 201, 0.5);
            }

            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                width: 0px;
            }

            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {
                background: none;
            }

            /* 🔥 REMOVE focus & current-cell outlines completely */
            QTableWidget::item {
                outline: 0;
            }

            QTableWidget::item:selected {
                outline: 0;
            }

            QTableWidget::item:selected:!active {
                outline: 0;
            }

            /* MAIN ROW SEPARATOR */
            QTableWidget::item {
                padding: 5px 8px;
                border-bottom: 1px solid #1E2430;
            }

            /* ROW HOVER (via selection) */
            /* ===== PREMIUM ROW HIGHLIGHT ===== */
            QTableWidget::item:selected,
            QTableWidget::item:selected:active,
            QTableWidget::item:selected:!active {
                background-color: #184540; 
                color: #E6E9F2;
                border: none;
            }

            /* Subtle depth: top/bottom light */
            QTableWidget::item:selected {
                border-top: 1px solid rgba(120, 150, 255, 0.12);
                border-bottom: 1px solid rgba(20, 30, 80, 0.45);
            }

            /* Hovered row (current cell, not selected) */
            QTableWidget::item:!selected:current {
                background-color: rgba(4, 29, 39, 0.6);
            }

            /* REMOVE current-cell focus rectangle */
            QTableWidget::item:selected:!active {
                outline: 0;
            }

            /* Ensure spanned SL/TP rows also glow */
            QTableWidget::item:selected,
            QTableWidget::item:selected:active {
                background-clip: padding;
            }

            /* REMOVE CELL HOVER COMPLETELY */
            QTableWidget::item:hover {
                background-color: transparent;
            }

            #footer {
                background: #041D27;
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

            #portfolioSLTPLabel {
                color: #A9B1C3;
                font-size: 12px;
                font-weight: 500;
            }

            #footerIconButton {
                background-color: transparent;
                color: #A9B1C3;
                border: 1px solid #3A4458;
                border-radius: 4px;
                padding: 0px;
                font-size: 13px;
                font-weight: 600;
            }

            #footerIconButton:hover {
                background-color: #29C7C9;
                color: #161A25;
                border-color: #29C7C9;
            }
            #portfolioSLTPLabel {
                color: #A9B1C3;
                font-size: 11px;
                font-weight: 500;
                padding: 0px 8px;
            }
            #footerTitleLabel {
                color: #A9B1C3;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.3px;
            }

            #footerValueLabel {
                font-size: 13px;
                font-weight: 700;
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

            /* Exit action – danger semantics */
            QMenu::item#exitAction {
                color: #F85149;
                font-weight: 600;
            }

            QMenu::item#exitAction:selected {
                background-color: rgba(248, 81, 73, 0.15);
            }
        """)
