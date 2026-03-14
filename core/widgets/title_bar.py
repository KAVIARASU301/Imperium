from PySide6.QtCore import QPoint, Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QBoxLayout, QPushButton,
    QHBoxLayout, QLabel, QVBoxLayout,
    QSizePolicy,
)

from core.ui_kit.menu_styles import APP_MENU_STYLESHEET

_BTN_W = 44
_BTN_H = 30

# Approximate pixel width reserved for the title label (used for clipping guard)
_TITLE_W = 180


class TitleBar(QWidget):
    """
    Custom title bar.

    Layout band (in the QHBoxLayout):
        [ menu_bar (inserted at 0) ]  [ spacer → ]  [ controls ]

    The app-name label is NOT in that layout at all.
    It is a plain child widget positioned absolutely in resizeEvent,
    always centred on the full widget width (= full window width).
    This means it stays dead-centre regardless of how wide the menu bar
    or the controls are.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.dragging      = False
        self.drag_position = QPoint()

        self.setFixedHeight(_BTN_H)
        self.setStyleSheet("""
            QWidget {
                background-color: #07090E;
                border-bottom: 1px solid #1C2333;
            }
        """)

        # ── Flow layout: only menu-bar placeholder + right-side controls ──────
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.menu_bar = None

        # single stretch fills all space between menu bar and controls
        layout.addStretch(1)

        # controls widget (fixed width, pinned to right edge)
        controls_widget = QWidget()
        controls_widget.setStyleSheet("background: transparent; border: none;")
        ctrl_layout = QHBoxLayout(controls_widget)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(0)
        self._build_controls(ctrl_layout)
        layout.addWidget(controls_widget)

        # ── Floating title label — NOT in any layout ──────────────────────────
        self.title_label = QLabel("IMPERIUM DESK", self)   # parent=self → child, but no layout
        self.title_label.setAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)  # clicks pass through
        self.title_label.setStyleSheet("""
            QLabel {
                color: #00C4C6;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.12em;
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)
        self.title_label.setFixedSize(_TITLE_W, _BTN_H)
        self.title_label.raise_()   # always on top of the layout children

    # ── Overlay centering ─────────────────────────────────────────────────────

    def resizeEvent(self, event):
        """Re-centre the floating label on every resize."""
        super().resizeEvent(event)
        x = (self.width() - self.title_label.width()) // 2
        self.title_label.move(x, 0)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_title(self, mode_text: str = ""):
        base = "IMPERIUM DESK"
        if mode_text:
            text = f"{base}  ·  {mode_text.upper()}"
        else:
            text = base
        self.title_label.setText(text)
        # Widen label if mode badge makes it longer
        fm_w = self.title_label.fontMetrics().horizontalAdvance(text) + 24
        self.title_label.setFixedWidth(max(_TITLE_W, fm_w))
        # Re-centre immediately
        x = (self.width() - self.title_label.width()) // 2
        self.title_label.move(x, 0)

    def set_menu_bar(self, menu_bar):
        """Insert menu bar at index 0 (left edge), before the stretch."""
        self.menu_bar = menu_bar
        layout = self.layout()
        if isinstance(layout, QBoxLayout):
            layout.insertWidget(0, menu_bar)
        menu_bar.setStyleSheet(APP_MENU_STYLESHEET)

    # ── Button builder ────────────────────────────────────────────────────────

    def _build_controls(self, layout: QHBoxLayout):
        # The global QPushButton rule in main_window_shell.py applies:
        #   min-width: 64px  +  padding: 5px 14px
        # Both must be explicitly cancelled here, then setFixedSize() hard-locks
        # the geometry so no cascade can override it.

        std_style = """
            QPushButton {
                min-width:        0px;
                padding:          0px;
                margin:           0px;
                background-color: transparent;
                border:           none;
                border-radius:    0px;
                color:            #4E5D6E;
                font-size:        13px;
                font-weight:      300;
            }
            QPushButton:hover   { background-color: #111827; color: #C8D0DC; }
            QPushButton:pressed { background-color: #1C2333; color: #C8D0DC; }
        """

        close_style = """
            QPushButton {
                min-width:        0px;
                padding:          0px;
                margin:           0px;
                background-color: transparent;
                border:           none;
                border-left:      1px solid #161C28;
                border-radius:    0px;
                color:            #4E5D6E;
                font-size:        14px;
                font-weight:      300;
            }
            QPushButton:hover {
                background-color: #200B0D;
                border-left:      1px solid #3A1215;
                color:            #E0424A;
            }
            QPushButton:pressed { background-color: #2A1215; color: #E0424A; }
        """

        # 1 px separator before button group
        sep = QWidget()
        sep.setFixedSize(1, _BTN_H)
        sep.setStyleSheet("background-color: #1C2333; border: none;")
        layout.addWidget(sep)

        # Minimize
        minimize_btn = QPushButton("−")
        minimize_btn.setFixedSize(_BTN_W, _BTN_H)
        minimize_btn.setStyleSheet(std_style)
        minimize_btn.setToolTip("Minimize")
        minimize_btn.clicked.connect(self.parent_window.showMinimized)
        layout.addWidget(minimize_btn)

        # Maximize — CSS-drawn hollow square icon, no emoji
        self.maximize_btn = QPushButton()
        self.maximize_btn.setFixedSize(_BTN_W, _BTN_H)
        self.maximize_btn.setToolTip("Maximize")
        self.maximize_btn.clicked.connect(self.toggle_maximize)

        self._max_icon = QLabel(self.maximize_btn)
        self._max_icon.setFixedSize(10, 9)
        self._max_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._max_icon.setStyleSheet("""
            QLabel {
                background:    transparent;
                border:        1.5px solid #4E5D6E;
                border-radius: 0px;
            }
        """)
        _inner = QVBoxLayout(self.maximize_btn)
        _inner.setContentsMargins(0, 0, 0, 0)
        _inner.setAlignment(Qt.AlignCenter)
        _inner.addWidget(self._max_icon, alignment=Qt.AlignCenter)

        self.maximize_btn.setStyleSheet("""
            QPushButton {
                min-width:        0px;
                padding:          0px;
                margin:           0px;
                background-color: transparent;
                border:           none;
                border-radius:    0px;
            }
            QPushButton:hover   { background-color: #111827; }
            QPushButton:pressed { background-color: #1C2333; }
        """)
        layout.addWidget(self.maximize_btn)

        # Close
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(_BTN_W, _BTN_H)
        close_btn.setStyleSheet(close_style)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self.parent_window.close)
        layout.addWidget(close_btn)

    # ── Maximize toggle ───────────────────────────────────────────────────────

    def toggle_maximize(self):
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
            self.maximize_btn.setToolTip("Maximize")
            self._max_icon.setStyleSheet("""
                QLabel {
                    background: transparent;
                    border: 1.5px solid #4E5D6E;
                    border-radius: 0px;
                }
            """)
        else:
            self.parent_window.showMaximized()
            self.maximize_btn.setToolTip("Restore")
            self._max_icon.setStyleSheet("""
                QLabel {
                    background: transparent;
                    border: 1.5px solid #4E5D6E;
                    border-top: 2.5px solid #4E5D6E;
                    border-radius: 0px;
                }
            """)

    # ── Drag-to-move ──────────────────────────────────────────────────────────

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