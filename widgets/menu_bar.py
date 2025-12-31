# core/menu_bar.py

from PySide6.QtWidgets import QMenuBar
from PySide6.QtGui import QAction
from typing import Dict, Tuple


def create_enhanced_menu_bar(parent) -> Tuple[QMenuBar, Dict[str, QAction]]:
    """Create and return a styled, compact menu bar with the premium dark theme."""
    menubar = QMenuBar(parent)
    menubar.setStyleSheet("""
        QMenuBar {
            background-color: #161A25; /* Match main background */
            color: #A9B1C3;
            border-bottom: 1px solid #2A3140;
            padding: 2px 4px; /* Reduced padding for a shorter bar */
            font-family: "Segoe UI";
            font-size: 12px;
        }
        QMenuBar::item {
            padding: 4px 10px; /* Reduced vertical padding */
            margin: 0px 1px;
            border-radius: 4px;
        }
        QMenuBar::item:selected {
            background-color: #212635;
            color: #FFFFFF;
        }
        QMenu {
            background-color: #212635;
            color: #E0E0E0;
            border: 1px solid #3A4458;
            padding: 5px;
        }
        QMenu::item {
            padding: 8px 25px; /* Standard padding for dropdown items */
            margin: 2px 3px;
            border-radius: 4px;
        }
        QMenu::item:selected {
            background-color: #29C7C9;
            color: #161A25;
        }
        QMenu::separator {
            height: 1px;
            background: #3A4458;
            margin: 5px 10px;
        }
    """)

    menu_actions = {}

    # --- File menu ---
    file_menu = menubar.addMenu("&File")
    menu_actions['refresh'] = file_menu.addAction("Refresh Data")
    menu_actions['refresh'].setShortcut("F5")
    menu_actions['refresh_positions'] = file_menu.addAction("Refresh Positions")
    menu_actions['refresh_positions'].setShortcut("Ctrl+R")
    file_menu.addSeparator()
    menu_actions['exit'] = file_menu.addAction("Exit")
    menu_actions['exit'].setShortcut("Ctrl+Q")

    # --- View menu ---
    view_menu = menubar.addMenu("&View")
    menu_actions['positions'] = view_menu.addAction("Open Positions")
    menu_actions['pending_orders'] = view_menu.addAction("Pending Orders")
    menu_actions['orders'] = view_menu.addAction("Order History")
    menu_actions['pnl_history'] = view_menu.addAction("P&L History")
    menu_actions['performance'] = view_menu.addAction("Performance")

    # --- Tools Menu ---
    tools_menu = menubar.addMenu("&Tools")
    menu_actions['market_monitor'] = tools_menu.addAction("Market Monitor")
    menu_actions['market_monitor'].setShortcut("Ctrl+M")
    menu_actions['cvd_chart'] = tools_menu.addAction("CVD Chart")
    menu_actions['cvd_chart'].setShortcut("Ctrl+C")
    menu_actions['cvd_market_monitor'] = tools_menu.addAction("CVD Multi Chart")
    menu_actions['cvd_market_monitor'].setShortcut("Ctrl+D")
    menu_actions['option_chain'] = QAction("Option Chain", parent)
    menu_actions['option_chain'].setShortcut("Ctrl+O")
    tools_menu.addAction(menu_actions['option_chain'])
    menu_actions['settings'] = tools_menu.addAction("Settings")
    menu_actions['settings'].setShortcut("Ctrl+,")
    # --- Help menu ---
    help_menu = menubar.addMenu("&Help")
    menu_actions['about'] = help_menu.addAction("About")

    return menubar, menu_actions