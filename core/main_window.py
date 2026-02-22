# core/main_window.py
import logging
import os
from enum import Enum
from collections import deque
import time
from pathlib import Path
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta, time, date

from core.cvd.cvd_mode import CVDMode
from utils.time_utils import TRADING_DAY_START
from uuid import uuid4
from PySide6.QtWidgets import (QMainWindow, QMessageBox, QDialog, QSplitter, QLabel, QFrame, QVBoxLayout)
from PySide6.QtCore import Qt, QTimer, QUrl, QByteArray
from PySide6.QtMultimedia import QSoundEffect
from kiteconnect import KiteConnect
from PySide6.QtGui import QShortcut, QKeySequence

# Internal imports
from utils.config_manager import ConfigManager
from core.market_data_worker import MarketDataWorker
from utils.data_models import OptionType, Position, Contract
from core.instrument_loader import InstrumentLoader
from dialogs.settings_dialog import SettingsDialog
from dialogs.open_positions_dialog import OpenPositionsDialog
from dialogs.quick_order_dialog import QuickOrderDialog, QuickOrderMode
from core.position_manager import PositionManager
from core.config import REFRESH_INTERVAL_MS
from utils.trade_logger import TradeLogger
from core.paper_trading_manager import PaperTradingManager
from dialogs.option_chain_dialog import OptionChainDialog
from dialogs.strategy_builder_dialog import StrategyBuilderDialog
from dialogs.order_confirmation_dialog import OrderConfirmationDialog
from core.cvd.cvd_engine import CVDEngine
from core.auto_trader import AutoTraderDialog
from core.auto_trader.cvd_automation_coordinator import CvdAutomationCoordinator
from core.cvd.cvd_symbol_sets import CVDSymbolSetManager
from dialogs.cvd_symbol_set_multi_chart_dialog import CVDSetMultiChartDialog
from core.trade_ledger import TradeLedger
from core.execution_stack import ExecutionRequest, ExecutionStack
from utils.title_bar import TitleBar
from core.ui.main_window_shell import MainWindowShell
from utils.api_circuit_breaker import APICircuitBreaker
from utils.about import show_about
from utils.expiry_days import show_expiry_days
from utils.shortcuts import show_shortcuts
from dialogs.fii_dii_dialog import FIIDIIDialog
from utils.network_utils import with_timeout, NetworkError, NetworkMonitor
from core.main_window_coordinators import RiskController, DialogCoordinator, MarketDataOrchestrator
from core.market_data import MarketSubscriptionPolicy
from core.account import AccountHealthService
from core.presentation import OrderDialogService, AnalyticsDialogService, MonitorDialogService
from core.positions import PositionSyncAdapter

logger = logging.getLogger(__name__)

api_logger = logging.getLogger("api_health")
api_handler = logging.FileHandler("logs/api_health.log")
api_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
api_handler.setFormatter(api_formatter)
api_logger.setLevel(logging.INFO)


