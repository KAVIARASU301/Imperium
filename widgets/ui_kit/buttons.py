from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtCore import QObject, Slot, Signal, Qt
from PySide6.QtGui import QFont
import sys
import json


# ------------------------------------------------------------
# Bridge: Python <-> JavaScript Communication
# ------------------------------------------------------------

class ButtonBridge(QObject):
    """Bridge for button click events from HTML to Python"""

    # Signal emitted when button is clicked
    buttonClicked = Signal(str, str)  # (button_id, button_type)

    def __init__(self):
        super().__init__()
        self._callbacks = {}

    @Slot(str, str)
    def onButtonClick(self, button_id, button_type):
        """Called from JavaScript when button is clicked"""
        print(f"Button clicked: {button_id} (type: {button_type})")
        self.buttonClicked.emit(button_id, button_type)

        if button_id in self._callbacks:
            self._callbacks[button_id]()

    def register_callback(self, button_id, callback):
        """Register Python callback for specific button"""
        self._callbacks[button_id] = callback


# ------------------------------------------------------------
# HTML/CSS Button System
# ------------------------------------------------------------

HTML_BUTTONS = """
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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Inter', sans-serif;
            background: transparent;
            padding: 20px;
            overflow-x: hidden;
        }

        .section {
            margin-bottom: 40px;
        }

        .section-title {
            color: #4A9EFF;
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 15px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .button-row {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            align-items: center;
        }

        /* ============================================
           BASE BUTTON STYLES
        ============================================ */

        .btn {
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            border: none;
            outline: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            user-select: none;
            position: relative;
            overflow: hidden;
        }

        .btn:active {
            transform: scale(0.97);
        }

        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        /* ============================================
           PRIMARY BUTTONS
        ============================================ */

        .btn-primary {
            background: linear-gradient(135deg, #4A9EFF 0%, #357ABD 100%);
            color: #FFFFFF;
            box-shadow: 0 4px 12px rgba(74, 158, 255, 0.3);
        }

        .btn-primary:hover {
            background: linear-gradient(135deg, #5AAAFF 0%, #4A9EFF 100%);
            box-shadow: 0 6px 16px rgba(74, 158, 255, 0.4);
            transform: translateY(-1px);
        }

        .btn-primary:active {
            transform: translateY(0) scale(0.97);
        }

        /* ============================================
           SECONDARY BUTTONS
        ============================================ */

        .btn-secondary {
            background: rgba(26, 35, 50, 0.8);
            color: #E8F0FF;
            border: 1px solid #2A3B4C;
        }

        .btn-secondary:hover {
            background: rgba(42, 59, 76, 0.9);
            border-color: #4A9EFF;
            box-shadow: 0 4px 12px rgba(74, 158, 255, 0.2);
        }

        /* ============================================
           GHOST/OUTLINED BUTTONS
        ============================================ */

        .btn-ghost {
            background: transparent;
            color: #8B9DC3;
            border: 1px solid #3A4B5C;
        }

        .btn-ghost:hover {
            background: rgba(74, 158, 255, 0.1);
            border-color: #4A9EFF;
            color: #4A9EFF;
        }

        /* ============================================
           SUCCESS BUTTONS
        ============================================ */

        .btn-success {
            background: linear-gradient(135deg, #4CAF50 0%, #388E3C 100%);
            color: #FFFFFF;
            box-shadow: 0 4px 12px rgba(76, 175, 80, 0.3);
        }

        .btn-success:hover {
            background: linear-gradient(135deg, #66BB6A 0%, #4CAF50 100%);
            box-shadow: 0 6px 16px rgba(76, 175, 80, 0.4);
            transform: translateY(-1px);
        }

        /* ============================================
           DANGER/DESTRUCTIVE BUTTONS
        ============================================ */

        .btn-danger {
            background: linear-gradient(135deg, #F44336 0%, #D32F2F 100%);
            color: #FFFFFF;
            box-shadow: 0 4px 12px rgba(244, 67, 54, 0.3);
        }

        .btn-danger:hover {
            background: linear-gradient(135deg, #EF5350 0%, #F44336 100%);
            box-shadow: 0 6px 16px rgba(244, 67, 54, 0.4);
            transform: translateY(-1px);
        }

        /* ============================================
           WARNING BUTTONS
        ============================================ */

        .btn-warning {
            background: linear-gradient(135deg, #FF9800 0%, #F57C00 100%);
            color: #FFFFFF;
            box-shadow: 0 4px 12px rgba(255, 152, 0, 0.3);
        }

        .btn-warning:hover {
            background: linear-gradient(135deg, #FFA726 0%, #FF9800 100%);
            box-shadow: 0 6px 16px rgba(255, 152, 0, 0.4);
            transform: translateY(-1px);
        }

        /* ============================================
           MINIMAL/TEXT BUTTONS
        ============================================ */

        .btn-minimal {
            background: transparent;
            color: #8B9DC3;
            border: none;
            padding: 8px 16px;
        }

        .btn-minimal:hover {
            background: rgba(139, 157, 195, 0.1);
            color: #E8F0FF;
        }

        /* ============================================
           ICON BUTTONS (CLOSE, MINIMIZE, ETC)
        ============================================ */

        .btn-icon {
            width: 32px;
            height: 32px;
            padding: 0;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            font-weight: 600;
        }

        .btn-icon.close {
            background: transparent;
            color: #8B9DC3;
            border: none;
        }

        .btn-icon.close:hover {
            background: rgba(244, 67, 54, 0.2);
            color: #F44336;
        }

        .btn-icon.minimize {
            background: transparent;
            color: #8B9DC3;
            border: none;
        }

        .btn-icon.minimize:hover {
            background: rgba(74, 158, 255, 0.2);
            color: #4A9EFF;
        }

        .btn-icon.maximize {
            background: transparent;
            color: #8B9DC3;
            border: none;
        }

        .btn-icon.maximize:hover {
            background: rgba(76, 175, 80, 0.2);
            color: #4CAF50;
        }

        /* ============================================
           SIZE VARIANTS
        ============================================ */

        .btn-sm {
            padding: 6px 14px;
            font-size: 12px;
            border-radius: 6px;
        }

        .btn-lg {
            padding: 14px 28px;
            font-size: 16px;
            border-radius: 10px;
        }

        .btn-icon.btn-sm {
            width: 24px;
            height: 24px;
            font-size: 14px;
        }

        .btn-icon.btn-lg {
            width: 40px;
            height: 40px;
            font-size: 18px;
        }

        /* ============================================
           LOADING STATE
        ============================================ */

        .btn.loading {
            position: relative;
            color: transparent;
            pointer-events: none;
        }

        .btn.loading::after {
            content: '';
            position: absolute;
            width: 16px;
            height: 16px;
            top: 50%;
            left: 50%;
            margin-left: -8px;
            margin-top: -8px;
            border: 2px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 0.6s linear infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        /* ============================================
           GLASSMORPHIC STYLE
        ============================================ */

        .btn-glass {
            background: rgba(26, 35, 50, 0.6);
            border: 1px solid rgba(74, 158, 255, 0.3);
            color: #E8F0FF;
        }

        .btn-glass:hover {
            background: rgba(74, 158, 255, 0.2);
            border-color: #4A9EFF;
        }

        /* ============================================
           GRADIENT HOVER EFFECT
        ============================================ */

        .btn-gradient {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #FFFFFF;
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
        }

        .btn-gradient:hover {
            background: linear-gradient(135deg, #764ba2 0%, #667eea 100%);
            box-shadow: 0 6px 16px rgba(102, 126, 234, 0.4);
            transform: translateY(-1px);
        }

        /* ============================================
           RIPPLE EFFECT (MATERIAL DESIGN)
        ============================================ */

        .btn::before {
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            width: 0;
            height: 0;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.3);
            transform: translate(-50%, -50%);
            transition: width 0.6s, height 0.6s;
        }

        .btn:active::before {
            width: 300px;
            height: 300px;
        }

        /* ============================================
           UTILITY CLASSES
        ============================================ */

        .btn-block {
            width: 100%;
            display: flex;
            justify-content: center;
        }

        .btn-group {
            display: flex;
            gap: 0;
        }

        .btn-group .btn {
            border-radius: 0;
            border-right: none;
        }

        .btn-group .btn:first-child {
            border-radius: 8px 0 0 8px;
        }

        .btn-group .btn:last-child {
            border-radius: 0 8px 8px 0;
            border-right: 1px solid #2A3B4C;
        }

        /* ============================================
           DEMO LAYOUT
        ============================================ */

        .demo-label {
            display: inline-block;
            color: #5A6B7D;
            font-size: 11px;
            margin-left: 8px;
            font-family: 'Courier New', monospace;
        }
    </style>
</head>
<body>
    <!-- Close Buttons Section -->
    <div class="section">
        <div class="section-title">Close Buttons</div>
        <div class="button-row">
            <button class="btn btn-icon close" onclick="handleClick('close-1', 'close')">✕</button>
            <span class="demo-label">default</span>

            <button class="btn btn-icon btn-sm close" onclick="handleClick('close-2', 'close')">✕</button>
            <span class="demo-label">small</span>

            <button class="btn btn-icon btn-lg close" onclick="handleClick('close-3', 'close')">✕</button>
            <span class="demo-label">large</span>

            <button class="btn btn-icon minimize" onclick="handleClick('minimize-1', 'minimize')">−</button>
            <span class="demo-label">minimize</span>

            <button class="btn btn-icon maximize" onclick="handleClick('maximize-1', 'maximize')">□</button>
            <span class="demo-label">maximize</span>
        </div>
    </div>

    <!-- Primary Actions -->
    <div class="section">
        <div class="section-title">Primary Actions</div>
        <div class="button-row">
            <button class="btn btn-primary" onclick="handleClick('save', 'primary')">
                💾 Save
            </button>

            <button class="btn btn-primary" onclick="handleClick('submit', 'primary')">
                ✓ Submit
            </button>

            <button class="btn btn-primary btn-sm" onclick="handleClick('apply', 'primary')">
                Apply
            </button>

            <button class="btn btn-primary btn-lg" onclick="handleClick('continue', 'primary')">
                Continue →
            </button>
        </div>
    </div>

    <!-- Secondary Actions -->
    <div class="section">
        <div class="section-title">Secondary Actions</div>
        <div class="button-row">
            <button class="btn btn-secondary" onclick="handleClick('cancel', 'secondary')">
                Cancel
            </button>

            <button class="btn btn-ghost" onclick="handleClick('back', 'ghost')">
                ← Back
            </button>

            <button class="btn btn-minimal" onclick="handleClick('skip', 'minimal')">
                Skip
            </button>
        </div>
    </div>

    <!-- Status Buttons -->
    <div class="section">
        <div class="section-title">Status & Actions</div>
        <div class="button-row">
            <button class="btn btn-success" onclick="handleClick('confirm', 'success')">
                ✓ Confirm
            </button>

            <button class="btn btn-danger" onclick="handleClick('delete', 'danger')">
                🗑️ Delete
            </button>

            <button class="btn btn-warning" onclick="handleClick('warn', 'warning')">
                ⚠️ Warning
            </button>
        </div>
    </div>

    <!-- Special Styles -->
    <div class="section">
        <div class="section-title">Special Styles</div>
        <div class="button-row">
            <button class="btn btn-glass" onclick="handleClick('glass', 'glass')">
                Glass Effect
            </button>

            <button class="btn btn-gradient" onclick="handleClick('gradient', 'gradient')">
                Gradient Style
            </button>

            <button class="btn btn-primary loading">
                Loading...
            </button>
        </div>
    </div>

    <!-- Button Groups -->
    <div class="section">
        <div class="section-title">Button Groups</div>
        <div class="btn-group">
            <button class="btn btn-secondary" onclick="handleClick('left', 'group')">Left</button>
            <button class="btn btn-secondary" onclick="handleClick('center', 'group')">Center</button>
            <button class="btn btn-secondary" onclick="handleClick('right', 'group')">Right</button>
        </div>
    </div>

    <!-- Block Button -->
    <div class="section">
        <div class="section-title">Full Width Button</div>
        <button class="btn btn-primary btn-block btn-lg" onclick="handleClick('full-width', 'block')">
            Start Now →
        </button>
    </div>

    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <script>
        let bridge = null;

        // Initialize WebChannel connection to Python
        new QWebChannel(qt.webChannelTransport, function(channel) {
            bridge = channel.objects.bridge;
            console.log("Bridge connected to Python");
        });

        function handleClick(buttonId, buttonType) {
            console.log('Button clicked:', buttonId, buttonType);

            // Notify Python
            if (bridge) {
                bridge.onButtonClick(buttonId, buttonType);
            }
        }
    </script>
</body>
</html>
"""


