from PySide6.QtWidgets import QComboBox, QPushButton, QVBoxLayout, QDialog


class ManageSymbolSetsDialog(QDialog):
    def __init__(self, symbol_sets: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Symbol Sets")
        self.setFixedSize(350, 300)
        self.symbol_sets = symbol_sets

        layout = QVBoxLayout(self)

        self.list_widget = QComboBox()
        for s in symbol_sets:
            self.list_widget.addItem(s["name"])
        layout.addWidget(self.list_widget)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_selected)
        layout.addWidget(delete_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _delete_selected(self):
        idx = self.list_widget.currentIndex()
        if idx >= 0:
            self.symbol_sets.pop(idx)
            self.list_widget.removeItem(idx)
