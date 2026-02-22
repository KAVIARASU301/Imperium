from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from utils.data_models import OptionType

SHORTCUTS = (
    {
        "title": "Menu Actions",
        "items": (
            ("Refresh Data", "F5"),
            ("Refresh Positions", "Ctrl+R"),
            ("Market Monitor", "Ctrl+M"),
            ("CVD Single Chart", "Ctrl+C"),
            ("CVD Multi Chart", "Ctrl+D"),
            ("CVD Symbol Sets", "Ctrl+Shift+D"),
            ("Option Chain", "Ctrl+O"),
            ("FII DII Data", "Ctrl+F"),
            ("Settings", "Ctrl+,"),
            ("Exit", "Ctrl+Q"),
        ),
    },
    {
        "title": "Trading Actions",
        "items": (
            ("Buy (current option type)", "B"),
            ("Toggle CALL / PUT", "T"),
            ("Exit all positions", "X"),
            ("Exit CALL positions", "Alt+C"),
            ("Exit PUT positions", "Alt+P"),
        ),
    },
    {
        "title": "Lot Size Control",
        "items": (
            ("Increase lot size by 1", "+"),
            ("Decrease lot size by 1", "-"),
            ("Set lot size to 1", "Alt+1"),
            ("Set lot size to 2", "Alt+2"),
            ("Set lot size to 3", "Alt+3"),
            ("Set lot size to 4", "Alt+4"),
            ("Set lot size to 5", "Alt+5"),
            ("Set lot size to 6", "Alt+6"),
            ("Set lot size to 7", "Alt+7"),
            ("Set lot size to 8", "Alt+8"),
            ("Set lot size to 9", "Alt+9"),
            ("Set lot size to 10", "Alt+0"),
        ),
    },
    {
        "title": "Exact Single-Strike Buy (Relative to ATM)",
        "items": (
            ("ATM +1 strike", "Shift+1"),
            ("ATM +2 strike", "Shift+2"),
            ("ATM +3 strike", "Shift+3"),
            ("ATM +4 strike", "Shift+4"),
            ("ATM +5 strike", "Shift+5"),
            ("ATM +6 strike", "Shift+6"),
            ("ATM +7 strike", "Shift+7"),
            ("ATM +8 strike", "Shift+8"),
            ("ATM +9 strike", "Shift+9"),
            ("ATM +10 strike", "Shift+0"),
            ("ATM -1 strike", "Ctrl+1"),
            ("ATM -2 strike", "Ctrl+2"),
            ("ATM -3 strike", "Ctrl+3"),
            ("ATM -4 strike", "Ctrl+4"),
            ("ATM -5 strike", "Ctrl+5"),
            ("ATM -6 strike", "Ctrl+6"),
            ("ATM -7 strike", "Ctrl+7"),
            ("ATM -8 strike", "Ctrl+8"),
            ("ATM -9 strike", "Ctrl+9"),
            ("ATM -10 strike", "Ctrl+0"),
        ),
    },
    {
        "title": "ATM Range Buy (All Strikes In Between)",
        "items": (
            ("ATM → +1 strike range", "Alt+Shift+1"),
            ("ATM → +2 strike range", "Alt+Shift+2"),
            ("ATM → +3 strike range", "Alt+Shift+3"),
            ("ATM → +4 strike range", "Alt+Shift+4"),
            ("ATM → +5 strike range", "Alt+Shift+5"),
            ("ATM → +6 strike range", "Alt+Shift+6"),
            ("ATM → +7 strike range", "Alt+Shift+7"),
            ("ATM → +8 strike range", "Alt+Shift+8"),
            ("ATM → +9 strike range", "Alt+Shift+9"),
            ("ATM → +10 strike range", "Alt+Shift+0"),
            ("ATM → -1 strike range", "Alt+Ctrl+1"),
            ("ATM → -2 strike range", "Alt+Ctrl+2"),
            ("ATM → -3 strike range", "Alt+Ctrl+3"),
            ("ATM → -4 strike range", "Alt+Ctrl+4"),
            ("ATM → -5 strike range", "Alt+Ctrl+5"),
            ("ATM → -6 strike range", "Alt+Ctrl+6"),
            ("ATM → -7 strike range", "Alt+Ctrl+7"),
            ("ATM → -8 strike range", "Alt+Ctrl+8"),
            ("ATM → -9 strike range", "Alt+Ctrl+9"),
            ("ATM → -10 strike range", "Alt+Ctrl+0"),
        ),
    },
)


