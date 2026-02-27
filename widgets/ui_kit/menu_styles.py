"""Shared styling for application menu bars and dropdown menus."""

APP_MENU_STYLESHEET = """
QMenuBar {
    background-color: transparent;
    color: #DDE5F0;
    border: none;
    font-size: 12px;
    padding: 2px 0px;
}

QMenuBar::item {
    background-color: transparent;
    padding: 4px 10px;
    border-radius: 4px;
    margin: 0px 2px;
    font-weight: 600;
}

QMenuBar::item:selected {
    background-color: #1D2A3E;
    color: #DDE5F0;
}

QMenuBar::item:pressed {
    background-color: #192333;
    color: #FFFFFF;
}

QMenu {
    background-color: #141C28;
    color: #DDE5F0;
    border: 1px solid #2A3F5A;
    border-radius: 6px;
    padding: 6px;
}

QMenu::item {
    padding: 8px 30px 8px 22px;
    margin: 2px 4px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #192333;
    color: #00C9AD;
}

QMenu::separator {
    height: 1px;
    background-color: #1E2D42;
    margin: 6px 12px;
}
"""
