from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QWidget, QBoxLayout, QPushButton, QHBoxLayout, QLabel


class TitleBar(QWidget):
    """Custom title bar with window controls and menu bar"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.dragging = False
        self.drag_position = QPoint()

        self.setFixedHeight(32)
        self.setStyleSheet("""
            QWidget {
                background-color: #161A25;
                border-bottom: 1px solid #2A3140;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(6)

        self.menu_bar = None

        layout.addStretch()  # Push everything right

        # 🔥 TITLE NOW NEAR CONTROLS
        self.title_label = QLabel("Imperium Desk")
        self.title_label.setStyleSheet("""
            QLabel {
                color: #29C7C9;
                font-size: 13px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }
        """)

        layout.addWidget(self.title_label)

        self.create_window_controls(layout)

    # -----------------------------------------------------

    def set_title(self, mode_text: str = ""):
        """
        Force Imperium Desk branding.
        Optionally append mode (LIVE / PAPER).
        """
        base = "Imperium Desk"
        if mode_text:
            self.title_label.setText(f"{base} — {mode_text}")
        else:
            self.title_label.setText(base)

    # -----------------------------------------------------

    def set_menu_bar(self, menu_bar):
        self.menu_bar = menu_bar
        layout = self.layout()
        if isinstance(layout, QBoxLayout):
            layout.insertWidget(0, menu_bar)

        menu_bar.setStyleSheet("""
            QMenuBar {
                background-color: transparent;
                color: #E0E0E0;
                border: none;
                font-size: 12px;
                padding: 2px 0px;
            }
            QMenuBar::item {
                background-color: transparent;
                padding: 4px 10px;
                border-radius: 4px;
                margin: 0px 2px;
            }
            QMenuBar::item:selected {
                background-color: #29C7C9;
                color: #161A25;
            }
            QMenuBar::item:pressed {
                background-color: #1f8a8c;
                color: #161A25;
            }
        """)

    # -----------------------------------------------------

    def create_window_controls(self, layout):
        button_style = """
            QPushButton {
                background-color: transparent;
                border: none;
                color: #E0E0E0;
                font-size: 16px;
                font-weight: bold;
                width: 42px;
                height: 28px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.2);
            }
        """

        close_button_style = button_style + """
            QPushButton:hover {
                background-color: #e74c3c;
                color: white;
            }
            QPushButton:pressed {
                background-color: #c0392b;
                color: white;
            }
        """

        minimize_btn = QPushButton("−")
        minimize_btn.setStyleSheet(button_style)
        minimize_btn.clicked.connect(self.parent_window.showMinimized)
        layout.addWidget(minimize_btn)

        self.maximize_btn = QPushButton("⛶")
        self.maximize_btn.setStyleSheet(button_style)
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        layout.addWidget(self.maximize_btn)

        close_btn = QPushButton("×")
        close_btn.setStyleSheet(close_button_style)
        close_btn.clicked.connect(self.parent_window.close)
        layout.addWidget(close_btn)

    # -----------------------------------------------------

    def toggle_maximize(self):
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
            self.maximize_btn.setText("⛶")
        else:
            self.parent_window.showMaximized()
            self.maximize_btn.setText("❐")

    # -----------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.drag_position = (
                event.globalPosition().toPoint()
                - self.parent_window.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.dragging:
            if not self.parent_window.isMaximized():
                self.parent_window.move(
                    event.globalPosition().toPoint() - self.drag_position
                )
            event.accept()

    def mouseReleaseEvent(self, event):
        self.dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximize()
            event.accept()
