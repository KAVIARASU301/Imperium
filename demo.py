# core/main_window.py
import logging
import os
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta, time, date
from PySide6.QtWidgets import (QMainWindow, QPushButton, QApplication, QWidget, QVBoxLayout,
                               QMessageBox, QDialog, QSplitter, QHBoxLayout, QBoxLayout)
from PySide6.QtCore import Qt, QTimer, QUrl, QByteArray,QPoint
from PySide6.QtMultimedia import QSoundEffect
from kiteconnect import KiteConnect
from PySide6.QtGui import QPalette, QColor
import ctypes

# Internal imports
from utils.config_manager import ConfigManager
from core.market_data_worker import MarketDataWorker
from utils.data_models import OptionType, Position, Contract
from core.instrument_loader import InstrumentLoader
from widgets.strike_ladder import StrikeLadderWidget
from widgets.header_toolbar import HeaderToolbar
from widgets.menu_bar import create_enhanced_menu_bar
from widgets.account_summary import AccountSummaryWidget
from dialogs.settings_dialog import SettingsDialog
from dialogs.open_positions_dialog import OpenPositionsDialog
from dialogs.performance_dialog import PerformanceDialog
from dialogs.quick_order_dialog import QuickOrderDialog
from core.position_manager import PositionManager
from widgets.positions_table import PositionsTable
from core.config import REFRESH_INTERVAL_MS
from widgets.buy_exit_panel import BuyExitPanel
from dialogs.order_history_dialog import OrderHistoryDialog
from utils.trade_logger import TradeLogger
from dialogs.pnl_history_dialog import PnlHistoryDialog
from dialogs.pending_orders_dialog import PendingOrdersDialog
from widgets.order_status_widget import OrderStatusWidget
from core.paper_trading_manager import PaperTradingManager
from dialogs.option_chain_dialog import OptionChainDialog
from dialogs.order_confirmation_dialog import OrderConfirmationDialog
from utils.pnl_logger import PnlLogger
from dialogs.market_monitor_dialog import MarketMonitorDialog


logger = logging.getLogger(__name__)