# ------------------------------------------------------------
# Qt Widget with HTML Button System
# ------------------------------------------------------------

class HTMLButtonWidget(QWidget):
    """Widget embedding complete HTML button system"""

    buttonClicked = Signal(str, str)  # (button_id, button_type)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create WebEngineView
        self.web_view = QWebEngineView()
        self.web_view.setMinimumHeight(600)

        # Setup WebChannel for Python-JS communication
        self.channel = QWebChannel()
        self.bridge = ButtonBridge()
        self.channel.registerObject("bridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        # Connect bridge signals
        self.bridge.buttonClicked.connect(self._on_button_clicked)

        # Load HTML content
        self.web_view.setHtml(HTML_BUTTONS)

        layout.addWidget(self.web_view)

    def _on_button_clicked(self, button_id, button_type):
        """Handle button clicks from HTML"""
        self.buttonClicked.emit(button_id, button_type)

    def register_callback(self, button_id, callback):
        """Register Python callback for specific button"""
        self.bridge.register_callback(button_id, callback)


# ------------------------------------------------------------
# Standalone Button Component
# ------------------------------------------------------------

class StandaloneHTMLButton(QWidget):
    """Single button as a reusable component"""

    clicked = Signal()

    def __init__(self, text="Button", button_type="primary", size="md", parent=None):
        super().__init__(parent)

        self.button_text = text
        self.button_type = button_type  # primary, secondary, ghost, success, danger, etc.
        self.button_size = size  # sm, md, lg

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create WebEngineView
        self.web_view = QWebEngineView()

        # Calculate height based on size
        heights = {"sm": 40, "md": 50, "lg": 65}
        self.web_view.setFixedHeight(heights.get(self.button_size, 50))

        # Setup WebChannel
        self.channel = QWebChannel()
        self.bridge = ButtonBridge()
        self.channel.registerObject("bridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        # Connect
        self.bridge.buttonClicked.connect(lambda _, __: self.clicked.emit())

        # Generate HTML for single button
        size_class = f"btn-{self.button_size}" if self.button_size != "md" else ""
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                {self._get_button_styles()}
            </style>
        </head>
        <body style="margin:0; padding:10px; background:transparent;">
            <button class="btn btn-{self.button_type} {size_class}" onclick="handleClick()">
                {self.button_text}
            </button>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <script>
                let bridge = null;
                new QWebChannel(qt.webChannelTransport, function(channel) {{
                    bridge = channel.objects.bridge;
                }});
                function handleClick() {{
                    if (bridge) bridge.onButtonClick('btn', 'click');
                }}
            </script>
        </body>
        </html>
        """

        self.web_view.setHtml(html)
        layout.addWidget(self.web_view)

    def _get_button_styles(self):
        """Return minimal CSS for button"""
        return """
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
            .btn {
                padding: 10px 20px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.3s;
                border: none;
                outline: none;
            }
            .btn:active { transform: scale(0.97); }
            .btn-primary {
                background: linear-gradient(135deg, #4A9EFF 0%, #357ABD 100%);
                color: #FFFFFF;
                box-shadow: 0 4px 12px rgba(74, 158, 255, 0.3);
            }
            .btn-primary:hover {
                background: linear-gradient(135deg, #5AAAFF 0%, #4A9EFF 100%);
                transform: translateY(-1px);
            }
            .btn-secondary {
                background: rgba(26, 35, 50, 0.8);
                color: #E8F0FF;
                border: 1px solid #2A3B4C;
            }
            .btn-secondary:hover {
                background: rgba(42, 59, 76, 0.9);
                border-color: #4A9EFF;
            }
            .btn-danger {
                background: linear-gradient(135deg, #F44336 0%, #D32F2F 100%);
                color: #FFFFFF;
            }
            .btn-danger:hover { transform: translateY(-1px); }
            .btn-sm { padding: 6px 14px; font-size: 12px; }
            .btn-lg { padding: 14px 28px; font-size: 16px; }
        """


# ------------------------------------------------------------
# Demo Application
# ------------------------------------------------------------

class DemoWindow(QWidget):
    """Demo showcasing HTML button system"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Premium HTML Button System")
        self.setFixedSize(800, 750)

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
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("Premium HTML Button System")
        title_font = title.font()
        title_font.setPointSize(18)
        title_font.setWeight(QFont.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        # Description
        desc = QLabel("All buttons with HTML/CSS - Click any button to see Python callback")
        desc.setStyleSheet("color: #8B9DC3; font-size: 12px;")
        layout.addWidget(desc)

        # HTML Button System
        self.button_widget = HTMLButtonWidget()
        self.button_widget.buttonClicked.connect(self.on_button_clicked)
        layout.addWidget(self.button_widget)

        # Status display
        self.status_label = QLabel("Click any button above...")
        self.status_label.setStyleSheet("""
            color: #4A9EFF;
            font-size: 14px;
            font-weight: 600;
            padding: 12px;
            background: rgba(74, 158, 255, 0.1);
            border-radius: 6px;
            border: 1px solid rgba(74, 158, 255, 0.3);
        """)
        layout.addWidget(self.status_label)

        layout.addStretch()

    def on_button_clicked(self, button_id, button_type):
        """Handle button clicks"""
        self.status_label.setText(f"✓ Clicked: {button_id} (type: {button_type})")
        print(f"Button clicked: {button_id} - {button_type}")


# ------------------------------------------------------------
# Launch
# ------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = DemoWindow()
    window.show()

    sys.exit(app.exec())