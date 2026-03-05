from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QDialog, QFrame
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import Qt, Signal, QSize, QPropertyAnimation, QEasingCurve, QRect
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath, QLinearGradient
import sys


# ------------------------------------------------------------
# Qt Native Close Buttons (Multiple Styles)
# ------------------------------------------------------------

class CloseButton(QPushButton):
    """
    Premium close button with hover animations
    Styles: minimal, filled, outlined, danger
    """

    def __init__(self, style="minimal", size=32, parent=None):
        super().__init__(parent)

        self.button_size = size
        self.button_style = style
        self.hover_progress = 0.0

        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)

        self._apply_style()

    def _apply_style(self):
        """Apply style based on button type"""

        if self.button_style == "minimal":
            # Clean minimal style - just icon, hover background
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    border-radius: {self.button_size // 2}px;
                    color: #8B9DC3;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.1);
                    color: #E8F0FF;
                }}
                QPushButton:pressed {{
                    background-color: rgba(255, 255, 255, 0.15);
                }}
            """)
            self.setText("✕")
            font = self.font()
            font.setPointSize(16)
            font.setWeight(QFont.Medium)
            self.setFont(font)

        elif self.button_style == "filled":
            # Solid background style
            self.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 #2A3B4C,
                        stop:1 #1A2332
                    );
                    border: 1px solid #3A4B5C;
                    border-radius: {self.button_size // 2}px;
                    color: #E8F0FF;
                }}
                QPushButton:hover {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 #3A4B5C,
                        stop:1 #2A3B4C
                    );
                    border: 1px solid #4A9EFF;
                }}
                QPushButton:pressed {{
                    background: #1A2332;
                }}
            """)
            self.setText("✕")
            font = self.font()
            font.setPointSize(14)
            font.setWeight(QFont.Bold)
            self.setFont(font)

        elif self.button_style == "outlined":
            # Outlined circle style
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: 2px solid #3A4B5C;
                    border-radius: {self.button_size // 2}px;
                    color: #8B9DC3;
                }}
                QPushButton:hover {{
                    border: 2px solid #4A9EFF;
                    color: #4A9EFF;
                    background-color: rgba(74, 158, 255, 0.1);
                }}
                QPushButton:pressed {{
                    background-color: rgba(74, 158, 255, 0.2);
                }}
            """)
            self.setText("✕")
            font = self.font()
            font.setPointSize(14)
            font.setWeight(QFont.Bold)
            self.setFont(font)

        elif self.button_style == "danger":
            # Danger/destructive action style
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: 1px solid #5A3A3A;
                    border-radius: {self.button_size // 2}px;
                    color: #E57373;
                }}
                QPushButton:hover {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 #D32F2F,
                        stop:1 #B71C1C
                    );
                    border: 1px solid #F44336;
                    color: #FFFFFF;
                }}
                QPushButton:pressed {{
                    background: #B71C1C;
                }}
            """)
            self.setText("✕")
            font = self.font()
            font.setPointSize(14)
            font.setWeight(QFont.Bold)
            self.setFont(font)

        elif self.button_style == "modern":
            # Modern glassmorphic style
            self.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(42, 59, 76, 180),
                        stop:1 rgba(26, 35, 50, 180)
                    );
                    border: 1px solid rgba(74, 158, 255, 80);
                    border-radius: {self.button_size // 2}px;
                    color: #8B9DC3;
                }}
                QPushButton:hover {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(74, 158, 255, 80),
                        stop:1 rgba(74, 158, 255, 60)
                    );
                    border: 1px solid #4A9EFF;
                    color: #4A9EFF;
                }}
                QPushButton:pressed {{
                    background: rgba(74, 158, 255, 100);
                }}
            """)
            self.setText("✕")
            font = self.font()
            font.setPointSize(14)
            font.setWeight(QFont.Bold)
            self.setFont(font)


class CustomPaintCloseButton(QPushButton):
    """Custom painted close button with smooth animations"""

    def __init__(self, size=32, parent=None):
        super().__init__(parent)

        self.button_size = size
        self._hover = False
        self._pressed = False

        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none;")

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()
        center = rect.center()
        radius = min(rect.width(), rect.height()) // 2 - 2

        # Background circle
        if self._pressed:
            bg_color = QColor(74, 158, 255, 80)
        elif self._hover:
            bg_color = QColor(74, 158, 255, 40)
        else:
            bg_color = QColor(255, 255, 255, 15)

        painter.setBrush(bg_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, radius, radius)

        # Draw X
        if self._hover or self._pressed:
            pen_color = QColor("#4A9EFF")
        else:
            pen_color = QColor("#8B9DC3")

        pen = QPen(pen_color, 2.5)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)

        # X lines
        offset = radius * 0.4
        painter.drawLine(
            int(center.x() - offset), int(center.y() - offset),
            int(center.x() + offset), int(center.y() + offset)
        )
        painter.drawLine(
            int(center.x() + offset), int(center.y() - offset),
            int(center.x() - offset), int(center.y() + offset)
        )


# ------------------------------------------------------------
# HTML/CSS Close Buttons
# ------------------------------------------------------------

HTML_CLOSE_BUTTONS = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: transparent;
            padding: 20px;
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
        }

        .close-btn-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
        }

        .label {
            color: #8B9DC3;
            font-size: 11px;
            text-align: center;
        }

        /* Base Close Button */
        .close-btn {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            font-size: 18px;
            font-weight: 600;
            user-select: none;
        }

        .close-btn:active {
            transform: scale(0.95);
        }

        /* Style 1: Minimal */
        .close-minimal {
            background: transparent;
            color: #8B9DC3;
            border: none;
        }

        .close-minimal:hover {
            background: rgba(255, 255, 255, 0.1);
            color: #E8F0FF;
            transform: rotate(90deg);
        }

        /* Style 2: Filled */
        .close-filled {
            background: linear-gradient(135deg, #2A3B4C 0%, #1A2332 100%);
            color: #E8F0FF;
            border: 1px solid #3A4B5C;
        }

        .close-filled:hover {
            background: linear-gradient(135deg, #3A4B5C 0%, #2A3B4C 100%);
            border-color: #4A9EFF;
            box-shadow: 0 4px 12px rgba(74, 158, 255, 0.3);
        }

        /* Style 3: Outlined */
        .close-outlined {
            background: transparent;
            color: #8B9DC3;
            border: 2px solid #3A4B5C;
        }

        .close-outlined:hover {
            border-color: #4A9EFF;
            color: #4A9EFF;
            background: rgba(74, 158, 255, 0.1);
        }

        /* Style 4: Danger */
        .close-danger {
            background: transparent;
            color: #E57373;
            border: 1px solid #5A3A3A;
        }

        .close-danger:hover {
            background: linear-gradient(135deg, #D32F2F 0%, #B71C1C 100%);
            border-color: #F44336;
            color: #FFFFFF;
            box-shadow: 0 4px 12px rgba(244, 67, 54, 0.4);
        }

        /* Style 5: Glassmorphic */
        .close-glass {
            background: rgba(26, 35, 50, 0.8);
            color: #8B9DC3;
            border: 1px solid rgba(74, 158, 255, 0.3);
        }

        .close-glass:hover {
            background: rgba(74, 158, 255, 0.3);
            border-color: #4A9EFF;
            color: #4A9EFF;
        }

        /* Style 6: macOS Style */
        .close-macos {
            background: #FF5F57;
            color: transparent;
            border: none;
            width: 12px;
            height: 12px;
            position: relative;
        }

        .close-macos::before,
        .close-macos::after {
            content: '';
            position: absolute;
            width: 8px;
            height: 1.5px;
            background: #8B0000;
            top: 50%;
            left: 50%;
            opacity: 0;
            transition: opacity 0.2s;
        }

        .close-macos::before {
            transform: translate(-50%, -50%) rotate(45deg);
        }

        .close-macos::after {
            transform: translate(-50%, -50%) rotate(-45deg);
        }

        .close-macos:hover::before,
        .close-macos:hover::after {
            opacity: 1;
        }

        .close-macos:hover {
            background: #E04B44;
        }

        /* Style 7: Modern Line */
        .close-modern {
            background: transparent;
            color: transparent;
            border: none;
            position: relative;
        }

        .close-modern::before,
        .close-modern::after {
            content: '';
            position: absolute;
            width: 18px;
            height: 2px;
            background: #8B9DC3;
            top: 50%;
            left: 50%;
            transition: all 0.3s;
        }

        .close-modern::before {
            transform: translate(-50%, -50%) rotate(45deg);
        }

        .close-modern::after {
            transform: translate(-50%, -50%) rotate(-45deg);
        }

        .close-modern:hover::before,
        .close-modern:hover::after {
            background: #4A9EFF;
            width: 20px;
        }

        .close-modern:hover {
            background: rgba(74, 158, 255, 0.1);
        }

        /* Style 8: Animated */
        .close-animated {
            background: transparent;
            border: 2px solid #3A4B5C;
            color: transparent;
            position: relative;
            overflow: hidden;
        }

        .close-animated::before,
        .close-animated::after {
            content: '';
            position: absolute;
            width: 16px;
            height: 2px;
            background: #8B9DC3;
            top: 50%;
            left: 50%;
            transition: all 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55);
        }

        .close-animated::before {
            transform: translate(-50%, -50%) rotate(45deg);
        }

        .close-animated::after {
            transform: translate(-50%, -50%) rotate(-45deg);
        }

        .close-animated:hover {
            border-color: #4A9EFF;
            transform: rotate(90deg);
        }

        .close-animated:hover::before,
        .close-animated:hover::after {
            background: #4A9EFF;
        }
    </style>
</head>
<body>
    <div class="close-btn-container">
        <div class="close-btn close-minimal" onclick="closeClicked('minimal')">✕</div>
        <div class="label">Minimal</div>
    </div>

    <div class="close-btn-container">
        <div class="close-btn close-filled" onclick="closeClicked('filled')">✕</div>
        <div class="label">Filled</div>
    </div>

    <div class="close-btn-container">
        <div class="close-btn close-outlined" onclick="closeClicked('outlined')">✕</div>
        <div class="label">Outlined</div>
    </div>

    <div class="close-btn-container">
        <div class="close-btn close-danger" onclick="closeClicked('danger')">✕</div>
        <div class="label">Danger</div>
    </div>

    <div class="close-btn-container">
        <div class="close-btn close-glass" onclick="closeClicked('glass')">✕</div>
        <div class="label">Glass</div>
    </div>

    <div class="close-btn-container">
        <div class="close-btn close-macos" onclick="closeClicked('macos')"></div>
        <div class="label">macOS</div>
    </div>

    <div class="close-btn-container">
        <div class="close-btn close-modern" onclick="closeClicked('modern')"></div>
        <div class="label">Modern</div>
    </div>

    <div class="close-btn-container">
        <div class="close-btn close-animated" onclick="closeClicked('animated')"></div>
        <div class="label">Animated</div>
    </div>

    <script>
        function closeClicked(style) {
            console.log('Close button clicked:', style);
            // This would trigger Python callback in real implementation
        }
    </script>
</body>
</html>
"""


