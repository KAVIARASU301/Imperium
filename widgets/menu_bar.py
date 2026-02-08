# core/menu_bar.py

from PySide6.QtWidgets import QMenuBar
from PySide6.QtGui import QAction
from typing import Dict, Tuple


def create_menu_bar(parent) -> Tuple[QMenuBar, Dict[str, QAction]]:
    """
    Premium, strong menu bar designed to anchor the application visually
    and align with the header toolbar.
    """
    menubar = QMenuBar(parent)

    menubar.setStyleSheet("""
        /* ===== MENU BAR CORE ===== */
        QMenuBar {
            background-color: qlineargradient(
                x1:0, y1:0, x2:0, y2:1,
                stop:0 #22293A,
                stop:1 #151A26
            );
            
            color: #C3CAD8;
            border-bottom: 1px solid #0D0F15;
            padding: 4px 8px;
            font-family: "Segoe UI";
            font-size: 12px;
        }

        /* Secondary depth line (gives weight) */
        QMenuBar::separator {
            background: #2A3140;
        }

        /* ===== TOP-LEVEL MENU ITEMS ===== */
        QMenuBar::item {
            padding: 6px 14px;
            margin: 0px 3px;
            border-radius: 6px;
            background: transparent;
            font-weight: 600;
        }

        QMenuBar::item:selected {
            background-color: rgba(41, 199, 201, 0.14);
            color: #FFFFFF;
        }

        /* ===== DROPDOWN MENUS ===== */
        QMenu {
            background-color: #1B2130;
            color: #E0E0E0;
            border: 1px solid #2A3140;
            border-radius: 6px;
            padding: 6px;
        }

        QMenu::item {
            padding: 8px 30px 8px 22px;
            margin: 2px 4px;
            border-radius: 5px;
        }

        QMenu::item:selected {
            background-color: #29C7C9;
            color: #161A25;
        }

        QMenu::separator {
            height: 1px;
            background-color: #3A4458;
            margin: 6px 12px;
        }
    """)

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
    menu_actions["positions"] = view_menu.addAction("Open Positions")
    menu_actions["pending_orders"] = view_menu.addAction("Pending Orders")
    menu_actions["orders"] = view_menu.addAction("Order History")
    menu_actions["pnl_history"] = view_menu.addAction("P&L History")
    menu_actions["performance"] = view_menu.addAction("Performance")

    # -------- TOOLS --------
    tools_menu = menubar.addMenu("&Tools")

    menu_actions["market_monitor"] = tools_menu.addAction("Market Monitor")
    menu_actions["market_monitor"].setShortcut("Ctrl+M")

    menu_actions["cvd_chart"] = tools_menu.addAction("CVD Single Chart")
    menu_actions["cvd_chart"].setShortcut("Ctrl+C")

    menu_actions["cvd_market_monitor"] = tools_menu.addAction("CVD Multi Chart")
    menu_actions["cvd_market_monitor"].setShortcut("Ctrl+D")

    menu_actions["cvd_symbol_sets"] = tools_menu.addAction("CVD Symbol Sets")
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
