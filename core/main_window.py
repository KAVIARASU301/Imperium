# core/main_window.py
import logging
import os
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta, time, date
from PySide6.QtWidgets import (QMainWindow, QPushButton, QApplication, QWidget, QVBoxLayout,
                               QMessageBox, QDialog, QSplitter, QHBoxLayout, QBoxLayout)
from PySide6.QtCore import Qt, QTimer, QUrl, QByteArray, QPoint
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
from dialogs.market_monitor_dialog import MarketMonitorDialog
from core.cvd.cvd_engine import CVDEngine
from dialogs.cvd_chart_dialog import CVDChartDialog
from dialogs.cvd_market_monitor_dialog import CVDMarketMonitorDialog

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

        self.menu_bar = None
        layout.addStretch()
        self.create_window_controls(layout)

    def set_menu_bar(self, menu_bar):
        self.menu_bar = menu_bar
        layout = self.layout()
        if isinstance(layout, QBoxLayout):
            layout.insertWidget(0, menu_bar)
        menu_bar.setStyleSheet("""
            QMenuBar {
                background-color: transparent; color: #E0E0E0; border: none;
                font-size: 13px; padding: 4px 0px;
            }
            QMenuBar::item {
                background-color: transparent; padding: 6px 12px;
                border-radius: 4px; margin: 0px 2px;
            }
            QMenuBar::item:selected { background-color: #29C7C9; color: #161A25; }
            QMenuBar::item:pressed { background-color: #1f8a8c; color: #161A25; }
        """)

    def create_window_controls(self, layout):
        button_style = """
            QPushButton {
                background-color: transparent; border: none; color: #E0E0E0;
                font-size: 16px; font-weight: bold; padding: 0px; margin: 0px;
                width: 45px; height: 32px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
            QPushButton:pressed { background-color: rgba(255, 255, 255, 0.2); }
        """
        maximize_button_style = """
            QPushButton {
                background-color: transparent; border: none; color: #E0E0E0;
                font-size: 14px; font-weight: bold; padding: 0px; margin: 0px;
                width: 45px; height: 32px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
            QPushButton:pressed { background-color: rgba(255, 255, 255, 0.2); }
        """
        close_button_style = button_style + """
            QPushButton:hover { background-color: #e74c3c; color: white; }
            QPushButton:pressed { background-color: #c0392b; color: white; }
        """

        minimize_btn = QPushButton("−")
        minimize_btn.setStyleSheet(button_style)
        minimize_btn.clicked.connect(self.parent_window.showMinimized)
        layout.addWidget(minimize_btn)

        self.maximize_btn = QPushButton("□")
        self.maximize_btn.setStyleSheet(maximize_button_style)
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        layout.addWidget(self.maximize_btn)

        close_btn = QPushButton("×")
        close_btn.setStyleSheet(close_button_style)
        close_btn.clicked.connect(self.parent_window.close)
        layout.addWidget(close_btn)

    def toggle_maximize(self):
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
            self.maximize_btn.setText("□")
        else:
            self.parent_window.showMaximized()
            self.maximize_btn.setText("❐")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.parent_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.dragging:
            if not self.parent_window.isMaximized():
                self.parent_window.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximize()
            event.accept()


class APICircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = "CLOSED"

    def can_execute(self) -> bool:
        if self.state == "CLOSED": return True
        if self.state == "OPEN":
            if self._should_attempt_reset():
                self.state = "HALF_OPEN"
                return True
            return False
        return True

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
        if not self.last_failure_time: return True
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
        self.trading_mode = 'paper' if isinstance(trader, PaperTradingManager) else 'live'
        self.trade_logger = TradeLogger(mode=self.trading_mode)
        # self.pnl_logger = PnlLogger(mode=self.trading_mode)
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
        self.api_health_check_timer = QTimer(self)
        self.api_health_check_timer.timeout.connect(self._periodic_api_health_check)
        self.api_health_check_timer.start(30000)
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
        self.network_status = "Initializing..."
        self.cvd_engine = CVDEngine()
        self.cvd_monitor_dialog = None
        # CVD monitor symbols (v1 – fixed indices)
        self.cvd_symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
        self.active_cvd_tokens: set[int] = set()

        # --- FIX: UI Throttling Implementation ---
        self._latest_market_data = {}
        self._ui_update_needed = False
        self.ui_update_timer = QTimer(self)
        self.ui_update_timer.timeout.connect(self._update_throttled_ui)
        self.ui_update_timer.start(100)  # Update UI at most every 100ms

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

        self.pending_order_refresh_timer = QTimer(self)
        self.pending_order_refresh_timer.setInterval(1000)
        self.pending_order_refresh_timer.timeout.connect(self._refresh_positions)

        self.restore_window_state()
        self.statusBar().showMessage("Loading instruments...")

    def _on_market_data(self, data: list):
        """
        Receives raw ticks from MarketDataWorker.
        Stores the latest tick per instrument_token.
        """
        for tick in data:
            token = tick.get('instrument_token')
            if token is not None:
                self._latest_market_data[token] = tick

        self._ui_update_needed = True

    def _update_throttled_ui(self):
        """
        Throttled UI update loop.
        IMPORTANT:
        - UI is throttled
        - Market data is NOT cleared
        """

        if not self._ui_update_needed:
            return

        # Use latest known tick per instrument
        ticks_to_process = list(self._latest_market_data.values())
        # 🔍 DEBUG: confirm which instruments are being processed
        logger.debug(
            f"Processing {len(ticks_to_process)} ticks: "
            f"{[t.get('instrument_token') for t in ticks_to_process]}"
        )
        # --- Core market consumers ---
        self.strike_ladder.update_prices(ticks_to_process)
        self.position_manager.update_pnl_from_market_data(ticks_to_process)
        self.cvd_engine.process_ticks(ticks_to_process)

        # --- UI updates ---
        self._update_account_summary_widget()

        if self.positions_dialog and self.positions_dialog.isVisible():
            if hasattr(self.positions_dialog, 'update_market_data'):
                self.positions_dialog.update_market_data(ticks_to_process)

        ladder_data = self.strike_ladder.get_ladder_data()
        if ladder_data:
            atm_strike = self.strike_ladder.atm_strike
            interval = self.strike_ladder.get_strike_interval()
            self.buy_exit_panel.update_strike_ladder(atm_strike, interval, ladder_data)

        if self.performance_dialog and self.performance_dialog.isVisible():
            self._update_performance()

        # Reset UI throttle flag ONLY
        self._ui_update_needed = False

    def _apply_dark_theme(self):
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
            )
        except:
            pass

        app = QApplication.instance()
        palette = QPalette()
        dark_bg = QColor(22, 26, 37)
        light_text = QColor(224, 224, 224)

        palette.setColor(QPalette.Window, dark_bg)
        palette.setColor(QPalette.Base, dark_bg)
        palette.setColor(QPalette.AlternateBase, dark_bg)
        palette.setColor(QPalette.Button, dark_bg)
        palette.setColor(QPalette.WindowText, light_text)
        palette.setColor(QPalette.Text, light_text)
        palette.setColor(QPalette.ButtonText, light_text)
        palette.setColor(QPalette.BrightText, light_text)
        palette.setColor(QPalette.Dark, dark_bg)
        palette.setColor(QPalette.Shadow, dark_bg)

        app.setPalette(palette)
        app.setStyle('Fusion')

        self.setStyleSheet("""
            QMainWindow { background-color: #0f0f0f !important; color: #ffffff; border: 1px solid #333; }
            QWidget { margin: 0px; padding: 0px; }
            QMessageBox { background-color: #161A25 !important; color: #E0E0E0 !important; border: 1px solid #3A4458; border-radius: 8px; }
            QMessageBox { border: none; margin: 0px; }
            QMessageBox::title, QMessageBox QWidget, QMessageBox * { background-color: #161A25 !important; color: #E0E0E0 !important; }
            QMessageBox QLabel { color: #E0E0E0 !important; background-color: #161A25 !important; font-size: 13px; }
            QMessageBox QPushButton { background-color: #212635 !important; color: #E0E0E0 !important; border: 1px solid #3A4458; border-radius: 5px; padding: 8px 16px; font-weight: 500; min-width: 70px; }
            QMessageBox QPushButton:hover { background-color: #29C7C9 !important; color: #161A25 !important; border-color: #29C7C9; }
            QMessageBox QPushButton:pressed { background-color: #1f8a8c !important; }
            QDialog { background-color: #161A25; color: #E0E0E0; }
            QStatusBar { background-color: #161A25; color: #A0A0A0; border-top: 1px solid #3A4458; padding: 4px 8px; font-size: 12px; }
            QDockWidget { background-color: #1a1a1a; color: #fff; border: 1px solid #333; }
            QDockWidget::title { background-color: #2a2a2a; padding: 5px; border-bottom: 1px solid #333; }
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
            transaction_type = order_data.get('transaction_type')
            tradingsymbol = order_data.get('tradingsymbol')

            if transaction_type == self.trader.TRANSACTION_TYPE_SELL:
                original_position = self.position_manager.get_position(tradingsymbol)
                if original_position and original_position.quantity > 0:
                    exit_price = order_data.get('average_price', 0.0)
                    entry_price = original_position.average_price
                    quantity = order_data.get('filled_quantity', 0)

                    realized_pnl = (exit_price - entry_price) * quantity
                    order_data['pnl'] = realized_pnl

            self.trade_logger.log_trade(order_data)
            # 🔊 PLAY SOUND ONLY FOR NON-BULK, NON-MANUAL EXITS
            if transaction_type == self.trader.TRANSACTION_TYPE_SELL:
                # Sound should NOT be played here for bulk exits
                # Bulk exit sound is handled centrally in _finalize_bulk_exit_result()
                pass

            logger.debug("Paper trade complete, triggering immediate account info refresh.")
            self._update_account_info()
            self._update_account_summary_widget()
            self._refresh_positions()

    def _setup_ui(self):
        main_container = QWidget()
        self.setCentralWidget(main_container)

        container_layout = QVBoxLayout(main_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        container_layout.addWidget(self.custom_title_bar)

        content_widget = QWidget()
        container_layout.addWidget(content_widget)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.header = HeaderToolbar()
        content_layout.addWidget(self.header)

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
        splitter.setHandleWidth(1)
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
        menubar, menu_actions = create_enhanced_menu_bar(self)
        self.custom_title_bar.set_menu_bar(menubar)
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
        menu_actions['cvd_chart'].triggered.connect(self._show_cvd_chart_dialog)
        menu_actions['cvd_market_monitor'].triggered.connect(self._show_cvd_market_monitor_dialog)

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
            # FIX: Pass the shared market_data_worker to the dialog
            dialog = MarketMonitorDialog(
                real_kite_client=self.real_kite_client,
                market_data_worker=self.market_data_worker,
                config_manager=self.config_manager,
                parent=self
            )

            self.market_monitor_dialogs.append(dialog)
            dialog.destroyed.connect(lambda: self._on_market_monitor_closed(dialog))
            dialog.show()
        except Exception as e:
            logger.error(f"Failed to create Market Monitor dialog: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Could not open Market Monitor:\n{e}")


    def _show_cvd_chart_dialog(self):
        """
        Opens TradingView-style CVD candlestick chart
        for the nearest FUTURE of the current symbol.
        """
        current_settings = self.header.get_current_settings()
        symbol = current_settings.get("symbol")

        if not symbol:
            QMessageBox.warning(self, "CVD Chart", "No symbol selected.")
            return

        fut_token = self._get_nearest_future_token(symbol)
        if not fut_token:
            QMessageBox.warning(
                self,
                "CVD Chart",
                f"No FUT contract found for {symbol}."
            )
            return
        self.active_cvd_tokens.add(fut_token)
        self._update_market_subscriptions()
        try:
            dlg = CVDChartDialog(
                kite=self.real_kite_client,
                instrument_token=fut_token,
                symbol=f"{symbol} FUT",
                cvd_engine=self.cvd_engine,
                parent=self
            )
            dlg.destroyed.connect(lambda: self._on_cvd_dialog_closed(fut_token))
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()

        except Exception as e:
            logger.error("Failed to open CVD Chart dialog", exc_info=True)
            QMessageBox.critical(
                self,
                "CVD Chart Error",
                f"Failed to open CVD chart:\n{e}"
            )


    def _on_cvd_dialog_closed(self, token: int):
        self.active_cvd_tokens.discard(token)
        self._update_market_subscriptions()

    def _show_cvd_market_monitor_dialog(self):
        symbol_to_token = {}

        for symbol in self.cvd_symbols:
            fut_token = self._get_nearest_future_token(symbol)
            if fut_token:
                symbol_to_token[symbol] = fut_token
                self.active_cvd_tokens.add(fut_token)

        if not symbol_to_token:
            QMessageBox.warning(self, "CVD Monitor", "No futures available.")
            return

        self._update_market_subscriptions()

        dlg = CVDMarketMonitorDialog(
            kite=self.real_kite_client,
            cvd_engine=self.cvd_engine,
            symbol_to_token=symbol_to_token,
            parent=self
        )

        dlg.destroyed.connect(self._on_cvd_market_monitor_closed)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_cvd_market_monitor_closed(self):
        self.active_cvd_tokens.clear()
        self._update_market_subscriptions()

    def _on_market_monitor_closed(self, dialog: QDialog):
        """Removes the market monitor dialog from the list when it's closed."""
        if dialog in self.market_monitor_dialogs:
            # FIX: Ensure the dialog unsubscribes from symbols when closed
            dialog.unsubscribe_all()
            self.market_monitor_dialogs.remove(dialog)
            logger.info(f"Closed a Market Monitor window. {len(self.market_monitor_dialogs)} remain open.")

    def _show_option_chain_dialog(self):
        if not self.instrument_data:
            QMessageBox.warning(self, "Data Not Ready",
                                "Instrument data is still loading. Please try again in a moment.")
            return

        if self.option_chain_dialog is None:
            self.option_chain_dialog = OptionChainDialog(
                self.real_kite_client,
                self.instrument_data,
                parent=None
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
        self.inline_positions_table.modify_sl_tp_requested.connect(self._show_modify_sl_tp_dialog)
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
        self.instrument_data = data
        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_instrument_data(data)

        self.position_manager.set_instrument_data(data)
        self.strike_ladder.set_instrument_data(data)

        symbols = sorted(data.keys())
        self.header.set_symbols(symbols)

        default_symbol = self.settings.get('default_symbol', 'NIFTY')
        default_lots = self.settings.get('default_lots', 1)

        if default_symbol not in symbols:
            logger.warning(f"Saved symbol '{default_symbol}' not found in instruments. Falling back to NIFTY.")
            default_symbol = 'NIFTY' if 'NIFTY' in symbols else (symbols[0] if symbols else "")

        if default_symbol:
            self.header.set_active_symbol(default_symbol)
            self.header.set_lot_size(default_lots)
            logger.info(f"Applied startup settings. Symbol: {default_symbol}, Lots: {default_lots}")
            self._on_settings_changed(self.header.get_current_settings())
        else:
            logger.error("No valid symbols found in instrument data. Cannot initialize UI.")

        self._refresh_positions()
        self.statusBar().showMessage("Instruments loaded and settings applied.", 3000)

    def _on_instrument_error(self, error: str):
        logger.error(f"Instrument loading failed: {error}")
        QMessageBox.critical(self, "Error", f"Failed to load instruments:\n{error}")

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

        # 1. Get tokens from the strike ladder (existing logic)
        if self.strike_ladder and self.strike_ladder.contracts:
            for strike_val_dict in self.strike_ladder.contracts.values():
                for contract_obj in strike_val_dict.values():
                    if contract_obj and contract_obj.instrument_token:
                        tokens_to_subscribe.add(contract_obj.instrument_token)

        # 2. Get the underlying index token (existing logic)
        current_settings = self.header.get_current_settings()
        underlying_symbol = current_settings.get('symbol')
        if underlying_symbol and underlying_symbol in self.instrument_data:
            index_token = self.instrument_data[underlying_symbol].get('instrument_token')
            if index_token:
                tokens_to_subscribe.add(index_token)

        # 3. Get tokens from open positions (existing logic)
        for pos in self.position_manager.get_all_positions():
            if pos.contract and pos.contract.instrument_token:
                tokens_to_subscribe.add(pos.contract.instrument_token)

        # 4. *** ADD THIS NEW LOGIC ***
        #    Get tokens from all open market monitor dialogs.
        for monitor_dialog in self.market_monitor_dialogs:
            # Check if the dialog is open and has a token map
            if monitor_dialog and hasattr(monitor_dialog, 'token_to_chart_map'):
                tokens_to_subscribe.update(monitor_dialog.token_to_chart_map.keys())

        # 5. ---ADD CVD FUTURES SYMBOLS ---

        tokens_to_subscribe.update(self.active_cvd_tokens)

        # 6. Make the final, consolidated call
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

        if self.positions_dialog and self.positions_dialog.isVisible():
            self.positions_dialog.update_positions(positions)

        if self.inline_positions_table:
            positions_as_dicts = [self._position_to_dict(p) for p in positions]
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
            self.position_manager.positions_updated.connect(self.positions_dialog.update_positions)
            self.positions_dialog.refresh_requested.connect(self._refresh_positions)
            self.positions_dialog.position_exit_requested.connect(self._exit_position_from_dialog)
            self.positions_dialog.modify_sl_tp_requested.connect(self._show_modify_sl_tp_dialog)
            self.position_manager.refresh_completed.connect(self.positions_dialog.on_refresh_completed)

        initial_positions = self.position_manager.get_all_positions()
        self.positions_dialog.update_positions(initial_positions)
        self.positions_dialog.show()
        self.positions_dialog.raise_()
        self.positions_dialog.activateWindow()

    def _show_modify_sl_tp_dialog(self, symbol: str):
        position = self.position_manager.get_position(symbol)
        if not position:
            QMessageBox.warning(self, "Error", "Position not found.")
            return

        lots = abs(position.quantity) / position.contract.lot_size if position.contract.lot_size > 0 else 1
        dialog = QuickOrderDialog(self, position.contract, lots)
        dialog.populate_from_order(position)
        dialog.order_placed.connect(self._modify_sl_tp_for_position)

    def _modify_sl_tp_for_position(self, order_params: dict):
        contract = order_params.get('contract')
        if not contract:
            logger.error("Modify SL/TP failed: Contract object missing from order params.")
            return

        tradingsymbol = contract.tradingsymbol
        sl_price = order_params.get('stop_loss_price')
        tp_price = order_params.get('target_price')
        tsl_value = order_params.get('trailing_stop_loss')

        # Delegate the entire logic to the PositionManager
        self.position_manager.update_sl_tp_for_position(
            tradingsymbol, sl_price, tp_price, tsl_value
        )

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
            self.performance_dialog = PerformanceDialog(
                mode=self.trading_mode,
                parent=self
            )
            self.performance_dialog.finished.connect(
                lambda: setattr(self, 'performance_dialog', None)
            )

        # Let the dialog pull data from the PnL database itself
        self.performance_dialog.refresh()

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
            self.pending_order_refresh_timer.start()
        elif not pending_orders and self.pending_order_refresh_timer.isActive():
            logger.info("No more pending orders. Stopping refresh timer.")
            self.pending_order_refresh_timer.stop()

    def _cancel_order_by_id(self, order_id: str):
        try:
            self.trader.cancel_order(self.trader.VARIETY_REGULAR, order_id)
            logger.info(f"Cancellation request sent for order ID: {order_id}")
            self.position_manager.refresh_from_api()
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            QMessageBox.critical(self, "Cancel Failed", f"Could not cancel order {order_id}:\n{e}")

    def _show_about(self):
        """Displays a concise About dialog for the application."""
        about_text = """
        <div style="font-family:'Segoe UI',sans-serif; font-size:10.5pt; line-height:1.45;">
            <h2 style="margin-bottom:6px;">Blue Whale Trading Terminal</h2>

            <p style="margin:2px 0;"><b>Version:</b> 1.0.0</p>
            <p style="margin:2px 0;"><b>Owner:</b> Kaviarasu Murugan</p>
            <p style="margin:2px 0;"><b>Contact:</b> kaviarasu301@gmail.com</p>
            <p style="margin:2px 0;">© 2025 Kaviarasu</p>

            <hr style="margin:10px 0;">

            <p>
                Blue Whale Trading Terminal is a desktop application designed for
                fast, stable, and secure options trading and market monitoring.
                It provides real-time data visualization, order management,
                and analytical tools focused on intraday decision-making.
            </p>

            <p>
                The application is built using Python and PySide6, with integration
                to the Kite Connect API. It supports both live trading and paper
                trading modes for testing and analysis.
            </p>

            <p style="margin-top:10px;">
                <b>License Notice:</b><br>
                Sale or redistribution of this software is not permitted.
            </p>
        </div>
        """

        QMessageBox.about(self, "About Blue Whale Trading Terminal", about_text)

    def _show_settings(self):
        """
        Correctly instantiates the SettingsDialog with only the parent.
        """
        settings_dialog = SettingsDialog(self)
        settings_dialog.accepted.connect(self._on_settings_dialog_accepted)
        settings_dialog.exec()

    def _on_settings_dialog_accepted(self):
        """
        Handles applying and saving all settings after the dialog is accepted.
        This is now the single point of truth for applying settings.
        """
        self.settings = self.config_manager.load_settings()
        logger.info(f"Settings dialog accepted. Applying new settings from config: {self.settings}")

        default_symbol = self.settings.get('default_symbol', 'NIFTY')
        default_lots = self.settings.get('default_lots', 1)

        self._suppress_signals = True
        self.header.set_active_symbol(default_symbol)
        self.header.set_lot_size(default_lots)
        self._suppress_signals = False

        auto_refresh_enabled = self.settings.get('auto_refresh', True)
        if hasattr(self, 'update_timer'):
            if auto_refresh_enabled:
                self.update_timer.start()
            else:
                self.update_timer.stop()

        if hasattr(self, 'strike_ladder'):
            auto_adjust = self.settings.get('auto_adjust_ladder', True)
            self.strike_ladder.set_auto_adjust(auto_adjust)

        self._on_settings_changed(self.header.get_current_settings())

    def _on_settings_changed(self, settings: dict):
        """
        Updates the strike ladder and other components when header settings change.
        """
        if self._settings_changing or not self.instrument_data:
            return
        self._settings_changing = True
        try:
            symbol = settings.get('symbol')
            if not symbol or symbol not in self.instrument_data:
                self._settings_changing = False
                return

            symbol_has_changed = (symbol != self.current_symbol)
            self.current_symbol = symbol

            today = datetime.now().date()

            raw_expiries = self.instrument_data[symbol].get('expiries', [])

            # 🔑 FILTER EXPIRED OPTION EXPIRIES
            valid_expiries = [
                exp for exp in raw_expiries
                if isinstance(exp, date) and exp >= today
            ]

            if not valid_expiries:
                logger.warning(f"No valid option expiries found for {symbol}")
                self._settings_changing = False
                return

            self.header.update_expiries(
                symbol,
                valid_expiries,
                preserve_selection=not symbol_has_changed
            )

            expiry_str = self.header.expiry_combo.currentText()
            if not expiry_str:
                logger.warning(f"No expiry date selected for {symbol}. Aborting ladder update.")
                self._settings_changing = False
                return

            expiry_date = datetime.strptime(expiry_str, '%d%b%y').date()

            current_price = self._get_current_price(symbol)
            if current_price is None:
                logger.error(f"Could not get current price for {symbol}. Ladder update aborted.")
                self._settings_changing = False
                return

            calculated_interval = self.strike_ladder.calculate_strike_interval(symbol)

            self.strike_ladder.update_strikes(
                symbol=symbol,
                current_price=current_price,
                expiry=expiry_date,
                strike_interval=calculated_interval
            )
            self._update_market_subscriptions()

            lot_quantity = self.instrument_data[symbol].get('lot_size', 1)
            self.buy_exit_panel.update_parameters(symbol, settings['lot_size'], lot_quantity, expiry_str)

        finally:
            self._settings_changing = False

    def _apply_settings(self, new_settings: dict):
        self.settings.update(new_settings)
        logger.info(f"Applying new settings: {new_settings}")
        auto_refresh_enabled = self.settings.get('auto_refresh_ui', True)
        ui_refresh_interval_sec = self.settings.get('ui_refresh_interval_seconds', 1)
        if hasattr(self, 'update_timer'):
            if auto_refresh_enabled:
                self.update_timer.setInterval(ui_refresh_interval_sec * 1000)
                if not self.update_timer.isActive(): self.update_timer.start()
                logger.info(f"UI refresh timer interval set to {ui_refresh_interval_sec}s and started.")
            else:
                self.update_timer.stop()
                logger.info("UI refresh timer stopped by settings.")
        if hasattr(self, 'strike_ladder'):
            auto_adjust_ladder = self.settings.get('auto_adjust_ladder', True)
            if hasattr(self.strike_ladder, 'set_auto_adjust'):
                self.strike_ladder.set_auto_adjust(auto_adjust_ladder)
        if hasattr(self, 'header'):
            default_lots_setting = self.settings.get('default_lots', 1)
            self.header.lot_size_spin.setValue(default_lots_setting)
        self._on_settings_changed(self._get_current_settings())
        try:
            # from src.utils.config_manager import ConfigManager
            config_manager = ConfigManager()
            config_manager.save_settings(self.settings)
            logger.info("Settings saved to configuration file.")
        except ImportError:
            logger.warning("ConfigManager not found. Cannot save settings to file.")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")


    def closeEvent(self, event):
        logger.info("Close event triggered.")

        # Stop timers first
        if hasattr(self, 'api_health_check_timer'):
            self.api_health_check_timer.stop()
        if hasattr(self, 'update_timer'):
            self.update_timer.stop()
        if hasattr(self, 'pending_order_refresh_timer'):
            self.pending_order_refresh_timer.stop()

        # Background workers
        if hasattr(self, 'market_data_worker') and self.market_data_worker.is_running:
            logger.info("Stopping market data worker...")

        if hasattr(self, 'instrument_loader') and self.instrument_loader.isRunning():
            logger.info("Stopping instrument loader...")
            self.instrument_loader.requestInterruption()
            self.instrument_loader.quit()
            if not self.instrument_loader.wait(2000):
                logger.warning("Instrument loader did not stop gracefully.")
            else:
                logger.info("Instrument loader stopped.")

        # ---- CLEAR EXIT CONFIRMATION ----
        if self.position_manager.has_positions():
            reply = QMessageBox.warning(
                self,
                "Exit Application",
                (
                    "You have open positions.\n\n"
                    "Closing the application will NOT exit or square off your positions.\n"
                    "They will remain open in your trading account.\n\n"
                    "Do you still want to close the application?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.No:
                event.ignore()

                # Restart timers if exit cancelled
                if hasattr(self, 'api_health_check_timer'):
                    self.api_health_check_timer.start()
                if hasattr(self, 'update_timer'):
                    self.update_timer.start()

                return

        logger.info("Proceeding with application shutdown.")
        self.save_window_state()
        event.accept()

    def save_window_state(self):
        try:
            from utils.config_manager import ConfigManager
            config_manager = ConfigManager()
            state = {
                'geometry': self.saveGeometry().toBase64().data().decode('utf-8'),
                'state': self.saveState().toBase64().data().decode('utf-8'),
                'splitter': self.main_splitter.saveState().toBase64().data().decode('utf-8')
            }
            config_manager.save_window_state(state)
            logger.info("Window state saved.")
        except Exception as e:
            logger.error(f"Failed to save window state: {e}")

    def restore_window_state(self):
        try:
            from utils.config_manager import ConfigManager
            config_manager = ConfigManager()
            state = config_manager.load_window_state()
            if state:
                if state.get('geometry'):
                    self.restoreGeometry(QByteArray.fromBase64(state['geometry'].encode('utf-8')))
                if state.get('state'):
                    self.restoreState(QByteArray.fromBase64(state['state'].encode('utf-8')))
                if state.get('splitter'):
                    self.main_splitter.restoreState(QByteArray.fromBase64(state['splitter'].encode('utf-8')))
                logger.info("Window state restored.")
            else:
                self.setWindowState(Qt.WindowMaximized)
        except Exception as e:
            logger.error(f"Failed to restore window state: {e}")
            self.setWindowState(Qt.WindowMaximized)

    def _exit_all_positions(self):
        all_positions = self.position_manager.get_all_positions()
        positions_to_exit = [p for p in all_positions if p.quantity != 0]

        if not positions_to_exit:
            QMessageBox.information(self, "No Positions", "No open positions to exit.")
            return

        total_pnl_all = sum(p.pnl for p in positions_to_exit)
        reply = QMessageBox.question(
            self, "Confirm Exit All Positions",
            f"Are you sure you want to exit ALL {len(positions_to_exit)} open positions?\n\n"
            f"Total P&L for all positions: ₹{total_pnl_all:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._execute_bulk_exit(positions_to_exit)

    def _execute_bulk_exit(self, positions_list: List[Position]):
        """
        Executes bulk exit using PositionManager to ensure
        proper exit-state tracking and UI stability.
        """

        if not positions_list:
            return

        # Filter only valid positions
        positions_to_exit = [
            p for p in positions_list
            if p.quantity != 0 and not p.is_exiting
        ]

        if not positions_to_exit:
            self.statusBar().showMessage("No valid positions to exit.", 2000)
            return

        self.statusBar().showMessage(
            f"Exiting {len(positions_to_exit)} positions...", 2000
        )

        for pos in positions_to_exit:
            try:
                # 🔒 SINGLE SOURCE OF TRUTH
                self.position_manager.exit_position(pos)

                logger.info(
                    f"Bulk exit initiated for {pos.tradingsymbol} "
                    f"(Qty: {abs(pos.quantity)})"
                )

            except Exception as e:
                # Do NOT abort bulk exit because of one failure
                logger.error(
                    f"Bulk exit initiation failed for {pos.tradingsymbol}: {e}",
                    exc_info=True
                )

        # Final verification must be state-based
        QTimer.singleShot(1500, self._finalize_bulk_exit_result)

    def _finalize_bulk_exit_result(self):
        """
        Final verification of bulk exit.
        Uses position state (not API timing) to decide success or partial failure.
        """

        remaining_positions = [
            p for p in self.position_manager.get_all_positions()
            if p.quantity != 0 and not p.is_exiting
        ]

        if not remaining_positions:
            self.statusBar().showMessage(
                "All positions exited successfully.", 5000
            )
            self._play_sound(success=True)
            logger.info(
                "Bulk exit completed successfully — no open positions remaining."
            )
            return

        # Some positions are genuinely still open
        symbols = ", ".join(p.tradingsymbol for p in remaining_positions[:5])

        QMessageBox.warning(
            self,
            "Partial Exit",
            (
                "Some positions are still open:\n\n"
                f"{symbols}\n\n"
                "Please review them manually."
            )
        )

        self._play_sound(success=False)
        logger.warning(
            f"Bulk exit incomplete — remaining positions: {symbols}"
        )

    def _exit_position(self, position_data_to_exit: dict):
        tradingsymbol = position_data_to_exit.get('tradingsymbol')
        current_quantity = position_data_to_exit.get('quantity', 0)
        entry_price = position_data_to_exit.get('average_price', 0.0)
        pnl = position_data_to_exit.get('pnl', 0.0)
        exchange = position_data_to_exit.get('exchange', 'NFO')
        product = position_data_to_exit.get('product', 'MIS')

        if not tradingsymbol or current_quantity == 0:
            QMessageBox.warning(self, "Exit Failed",
                                "Invalid position data for exit (missing symbol or zero quantity).")
            logger.warning(f"Attempted to exit invalid position data: {position_data_to_exit}")
            return

        exit_quantity = abs(current_quantity)

        reply = QMessageBox.question(
            self,
            "Confirm Exit Position",
            f"Are you sure you want to exit the position for {tradingsymbol}?\n\n"
            f"Quantity: {exit_quantity}\n"
            f"Current P&L: ₹{pnl:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.statusBar().showMessage(f"Exiting position {tradingsymbol}...", 1000)
        try:
            transaction_type = self.trader.TRANSACTION_TYPE_SELL if current_quantity > 0 else self.trader.TRANSACTION_TYPE_BUY
            order_id = self.trader.place_order(
                variety=self.trader.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=exit_quantity,
                product=product,
                order_type=self.trader.ORDER_TYPE_MARKET,
            )
            logger.info(f"Exit order placed for {tradingsymbol} (Qty: {exit_quantity}) -> Order ID: {order_id}")

            if not isinstance(self.trader, PaperTradingManager):
                import time
                time.sleep(0.5)
                confirmed_order = self._confirm_order_success(order_id)
                if confirmed_order:
                    exit_price = confirmed_order.get('average_price', position_data_to_exit.get('last_price', 0.0))
                    realized_pnl = (exit_price - entry_price) * exit_quantity

                    confirmed_order['pnl'] = realized_pnl
                    self.trade_logger.log_trade(confirmed_order)
                    self.pnl_logger.log_pnl(datetime.now(), realized_pnl)

                    self.statusBar().showMessage(
                        f"Exit order {order_id} for {tradingsymbol} confirmed. P&L: ₹{realized_pnl:,.2f}", 5000)
                    self._play_sound(success=True)
                else:
                    self.statusBar().showMessage(
                        f"Exit order {order_id} for {tradingsymbol} placed, but confirmation pending or failed.", 5000)
                    logger.warning(f"Exit order {order_id} for {tradingsymbol} could not be confirmed immediately.")
                    self._play_sound(success=False)
            else:
                self._play_sound(success=True)

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to place exit order for {tradingsymbol}: {error_msg}", exc_info=True)
            QMessageBox.critical(self, "Exit Order Failed",
                                 f"Failed to place exit order for {tradingsymbol}:\n{error_msg}")
            self._play_sound(success=False)
        finally:
            self._refresh_positions()

    def _exit_position_from_dialog(self, symbol_or_pos_data):
        position_to_exit_data = None
        if isinstance(symbol_or_pos_data, str):
            position_obj = self.position_manager.get_position(symbol_or_pos_data)
            if position_obj:
                position_to_exit_data = self._position_to_dict(position_obj)
            else:
                logger.warning(f"Cannot exit: Position {symbol_or_pos_data} not found in PositionManager.")
                QMessageBox.warning(self, "Exit Error", f"Position {symbol_or_pos_data} not found.")
                return
        elif isinstance(symbol_or_pos_data, dict):
            position_to_exit_data = symbol_or_pos_data
        else:
            logger.error(f"Invalid data type for exiting position: {type(symbol_or_pos_data)}")
            return

        if position_to_exit_data:
            self._exit_position(position_to_exit_data)
        else:
            logger.warning("Could not prepare position data for exit from dialog signal.")

    def _exit_option_positions(self, option_type: OptionType):
        positions_to_exit = [pos for pos in self.position_manager.get_all_positions() if
                             hasattr(pos, 'contract') and pos.contract and hasattr(pos.contract,
                                                                                   'option_type') and pos.contract.option_type == option_type.value]
        if not positions_to_exit:
            QMessageBox.information(self, "No Positions", f"No open {option_type.name} positions to exit.")
            return

        total_pnl_of_selection = sum(p.pnl for p in positions_to_exit)
        reply = QMessageBox.question(
            self, f"Exit All {option_type.name} Positions",
            f"Are you sure you want to exit all {len(positions_to_exit)} {option_type.name} positions?\n\n"
            f"Approximate P&L for these positions: ₹{total_pnl_of_selection:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._execute_bulk_exit(positions_to_exit)

    def _build_strikes_list(self, option_type: OptionType, contracts_above: int, contracts_below: int,
                            atm_strike: Optional[float], strike_step: Optional[float]) -> List[Dict]:
        strikes_info_list = []
        if atm_strike is None or strike_step is None or strike_step == 0:
            logger.warning("ATM strike or strike step is invalid. Cannot build strikes list.")
            return strikes_info_list

        for i in range(contracts_below, 0, -1):
            strike_price = atm_strike - (i * strike_step)
            contract = self._get_contract_from_ladder(strike_price, option_type)
            if contract:
                strikes_info_list.append(
                    self._create_strike_info_for_order(strike_price, option_type, contract, is_atm=False))

        atm_contract = self._get_contract_from_ladder(atm_strike, option_type)
        if atm_contract:
            strikes_info_list.append(
                self._create_strike_info_for_order(atm_strike, option_type, atm_contract, is_atm=True))

        for i in range(1, contracts_above + 1):
            strike_price = atm_strike + (i * strike_step)
            contract = self._get_contract_from_ladder(strike_price, option_type)
            if contract:
                strikes_info_list.append(
                    self._create_strike_info_for_order(strike_price, option_type, contract, is_atm=False))
        return strikes_info_list

    def _get_contract_from_ladder(self, strike: float, option_type: OptionType) -> Optional[Contract]:
        if strike in self.strike_ladder.contracts:
            return self.strike_ladder.contracts[strike].get(option_type.value)
        return None

    @staticmethod
    def _create_strike_info_for_order(strike: float, option_type: OptionType, contract_obj: Contract,
                                      is_atm: bool) -> Dict:
        return {'strike': strike, 'type': option_type.value, 'ltp': contract_obj.ltp if contract_obj else 0.0,
                'contract': contract_obj, 'is_atm': is_atm,
                'tradingsymbol': contract_obj.tradingsymbol if contract_obj else None}

    def _execute_orders(self, confirmed_order_details: dict):
        successful_orders_info = []
        failed_orders_info = []
        order_product = confirmed_order_details.get('product', self.trader.PRODUCT_MIS)
        total_quantity_per_strike = confirmed_order_details.get('total_quantity_per_strike', 0)

        if total_quantity_per_strike == 0:
            logger.error("Total quantity per strike is zero in confirmed_order_details.")
            QMessageBox.critical(self, "Order Error", "Order quantity is zero. Cannot place order.")
            return

        self.statusBar().showMessage("Placing orders...", 1000)
        for strike_detail in confirmed_order_details.get('strikes', []):
            contract_to_trade: Optional[Contract] = strike_detail.get('contract')
            if not contract_to_trade or not contract_to_trade.tradingsymbol:
                logger.warning(f"Missing contract or tradingsymbol for strike {strike_detail.get('strike')}. Skipping.")
                failed_orders_info.append(
                    {'symbol': f"Strike {strike_detail.get('strike')}", 'error': "Missing contract data"})
                continue
            try:
                order_id = self.trader.place_order(
                    variety=self.trader.VARIETY_REGULAR,
                    exchange=self.trader.EXCHANGE_NFO,
                    tradingsymbol=contract_to_trade.tradingsymbol,
                    transaction_type=self.trader.TRANSACTION_TYPE_BUY,
                    quantity=total_quantity_per_strike,
                    product=order_product,
                    order_type=self.trader.ORDER_TYPE_MARKET,
                )
                logger.info(
                    f"Order placed attempt: {order_id} for {contract_to_trade.tradingsymbol}, Qty: {total_quantity_per_strike}")

                if not isinstance(self.trader, PaperTradingManager):
                    import time
                    time.sleep(0.5)
                    confirmed_order_api_data = self._confirm_order_success(order_id)
                    if confirmed_order_api_data:
                        order_status = confirmed_order_api_data.get('status')
                        if order_status in ['OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED']:
                            logger.info(f"Order {order_id} is pending with status: {order_status}. Triggering refresh.")
                            self._refresh_positions()
                            continue

                        if order_status == 'COMPLETE':
                            avg_price_from_order = confirmed_order_api_data.get('average_price', contract_to_trade.ltp)
                            new_position = Position(
                                symbol=f"{contract_to_trade.symbol}{contract_to_trade.strike}{contract_to_trade.option_type}",
                                tradingsymbol=contract_to_trade.tradingsymbol,
                                quantity=confirmed_order_api_data.get('filled_quantity', total_quantity_per_strike),
                                average_price=avg_price_from_order,
                                ltp=avg_price_from_order,
                                pnl=0,
                                contract=contract_to_trade,
                                order_id=order_id,
                                exchange=self.trader.EXCHANGE_NFO,
                                product=order_product
                            )
                            self.position_manager.add_position(new_position)
                            self.trade_logger.log_trade(confirmed_order_api_data)
                            successful_orders_info.append(
                                {'order_id': order_id, 'symbol': contract_to_trade.tradingsymbol,
                                 'quantity': total_quantity_per_strike,
                                 'price': avg_price_from_order})
                            logger.info(
                                f"Order {order_id} for {contract_to_trade.tradingsymbol} successful and position added.")
                    else:
                        logger.warning(
                            f"Order {order_id} for {contract_to_trade.tradingsymbol} failed or not confirmed.")
                        failed_orders_info.append(
                            {'symbol': contract_to_trade.tradingsymbol,
                             'error': "Order rejected or status not confirmed"})
            except Exception as e:
                logger.error(f"Order placement failed for {contract_to_trade.tradingsymbol}: {e}", exc_info=True)
                failed_orders_info.append({'symbol': contract_to_trade.tradingsymbol, 'error': str(e)})

        self._refresh_positions()
        self._play_sound(success=not failed_orders_info)
        self._show_order_results(successful_orders_info, failed_orders_info)
        self.statusBar().clearMessage()

    def _show_order_results(self, successful_list: List[Dict], failed_list: List[Dict]):
        if not failed_list:
            logger.info(f"Successfully placed {len(successful_list)} orders. No prompt shown.")
            return

        msg = f"Order Placement Summary:\n\n"
        msg += f"  - Successful: {len(successful_list)} orders\n"
        msg += f"  - Failed: {len(failed_list)} orders\n\n"
        msg += "Failure Details:\n"

        for f_info in failed_list[:5]:
            symbol = f_info.get('symbol', 'N/A')
            error = f_info.get('error', 'Unknown error')
            msg += f"  • {symbol}: {error}\n"

        if len(failed_list) > 5:
            msg += f"  ... and {len(failed_list) - 5} more failures.\n"

        QMessageBox.warning(self, "Order Placement Issue", msg)

    def _on_single_strike_selected(self, contract: Contract):
        if not contract:
            logger.warning("Single strike selected but contract data is missing.")
            return

        if self.active_quick_order_dialog:
            self.active_quick_order_dialog.reject()

        default_lots = self.header.lot_size_spin.value()

        dialog = QuickOrderDialog(parent=self, contract=contract, default_lots=default_lots)
        self.active_quick_order_dialog = dialog

        dialog.order_placed.connect(self._execute_single_strike_order)
        dialog.refresh_requested.connect(self._on_quick_order_refresh_request)
        dialog.finished.connect(lambda: setattr(self, 'active_quick_order_dialog', None))

    def _execute_single_strike_order(self, order_params: dict):
        contract_to_trade: Contract = order_params.get('contract')
        quantity = order_params.get('quantity')
        price = order_params.get('price')
        order_type = order_params.get('order_type', self.trader.ORDER_TYPE_MARKET)
        product = order_params.get('product', self.settings.get('default_product', self.trader.PRODUCT_MIS))
        transaction_type = order_params.get('transaction_type', self.trader.TRANSACTION_TYPE_BUY)
        stop_loss_price = order_params.get('stop_loss_price')
        target_price = order_params.get('target_price')
        trailing_stop_loss = order_params.get('trailing_stop_loss')

        if not contract_to_trade or not quantity:
            logger.error("Invalid parameters for single strike order.")
            QMessageBox.critical(self, "Order Error", "Missing contract or quantity for the order.")
            return

        try:
            order_args = {
                'variety': self.trader.VARIETY_REGULAR,
                'exchange': self.trader.EXCHANGE_NFO,
                'tradingsymbol': contract_to_trade.tradingsymbol,
                'transaction_type': transaction_type,
                'quantity': quantity,
                'product': product,
                'order_type': order_type,
            }
            if order_type == self.trader.ORDER_TYPE_LIMIT and price is not None:
                order_args['price'] = price
            order_id = self.trader.place_order(**order_args)
            logger.info(f"Single strike order placed attempt: {order_id} for {contract_to_trade.tradingsymbol}")

            QTimer.singleShot(500, self._refresh_positions)

            if not isinstance(self.trader, PaperTradingManager):
                import time
                time.sleep(0.5)
                confirmed_order_api_data = self._confirm_order_success(order_id)
                if confirmed_order_api_data:
                    order_status = confirmed_order_api_data.get('status')
                    if order_status in ['OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED']:
                        self._play_sound(success=True)
                        return

                    if order_status == 'COMPLETE':
                        avg_price_from_order = confirmed_order_api_data.get('average_price',
                                                                            price if price else contract_to_trade.ltp)
                        filled_quantity = confirmed_order_api_data.get('filled_quantity', quantity)

                        if transaction_type == self.trader.TRANSACTION_TYPE_BUY:
                            new_position = Position(
                                symbol=f"{contract_to_trade.symbol}{contract_to_trade.strike}{contract_to_trade.option_type}",
                                tradingsymbol=contract_to_trade.tradingsymbol,
                                quantity=filled_quantity,
                                average_price=avg_price_from_order,
                                ltp=avg_price_from_order,
                                pnl=0,
                                contract=contract_to_trade,
                                order_id=order_id,
                                exchange=self.trader.EXCHANGE_NFO,
                                product=product,
                                stop_loss_price=stop_loss_price,
                                target_price=target_price,
                                trailing_stop_loss=trailing_stop_loss if trailing_stop_loss > 0 else None
                            )
                            self.position_manager.add_position(new_position)
                            self.trade_logger.log_trade(confirmed_order_api_data)
                            action_msg = "bought"
                        else:
                            original_position = self.position_manager.get_position(contract_to_trade.tradingsymbol)
                            if original_position:
                                realized_pnl = (avg_price_from_order - original_position.average_price) * abs(
                                    original_position.quantity)
                                confirmed_order_api_data['pnl'] = realized_pnl
                                self.pnl_logger.log_pnl(datetime.now(), realized_pnl)
                            self.trade_logger.log_trade(confirmed_order_api_data)
                            action_msg = "sold"

                        self._play_sound(success=True)
                        self.statusBar().showMessage(
                            f"Order {order_id} ({action_msg} {filled_quantity} {contract_to_trade.tradingsymbol} @ {avg_price_from_order:.2f}) successful.",
                            5000)
                        self._show_order_results([{'order_id': order_id, 'symbol': contract_to_trade.tradingsymbol}],
                                                 [])
                else:
                    self._play_sound(success=False)
                    logger.warning(
                        f"Single strike order {order_id} for {contract_to_trade.tradingsymbol} failed or not confirmed.")
                    self._show_order_results([], [{'symbol': contract_to_trade.tradingsymbol,
                                                   'error': "Order rejected or status not confirmed"}])
            else:
                self._play_sound(success=True)

        except Exception as e:
            self._play_sound(success=False)
            logger.error(f"Single strike order execution failed for {contract_to_trade.tradingsymbol}: {e}",
                         exc_info=True)
            self._handle_order_error(e, order_params)
            self._show_order_results([], [{'symbol': contract_to_trade.tradingsymbol, 'error': str(e)}])
        finally:
            self._refresh_positions()

    def _handle_order_error(self, error: Exception, order_params: dict):
        error_msg_str = str(error).strip().lower()
        contract_obj: Contract = order_params.get('contract')
        user_display_error = f"Order failed for {contract_obj.tradingsymbol if contract_obj else 'Unknown'}:\n"
        if "networkexception" in error_msg_str or "connection" in error_msg_str:
            user_display_error += "A network error occurred. Please check your internet connection."
        elif "inputexception" in error_msg_str:
            user_display_error += f"There was an issue with the order parameters: {str(error)}"
            if "amo" in error_msg_str or "after market" in error_msg_str:
                user_display_error += "\nMarket might be closed or order type not supported (AMO)."
            elif "market order" in error_msg_str and contract_obj and contract_obj.symbol not in ['NIFTY', 'BANKNIFTY',
                                                                                                  'FINNIFTY',
                                                                                                  'MIDCPNIFTY']:
                user_display_error += "\nStock options typically require LIMIT orders. Try placing a LIMIT order."
        elif "authexception" in error_msg_str:
            user_display_error += "Authentication error. Your session might have expired. Please re-login."
        elif "generalexception" in error_msg_str or "apiexception" in error_msg_str:
            user_display_error += f"API Error: {str(error)}"
            if "insufficient funds" in error_msg_str or "margin" in error_msg_str:
                user_display_error += "\nPlease check your available funds and margins."
        else:
            user_display_error += f"An unexpected error occurred: {str(error)}"
        logger.error(f"Order error details: {error}, params: {order_params}")
        QMessageBox.critical(self, "Order Failed", user_display_error)

    ALLOWED_ORDER_STATUSES = {'OPEN', 'TRIGGER PENDING', 'COMPLETE', 'AMO REQ RECEIVED'}

    def _confirm_order_success(self, order_id: str, retries: int = 5, delay: float = 0.7) -> Optional[dict]:
        if not self.trader: return None
        for i in range(retries):
            try:
                all_orders = self.trader.orders()
                for order in all_orders:
                    if order.get('order_id') == order_id:
                        logger.debug(
                            f"Order ID {order_id} found. Status: {order.get('status')}, Tag: {order.get('tag')}")
                        if order.get('status') in self.ALLOWED_ORDER_STATUSES:
                            if order.get('status') == 'COMPLETE' and order.get('transaction_type') in [
                                self.trader.TRANSACTION_TYPE_BUY, self.trader.TRANSACTION_TYPE_SELL]:
                                if order.get('filled_quantity', 0) > 0:
                                    return order
                                else:
                                    logger.warning(
                                        f"Order {order_id} is COMPLETE but filled_quantity is 0. Considering it failed to fill as expected.")
                                    return order
                            return order
                        elif order.get('status') == 'REJECTED':
                            logger.warning(f"Order {order_id} was REJECTED. Reason: {order.get('status_message')}")
                            return None
                logger.debug(f"Order {order_id} not in allowed status or not found yet. Retry {i + 1}/{retries}")
            except Exception as e:
                logger.warning(f"Error fetching order status for {order_id} on retry {i + 1}: {e}")
            import time
            time.sleep(delay)
        logger.error(f"Order {order_id} confirmation failed after {retries} retries.")
        return None

    def _play_sound(self, success: bool = True):
        try:
            sound_effect = QSoundEffect(self)
            filename = "success.wav" if success else "fail.wav"
            base_path = os.path.dirname(os.path.abspath(__file__))
            assets_dir = os.path.join(base_path, "..", "assets")
            if not os.path.exists(assets_dir):
                assets_dir = os.path.join(base_path, "assets")
            sound_path = os.path.join(assets_dir, filename)
            if os.path.exists(sound_path):
                sound_effect.setSource(QUrl.fromLocalFile(sound_path))
                sound_effect.setVolume(0.8)
                sound_effect.play()
            else:
                logger.warning(f"Sound file not found: {sound_path}")
        except Exception as e:
            logger.error(f"Error playing sound: {e}")

    @staticmethod
    def _calculate_smart_limit_price(contract: Contract) -> float:
        base_price = contract.ltp
        bid_price = contract.bid if hasattr(contract, 'bid') else 0.0
        ask_price = contract.ask if hasattr(contract, 'ask') else 0.0
        tick_size = 0.05
        if base_price <= 0:
            if ask_price > 0: return round(ask_price / tick_size) * tick_size
            return tick_size
        if not (0 < bid_price < ask_price):
            return ScalperMainWindow._calculate_ltp_based_price(base_price, tick_size)
        spread_info = ScalperMainWindow._analyze_bid_ask_spread(bid_price, ask_price, base_price, tick_size)
        if spread_info['has_valid_spread']:
            return ScalperMainWindow._calculate_spread_based_price(base_price, bid_price, ask_price, spread_info)
        else:
            return ScalperMainWindow._calculate_ltp_based_price(base_price, tick_size)

    @staticmethod
    def _analyze_bid_ask_spread(bid_price: float, ask_price: float, ltp: float, tick_size: float) -> dict:
        has_valid_spread = 0 < bid_price < ask_price
        result = {'has_valid_spread': has_valid_spread, 'spread_points': 0, 'mid_price': ltp, 'tick_size': tick_size}
        if has_valid_spread:
            result['spread_points'] = ask_price - bid_price
            result['mid_price'] = (bid_price + ask_price) / 2
        return result

    @staticmethod
    def _calculate_spread_based_price(ltp: float, bid: float, ask: float, spread_info: dict) -> float:
        tick_size = spread_info.get('tick_size', 0.05)
        if spread_info['spread_points'] <= 2 * tick_size:
            target_price = ask
        else:
            if bid < ltp < ask:
                target_price = ltp + tick_size
            else:
                target_price = (spread_info['mid_price'] + ask) / 2
                if target_price <= bid:
                    target_price = bid + tick_size
        final_price = max(target_price, bid + tick_size)
        final_price = min(final_price, ask + 5 * tick_size)
        return round(final_price / tick_size) * tick_size

    @staticmethod
    def _calculate_ltp_based_price(base_price: float, tick_size: float) -> float:
        if base_price < 1:
            buffer = tick_size * 2
        elif base_price < 10:
            buffer = tick_size * 3
        elif base_price < 50:
            buffer = max(tick_size * 4, base_price * 0.01)
        else:
            buffer = max(tick_size * 5, base_price * 0.005)
        limit_price = base_price + buffer
        return round(limit_price / tick_size) * tick_size

    def _get_current_settings(self) -> dict:
        strike_step = 50.0
        if hasattr(self, 'strike_ladder') and hasattr(self.strike_ladder, 'user_strike_interval'):
            strike_step = self.strike_ladder.user_strike_interval
        return {'symbol': self.header.symbol_combo.currentText(), 'strike_step': strike_step,
                'expiry': self.header.expiry_combo.currentText(), 'lot_size': self.header.lot_size_spin.value()}

    def _on_lot_size_changed(self, num_lots: int):
        if self._settings_changing or not self.instrument_data:
            return

        symbol = self.header.symbol_combo.currentText()
        expiry_str = self.header.expiry_combo.currentText()

        if not symbol:
            return

        lot_quantity = self.instrument_data.get(symbol, {}).get('lot_size', 1)

        self.buy_exit_panel.update_parameters(symbol, num_lots, lot_quantity, expiry_str)
        logger.debug(f"Lot size updated to {num_lots} without refreshing ladder.")

    def _refresh_data(self):
        self.statusBar().showMessage("Refreshing data...", 0)
        self._refresh_positions()
        self._refresh_orders()
        self._update_account_info()
        self.statusBar().showMessage("Data refreshed", 3000)

    def _refresh_positions(self):
        if not self.trader:
            logger.warning("Kite client not available for position refresh.")
            self.statusBar().showMessage("API client not set. Cannot refresh positions.", 3000)
            return
        logger.debug("Attempting to refresh positions from API via PositionManager.")
        self.position_manager.refresh_from_api()

    @staticmethod
    def _position_to_dict(position: Position) -> dict:
        return {
            'tradingsymbol': position.tradingsymbol,
            'symbol': position.symbol,
            'quantity': position.quantity,
            'average_price': position.average_price,
            'last_price': position.ltp,
            'pnl': position.pnl,
            'exchange': position.exchange,
            'product': position.product,
            'strike': position.contract.strike,
            'option_type': position.contract.option_type,
            'stop_loss_price': position.stop_loss_price,
            'target_price': position.target_price,
            'trailing_stop_loss': position.trailing_stop_loss
        }

    def _refresh_orders(self):
        if not self.trader:
            logger.warning("Kite client not available for order refresh.")
            return
        try:
            orders = self.trader.orders()
            logger.info(f"Fetched {len(orders)} orders.")
        except Exception as e:
            logger.error(f"Failed to fetch orders: {e}")
            self.statusBar().showMessage(f"Failed to fetch orders: {e}", 3000)

    def _update_performance(self):
        all_trades = self.trade_logger.get_all_trades()
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
                winning_trades)) if winning_trades else 0,
            'avg_loss': abs(
                sum(t.get('pnl', 0.0) for t in losing_trades) / len(losing_trades)) if losing_trades else 0,
        }

        if self.performance_dialog and self.performance_dialog.isVisible() and hasattr(self.performance_dialog,
                                                                                       'update_metrics'):
            self.performance_dialog.update_metrics(metrics)

    def _update_account_summary_widget(self):
        all_trades = self.trade_logger.get_all_trades()
        winning_trades = [t for t in all_trades if t.get("pnl", 0.0) > 0]
        losing_trades = [t for t in all_trades if t.get("pnl", 0.0) < 0]
        total_trades = len(winning_trades) + len(losing_trades)

        win_rate = (len(winning_trades) / total_trades * 100) if total_trades else 0.0

        unrealized_pnl = self.position_manager.get_total_pnl()
        realized_pnl = self.position_manager.get_realized_day_pnl()

        try:
            margins = self.trader.margins().get("equity", {})
        except Exception:
            margins = {}

        used_margin = margins.get("utilised", {}).get("total", 0.0)
        available_margin = margins.get("available", {}).get("live_balance", 0.0)

        self.account_summary.update_summary(
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            used_margin=used_margin,
            available_margin=available_margin,
            win_rate=win_rate,
            trade_count=total_trades
        )

    def _update_ui(self):
        self._update_account_summary_widget()

        ladder_data = self.strike_ladder.get_ladder_data()
        if ladder_data:
            atm_strike = self.strike_ladder.atm_strike
            interval = self.strike_ladder.get_strike_interval()
            self.buy_exit_panel.update_strike_ladder(atm_strike, interval, ladder_data)

        if self.performance_dialog and self.performance_dialog.isVisible():
            self._update_performance()

        now = datetime.now()
        market_open_time = time(9, 15)
        market_close_time = time(15, 30)
        is_market_open = (market_open_time <= now.time() <= market_close_time) and (now.weekday() < 5)
        status = "Market Open" if is_market_open else "Market Closed"

        api_status = ""
        if self.margin_circuit_breaker.state == "OPEN" or self.profile_circuit_breaker.state == "OPEN":
            api_status = " | ⚠️ API Issues"
        elif self.margin_circuit_breaker.state == "HALF_OPEN" or self.profile_circuit_breaker.state == "HALF_OPEN":
            api_status = " | 🔄 API Recovering"

        network_display_status = ""
        if "Connected" in self.network_status:
            network_display_status = "  📡  Connected"
        elif "Disconnected" in self.network_status:
            network_display_status = " | ❌ Disconnected"
        elif "Connecting" in self.network_status or "Reconnecting" in self.network_status:
            network_display_status = f" | 🔄 {self.network_status}"
        else:
            network_display_status = f" | ⚠️ {self.network_status}"

        self.statusBar().showMessage(f"{network_display_status} | {status} | {now.strftime('%H:%M:%S')}{api_status}")

    def _get_cached_positions(self) -> List[Position]:
        return self.position_manager.get_all_positions()

    def _calculate_live_pnl_from_market_data(self, market_data: dict) -> float:
        total_pnl = 0.0
        current_positions = self.position_manager.get_all_positions()

        for position in current_positions:
            try:
                quote_key = f"{position.exchange}:{position.tradingsymbol}"
                if quote_key in market_data:
                    current_price = market_data[quote_key].get('last_price', position.ltp)
                    avg_price = position.average_price
                    quantity = position.quantity

                    if quantity > 0:
                        pnl = (current_price - avg_price) * quantity
                    else:
                        pnl = (avg_price - current_price) * abs(quantity)
                    total_pnl += pnl
                else:
                    total_pnl += position.pnl
            except Exception as e:
                logger.debug(f"Error calculating live P&L for position {position.tradingsymbol}: {e}")
                total_pnl += position.pnl
                continue
        return total_pnl

    def _show_modify_order_dialog(self, order_data: dict):
        order_id = order_data.get("order_id")
        tradingsymbol = order_data.get("tradingsymbol")
        logger.info(f"Modification requested for order ID: {order_id}")

        if not order_id or not tradingsymbol:
            logger.error("Modify request failed: No order_id or tradingsymbol in data.")
            QMessageBox.critical(self, "Error", "Cannot modify order: missing order details.")
            return

        contract = self._get_latest_contract_from_ladder(tradingsymbol)
        if not contract:
            logger.error(f"Could not find instrument details for {tradingsymbol} to modify order.")
            QMessageBox.critical(self, "Error", f"Could not find instrument details for {tradingsymbol}.")
            return

        try:
            self.trader.cancel_order(self.trader.VARIETY_REGULAR, order_id)
            logger.info(f"Order {order_id} cancelled for modification.")
            self.statusBar().showMessage(f"Order {order_id} cancelled. Please enter new order details.", 4000)
        except Exception as e:
            logger.warning(f"Failed to cancel order {order_id} for modification, it might have been executed: {e}")
            QMessageBox.information(self, "Order Not Found",
                                    "The order could not be modified as it may have been executed. Please refresh the positions table to confirm.")
            return

        QTimer.singleShot(100, lambda: self._open_prefilled_order_dialog(contract, order_data))

    def _open_prefilled_order_dialog(self, contract: Contract, order_data: dict):
        if self.active_quick_order_dialog:
            self.active_quick_order_dialog.reject()

        default_lots = int(order_data.get('quantity', 1) / contract.lot_size if contract.lot_size > 0 else 1)

        dialog = QuickOrderDialog(parent=self, contract=contract, default_lots=default_lots)
        self.active_quick_order_dialog = dialog

        dialog.populate_from_order(order_data)

        dialog.order_placed.connect(self._execute_single_strike_order)
        dialog.refresh_requested.connect(self._on_quick_order_refresh_request)
        dialog.finished.connect(lambda: setattr(self, 'active_quick_order_dialog', None))

    def _on_quick_order_refresh_request(self, tradingsymbol: str):
        if not self.active_quick_order_dialog:
            return

        logger.debug(f"Handling refresh request for {tradingsymbol}")

        latest_contract = self._get_latest_contract_from_ladder(tradingsymbol)
        if latest_contract:
            self.active_quick_order_dialog.update_contract_data(latest_contract)
        else:
            logger.warning(f"Could not find latest contract data for {tradingsymbol} to refresh dialog.")

    def _on_order_confirmation_refresh_request(self):
        if not self.active_order_confirmation_dialog:
            return

        logger.debug("Handling refresh request for order confirmation dialog.")

        current_details = self.active_order_confirmation_dialog.order_details
        new_strikes_list = []
        new_total_premium = 0.0

        total_quantity_per_strike = current_details.get('total_quantity_per_strike', 0)

        if total_quantity_per_strike == 0:
            logger.error("Cannot refresh order confirmation: total_quantity_per_strike is zero.")
            return

        for strike_info in current_details.get('strikes', []):
            contract = strike_info.get('contract')
            if not contract:
                continue

            latest_contract = self._get_latest_contract_from_ladder(contract.tradingsymbol)

            new_ltp = latest_contract.ltp if latest_contract else strike_info.get('ltp', 0.0)

            new_strikes_list.append({
                "strike": contract.strike,
                "ltp": new_ltp,
                "contract": latest_contract if latest_contract else contract
            })
            new_total_premium += new_ltp * total_quantity_per_strike

        new_details = current_details.copy()
        new_details['strikes'] = new_strikes_list
        new_details['total_premium_estimate'] = new_total_premium

        self.active_order_confirmation_dialog.update_order_details(new_details)

    def _get_latest_contract_from_ladder(self, tradingsymbol: str) -> Optional[Contract]:
        for strike_data in self.strike_ladder.contracts.values():
            for contract in strike_data.values():
                if contract.tradingsymbol == tradingsymbol:
                    return contract
        return None

    def _on_network_status_changed(self, status: str):
        self.network_status = status
        self._update_ui()

    def _get_nearest_future_token(self, symbol: str):
        symbol = symbol.upper()
        symbol_info = self.instrument_data.get(symbol)
        if not symbol_info:
            return None

        futures = symbol_info.get("futures", [])
        if not futures:
            return None

        today = datetime.now().date()

        # 🔑 FILTER OUT EXPIRED FUTURES
        valid_futures = [
            f for f in futures
            if f.get("expiry") and f["expiry"] >= today
        ]

        if not valid_futures:
            logger.warning(f"No valid (unexpired) FUT found for {symbol}")
            return None

        # Pick nearest unexpired FUT
        valid_futures.sort(key=lambda x: x["expiry"])
        fut = valid_futures[0]

        logger.info(
            f"Using FUT {fut.get('tradingsymbol')} "
            f"(expiry {fut.get('expiry')}) for {symbol}"
        )

        return fut.get("instrument_token")
