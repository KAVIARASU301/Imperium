from __future__ import annotations

import ctypes

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.widgets.account_summary import AccountSummaryWidget
from core.widgets.buy_exit_panel import BuyExitPanel
from core.widgets.header_toolbar import HeaderToolbar
from core.widgets import create_menu_bar
from core.widgets import PositionsTable
from core.widgets.status_bar import StatusBarWidget
from core.widgets import StrikeLadderWidget


class MainWindowShell:
    """UI shell utilities extracted from ImperiumMainWindow."""

    @staticmethod
    def apply_dark_theme(window):
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(window.winId()), 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

        app = QApplication.instance()
        palette = QPalette()

        # ── Palette: base coat ──────────────────────────────────────────────────
        # #07090E  void / deepest background
        # #0C0F17  panel surface
        # #111520  elevated panel / card
        # #161C28  hover / subtle highlight
        # #1C2333  separator / border
        # #253047  interactive border / focus ring
        # #00C4C6  primary accent — cold teal
        # #C8D0DC  primary text
        # #7A8799  secondary text / labels
        base      = QColor(7,   9,  14)   # #07090E
        surface   = QColor(12,  15, 23)   # #0C0F17
        text_hi   = QColor(200, 208, 220) # #C8D0DC
        text_lo   = QColor(122, 135, 153) # #7A8799

        palette.setColor(QPalette.Window,        base)
        palette.setColor(QPalette.Base,          surface)
        palette.setColor(QPalette.AlternateBase, QColor(10, 13, 20))
        palette.setColor(QPalette.Button,        surface)
        palette.setColor(QPalette.WindowText,    text_hi)
        palette.setColor(QPalette.Text,          text_hi)
        palette.setColor(QPalette.ButtonText,    text_hi)
        palette.setColor(QPalette.BrightText,    QColor(255, 255, 255))
        palette.setColor(QPalette.Highlight,     QColor(0, 196, 198))
        palette.setColor(QPalette.HighlightedText, QColor(7, 9, 14))
        palette.setColor(QPalette.PlaceholderText, text_lo)
        palette.setColor(QPalette.Dark,          base)
        palette.setColor(QPalette.Shadow,        QColor(0, 0, 0))

        if app is not None:
            app.setPalette(palette)
            app.setStyle("Fusion")

        window.setStyleSheet(
            """
/* ═══════════════════════════════════════════════════════════════════════════
   IMPERIUM DESK — TERMINAL STYLESHEET
   Palette:
     void      #07090E   deepest background
     panel     #0C0F17   widget surface
     card      #111520   elevated card
     lift      #161C28   hover / row-hover
     border    #1C2333   default border
     fence     #253047   interactive border / focus
     accent    #00C4C6   primary cold-teal accent
     accent-d  #008F91   pressed / active accent
     text-hi   #C8D0DC   primary text
     text-lo   #7A8799   muted / label text
     green     #1DB87E   profit / bid
     red       #E0424A   loss / ask
     amber     #C89B3C   warning / neutral
═══════════════════════════════════════════════════════════════════════════ */

/* ── GLOBAL BASE ─────────────────────────────────────────────────────────── */
QMainWindow {
    background-color: #07090E;
    color: #C8D0DC;
    border: none;
}
QWidget {
    margin: 0px;
    padding: 0px;
    color: #C8D0DC;
    font-family: "Inter", "Segoe UI", "SF Pro Text", sans-serif;
    font-size: 12px;
}

/* ── PANELS & FRAMES ─────────────────────────────────────────────────────── */
QFrame {
    background-color: #0C0F17;
    border: 1px solid #1C2333;
}
QFrame[frameShape="4"],
QFrame[frameShape="5"] {
    background-color: #1C2333;
    border: none;
    max-width: 1px;
    max-height: 1px;
}
QGroupBox {
    background-color: #0C0F17;
    border: 1px solid #1C2333;
    border-radius: 2px;
    margin-top: 18px;
    padding: 4px 6px;
    font-size: 11px;
    font-weight: 600;
    color: #7A8799;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    color: #7A8799;
    background-color: #0C0F17;
}

/* ── SCROLL BARS ─────────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: #07090E;
    width: 6px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #1C2333;
    border-radius: 3px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #253047; }
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical { height: 0px; }

QScrollBar:horizontal {
    background: #07090E;
    height: 6px;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background: #1C2333;
    border-radius: 3px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: #253047; }
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal { width: 0px; }

/* ── TABLES ──────────────────────────────────────────────────────────────── */
QTableWidget, QTableView {
    background-color: #0C0F17;
    alternate-background-color: #0F1219;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    border-radius: 0px;
    gridline-color: #141A24;
    selection-background-color: #161C28;
    selection-color: #C8D0DC;
    outline: none;
}
QTableWidget::item, QTableView::item {
    padding: 2px 6px;
    border: none;
}
QTableWidget::item:selected, QTableView::item:selected {
    background-color: #161C28;
    color: #C8D0DC;
    border-left: 2px solid #00C4C6;
}
QTableWidget::item:hover, QTableView::item:hover {
    background-color: #111925;
}
QHeaderView {
    background-color: #0A0D14;
    border: none;
}
QHeaderView::section {
    background-color: #0A0D14;
    color: #7A8799;
    border: none;
    border-bottom: 1px solid #1C2333;
    border-right: 1px solid #141A24;
    padding: 4px 8px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
}
QHeaderView::section:hover {
    background-color: #111520;
    color: #C8D0DC;
}
QHeaderView::section:first { border-left: none; }
QHeaderView::section:checked { color: #00C4C6; }

/* ── TREE VIEWS ──────────────────────────────────────────────────────────── */
QTreeWidget, QTreeView {
    background-color: #0C0F17;
    alternate-background-color: #0F1219;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    selection-background-color: #161C28;
    selection-color: #C8D0DC;
    outline: none;
}
QTreeWidget::item:hover, QTreeView::item:hover {
    background-color: #111925;
}
QTreeWidget::item:selected, QTreeView::item:selected {
    background-color: #161C28;
    border-left: 2px solid #00C4C6;
}
QTreeWidget::branch { background: #0C0F17; }

/* ── INPUTS ──────────────────────────────────────────────────────────────── */
QLineEdit {
    background-color: #0A0D14;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    border-radius: 2px;
    padding: 4px 8px;
    selection-background-color: #253047;
}
QLineEdit:focus {
    border: 1px solid #253047;
    background-color: #0C0F17;
}
QLineEdit:hover { border-color: #253047; }
QLineEdit:disabled { color: #3A4458; background-color: #080B11; }

QSpinBox, QDoubleSpinBox {
    background-color: #0A0D14;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    border-radius: 2px;
    padding: 3px 6px;
    selection-background-color: #253047;
}
QSpinBox:focus, QDoubleSpinBox:focus { border-color: #253047; }
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background-color: #111520;
    border: none;
    border-left: 1px solid #1C2333;
    width: 16px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #1C2333;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    width: 6px; height: 6px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    width: 6px; height: 6px;
}

QComboBox {
    background-color: #0A0D14;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    border-radius: 2px;
    padding: 4px 8px;
    min-width: 80px;
}
QComboBox:hover  { border-color: #253047; }
QComboBox:focus  { border-color: #00C4C6; }
QComboBox::drop-down {
    border: none;
    border-left: 1px solid #1C2333;
    width: 20px;
    background: #111520;
}
QComboBox::down-arrow {
    width: 8px; height: 8px;
}
QComboBox QAbstractItemView {
    background-color: #0C0F17;
    color: #C8D0DC;
    border: 1px solid #253047;
    selection-background-color: #161C28;
    selection-color: #00C4C6;
    outline: none;
    padding: 2px;
}
QComboBox QAbstractItemView::item { padding: 4px 8px; min-height: 22px; }
QComboBox QAbstractItemView::item:hover { background-color: #111925; }

QTextEdit, QPlainTextEdit {
    background-color: #0A0D14;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    border-radius: 2px;
    padding: 4px;
    selection-background-color: #253047;
    font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
    font-size: 12px;
}
QTextEdit:focus, QPlainTextEdit:focus { border-color: #253047; }

/* ── BUTTONS ─────────────────────────────────────────────────────────────── */
QPushButton {
    background-color: #111520;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    border-radius: 2px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 500;
    min-width: 64px;
}
QPushButton:hover {
    background-color: #161C28;
    border-color: #253047;
    color: #E8EEF8;
}
QPushButton:pressed {
    background-color: #0C0F17;
    border-color: #00C4C6;
}
QPushButton:disabled {
    background-color: #0A0D14;
    color: #3A4458;
    border-color: #141A24;
}
QPushButton#primaryButton, QPushButton[class="primary"] {
    background-color: #00A8AA;
    color: #07090E;
    border: 1px solid #00C4C6;
    font-weight: 700;
}
QPushButton#primaryButton:hover, QPushButton[class="primary"]:hover {
    background-color: #00C4C6;
    border-color: #00C4C6;
}
QPushButton#primaryButton:pressed, QPushButton[class="primary"]:pressed {
    background-color: #008F91;
}
QPushButton#dangerButton, QPushButton[class="danger"] {
    background-color: #1E0E10;
    color: #E0424A;
    border: 1px solid #3D1517;
}
QPushButton#dangerButton:hover, QPushButton[class="danger"]:hover {
    background-color: #2A1215;
    border-color: #E0424A;
}

/* ── CHECK / RADIO ───────────────────────────────────────────────────────── */
QCheckBox {
    color: #C8D0DC;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 13px;
    height: 13px;
    border: 1px solid #253047;
    border-radius: 2px;
    background: #0A0D14;
}
QCheckBox::indicator:checked {
    background: #00C4C6;
    border-color: #00C4C6;
}
QCheckBox::indicator:hover { border-color: #00C4C6; }

QRadioButton { color: #C8D0DC; spacing: 6px; }
QRadioButton::indicator {
    width: 13px; height: 13px;
    border: 1px solid #253047;
    border-radius: 7px;
    background: #0A0D14;
}
QRadioButton::indicator:checked {
    background: #00C4C6;
    border-color: #00C4C6;
}

/* ── SLIDERS ─────────────────────────────────────────────────────────────── */
QSlider::groove:horizontal {
    background: #1C2333;
    height: 3px;
    border-radius: 1px;
}
QSlider::handle:horizontal {
    background: #00C4C6;
    width: 12px; height: 12px;
    margin: -5px 0;
    border-radius: 6px;
}
QSlider::sub-page:horizontal { background: #00A8AA; border-radius: 1px; }

/* ── LABELS ──────────────────────────────────────────────────────────────── */
QLabel {
    color: #C8D0DC;
    background-color: transparent;
    border: none;
}
QLabel#sectionTitle {
    color: #7A8799;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
}

/* ── TABS ────────────────────────────────────────────────────────────────── */
QTabWidget::pane {
    background-color: #0C0F17;
    border: 1px solid #1C2333;
    border-top: none;
}
QTabBar {
    background: #07090E;
}
QTabBar::tab {
    background-color: #07090E;
    color: #7A8799;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 6px 14px;
    min-width: 70px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
}
QTabBar::tab:hover {
    color: #C8D0DC;
    background-color: #0C0F17;
    border-bottom: 2px solid #253047;
}
QTabBar::tab:selected {
    color: #00C4C6;
    background-color: #0C0F17;
    border-bottom: 2px solid #00C4C6;
}

/* ── MENU BAR ────────────────────────────────────────────────────────────── */
QMenuBar {
    background-color: #07090E;
    color: #7A8799;
    border-bottom: 1px solid #1C2333;
    padding: 0px 2px;
    font-size: 12px;
}
QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
}
QMenuBar::item:selected {
    background-color: #111520;
    color: #C8D0DC;
}
QMenuBar::item:pressed {
    background-color: #161C28;
    color: #00C4C6;
}
QMenu {
    background-color: #0C0F17;
    color: #C8D0DC;
    border: 1px solid #253047;
    padding: 3px 0px;
    font-size: 12px;
}
QMenu::item {
    padding: 5px 28px 5px 16px;
    background: transparent;
}
QMenu::item:selected {
    background-color: #161C28;
    color: #00C4C6;
}
QMenu::separator {
    height: 1px;
    background-color: #1C2333;
    margin: 3px 8px;
}
QMenu::indicator {
    width: 14px; height: 14px;
    left: 6px;
}

/* ── TOOLBARS ────────────────────────────────────────────────────────────── */
QToolBar {
    background-color: #0C0F17;
    border-bottom: 1px solid #1C2333;
    spacing: 2px;
    padding: 2px 4px;
}
QToolBar::separator {
    background-color: #1C2333;
    width: 1px;
    margin: 4px 3px;
}
QToolButton {
    background: transparent;
    color: #7A8799;
    border: 1px solid transparent;
    border-radius: 2px;
    padding: 4px 8px;
}
QToolButton:hover {
    background-color: #111520;
    border-color: #1C2333;
    color: #C8D0DC;
}
QToolButton:pressed, QToolButton:checked {
    background-color: #0A0D14;
    border-color: #00C4C6;
    color: #00C4C6;
}

/* ── DIALOGS ─────────────────────────────────────────────────────────────── */
QDialog {
    background-color: #0C0F17;
    color: #C8D0DC;
    border: 1px solid #1C2333;
}
QMessageBox {
    background-color: #0C0F17;
    color: #C8D0DC;
    border: 1px solid #253047;
    border-radius: 3px;
    min-width: 440px;
    min-height: 240px;
}
QMessageBox QLabel {
    color: #C8D0DC;
    background-color: transparent;
    font-size: 13px;
    min-height: 80px;
    padding: 8px 0;
}
QMessageBox QPushButton {
    background-color: #111520;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    border-radius: 2px;
    padding: 6px 18px;
    font-weight: 600;
    min-width: 72px;
}
QMessageBox QPushButton:hover {
    background-color: #161C28;
    border-color: #00C4C6;
    color: #00C4C6;
}
QMessageBox QPushButton:pressed { background-color: #0A0D14; }

/* ── PROGRESS BAR ────────────────────────────────────────────────────────── */
QProgressBar {
    background-color: #0A0D14;
    border: 1px solid #1C2333;
    border-radius: 2px;
    height: 6px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background-color: #00C4C6;
    border-radius: 2px;
}

/* ── TOOLTIPS ────────────────────────────────────────────────────────────── */
QToolTip {
    background-color: #161C28;
    color: #C8D0DC;
    border: 1px solid #253047;
    border-radius: 2px;
    padding: 5px 9px;
    font-size: 11px;
    opacity: 240;
}

/* ── STATUS BAR ──────────────────────────────────────────────────────────── */
QStatusBar {
    background-color: #07090E;
    color: #7A8799;
    border-top: 1px solid #1C2333;
    padding: 0px 8px;
    font-size: 11px;
    min-height: 22px;
}
QStatusBar::item { border: none; }
#footerModeChip, #footerStatusChip, #footerClockChip {
    color: #7A8799;
    background: transparent;
    border: none;
    padding: 0px 2px;
    margin: 0px 1px;
    font-size: 11px;
    font-weight: 400;
}
#footerSeparator {
    color: #1C2333;
    background-color: #1C2333;
    max-width: 1px;
    margin: 0 5px;
}

/* ── DOCK WIDGETS ────────────────────────────────────────────────────────── */
QDockWidget {
    background-color: #0C0F17;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    titlebar-close-icon: none;
}
QDockWidget::title {
    background-color: #07090E;
    color: #7A8799;
    padding: 5px 8px;
    border-bottom: 1px solid #1C2333;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

/* ── SPLITTERS ───────────────────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #1C2333;
    border: none;
}
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical   { height: 1px; }
QSplitter::handle:hover { background-color: #00C4C6; }

/* ── LIST WIDGETS ────────────────────────────────────────────────────────── */
QListWidget, QListView {
    background-color: #0C0F17;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    outline: none;
}
QListWidget::item, QListView::item {
    padding: 4px 8px;
    border-bottom: 1px solid #141A24;
}
QListWidget::item:hover, QListView::item:hover {
    background-color: #111925;
}
QListWidget::item:selected, QListView::item:selected {
    background-color: #161C28;
    color: #C8D0DC;
    border-left: 2px solid #00C4C6;
}

/* ── CALENDAR ────────────────────────────────────────────────────────────── */
QCalendarWidget {
    background-color: #0C0F17;
    color: #C8D0DC;
}
QCalendarWidget QAbstractItemView {
    background-color: #0C0F17;
    color: #C8D0DC;
    selection-background-color: #00C4C6;
    selection-color: #07090E;
}
QCalendarWidget QToolButton {
    color: #C8D0DC;
    background: transparent;
}

/* ── DATE / TIME EDIT ────────────────────────────────────────────────────── */
QDateEdit, QTimeEdit, QDateTimeEdit {
    background-color: #0A0D14;
    color: #C8D0DC;
    border: 1px solid #1C2333;
    border-radius: 2px;
    padding: 3px 6px;
}
QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus {
    border-color: #253047;
}

/* ── CUSTOM OBJECT NAMES ─────────────────────────────────────────────────── */
#strikeTableBid  { color: #1DB87E; font-weight: 600; }
#strikeTableAsk  { color: #E0424A; font-weight: 600; }
#strikeTableATM  { background-color: #0E1820; border-left: 2px solid #00C4C6; }
#pnlPositive     { color: #1DB87E; font-weight: 700; }
#pnlNegative     { color: #E0424A; font-weight: 700; }
#accentLabel     { color: #00C4C6; font-weight: 600; }
#warningLabel    { color: #C89B3C; }
#killSwitchBanner {
    background-color: #1A0709;
    color: #E0424A;
    border: 1px solid #3D1517;
    border-left: 3px solid #E0424A;
    padding: 6px 12px;
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 0.06em;
}
"""
        )

    @staticmethod
    def setup_ui(window):
        main_container = QWidget()
        window.setCentralWidget(main_container)

        container_layout = QVBoxLayout(main_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        container_layout.addWidget(window.title_bar)

        content_widget = QWidget()
        container_layout.addWidget(content_widget)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        window.header = HeaderToolbar()
        content_layout.addWidget(window.header)

        main_content_widget = QWidget()
        content_layout.addWidget(main_content_widget)
        main_content_layout = QVBoxLayout(main_content_widget)
        main_content_layout.setContentsMargins(0, 0, 0, 0)
        main_content_layout.setSpacing(0)

        MainWindowShell.create_main_widgets(window)

        window.main_splitter = QSplitter(Qt.Horizontal)
        MainWindowShell.style_splitter(window.main_splitter)

        left_splitter = MainWindowShell.create_left_column(window)
        window.main_splitter.addWidget(left_splitter)

        center_column = MainWindowShell.create_center_column(window)
        center_widget = QWidget()
        center_widget.setLayout(center_column)
        window.main_splitter.addWidget(center_widget)

        fourth_column = MainWindowShell.create_fourth_column(window)
        fourth_widget = QWidget()
        fourth_widget.setLayout(fourth_column)
        window.main_splitter.addWidget(fourth_widget)

        window.main_splitter.setSizes([250, 600, 350])
        main_content_layout.addWidget(window.main_splitter)

        MainWindowShell.setup_menu_bar(window)
        MainWindowShell.setup_status_footer(window)

        QTimer.singleShot(3000, window._update_account_info)

    @staticmethod
    def setup_status_footer(window):
        window.status_bar_widget = StatusBarWidget(window.statusBar(), window.trading_mode)

    @staticmethod
    def publish_status(window, message: str, timeout_ms: int = 4000, level: str = "info"):
        window.status_bar_widget.publish_message(message, timeout_ms, level)

    @staticmethod
    def create_main_widgets(window):
        window.buy_exit_panel = BuyExitPanel(window.trader)
        window.buy_exit_panel.setMinimumSize(200, 320)
        window.account_summary = AccountSummaryWidget()
        window.account_summary.setMinimumHeight(200)
        window.account_summary.setContentsMargins(3, 0, 3, 0)
        window.strike_ladder = StrikeLadderWidget(window.real_kite_client)
        window.auto_trader_embed = None
        window.strike_ladder.setMinimumWidth(500)
        if hasattr(window.strike_ladder, "setMaximumWidth"):
            window.strike_ladder.setMaximumWidth(800)
            window.strike_ladder.setMaximumHeight(700)
        window.inline_positions_table = PositionsTable(config_manager=window.config_manager)
        window.inline_positions_table.setMinimumWidth(300)
        window.inline_positions_table.setMinimumHeight(200)

    @staticmethod
    def create_left_column(window) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setContentsMargins(0, 0, 0, 0)
        MainWindowShell.style_splitter(splitter)
        splitter.addWidget(window.buy_exit_panel)
        splitter.addWidget(window.account_summary)
        splitter.setSizes([400, 200])
        return splitter

    @staticmethod
    def create_center_column(window) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(3, 3, 3, 3)

        # Manual mode only — strike ladder is always the center widget.
        # No QStackedWidget needed; auto trader panel slot removed.
        layout.addWidget(window.strike_ladder, 1)

        return layout

    @staticmethod
    def create_fourth_column(window) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setContentsMargins(3, 3, 0, 3)
        layout.setSpacing(0)
        layout.addWidget(window.inline_positions_table, 1)
        return layout

    @staticmethod
    def setup_menu_bar(window):
        menubar, menu_actions = create_menu_bar(window)
        window.title_bar.set_menu_bar(menubar)
        menu_actions["refresh"].triggered.connect(window._refresh_data)
        menu_actions["exit"].triggered.connect(window.close)
        menu_actions["positions"].triggered.connect(window._show_positions_dialog)
        menu_actions["pnl_history"].triggered.connect(window._show_pnl_history_dialog)
        menu_actions["pending_orders"].triggered.connect(window._show_pending_orders_dialog)
        menu_actions["orders"].triggered.connect(window._show_order_history_dialog)
        menu_actions["performance"].triggered.connect(window._show_performance_dialog)
        menu_actions["watchlist"].triggered.connect(window._show_watchlist_dialog)
        menu_actions["settings"].triggered.connect(window._show_settings)
        menu_actions["option_chain"].triggered.connect(window._show_option_chain_dialog)
        menu_actions["strategy_builder"].triggered.connect(window._show_strategy_builder_dialog)
        menu_actions["refresh_positions"].triggered.connect(window._refresh_positions)
        menu_actions["shortcuts"].triggered.connect(window._show_shortcuts)
        menu_actions["expiry_days"].triggered.connect(window._show_expiry_days)
        menu_actions["about"].triggered.connect(window._show_about)
        menu_actions["market_monitor"].triggered.connect(window._show_market_monitor_dialog)
        menu_actions["cvd_chart"].triggered.connect(window._show_cvd_chart_dialog)
        menu_actions["cvd_market_monitor"].triggered.connect(window._show_cvd_market_monitor_dialog)
        menu_actions["cvd_symbol_sets"].triggered.connect(window._show_cvd_symbol_set_dialog)
        menu_actions["price_cvd_chart"].triggered.connect(window._show_price_cvd_chart_dialog)
        menu_actions["fii_dii_data"].triggered.connect(window._show_fii_dii_dialog)

    @staticmethod
    def style_splitter(splitter: QSplitter):
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            """
            QSplitter::handle            { background-color: #1C2333; border: none; }
            QSplitter::handle:horizontal { width: 1px; }
            QSplitter::handle:vertical   { height: 1px; }
            QSplitter::handle:hover      { background-color: #00C4C6; }
            """
        )

    @staticmethod
    def fade_in_widget(widget, duration_ms: int = 220):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(duration_ms)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.start()
        return animation