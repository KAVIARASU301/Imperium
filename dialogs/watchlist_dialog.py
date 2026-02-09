import logging
from typing import List, Dict, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QTabWidget, QWidget, QToolButton,
    QInputDialog, QMessageBox, QCompleter
)

from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class WatchlistGroup(QWidget):
    symbol_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("watchlistItems")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.setSpacing(2)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

    def _on_item_clicked(self, item: QListWidgetItem):
        if item:
            self.symbol_clicked.emit(item.text())

    def add_symbol(self, symbol: str) -> bool:
        if not symbol:
            return False
        existing = [
            self.list_widget.item(i).text()
            for i in range(self.list_widget.count())
        ]
        if symbol in existing:
            return False
        self.list_widget.addItem(symbol)
        return True

    def contains_symbol(self, symbol: str) -> bool:
        return any(
            self.list_widget.item(i).text() == symbol
            for i in range(self.list_widget.count())
        )


class WatchlistDialog(QDialog):
    symbol_selected = Signal(str)

    def __init__(self, symbols: List[str] | None = None, parent=None):
        super().__init__(parent)
        self.available_symbols = sorted(symbols or [])
        self._symbol_lookup: Dict[str, str] = {}
        self._update_symbol_lookup()
        self.config_manager = ConfigManager()

        self.setWindowTitle("Watchlist")
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setMinimumSize(320, 360)

        self._setup_ui()
        self._apply_styles()
        self._load_groups()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        title = QLabel("Watchlist")
        title.setObjectName("watchlistTitle")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))

        self.add_group_button = QToolButton()
        self.add_group_button.setText("+")
        self.add_group_button.setObjectName("addGroupButton")
        self.add_group_button.setToolTip("Create a new watchlist group")
        self.add_group_button.clicked.connect(self._create_group_prompt)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.add_group_button)

        search_layout = QHBoxLayout()
        search_layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search symbol and press Enter...")
        self.search_input.returnPressed.connect(self._handle_add_symbol)
        self.search_input.setObjectName("watchlistSearch")

        self.add_button = QPushButton("Add")
        self.add_button.setObjectName("watchlistAddButton")
        self.add_button.clicked.connect(self._handle_add_symbol)
        self.add_button.setFixedHeight(28)

        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.add_button)

        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("watchlistTabs")
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_group)
        self.tab_widget.tabBarDoubleClicked.connect(self._rename_group)

        main_layout.addLayout(header_layout)
        main_layout.addLayout(search_layout)
        main_layout.addWidget(self.tab_widget, 1)

        self._refresh_completer()

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #121722;
                color: #E3E8F0;
                border: 1px solid #2A3140;
                border-radius: 8px;
            }

            QLabel#watchlistTitle {
                color: #F1F5FF;
                letter-spacing: 0.5px;
            }

            QToolButton#addGroupButton {
                background-color: #20283A;
                color: #9EE3E3;
                border: 1px solid #2E3A52;
                border-radius: 8px;
                font-size: 14px;
                padding: 2px 8px;
            }

            QToolButton#addGroupButton:hover {
                background-color: #29C7C9;
                color: #0F1117;
            }

            QLineEdit#watchlistSearch {
                background-color: #151C2B;
                border: 1px solid #2A3140;
                border-radius: 6px;
                padding: 6px 10px;
                color: #E3E8F0;
                font-size: 11px;
            }

            QLineEdit#watchlistSearch:focus {
                border: 1px solid #29C7C9;
                background-color: #1A2334;
            }

            QPushButton#watchlistAddButton {
                background-color: #29C7C9;
                color: #0F1117;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
            }

            QPushButton#watchlistAddButton:hover {
                background-color: #36D6D6;
            }

            QTabWidget#watchlistTabs::pane {
                border: 1px solid #2A3140;
                border-radius: 8px;
                background-color: #121722;
                padding: 2px;
            }

            QTabBar::tab {
                background-color: #1A2233;
                color: #AEB6C7;
                border: 1px solid #2A3140;
                padding: 4px 10px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 3px;
                font-weight: 600;
                font-size: 10.5px;
            }

            QTabBar::tab:selected {
                background-color: #29C7C9;
                color: #0F1117;
                border: 1px solid #29C7C9;
            }

            QListWidget#watchlistItems {
                background-color: #101621;
                border: none;
                padding: 4px;
            }

            QListWidget#watchlistItems::item {
                background-color: #151C2B;
                color: #E3E8F0;
                padding: 6px 10px;
                margin: 4px 2px;
                border-radius: 6px;
                border: 1px solid #1F2937;
                font-size: 11px;
            }

            QListWidget#watchlistItems::item:selected {
                background-color: #223449;
                border: 1px solid #29C7C9;
                color: #CFFBFB;
            }
        """)

    def _create_default_group(self):
        if self.tab_widget.count() == 0:
            self._add_group("Core", persist=False)

    def _add_group(self, name: str, persist: bool = True):
        group = WatchlistGroup(self)
        group.symbol_clicked.connect(self.symbol_selected.emit)
        self.tab_widget.addTab(group, name)
        self.tab_widget.setCurrentWidget(group)
        if persist:
            self._persist_groups()

    def _create_group_prompt(self):
        default_name = f"Group {self.tab_widget.count() + 1}"
        name, ok = QInputDialog.getText(
            self,
            "New Watchlist Group",
            "Name your group:",
            text=default_name
        )
        if ok and name.strip():
            self._add_group(name.strip())

    def _rename_group(self, index: int):
        if index < 0:
            return
        current_name = self.tab_widget.tabText(index)
        name, ok = QInputDialog.getText(
            self,
            "Rename Group",
            "Update group name:",
            text=current_name
        )
        if ok and name.strip():
            self.tab_widget.setTabText(index, name.strip())
            self._persist_groups()

    def _close_group(self, index: int):
        if self.tab_widget.count() <= 1:
            QMessageBox.information(self, "Watchlist", "At least one watchlist group is required.")
            return
        widget = self.tab_widget.widget(index)
        self.tab_widget.removeTab(index)
        if widget:
            widget.deleteLater()
        self._persist_groups()

    def _update_symbol_lookup(self):
        self._symbol_lookup = {symbol.upper(): symbol for symbol in self.available_symbols}

    def _refresh_completer(self):
        completer = QCompleter(self.available_symbols, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search_input.setCompleter(completer)

    def set_symbols(self, symbols: List[str]):
        self.available_symbols = sorted(symbols or [])
        self._update_symbol_lookup()
        self._refresh_completer()

    def _resolve_symbol(self, text: str) -> str | None:
        symbol = text.strip().upper()
        if not symbol:
            return None
        return self._symbol_lookup.get(symbol)

    def _handle_add_symbol(self):
        text = self.search_input.text()
        symbol = self._resolve_symbol(text)
        if not symbol:
            QMessageBox.warning(self, "Watchlist", "Please enter a valid symbol from the list.")
            return
        group = self.tab_widget.currentWidget()
        if not isinstance(group, WatchlistGroup):
            return
        if group.contains_symbol(symbol):
            QMessageBox.information(self, "Watchlist", f"{symbol} is already in this group.")
            return
        group.add_symbol(symbol)
        self.search_input.clear()
        logger.info("Added %s to watchlist group %s", symbol, self.tab_widget.tabText(self.tab_widget.currentIndex()))
        self._persist_groups()

    def _load_groups(self):
        settings = self.config_manager.load_settings()
        groups = settings.get("watchlist_groups", [])
        if not groups:
            self._create_default_group()
            self._persist_groups()
            return
        for group in groups:
            name = group.get("name", "Group")
            symbols = group.get("symbols", [])
            self._add_group(name, persist=False)
            current_group = self.tab_widget.currentWidget()
            if isinstance(current_group, WatchlistGroup):
                for symbol in symbols:
                    if symbol in self.available_symbols or not self.available_symbols:
                        current_group.add_symbol(symbol)
        self._persist_groups()

    def _collect_groups(self) -> List[Dict[str, Any]]:
        groups = []
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if not isinstance(widget, WatchlistGroup):
                continue
            symbols = [
                widget.list_widget.item(i).text()
                for i in range(widget.list_widget.count())
            ]
            groups.append({
                "name": self.tab_widget.tabText(index),
                "symbols": symbols
            })
        return groups

    def _persist_groups(self):
        settings = self.config_manager.load_settings()
        settings["watchlist_groups"] = self._collect_groups()
        self.config_manager.save_settings(settings)

    def closeEvent(self, event):
        self._persist_groups()
        super().closeEvent(event)