# ------------------------------------------------------------
# Demo Dialog with Close Buttons
# ------------------------------------------------------------

class PremiumDialog(QDialog):
    """Dialog demonstrating close buttons"""

    def __init__(self, close_style="minimal", parent=None):
        super().__init__(parent)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.setFixedSize(500, 400)

        # Main container
        container = QFrame(self)
        container.setGeometry(0, 0, 500, 400)
        container.setStyleSheet("""
            QFrame {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0A0E17,
                    stop:1 #1A1F2E
                );
                border: 1px solid #2A3B4C;
                border-radius: 12px;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header with close button
        header = QHBoxLayout()

        title = QLabel("Premium Dialog")
        title.setStyleSheet("color: #E8F0FF; font-size: 18px; font-weight: bold;")
        header.addWidget(title)

        header.addStretch()

        # Close button
        if close_style == "custom":
            close_btn = CustomPaintCloseButton(36)
        else:
            close_btn = CloseButton(close_style, 36)
        close_btn.clicked.connect(self.accept)
        header.addWidget(close_btn)

        layout.addLayout(header)

        # Content
        content = QLabel("Dialog content goes here...\n\nClick the close button to dismiss.")
        content.setStyleSheet("color: #8B9DC3; font-size: 14px; padding: 40px;")
        content.setAlignment(Qt.AlignCenter)
        layout.addWidget(content, 1)


# ------------------------------------------------------------
# Demo Application
# ------------------------------------------------------------

class DemoWindow(QWidget):
    """Demo showcasing all close button styles"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Premium Close Buttons")
        self.setFixedSize(900, 700)

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
            }
            QPushButton {
                background: #1A2332;
                color: #E8F0FF;
                border: 1px solid #2A3B4C;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #2A3B4C;
                border-color: #4A9EFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(40, 40, 40, 40)

        # Title
        title = QLabel("Premium Close Button Styles")
        title_font = title.font()
        title_font.setPointSize(20)
        title_font.setWeight(QFont.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        # Qt Native Buttons Section
        section1 = QLabel("Qt Native Close Buttons:")
        section1.setStyleSheet("color: #4A9EFF; font-size: 14px; font-weight: bold; margin-top: 20px;")
        layout.addWidget(section1)

        native_row = QHBoxLayout()
        native_row.setSpacing(30)

        styles = ["minimal", "filled", "outlined", "danger", "modern"]
        for style in styles:
            col = QVBoxLayout()
            col.setAlignment(Qt.AlignCenter)

            btn = CloseButton(style, 20)
            btn.clicked.connect(lambda s=style: self.show_dialog(s))
            col.addWidget(btn, alignment=Qt.AlignCenter)

            label = QLabel(style.capitalize())
            label.setStyleSheet("color: #8B9DC3; font-size: 11px;")
            label.setAlignment(Qt.AlignCenter)
            col.addWidget(label)

            native_row.addLayout(col)

        layout.addLayout(native_row)

        # Custom Paint Button
        section2 = QLabel("Custom Painted:")
        section2.setStyleSheet("color: #4A9EFF; font-size: 14px; font-weight: bold; margin-top: 20px;")
        layout.addWidget(section2)

        custom_row = QHBoxLayout()
        col = QVBoxLayout()
        col.setAlignment(Qt.AlignCenter)

        custom_btn = CustomPaintCloseButton(40)
        custom_btn.clicked.connect(lambda: self.show_dialog("custom"))
        col.addWidget(custom_btn, alignment=Qt.AlignCenter)

        label = QLabel("Custom Paint")
        label.setStyleSheet("color: #8B9DC3; font-size: 11px;")
        label.setAlignment(Qt.AlignCenter)
        col.addWidget(label)

        custom_row.addLayout(col)
        custom_row.addStretch()
        layout.addLayout(custom_row)

        # HTML/CSS Buttons Section
        section3 = QLabel("HTML/CSS Close Buttons:")
        section3.setStyleSheet("color: #4A9EFF; font-size: 14px; font-weight: bold; margin-top: 20px;")
        layout.addWidget(section3)

        web_view = QWebEngineView()
        web_view.setFixedHeight(120)
        web_view.setHtml(HTML_CLOSE_BUTTONS)
        layout.addWidget(web_view)

        layout.addStretch()

        # Info
        info = QLabel("Click any button to see it in a dialog")
        info.setStyleSheet("color: #5A6B7D; font-size: 11px; text-align: center;")
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)

    def show_dialog(self, style):
        """Show dialog with selected close button style"""
        dialog = PremiumDialog(style, self)
        dialog.exec()


# ------------------------------------------------------------
# Launch
# ------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = DemoWindow()
    window.show()

    sys.exit(app.exec())