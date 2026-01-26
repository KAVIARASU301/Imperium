# core/login_manager.py
import logging
import webbrowser
from typing import Optional, Dict
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QWidget, QStackedWidget,
    QCheckBox, QFrame
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from kiteconnect import KiteConnect
from core.token_manager import TokenManager
from utils.styled_message_box import show_message

logger = logging.getLogger(__name__)


class LoginWorker(QThread):
    """Background worker for API authentication."""
    success = Signal(str)
    error = Signal(str)

    def __init__(self, api_key: str, api_secret: str, request_token: str):
        super().__init__()
        self.api_key = api_key
        self.api_secret = api_secret
        self.request_token = request_token

    def run(self):
        try:
            kite = KiteConnect(api_key=self.api_key, timeout=20)
            data = kite.generate_session(self.request_token, api_secret=self.api_secret)
            self.success.emit(data.get('access_token'))
        except Exception as e:
            self.error.emit(str(e))


class RequestTokenServer(QThread):
    """
    Lightweight local HTTP server that listens once for:
        http://127.0.0.1:5678/kite_callback?request_token=...
    Then emits token_received and exits.
    """
    token_received = Signal(str)
    error = Signal(str)

    def __init__(self, host: str = "127.0.0.1", port: int = 5678, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.host = host
        self.port = port

    def run(self):
        outer_self = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    parsed = urlparse(self.path)
                    qs = parse_qs(parsed.query)
                    token = qs.get("request_token", [None])[0]

                    # Simple success page in browser
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body style='font-family:Segoe UI,sans-serif;background:#111;color:#eee;text-align:center;padding-top:40px;'>"
                        b"<h2>Login successful</h2>"
                        b"<p>You can now return to Options Badger Pro.</p>"
                        b"</body></html>"
                    )

                    if token:
                        outer_self.token_received.emit(token)
                except Exception as e:
                    outer_self.error.emit(str(e))

            # Silence default console logging
            def log_message(self, format, *args):
                return

        try:
            httpd = HTTPServer((self.host, self.port), Handler)
            # Handle just ONE request, then return
            httpd.handle_request()
        except Exception as e:
            self.error.emit(str(e))


