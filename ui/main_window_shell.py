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

from widgets.account_summary import AccountSummaryWidget
from widgets.buy_exit_panel import BuyExitPanel
from widgets.header_toolbar import HeaderToolbar
from widgets.menu_bar import create_menu_bar
from widgets.positions_table import PositionsTable
from widgets.status_bar import StatusBarWidget
from widgets.strike_ladder import StrikeLadderWidget


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
        dark_bg = QColor(22, 26, 37)
        light_text = QColor(224, 224, 224)

        palette.setColor(QPalette.Window, dark_bg)
        palette.setColor(QPalette.Base, dark_bg)
        palette.setColor(QPalette.AlternateBase, dark_bg)
        palette.setColor(QPalette.Button, dark_bg)
        palette.setColor(QPalette.WindowText, light_text)
        palette.setColor(QPalette.Text, light_text)
        palette.setColor(QPalette.ButtonText, light_text)
        palette.setColor(QPalette.BrightText, light_text)
        palette.setColor(QPalette.Dark, dark_bg)
        palette.setColor(QPalette.Shadow, dark_bg)

        if app is not None:
            app.setPalette(palette)
            app.setStyle("Fusion")

        window.setStyleSheet(
            """
            QMainWindow { background-image: url("assets/textures/main_window_bg.png");background-color: #0f0f0f !important; color: #ffffff; border: 1px solid #333; }
            QWidget { margin: 0px; padding: 0px; }
            QMessageBox { background-image: url("assets/textures/Qmessage_texture.png");background-color: #161A25 !important; color: #E0E0E0 !important; border: 1px solid #3A4458; border-radius: 8px; min-width: 460px; min-height: 260px; }
            QMessageBox { border: none; margin: 0px; }
            QMessageBox::title, QMessageBox QWidget, QMessageBox * { background-image: url("assets/textures/Qmessage_texture.png"); background-color: #161A25 !important; color: #E0E0E0 !important; }
            QMessageBox QLabel { color: #E0E0E0 !important; background-color: #161A25 !important; font-size: 13px; min-height: 120px; }
            QMessageBox QPushButton { background-color: #212635 !important; color: #E0E0E0 !important; border: 1px solid #3A4458; border-radius: 5px; padding: 8px 16px; font-weight: 500; min-width: 70px; }
            QMessageBox QPushButton:hover { background-color: #29C7C9 !important; color: #04b3bd !important; border-color: #29C7C9; }
            QMessageBox QPushButton:pressed { background-color: #1f8a8c !important; }
            QDialog { background-color: #161A25; color: #E0E0E0; }
            QStatusBar {
                background-image: url("assets/textures/status_bar_texture.png");
                background-color: #141A27;
                color: #8F9CB2;
                border-top: 1px solid #242C3B;
                padding: 1px 8px;
                font-size: 11px;
            }
            QStatusBar::item { border: none; }
            #footerModeChip, #footerStatusChip, #footerClockChip {
                color: #8390A7;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px 2px;
                font-weight: 400;
            }
            #footerSeparator {
                color: #202736;
                background-color: #202736;
                max-width: 1px;
                margin: 0 4px;
            }
            QDockWidget { background-color: #1a1a1a; color: #fff; border: 1px solid #333; }
            QDockWidget::title { background-color: #2a2a2a; padding: 5px; border-bottom: 1px solid #333; }
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
        window.buy_exit_panel.setMinimumSize(200, 300)
        window.account_summary = AccountSummaryWidget()
        window.account_summary.setMinimumHeight(220)
        window.account_summary.setContentsMargins(3, 0, 3, 0)
        window.strike_ladder = StrikeLadderWidget(window.real_kite_client)
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
        menu_actions["fii_dii_data"].triggered.connect(window._show_fii_dii_dialog)

    @staticmethod
    def style_splitter(splitter: QSplitter):
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            """
            QSplitter::handle {
                background-color: #2A3140;
                border: none;
            }
            QSplitter::handle:hover {
                background-color: #3A4458;
            }
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