def _build_section(section: dict) -> QGroupBox:
    group = QGroupBox(section["title"])
    group.setStyleSheet(
        """
        QGroupBox {
            color: #FFFFFF;
            font-weight: 600;
            margin-top: 12px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 6px;
        }
        """
    )
    form = QFormLayout()
    form.setContentsMargins(10, 8, 10, 8)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(6)
    form.setLabelAlignment(Qt.AlignLeft)
    for label, keys in section["items"]:
        label_widget = QLabel(label)
        label_widget.setStyleSheet("color: #A9B1C3;")
        key_widget = QLabel(keys)
        key_widget.setStyleSheet("color: #E6EAF2; font-weight: 600;")
        form.addRow(label_widget, key_widget)
    group.setLayout(form)
    return group


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setModal(True)
        self.resize(900, 620)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(16, 16, 16, 12)
        main_layout.setSpacing(12)

        title = QLabel("Keyboard Shortcuts")
        title.setStyleSheet("color: #FFFFFF; font-size: 16pt; font-weight: 700;")

        subtitle = QLabel("Use these shortcuts anywhere in the app for faster actions.")
        subtitle.setStyleSheet("color: #9AA4B2; font-size: 10.5pt;")

        content_widget = QWidget()
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)

        midpoint = (len(SHORTCUTS) + 1) // 2
        left_sections = SHORTCUTS[:midpoint]
        right_sections = SHORTCUTS[midpoint:]

        for row, section in enumerate(left_sections):
            grid.addWidget(_build_section(section), row, 0)

        for row, section in enumerate(right_sections):
            grid.addWidget(_build_section(section), row, 1)

        content_widget.setLayout(grid)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        scroll_area.setWidget(content_widget)

        footer_layout = QHBoxLayout()
        footer_layout.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        footer_layout.addWidget(close_button)

        main_layout.addWidget(title)
        main_layout.addWidget(subtitle)
        main_layout.addWidget(scroll_area, 1)
        main_layout.addLayout(footer_layout)
        self.setLayout(main_layout)


def show_shortcuts(parent) -> None:
    dialog = ShortcutsDialog(parent)
    dialog.exec()


