import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QGroupBox, QGridLayout, QLabel, QLineEdit,
    QSpinBox, QComboBox, QCheckBox, QPushButton, QMessageBox
)
from PySide6.QtCore import Qt, Signal

from utils.config_manager import ConfigManager
from core.token_manager import TokenManager

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """A premium, draggable settings dialog with a consistent dark theme."""

    settings_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(550)

        # Frameless + translucent (same as other dialogs)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._drag_pos = None

        self.token_manager = TokenManager()
        self.config_manager = ConfigManager()

        self._setup_ui()
        self._load_settings()
        self._apply_styles()

    # ---------------- UI SETUP ---------------- #

    def _setup_ui(self):
        container = QWidget(self)
        container.setObjectName("mainContainer")

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 10, 20, 20)
        layout.setSpacing(15)

        layout.addLayout(self._create_header())

        tabs = QTabWidget()
        tabs.setObjectName("mainTabs")
        tabs.addTab(self._create_trading_tab(), "TRADING")
        tabs.addTab(self._create_display_tab(), "DISPLAY")
        tabs.addTab(self._create_api_tab(), "API")
        layout.addWidget(tabs)

        layout.addLayout(self._create_action_buttons())

        dialog_layout = QVBoxLayout(self)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        dialog_layout.addWidget(container)

    def _create_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Application Settings")
        title.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.reject)  # ✅ Correct dialog close

        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        return header

    def _create_action_buttons(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 10, 0, 0)

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setObjectName("secondaryButton")
        reset_btn.clicked.connect(self._reset_to_defaults)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save_settings)

        layout.addWidget(reset_btn)
        layout.addStretch()
        layout.addWidget(cancel_btn)
        layout.addWidget(save_btn)
        return layout

    # ---------------- TABS ---------------- #

    def _create_trading_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)

        group = QGroupBox("Default Trading Values")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(15)
        grid.setVerticalSpacing(12)

        grid.addWidget(QLabel("Default Symbol:"), 0, 0)
        self.default_symbol = QComboBox()
        self.default_symbol.addItems(["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])
        grid.addWidget(self.default_symbol, 0, 1)

        grid.addWidget(QLabel("Default Product:"), 1, 0)
        self.default_product = QComboBox()
        self.default_product.addItems(["MIS", "NRML"])
        grid.addWidget(self.default_product, 1, 1)

        grid.addWidget(QLabel("Default Lots:"), 2, 0)
        self.default_lots = QSpinBox()
        self.default_lots.setRange(1, 100)
        grid.addWidget(self.default_lots, 2, 1)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    def _create_display_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)

        group = QGroupBox("Display Settings")
        grid = QGridLayout(group)
        grid.setVerticalSpacing(12)

        self.auto_refresh = QCheckBox("Auto-refresh UI components")
        grid.addWidget(self.auto_refresh, 0, 0, 1, 2)

        grid.addWidget(QLabel("Refresh Interval (sec):"), 1, 0)
        self.refresh_interval = QSpinBox()
        self.refresh_interval.setRange(1, 60)
        grid.addWidget(self.refresh_interval, 1, 1)

        self.auto_adjust_ladder = QCheckBox(
            "Auto-adjust strike ladder on price movement"
        )
        grid.addWidget(self.auto_adjust_ladder, 2, 0, 1, 2)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    def _create_api_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)

        group = QGroupBox("API Configuration")
        grid = QGridLayout(group)
        grid.setVerticalSpacing(12)

        grid.addWidget(QLabel("API Key:"), 0, 0)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.api_key, 0, 1)

        grid.addWidget(QLabel("API Secret:"), 1, 0)
        self.api_secret = QLineEdit()
        self.api_secret.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.api_secret, 1, 1)

        self.save_credentials = QCheckBox(
            "Save credentials securely on this machine"
        )
        grid.addWidget(self.save_credentials, 2, 0, 1, 2)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    # ---------------- STYLES ---------------- #

    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle {
                color: #FFFFFF;
                font-size: 16px;
                font-weight: 600;
            }
            #closeButton {
                background-color: transparent;
                border: none;
                color: #8A9BA8;
                font-size: 14px;
                font-weight: 600;
            }
            #closeButton:hover {
                color: #FFFFFF;
            }
            #closeButton:pressed {
                color: #29C7C9;
            }

            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background-color: transparent;
                color: #8A9BA8;
                font-weight: bold;
                padding: 10px 20px;
                border-bottom: 2px solid transparent;
            }
            QTabBar::tab:selected {
                color: #FFFFFF;
                border-bottom: 2px solid #29C7C9;
            }
            QTabBar::tab:hover {
                color: #FFFFFF;
            }

            QGroupBox {
                color: #A9B1C3;
                border: 1px solid #2A3140;
                border-radius: 8px;
                font-size: 11px;
                margin-top: 10px;
                padding-top: 15px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }

            QLabel {
                color: #A9B1C3;
                font-size: 13px;
            }
            QLineEdit, QSpinBox, QComboBox {
                background-color: #212635;
                border: 1px solid #3A4458;
                color: #E0E0E0;
                padding: 8px;
                border-radius: 6px;
                font-size: 13px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 1px solid #29C7C9;
            }

            QCheckBox {
                color: #A9B1C3;
                spacing: 8px;
            }

            QPushButton {
                font-weight: bold;
                border-radius: 6px;
                padding: 10px 18px;
                border: none;
                font-size: 12px;
            }
            #secondaryButton {
                background-color: #3A4458;
                color: #E0E0E0;
            }
            #secondaryButton:hover {
                background-color: #4A5568;
            }
            #primaryButton {
                background-color: #29C7C9;
                color: #161A25;
            }
            #primaryButton:hover {
                background-color: #32E0E3;
            }
        """)

    # ---------------- LOGIC ---------------- #

    def _load_settings(self):
        settings = self.config_manager.load_settings()

        self.default_symbol.setCurrentText(
            settings.get("default_symbol", "NIFTY")
        )
        self.default_product.setCurrentText(
            settings.get("default_product", "MIS")
        )
        self.default_lots.setValue(
            settings.get("default_lots", 1)
        )
        self.auto_refresh.setChecked(
            settings.get("auto_refresh", True)
        )
        self.refresh_interval.setValue(
            settings.get("refresh_interval", 2)
        )
        self.auto_adjust_ladder.setChecked(
            settings.get("auto_adjust_ladder", True)
        )

        creds = self.token_manager.load_credentials()
        if creds:
            self.api_key.setText(creds.get("api_key", ""))
            self.api_secret.setText(creds.get("api_secret", ""))
            self.save_credentials.setChecked(True)

    def _save_settings(self):
        settings = {
            "default_symbol": self.default_symbol.currentText(),
            "default_product": self.default_product.currentText(),
            "default_lots": self.default_lots.value(),
            "auto_refresh": self.auto_refresh.isChecked(),
            "refresh_interval": self.refresh_interval.value(),
            "auto_adjust_ladder": self.auto_adjust_ladder.isChecked(),
        }

        self.config_manager.save_settings(settings)

        if (
            self.save_credentials.isChecked()
            and self.api_key.text()
            and self.api_secret.text()
        ):
            self.token_manager.save_credentials(
                self.api_key.text(),
                self.api_secret.text()
            )

        self.settings_changed.emit(settings)
        self.accept()

    def _reset_to_defaults(self):
        reply = QMessageBox.question(
            self,
            "Confirm Reset",
            "Are you sure you want to reset all settings to default?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            defaults = self.config_manager.default_settings

            self.default_symbol.setCurrentText(defaults["default_symbol"])
            self.default_product.setCurrentText(defaults["default_product"])
            self.default_lots.setValue(defaults["default_lots"])
            self.auto_refresh.setChecked(defaults.get("auto_refresh", True))
            self.refresh_interval.setValue(defaults.get("refresh_interval", 2))
            self.auto_adjust_ladder.setChecked(
                defaults.get("auto_adjust_ladder", True)
            )

            self.api_key.clear()
            self.api_secret.clear()
            self.save_credentials.setChecked(False)

    # ---------------- DRAGGING ---------------- #

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()