class CustomTitleBar(QWidget):
    """Custom title bar with window controls and menu bar"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.dragging = False
        self.drag_position = QPoint()

        self.setFixedHeight(32)
        self.setStyleSheet("""
            CustomTitleBar {
                background-color: #1a1a1a;
                border-bottom: 1px solid #333;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(0)

        # Menu bar (will be set from main window)
        self.menu_bar = None

        # Spacer to push window controls to the right
        layout.addStretch()

        # Window control buttons
        self.create_window_controls(layout)

    def set_menu_bar(self, menu_bar):
        """Set the menu bar in the custom title bar"""
        self.menu_bar = menu_bar

        layout = self.layout()
        if isinstance(layout, QBoxLayout):  # runtime check to be safe
            layout.insertWidget(0, menu_bar)
        menu_bar.setStyleSheet("""
            QMenuBar {
                background-color: transparent;
                color: #E0E0E0;
                border: none;
                font-size: 13px;
                padding: 4px 0px;
            }
            QMenuBar::item {
                background-color: transparent;
                padding: 6px 12px;
                border-radius: 4px;
                margin: 0px 2px;
            }
            QMenuBar::item:selected {
                background-color: #29C7C9;
                color: #161A25;
            }
            QMenuBar::item:pressed {
                background-color: #1f8a8c;
                color: #161A25;
            }
        """)

    def create_window_controls(self, layout):
        """Create minimize, maximize, and close buttons"""
        button_style = """
            QPushButton {
                background-color: transparent;
                border: none;
                color: #E0E0E0;
                font-size: 16px;
                font-weight: bold;
                padding: 0px;
                margin: 0px;
                width: 45px;
                height: 32px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.2);
            }
        """

        # Special style for maximize button to make it visually consistent
        maximize_button_style = """
            QPushButton {
                background-color: transparent;
                border: none;
                color: #E0E0E0;
                font-size: 14px;
                font-weight: bold;
                padding: 0px;
                margin: 0px;
                width: 45px;
                height: 32px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.2);
            }
        """

        close_button_style = button_style + """
            QPushButton:hover {
                background-color: #e74c3c;
                color: white;
            }
            QPushButton:pressed {
                background-color: #c0392b;
                color: white;
            }
        """

        # Minimize button
        minimize_btn = QPushButton("−")
        minimize_btn.setStyleSheet(button_style)
        minimize_btn.clicked.connect(self.parent_window.showMinimized)
        layout.addWidget(minimize_btn)

        # Maximize/Restore button with smaller font size
        self.maximize_btn = QPushButton("□")
        self.maximize_btn.setStyleSheet(maximize_button_style)  # Use the special style
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        layout.addWidget(self.maximize_btn)

        # Close button
        close_btn = QPushButton("×")
        close_btn.setStyleSheet(close_button_style)
        close_btn.clicked.connect(self.parent_window.close)
        layout.addWidget(close_btn)

    def toggle_maximize(self):
        """Toggle between maximized and normal window state"""
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
            self.maximize_btn.setText("□")
        else:
            self.parent_window.showMaximized()
            self.maximize_btn.setText("❐")

    def mousePressEvent(self, event):
        """Handle mouse press for window dragging"""
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.parent_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """Handle mouse move for window dragging"""
        if event.buttons() == Qt.LeftButton and self.dragging:
            if not self.parent_window.isMaximized():
                self.parent_window.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        """Handle mouse release to stop dragging"""
        self.dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        """Handle double-click to maximize/restore"""
        if event.button() == Qt.LeftButton:
            self.toggle_maximize()
            event.accept()
class APICircuitBreaker:
    """
    Circuit breaker for API calls to prevent overwhelming failed endpoints
    """

    def __init__(self, failure_threshold: int = 5, timeout_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = "CLOSED"

    def can_execute(self) -> bool:
        if self.state == "CLOSED":
            return True
        elif self.state == "OPEN":
            if self._should_attempt_reset():
                self.state = "HALF_OPEN"
                return True
            return False
        elif self.state == "HALF_OPEN":
            return True
        return False

    def record_success(self):
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            logger.warning(f"Circuit breaker OPEN after {self.failure_count} failures")

    def _should_attempt_reset(self) -> bool:
        if not self.last_failure_time:
            return True
        return datetime.now() - self.last_failure_time >= timedelta(seconds=self.timeout_seconds)


api_logger = logging.getLogger("api_health")
api_handler = logging.FileHandler("logs/api_health.log")
api_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
api_handler.setFormatter(api_formatter)
api_logger.setLevel(logging.INFO)


class ScalperMainWindow(QMainWindow):
    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], real_kite_client: KiteConnect, api_key: str,
                 access_token: str):
        super().__init__()

        self.api_key = api_key
        self.access_token = access_token
        self.trader = trader
        self.real_kite_client = real_kite_client

        # --- FIX: Added market_data_client attribute ---
        self.market_data_client = MarketDataWorker(api_key, access_token)

        self.trading_mode = 'paper' if isinstance(trader, PaperTradingManager) else 'live'
        self.trade_logger = TradeLogger(mode=self.trading_mode)
        self.pnl_logger = PnlLogger(mode=self.trading_mode)

        self.position_manager = PositionManager(self.trader, self.trade_logger)
        self.config_manager = ConfigManager()
        self.instrument_data = {}
        self.settings = self.config_manager.load_settings()
        self._settings_changing = False

        self.margin_circuit_breaker = APICircuitBreaker(failure_threshold=3, timeout_seconds=30)
        self.profile_circuit_breaker = APICircuitBreaker(failure_threshold=3, timeout_seconds=30)
        self.last_successful_balance = 0.0
        self.last_successful_user_id = "Unknown"
        self.last_successful_margins = {}
        self.api_health_check_timer = QTimer()
        self.api_health_check_timer.timeout.connect(self._periodic_api_health_check)
        self.api_health_check_timer.start(30000)
        self.rms_failures = 0
        self.max_rms_retries = 5

        self.active_quick_order_dialog: Optional[QuickOrderDialog] = None
        self.active_order_confirmation_dialog: Optional[OrderConfirmationDialog] = None
        self.positions_dialog = None
        self.performance_dialog = None
        self.order_history_dialog = None
        self.pnl_history_dialog = None
        self.pending_orders_dialog = None
        self.option_chain_dialog = None
        self.pending_order_widgets = {}
        self.market_monitor_dialogs = []
        self.current_symbol = ""
        self.chartink_manager = None
        self.network_status = "Initializing..."


        self.setWindowFlags(Qt.FramelessWindowHint)
        self.custom_title_bar = CustomTitleBar(self)
        self.setMinimumSize(1200, 700)
        self.setWindowState(Qt.WindowMaximized)

        self._apply_dark_theme()
        self._setup_ui()
        self._setup_position_manager()
        self._connect_signals()
        self._init_background_workers()

        if isinstance(self.trader, PaperTradingManager):
            self.trader.order_update.connect(self._on_paper_trade_update)
            self.market_data_worker.data_received.connect(self.trader.update_market_data)

        # timer for refreshing positions when orders are pending
        self.pending_order_refresh_timer = QTimer(self)
        self.pending_order_refresh_timer.setInterval(1000)  # 1000ms = 1 second
        self.pending_order_refresh_timer.timeout.connect(self._refresh_positions)

        self.restore_window_state()
        self.statusBar().showMessage("Loading instruments...")

    def _place_order(self, order_details_from_panel: dict):
        """Handles the buy signal from the panel by showing a confirmation dialog."""
        if not order_details_from_panel.get('strikes'):
            QMessageBox.warning(self, "Error", "No valid strikes found for the order.")
            logger.warning("place_order called with no strikes in details.")
            return

        if self.active_order_confirmation_dialog:
            self.active_order_confirmation_dialog.reject()

        order_details_for_dialog = order_details_from_panel.copy()

        symbol = order_details_for_dialog.get('symbol')
        if not symbol or symbol not in self.instrument_data:
            QMessageBox.warning(self, "Error", "Symbol data not found.")
            return

        instrument_lot_quantity = self.instrument_data[symbol].get('lot_size', 1)
        num_lots = order_details_for_dialog.get('lot_size', 1)
        order_details_for_dialog['total_quantity_per_strike'] = num_lots * instrument_lot_quantity
        order_details_for_dialog['product'] = self.settings.get('default_product', 'MIS')

        dialog = OrderConfirmationDialog(self, order_details_for_dialog)

        self.active_order_confirmation_dialog = dialog

        dialog.refresh_requested.connect(self._on_order_confirmation_refresh_request)
        dialog.finished.connect(lambda: setattr(self, 'active_order_confirmation_dialog', None))

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._execute_orders(order_details_for_dialog)

    def _on_paper_trade_update(self, order_data: dict):
        """Logs completed paper trades and triggers an immediate UI refresh."""
        if order_data and order_data.get('status') == 'COMPLETE':
            # FIX: Calculate PNL for exit trades before logging
            transaction_type = order_data.get('transaction_type')
            tradingsymbol = order_data.get('tradingsymbol')

            # Check if it's an exit of a long position
            if transaction_type == self.trader.TRANSACTION_TYPE_SELL:
                original_position = self.position_manager.get_position(tradingsymbol)
                if original_position and original_position.quantity > 0:
                    exit_price = order_data.get('average_price', 0.0)
                    entry_price = original_position.average_price
                    quantity = order_data.get('filled_quantity', 0)

                    realized_pnl = (exit_price - entry_price) * quantity
                    order_data['pnl'] = realized_pnl
                    #self.pnl_logger.log_pnl(datetime.now(), realized_pnl)

            # (Future improvement: add logic for exiting short positions if implemented)

            self.trade_logger.log_trade(order_data)

            logger.debug("Paper trade complete, triggering immediate account info refresh.")
            self._update_account_info()
            self._update_account_summary_widget()
            self._refresh_positions()  # Refresh positions after a trade

    def _apply_dark_theme(self):
        # Force Windows palette to match app colors


        # Windows-specific: Force dark mode at OS level
        try:
            # Tell Windows to use dark mode for this app
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
            )
        except:
            pass  # Ignore if it fails

        app = QApplication.instance()
        palette = QPalette()

        # Force ALL palette colors to match dialog background
        dark_bg = QColor(22, 26, 37)  # #161A25
        light_text = QColor(224, 224, 224)  # #E0E0E0

        palette.setColor(QPalette.Window, dark_bg)
        palette.setColor(QPalette.Base, dark_bg)
        palette.setColor(QPalette.AlternateBase, dark_bg)
        palette.setColor(QPalette.Button, dark_bg)
        palette.setColor(QPalette.WindowText, light_text)
        palette.setColor(QPalette.Text, light_text)
        palette.setColor(QPalette.ButtonText, light_text)
        palette.setColor(QPalette.BrightText, light_text)

        # Force title bar colors specifically
        palette.setColor(QPalette.Dark, dark_bg)
        palette.setColor(QPalette.Shadow, dark_bg)

        app.setPalette(palette)
        app.setStyle('Fusion')  # Better dark theme support

        # Your existing stylesheet with title bar fix
        self.setStyleSheet("""
            QMainWindow { 
                background-color: #0f0f0f !important; 
                color: #ffffff;
                border: 1px solid #333;
            }
            /* Remove widget spacing */
            QWidget {
                margin: 0px;
                padding: 0px;
            }

            /* Aggressive QMessageBox title bar fix */
            QMessageBox {
                background-color: #161A25 !important;
                color: #E0E0E0 !important;
                border: 1px solid #3A4458;
                border-radius: 8px;
            }
            QMessageBox { border: none; margin: 0px; }

            /* Multiple selectors to force title bar color */
            QMessageBox::title,
            QMessageBox QWidget,
            QMessageBox * {
                background-color: #161A25 !important;
                color: #E0E0E0 !important;
            }

            QMessageBox QLabel {
                color: #E0E0E0 !important;
                background-color: #161A25 !important;
                font-size: 13px;
            }
            QMessageBox QPushButton {
                background-color: #212635 !important;
                color: #E0E0E0 !important;
                border: 1px solid #3A4458;
                border-radius: 5px;
                padding: 8px 16px;
                font-weight: 500;
                min-width: 70px;
            }
            QMessageBox QPushButton:hover {
                background-color: #29C7C9 !important;
                color: #161A25 !important;
                border-color: #29C7C9;
            }
            QMessageBox QPushButton:pressed {
                background-color: #1f8a8c !important;
            }

            /* Dialog backgrounds */
            QDialog {
                background-color: #161A25;
                color: #E0E0E0;
            }

            QStatusBar {
                background-color: #161A25;
                color: #A0A0A0;
                border-top: 1px solid #3A4458;
                padding: 4px 8px;
                font-size: 12px;
            }
            QDockWidget { 
                background-color: #1a1a1a; 
                color: #fff; 
                border: 1px solid #333; 
            }
            QDockWidget::title { 
                background-color: #2a2a2a; 
                padding: 5px; 
                border-bottom: 1px solid #333; 
            }
        """)
    def _init_background_workers(self):
        self.instrument_loader = InstrumentLoader(self.real_kite_client)
        self.instrument_loader.instruments_loaded.connect(self._on_instruments_loaded)
        self.instrument_loader.error_occurred.connect(self._on_api_error)
        self.instrument_loader.start()

        self.market_data_worker = MarketDataWorker(self.api_key, self.access_token)
        self.market_data_worker.data_received.connect(self._on_market_data)
        self.market_data_worker.connection_status_changed.connect(self._on_network_status_changed)
        self.market_data_worker.start()

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update_ui)
        self.update_timer.start(REFRESH_INTERVAL_MS)


    def _setup_ui(self):
        # Create main container with custom title bar
        main_container = QWidget()
        self.setCentralWidget(main_container)

        # Main layout with custom title bar at top
        container_layout = QVBoxLayout(main_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Add custom title bar
        container_layout.addWidget(self.custom_title_bar)

        # Content area
        content_widget = QWidget()
        container_layout.addWidget(content_widget)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Add header toolbar
        self.header = HeaderToolbar()
        content_layout.addWidget(self.header)

        # Main content area
        main_content_widget = QWidget()
        content_layout.addWidget(main_content_widget)
        main_content_layout = QVBoxLayout(main_content_widget)
        main_content_layout.setContentsMargins(0, 0, 0, 0)
        main_content_layout.setSpacing(0)

        self._create_main_widgets()

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setHandleWidth(1)
        self.main_splitter.setStyleSheet("""
            QSplitter::handle { 
                background-color: #2A3140; 
                border: none;
            } 
            QSplitter::handle:hover { 
                background-color: #3A4458; 
            }
        """)
        left_splitter = self._create_left_column()
        self.main_splitter.addWidget(left_splitter)

        center_column = self._create_center_column()
        center_widget = QWidget()
        center_widget.setLayout(center_column)
        self.main_splitter.addWidget(center_widget)

        fourth_column = self._create_fourth_column()
        fourth_widget = QWidget()
        fourth_widget.setLayout(fourth_column)
        self.main_splitter.addWidget(fourth_widget)

        self.main_splitter.setSizes([250, 600, 350])
        main_content_layout.addWidget(self.main_splitter)

        # Setup menu bar in custom title bar
        self._setup_menu_bar()

        QTimer.singleShot(3000, self._update_account_info)

    def _create_main_widgets(self):
        self.buy_exit_panel = BuyExitPanel(self.trader)
        self.buy_exit_panel.setMinimumSize(200, 300)
        self.account_summary = AccountSummaryWidget()
        self.account_summary.setMinimumHeight(200)
        self.strike_ladder = StrikeLadderWidget(self.real_kite_client)
        self.strike_ladder.setMinimumWidth(500)
        if hasattr(self.strike_ladder, 'setMaximumWidth'):
            self.strike_ladder.setMaximumWidth(800)
            self.strike_ladder.setMaximumHeight(700)
        self.inline_positions_table = PositionsTable(config_manager=self.config_manager)
        self.inline_positions_table.setMinimumWidth(300)
        self.inline_positions_table.setMinimumHeight(200)

    def _create_left_column(self) -> QSplitter:
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(1)  # ← Change from 0 to 1
        splitter.setStyleSheet("""
            QSplitter::handle { 
                background-color: #2A3140; 
                border: none;
            } 
            QSplitter::handle:hover { 
                background-color: #3A4458; 
            }
        """)
        splitter.addWidget(self.buy_exit_panel)
        splitter.addWidget(self.account_summary)
        splitter.setSizes([400, 200])
        return splitter

    def _create_center_column(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.addWidget(self.strike_ladder, 1)
        return layout

    def _create_fourth_column(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.addWidget(self.inline_positions_table)
        return layout

    def _setup_menu_bar(self):
        """Setup menu bar in custom title bar instead of main window"""
        menubar, menu_actions = create_enhanced_menu_bar(self)

        # Add menu bar to custom title bar instead of main window
        self.custom_title_bar.set_menu_bar(menubar)

        # Connect menu actions (keep all your existing connections)
        menu_actions['refresh'].triggered.connect(self._refresh_data)
        menu_actions['exit'].triggered.connect(self.close)
        menu_actions['positions'].triggered.connect(self._show_positions_dialog)
        menu_actions['pnl_history'].triggered.connect(self._show_pnl_history_dialog)
        menu_actions['pending_orders'].triggered.connect(self._show_pending_orders_dialog)
        menu_actions['orders'].triggered.connect(self._show_order_history_dialog)
        menu_actions['performance'].triggered.connect(self._show_performance_dialog)
        menu_actions['settings'].triggered.connect(self._show_settings)
        menu_actions['option_chain'].triggered.connect(self._show_option_chain_dialog)
        menu_actions['refresh_positions'].triggered.connect(self._refresh_positions)
        menu_actions['about'].triggered.connect(self._show_about)
        menu_actions['market_monitor'].triggered.connect(self._show_market_monitor_dialog)

    def _show_order_history_dialog(self):
        if not hasattr(self, 'order_history_dialog') or self.order_history_dialog is None:
            self.order_history_dialog = OrderHistoryDialog(self)
            self.order_history_dialog.refresh_requested.connect(
                lambda: self.order_history_dialog.update_orders(self.trade_logger.get_all_trades()))
        all_trades = self.trade_logger.get_all_trades()
        self.order_history_dialog.update_orders(all_trades)
        self.order_history_dialog.show()
        self.order_history_dialog.activateWindow()

    def _show_market_monitor_dialog(self):
        """Creates and shows a new Market Monitor dialog instance."""
        try:
            dialog = MarketMonitorDialog(
                real_kite_client=self.real_kite_client,
                market_data_worker=self.market_data_worker,
                config_manager=self.config_manager,
                parent=self
            )
            # Add to list to keep a reference and manage multiple windows
            self.market_monitor_dialogs.append(dialog)
            # Connect the finished signal to a cleanup slot
            dialog.finished.connect(lambda: self._on_market_monitor_closed(dialog))
            dialog.show()
        except Exception as e:
            logger.error(f"Failed to create Market Monitor dialog: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Could not open Market Monitor:\n{e}")

    def _on_market_monitor_closed(self, dialog: QDialog):
        """Removes the market monitor dialog from the list when it's closed."""
        if dialog in self.market_monitor_dialogs:
            self.market_monitor_dialogs.remove(dialog)
            logger.info(f"Closed a Market Monitor window. {len(self.market_monitor_dialogs)} remain open.")

    def _show_option_chain_dialog(self):
        if not self.instrument_data:
            QMessageBox.warning(self, "Data Not Ready",
                                "Instrument data is still loading. Please try again in a moment.")
            return

        if self.option_chain_dialog is None:
            # --- FIX: Instantiate the dialog with parent=None to make it a separate window ---
            self.option_chain_dialog = OptionChainDialog(
                self.real_kite_client,
                self.instrument_data,
                parent=None  # This makes it a top-level, independent window
            )
            self.option_chain_dialog.finished.connect(lambda: setattr(self, 'option_chain_dialog', None))

        self.option_chain_dialog.show()
        self.option_chain_dialog.activateWindow()
        self.option_chain_dialog.raise_()

    def _connect_signals(self):
        self.header.settings_changed.connect(self._on_settings_changed)
        self.header.lot_size_changed.connect(self._on_lot_size_changed)
        self.header.exit_all_clicked.connect(self._exit_all_positions)
        self.header.settings_button.clicked.connect(self._show_settings)
        self.buy_exit_panel.buy_clicked.connect(self._place_order)
        self.buy_exit_panel.exit_clicked.connect(self._exit_option_positions)
        self.strike_ladder.strike_selected.connect(self._on_single_strike_selected)
        self.inline_positions_table.exit_requested.connect(self._exit_position)
        self.account_summary.pnl_history_requested.connect(self._show_pnl_history_dialog)
        self.position_manager.pending_orders_updated.connect(self._update_pending_order_widgets)
        self.inline_positions_table.refresh_requested.connect(self._refresh_positions)

    def _setup_position_manager(self):
        self.position_manager.positions_updated.connect(self._on_positions_updated)
        self.position_manager.position_added.connect(self._on_position_added)
        self.position_manager.position_removed.connect(self._on_position_removed)
        self.position_manager.refresh_completed.connect(self._on_refresh_completed)
        self.position_manager.api_error_occurred.connect(self._on_api_error)

    def _on_instruments_loaded(self, data: dict):
        """
        Handles loaded instruments, populates the header, and correctly applies
        saved settings to prevent startup errors.
        """
        self.instrument_data = data
        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_instrument_data(data)

        self.position_manager.set_instrument_data(data)
        self.strike_ladder.set_instrument_data(data)

        symbols = sorted(data.keys())
        self.header.set_symbols(symbols)

        # Apply saved settings *before* triggering any updates.
        default_symbol = self.settings.get('default_symbol', 'NIFTY')
        default_lots = self.settings.get('default_lots', 1)

        # Ensure the selected symbol is valid before setting it
        if default_symbol not in symbols:
            logger.warning(f"Saved symbol '{default_symbol}' not found in instruments. Falling back to NIFTY.")
            default_symbol = 'NIFTY' if 'NIFTY' in symbols else (symbols[0] if symbols else "")

        # Explicitly set the UI state only if a valid symbol is found
        if default_symbol:
            self.header.set_active_symbol(default_symbol)
            self.header.set_lot_size(default_lots)
            logger.info(f"Applied startup settings. Symbol: {default_symbol}, Lots: {default_lots}")

            # Now, with the correct state set, trigger the ladder update
            self._on_settings_changed(self.header.get_current_settings())
        else:
            logger.error("No valid symbols found in instrument data. Cannot initialize UI.")

        self._refresh_positions()
        self.statusBar().showMessage("Instruments loaded and settings applied.", 3000)

    def _on_instrument_error(self, error: str):
        logger.error(f"Instrument loading failed: {error}")
        QMessageBox.critical(self, "Error", f"Failed to load instruments:\n{error}")

    def _on_market_data(self, data: dict):
        self.strike_ladder.update_prices(data)
        self.position_manager.update_pnl_from_market_data(data)
        self._update_account_summary_widget()
        if self.positions_dialog and self.positions_dialog.isVisible():
            if hasattr(self.positions_dialog, 'update_market_data'):
                self.positions_dialog.update_market_data(data)
        ladder_data = self.strike_ladder.get_ladder_data()
        if ladder_data:
            atm_strike = self.strike_ladder.atm_strike
            interval = self.strike_ladder.get_strike_interval()
            self.buy_exit_panel.update_strike_ladder(atm_strike, interval, ladder_data)
        if self.performance_dialog and self.performance_dialog.isVisible():
            self._update_performance()
        # --- FIX: Explicitly forward data to Market Monitor ---
        for dialog in self.market_monitor_dialogs[:]:
            if dialog.isVisible():
                # The dialog's internal _connect_signals method already connects
                # market_data_worker.data_received to its _on_ticks_received slot.
                # Therefore, we just need to ensure the worker's signal is emitted.
                # The logic below is implicitly handled by the worker's signal.
                pass

    def _get_current_price(self, symbol: str) -> Optional[float]:
        if not self.real_kite_client: return None
        try:
            index_map = {
                'NIFTY': 'NIFTY 50',
                'BANKNIFTY': 'NIFTY BANK',
                'FINNIFTY': 'NIFTY FIN SERVICE',
                'MIDCPNIFTY': 'NIFTY MID SELECT'
            }
            underlying_instrument_name = index_map.get(symbol.upper(), symbol.upper())
            instrument_for_ltp = f"NSE:{underlying_instrument_name}"
            ltp_data = self.real_kite_client.ltp(instrument_for_ltp)
            if ltp_data and instrument_for_ltp in ltp_data:
                return ltp_data[instrument_for_ltp]['last_price']
            else:
                logger.warning(f"LTP data not found for {instrument_for_ltp}. Response: {ltp_data}")
                return None
        except Exception as e:
            logger.error(f"Failed to get current price for {symbol}: {e}")
            return None


    def _update_market_subscriptions(self):
        tokens_to_subscribe = set()

        # Get tokens for the strike ladder (current behavior)
        if self.strike_ladder and self.strike_ladder.contracts:
            for strike_val_dict in self.strike_ladder.contracts.values():
                for contract_obj in strike_val_dict.values():
                    if contract_obj and contract_obj.instrument_token:
                        tokens_to_subscribe.add(contract_obj.instrument_token)

        # Get tokens for the index (current behavior)
        current_settings = self.header.get_current_settings()
        underlying_symbol = current_settings.get('symbol')
        if underlying_symbol and underlying_symbol in self.instrument_data:
            index_token = self.instrument_data[underlying_symbol].get('instrument_token')
            if index_token:
                tokens_to_subscribe.add(index_token)

        # Get tokens for open positions (current behavior)
        for pos in self.position_manager.get_all_positions():
            if pos.contract and pos.contract.instrument_token:
                tokens_to_subscribe.add(pos.contract.instrument_token)


        if self.market_data_worker:
            self.market_data_worker.set_instruments(tokens_to_subscribe)

    def _periodic_api_health_check(self):
        logger.debug("Performing periodic API health check.")
        if self.profile_circuit_breaker.can_execute() or self.margin_circuit_breaker.can_execute():
            self._update_account_info()
        else:
            logger.debug("API health check skipped - circuit breakers are OPEN.")

    def _update_account_info(self):
        if isinstance(self.trader, PaperTradingManager):
            try:
                profile = self.trader.profile()
                margins_data = self.trader.margins()
                user_id = profile.get("user_id", "PAPER")
                balance = margins_data.get("equity", {}).get("net", 0.0)
                self.last_successful_margins = margins_data
                self.last_successful_user_id = user_id
                self.last_successful_balance = balance
                self.header.update_account_info(user_id, balance)
                logger.debug(f"Paper account info updated. Balance: {balance}")
            except Exception as e:
                logger.error(f"Failed to get paper account info: {e}")
            return

        if not self.real_kite_client or not hasattr(self.real_kite_client,
                                                    'access_token') or not self.real_kite_client.access_token:
            logger.debug("Skipping live account info update: Not a valid Kite client.")
            return

        if self.profile_circuit_breaker.can_execute():
            try:
                profile = self.real_kite_client.profile()
                if profile and isinstance(profile, dict):
                    self.last_successful_user_id = profile.get("user_id", "Unknown")
                    self.profile_circuit_breaker.record_success()
                    api_logger.info("Profile fetch successful.")
                else:
                    logger.warning(f"Profile fetch returned unexpected data type: {type(profile)}")
                    self.profile_circuit_breaker.record_failure()
                    api_logger.warning(f"Profile fetch: Unexpected data type {type(profile)}")
            except Exception as e:
                logger.warning(f"Profile fetch API call failed: {e}")
                self.profile_circuit_breaker.record_failure()
                api_logger.warning(f"Profile fetch failed: {e}")

        current_balance_to_display = self.last_successful_balance
        if self.margin_circuit_breaker.can_execute():
            try:
                margins_data = self.real_kite_client.margins()
                if margins_data and isinstance(margins_data, dict):
                    calculated_balance = 0
                    if 'equity' in margins_data and margins_data['equity'] is not None:
                        calculated_balance += margins_data['equity'].get('net', 0)
                    if 'commodity' in margins_data and margins_data['commodity'] is not None:
                        calculated_balance += margins_data['commodity'].get('net', 0)
                    self.last_successful_balance = calculated_balance
                    current_balance_to_display = self.last_successful_balance
                    self.margin_circuit_breaker.record_success()
                    api_logger.info(f"Margins fetch successful. Balance: {current_balance_to_display}")
                    self.rms_failures = 0
                else:
                    logger.warning(f"Margins fetch returned unexpected data type: {type(margins_data)}")
                    self.margin_circuit_breaker.record_failure()
                    api_logger.warning(f"Margins fetch: Unexpected data type {type(margins_data)}")
            except Exception as e:
                logger.error(f"Margins fetch API call failed: {e}")
                self.margin_circuit_breaker.record_failure()
                api_logger.error(f"Margins fetch failed: {e}")
                if self.margin_circuit_breaker.state == "OPEN":
                    self.statusBar().showMessage("⚠️ API issues (margins) - using cached data.", 5000)
        if hasattr(self, 'header'):
            self.header.update_account_info(self.last_successful_user_id, current_balance_to_display)

    def _get_account_balance_safe(self) -> float:
        return self.last_successful_balance

    def _on_positions_updated(self, positions: List[Position]):
        logger.debug(f"Received {len(positions)} positions from PositionManager for UI update.")

        # Update the pop-out positions dialog if it's open
        if self.positions_dialog and self.positions_dialog.isVisible():
            self.positions_dialog.update_positions(positions)

        # Update the inline positions table
        if self.inline_positions_table:
            # The inline table needs dicts, so we convert here
            positions_as_dicts = [
                {'tradingsymbol': p.tradingsymbol, 'quantity': p.quantity, 'average_price': p.average_price,
                 'last_price': p.ltp, 'pnl': p.pnl, 'exchange': p.exchange, 'product': p.product} for p in positions]
            self.inline_positions_table.update_positions(positions_as_dicts)

        self._update_performance()
        self._update_market_subscriptions()

    def _on_position_added(self, position: Position):
        logger.debug(f"Position added: {position.tradingsymbol}, forwarding to UI.")
        if self.positions_dialog and self.positions_dialog.isVisible():
            if hasattr(self.positions_dialog, 'positions_table') and hasattr(self.positions_dialog.positions_table,
                                                                             'add_position'):
                self.positions_dialog.positions_table.add_position(position)
            else:
                self._sync_positions_to_dialog()
        self._update_performance()

    def _on_position_removed(self, symbol: str):
        logger.debug(f"Position removed: {symbol}, forwarding to UI.")
        if self.positions_dialog and self.positions_dialog.isVisible():
            if hasattr(self.positions_dialog, 'positions_table') and hasattr(self.positions_dialog.positions_table,
                                                                             'remove_position'):
                self.positions_dialog.positions_table.remove_position(symbol)
            else:
                self._sync_positions_to_dialog()
        self._update_performance()

    def _on_refresh_completed(self, success: bool):
        if success:
            self.statusBar().showMessage("Positions refreshed successfully.", 2000)
            logger.info("Position refresh completed successfully via PositionManager.")
        else:
            self.statusBar().showMessage("Position refresh failed. Check logs.", 3000)
            logger.warning("Position refresh failed via PositionManager.")

    def _on_api_error(self, error_message: str):
        logger.error(f"PositionManager reported API error: {error_message}")
        self.statusBar().showMessage(f"API Error: {error_message}", 5000)

    def _show_positions_dialog(self):
        if self.positions_dialog is None:
            self.positions_dialog = OpenPositionsDialog(self)
            # Connect the dialog to the PositionManager's signal
            self.position_manager.positions_updated.connect(self.positions_dialog.update_positions)
            self.positions_dialog.refresh_requested.connect(self._refresh_positions)
            self.positions_dialog.position_exit_requested.connect(self._exit_position_from_dialog)
            self.position_manager.refresh_completed.connect(self.positions_dialog.on_refresh_completed)

        # Initial population of the dialog
        initial_positions = self.position_manager.get_all_positions()
        self.positions_dialog.update_positions(initial_positions)
        self.positions_dialog.show()
        self.positions_dialog.raise_()
        self.positions_dialog.activateWindow()

    def _show_pending_orders_dialog(self):
        if self.pending_orders_dialog is None:
            self.pending_orders_dialog = PendingOrdersDialog(self)
            self.position_manager.pending_orders_updated.connect(self.pending_orders_dialog.update_orders)
        self.pending_orders_dialog.update_orders(self.position_manager.get_pending_orders())
        self.pending_orders_dialog.show()
        self.pending_orders_dialog.activateWindow()

    def _sync_positions_to_dialog(self):
        if not self.positions_dialog or not self.positions_dialog.isVisible():
            return
        positions_list = self.position_manager.get_all_positions()
        if hasattr(self.positions_dialog, 'positions_table'):
            table_widget = self.positions_dialog.positions_table
            if hasattr(table_widget, 'update_positions'):
                table_widget.update_positions(positions_list)
            elif hasattr(table_widget, 'clear_all_positions') and hasattr(table_widget, 'add_position'):
                table_widget.clear_all_positions()
                for position in positions_list:
                    table_widget.add_position(position)
            else:
                logger.warning("OpenPositionsDialog's table does not have suitable methods for syncing.")
        else:
            logger.warning("OpenPositionsDialog does not have 'positions_table' attribute for syncing.")

    def _show_pnl_history_dialog(self):
        if not hasattr(self, 'pnl_history_dialog') or self.pnl_history_dialog is None:
            self.pnl_history_dialog = PnlHistoryDialog(mode=self.trading_mode, parent=self)
        self.pnl_history_dialog.show()
        self.pnl_history_dialog.activateWindow()
        self.pnl_history_dialog.raise_()


    def _show_performance_dialog(self):
        if self.performance_dialog is None:
            # FIX: Pass the trading mode to the dialog's constructor
            self.performance_dialog = PerformanceDialog(mode=self.trading_mode, parent=self)

        all_trades = self.trade_logger.get_all_trades()
        # Consider only trades with non-zero PNL for performance metrics
        completed_trades = [trade for trade in all_trades if trade.get('pnl', 0.0) != 0.0]
        total_pnl = sum(trade.get('pnl', 0.0) for trade in completed_trades)
        winning_trades = [trade for trade in completed_trades if trade.get('pnl', 0.0) > 0]
        losing_trades = [trade for trade in completed_trades if trade.get('pnl', 0.0) < 0]

        total_completed_trades = len(completed_trades)
        metrics = {
            'total_trades': total_completed_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'total_pnl': total_pnl,
            'win_rate': (len(winning_trades) / total_completed_trades * 100) if total_completed_trades else 0,
            'avg_profit': (sum(t.get('pnl', 0.0) for t in winning_trades) / len(
                winning_trades)) if winning_trades else 0.0,
            'avg_loss': abs(
                sum(t.get('pnl', 0.0) for t in losing_trades) / len(losing_trades)) if losing_trades else 0.0,
        }
        self.performance_dialog.update_metrics(metrics)

        self.performance_dialog.show()
        self.performance_dialog.raise_()
        self.performance_dialog.activateWindow()

    def _update_pending_order_widgets(self, pending_orders: List[Dict]):
        screen_geometry = self.screen().availableGeometry()
        spacing = 10
        widget_height = 110 + spacing
        current_order_ids = {order['order_id'] for order in pending_orders}
        existing_widget_ids = set(self.pending_order_widgets.keys())

        for order_id in existing_widget_ids - current_order_ids:
            widget = self.pending_order_widgets.pop(order_id)
            widget.close_widget()

        for i, order_data in enumerate(pending_orders):
            order_id = order_data['order_id']
            if order_id not in self.pending_order_widgets:
                widget = OrderStatusWidget(order_data, self)
                widget.cancel_requested.connect(self._cancel_order_by_id)
                widget.modify_requested.connect(self._show_modify_order_dialog)
                self.pending_order_widgets[order_id] = widget

            widget = self.pending_order_widgets[order_id]
            x_pos = screen_geometry.right() - widget.width() - spacing
            y_pos = screen_geometry.bottom() - (widget_height * (i + 1))
            widget.move(x_pos, y_pos)

        if pending_orders and not self.pending_order_refresh_timer.isActive():
            logger.info("Pending orders detected. Starting 1-second position refresh timer.")
            self.pending_order_refresh_timer