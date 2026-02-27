# core/menu_bar.py

from PySide6.QtWidgets import QMenuBar
from PySide6.QtGui import QAction
from typing import Dict, Tuple

from widgets.ui_kit.menu_styles import APP_MENU_STYLESHEET


def create_menu_bar(parent) -> Tuple[QMenuBar, Dict[str, QAction]]:
    """
    Premium, strong menu bar designed to anchor the application visually
    and align with the header toolbar.
    """
    menubar = QMenuBar(parent)

    menubar.setStyleSheet(APP_MENU_STYLESHEET)

    menu_actions: Dict[str, QAction] = {}

    # -------- FILE --------
    file_menu = menubar.addMenu("&File")

    menu_actions["refresh"] = file_menu.addAction("Refresh Data")
    menu_actions["refresh"].setShortcut("F5")

    menu_actions["refresh_positions"] = file_menu.addAction("Refresh Positions")
    menu_actions["refresh_positions"].setShortcut("Ctrl+R")

    file_menu.addSeparator()

    menu_actions["exit"] = file_menu.addAction("Exit")
    menu_actions["exit"].setShortcut("Ctrl+Q")

    # -------- VIEW --------
    view_menu = menubar.addMenu("&View")
    menu_actions["watchlist"] = view_menu.addAction("Watchlist")
    menu_actions["watchlist"].setShortcut("Ctrl+Shift+W")
    menu_actions["positions"] = view_menu.addAction("Open Positions")
    menu_actions["pending_orders"] = view_menu.addAction("Pending Orders")
    menu_actions["orders"] = view_menu.addAction("Order History")
    menu_actions["pnl_history"] = view_menu.addAction("P&L History")
    menu_actions["performance"] = view_menu.addAction("Performance")

    # -------- TOOLS --------
    tools_menu = menubar.addMenu("&Tools")

    menu_actions["market_monitor"] = tools_menu.addAction("Market Monitor")
    menu_actions["market_monitor"].setShortcut("Ctrl+M")

    menu_actions["cvd_chart"] = tools_menu.addAction("Auto Trader")
    menu_actions["cvd_chart"].setShortcut("Ctrl+C")

    menu_actions["cvd_market_monitor"] = tools_menu.addAction("CVD Index Chart")
    menu_actions["cvd_market_monitor"].setShortcut("Ctrl+D")

    menu_actions["cvd_symbol_sets"] = tools_menu.addAction("CVD Multi Symbol Chart")
    menu_actions["cvd_symbol_sets"].setShortcut("Ctrl+Shift+D")

    menu_actions["option_chain"] = QAction("Option Chain", parent)
    menu_actions["option_chain"].setShortcut("Ctrl+O")

    tools_menu.addAction(menu_actions["option_chain"])

    menu_actions["strategy_builder"] = tools_menu.addAction("Strategy Builder")
    menu_actions["strategy_builder"].setShortcut("Ctrl+Shift+B")

    tools_menu.addSeparator()

    menu_actions["fii_dii_data"] = tools_menu.addAction("FII DII Data")
    menu_actions["fii_dii_data"].setShortcut("Ctrl+F")

    menu_actions["settings"] = tools_menu.addAction("Settings")
    menu_actions["settings"].setShortcut("Ctrl+,")

    # -------- HELP --------
    help_menu = menubar.addMenu("&Help")
    menu_actions["shortcuts"] = help_menu.addAction("Keyboard Shortcuts")
    menu_actions["expiry_days"] = help_menu.addAction("Expiry Days")
    menu_actions["about"] = help_menu.addAction("About")

    return menubar, menu_actions
