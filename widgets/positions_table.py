import logging
import json
import os
from typing import Dict, List
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QApplication,
    QMenu, QAbstractItemView, QDialog, QFormLayout, QDoubleSpinBox, QPushButton
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
        self.visual_order = self._load_visual_order()

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
        self.table.setDragDropMode(QAbstractItemView.DragDrop)

        self.table.setMouseTracking(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)

        main_layout.addWidget(self.table, 1)

        # 🔥 REDESIGNED FOOTER
        self.footer = QWidget()
        self.footer.setFixedHeight(32)
        self.footer.setObjectName("footer")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(10, 0, 10, 0)
        footer_layout.setSpacing(12)
        self.footer.setContextMenuPolicy(Qt.CustomContextMenu)
        self.footer.customContextMenuRequested.connect(
            self._show_footer_context_menu
        )

        # --- LEFT: Refresh button ---
        self.refresh_button = QPushButton("⟳")
        self.refresh_button.setObjectName("footerIconButton")
        self.refresh_button.setFixedSize(24, 24)
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
                if self._drag_active:
                    return False

                row = self.table.rowAt(event.position().toPoint().y())
                if row != self._hovered_row and row >= 0:
                    self._hovered_row = row
                    self.table.setCurrentCell(row, self.SYMBOL_COL)

            elif event.type() == QEvent.Type.Leave:
                # Only clear hover if NOT dragging
                if not self._drag_active:
                    self._hovered_row = -1
                    self.table.setCurrentCell(-1, -1)

            elif event.type() == QEvent.Type.DragLeave:
                self._hovered_row = -1
                self._drag_active = False
                # Don't clear selection here - drag might still be in progress

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

        if not self.visual_order:
            # First run, no saved order
            self.visual_order = list(self.positions.keys())
        else:
            # Merge saved order with live positions
            live = list(self.positions.keys())

            # Keep existing order for still-open positions
            self.visual_order = [s for s in self.visual_order if s in live]

            # Append any new positions at the end
            for s in live:
                if s not in self.visual_order:
                    self.visual_order.append(s)

        self._rebuild_table_from_order()

    def _rebuild_table_from_order(self):
        self.table.setRowCount(0)
        self.position_row_map.clear()

        for symbol in self.visual_order:
            pos = self.positions.get(symbol)
            if not pos:
                continue
            self._add_position_rows(pos)

        self._update_footer()

    def _add_position_rows(self, pos_data: dict):
        symbol = pos_data['tradingsymbol']
        self.positions[symbol] = pos_data

        main_row = self.table.rowCount()
        self.table.insertRow(main_row)
        self.position_row_map[symbol] = main_row
        self.table.setRowHeight(main_row, 32)
        self.table.setProperty(f"row_pid_{main_row}", symbol)
        self.table.setProperty(f"row_role_{main_row}", "MAIN")

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
            self.table.setProperty(f"row_type_{sltp_row}", "SLTP")
            self._set_sltp_row(sltp_row, pos_data)
            self.table.setProperty(f"row_pid_{sltp_row}", symbol)
            self.table.setProperty(f"row_role_{sltp_row}", "SLTP")

    # ------------------------------------------------------------------
    # Helpers (UNCHANGED)
    # ------------------------------------------------------------------

    def _update_footer(self):
        total_pnl = sum(pos.get('pnl', 0.0) for pos in self.positions.values())
        self.total_pnl_value.setText(f"{total_pnl:,.0f}")

        color = "#1DE9B6" if total_pnl >= 0 else "#F85149"
        self.total_pnl_value.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 14px;"
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
    def _load_visual_order(self):
        try:
            path = os.path.expanduser("~/.options_scalper/positions_table_order.json")
            if not os.path.exists(path):
                return []

            with open(path, "r") as f:
                order = json.load(f)

            if isinstance(order, list):
                return order
        except Exception as e:
            logger.warning(f"Failed to load position order: {e}")

        return []

    def _save_visual_order(self):
        try:
            path = os.path.expanduser("~/.options_scalper/positions_table_order.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)

            with open(path, "w") as f:
                json.dump(self.visual_order, f, indent=2)
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
        return row in self.position_row_map.values()

    def _is_sltp_row(self, row):
        return self.table.property(f"row_type_{row}") == "SLTP"


    def _on_item_pressed(self, item: QTableWidgetItem):
        row = item.row()

        # ❌ Prevent dragging SL/TP rows
        if self._is_sltp_row(row):
            return

        self._drag_active = True

    def _handle_drop(self, event):
        self._drag_active = False

        source_row = self.table.currentRow()
        if source_row < 0:
            self.table.setCurrentCell(-1, -1)  # 🔥 Clear here
            return

        source_item = self.table.item(source_row, self.SYMBOL_COL)
        if not source_item:
            self.table.setCurrentCell(-1, -1)  # 🔥 Clear here
            return

        symbol = source_item.text().split()[0]

        pos = event.position().toPoint()
        target_row = self.table.rowAt(pos.y())
        if target_row < 0:
            self.table.setCurrentCell(-1, -1)  # 🔥 Clear here
            return

        target_item = self.table.item(target_row, self.SYMBOL_COL)
        if not target_item:
            self.table.setCurrentCell(-1, -1)  # 🔥 Clear here
            return

        target_symbol = target_item.text().split()[0]

        if symbol == target_symbol:
            self.table.setCurrentCell(-1, -1)  # 🔥 Clear here
            return

        if symbol not in self.visual_order or target_symbol not in self.visual_order:
            self.table.setCurrentCell(-1, -1)  # 🔥 Clear here
            return

        src_idx = self.visual_order.index(symbol)
        tgt_idx = self.visual_order.index(target_symbol)

        self.visual_order.remove(symbol)

        if src_idx < tgt_idx:
            insert_at = self.visual_order.index(target_symbol) + 1
        else:
            insert_at = self.visual_order.index(target_symbol)

        self.visual_order.insert(insert_at, symbol)

        self._rebuild_table_from_order()
        self._save_visual_order()

        self.table.setCurrentCell(-1, -1)  # 🔥 Clear selection after successful drop

    def save_state(self):
        self._save_column_widths()

    def closeEvent(self, event):
        self._save_column_widths()
        self._save_visual_order()
        super().closeEvent(event)

    def _apply_styles(self):
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setFocusPolicy(Qt.StrongFocus)

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
                padding: 6px 8px;
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
                font-size: 15px;
                font-weight: 600;
            }

            #footerIconButton:hover {
                background-color: #29C7C9;
                color: #161A25;
                border-color: #29C7C9;
            }
            #portfolioSLTPLabel {
                color: #A9B1C3;
                font-size: 11.5px;
                font-weight: 500;
                padding: 0px 8px;
            }
            #footerTitleLabel {
                color: #A9B1C3;
                font-size: 11.5px;
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
