# widgets/symbol_picker.py
import logging
from typing import List
from collections import defaultdict
from PySide6.QtCore import QSettings

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

logger = logging.getLogger(__name__)


class SymbolPickerPopup(QWidget):
    """
    Custom dropdown replacement showing symbols grouped alphabetically
    with horizontal flow within each letter group.
    """
    symbol_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.symbols = []
        self.index_symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
        self.recent_symbols: List[str] = []
        self._last_selected_symbol: str | None = None
        self._visible_buttons: List[QPushButton] = []
        self._highlight_index = -1
        self._settings = QSettings("ImperiumDesk", "SymbolPicker")
        self._load_recent_symbols()

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        # Main container with border/shadow
        container = QFrame(self)
        container.setObjectName("popupContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        # Content layout
        content_layout = QVBoxLayout(container)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(8)

        # Search box
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search symbols...")
        self.search_input.textChanged.connect(self._filter_symbols)
        content_layout.addWidget(self.search_input)

        # Scrollable area for symbols
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setObjectName("symbolScroll")

        self.symbols_widget = QWidget()
        self.symbols_layout = QVBoxLayout(self.symbols_widget)
        self.symbols_layout.setContentsMargins(0, 0, 0, 0)
        self.symbols_layout.setSpacing(10)
        self.symbols_layout.setAlignment(Qt.AlignTop)

        scroll.setWidget(self.symbols_widget)
        content_layout.addWidget(scroll)

        self.setFixedSize(480, 420)

    def _load_recent_symbols(self):
        stored = self._settings.value("recent_symbols", [])
        if isinstance(stored, list):
            self.recent_symbols = stored[:4]
        else:
            self.recent_symbols = []

    def _save_recent_symbols(self):
        self._settings.setValue("recent_symbols", self.recent_symbols[:4])

    def set_symbols(self, symbols: List[str]):
        """Populate the picker with symbols grouped alphabetically."""
        self.symbols = sorted(symbols)
        self._render_symbols(self.symbols)

    def _render_symbols(self, symbols: List[str]):
        """Render index symbols first, then filtered symbols."""
        self._visible_buttons.clear()
        self._highlight_index = -1

        while self.symbols_layout.count():
            item = self.symbols_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # ---------- RECENT SYMBOLS ----------
        if self.recent_symbols:
            header = QLabel("RECENT")
            header.setObjectName("groupHeader")
            self.symbols_layout.addWidget(header)

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            for sym in self.recent_symbols:
                if sym in self.symbols:
                    btn = self._create_symbol_button(sym)
                    row_layout.addWidget(btn)

            row_layout.addStretch()
            self.symbols_layout.addWidget(row)

        # ---------- INDEX SYMBOLS (PINNED) ----------
        index_present = [s for s in self.index_symbols if s in self.symbols]

        if index_present:
            header = QLabel("INDEX")
            header.setObjectName("groupHeader")
            self.symbols_layout.addWidget(header)

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            for sym in index_present:
                btn = self._create_symbol_button(sym)
                row_layout.addWidget(btn)

            row_layout.addStretch()
            self.symbols_layout.addWidget(row)

        # ---------- NORMAL SYMBOLS ----------
        alpha_groups = defaultdict(list)
        other_symbols = []

        for symbol in symbols:
            if symbol in self.index_symbols:
                continue

            first_char = symbol[0].upper()
            if first_char.isalpha():
                alpha_groups[first_char].append(symbol)
            else:
                other_symbols.append(symbol)

        # ---------- ALPHABETIC SYMBOLS (A–Z) ----------
        for letter in sorted(alpha_groups.keys()):
            header = QLabel(letter)
            header.setObjectName("groupHeader")
            self.symbols_layout.addWidget(header)

            flow = QWidget()
            flow_layout = QHBoxLayout(flow)
            flow_layout.setContentsMargins(0, 0, 0, 0)
            flow_layout.setSpacing(8)

            for i, symbol in enumerate(alpha_groups[letter]):
                if i > 0 and i % 4 == 0:
                    flow_layout.addStretch()
                    self.symbols_layout.addWidget(flow)
                    flow = QWidget()
                    flow_layout = QHBoxLayout(flow)
                    flow_layout.setContentsMargins(0, 0, 0, 0)
                    flow_layout.setSpacing(8)

                btn = self._create_symbol_button(symbol)
                flow_layout.addWidget(btn)

            flow_layout.addStretch()
            self.symbols_layout.addWidget(flow)

        # ---------- OTHER SYMBOLS (NUMERIC / SPECIAL) ----------
        if other_symbols:
            header = QLabel("OTHERS")
            header.setObjectName("groupHeader")
            self.symbols_layout.addWidget(header)
    
            flow = QWidget()
            flow_layout = QHBoxLayout(flow)
            flow_layout.setContentsMargins(0, 0, 0, 0)
            flow_layout.setSpacing(8)

            for i, symbol in enumerate(sorted(other_symbols)):
                if i > 0 and i % 4 == 0:
                    flow_layout.addStretch()
                    self.symbols_layout.addWidget(flow)
                    flow = QWidget()
                    flow_layout = QHBoxLayout(flow)
                    flow_layout.setContentsMargins(0, 0, 0, 0)
                    flow_layout.setSpacing(8)

                btn = self._create_symbol_button(symbol)
                flow_layout.addWidget(btn)

            flow_layout.addStretch()
            self.symbols_layout.addWidget(flow)

    def _create_symbol_button(self, symbol: str) -> QPushButton:
        btn = QPushButton(symbol)
        btn.setObjectName("symbolButton")
        btn.setFixedWidth(105)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(lambda _, s=symbol: self._select_symbol(s))

        self._visible_buttons.append(btn)
        return btn

    def _filter_symbols(self, text: str):
        query = text.strip().upper()

        self._is_search_active = bool(query)

        if not query:
            self._render_symbols(self.symbols)
            self._highlight_index = 0
            self._update_highlight()
            return

        matched = [s for s in self.symbols if s.startswith(query)]
        self._render_symbols(matched)

        # 🔑 Highlight first NON-INDEX result
        self._highlight_first_search_result()

    def _highlight_first_search_result(self):
        """
        When searching, skip INDEX symbols and highlight
        the first real matching symbol.
        """
        for i, btn in enumerate(self._visible_buttons):
            if btn.text() not in self.index_symbols:
                self._highlight_index = i
                self._update_highlight()
                return

    def _select_symbol(self, symbol: str):
        # ---- FIX UI SELECTION ARTIFACT ----
        self.search_input.deselect()
        self.search_input.clearFocus()
        self.setFocus(Qt.NoFocusReason)

        # ---- RECENT SYMBOL TRACKING ----
        if symbol in self.recent_symbols:
            self.recent_symbols.remove(symbol)

        self.recent_symbols.insert(0, symbol)
        self.recent_symbols = self.recent_symbols[:4]
        self._save_recent_symbols()

        # ---- PERSIST LAST SELECTION ----
        self._last_selected_symbol = symbol

        # ---- EMIT + CLOSE ----
        self.symbol_selected.emit(symbol)
        self.close()

    def show_below(self, parent_widget: QWidget):
        parent_pos = parent_widget.mapToGlobal(parent_widget.rect().bottomLeft())
        self.move(parent_pos.x(), parent_pos.y() + 2)
        self.show()

        self.search_input.clear()  # 🔥 reset search
        self.search_input.setFocus()

        self.search_input.setCursorPosition(len(self.search_input.text()))
        self.search_input.deselect()

        # ---- RESTORE LAST SELECTED HIGHLIGHT ----
        if self._last_selected_symbol:
            for i, btn in enumerate(self._visible_buttons):
                if btn.text() == self._last_selected_symbol:
                    self._highlight_index = i
                    self._update_highlight()
                    break

    def keyPressEvent(self, event):
        if not self._visible_buttons:
            return

        if event.key() == Qt.Key_Down:
            next_index = min(self._highlight_index + 1, len(self._visible_buttons) - 1)

            # Skip index symbols while searching
            if getattr(self, "_is_search_active", False):
                while (
                        next_index < len(self._visible_buttons)
                        and self._visible_buttons[next_index].text() in self.index_symbols
                ):
                    next_index += 1

            self._highlight_index = min(next_index, len(self._visible_buttons) - 1)
            self._update_highlight()
            return

        if event.key() == Qt.Key_Up:
            self._highlight_index = max(self._highlight_index - 1, 0)
            self._update_highlight()
            return

        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if 0 <= self._highlight_index < len(self._visible_buttons):
                self._visible_buttons[self._highlight_index].click()
            return

        super().keyPressEvent(event)

    def _update_highlight(self):
        """
        Update keyboard selection highlight using a dynamic
        Qt property instead of inline styles.
        """
        for i, btn in enumerate(self._visible_buttons):
            is_selected = (i == self._highlight_index)
            btn.setProperty("kb_selected", is_selected)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _apply_styles(self):
        self.setStyleSheet("""
            #popupContainer {
                background-color: #1A1F2E;
                border: 1px solid #3A4458;
                border-radius: 8px;
            }

            QLineEdit {
                background-color: #212635;
                border: 1px solid #3A4458;
                border-radius: 6px;
                color: #E0E0E0;
                padding: 8px 12px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #29C7C9;
            }

            #symbolScroll {
                background-color: transparent;
                border: none;
            }

            #groupHeader {
                color: #29C7C9;
                font-size: 14px;
                font-weight: bold;
                padding: 4px 0px;
            }

            #symbolButton {
                background-color: #212635;
                color: #E0E0E0;
                border: 1px solid #3A4458;
                border-radius: 5px;
                padding: 8px 12px;
                font-size: 12px;
                font-weight: 500;
                text-align: left;
            }
            #symbolButton:hover {
                background-color: #2A3144;
                border-color: #29C7C9;
                color: #FFFFFF;
            }
            #symbolButton:pressed {
                background-color: #29C7C9;
                color: #161A25;
            }
            #symbolButton[kb_selected="true"] {
                background-color: #29C7C9;
                color: #161A25;
                border-color: #29C7C9;
            }

            QScrollBar:vertical {
                background-color: #1A1F2E;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background-color: #3A4458;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #4A5568;
            }
        """)