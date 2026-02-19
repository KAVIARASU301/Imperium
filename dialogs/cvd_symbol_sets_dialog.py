from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QPushButton, QLabel, QLineEdit, QTextEdit, QMessageBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtCore import Signal

from core.cvd.cvd_symbol_sets import CVDSymbolSetManager


class ManageCVDSymbolSetsDialog(QDialog):
    symbol_sets_updated = Signal()

    def __init__(self, symbol_set_manager: CVDSymbolSetManager, parent=None):
        super().__init__(parent)

        self.symbol_set_manager = symbol_set_manager
        self.symbol_sets = self.symbol_set_manager.load_sets()
        self.current_index = None

        self.setWindowTitle("Manage CVD Symbol Sets")
        self.setMinimumSize(520, 380)

        self._apply_skin()
        self._setup_ui()
        self._load_list()

    # -------------------------------------------------

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setSpacing(10)

        # ---- Left: Set list ----
        left = QVBoxLayout()
        left.addWidget(QLabel("Symbol Sets"))

        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QListWidget.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.MoveAction)
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.list_widget.currentRowChanged.connect(self._on_set_selected)
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        self.list_widget.setTextElideMode(Qt.ElideNone)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        self.list_widget.setSelectionRectVisible(False)
        self.list_widget.setToolTip("Drag and drop to reorder symbol sets")

        left.addWidget(self.list_widget, 1)

        add_btn = QPushButton("Add New")
        add_btn.setAutoDefault(False)
        add_btn.setDefault(False)
        add_btn.clicked.connect(self._add_new_set)
        left.addWidget(add_btn)

        root.addLayout(left, 1)

        # ---- Right: Editor ----
        right = QVBoxLayout()

        right.addWidget(QLabel("Set Name"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Enter symbol set name")
        self.name_edit.returnPressed.connect(self._apply_set_name)
        right.addWidget(self.name_edit)

        right.addWidget(QLabel("Symbols (comma separated)"))
        self.symbols_edit = QTextEdit()
        self.symbols_edit.setPlaceholderText(
            "HDFCBANK, ICICIBANK, AXISBANK, SBIN"
        )
        right.addWidget(self.symbols_edit, 1)

        # ---- Stretch pushes status to bottom ----
        right.addStretch(1)

        # ---- Fixed bottom status row ----
        self.status_label = QLabel("")
        self.status_label.setFixedHeight(16)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.status_label.setStyleSheet("""
            color: #7FD6DB;
            font-size: 10px;
            padding-left: 2px;
        """)
        right.addWidget(self.status_label)


        btns = QHBoxLayout()

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_set)
        btns.addWidget(save_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_set)
        btns.addWidget(delete_btn)

        right.addLayout(btns)
        root.addLayout(right, 2)

    def _apply_set_name(self):
        """
        Apply name edit to the currently selected symbol set.
        Triggered by Enter key.
        """
        if self.current_index is None:
            return

        name = self.name_edit.text().strip()
        if not name:
            return

        # Update model
        self.symbol_sets[self.current_index]["name"] = name
        self.symbol_set_manager.save_sets(self.symbol_sets)

        # Update list UI immediately
        self._load_list(restore_index=self.current_index)

    def _on_rows_moved(self, parent, start, end, destination, row):
        """
        Sync symbol_sets order after drag-and-drop reorder.
        """
        if start == row or start < 0:
            return

        moved = self.symbol_sets.pop(start)

        # Qt gives destination index *after* removal
        if row > start:
            row -= 1

        self.symbol_sets.insert(row, moved)

        # Persist new order
        self.symbol_set_manager.save_sets(self.symbol_sets)

        # Restore selection
        self.current_index = row
        self._load_list(restore_index=row)

    # -------------------------------------------------

    def _load_list(self, restore_index: int | None = None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        for s in self.symbol_sets:
            self.list_widget.addItem(s.get("name", "Unnamed"))

        self.list_widget.blockSignals(False)

        if restore_index is not None and 0 <= restore_index < len(self.symbol_sets):
            self.list_widget.setCurrentRow(restore_index)

    # -------------------------------------------------

    def _on_set_selected(self, index: int):
        if index < 0 or index >= len(self.symbol_sets):
            self.current_index = None
            self.name_edit.clear()
            self.symbols_edit.clear()
            return

        self.current_index = index
        data = self.symbol_sets[index]

        self.name_edit.setText(data.get("name", ""))
        self.symbols_edit.setPlainText(
            ", ".join(data.get("symbols", []))
        )

    # -------------------------------------------------

    def _add_new_set(self):
        self.symbol_sets.append({
            "name": "New Set",
            "symbols": []
        })
        self._load_list()
        self.list_widget.setCurrentRow(len(self.symbol_sets) - 1)

    # -------------------------------------------------

    def _save_set(self):
        if self.current_index is None:
            return

        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Set name cannot be empty.")
            return

        raw_symbols = self.symbols_edit.toPlainText()
        symbols = [
            s.strip().upper()
            for s in raw_symbols.split(",")
            if s.strip()
        ]

        idx = self.current_index  # preserve before list rebuild

        self.symbol_sets[idx] = {
            "name": name,
            "symbols": symbols
        }

        self.symbol_set_manager.save_sets(self.symbol_sets)
        self._load_list(restore_index=idx)
        self.symbol_sets_updated.emit()
        self.status_label.setText("Saved ✓")

        QTimer.singleShot(1200, lambda: self.status_label.setText(""))

    # -------------------------------------------------

    def _delete_set(self):
        if self.current_index is None:
            return

        idx = self.current_index

        # Delete immediately (no confirmation)
        self.symbol_sets.pop(idx)
        self.symbol_set_manager.save_sets(self.symbol_sets)
        self.symbol_sets_updated.emit()
        self.current_index = None

        # Restore sensible selection
        self._load_list(
            restore_index=min(idx, len(self.symbol_sets) - 1)
            if self.symbol_sets else None
        )

        self.name_edit.clear()
        self.symbols_edit.clear()

    # -------------------------------------------------
    def _apply_skin(self):
        self.setStyleSheet("""
            /* =========================
               Dialog background (texture)
               ========================= */
            QDialog {
                background-color: qlineargradient(
                                    x1:0, y1:0, x2:0, y2:1,
                                    stop:0 #1F2533,
                                    stop:1 #141925
                                );
                background-image: url("assets/textures/texture.png");
   
            }

            /* =========================
               Section labels
               ========================= */
            QLabel {
                color: #CFE8EA;
                font-size: 11px;
            }

            /* =========================
               List widget (symbol sets)
               ========================= */
            QListWidget {
                background-color: rgba(0, 0, 0, 40);
                border: 1px solid #2E5A60;
                border-radius: 6px;
                padding: 4px;
                color: #E6F3F4;
            }

            QListWidget::item {
                padding: 6px;
            }

            QListWidget::item:selected {
                background-color: rgba(0, 150, 160, 140);
                color: #FFFFFF;
                border-radius: 4px;
            }
            QListWidget::item {
                selection-background-color: transparent;
                selection-color: #FFFFFF;
            }

            QListWidget::item:selected:!active {
                background-color: rgba(0, 150, 160, 120);
                color: #FFFFFF;
            }


            /* =========================
               Text inputs
               ========================= */
            QLineEdit, QTextEdit {
                background-color: rgba(0, 0, 0, 55);
                border: 1px solid #2E5A60;
                border-radius: 6px;
                padding: 6px;
                color: #E6F3F4;
                font-size: 11px;
            }

            QTextEdit {
                selection-background-color: #007A85;
            }

            /* =========================
               Buttons
               ========================= */
            QPushButton {
                background-color: rgba(0, 0, 0, 45);
                border: 1px solid #2E5A60;
                border-radius: 6px;
                padding: 6px 12px;
                color: #CFE8EA;
                font-size: 11px;
            }

            QPushButton:hover {
                background-color: rgba(0, 150, 160, 70);
                color: #FFFFFF;
            }

            QPushButton:pressed {
                background-color: rgba(0, 120, 130, 90);
            }

            /* =========================
               Message boxes (confirm delete)
               ========================= */
            QMessageBox {
                background-color: #003038;
                color: #E6F3F4;
            }
        """)

    def exec(self):
        result = super().exec()
        return result