class LoginManager(QDialog):
    """A professional, self-contained, all-in-one login dialog with a premium UI."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self.token_manager = TokenManager()
        self.api_key = ""
        self.api_secret = ""
        self.access_token = None
        self.trading_mode = 'live'

        self.token_server: Optional[RequestTokenServer] = None

        self.setWindowTitle("Options Badger Pro - Authentication")
        self.setMinimumSize(420, 450)
        self.setModal(True)
        # --- Make window frameless for custom styling ---
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None

        self._setup_ui()
        self._apply_styles()

        QTimer.singleShot(100, self._try_auto_login)

    # ---------------- UI SETUP ---------------- #

    def _setup_ui(self):
        # Main container for rounded corners and background
        container = QWidget(self)
        container.setObjectName("mainContainer")

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(container)

        # Main layout for the container widget
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(25, 20, 25, 25)
        container_layout.setSpacing(15)

        # App Title
        app_title = QLabel(" 🦡 Options Badger Pro")
        app_title.setObjectName("appTitle")
        container_layout.addWidget(app_title, 0, Qt.AlignmentFlag.AlignCenter)

        # Separator Line
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        container_layout.addWidget(divider)

        self.stacked_widget = QStackedWidget()
        container_layout.addWidget(self.stacked_widget)

        self.stacked_widget.addWidget(self._create_auto_login_page())
        self.stacked_widget.addWidget(self._create_credential_input_page())
        self.stacked_widget.addWidget(self._create_token_input_page())

    def _create_auto_login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        status_label = QLabel("Active Trading Session Found")
        status_label.setObjectName("dialogTitle")
        self.info_label = QLabel(
            "An active access session for today was detected.\n"
            "Please choose how you want to continue."
        )
        self.info_label.setObjectName("infoLabel")
        self.info_label.setAlignment(Qt.AlignCenter)

        live_button = QPushButton("Start Live Trading")
        live_button.setObjectName("primaryButton")
        live_button.clicked.connect(lambda: self._select_mode_and_accept('live'))

        paper_button = QPushButton("Start Paper Trading")
        paper_button.setObjectName("secondaryButton")
        paper_button.clicked.connect(lambda: self._select_mode_and_accept('paper'))

        cancel_button = QPushButton("Change API Credentials")
        cancel_button.setObjectName("linkButton")
        cancel_button.setCursor(Qt.PointingHandCursor)
        cancel_button.clicked.connect(self._cancel_auto_login)

        layout.addWidget(status_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.info_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(live_button)
        layout.addWidget(paper_button)
        layout.addSpacing(10)
        layout.addWidget(cancel_button, 0, Qt.AlignmentFlag.AlignCenter)
        return page

    def _create_credential_input_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 10, 0, 0)

        title = QLabel("Kite API Credentials")
        title.setObjectName("dialogTitle")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(QLabel("API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter your API Key")
        layout.addWidget(self.api_key_input)

        layout.addWidget(QLabel("API Secret:"))
        self.api_secret_input = QLineEdit()
        self.api_secret_input.setPlaceholderText("Enter your API Secret")
        self.api_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.api_secret_input)

        self.save_creds_checkbox = QCheckBox("Save Credentials Securely")
        self.save_creds_checkbox.setChecked(True)
        layout.addWidget(self.save_creds_checkbox)
        layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        paper_button = QPushButton("Paper Trading")
        paper_button.setObjectName("secondaryButton")
        live_button = QPushButton("Live Trading")
        live_button.setObjectName("primaryButton")

        button_layout.addWidget(paper_button)
        button_layout.addWidget(live_button)
        layout.addLayout(button_layout)

        live_button.clicked.connect(lambda: self._on_mode_selected('live'))
        paper_button.clicked.connect(lambda: self._on_mode_selected('paper'))
        return page

    def _create_token_input_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 15, 0, 0)
        layout.setSpacing(14)

        # ---- Title ----
        token_title = QLabel("Finalize Trading Session")
        token_title.setObjectName("dialogTitle")
        token_title.setAlignment(Qt.AlignCenter)

        # ---- Info text ----
        token_info = QLabel(
            "Completing your secure session.\n\n"
            "If the browser redirect doesn’t return automatically,\n"
            "paste the token shown in the address bar below."
        )
        token_info.setWordWrap(True)
        token_info.setAlignment(Qt.AlignCenter)
        token_info.setObjectName("infoLabel")

        # ---- Input container (visual grouping) ----
        input_container = QWidget()
        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(20, 16, 20, 16)
        input_layout.setSpacing(8)

        input_label = QLabel("Session Token")
        input_label.setObjectName("inputLabel")

        self.request_token_input = QLineEdit()
        self.request_token_input.setPlaceholderText("Paste token here")
        self.request_token_input.setMinimumHeight(42)
        self.request_token_input.setFocus()
        self.request_token_input.returnPressed.connect(self._on_complete_login)

        input_layout.addWidget(input_label)
        input_layout.addWidget(self.request_token_input)

        input_container.setObjectName("inputContainer")

        # ---- Action button ----
        self.generate_button = QPushButton("Start Trading")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.setFixedHeight(42)
        self.generate_button.clicked.connect(self._on_complete_login)
        self.generate_button.setEnabled(False)
        self.request_token_input.textChanged.connect(
            lambda text: self.generate_button.setEnabled(bool(text.strip()))
        )
        back_button = QPushButton("Change API Credentials")
        back_button.setObjectName("linkButton")
        back_button.clicked.connect(self._go_back_to_credentials)

        layout.addWidget(token_title)
        layout.addWidget(token_info)
        layout.addSpacing(10)
        layout.addWidget(input_container)
        layout.addSpacing(12)
        layout.addWidget(self.generate_button, 0, Qt.AlignCenter)
        layout.addSpacing(6)
        layout.addWidget(back_button, 0, Qt.AlignCenter)

        return page

    def _go_back_to_credentials(self):
        # Stop token server if running
        if self.token_server:
            self.token_server.quit()
            self.token_server = None

        if hasattr(self, "_token_timeout_timer") and self._token_timeout_timer.isActive():
            self._token_timeout_timer.stop()

        self._login_in_progress = False
        self.request_token_input.clear()
        self.generate_button.setText("Start Trading")
        self.generate_button.setEnabled(False)

        self.stacked_widget.setCurrentIndex(1)  # Credentials page

    def _is_token_expired(self, token_data: dict) -> bool:
        """Check if access token is older than 12 hours."""
        if not token_data or 'created_at' not in token_data:
            return True

        try:
            created_at = datetime.fromisoformat(token_data['created_at'])
            expiry_time = created_at + timedelta(hours=12)
            return datetime.now() >= expiry_time
        except (ValueError, TypeError):
            return True
    # ---------------- DRAGGABLE WINDOW ---------------- #

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    # ---------------- LOGIC METHODS ---------------- #

    def _try_auto_login(self):
        creds = self.token_manager.load_credentials()
        if creds:
            self.api_key = creds.get('api_key', '')
            self.api_secret = creds.get('api_secret', '')
            self.api_key_input.setText(self.api_key)
            self.api_secret_input.setText(self.api_secret)

        token_data = self.token_manager.load_token_data()

        # Check if token exists AND is not expired
        if (token_data and
                token_data.get('access_token') and
                self.api_key and
                not self._is_token_expired(token_data)):

            self.access_token = token_data['access_token']
            self.trading_mode = token_data.get('trading_mode', 'live')
            self.stacked_widget.setCurrentIndex(0)
        else:
            # Token expired or doesn't exist - clear it and force re-login
            if token_data and self._is_token_expired(token_data):
                logger.info("Access token expired (>12 hours). Clearing session.")
                self.token_manager.clear_token_data()

            self.access_token = None
            self.stacked_widget.setCurrentIndex(1)


    def _cancel_auto_login(self):
        self.token_manager.clear_token_data()
        self.access_token = None
        self.stacked_widget.setCurrentIndex(1)

    def _on_mode_selected(self, mode: str):
        """
        User selected Live/Paper after entering API key/secret.
        Start local callback server, open Kite login URL, go to token page.
        """
        self.trading_mode = mode
        self.api_key = self.api_key_input.text().strip()
        self.api_secret = self.api_secret_input.text().strip()

        if not (self.api_key and self.api_secret):
            show_message(
                self,
                "Input Error",
                "Please enter both the API key and API secret.",
                icon=QMessageBox.Warning
            )
            return

        if self.save_creds_checkbox.isChecked():
            self.token_manager.save_credentials(self.api_key, self.api_secret)

        # ---- Start local server to capture request_token ----
        try:
            self.token_server = RequestTokenServer(parent=self)
            self.token_server.token_received.connect(self._on_request_token_auto)
            self.token_server.error.connect(self._on_token_server_error)
            self.token_server.start()
            logger.info("Started local RequestTokenServer on 127.0.0.1:5678")

            # 5 minutes timeout (300 seconds)
            self._token_timeout_timer = QTimer(self)
            self._token_timeout_timer.setSingleShot(True)
            self._token_timeout_timer.timeout.connect(self._token_timeout_check)
            self._token_timeout_timer.start(5 * 60 * 1000)

        except Exception as e:
            logger.error(f"Failed to start RequestTokenServer: {e}")
            show_message(
                self,
                "Callback Server Error",
                "The local callback server could not be started.\n\n"
                "This can happen if the port is blocked or already in use.\n"
                "You can continue by pasting the session token manually.",
                icon=QMessageBox.Warning
            )

        # ---- Validate API key before opening browser ----
        try:
            kite = KiteConnect(api_key=self.api_key)
            login_url = kite.login_url()
        except Exception:
            show_message(
                self,
                "Invalid API Key",
                "The API key entered is invalid.\n\n"
                "Please verify and try again.",
                icon=QMessageBox.Critical
            )

            self._go_back_to_credentials()
            return

        webbrowser.open_new(login_url)
        self.stacked_widget.setCurrentIndex(2)

    def _token_timeout_check(self):
        if not self.request_token_input.text().strip():
            show_message(
                self,
                "Authentication Incomplete",
                "No session token was received.\n\n"
                "This may happen if the API key is incorrect, "
                "login was cancelled, or authentication timed out.",
                icon=QMessageBox.Information
            )

            self._go_back_to_credentials()

    def _on_token_server_error(self, msg: str):
        logger.error(f"RequestTokenServer error: {msg}")

    def _on_request_token_auto(self, token: str):
        if not token:
            return

        logger.info("Automatically captured request_token from browser redirect.")

        # Cancel timeout timer
        if hasattr(self, "_token_timeout_timer") and self._token_timeout_timer.isActive():
            self._token_timeout_timer.stop()

        self.request_token_input.setText(token)
        self._on_complete_login()

    def _on_complete_login(self):
        if hasattr(self, "_token_timeout_timer") and self._token_timeout_timer.isActive():
            self._token_timeout_timer.stop()

        # Prevent double submission / race condition
        if getattr(self, "_login_in_progress", False):
            return
        self._login_in_progress = True

        # ---- Extract token safely (handles full URL paste) ----
        text = self.request_token_input.text().strip()

        if "request_token=" in text:
            text = text.split("request_token=")[-1].split("&")[0]

        request_token = text.strip()

        if not request_token:
            self._login_in_progress = False
            show_message(
                self,
                "Input Error",
                "Session token is empty.",
                icon=QMessageBox.Warning
            )
            return

        # ---- UI feedback ----
        self.generate_button.setText("Starting Session…")
        self.generate_button.setEnabled(False)
        self.setCursor(Qt.BusyCursor)

        # ---- Start background login worker ----
        self.worker = LoginWorker(self.api_key, self.api_secret, request_token)
        self.worker.success.connect(self._on_login_success)
        self.worker.error.connect(self._on_login_error)
        self.worker.start()

    def _on_login_success(self, access_token: str):
        self.access_token = access_token
        self.token_manager.save_token_data({
            'access_token': access_token,
            'trading_mode': self.trading_mode,
            'created_at': datetime.now().isoformat()  # Add timestamp
        })
        self.setCursor(Qt.ArrowCursor)
        self.accept()

    def _on_login_error(self, error_msg: str):
        self._login_in_progress = False

        show_message(
            self,
            title="Login Failed",
            message=f"Failed to generate session:\n\n{error_msg}",
            icon=QMessageBox.Critical,
            align=Qt.AlignLeft | Qt.AlignVCenter
        )

        self.generate_button.setText("Start Trading")
        self.generate_button.setEnabled(True)
        self.setCursor(Qt.ArrowCursor)

    def _select_mode_and_accept(self, mode: str):
        self.trading_mode = mode
        logger.info(f"User selected {mode.upper()} mode during auto-login.")
        self.accept()

    def get_api_creds(self) -> Optional[Dict[str, str]]:
        if self.api_key and self.api_secret:
            return {"api_key": self.api_key, "api_secret": self.api_secret}
        return None

    def get_access_token(self) -> Optional[str]:
        return self.access_token

    def get_trading_mode(self) -> Optional[str]:
        return self.trading_mode

    # ---------------- STYLES ---------------- #

    def _apply_styles(self):
        """Applies a premium, modern dark theme."""
        self.setStyleSheet("""
            #mainContainer {
                background-image: url("assets/textures/main_window_bg.png");
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #appTitle {
                font-size: 24px;
                font-weight: 300;
                color: #E0E0E0;
                padding-bottom: 5px;
            }
            #dialogTitle {
                font-size: 18px;
                font-weight: 600;
                color: #FFFFFF;
                padding-bottom: 15px;
            }
            #infoLabel {
                color: #8A9BA8;
                font-size: 13px;
            }
            #divider {
                background-color: #3A4458;
                height: 1px;
            }
            QLabel {
                color: #A9B1C3;
                font-size: 13px;
            }
            QLineEdit {
                background-color: #212635;
                border: 1px solid #3A4458;
                border-radius: 6px;
                color: #E0E0E0;
                font-size: 14px;
                padding: 10px;
            }
            QLineEdit:focus {
                border: 1px solid #29C7C9;
            }
            QCheckBox {
                color: #A9B1C3;
                spacing: 8px;
            }
            QPushButton {
                padding: 10px 14px;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 500;
            }
            #primaryButton {
                background-color: #29C7C9;
                color: #161A25;
                border: none;
            }
            #primaryButton:hover {
                background-color: #32E0E3;
            }
            #secondaryButton {
                background-color: #3A4458;
                color: #E0E0E0;
                border: none;
            }
            #secondaryButton:hover {
                background-color: #4A5568;
            }
            #linkButton {
                background-color: transparent;
                color: #8A9BA8;
                border: none;
                text-decoration: underline;
                font-size: 12px;
            }
            #linkButton:hover {
                color: #FFFFFF;
            }
         

        """)