def setup_keyboard_shortcuts(window) -> None:
    """Wire global keyboard shortcuts for the main window."""
    window._shortcuts = []

    def bind(key, callback):
        sc = QShortcut(QKeySequence(key), window)
        sc.setContext(Qt.ApplicationShortcut)
        sc.activated.connect(callback)
        window._shortcuts.append(sc)

    bind("B", lambda: window.buy_exit_panel._on_buy_clicked())
    bind("T", window.buy_exit_panel.toggle_option_type)

    bind("X", window._exit_all_positions)
    bind("Alt+C", lambda: window._exit_option_positions(OptionType.CALL))
    bind("Alt+P", lambda: window._exit_option_positions(OptionType.PUT))

    bind("+", lambda: window._change_lot_size(1))
    bind("-", lambda: window._change_lot_size(-1))

    bind("Alt+1", lambda: window._set_lot_size(1))
    bind("Alt+2", lambda: window._set_lot_size(2))
    bind("Alt+3", lambda: window._set_lot_size(3))
    bind("Alt+4", lambda: window._set_lot_size(4))
    bind("Alt+5", lambda: window._set_lot_size(5))
    bind("Alt+6", lambda: window._set_lot_size(6))
    bind("Alt+7", lambda: window._set_lot_size(7))
    bind("Alt+8", lambda: window._set_lot_size(8))
    bind("Alt+9", lambda: window._set_lot_size(9))
    bind("Alt+0", lambda: window._set_lot_size(10))

    bind("Shift+1", lambda: window._buy_exact_relative_strike(+1))
    bind("Shift+2", lambda: window._buy_exact_relative_strike(+2))
    bind("Shift+3", lambda: window._buy_exact_relative_strike(+3))
    bind("Shift+4", lambda: window._buy_exact_relative_strike(+4))
    bind("Shift+5", lambda: window._buy_exact_relative_strike(+5))
    bind("Shift+6", lambda: window._buy_exact_relative_strike(+6))
    bind("Shift+7", lambda: window._buy_exact_relative_strike(+7))
    bind("Shift+8", lambda: window._buy_exact_relative_strike(+8))
    bind("Shift+9", lambda: window._buy_exact_relative_strike(+9))
    bind("Shift+0", lambda: window._buy_exact_relative_strike(+10))

    bind("Ctrl+1", lambda: window._buy_exact_relative_strike(-1))
    bind("Ctrl+2", lambda: window._buy_exact_relative_strike(-2))
    bind("Ctrl+3", lambda: window._buy_exact_relative_strike(-3))
    bind("Ctrl+4", lambda: window._buy_exact_relative_strike(-4))
    bind("Ctrl+5", lambda: window._buy_exact_relative_strike(-5))
    bind("Ctrl+6", lambda: window._buy_exact_relative_strike(-6))
    bind("Ctrl+7", lambda: window._buy_exact_relative_strike(-7))
    bind("Ctrl+8", lambda: window._buy_exact_relative_strike(-8))
    bind("Ctrl+9", lambda: window._buy_exact_relative_strike(-9))
    bind("Ctrl+0", lambda: window._buy_exact_relative_strike(-10))

    bind("Alt+Shift+1", lambda: window._buy_relative_to_atm(above=1))
    bind("Alt+Shift+2", lambda: window._buy_relative_to_atm(above=2))
    bind("Alt+Shift+3", lambda: window._buy_relative_to_atm(above=3))
    bind("Alt+Shift+4", lambda: window._buy_relative_to_atm(above=4))
    bind("Alt+Shift+5", lambda: window._buy_relative_to_atm(above=5))
    bind("Alt+Shift+6", lambda: window._buy_relative_to_atm(above=6))
    bind("Alt+Shift+7", lambda: window._buy_relative_to_atm(above=7))
    bind("Alt+Shift+8", lambda: window._buy_relative_to_atm(above=8))
    bind("Alt+Shift+9", lambda: window._buy_relative_to_atm(above=9))
    bind("Alt+Shift+0", lambda: window._buy_relative_to_atm(above=10))

    bind("Alt+Ctrl+1", lambda: window._buy_relative_to_atm(below=1))
    bind("Alt+Ctrl+2", lambda: window._buy_relative_to_atm(below=2))
    bind("Alt+Ctrl+3", lambda: window._buy_relative_to_atm(below=3))
    bind("Alt+Ctrl+4", lambda: window._buy_relative_to_atm(below=4))
    bind("Alt+Ctrl+5", lambda: window._buy_relative_to_atm(below=5))
    bind("Alt+Ctrl+6", lambda: window._buy_relative_to_atm(below=6))
    bind("Alt+Ctrl+7", lambda: window._buy_relative_to_atm(below=7))
    bind("Alt+Ctrl+8", lambda: window._buy_relative_to_atm(below=8))
    bind("Alt+Ctrl+9", lambda: window._buy_relative_to_atm(below=9))
    bind("Alt+Ctrl+0", lambda: window._buy_relative_to_atm(below=10))
