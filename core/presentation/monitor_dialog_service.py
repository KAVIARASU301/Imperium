import logging

from PySide6.QtWidgets import QMessageBox

from core.dialogs import MarketMonitorDialog
from core.dialogs import WatchlistDialog

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
        w = self.main_window
        current_settings = w.header.get_current_settings()
        symbol = current_settings.get("symbol")

        if not symbol:
            QMessageBox.warning(w, "CVD Chart", "No symbol selected.")
            return

        cvd_token, _, suffix = w._get_cvd_token(symbol)
        if not cvd_token:
            QMessageBox.warning(w, "CVD Chart", f"No token found for {symbol}.")
            return

        w._open_cvd_single_chart(symbol, cvd_token, suffix)