"""Dialog components for Options Scalper"""
from .market_monitor_dialog import MarketMonitorDialog
from .open_positions_dialog import OpenPositionsDialog
from .option_chain_dialog import OptionChainDialog
from .order_confirmation_dialog import OrderConfirmationDialog
from .order_history_dialog import OrderHistoryDialog
from .pending_orders_dialog import PendingOrdersDialog
from .performance_dialog import PerformanceDialog
from .pnl_history_dialog import PnlHistoryDialog
from .quick_order_dialog import QuickOrderDialog
from .settings_dialog import SettingsDialog
from .strategy_builder_dialog import StrategyBuilderDialog
from .watchlist_dialog import WatchlistDialog

__all__ = [
    'MarketMonitorDialog',
    'OpenPositionsDialog',
    'OptionChainDialog',
    'OrderConfirmationDialog',
    'OrderHistoryDialog',
    'PendingOrdersDialog',
    'PerformanceDialog',
    'PnlHistoryDialog',
    'QuickOrderDialog',
    'SettingsDialog',
    'StrategyBuilderDialog',
    'WatchlistDialog'
]
