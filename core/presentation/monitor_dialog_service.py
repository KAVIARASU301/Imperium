import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from dialogs.cvd_multi_chart_dialog import CVDMultiChartDialog
from dialogs.market_monitor_dialog import MarketMonitorDialog
from dialogs.watchlist_dialog import WatchlistDialog

logger = logging.getLogger(__name__)


class MonitorDialogService:
    """Presentation orchestration for market monitor/watchlist/CVD monitor dialogs."""

    def __init__(self, main_window):
        self.main_window = main_window

    def show_market_monitor_dialog(self):
        w = self.main_window
        try:
            dialog = MarketMonitorDialog(
                real_kite_client=w.real_kite_client,
                market_data_worker=w.market_data_worker,
                config_manager=w.config_manager,
                parent=w,
            )
            w.market_monitor_dialogs.append(dialog)
            dialog.destroyed.connect(lambda: w._on_market_monitor_closed(dialog))
            dialog.show()
        except Exception as e:
            logger.error("Failed to create Market Monitor dialog: %s", e, exc_info=True)
            QMessageBox.critical(w, "Error", f"Could not open Market Monitor:\n{e}")

    def show_watchlist_dialog(self):
        w = self.main_window
        if w.watchlist_dialog is None:
            symbols = sorted(w.instrument_data.keys()) if w.instrument_data else []
            w.watchlist_dialog = WatchlistDialog(symbols=symbols, parent=w)
            w.watchlist_dialog.symbol_selected.connect(w._on_watchlist_symbol_selected)
            w.watchlist_dialog.finished.connect(lambda: setattr(w, "watchlist_dialog", None))
        w.watchlist_dialog.show()
        w.watchlist_dialog.raise_()
        w.watchlist_dialog.activateWindow()

    def show_cvd_chart_dialog(self):
        """
        Previously opened an AutoTraderDialog chart popup per symbol.
        Now routes the selected symbol directly into the AtrScannerPanel
        watchlist (the embedded center panel) instead of opening a popup.
        """
        w = self.main_window
        current_settings = w.header.get_current_settings()
        symbol = current_settings.get("symbol")

        if not symbol:
            QMessageBox.warning(w, "ATR Scanner", "No symbol selected.")
            return

        cvd_token, _, _ = w._get_cvd_token(symbol)
        if not cvd_token:
            QMessageBox.warning(w, "ATR Scanner", f"No instrument token found for {symbol}.")
            return

        # Register the token for market data subscription (keep existing infra working)
        w.cvd_engine.register_token(cvd_token)
        w.active_cvd_tokens.add(cvd_token)
        w._update_market_subscriptions()

        # Forward to the embedded AtrScannerPanel
        panel = getattr(w, "auto_trader_embed", None)
        if panel is not None and hasattr(panel, "add_symbol_programmatic"):
            panel.add_symbol_programmatic(symbol, cvd_token)
            # Switch center view to the scanner panel if not already visible
            if hasattr(w, "center_stack"):
                w.center_stack.setCurrentIndex(1)
            logger.info("[CVD] Routed %s (token=%d) to ATR Scanner panel", symbol, cvd_token)
        else:
            QMessageBox.information(
                w,
                "ATR Scanner",
                f"{symbol} (token {cvd_token}) — ATR Scanner panel not available.\n"
                "Switch layout to 'auto' mode to open it.",
            )

    def show_cvd_market_monitor_dialog(self):
        w = self.main_window
        symbol_to_token = {}

        for symbol in w.cvd_symbols:
            fut_token = w._get_nearest_future_token(symbol)
            if fut_token:
                symbol_to_token[symbol] = fut_token
                w.active_cvd_tokens.add(fut_token)

        if not symbol_to_token:
            QMessageBox.warning(w, "CVD Monitor", "No futures available.")
            return

        w._update_market_subscriptions()

        dlg = CVDMultiChartDialog(kite=w.real_kite_client, symbol_to_token=symbol_to_token, parent=w)
        dlg.destroyed.connect(w._on_cvd_market_monitor_closed)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()