class WebSocketState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class ImperiumMainWindow(QMainWindow):
    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], real_kite_client: KiteConnect, api_key: str,
                 access_token: str):
        super().__init__()

        self.api_key = api_key
        self.access_token = access_token
        self.trader = trader
        self.real_kite_client = real_kite_client
        self.trading_mode = 'paper' if isinstance(trader, PaperTradingManager) else 'live'
        self.trade_logger = TradeLogger(mode=self.trading_mode)

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

        # WebSocket state management
        self.ws_state = WebSocketState.DISCONNECTED
        self.ws_connection_time: Optional[datetime] = None
        self.subscription_queue = deque()  # Queue for subscriptions before WS ready
        self.pending_subscriptions = set()  # Track what's queued
        self.ws_ready_event_fired = False
        self._cached_index_prices = {}  # Price cache for fallback
        self._network_error_shown = False  # Track if error notification shown

        # Network monitoring
        self.network_monitor = NetworkMonitor(self)
        self.network_monitor.connection_lost.connect(self._on_connection_lost)
        self.network_monitor.connection_restored.connect(self._on_connection_restored)
        self._network_error_notification = None  # Track active notification

        self.account_health_service = AccountHealthService(
            trader=self.trader,
            real_kite_client=self.real_kite_client,
            profile_circuit_breaker=self.profile_circuit_breaker,
            margin_circuit_breaker=self.margin_circuit_breaker,
            network_monitor=self.network_monitor,
            publish_status=lambda message, timeout_ms, level: self._publish_status(message, timeout_ms, level=level),
            update_header_account_info=lambda user_id, balance: self.header.update_account_info(user_id, balance)
            if hasattr(self, "header") else None,
        )
        self.position_sync_adapter = PositionSyncAdapter(
            get_positions_dialog=lambda: self.positions_dialog,
            get_inline_positions_table=lambda: self.inline_positions_table,
            get_all_positions=self.position_manager.get_all_positions,
            position_to_dict=self._position_to_dict,
            update_performance=self._update_performance,
            update_market_subscriptions=self._update_market_subscriptions,
            reconcile_cvd_automation_positions=self._reconcile_cvd_automation_positions,
            publish_status=lambda message, timeout_ms, level: self._publish_status(message, timeout_ms, level=level),
        )

        self.active_quick_order_dialog: Optional[QuickOrderDialog] = None
        self.active_order_confirmation_dialog: Optional[OrderConfirmationDialog] = None
        self._auto_confirm_next_panel_order = False
        self._auto_confirm_next_quick_order = False  # Flag for quick order auto-confirm in automation
        self.positions_dialog = None
        self.performance_dialog = None
        self.order_history_dialog = None
        self.pnl_history_dialog = None
        self.pending_orders_dialog = None
        self.option_chain_dialog = None
        self.strategy_builder_dialog = None
        self.fii_dii_dialog = None
        self.watchlist_dialog = None
        self.journal_dialog = None

        self.pending_order_widgets = {}
        self.market_monitor_dialogs = []
        self.current_symbol = ""
        self.network_status = "Initializing..."
        self.cvd_engine = CVDEngine()
        self.cvd_monitor_dialog = None
        self.cvd_single_chart_dialogs = {}  # Dict[int, AutoTraderDialog] - token -> dialog
        self.header_linked_cvd_token: Optional[int] = None
        self.trade_ledger = TradeLedger(mode=self.trading_mode)
        self.execution_stack = ExecutionStack(trading_mode=self.trading_mode, base_dir=Path.home() / ".imperium_desk")
        self.cvd_automation_coordinator = CvdAutomationCoordinator(
            main_window=self,
            trading_mode=self.trading_mode,
            base_dir=Path.home() / ".imperium_desk",
        )
        self._cvd_automation_positions: Dict[int, dict] = self.cvd_automation_coordinator.positions
        self._cvd_automation_market_state: Dict[int, dict] = self.cvd_automation_coordinator.market_state
        self._cvd_pending_retry_timers: Dict[int, QTimer] = {}
        self._cvd_automation_state_file = self.cvd_automation_coordinator.state_file
        self._load_cvd_automation_state()

        # -----------------------------
        # Risk-first hardening controls
        # -----------------------------
        self.global_kill_switch_active = False
        self.global_kill_switch_reason = ""
        self.intraday_drawdown_lock_active = False
        self._intraday_peak_pnl = 0.0
        self._intraday_drawdown_limit = float(max(0.0, self.settings.get("risk_intraday_drawdown_limit", 0.0)))
        self._max_portfolio_loss = float(max(0.0, self.settings.get("risk_max_portfolio_loss", 0.0)))
        self._max_open_positions = int(max(0, self.settings.get("risk_max_open_positions", 0)))
        self._max_gross_open_quantity = int(max(0, self.settings.get("risk_max_gross_open_quantity", 0)))

        # CVD monitor symbols (v1 – fixed indices)
        self.cvd_symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
        self.active_cvd_tokens: set[int] = set()
        self.cvd_symbol_set_manager = CVDSymbolSetManager(base_dir=Path.home() / ".imperium_desk")

        self._last_subscription_set: set[int] = set()

        # Coordinators extracted from main window for lower complexity
        self.risk_controller = RiskController(self)
        self.dialog_coordinator = DialogCoordinator(self)
        self.market_data_orchestrator = MarketDataOrchestrator(self)
        self.order_dialog_service = OrderDialogService(self)
        self.analytics_dialog_service = AnalyticsDialogService(self)
        self.monitor_dialog_service = MonitorDialogService(self)
        self.subscription_policy = MarketSubscriptionPolicy(self)

        # --- FIX: UI Throttling Implementation ---
        self._latest_market_data = {}
        self._ui_update_needed = False
        self.ui_update_timer = QTimer(self)
        self.ui_update_timer.timeout.connect(self._update_throttled_ui)
        self.ui_update_timer.start(100)  # Update UI at most every 100ms

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.title_bar = TitleBar(self)
        self.title_bar.set_title(self.trading_mode.upper())

        self.setMinimumSize(1200, 700)
        self.setWindowState(Qt.WindowState.WindowMaximized)

        self._apply_dark_theme()
        self._setup_ui()
        self._setup_position_manager()
        self._connect_signals()
        self._setup_keyboard_shortcuts()
        self._init_background_workers()
        self._schedule_trading_day_reset()

        self._publish_status("App startup successful. Initializing live data flows...", 6000, level="success")

        if isinstance(self.trader, PaperTradingManager):
            self.trader.order_update.connect(self._on_paper_trade_update)
            self.trader.order_rejected.connect(self._on_paper_order_rejected)
            self.market_data_worker.data_received.connect(self.trader.update_market_data, Qt.QueuedConnection)

        self.pending_order_refresh_timer = QTimer(self)
        self.pending_order_refresh_timer.setInterval(1000)
        self.pending_order_refresh_timer.timeout.connect(self._refresh_positions)

        self._processed_live_exit_orders: set[str] = set()

        # 🔥 FIX: Cache position snapshots before exit to preserve entry data
        # When a SELL order completes, the position may already be gone from API
        # This cache maps tradingsymbol → Position snapshot at exit time
        self._position_snapshots_for_exit: Dict[str, object] = {}

        self.live_order_monitor_timer = QTimer(self)
        self.live_order_monitor_timer.timeout.connect(self._check_live_completed_orders)
        self.live_order_monitor_timer.start(1000)  # 1s polling (safe)

        self.restore_window_state()
        self._publish_status("Loading instruments...", 5000, level="action")

    def _on_market_data(self, data: list):
        self.market_data_orchestrator.on_market_data(data)

    def _update_throttled_ui(self):
        self.market_data_orchestrator.update_throttled_ui()

    def _apply_dark_theme(self):
        MainWindowShell.apply_dark_theme(self)

    def _init_background_workers(self):

        self.instrument_loader = InstrumentLoader(self.real_kite_client)
        self.instrument_loader.instruments_loaded.connect(self._on_instruments_loaded)
        self.instrument_loader.error_occurred.connect(self._on_api_error)
        self.instrument_loader.start()

        self.market_data_worker = MarketDataWorker(self.api_key, self.access_token)
        self.market_data_worker.data_received.connect(self._on_market_data, Qt.QueuedConnection)
        self.market_data_worker.connection_status_changed.connect(self._on_network_status_changed)
        # self.market_data_worker.state_changed.connect(self._on_websocket_state_changed)

        self.market_data_worker.start()

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update_ui)
        self.update_timer.start(REFRESH_INTERVAL_MS)

    def _place_order(self, order_details_from_panel: dict):
        """Handles the buy signal from the panel by showing a confirmation dialog."""
        auto_confirm = bool(self._auto_confirm_next_panel_order)
        # Consume one-shot auto-confirm intent so manual clicks are never affected.
        self._auto_confirm_next_panel_order = False

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
        # 🔑 PASS RISK PARAMS FOR POSITION CREATION
        order_details_for_dialog["stop_loss_price"] = order_details_from_panel.get("stop_loss_price")
        order_details_for_dialog["target_price"] = order_details_from_panel.get("target_price")
        order_details_for_dialog["trailing_stop_loss"] = order_details_from_panel.get("trailing_stop_loss")

        dialog = OrderConfirmationDialog(self, order_details_for_dialog)

        self.active_order_confirmation_dialog = dialog

        dialog.refresh_requested.connect(self._on_order_confirmation_refresh_request)
        dialog.finished.connect(lambda: setattr(self, 'active_order_confirmation_dialog', None))

        if auto_confirm:
            logger.info("[AUTO] Auto-confirming Buy/Exit panel order for %s", symbol)
            self._execute_orders(order_details_for_dialog)
            return

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._execute_orders(order_details_for_dialog)

    def _on_paper_trade_update(self, order_data: dict):
        """Logs completed paper trades and triggers an immediate UI refresh."""
        self._processed_paper_exit_orders = getattr(self, "_processed_paper_exit_orders", set())

        order_id = order_data.get("order_id")
        if order_id in self._processed_paper_exit_orders:
            return

        self._processed_paper_exit_orders.add(order_id)

        if order_data and order_data.get('status') == 'COMPLETE':
            tradingsymbol = order_data.get('tradingsymbol')
            exit_qty = order_data.get("exit_qty", 0)

            if exit_qty > 0:
                if order_data.get("_ledger_recorded"):
                    return  # 🔒 already processed

                original_position = self.position_manager.get_position(tradingsymbol)
                if original_position:
                    confirmed_order = {
                        **order_data,
                        "filled_quantity": exit_qty
                    }
                    self._record_completed_exit_trade(
                        confirmed_order=confirmed_order,
                        original_position=original_position,
                        trading_mode="PAPER"
                    )
                    # 🔒 Mark as recorded AFTER successful write
                    order_data["_ledger_recorded"] = True
                return

            logger.debug("Paper trade complete, triggering immediate account info refresh.")
            self._update_account_info()
            self._update_account_summary_widget()
            self._refresh_positions()

    def _setup_ui(self):
        MainWindowShell.setup_ui(self)

    def _setup_status_footer(self):
        """Initialize status bar widget"""
        MainWindowShell.setup_status_footer(self)

    def _publish_status(self, message: str, timeout_ms: int = 4000, level: str = "info"):
        """Publish status message through StatusBarWidget"""
        MainWindowShell.publish_status(self, message, timeout_ms, level)

    def _create_main_widgets(self):
        MainWindowShell.create_main_widgets(self)

    def _create_left_column(self) -> QSplitter:
        return MainWindowShell.create_left_column(self)

    def _create_center_column(self):
        return MainWindowShell.create_center_column(self)

    def _create_fourth_column(self):
        return MainWindowShell.create_fourth_column(self)

    def _setup_menu_bar(self):
        MainWindowShell.setup_menu_bar(self)

    def _show_order_history_dialog(self):
        self.order_dialog_service.show_order_history_dialog()

    def _show_journal_dialog(self, enforce_read_time: bool = False):
        self.dialog_coordinator.show_journal_dialog(enforce_read_time=enforce_read_time)

    def _show_startup_journal(self):
        self._show_journal_dialog(enforce_read_time=True)

    def _refresh_order_history_from_ledger(self):
        self.order_dialog_service.refresh_order_history_from_ledger()

    def _show_market_monitor_dialog(self):
        self.monitor_dialog_service.show_market_monitor_dialog()

    def _show_watchlist_dialog(self):
        self.monitor_dialog_service.show_watchlist_dialog()

    def _on_watchlist_symbol_selected(self, symbol: str):
        self.dialog_coordinator.on_watchlist_symbol_selected(symbol)

    def _show_cvd_chart_dialog(self):
        self.monitor_dialog_service.show_cvd_chart_dialog()

    def _open_cvd_chart_after_subscription(
            self,
            cvd_token: int,
            symbol: str,
            suffix: str = "",
            link_to_header: bool = False
    ):
        self.monitor_dialog_service.open_cvd_chart_after_subscription(cvd_token, symbol, suffix, link_to_header)

    def _log_active_subscriptions(self):
        self.subscription_policy.log_active_subscriptions()

    def _on_cvd_dialog_closed(self, token):
        QTimer.singleShot(0, self._update_market_subscriptions)

    def _on_cvd_single_chart_closed(self, token):
        """Handle CVD single chart dialog close."""
        if token in self.cvd_single_chart_dialogs:
            del self.cvd_single_chart_dialogs[token]
        self._stop_cvd_pending_retry(token)
        if self._cvd_automation_positions.pop(token, None) is not None:
            self._persist_cvd_automation_state()
        self._cvd_automation_market_state.pop(token, None)
        if self.header_linked_cvd_token == token:
            self.header_linked_cvd_token = None
        QTimer.singleShot(0, self._update_market_subscriptions)

    def _on_cvd_automation_signal(self, payload: dict):
        self.cvd_automation_coordinator.handle_signal(payload)

    def _on_cvd_automation_market_state(self, payload: dict):
        self.cvd_automation_coordinator.handle_market_state(payload)

    def _is_cvd_auto_cutoff_reached(self) -> bool:
        return self.cvd_automation_coordinator.is_cutoff_reached()

    def _enforce_cvd_auto_cutoff_exit(self, reason: str = "AUTO_3PM_CUTOFF"):
        self.cvd_automation_coordinator.enforce_cutoff_exit(reason=reason)

    def _persist_cvd_automation_state(self):
        self.cvd_automation_coordinator.persist_state()

    def _load_cvd_automation_state(self):
        self.cvd_automation_coordinator.load_state()

    def _reconcile_failed_auto_entry(self, token: int, tradingsymbol: str, signal_timestamp: str | None):
        self.cvd_automation_coordinator.reconcile_failed_entry(token, tradingsymbol, signal_timestamp)

    def _reconcile_cvd_automation_positions(self):
        self.cvd_automation_coordinator.reconcile_positions()

    def _get_atm_contract_for_signal(self, signal_side: str) -> Optional[Contract]:
        return self.cvd_automation_coordinator.get_atm_contract_for_signal(signal_side)

    def _exit_position_automated(self, position: Position, reason: str = "AUTO"):
        self.cvd_automation_coordinator.exit_position_automated(position, reason=reason)

    def _update_cvd_chart_symbol(self, symbol: str, cvd_token: int, suffix: str = ""):
        self.subscription_policy.update_cvd_chart_symbol(symbol, cvd_token, suffix)

    def _retarget_cvd_dialog(
            self,
            dialog: AutoTraderDialog,
            old_token: int,
            new_token: int,
            symbol: str,
            suffix: str = ""
    ):
        self.monitor_dialog_service.retarget_cvd_dialog(dialog, old_token, new_token, symbol, suffix)

    def _show_cvd_market_monitor_dialog(self):
        self.monitor_dialog_service.show_cvd_market_monitor_dialog()

    def _show_cvd_symbol_set_dialog(self):
        def resolve_cvd_token_for_sets(symbol: str):
            """Wrapper for CVD symbol sets - returns just the token."""
            cvd_token, is_equity, suffix = self._get_cvd_token(symbol)
            return cvd_token

        dlg = CVDSetMultiChartDialog(
            kite=self.real_kite_client,
            symbol_set_manager=self.cvd_symbol_set_manager,
            resolve_fut_token_fn=resolve_cvd_token_for_sets,
            register_token_fn=lambda t: (
                self.cvd_engine.register_token(t),
                self.active_cvd_tokens.add(t),
                self._update_market_subscriptions()
            ),
            unregister_tokens_fn=lambda tokens: (
                self.active_cvd_tokens.difference_update(tokens),
                self._update_market_subscriptions()
            ),
            parent=self
        )
        dlg.show()

    def _on_cvd_market_monitor_closed(self):
        self.market_data_orchestrator.on_cvd_market_monitor_closed()

    def _on_market_monitor_closed(self, dialog: QDialog):
        self.dialog_coordinator.on_market_monitor_closed(dialog)

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

    def _show_strategy_builder_dialog(self):
        if not self.instrument_data:
            QMessageBox.warning(self, "Data Not Ready",
                                "Instrument data is still loading. Please try again in a moment.")
            return

        current_settings = self.header.get_current_settings()
        symbol = current_settings.get("symbol")
        expiry = current_settings.get("expiry")
        default_lots = current_settings.get("lot_size", 1)

        if not symbol:
            QMessageBox.warning(self, "Symbol Missing", "Select a symbol before opening the strategy builder.")
            return

        if self.strategy_builder_dialog is not None:
            self.strategy_builder_dialog.close()

        self.strategy_builder_dialog = StrategyBuilderDialog(
            instrument_data=self.instrument_data,
            strike_ladder=self.strike_ladder,
            symbol=symbol,
            expiry=expiry,
            default_lots=default_lots,
            product=self.settings.get("default_product", self.trader.PRODUCT_MIS),
            on_execute=self._execute_strategy_orders,
            parent=None,
        )
        self.strategy_builder_dialog.finished.connect(
            lambda: setattr(self, 'strategy_builder_dialog', None)
        )

        self.strategy_builder_dialog.show()
        self.strategy_builder_dialog.activateWindow()
        self.strategy_builder_dialog.raise_()

    def _connect_signals(self):
        self.header.settings_changed.connect(self._on_settings_changed)
        self.header.lot_size_changed.connect(self._on_lot_size_changed)
        self.header.exit_all_clicked.connect(self._exit_all_positions)
        self.header.settings_button.clicked.connect(self._show_settings)
        self.header.journal_clicked.connect(self._show_journal_dialog)
        self.buy_exit_panel.buy_clicked.connect(self._place_order)
        self.buy_exit_panel.exit_clicked.connect(self._exit_option_positions)
        self.strike_ladder.strike_selected.connect(self._on_single_strike_selected)
        self.inline_positions_table.exit_requested.connect(self._exit_position)
        self.inline_positions_table.modify_sl_tp_requested.connect(self._show_modify_sl_tp_dialog)
        self.account_summary.pnl_history_requested.connect(self._show_pnl_history_dialog)
        self.position_manager.pending_orders_updated.connect(self._update_pending_order_widgets)
        self.inline_positions_table.refresh_requested.connect(self._refresh_positions)
        self.inline_positions_table.portfolio_sl_tp_requested.connect(self.position_manager.set_portfolio_sl_tp)
        self.inline_positions_table.portfolio_sl_tp_cleared.connect(self.position_manager.clear_portfolio_sl_tp)
        self.strike_ladder.chart_requested.connect(self._on_strike_chart_requested)
        self.strike_ladder.visible_tokens_changed.connect(self._update_market_subscriptions)

    def _setup_keyboard_shortcuts(self):
        """
        Global keyboard shortcuts for ultra-fast trading.
        These are safe because they reuse existing methods.
        """

        self._shortcuts = []

        def bind(key, callback):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ApplicationShortcut)  # Works even if focus is elsewhere
            sc.activated.connect(callback)
            self._shortcuts.append(sc)

        # -------------------------
        # BUY / SELL (ATM)
        # -------------------------
        # BUY (current option type)
        bind("B", lambda: self.buy_exit_panel._on_buy_clicked())

        # Toggle CALL / PUT
        bind("T", self.buy_exit_panel.toggle_option_type)

        # -------------------------
        # EXIT CONTROLS
        # -------------------------
        bind("X", self._exit_all_positions)  # Exit ALL
        bind("Alt+C", lambda: self._exit_option_positions(OptionType.CALL))
        bind("Alt+P", lambda: self._exit_option_positions(OptionType.PUT))

        # -------------------------
        # LOT SIZE CONTROL (SAFE)
        # -------------------------

        # Fine-tuning
        bind("+", lambda: self._change_lot_size(1))
        bind("-", lambda: self._change_lot_size(-1))

        # Direct lot jumps (INTENT REQUIRED)
        bind("Alt+1", lambda: self._set_lot_size(1))
        bind("Alt+2", lambda: self._set_lot_size(2))
        bind("Alt+3", lambda: self._set_lot_size(3))
        bind("Alt+4", lambda: self._set_lot_size(4))
        bind("Alt+5", lambda: self._set_lot_size(5))
        bind("Alt+6", lambda: self._set_lot_size(6))
        bind("Alt+7", lambda: self._set_lot_size(7))
        bind("Alt+8", lambda: self._set_lot_size(8))
        bind("Alt+9", lambda: self._set_lot_size(9))
        bind("Alt+0", lambda: self._set_lot_size(10))

        # -------------------------
        # EXACT SINGLE STRIKE BUY
        # -------------------------

        # ATM + exact strike
        bind("Shift+1", lambda: self._buy_exact_relative_strike(+1))
        bind("Shift+2", lambda: self._buy_exact_relative_strike(+2))
        bind("Shift+3", lambda: self._buy_exact_relative_strike(+3))
        bind("Shift+4", lambda: self._buy_exact_relative_strike(+4))
        bind("Shift+5", lambda: self._buy_exact_relative_strike(+5))
        bind("Shift+6", lambda: self._buy_exact_relative_strike(+6))
        bind("Shift+7", lambda: self._buy_exact_relative_strike(+7))
        bind("Shift+8", lambda: self._buy_exact_relative_strike(+8))
        bind("Shift+9", lambda: self._buy_exact_relative_strike(+9))
        bind("Shift+0", lambda: self._buy_exact_relative_strike(+10))

        # ATM - exact strike
        bind("Ctrl+1", lambda: self._buy_exact_relative_strike(-1))
        bind("Ctrl+2", lambda: self._buy_exact_relative_strike(-2))
        bind("Ctrl+3", lambda: self._buy_exact_relative_strike(-3))
        bind("Ctrl+4", lambda: self._buy_exact_relative_strike(-4))
        bind("Ctrl+5", lambda: self._buy_exact_relative_strike(-5))
        bind("Ctrl+6", lambda: self._buy_exact_relative_strike(-6))
        bind("Ctrl+7", lambda: self._buy_exact_relative_strike(-7))
        bind("Ctrl+8", lambda: self._buy_exact_relative_strike(-8))
        bind("Ctrl+9", lambda: self._buy_exact_relative_strike(-9))
        bind("Ctrl+0", lambda: self._buy_exact_relative_strike(-10))

        # -------------------------
        # ATM RELATIVE STRIKE BUY (RANGE / ALL)
        # -------------------------

        # ATM → +N (ALL strikes in between)
        bind("Alt+Shift+1", lambda: self._buy_relative_to_atm(above=1))
        bind("Alt+Shift+2", lambda: self._buy_relative_to_atm(above=2))
        bind("Alt+Shift+3", lambda: self._buy_relative_to_atm(above=3))
        bind("Alt+Shift+4", lambda: self._buy_relative_to_atm(above=4))
        bind("Alt+Shift+5", lambda: self._buy_relative_to_atm(above=5))
        bind("Alt+Shift+6", lambda: self._buy_relative_to_atm(above=6))
        bind("Alt+Shift+7", lambda: self._buy_relative_to_atm(above=7))
        bind("Alt+Shift+8", lambda: self._buy_relative_to_atm(above=8))
        bind("Alt+Shift+9", lambda: self._buy_relative_to_atm(above=9))
        bind("Alt+Shift+0", lambda: self._buy_relative_to_atm(above=10))

        # ATM → −N (ALL strikes in between)
        bind("Alt+Ctrl+1", lambda: self._buy_relative_to_atm(below=1))
        bind("Alt+Ctrl+2", lambda: self._buy_relative_to_atm(below=2))
        bind("Alt+Ctrl+3", lambda: self._buy_relative_to_atm(below=3))
        bind("Alt+Ctrl+4", lambda: self._buy_relative_to_atm(below=4))
        bind("Alt+Ctrl+5", lambda: self._buy_relative_to_atm(below=5))
        bind("Alt+Ctrl+6", lambda: self._buy_relative_to_atm(below=6))
        bind("Alt+Ctrl+7", lambda: self._buy_relative_to_atm(below=7))
        bind("Alt+Ctrl+8", lambda: self._buy_relative_to_atm(below=8))
        bind("Alt+Ctrl+9", lambda: self._buy_relative_to_atm(below=9))
        bind("Alt+Ctrl+0", lambda: self._buy_relative_to_atm(below=10))

    def _setup_position_manager(self):
        self.position_manager.positions_updated.connect(self._on_positions_updated)
        self.position_manager.position_added.connect(self._on_position_added)
        self.position_manager.position_removed.connect(self._on_position_removed)
        self.position_manager.refresh_completed.connect(self._on_refresh_completed)
        self.position_manager.api_error_occurred.connect(self._on_api_error)
        self.position_manager.portfolio_exit_triggered.connect(self._on_portfolio_exit_triggered)

    def _on_instruments_loaded(self, data: dict):
        self.instrument_data = data
        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_instrument_data(data)

        self.position_manager.set_instrument_data(data)
        self.strike_ladder.set_instrument_data(data)

        symbols = sorted(data.keys())
        self.header.set_symbols(symbols)
        if self.watchlist_dialog:
            self.watchlist_dialog.set_symbols(symbols)

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
        self._publish_status("Instruments loaded successfully.", 4000, level="success")

    def _on_instrument_error(self, error: str):
        logger.error(f"Instrument loading failed: {error}")
        QMessageBox.critical(self, "Error", f"Failed to load instruments:\n{error}")

    @with_timeout(timeout_seconds=5)
    @with_timeout(timeout_seconds=5)
    def _fetch_ltp_safe(self, instrument: str):
        """Helper method to fetch LTP with timeout"""
        return self.real_kite_client.ltp(instrument)

    def _get_current_price(self, symbol: str, max_retries: int = 3) -> Optional[float]:
        """
        Get current price with circuit breaker protection and retry logic

        Args:
            symbol: Index symbol (NIFTY, BANKNIFTY, etc.)
            max_retries: Maximum retry attempts with exponential backoff

        Returns:
            Current price or None if all attempts fail
        """
        if not symbol:
            return None

        # Check circuit breaker first
        if not self.profile_circuit_breaker.can_execute():
            logger.warning(
                f"Circuit breaker {self.profile_circuit_breaker.get_state()} - "
                f"using cached price for {symbol}"
            )
            # Try to use cached price from market data worker
            cached_price = self._get_cached_index_price(symbol)
            if cached_price:
                return cached_price
            # If no cache, we have to wait for circuit to recover
            return None

        # Index mapping
        index_map = {
            "NIFTY": ("NSE", "NIFTY 50"),
            "BANKNIFTY": ("NSE", "NIFTY BANK"),
            "FINNIFTY": ("NSE", "NIFTY FIN SERVICE"),
            "MIDCPNIFTY": ("NSE", "NIFTY MID SELECT"),
            "SENSEX": ("BSE", "SENSEX"),
            "BANKEX": ("BSE", "BANKEX"),
        }

        exchange, name = index_map.get(symbol.upper(), ("NSE", symbol.upper()))
        instrument = f"{exchange}:{name}"

        # Retry with exponential backoff
        for attempt in range(max_retries):
            try:
                ltp_data = self._fetch_ltp_safe(instrument)

                if ltp_data and instrument in ltp_data:
                    price = ltp_data[instrument]["last_price"]
                    self.profile_circuit_breaker.record_success()
                    self.network_monitor.record_success()  # ✅ Network success

                    # Cache the price
                    self._cache_index_price(symbol, price)

                    if attempt > 0:
                        logger.info(f"✓ Retrieved price for {symbol}: {price} (attempt {attempt + 1})")

                    return price

                logger.warning(f"LTP data not found for {instrument}. Response: {ltp_data}")

            except NetworkError as e:
                self.profile_circuit_breaker.record_failure()
                self.network_monitor.record_failure()  # ❌ Network failure

                if attempt < max_retries - 1:
                    # Exponential backoff: 0.5s, 1s, 2s
                    sleep_time = 0.5 * (2 ** attempt)
                    logger.warning(
                        f"Network error attempt {attempt + 1}/{max_retries} for {symbol}: {e}. "
                        f"Retrying in {sleep_time}s..."
                    )
                    time.sleep(sleep_time)
                else:
                    logger.error(
                        f"Network timeout for {symbol} after {max_retries} attempts: {e}"
                    )
                    api_logger.error(f"PRICE_FETCH_TIMEOUT symbol={symbol} error={str(e)[:100]}")

            except Exception as e:
                self.profile_circuit_breaker.record_failure()

                if attempt < max_retries - 1:
                    # Exponential backoff: 0.5s, 1s, 2s
                    sleep_time = 0.5 * (2 ** attempt)
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries} failed for {symbol}: {e}. "
                        f"Retrying in {sleep_time}s..."
                    )
                    time.sleep(sleep_time)
                else:
                    logger.error(
                        f"Failed to get current price for {symbol} after {max_retries} attempts: {e}"
                    )
                    api_logger.error(f"PRICE_FETCH_FAILED symbol={symbol} error={str(e)[:100]}")

        # All retries failed - try cache as last resort
        cached_price = self._get_cached_index_price(symbol)
        if cached_price:
            logger.warning(f"Using cached price for {symbol}: {cached_price}")
            return cached_price

        return None

    def _cache_index_price(self, symbol: str, price: float):
        """Cache index price for fallback use"""
        if not hasattr(self, '_cached_index_prices'):
            self._cached_index_prices = {}

        self._cached_index_prices[symbol] = {
            'price': price,
            'timestamp': datetime.now()
        }

    def _get_cached_index_price(self, symbol: str) -> Optional[float]:
        """
        Get cached index price if available and fresh (< 5 minutes old)
        """
        if not hasattr(self, '_cached_index_prices'):
            return None

        cached = self._cached_index_prices.get(symbol)
        if not cached:
            return None

        # Check if cache is stale (> 5 minutes)
        age = (datetime.now() - cached['timestamp']).total_seconds()
        if age > 300:  # 5 minutes
            logger.debug(f"Cached price for {symbol} is stale ({age:.0f}s old)")
            return None

        return cached['price']

    def _update_market_subscriptions(self):
        self.market_data_orchestrator.update_market_subscriptions()

    @with_timeout(timeout_seconds=5)
    def _periodic_api_health_check(self):
        self.account_health_service._periodic_api_health_check()
        self._sync_account_health_state()

    @with_timeout(timeout_seconds=5)
    def _fetch_profile_safe(self):
        return self.account_health_service._fetch_profile_safe()

    @with_timeout(timeout_seconds=5)
    def _fetch_margins_safe(self):
        return self.account_health_service._fetch_margins_safe()

    def _update_account_info(self):
        self.account_health_service._update_account_info()
        self._sync_account_health_state()

    def _get_account_balance_safe(self) -> float:
        return self.account_health_service._get_account_balance_safe()

    def _sync_account_health_state(self):
        self.last_successful_balance = self.account_health_service.last_successful_balance
        self.last_successful_user_id = self.account_health_service.last_successful_user_id
        self.last_successful_margins = self.account_health_service.last_successful_margins
        self.rms_failures = self.account_health_service.rms_failures

    def _on_positions_updated(self, positions: List[Position]):
        self.position_sync_adapter.on_positions_updated(positions)

    def _on_position_added(self, position: Position):
        self.position_sync_adapter.on_position_added(position)

    def _on_paper_order_rejected(self, data: dict):
        reason = data.get("reason", "Order rejected by RMS")
        symbol = data.get("tradingsymbol", "")
        qty = data.get("quantity", 0)

        message = f"❌ PAPER RMS REJECTED\n{symbol} × {qty}\n\n{reason}"

        # Status bar (non-intrusive)
        self._publish_status(message, 7000, level="error")

        # Optional: modal dialog for visibility
        QMessageBox.warning(
            self,
            "Paper RMS Rejection",
            message
        )

        logger.warning(f"Paper RMS rejection shown to user: {reason}")

    def _on_position_removed(self, symbol: str):
        self.position_sync_adapter.on_position_removed(symbol)

    def _on_refresh_completed(self, success: bool):
        self.position_sync_adapter.on_refresh_completed(success)

    def _on_api_error(self, error_message: str):
        logger.error(f"PositionManager reported API error: {error_message}")
        self._publish_status(f"API Error: {error_message}", 5000, level="error")

    def _on_portfolio_exit_triggered(self, reason: str, pnl: float):
        logger.info(
            f"Portfolio exit handled by UI | Reason={reason}, PnL={pnl:.2f}"
        )

        self._activate_global_kill_switch(
            reason=f"PORTFOLIO_{reason}",
            user_message=f"Portfolio {reason.replace('_', ' ').title()} hit at ₹{pnl:,.2f}. Exiting all and locking entries.",
            exit_open_positions=True,
        )

        # SUCCESS sound for TARGET, FAIL sound for SL
        if reason == "TARGET":
            self._play_sound(success=True)
        else:
            self._play_sound(success=False)

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
        dialog = QuickOrderDialog(
            self,
            position.contract,
            lots,
            mode=QuickOrderMode.MODIFY_RISK
        )
        dialog.populate_from_order(position)
        dialog.risk_confirmed.connect(self._modify_sl_tp_for_position)

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
        self.order_dialog_service.show_pending_orders_dialog()

    def _sync_positions_to_dialog(self):
        self.position_sync_adapter.sync_positions_to_dialog()

    def _show_pnl_history_dialog(self):
        self.analytics_dialog_service.show_pnl_history_dialog()

    def _show_performance_dialog(self):
        self.analytics_dialog_service.show_performance_dialog()

    def _update_pending_order_widgets(self, pending_orders: List[Dict]):
        self.order_dialog_service.update_pending_order_widgets(pending_orders)

    def _cancel_order_by_id(self, order_id: str):
        self.order_dialog_service.cancel_order_by_id(order_id)

    def _show_about(self):
        show_about(self)

    def _show_shortcuts(self):
        show_shortcuts(self)

    def _show_expiry_days(self):
        show_expiry_days(self)

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
        self._reload_risk_limits_from_settings()

    def _reload_risk_limits_from_settings(self):
        self.risk_controller.reload_limits_from_settings()

    def _activate_global_kill_switch(self, reason: str, user_message: Optional[str] = None,
                                     exit_open_positions: bool = True):
        self.risk_controller.activate_global_kill_switch(reason, user_message, exit_open_positions)

    def _evaluate_risk_locks(self):
        self.risk_controller.evaluate_risk_locks()

    def _validate_pre_trade_risk(self, transaction_type: str, quantity: int, tradingsymbol: Optional[str]) -> tuple[bool, str]:
        return self.risk_controller.validate_pre_trade_risk(transaction_type, quantity, tradingsymbol)

    def _reject_order_for_risk(self, reason: str):
        self.risk_controller.reject_order_for_risk(reason)

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

            if symbol_has_changed:
                cvd_token, _, suffix = self._get_cvd_token(symbol)
                if cvd_token:
                    self._update_cvd_chart_symbol(symbol, cvd_token, suffix)

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

            # Use fallback-enabled ladder update instead of hard abort
            if not self._update_strike_ladder_with_fallback(symbol, expiry_date):
                # All fallback strategies failed
                self._settings_changing = False
                return

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
        if getattr(self, '_close_in_progress', False):
            event.ignore()
            return
        self._close_in_progress = True
        logger.info("Close event triggered.")

        # Stop timers first
        if hasattr(self, 'api_health_check_timer'):
            self.api_health_check_timer.stop()
        if hasattr(self, 'update_timer'):
            self.update_timer.stop()
        if hasattr(self, 'pending_order_refresh_timer'):
            self.pending_order_refresh_timer.stop()
        for token in list(getattr(self, '_cvd_pending_retry_timers', {}).keys()):
            self._stop_cvd_pending_retry(token)

        # Background workers
        if hasattr(self, 'market_data_worker') and self.market_data_worker.is_running:
            logger.info("Stopping market data worker...")
            self.market_data_worker.stop()

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
        Executes bulk exit by placing opposite-side orders for each position.
        Execution → order_update → ledger → position update.
        """

        if not positions_list:
            return

        positions_to_exit = [
            p for p in positions_list
            if p.quantity != 0 and not p.is_exiting
        ]

        if not positions_to_exit:
            self._publish_status("No valid positions to exit.", 2500, level="warning")
            return

        self._publish_status(
            f"Exiting {len(positions_to_exit)} positions...", 2500, level="action"
        )

        for pos in positions_to_exit:
            try:
                pos.is_exiting = True  # UI hint only, not state mutation

                # 🔥 FIX: Snapshot position BEFORE exit for live trading
                if self.trading_mode == "live":
                    self._position_snapshots_for_exit[pos.tradingsymbol] = pos
                    logger.debug(f"Cached position snapshot for {pos.tradingsymbol} (Bulk exit)")

                transaction_type = (
                    self.trader.TRANSACTION_TYPE_SELL
                    if pos.quantity > 0
                    else self.trader.TRANSACTION_TYPE_BUY
                )

                order_id = self.trader.place_order(
                    variety=self.trader.VARIETY_REGULAR,
                    exchange=pos.exchange,
                    tradingsymbol=pos.tradingsymbol,
                    transaction_type=transaction_type,
                    quantity=abs(pos.quantity),
                    product=pos.product,
                    order_type=self.trader.ORDER_TYPE_MARKET,
                )

                if not order_id:
                    pos.is_exiting = False
                    logger.error(f"Bulk exit failed for {pos.tradingsymbol}")
                else:
                    logger.info(
                        f"Bulk exit order placed for {pos.tradingsymbol} "
                        f"(Qty: {abs(pos.quantity)}) → {order_id}"
                    )

            except Exception as e:
                pos.is_exiting = False
                logger.error(
                    f"Bulk exit initiation failed for {pos.tradingsymbol}: {e}",
                    exc_info=True
                )

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
            self._publish_status(
                "All positions exited successfully.", 5000, level="success"
            )

            # 🔑 FORCE UI SYNC AFTER BULK EXIT
            self._refresh_positions()
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
        # Keep positions table in sync after partial exits so users don't need
        # to manually trigger refresh.
        self._refresh_positions()
        logger.warning(
            f"Bulk exit incomplete — remaining positions: {symbols}"
        )

    def _exit_position(self, position_data_to_exit: dict):
        tradingsymbol = position_data_to_exit.get("tradingsymbol")
        current_quantity = position_data_to_exit.get("quantity", 0)
        entry_price = position_data_to_exit.get("average_price", 0.0)
        pnl = position_data_to_exit.get("pnl", 0.0)
        exchange = position_data_to_exit.get("exchange", "NFO")
        product = position_data_to_exit.get("product", "MIS")

        # --------------------------------------------------
        # Basic validation
        # --------------------------------------------------
        if not tradingsymbol or current_quantity == 0:
            QMessageBox.warning(
                self,
                "Exit Failed",
                "Invalid position data for exit (missing symbol or zero quantity)."
            )
            logger.warning(f"Invalid exit request: {position_data_to_exit}")
            return

        exit_quantity = abs(current_quantity)

        # --------------------------------------------------
        # 🔒 SNAPSHOT POSITION BEFORE EXIT
        # --------------------------------------------------
        original_position = self.position_manager.get_position(tradingsymbol)
        if not original_position:
            QMessageBox.warning(
                self,
                "Exit Failed",
                f"Position {tradingsymbol} not found. It may have already been exited."
            )
            logger.warning(f"Exit aborted — position not found: {tradingsymbol}")
            return

        # --------------------------------------------------
        # User confirmation
        # --------------------------------------------------
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

        self._publish_status(f"Exiting position {tradingsymbol}...", 2000, level="action")

        # --------------------------------------------------
        # Place exit order
        # --------------------------------------------------
        try:
            transaction_type = (
                self.trader.TRANSACTION_TYPE_SELL
                if current_quantity > 0
                else self.trader.TRANSACTION_TYPE_BUY
            )

            order_id = self.trader.place_order(
                variety=self.trader.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=exit_quantity,
                product=product,
                order_type=self.trader.ORDER_TYPE_MARKET,
            )

            logger.info(
                f"Exit order placed for {tradingsymbol} "
                f"(Qty: {exit_quantity}) | Order ID: {order_id}"
            )

            # 🔥 FIX: Snapshot position IMMEDIATELY after placing order
            # This preserves entry data before the position is removed from the API
            if self.trading_mode == "live":
                self._position_snapshots_for_exit[tradingsymbol] = original_position
                logger.debug(f"Cached position snapshot for {tradingsymbol} (Live exit)")

            # --------------------------------------------------
            # PAPER MODE → UI only, no confirmation loop
            # --------------------------------------------------
            if isinstance(self.trader, PaperTradingManager):
                self._play_sound(success=True)
                return  # 🔒 ABSOLUTE STOP (PositionManager handles removal)

            # --------------------------------------------------
            # LIVE MODE → confirm execution
            # --------------------------------------------------
            import time
            time.sleep(0.5)

            confirmed_order = self._confirm_order_success(order_id)

            if confirmed_order and confirmed_order.get("status") == "COMPLETE":
                exit_price = confirmed_order.get("average_price", 0.0)
                filled_qty = confirmed_order.get("filled_quantity", exit_quantity)

                if current_quantity > 0:
                    realized_pnl = (exit_price - entry_price) * filled_qty
                else:
                    realized_pnl = (entry_price - exit_price) * filled_qty

                self._publish_status(
                    f"Exit confirmed for {tradingsymbol}. Realized P&L: ₹{realized_pnl:,.2f}",
                    5000,
                    level="success"
                )
                self._play_sound(success=True)

            else:
                logger.warning(
                    f"Exit order {order_id} for {tradingsymbol} "
                    f"placed but confirmation pending or failed."
                )
                self._publish_status(
                    f"Exit order {order_id} placed for {tradingsymbol}; confirmation pending.",
                    5000,
                    level="warning"
                )
                self._play_sound(success=False)

        except Exception as e:
            logger.error(
                f"Failed to exit position {tradingsymbol}: {e}",
                exc_info=True
            )
            QMessageBox.critical(
                self,
                "Exit Order Failed",
                f"Failed to place exit order for {tradingsymbol}:\n{e}"
            )
            self._play_sound(success=False)

        finally:
            # --------------------------------------------------
            # Final sync (safe now)
            # --------------------------------------------------
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
            ladder_key = self._option_type_to_ladder_key(option_type)
            return self.strike_ladder.contracts[strike].get(ladder_key)
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

        for strike_detail in confirmed_order_details.get('strikes', []):
            contract = strike_detail.get('contract')
            ok, reason = self._validate_pre_trade_risk(
                transaction_type=self.trader.TRANSACTION_TYPE_BUY,
                quantity=total_quantity_per_strike,
                tradingsymbol=getattr(contract, 'tradingsymbol', None),
            )
            if not ok:
                self._reject_order_for_risk(reason)
                return

        self._publish_status("Placing orders...", 2000, level="action")
        for strike_detail in confirmed_order_details.get('strikes', []):
            contract_to_trade: Optional[Contract] = strike_detail.get('contract')
            if not contract_to_trade or not contract_to_trade.tradingsymbol:
                logger.warning(f"Missing contract or tradingsymbol for strike {strike_detail.get('strike')}. Skipping.")
                failed_orders_info.append(
                    {'symbol': f"Strike {strike_detail.get('strike')}", 'error': "Missing contract data"})
                continue
            try:
                order_args = {
                    'variety': self.trader.VARIETY_REGULAR,
                    'exchange': self.trader.EXCHANGE_NFO,
                    'tradingsymbol': contract_to_trade.tradingsymbol,
                    'transaction_type': self.trader.TRANSACTION_TYPE_BUY,
                    'quantity': total_quantity_per_strike,
                    'product': order_product,
                    'order_type': self.trader.ORDER_TYPE_MARKET,
                }
                execution_request = ExecutionRequest(
                    tradingsymbol=contract_to_trade.tradingsymbol,
                    transaction_type=self.trader.TRANSACTION_TYPE_BUY,
                    quantity=int(total_quantity_per_strike),
                    order_type=self.trader.ORDER_TYPE_MARKET,
                    product=order_product,
                    ltp=float(getattr(contract_to_trade, 'ltp', 0.0) or 0.0),
                    bid=float(getattr(contract_to_trade, 'bid', 0.0) or 0.0),
                    ask=float(getattr(contract_to_trade, 'ask', 0.0) or 0.0),
                    limit_price=None,
                    urgency=str(confirmed_order_details.get('execution_urgency') or 'normal'),
                    participation_rate=float(confirmed_order_details.get('participation_rate') or 0.15),
                    execution_algo=str(confirmed_order_details.get('execution_algo') or 'IMMEDIATE'),
                    max_child_orders=int(confirmed_order_details.get('max_child_orders') or 1),
                    randomize_slices=bool(confirmed_order_details.get('randomize_slices', True)),
                    metadata={'source': 'buy_exit_panel'},
                )
                placed_order_ids = self.execution_stack.execute(
                    request=execution_request,
                    place_order_fn=self.trader.place_order,
                    base_order_args=order_args,
                )

                logger.info(
                    "Execution stack placed %s child order(s) for panel order %s, Qty: %s",
                    len(placed_order_ids),
                    contract_to_trade.tradingsymbol,
                    total_quantity_per_strike,
                )
                if isinstance(self.trader, PaperTradingManager):
                    successful_orders_info.append(
                        {'order_id': placed_order_ids[-1], 'symbol': contract_to_trade.tradingsymbol,
                         'quantity': total_quantity_per_strike,
                         'price': contract_to_trade.ltp})
                    continue

                for order_id in placed_order_ids:
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
                            tsl = confirmed_order_details.get("trailing_stop_loss") or 0
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
                                product=order_product,
                                stop_loss_price=confirmed_order_details.get("stop_loss_price"),
                                target_price=confirmed_order_details.get("target_price"),
                                trailing_stop_loss=tsl if tsl > 0 else None
                            )

                            self.position_manager.add_position(new_position)
                            self.trade_logger.log_trade(confirmed_order_api_data)
                            successful_orders_info.append(
                                {'order_id': order_id, 'symbol': contract_to_trade.tradingsymbol,
                                 'quantity': confirmed_order_api_data.get('filled_quantity', total_quantity_per_strike),
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
        self._publish_status("Order placement flow completed.", 3000, level="info")

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

        # Check if position already exists for this symbol
        position_exists = self.position_manager.get_position(contract.tradingsymbol) is not None

        dialog = QuickOrderDialog(
            parent=self,
            contract=contract,
            default_lots=default_lots,
            position_exists=position_exists
        )
        self.active_quick_order_dialog = dialog

        dialog.order_placed.connect(self._execute_single_strike_order)
        dialog.refresh_requested.connect(self._on_quick_order_refresh_request)
        dialog.finished.connect(lambda: setattr(self, 'active_quick_order_dialog', None))

    def _on_prefilled_modify_dialog_finished(self, result: int, pending_order_id: str | None):
        self.active_quick_order_dialog = None

        if result == QDialog.DialogCode.Rejected:
            if pending_order_id:
                self._publish_status(f"Order {pending_order_id} cancelled.", 4000, level="info")
            else:
                self._publish_status("Order cancelled.", 4000, level="info")

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
        stop_loss_amount = float(order_params.get('stop_loss_amount') or 0)
        target_amount = float(order_params.get('target_amount') or 0)
        trailing_stop_loss_amount = float(order_params.get('trailing_stop_loss_amount') or 0)
        group_name = order_params.get('group_name')
        auto_token = order_params.get('auto_token')

        if group_name and contract_to_trade:
            self.position_manager.set_group_name_hint(contract_to_trade.tradingsymbol, group_name)

        if not contract_to_trade or not quantity:
            logger.error("Invalid parameters for single strike order.")
            QMessageBox.critical(self, "Order Error", "Missing contract or quantity for the order.")
            return

        ok, reason = self._validate_pre_trade_risk(
            transaction_type=transaction_type,
            quantity=quantity,
            tradingsymbol=getattr(contract_to_trade, 'tradingsymbol', None),
        )
        if not ok:
            self._reject_order_for_risk(reason)
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
            if isinstance(self.trader, PaperTradingManager):
                order_args['group_name'] = group_name
            if order_type == self.trader.ORDER_TYPE_LIMIT and price is not None:
                order_args['price'] = price

            execution_request = ExecutionRequest(
                tradingsymbol=contract_to_trade.tradingsymbol,
                transaction_type=transaction_type,
                quantity=int(quantity),
                order_type=order_type,
                product=product,
                ltp=float(getattr(contract_to_trade, 'ltp', 0.0) or 0.0),
                bid=float(getattr(contract_to_trade, 'bid', 0.0) or 0.0),
                ask=float(getattr(contract_to_trade, 'ask', 0.0) or 0.0),
                limit_price=float(price) if price is not None else None,
                urgency=str(order_params.get('execution_urgency') or 'normal'),
                participation_rate=float(order_params.get('participation_rate') or 0.15),
                execution_algo=str(order_params.get('execution_algo') or 'IMMEDIATE'),
                max_child_orders=int(order_params.get('max_child_orders') or 1),
                randomize_slices=bool(order_params.get('randomize_slices', True)),
                metadata={
                    'auto_token': auto_token,
                    'group_name': group_name,
                },
            )
            placed_order_ids = self.execution_stack.execute(
                request=execution_request,
                place_order_fn=self.trader.place_order,
                base_order_args=order_args,
            )
            order_id = placed_order_ids[-1]
            logger.info(
                "Execution stack placed %s child order(s) for %s. Last order id: %s",
                len(placed_order_ids),
                contract_to_trade.tradingsymbol,
                order_id,
            )

            # Re-anchor SL/TP from actual average fill, so entry behaves like MODIFY flow.
            def _build_fill_anchored_risk_values(position):
                qty = abs(position.quantity)
                if qty <= 0:
                    return None, None, None

                is_buy_position = position.quantity > 0
                avg_fill_price = float(position.average_price or 0)
                if avg_fill_price <= 0:
                    return None, None, None

                anchored_sl = None
                anchored_tp = None
                anchored_tsl = None

                if stop_loss_amount > 0:
                    sl_per_unit = stop_loss_amount / qty
                    anchored_sl = (
                        avg_fill_price - sl_per_unit
                        if is_buy_position
                        else avg_fill_price + sl_per_unit
                    )

                if target_amount > 0:
                    tp_per_unit = target_amount / qty
                    anchored_tp = (
                        avg_fill_price + tp_per_unit
                        if is_buy_position
                        else avg_fill_price - tp_per_unit
                    )

                if trailing_stop_loss_amount > 0:
                    anchored_tsl = trailing_stop_loss_amount / qty

                return anchored_sl, anchored_tp, anchored_tsl

            def _apply_risk_after_fill():
                position = self.position_manager.get_position(contract_to_trade.tradingsymbol)
                if not position:
                    logger.warning(
                        "Position not yet available for risk application: %s",
                        contract_to_trade.tradingsymbol,
                    )
                    return

                anchored_sl, anchored_tp, anchored_tsl = _build_fill_anchored_risk_values(position)
                self.position_manager.update_sl_tp_for_position(
                    contract_to_trade.tradingsymbol,
                    anchored_sl,
                    anchored_tp,
                    anchored_tsl,
                )
                logger.info(
                    "✅ Applied fill-anchored SL/TP for %s | SL=%s TP=%s TSL=%s",
                    contract_to_trade.tradingsymbol,
                    anchored_sl,
                    anchored_tp,
                    anchored_tsl,
                )

            # 🔥 FIX: For paper trading, schedule SL/TP application AFTER position refresh
            if isinstance(self.trader, PaperTradingManager):
                # Refresh positions first, then apply SL/TP/TSL from fill-anchored cash risk.
                # This must run for both BUY and SELL entries so trailing SL works consistently.
                QTimer.singleShot(500, self._refresh_positions)
                QTimer.singleShot(1000, _apply_risk_after_fill)

                self._play_sound(success=True)
                return

            # LIVE TRADING PATH
            # CRITICAL: Never call time.sleep() on the GUI thread — it freezes the event
            # loop, stacks up pending timer callbacks and crashes the app under automation.
            # Use QTimer.singleShot to do the confirmation check asynchronously instead.
            if not isinstance(self.trader, PaperTradingManager):
                child_count = max(1, len(placed_order_ids))
                child_qty = max(1, int(quantity / child_count))
                for index, child_order_id in enumerate(placed_order_ids):
                    delay_ms = 500 + (index * 250)
                    QTimer.singleShot(
                        delay_ms,
                        lambda oid=child_order_id, c=contract_to_trade, qty=child_qty,
                               p=price, tt=transaction_type, prod=product,
                               sl=stop_loss_price, tp=target_price,
                               tsl=trailing_stop_loss,
                               sl_amt=stop_loss_amount, tp_amt=target_amount,
                               tsl_amt=trailing_stop_loss_amount, gn=group_name,
                               at=auto_token:
                        self._confirm_and_finalize_order(oid, c, qty, p, tt, prod, sl, tp, tsl,
                                                         sl_amt, tp_amt, tsl_amt, gn, at)
                    )
                return

        except Exception as e:
            self._play_sound(success=False)
            logger.error(f"Single strike order execution failed for {contract_to_trade.tradingsymbol}: {e}",
                         exc_info=True)
            self._handle_order_error(e, order_params)
            self._show_order_results([], [{'symbol': contract_to_trade.tradingsymbol, 'error': str(e)}])

    def _confirm_and_finalize_order(
            self, order_id, contract_to_trade, quantity, price,
            transaction_type, product, stop_loss_price, target_price,
            trailing_stop_loss, stop_loss_amount, target_amount,
            trailing_stop_loss_amount, group_name, auto_token=None
    ):
        """
        Async callback (called via QTimer.singleShot) that replaces the old
        blocking time.sleep(0.5) + _confirm_order_success() pattern.

        This runs on the GUI thread AFTER yielding to the event loop, so all
        pending Qt callbacks (timers, signals) drain normally first.
        """
        self._refresh_positions()
        confirmed_order_api_data = self._confirm_order_success(order_id)
        if confirmed_order_api_data:
            order_status = confirmed_order_api_data.get('status')
            if order_status in ['OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED']:
                if auto_token is not None:
                    self._start_cvd_pending_retry(auto_token)
                self._play_sound(success=True)
                return

            if order_status == 'COMPLETE':
                avg_price_from_order = confirmed_order_api_data.get(
                    'average_price', price if price else contract_to_trade.ltp)
                filled_quantity = confirmed_order_api_data.get('filled_quantity', quantity)

                if transaction_type == self.trader.TRANSACTION_TYPE_BUY:
                    avg_fill_price = avg_price_from_order
                    risk_qty = abs(filled_quantity)

                    if stop_loss_amount and stop_loss_amount > 0 and risk_qty > 0:
                        sl_per_unit = stop_loss_amount / risk_qty
                        stop_loss_price = avg_fill_price - sl_per_unit

                    if target_amount and target_amount > 0 and risk_qty > 0:
                        tp_per_unit = target_amount / risk_qty
                        target_price = avg_fill_price + tp_per_unit

                    if trailing_stop_loss_amount and trailing_stop_loss_amount > 0 and risk_qty > 0:
                        trailing_stop_loss = trailing_stop_loss_amount / risk_qty

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
                        trailing_stop_loss=trailing_stop_loss if trailing_stop_loss and trailing_stop_loss > 0 else None,
                        group_name=group_name
                    )
                    self.position_manager.add_position(new_position)
                    self.trade_logger.log_trade(confirmed_order_api_data)
                    action_msg = "bought"
                else:
                    action_msg = "sold"

                self._play_sound(success=True)
                if auto_token is not None:
                    self._stop_cvd_pending_retry(auto_token)
                self._publish_status(
                    f"Order {order_id} {action_msg} {filled_quantity} {contract_to_trade.tradingsymbol} @ {avg_price_from_order:.2f}.",
                    5000,
                    level="success")
                self._show_order_results(
                    [{'order_id': order_id, 'symbol': contract_to_trade.tradingsymbol}], [])
        else:
            self._play_sound(success=False)
            if auto_token is not None:
                self._start_cvd_pending_retry(auto_token)
            logger.warning(
                f"Single strike order {order_id} for {contract_to_trade.tradingsymbol} "
                "failed or not confirmed.")
            self._show_order_results(
                [], [{'symbol': contract_to_trade.tradingsymbol,
                      'error': "Order rejected or status not confirmed"}])

    def _has_pending_order_for_symbol(self, tradingsymbol: str | None) -> bool:
        if not tradingsymbol:
            return False

        pending_orders = self.position_manager.get_pending_orders() or []
        return any(
            order.get("tradingsymbol") == tradingsymbol
            and order.get("status") in {'OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'}
            for order in pending_orders
        )

    def _start_cvd_pending_retry(self, token: int):
        if isinstance(self.trader, PaperTradingManager):
            return

        if token not in self._cvd_automation_positions:
            self._stop_cvd_pending_retry(token)
            return

        timer = self._cvd_pending_retry_timers.get(token)
        if timer and timer.isActive():
            return

        if timer is None:
            timer = QTimer(self)
            timer.setInterval(10_000)
            timer.timeout.connect(lambda t=token: self._retry_cvd_pending_order(t))
            self._cvd_pending_retry_timers[token] = timer

        logger.info("[AUTO] Started 10s pending-order retry for token=%s", token)
        timer.start()

    def _stop_cvd_pending_retry(self, token: int):
        timer = self._cvd_pending_retry_timers.pop(token, None)
        if timer:
            timer.stop()
            timer.deleteLater()
            logger.info("[AUTO] Stopped pending-order retry for token=%s", token)

    def _retry_cvd_pending_order(self, token: int):
        active_trade = self._cvd_automation_positions.get(token)
        if not active_trade:
            self._stop_cvd_pending_retry(token)
            return

        tradingsymbol = active_trade.get("tradingsymbol")
        if not tradingsymbol:
            self._stop_cvd_pending_retry(token)
            return

        if self.position_manager.get_position(tradingsymbol):
            self._stop_cvd_pending_retry(token)
            return

        self._refresh_positions()

        pending_candidates = [
            order for order in (self.position_manager.get_pending_orders() or [])
            if order.get("tradingsymbol") == tradingsymbol
               and order.get("status") in {'OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'}
        ]
        if not pending_candidates:
            logger.info("[AUTO] No pending order left for %s during retry tick", tradingsymbol)
            return

        pending_order = pending_candidates[-1]
        pending_order_id = pending_order.get("order_id")

        try:
            if pending_order_id:
                self.trader.cancel_order(self.trader.VARIETY_REGULAR, pending_order_id)
                logger.info("[AUTO] Cancelled pending order %s for retry", pending_order_id)
        except Exception as exc:
            logger.warning("[AUTO] Could not cancel pending order %s: %s", pending_order_id, exc)
            return

        latest_contract = self._get_latest_contract_from_ladder(tradingsymbol)
        if not latest_contract:
            logger.warning("[AUTO] Latest contract unavailable for retry: %s", tradingsymbol)
            return

        retry_price = self._calculate_smart_limit_price(latest_contract)
        retry_qty = int(
            pending_order.get("pending_quantity")
            or pending_order.get("quantity")
            or active_trade.get("quantity")
            or 0
        )
        if retry_qty <= 0:
            logger.warning("[AUTO] Invalid retry quantity for %s", tradingsymbol)
            return

        retry_params = {
            "contract": latest_contract,
            "quantity": retry_qty,
            "order_type": self.trader.ORDER_TYPE_LIMIT,
            "price": retry_price,
            "product": pending_order.get("product") or active_trade.get("product") or self.trader.PRODUCT_MIS,
            "transaction_type": pending_order.get("transaction_type") or active_trade.get(
                "transaction_type") or self.trader.TRANSACTION_TYPE_BUY,
            "group_name": active_trade.get("group_name") or f"CVD_AUTO_{token}",
            "auto_token": token,
        }
        logger.info(
            "[AUTO] Replacing pending order for %s every 10s with refreshed LTP %.2f",
            tradingsymbol,
            retry_price,
        )
        self._execute_single_strike_order(retry_params)

    def _execute_strategy_orders(self, order_params_list: List[dict], strategy_name: Optional[str] = None):
        if not order_params_list:
            return

        for order_params in order_params_list:
            side = order_params.get("side", "BUY")
            transaction_type = (
                self.trader.TRANSACTION_TYPE_BUY
                if side.upper() == "BUY"
                else self.trader.TRANSACTION_TYPE_SELL
            )
            mapped_params = {
                **order_params,
                "transaction_type": transaction_type,
                "order_type": self.trader.ORDER_TYPE_MARKET,
                "product": order_params.get("product", self.settings.get("default_product", self.trader.PRODUCT_MIS)),
                "group_name": strategy_name or order_params.get("group_name"),
            }
            self._execute_single_strike_order(mapped_params)
        # Only refresh for live trading, paper trading already scheduled refresh above
        if not isinstance(self.trader, PaperTradingManager):
            self._refresh_positions()

    def _record_completed_exit_trade(
            self,
            confirmed_order: dict,
            original_position,
            trading_mode: str,
            exit_reason: str = "MANUAL"
    ):
        # --------------------------------------------------
        # 🔒 HARD GUARD: original position must exist
        # --------------------------------------------------
        if original_position is None:
            logger.error(
                f"Exit trade skipped: original_position is None "
                f"(order_id={confirmed_order.get('order_id')})"
            )
            return

        trading_mode = trading_mode.upper()

        exit_price = confirmed_order.get("average_price", 0.0)
        filled_qty = confirmed_order.get(
            "filled_quantity",
            abs(original_position.quantity)
        )

        entry_price = original_position.average_price
        is_long = original_position.quantity > 0

        # --------------------------------------------------
        # 🔑 Correct realized P&L calculation
        # --------------------------------------------------
        if is_long:
            realized_pnl = (exit_price - entry_price) * filled_qty
            side = "LONG"
        else:
            realized_pnl = (entry_price - exit_price) * filled_qty
            side = "SHORT"

        trade = {
            "trade_id": str(uuid4()),

            "order_id_entry": original_position.order_id,
            "order_id_exit": confirmed_order.get("order_id"),

            "symbol": original_position.contract.symbol,
            "tradingsymbol": original_position.tradingsymbol,
            "instrument_token": original_position.contract.instrument_token,
            "option_type": original_position.contract.option_type,
            "expiry": original_position.contract.expiry,
            "strike": original_position.contract.strike,

            "side": side,
            "quantity": filled_qty,

            "entry_price": entry_price,
            "exit_price": exit_price,

            "entry_time": (
                original_position.entry_time.isoformat()
                if hasattr(original_position, "entry_time") and original_position.entry_time
                else None
            ),
            "exit_time": datetime.now().isoformat(),

            "realized_pnl": realized_pnl,
            "charges": 0.0,
            "net_pnl": realized_pnl,

            "exit_reason": exit_reason,
            "strategy_tag": None,

            "trading_mode": trading_mode,
            "session_date": date.today().isoformat(),
        }

        # --------------------------------------------------
        # Record trade atomically
        # --------------------------------------------------
        self.trade_ledger.record_trade(trade)

        logger.info(
            f"Trade recorded | {trade['tradingsymbol']} | "
            f"{side} | Qty={filled_qty} | PnL={realized_pnl:.2f}"
        )

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
        """
        Play notification sound with guaranteed volume level.
        Temporarily sets system volume to 80% to ensure audibility.
        """
        try:
            # Save current system volume
            original_volume = self._get_system_volume()

            if original_volume is not None:
                # Set to 80% for notification
                self._set_system_volume(80)

            # Play sound
            sound_effect = QSoundEffect(self)
            filename = "success.wav" if success else "fail.wav"
            base_path = os.path.dirname(os.path.abspath(__file__))
            assets_dir = os.path.join(base_path, "..", "assets")
            if not os.path.exists(assets_dir):
                assets_dir = os.path.join(base_path, "assets")
            sound_path = os.path.join(assets_dir, filename)

            if os.path.exists(sound_path):
                sound_effect.setSource(QUrl.fromLocalFile(sound_path))
                sound_effect.setVolume(1.0)  # Max app volume since we control system volume
                sound_effect.play()

                # Restore original volume after sound plays (~1 second)
                if original_volume is not None:
                    QTimer.singleShot(1200, lambda: self._set_system_volume(original_volume))
            else:
                logger.warning(f"Sound file not found: {sound_path}")

        except Exception as e:
            logger.error(f"Error playing sound: {e}")

    def _get_system_volume(self) -> Optional[int]:
        """Get current system volume (0-100). Returns None if unable to detect."""
        try:
            import platform
            import subprocess

            system = platform.system()

            if system == "Linux":
                # Try PulseAudio (most common)
                try:
                    result = subprocess.run(
                        ['pactl', 'get-sink-volume', '@DEFAULT_SINK@'],
                        capture_output=True,
                        text=True,
                        timeout=1
                    )
                    if result.returncode == 0:
                        # Parse output: "Volume: front-left: 13107 /  20% / -41.79 dB"
                        for part in result.stdout.split():
                            if '%' in part:
                                return int(part.rstrip('%'))
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

                # Try ALSA as fallback
                try:
                    result = subprocess.run(
                        ['amixer', 'get', 'Master'],
                        capture_output=True,
                        text=True,
                        timeout=1
                    )
                    if result.returncode == 0:
                        # Parse: "[20%]"
                        import re
                        match = re.search(r'\[(\d+)%\]', result.stdout)
                        if match:
                            return int(match.group(1))
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

            elif system == "Windows":
                # Windows volume control via comtypes
                try:
                    from comtypes import CLSCTX_ALL
                    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

                    devices = AudioUtilities.GetSpeakers()
                    interface = devices.Activate(
                        IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                    )
                    volume = interface.QueryInterface(IAudioEndpointVolume)
                    current_volume = volume.GetMasterVolumeLevelScalar()
                    return int(current_volume * 100)
                except ImportError:
                    logger.debug("pycaw not installed - install with: pip install pycaw comtypes")
                except Exception:
                    pass

            elif system == "Darwin":  # macOS
                try:
                    result = subprocess.run(
                        ['osascript', '-e', 'output volume of (get volume settings)'],
                        capture_output=True,
                        text=True,
                        timeout=1
                    )
                    if result.returncode == 0:
                        return int(result.stdout.strip())
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

            return None

        except Exception as e:
            logger.debug(f"Could not get system volume: {e}")
            return None

    def _set_system_volume(self, volume: int):
        """Set system volume to percentage (0-100)."""
        try:
            import platform
            import subprocess

            volume = max(0, min(100, volume))  # Clamp to 0-100
            system = platform.system()

            if system == "Linux":
                # Try PulseAudio
                try:
                    subprocess.run(
                        ['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{volume}%'],
                        timeout=1,
                        check=False
                    )
                    logger.debug(f"Set system volume to {volume}% (PulseAudio)")
                    return
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

                # Try ALSA
                try:
                    subprocess.run(
                        ['amixer', 'set', 'Master', f'{volume}%'],
                        timeout=1,
                        check=False
                    )
                    logger.debug(f"Set system volume to {volume}% (ALSA)")
                    return
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

            elif system == "Windows":
                try:
                    from comtypes import CLSCTX_ALL
                    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

                    devices = AudioUtilities.GetSpeakers()
                    interface = devices.Activate(
                        IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                    )
                    volume_interface = interface.QueryInterface(IAudioEndpointVolume)
                    volume_interface.SetMasterVolumeLevelScalar(volume / 100.0, None)
                    logger.debug(f"Set system volume to {volume}% (Windows)")
                    return
                except ImportError:
                    logger.debug("pycaw not installed")
                except Exception:
                    pass

            elif system == "Darwin":  # macOS
                try:
                    subprocess.run(
                        ['osascript', '-e', f'set volume output volume {volume}'],
                        timeout=1,
                        check=False
                    )
                    logger.debug(f"Set system volume to {volume}% (macOS)")
                    return
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

        except Exception as e:
            logger.debug(f"Could not set system volume: {e}")

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
            return ImperiumMainWindow._calculate_ltp_based_price(base_price, tick_size)
        spread_info = ImperiumMainWindow._analyze_bid_ask_spread(bid_price, ask_price, base_price, tick_size)
        if spread_info['has_valid_spread']:
            return ImperiumMainWindow._calculate_spread_based_price(base_price, bid_price, ask_price, spread_info)
        else:
            return ImperiumMainWindow._calculate_ltp_based_price(base_price, tick_size)

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
        return {'symbol': self.header.symbol_button.text(), 'strike_step': strike_step,
                'expiry': self.header.expiry_combo.currentText(), 'lot_size': self.header.lot_size_spin.value()}

    def _on_lot_size_changed(self, num_lots: int):
        if self._settings_changing or not self.instrument_data:
            return

        symbol = self.header.symbol_button.text()
        expiry_str = self.header.expiry_combo.currentText()

        if not symbol:
            return

        lot_quantity = self.instrument_data.get(symbol, {}).get('lot_size', 1)

        self.buy_exit_panel.update_parameters(symbol, num_lots, lot_quantity, expiry_str)
        logger.debug(f"Lot size updated to {num_lots} without refreshing ladder.")

    def _refresh_data(self):
        self._publish_status("Refreshing data...", 3000, level="action")
        self._refresh_positions()
        self._refresh_orders()
        self._update_account_info()
        self._publish_status("Data refreshed.", 3000, level="success")

    def _refresh_positions(self):
        if not self.trader:
            logger.warning("Kite client not available for position refresh.")
            self._publish_status("API client not set. Cannot refresh positions.", 3500, level="error")
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
            'trailing_stop_loss': position.trailing_stop_loss,
            'group_name': position.group_name
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
            self._publish_status(f"Failed to fetch orders: {e}", 3500, level="warning")

    def _update_performance(self):
        if not self.performance_dialog:
            return

        today = date.today().isoformat()
        stats = self.trade_ledger.get_trade_stats_for_date(today)

        metrics = {
            "total_trades": stats["total_trades"],
            "winning_trades": stats["wins"],
            "losing_trades": stats["losses"],
            "win_rate": stats["win_rate"],
            "total_pnl": stats["total_pnl"],
        }

        if self.performance_dialog.isVisible():
            self.performance_dialog.update_metrics(metrics)

    def _update_account_summary_widget(self):
        trading_day = date.today().isoformat()
        # 1️⃣ TODAY'S realized stats (LEDGER)
        stats = self.trade_ledger.get_daily_trade_stats(
            trading_day=trading_day
        )

        realized_pnl = stats["total_pnl"]
        win_rate = stats["win_rate"]
        trade_count = stats["total_trades"]

        # 2️⃣ Unrealized PnL (POSITIONS)
        unrealized_pnl = self.position_manager.get_total_pnl()

        # 3️⃣ Margins (MODE AWARE)
        used_margin = 0.0
        available_margin = 0.0

        try:
            if self.trading_mode == "paper":
                margins = self.trader.margins()
                equity = margins.get("equity", {})
                available_margin = equity.get("available", {}).get("live_balance", 0.0)
                used_margin = equity.get("utilised", {}).get("total", 0.0)
            else:
                margins = self.real_kite_client.margins()
                equity = margins.get("equity", {})
                available_margin = equity.get("available", {}).get("live_balance", 0.0)
                used_margin = equity.get("utilised", {}).get("total", 0.0)
        except Exception as e:
            logger.warning(f"Margin fetch failed: {e}")

        # 4️⃣ Push ONLY daily data to widget
        self.account_summary.update_summary(
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            used_margin=used_margin,
            available_margin=available_margin,
            win_rate=win_rate,
            trade_count=trade_count
        )

    def _schedule_trading_day_reset(self):

        now = datetime.now()
        today_730 = datetime.combine(now.date(), TRADING_DAY_START)

        if now >= today_730:
            today_730 += timedelta(days=1)

        ms_until_reset = int((today_730 - now).total_seconds() * 1000)

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_trading_day_reset)
        timer.start(ms_until_reset)

    def _on_trading_day_reset(self):
        logger.info("Trading day reset at 07:30 AM")
        self.risk_controller.reset_for_new_trading_day()
        self._update_account_summary_widget()
        self._schedule_trading_day_reset()  # schedule next day

    def _update_ui(self):
        self._update_account_summary_widget()
        self._evaluate_risk_locks()

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
        market_status = "Open" if is_market_open else "Closed"

        if self.margin_circuit_breaker.state == "OPEN" or self.profile_circuit_breaker.state == "OPEN":
            api_status = "Degraded"
        elif self.margin_circuit_breaker.state == "HALF_OPEN" or self.profile_circuit_breaker.state == "HALF_OPEN":
            api_status = "Recovering"
        else:
            api_status = "Healthy"

        # Update status bar through StatusBarWidget
        self.status_bar_widget.update_api_status(f"API {api_status}")

        # Use case-insensitive check since self.network_status is lowercase
        network_status_lower = self.network_status.lower()
        if "connected" in network_status_lower and "disconnected" not in network_status_lower:
            network_chip_status = "Connected"
        elif "disconnected" in network_status_lower:
            network_chip_status = "Disconnected"
        elif "connecting" in network_status_lower or "reconnecting" in network_status_lower:
            network_chip_status = self.network_status.title()  # Capitalize first letter
        else:
            network_chip_status = self.network_status.title()

        self.status_bar_widget.update_network_status(network_chip_status)
        self.status_bar_widget.update_market_status(f"Market {market_status}")
        self.status_bar_widget.update_clock(now.strftime("%H:%M:%S"))

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
            self._publish_status(f"Order {order_id} cancelled. Please enter new order details.", 4000, level="info")
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

        # Check if position already exists for this symbol
        position_exists = self.position_manager.get_position(contract.tradingsymbol) is not None

        dialog = QuickOrderDialog(
            parent=self,
            contract=contract,
            default_lots=default_lots,
            position_exists=position_exists
        )
        self.active_quick_order_dialog = dialog

        dialog.populate_from_order(order_data)

        dialog.order_placed.connect(self._execute_single_strike_order)
        dialog.refresh_requested.connect(self._on_quick_order_refresh_request)
        dialog.finished.connect(
            lambda result, pending_order_id=order_data.get("order_id"): self._on_prefilled_modify_dialog_finished(
                result,
                pending_order_id,
            )
        )

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

    def _get_cvd_token(self, symbol: str):
        """
        Get the appropriate token for CVD calculation.

        Logic:
        - For INDICES (NIFTY, BANKNIFTY, etc.): Use FUTURES (no equity available)
        - For STOCKS: Use EQUITY token if available, fallback to FUTURES

        Returns tuple: (token, is_equity, suffix_for_display)
        """
        symbol = symbol.upper()
        symbol_info = self.instrument_data.get(symbol)
        if not symbol_info:
            return None, False, ""

        # List of known indices (these MUST use futures)
        INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}

        is_index = symbol in INDICES

        # For indices, always use futures
        if is_index:
            fut_token = self._get_nearest_future_token(symbol)
            if fut_token:
                logger.info(f"[CVD] Using FUT for INDEX {symbol}: {fut_token}")
                return fut_token, False, " FUT"
            return None, False, ""

        # For stocks, prefer equity token
        equity_token = symbol_info.get('instrument_token')
        if equity_token:
            logger.info(f"[CVD] Using EQUITY for {symbol}: {equity_token}")
            return equity_token, True, ""

        # Fallback to futures if equity not available
        fut_token = self._get_nearest_future_token(symbol)
        if fut_token:
            logger.info(f"[CVD] Using FUT (fallback) for {symbol}: {fut_token}")
            return fut_token, False, " FUT"

        logger.warning(f"[CVD] No token found for {symbol}")
        return None, False, ""

    def _change_lot_size(self, delta: int):
        current = self.header.lot_size_spin.value()
        new_value = max(1, current + delta)
        self.header.lot_size_spin.setValue(new_value)

    def _set_lot_size(self, value: int):
        if value > 0:
            self.header.lot_size_spin.setValue(value)

    def _buy_relative_to_atm(self, above: int = 0, below: int = 0):
        """
        Buy option at ATM / ATM+n / ATM-n using BuyExitPanel logic.
        above: strikes above ATM
        below: strikes below ATM
        """

        if not self.buy_exit_panel:
            return

        previous_above = self.buy_exit_panel.above_spin.value()
        previous_below = self.buy_exit_panel.below_spin.value()

        try:
            # Reset previous selections
            self.buy_exit_panel.above_spin.setValue(0)
            self.buy_exit_panel.below_spin.setValue(0)

            # Apply new selection
            if above > 0:
                self.buy_exit_panel.above_spin.setValue(above)
            if below > 0:
                self.buy_exit_panel.below_spin.setValue(below)

            # Trigger BUY using existing safe logic
            self.buy_exit_panel._on_buy_clicked()
        finally:
            # Restore UI state so keyboard quick-actions don't overwrite persisted panel settings.
            self.buy_exit_panel.above_spin.setValue(previous_above)
            self.buy_exit_panel.below_spin.setValue(previous_below)

    def _buy_exact_relative_strike(self, offset: int):
        """
        Buy EXACTLY one strike relative to ATM.
        Must NOT inherit BuyExitPanel above/below state.
        """

        if not self.strike_ladder or not self.buy_exit_panel:
            return

        atm_strike = self.strike_ladder.atm_strike
        strike_step = self.strike_ladder.get_strike_interval()

        if atm_strike is None or not strike_step:
            return

        target_strike = atm_strike + (offset * strike_step)
        option_type = self.buy_exit_panel.option_type

        ladder_key = self._option_type_to_ladder_key(option_type)
        contract = self.strike_ladder.contracts.get(
            target_strike, {}
        ).get(ladder_key)

        if not contract:
            QMessageBox.warning(
                self,
                "Strike Not Available",
                f"Strike {target_strike} not available for {option_type.name}"
            )
            return

        # ✅ EXACT same behavior as clicking ONE ladder row
        self._on_single_strike_selected(contract)

    def _check_live_completed_orders(self):
        if self.trading_mode != "live":
            return

        try:
            orders = self.real_kite_client.orders()
        except Exception as e:
            logger.debug(f"Live order fetch failed: {e}")
            return

        for order in orders:
            if order.get("status") != "COMPLETE":
                continue

            if order.get("transaction_type") != "SELL":
                continue

            order_id = order.get("order_id")
            if not order_id:
                continue

            # 🔒 Prevent duplicate ledger writes
            if order_id in self._processed_live_exit_orders:
                continue

            tradingsymbol = order.get("tradingsymbol")

            # NOTE:
            # We intentionally snapshot the CURRENT Position object at exit time.
            # For LIVE trading, each completed SELL order is treated as an independent exit trade.
            # This design correctly supports partial exits and scaling out.
            # Do NOT replace this with cached entry data or dict snapshots.

            # 🔥 FIX: Try cached snapshot first, then current position
            # The position may already be removed from API after order completion
            original_position = self._position_snapshots_for_exit.get(tradingsymbol)

            if not original_position:
                # Fallback: Try getting current position (may still exist for partial exits)
                original_position = self.position_manager.get_position(tradingsymbol)

            if not original_position:
                logger.warning(
                    f"[LIVE] Cannot record exit trade for {tradingsymbol} - "
                    f"no position snapshot or current position found (order_id: {order_id})"
                )
                continue

            self._record_completed_exit_trade(
                confirmed_order=order,
                original_position=original_position,
                trading_mode="LIVE"
            )

            self._processed_live_exit_orders.add(order_id)

            # 🔥 FIX: Clean up snapshot after successful recording
            if tradingsymbol in self._position_snapshots_for_exit:
                del self._position_snapshots_for_exit[tradingsymbol]
                logger.debug(f"Removed position snapshot for {tradingsymbol}")

    def _on_strike_chart_requested(self, contract: Contract):
        """Open CVD Single Chart Dialog for the selected strike"""
        if not contract:
            logger.warning("Chart requested but contract data is missing.")
            return

        try:
            cvd_token = contract.instrument_token
            symbol = contract.tradingsymbol

            # If dialog already exists for this token, just raise it
            if cvd_token in self.cvd_single_chart_dialogs:
                existing_dialog = self.cvd_single_chart_dialogs[cvd_token]
                try:
                    if existing_dialog and not existing_dialog.isHidden():
                        existing_dialog.raise_()
                        existing_dialog.activateWindow()
                        return
                except RuntimeError:
                    # Dialog was already destroyed
                    del self.cvd_single_chart_dialogs[cvd_token]

            # Ensure CVD engine + websocket are both tracking this token.
            if self.cvd_engine:
                try:
                    self.cvd_engine.set_mode(CVDMode.SINGLE_CHART)
                    self.cvd_engine.register_token(cvd_token)
                    self.active_cvd_tokens.add(cvd_token)
                    self._update_market_subscriptions()

                    if hasattr(self.market_data_worker, 'subscribed_tokens') and (
                            cvd_token not in self.market_data_worker.subscribed_tokens
                    ):
                        QMessageBox.warning(
                            self,
                            "Subscription Failed",
                            f"Failed to subscribe to market data for {symbol}.\n"
                            "The chart may not update in real-time."
                        )
                except Exception as e:
                    logger.error(f"Failed to subscribe to CVD data: {e}")

            # Create new CVD Single Chart Dialog
            dialog = AutoTraderDialog(
                kite=self.real_kite_client,
                instrument_token=cvd_token,
                symbol=symbol,
                cvd_engine=self.cvd_engine,
                parent=self
            )
            dialog.destroyed.connect(
                lambda: self._on_cvd_single_chart_closed(cvd_token)
            )
            self.cvd_single_chart_dialogs[cvd_token] = dialog
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

            logger.info(f"[CVD] Chart opened for strike {symbol} (token: {cvd_token})")

        except Exception as e:
            logger.error(f"Failed to open chart for strike: {e}")
            QMessageBox.critical(
                self,
                "Chart Error",
                f"Failed to open chart for {contract.tradingsymbol}:\n{str(e)}"
            )

    @staticmethod
    def _option_type_to_ladder_key(option_type: OptionType) -> str:
        return "CE" if option_type == OptionType.CALL else "PE"

    def _show_fii_dii_dialog(self):
        """Show FII/DII data dialog"""
        if self.fii_dii_dialog is None:
            self.fii_dii_dialog = FIIDIIDialog(parent=self)

        if self.fii_dii_dialog.isVisible():
            self.fii_dii_dialog.raise_()
            self.fii_dii_dialog.activateWindow()
        else:
            self.fii_dii_dialog.show()

    def _update_strike_ladder_with_fallback(self, symbol: str, expiry_date):
        """
        Update strike ladder with graceful degradation on price fetch failure
        """
        current_price = self._get_current_price(symbol)

        if current_price is None:
            # CRITICAL: Don't abort - use fallback strategies
            logger.warning(f"Could not get current price for {symbol}. Trying fallback strategies...")

            # Strategy 1: Use last known index price from market data
            if hasattr(self.strike_ladder, 'last_index_price') and self.strike_ladder.last_index_price:
                current_price = self.strike_ladder.last_index_price
                logger.info(f"Using last known index price: {current_price}")

            # Strategy 2: Use position-based price estimate
            elif self.position_manager.get_all_positions():
                estimated_price = self._estimate_underlying_from_positions(symbol)
                if estimated_price:
                    current_price = estimated_price
                    logger.info(f"Using position-based price estimate: {current_price}")

            # Strategy 3: Load from yesterday's close (if you have this data)
            if current_price is None:
                # Show clear error to user with retry option
                self._show_network_error_notification(symbol)
                logger.error(f"All fallback strategies failed for {symbol}. Ladder update aborted.")
                return False

        # Proceed with ladder update
        calculated_interval = self.strike_ladder.calculate_strike_interval(symbol)

        self.strike_ladder.update_strikes(
            symbol=symbol,
            current_price=current_price,
            expiry=expiry_date,
            strike_interval=calculated_interval
        )

        self._update_market_subscriptions()
        return True

    def _estimate_underlying_from_positions(self, symbol: str) -> Optional[float]:
        """
        Estimate underlying price from current option positions
        Uses Black-Scholes reverse calculation approximation
        """
        positions = self.position_manager.get_all_positions()

        for pos in positions:
            if pos.contract and pos.contract.symbol == symbol:
                # Simple approximation: ATM strike ≈ underlying
                # For better accuracy, you'd use IV and Greeks
                if abs(pos.contract.strike - pos.last_price) < 1000:  # Near ATM
                    return pos.contract.strike

        return None

    def _show_network_error_notification(self, symbol: str):
        """Show user-friendly network error with retry option"""
        # Only show once per session to avoid spam
        if hasattr(self, '_network_error_shown') and self._network_error_shown:
            return

        self._network_error_shown = True

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Network Issue")
        msg.setText(
            f"Unable to fetch current price for {symbol}.\n\n"
            "This may be due to:\n"
            "• Network connectivity issues\n"
            "• Kite API temporarily unavailable\n"
            "• DNS resolution failure\n\n"
            "The app will continue with cached data where possible."
        )
        msg.setStandardButtons(QMessageBox.Retry | QMessageBox.Ignore)

        # Show status in status bar
        self._publish_status(
            f"⚠ Network issue detected - using cached data for {symbol}",
            duration=10000,
            level="warning"
        )

        if msg.exec() == QMessageBox.Retry:
            # Clear circuit breaker and retry
            self.profile_circuit_breaker.reset()
            self._network_error_shown = False

    def _on_websocket_state_changed(self, new_state: str):
        """
        Handle WebSocket state changes with proper subscription management

        Called by market_data_worker when connection state changes
        """
        if new_state == "connected":
            self.ws_state = WebSocketState.CONNECTED
            self.ws_connection_time = datetime.now()
            self.ws_ready_event_fired = True

            logger.info("WebSocket connected - processing subscription queue")

            # 🔥 Subscribe ALL required tokens cleanly
            self._update_market_subscriptions()

            self._process_subscription_queue()

            self._publish_status("✓ Market data connected", 3000, level="success")


        elif new_state == "connecting":
            self.ws_state = WebSocketState.CONNECTING
            self._publish_status("Connecting to market data...", 5000, level="action")

        elif new_state == "disconnected":
            old_state = self.ws_state
            self.ws_state = WebSocketState.DISCONNECTED
            self.ws_ready_event_fired = False

            if old_state == WebSocketState.CONNECTED:
                logger.warning("WebSocket disconnected - will queue subscriptions")
                self._publish_status("⚠ Market data disconnected - reconnecting...", 0, level="warning")

        elif new_state == "reconnecting":
            self.ws_state = WebSocketState.RECONNECTING
            self._publish_status("Reconnecting to market data...", 5000, level="action")

    def _process_subscription_queue(self):
        """
        Process all queued subscriptions when WebSocket becomes ready
        """
        if not self.subscription_queue:
            logger.debug("Subscription queue is empty")
            return

        logger.info(f"Processing {len(self.subscription_queue)} queued subscriptions")

        # Merge all queued token sets
        all_tokens = set()
        while self.subscription_queue:
            tokens = self.subscription_queue.popleft()
            all_tokens.update(tokens)

        self.pending_subscriptions.clear()

        if all_tokens:
            logger.info(f"Subscribing to {len(all_tokens)} tokens from queue")
            self.market_data_worker.set_instruments(all_tokens)

    def _on_network_status_changed(self, status):
        """
        Normalize and handle network status changes.
        Handles decorated strings like:
        'Connected (Post-Market)'
        'Connected (Pre-Market)'
        """

        if isinstance(status, dict):
            raw_state = str(status.get("state", "")).strip().lower()
            message = status.get("message", "")
        else:
            raw_state = str(status).strip().lower()
            message = ""

        # 🔥 FIX: Use prefix matching instead of exact match
        if raw_state.startswith("connected"):
            normalized = "connected"
        elif raw_state.startswith("connecting") or raw_state.startswith("reconnecting"):
            normalized = "connecting"
        elif raw_state.startswith("disconnected") or raw_state.startswith("error"):
            normalized = "disconnected"
        else:
            normalized = "initializing"

        self.network_status = raw_state
        self.status_bar_widget.update_network_status(status)

        # 🔥 CRITICAL
        self._on_websocket_state_changed(normalized)

        if message:
            logger.info(f"Network status: {normalized} - {message}")

    def _on_connection_lost(self):
        """Called when network connection is lost (detected by failed API calls)"""
        logger.warning("🔴 Network connection lost - showing error banner")
        self._show_connection_error_banner("No internet connection detected")

        # Update status bar with persistent warning
        self._publish_status(
            "⚠️ Internet connection lost - retrying...",
            duration=0,  # Persistent until resolved
            level="error"
        )

    def _on_connection_restored(self):
        """Called when network connection is restored"""
        logger.info("✅ Network connection restored")

        # Clear error banner
        if self._network_error_notification:
            self._network_error_notification.close()
            self._network_error_notification = None

        # Update status bar with success message
        self._publish_status(
            "✅ Internet connection restored",
            duration=3000,
            level="success"
        )

        # Refresh data after reconnection
        self._periodic_api_health_check()
        self._update_market_subscriptions()

    def _show_connection_error_banner(self, message: str):
        """Show persistent connection error banner at top of window"""
        # Don't spam notifications
        if self._network_error_notification:
            return

        banner = QFrame(self)
        banner.setStyleSheet("""
            QFrame {
                background-color: #ff4444;
                border: 2px solid #cc0000;
                border-radius: 4px;
                padding: 8px;
            }
        """)

        layout = QVBoxLayout(banner)
        label = QLabel(f"⚠️ {message}\nRetrying automatically...")
        label.setStyleSheet("color: white; font-weight: bold; font-size: 12px;")
        layout.addWidget(label)

        # Position at top of window
        banner.setParent(self)
        banner.setGeometry(10, 60, self.width() - 20, 50)  # Below title bar
        banner.show()
        banner.raise_()

        self._network_error_notification = banner
        logger.debug("Connection error banner shown")
