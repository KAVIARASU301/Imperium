import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from core.auto_trader import AutoTraderDialog
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

        if w.header_linked_cvd_token is not None:
            linked_dialog = w.cvd_single_chart_dialogs.get(w.header_linked_cvd_token)
            if linked_dialog and not linked_dialog.isHidden():
                if w.header_linked_cvd_token == cvd_token:
                    linked_dialog.raise_()
                    linked_dialog.activateWindow()
                    return

                self.retarget_cvd_dialog(
                    dialog=linked_dialog,
                    old_token=w.header_linked_cvd_token,
                    new_token=cvd_token,
                    symbol=symbol,
                    suffix=suffix,
                )
                w.header_linked_cvd_token = cvd_token
                linked_dialog.raise_()
                linked_dialog.activateWindow()
                return

        if cvd_token in w.cvd_single_chart_dialogs:
            existing_dialog = w.cvd_single_chart_dialogs[cvd_token]
            if existing_dialog and not existing_dialog.isHidden():
                existing_dialog.raise_()
                existing_dialog.activateWindow()
                return

        w.cvd_engine.register_token(cvd_token)
        w.active_cvd_tokens.add(cvd_token)

        w._update_market_subscriptions()

        QTimer.singleShot(500, lambda: self.open_cvd_chart_after_subscription(cvd_token, symbol, suffix, True))
        QTimer.singleShot(1000, w._log_active_subscriptions)

    def open_cvd_chart_after_subscription(self, cvd_token: int, symbol: str, suffix: str = "", link_to_header: bool = False):
        w = self.main_window
        try:
            if hasattr(w.market_data_worker, "subscribed_tokens") and cvd_token not in w.market_data_worker.subscribed_tokens:
                logger.error("[CVD] Token %s NOT in subscribed_tokens!", cvd_token)
                QMessageBox.warning(
                    w,
                    "Subscription Failed",
                    f"Failed to subscribe to market data for {symbol}.\nThe chart may not update in real-time.",
                )

            dialog = AutoTraderDialog(
                kite=w.real_kite_client,
                instrument_token=cvd_token,
                symbol=f"{symbol}{suffix}",
                cvd_engine=w.cvd_engine,
                parent=w,
            )
            dialog.automation_signal.connect(w._on_cvd_automation_signal)
            dialog.automation_state_signal.connect(w._on_cvd_automation_market_state)
            dialog.destroyed.connect(lambda: w._on_cvd_single_chart_closed(cvd_token))
            w.cvd_single_chart_dialogs[cvd_token] = dialog
            if link_to_header:
                w.header_linked_cvd_token = cvd_token
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

            logger.info("[CVD] Chart opened for token %s (%s%s)", cvd_token, symbol, suffix)

        except Exception as e:
            logger.error("Failed to open CVD Chart dialog", exc_info=True)
            QMessageBox.critical(w, "CVD Chart Error", f"Failed to open CVD chart:\n{e}")

    def retarget_cvd_dialog(self, dialog: AutoTraderDialog, old_token: int, new_token: int, symbol: str, suffix: str = ""):
        w = self.main_window
        if old_token == new_token:
            return

        try:
            w.cvd_engine.register_token(new_token)
            w.active_cvd_tokens.add(new_token)

            if old_token and old_token != new_token:
                w.active_cvd_tokens.discard(old_token)

            if old_token in w.cvd_single_chart_dialogs:
                del w.cvd_single_chart_dialogs[old_token]
            w.cvd_single_chart_dialogs[new_token] = dialog

            dialog.instrument_token = new_token
            dialog.symbol = f"{symbol}{suffix}"
            dialog.setWindowTitle(f"Price & Cumulative Volume Chart — {symbol}{suffix}")

            dialog.current_date, dialog.previous_date = dialog.navigator.get_dates()
            dialog._load_and_plot()

            w._update_market_subscriptions()

            logger.info("[CVD] Updated chart from token %s to %s (%s%s)", old_token, new_token, symbol, suffix)

        except Exception as e:
            logger.error("Failed to update CVD chart symbol: %s", e, exc_info=True)

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
