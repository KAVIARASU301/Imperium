# widgets/status_bar.py
import os
from PySide6.QtWidgets import QLabel, QFrame, QStatusBar
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import QGraphicsOpacityEffect


class StatusBarWidget:
    """
    Institutional-grade status bar with telemetry:
    - Trading mode (LIVE/PAPER)
    - Network status with icon
    - Market status
    - API health with icon
    - Clock
    """

    def __init__(self, status_bar: QStatusBar, trading_mode: str):
        self.status_bar = status_bar
        self.trading_mode = trading_mode
        self._network_opacity_effect = None
        self._network_icon_animation = None
        self._setup_widgets()

        # Initialize with default states after widgets are set up
        self.update_network_status("Connecting")
        self.update_api_status("Healthy")

    def _setup_widgets(self):
        """Initialize all status bar widgets"""
        self.status_bar.setSizeGripEnabled(False)

        # --- MODE ---
        self.mode_chip = QLabel(self.trading_mode.upper())
        self.mode_chip.setObjectName("footerModeChip")

        # --- NETWORK ICON ---
        self.network_icon = QLabel()
        self.network_icon.setFixedSize(14, 14)
        self.network_icon.setScaledContents(True)

        # --- NETWORK TEXT ---
        self.network_chip = QLabel("Connecting")
        self.network_chip.setObjectName("footerStatusChip")

        # --- MARKET ---
        self.market_chip = QLabel("Market --")
        self.market_chip.setObjectName("footerStatusChip")

        # --- API ICON ---
        self.api_icon = QLabel()
        self.api_icon.setFixedSize(14, 14)
        self.api_icon.setScaledContents(True)

        # --- API TEXT ---
        self.api_chip = QLabel("API --")
        self.api_chip.setObjectName("footerStatusChip")

        # --- CLOCK ---
        self.clock_chip = QLabel("--:--:--")
        self.clock_chip.setObjectName("footerClockChip")

        # Add all widgets to status bar
        for widget in (
                self.mode_chip,
                self._create_separator(),
                self.network_icon,
                self.network_chip,
                self._create_separator(),
                self.market_chip,
                self._create_separator(),
                self.api_icon,
                self.api_chip,
                self._create_separator(),
                self.clock_chip,
        ):
            self.status_bar.addPermanentWidget(widget)

        self.status_bar.showMessage("Ready.")

    @staticmethod
    def _create_separator() -> QFrame:
        """Create a vertical separator line"""
        separator = QFrame()
        separator.setObjectName("footerSeparator")
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        return separator

    def update_network_status(self, status: str):
        """Update network status text and icon"""
        self.network_chip.setText(status)
        self._update_network_icon(status)

    def update_market_status(self, status: str):
        """Update market status text"""
        self.market_chip.setText(status)

    def update_api_status(self, status: str):
        """Update API status text and icon"""
        self.api_chip.setText(status)
        self._update_api_icon(status)

    def update_clock(self, time_str: str):
        """Update clock display"""
        self.clock_chip.setText(time_str)

    def publish_message(self, message: str, timeout_ms: int = 4000, level: str = "info"):
        """
        Show a temporary message in the status bar with appropriate icon

        Args:
            message: The message to display
            timeout_ms: How long to show the message (0 = permanent)
            level: Message type - 'success', 'warning', 'error', 'action', 'info'
        """
        icon_map = {
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
            "action": "⏳",
            "info": "ℹ️",
        }
        icon = icon_map.get(level, "ℹ️")
        formatted_message = message if message[:1] in {"✅", "⚠", "❌", "⏳", "ℹ"} else f"{icon} {message}"
        self.status_bar.showMessage(formatted_message, timeout_ms)

    def _update_network_icon(self, status: str):
        """
        Update network icon with color tint and smooth fade animation
        """
        base_path = os.path.dirname(os.path.abspath(__file__))
        icons_dir = os.path.join(base_path, "..", "assets", "icons")

        connected_icon = os.path.join(icons_dir, "connected.svg")
        disconnected_icon = os.path.join(icons_dir, "disconnected.svg")

        # Determine state
        is_connected = "Connected" in status

        icon_path = connected_icon if is_connected else disconnected_icon
        tint_color = QColor("#00E676") if is_connected else QColor("#FF5252")

        if not os.path.exists(icon_path):
            return

        original = QPixmap(icon_path)
        if original.isNull():
            return

        # Tint SVG icon
        tinted = QPixmap(original.size())
        tinted.fill(Qt.transparent)

        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, original)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), tint_color)
        painter.end()

        self.network_icon.setPixmap(tinted)

        # Smooth fade animation
        if not self._network_opacity_effect:
            self._network_opacity_effect = QGraphicsOpacityEffect()
            self.network_icon.setGraphicsEffect(self._network_opacity_effect)

        animation = QPropertyAnimation(self._network_opacity_effect, b"opacity")
        animation.setDuration(250)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.start()

        # Prevent garbage collection
        self._network_icon_animation = animation

    def _update_api_icon(self, api_status: str):
        """
        Update API health icon with color tint (no animation for stability)
        """
        base_path = os.path.dirname(os.path.abspath(__file__))
        icons_dir = os.path.join(base_path, "..", "assets", "icons")
        heartbeat_icon = os.path.join(icons_dir, "heart_beat.svg")

        if not os.path.exists(heartbeat_icon):
            return

        original = QPixmap(heartbeat_icon)
        if original.isNull():
            return

        # Determine color by status - handle both "Healthy" and "API Healthy" formats
        if "Healthy" in api_status:
            tint_color = QColor("#00E676")  # green
        elif "Recovering" in api_status:
            tint_color = QColor("#FFC107")  # amber
        else:
            tint_color = QColor("#FF1744")  # red

        tinted = QPixmap(original.size())
        tinted.fill(Qt.transparent)

        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, original)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), tint_color)
        painter.end()

        self.api_icon.setPixmap(tinted)