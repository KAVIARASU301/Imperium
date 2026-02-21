from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtCore import QObject, Slot, Signal, QUrl, Qt
from PySide6.QtGui import QFont
import sys
import json


# ------------------------------------------------------------
# Bridge: Python <-> JavaScript Communication
# ------------------------------------------------------------

class DropdownBridge(QObject):
    """Bridge for bidirectional communication between Python and HTML/JS"""

    # Signal to send data from Python to JavaScript
    updateItems = Signal(str)

    def __init__(self):
        super().__init__()
        self._selected_value = None
        self._on_change_callback = None

    @Slot(str)
    def onSelectionChanged(self, value):
        """Called from JavaScript when user selects an item"""
        self._selected_value = value
        print(f"Python received: {value}")

        if self._on_change_callback:
            self._on_change_callback(value)

    @Slot(str)
    def onSearchQuery(self, query):
        """Called from JavaScript when user types in search"""
        print(f"Search query: {query}")

    def set_on_change(self, callback):
        """Set Python callback for selection changes"""
        self._on_change_callback = callback

    def get_selected_value(self):
        """Get currently selected value"""
        return self._selected_value

    def update_items_from_python(self, items):
        """Update dropdown items from Python"""
        items_json = json.dumps(items)
        self.updateItems.emit(items_json)


# ------------------------------------------------------------
# HTML/CSS/JS Dropdown Component
# ------------------------------------------------------------

