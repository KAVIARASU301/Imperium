from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QWidget, QBoxLayout, QPushButton, QHBoxLayout


class TitleBar(QWidget):
    """Custom title bar with window controls and menu bar"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.dragging = False
        self.drag_position = QPoint()

        self.setFixedHeight(28)
        self.setStyleSheet("""
            CustomTitleBar {
                background-color: #1a1a1a;
                border-bottom: 1px solid #333;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(0)

        self.menu_bar = None
        layout.addStretch()
        self.create_window_controls(layout)

    def set_menu_bar(self, menu_bar):
        self.menu_bar = menu_bar
        layout = self.layout()
        if isinstance(layout, QBoxLayout):
            layout.insertWidget(0, menu_bar)
        menu_bar.setStyleSheet("""
            QMenuBar {

                background-color: transparent; color: #E0E0E0; border: none;
                font-size: 12px; padding: 2px 0px;
            }
            QMenuBar::item {
                background-color: transparent; padding: 4px 10px;
                border-radius: 4px; margin: 0px 2px;
            }
            QMenuBar::item:selected { background-color: #29C7C9; color: #161A25; }
            QMenuBar::item:pressed { background-color: #1f8a8c; color: #161A25; }
        """)

    def create_window_controls(self, layout):
        button_style = """
            QPushButton {

                background-color: transparent; border: none; color: #E0E0E0;
                font-size: 16px; font-weight: bold; padding: 0px; margin: 0px;
                width: 42px; height: 28px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
            QPushButton:pressed { background-color: rgba(255, 255, 255, 0.2); }
        """
        maximize_button_style = """
            QPushButton {
                background-color: transparent; border: none; color: #E0E0E0;
                font-size: 14px; font-weight: bold; padding: 0px; margin: 0px;
                width: 42px; height: 28px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
            QPushButton:pressed { background-color: rgba(255, 255, 255, 0.2); }
        """
        close_button_style = button_style + """
            QPushButton:hover { background-color: #e74c3c; color: white; }
            QPushButton:pressed { background-color: #c0392b; color: white; }
        """

        minimize_btn = QPushButton("−")
        minimize_btn.setStyleSheet(button_style)
        minimize_btn.clicked.connect(self.parent_window.showMinimized)
        layout.addWidget(minimize_btn)

        self.maximize_btn = QPushButton("⛶")
        self.maximize_btn.setStyleSheet(maximize_button_style)
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        layout.addWidget(self.maximize_btn)

        close_btn = QPushButton("×")
        close_btn.setStyleSheet(close_button_style)
        close_btn.clicked.connect(self.parent_window.close)
        layout.addWidget(close_btn)

    def toggle_maximize(self):
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
            self.maximize_btn.setText("⛶")
        else:
            self.parent_window.showMaximized()
            self.maximize_btn.setText("❐")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.parent_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.dragging:
            if not self.parent_window.isMaximized():
                self.parent_window.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximize()
            event.accept()
