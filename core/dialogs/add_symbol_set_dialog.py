from PySide6.QtWidgets import QPushButton, QHBoxLayout, QLineEdit, QDialog, QVBoxLayout, QLabel


class AddSymbolSetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Symbol Set")
        self.setFixedSize(400, 220)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Group Name"))
        self.name_edit = QLineEdit()
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("Symbols (comma separated)"))
        self.symbols_edit = QLineEdit()
        layout.addWidget(self.symbols_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")

        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    def get_data(self):
        return {
            "name": self.name_edit.text().strip(),
            "symbols": self.symbols_edit.text().strip()
        }