HTML_DROPDOWN = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: transparent;
            padding: 20px;
            overflow: hidden;
        }

        .dropdown-container {
            position: relative;
            width: 100%;
            max-width: 400px;
        }

        .dropdown-trigger {
            background: linear-gradient(135deg, #1A2332 0%, #141C28 100%);
            color: #E8F0FF;
            border: 1px solid #2A3B4C;
            border-radius: 10px;
            padding: 14px 40px 14px 18px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            user-select: none;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .dropdown-trigger:hover {
            border-color: #4A9EFF;
            background: linear-gradient(135deg, #1E2838 0%, #16202E 100%);
            box-shadow: 0 4px 12px rgba(74, 158, 255, 0.15);
        }

        .dropdown-trigger.open {
            border-color: #4A9EFF;
            border-bottom-left-radius: 0;
            border-bottom-right-radius: 0;
        }

        .dropdown-icon {
            position: absolute;
            right: 16px;
            top: 50%;
            transform: translateY(-50%);
            width: 0;
            height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #8B9DC3;
            transition: all 0.3s ease;
        }

        .dropdown-trigger.open .dropdown-icon {
            transform: translateY(-50%) rotate(180deg);
            border-top-color: #4A9EFF;
        }

        .dropdown-menu {
            position: absolute;
            top: 100%;
            left: 0;
            right: 0;
            background: #0A0E17;
            border: 1px solid #2A3B4C;
            border-top: none;
            border-radius: 0 0 10px 10px;
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
            z-index: 1000;
        }

        .dropdown-menu.open {
            max-height: 400px;
        }

        .search-box {
            padding: 12px;
            border-bottom: 1px solid #1A2332;
            position: sticky;
            top: 0;
            background: #0A0E17;
            z-index: 10;
        }

        .search-input {
            width: 100%;
            background: #0F1419;
            color: #E8F0FF;
            border: 1px solid #2A3B4C;
            border-radius: 6px;
            padding: 10px 12px;
            font-size: 13px;
            outline: none;
            transition: all 0.2s ease;
        }

        .search-input:focus {
            border-color: #4A9EFF;
            background: #131920;
        }

        .search-input::placeholder {
            color: #5A6B7D;
        }

        .dropdown-items {
            max-height: 288px;
            overflow-y: auto;
            overflow-x: hidden;
        }

        /* Custom Scrollbar */
        .dropdown-items::-webkit-scrollbar {
            width: 8px;
        }

        .dropdown-items::-webkit-scrollbar-track {
            background: transparent;
        }

        .dropdown-items::-webkit-scrollbar-thumb {
            background: #2A3B4C;
            border-radius: 4px;
        }

        .dropdown-items::-webkit-scrollbar-thumb:hover {
            background: #4A9EFF;
        }

        .dropdown-item {
            padding: 12px 18px;
            color: #E8F0FF;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 13px;
            border-left: 3px solid transparent;
        }

        .dropdown-item:hover {
            background: #1A2332;
            border-left-color: #4A9EFF;
        }

        .dropdown-item.selected {
            background: linear-gradient(90deg, #1E3A5F 0%, #2A4A6F 100%);
            border-left-color: #4A9EFF;
        }

        .dropdown-item.hidden {
            display: none;
        }

        .no-results {
            padding: 20px;
            text-align: center;
            color: #5A6B7D;
            font-size: 13px;
        }

        /* Animations */
        @keyframes fadeIn {
            from {
                opacity: 0;
                transform: translateY(-10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        .dropdown-menu.open .dropdown-item {
            animation: fadeIn 0.3s ease forwards;
        }

        .dropdown-menu.open .dropdown-item:nth-child(1) { animation-delay: 0.05s; }
        .dropdown-menu.open .dropdown-item:nth-child(2) { animation-delay: 0.08s; }
        .dropdown-menu.open .dropdown-item:nth-child(3) { animation-delay: 0.11s; }
        .dropdown-menu.open .dropdown-item:nth-child(4) { animation-delay: 0.14s; }
        .dropdown-menu.open .dropdown-item:nth-child(5) { animation-delay: 0.17s; }
    </style>
</head>
<body>
    <div class="dropdown-container">
        <div class="dropdown-trigger" id="dropdownTrigger">
            <span id="selectedText">Select an option</span>
            <div class="dropdown-icon"></div>
        </div>

        <div class="dropdown-menu" id="dropdownMenu">
            <div class="search-box">
                <input 
                    type="text" 
                    class="search-input" 
                    id="searchInput" 
                    placeholder="🔍 Search..."
                    autocomplete="off"
                >
            </div>
            <div class="dropdown-items" id="dropdownItems">
                <!-- Items populated by JavaScript -->
            </div>
        </div>
    </div>

    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <script>
        let bridge = null;
        let items = [
            "NIFTY 50",
            "BANK NIFTY",
            "FIN NIFTY",
            "NIFTY IT",
            "NIFTY PHARMA",
            "NIFTY AUTO",
            "NIFTY METAL",
            "NIFTY MEDIA",
            "NIFTY REALTY",
            "NIFTY ENERGY",
            "SENSEX",
            "MIDCAP NIFTY"
        ];

        let selectedValue = null;

        // Initialize WebChannel connection to Python
        new QWebChannel(qt.webChannelTransport, function(channel) {
            bridge = channel.objects.bridge;

            // Listen for updates from Python
            bridge.updateItems.connect(function(itemsJson) {
                items = JSON.parse(itemsJson);
                renderItems();
            });

            console.log("Bridge connected to Python");
        });

        const trigger = document.getElementById('dropdownTrigger');
        const menu = document.getElementById('dropdownMenu');
        const itemsContainer = document.getElementById('dropdownItems');
        const searchInput = document.getElementById('searchInput');
        const selectedText = document.getElementById('selectedText');

        // Toggle dropdown
        trigger.addEventListener('click', function(e) {
            e.stopPropagation();
            const isOpen = menu.classList.contains('open');

            if (isOpen) {
                closeDropdown();
            } else {
                openDropdown();
            }
        });

        function openDropdown() {
            trigger.classList.add('open');
            menu.classList.add('open');
            searchInput.focus();
            renderItems();
        }

        function closeDropdown() {
            trigger.classList.remove('open');
            menu.classList.remove('open');
            searchInput.value = '';
            renderItems();
        }

        // Close on outside click
        document.addEventListener('click', function(e) {
            if (!menu.contains(e.target) && e.target !== trigger) {
                closeDropdown();
            }
        });

        // Search functionality
        searchInput.addEventListener('input', function(e) {
            const query = e.target.value.toLowerCase();

            // Notify Python of search query
            if (bridge) {
                bridge.onSearchQuery(query);
            }

            filterItems(query);
        });

        function filterItems(query) {
            const itemElements = itemsContainer.querySelectorAll('.dropdown-item');
            let visibleCount = 0;

            itemElements.forEach(item => {
                const text = item.textContent.toLowerCase();
                if (text.includes(query)) {
                    item.classList.remove('hidden');
                    visibleCount++;
                } else {
                    item.classList.add('hidden');
                }
            });

            // Show "no results" message
            const noResults = itemsContainer.querySelector('.no-results');
            if (noResults) noResults.remove();

            if (visibleCount === 0) {
                itemsContainer.innerHTML = '<div class="no-results">No results found</div>';
            }
        }

        function renderItems() {
            itemsContainer.innerHTML = '';

            items.forEach(item => {
                const div = document.createElement('div');
                div.className = 'dropdown-item';
                if (item === selectedValue) {
                    div.classList.add('selected');
                }
                div.textContent = item;

                div.addEventListener('click', function(e) {
                    e.stopPropagation();
                    selectItem(item);
                });

                itemsContainer.appendChild(div);
            });
        }

        function selectItem(value) {
            selectedValue = value;
            selectedText.textContent = value;

            // Notify Python
            if (bridge) {
                bridge.onSelectionChanged(value);
            }

            closeDropdown();
        }

        // Initial render
        renderItems();
    </script>
</body>
</html>
"""


# ------------------------------------------------------------
# Qt Widget with Embedded HTML Dropdown
# ------------------------------------------------------------

class HTMLDropdownWidget(QWidget):
    """Qt Widget that embeds HTML/CSS/JS dropdown"""

    selectionChanged = Signal(str)

    def __init__(self, items=None, parent=None, width=400, height=400, item_height=36):
        super().__init__(parent)

        self.items = items or []
        self.dropdown_width = width
        self.dropdown_height = height
        self.item_height = item_height
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create WebEngineView
        self.web_view = QWebEngineView()
        self.web_view.setMinimumHeight(self.dropdown_height)

        # Setup WebChannel for Python-JS communication
        self.channel = QWebChannel()
        self.bridge = DropdownBridge()
        self.channel.registerObject("bridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        # Connect bridge signals
        self.bridge.set_on_change(self._on_selection_changed)

        # Load HTML content with custom dimensions
        html = self._get_html_with_dimensions()
        self.web_view.setHtml(html)

        layout.addWidget(self.web_view)

        # Update items after a short delay (wait for JS to initialize)
        if self.items:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, lambda: self.set_items(self.items))

    def _get_html_with_dimensions(self):
        """Generate HTML with custom width, height, and item height"""
        return HTML_DROPDOWN.replace(
            'max-width: 400px;',
            f'max-width: {self.dropdown_width}px; width: {self.dropdown_width}px;'
        ).replace(
            'padding: 12px 18px;',
            f'padding: {self.item_height // 3}px 18px;'
        ).replace(
            'max-height: 288px;',
            f'max-height: {self.item_height * 8}px;'
        )

    def _on_selection_changed(self, value):
        """Internal handler for selection changes"""
        self.selectionChanged.emit(value)

    def set_items(self, items):
        """Update dropdown items from Python"""
        self.items = items
        self.bridge.update_items_from_python(items)

    def get_selected_value(self):
        """Get currently selected value"""
        return self.bridge.get_selected_value()


# ------------------------------------------------------------
# Demo Application
# ------------------------------------------------------------

class DemoWindow(QWidget):
    """Demo showing HTML dropdown integration"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HTML/CSS Dropdown in Qt")
        self.setFixedSize(700, 800)

        # Dark theme
        self.setStyleSheet("""
            QWidget {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0A0E17,
                    stop:1 #1A1F2E
                );
            }
            QLabel {
                color: #E8F0FF;
                font-size: 14px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(40, 40, 40, 40)

        # Title
        title = QLabel("HTML/CSS Dropdown with Qt Integration")
        title_font = title.font()
        title_font.setPointSize(18)
        title_font.setWeight(QFont.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        # Description
        desc = QLabel("Full HTML/CSS/JS power + Python backend communication")
        desc.setStyleSheet("color: #8B9DC3; font-size: 12px;")
        layout.addWidget(desc)

        # HTML Dropdown - Default size
        items = [
            "NIFTY 50",
            "BANK NIFTY",
            "FIN NIFTY",
            "NIFTY IT",
            "NIFTY PHARMA",
            "NIFTY AUTO",
            "NIFTY METAL",
            "NIFTY MEDIA",
            "NIFTY REALTY",
            "NIFTY ENERGY",
            "SENSEX",
            "MIDCAP NIFTY"
        ]

        # Example 1: Compact dropdown (width=300, item_height=32)
        label1 = QLabel("Compact Dropdown (300px wide, 32px rows):")
        layout.addWidget(label1)

        self.dropdown1 = HTMLDropdownWidget(items, width=300, height=350, item_height=32)
        self.dropdown1.selectionChanged.connect(lambda v: self.on_dropdown_changed(v, "Dropdown 1"))
        layout.addWidget(self.dropdown1)

        layout.addSpacing(10)

        # Example 2: Wide dropdown (width=500, item_height=40)
        label2 = QLabel("Wide Dropdown (500px wide, 40px rows):")
        layout.addWidget(label2)

        self.dropdown2 = HTMLDropdownWidget(items, width=500, height=350, item_height=40)
        self.dropdown2.selectionChanged.connect(lambda v: self.on_dropdown_changed(v, "Dropdown 2"))
        layout.addWidget(self.dropdown2)

        # Selected value display
        self.result_label = QLabel("Selected: None")
        self.result_label.setStyleSheet("""
            color: #4A9EFF;
            font-size: 16px;
            font-weight: 600;
            padding: 10px;
            background: rgba(74, 158, 255, 0.1);
            border-radius: 6px;
        """)
        layout.addWidget(self.result_label)

        layout.addStretch()

    def on_dropdown_changed(self, value, source="Dropdown"):
        """Handle selection changes"""
        self.result_label.setText(f"{source} Selected: {value}")
        print(f"{source} changed to: {value}")


# ------------------------------------------------------------
# Launch
# ------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = DemoWindow()
    window.show()

    sys.exit(app.exec())