import logging
from datetime import date
from typing import Optional

from PySide6.QtWidgets import QDialog, QMessageBox

from dialogs.journal_dialog import JournalDialog
from dialogs.market_monitor_dialog import MarketMonitorDialog
from dialogs.order_history_dialog import OrderHistoryDialog
from dialogs.watchlist_dialog import WatchlistDialog

logger = logging.getLogger(__name__)


class RiskController:
    """Centralized runtime risk controls for ImperiumMainWindow."""

    def __init__(self, main_window):
        self.main_window = main_window
        self.reload_limits_from_settings()

    def reload_limits_from_settings(self):
        w = self.main_window
        w._intraday_drawdown_limit = float(max(0.0, w.settings.get("risk_intraday_drawdown_limit", 0.0)))
        w._max_portfolio_loss = float(max(0.0, w.settings.get("risk_max_portfolio_loss", 0.0)))
        w._max_open_positions = int(max(0, w.settings.get("risk_max_open_positions", 0)))
        w._max_gross_open_quantity = int(max(0, w.settings.get("risk_max_gross_open_quantity", 0)))

    def reset_for_new_trading_day(self):
        w = self.main_window
        w.global_kill_switch_active = False
        w.global_kill_switch_reason = ""
        w.intraday_drawdown_lock_active = False
        w._intraday_peak_pnl = 0.0

    def activate_global_kill_switch(self, reason: str, user_message: Optional[str] = None, exit_open_positions: bool = True):
        w = self.main_window
        if w.global_kill_switch_active:
            return

        w.global_kill_switch_active = True
        w.global_kill_switch_reason = reason
        w.intraday_drawdown_lock_active = True

        logger.critical("🚨 GLOBAL KILL SWITCH ACTIVATED | reason=%s", reason)

        for _, state in w._cvd_automation_market_state.items():
            state["enabled"] = False
        w._cvd_automation_positions.clear()
        w._persist_cvd_automation_state()

        if exit_open_positions:
            positions = [p for p in w.position_manager.get_all_positions() if p.quantity != 0]
            if positions:
                w._execute_bulk_exit(positions)

        msg = user_message or f"Global kill switch activated: {reason}."
        w._publish_status(msg, 6000, level="error")
        QMessageBox.critical(w, "Risk Lock Active", msg)

    def evaluate_risk_locks(self):
        w = self.main_window
        stats = w.trade_ledger.get_daily_trade_stats(trading_day=date.today().isoformat())
        realized_pnl = float(stats.get("total_pnl") or 0.0)
        unrealized_pnl = float(w.position_manager.get_total_pnl() or 0.0)
        total_intraday_pnl = realized_pnl + unrealized_pnl

        if total_intraday_pnl > w._intraday_peak_pnl:
            w._intraday_peak_pnl = total_intraday_pnl

        if w._max_portfolio_loss > 0 and total_intraday_pnl <= -w._max_portfolio_loss:
            self.activate_global_kill_switch(
                reason="MAX_PORTFOLIO_LOSS",
                user_message=(
                    f"Max portfolio loss breached: ₹{total_intraday_pnl:,.2f} "
                    f"(limit ₹{-w._max_portfolio_loss:,.2f}). Exiting all and locking entries."
                ),
            )
            return

        if w._intraday_drawdown_limit > 0:
            drawdown = w._intraday_peak_pnl - total_intraday_pnl
            if drawdown >= w._intraday_drawdown_limit:
                self.activate_global_kill_switch(
                    reason="INTRADAY_DRAWDOWN_LOCK",
                    user_message=(
                        f"Intraday drawdown lock triggered: drawdown ₹{drawdown:,.2f} "
                        f"from peak ₹{w._intraday_peak_pnl:,.2f}."
                    ),
                )

    def validate_pre_trade_risk(self, transaction_type: str, quantity: int, tradingsymbol: Optional[str]) -> tuple[bool, str]:
        w = self.main_window
        if transaction_type != w.trader.TRANSACTION_TYPE_BUY:
            return True, ""

        if w.global_kill_switch_active:
            return False, f"Global kill switch is active ({w.global_kill_switch_reason or 'risk lock'})."

        if w._max_open_positions > 0:
            active_symbols = {p.tradingsymbol for p in w.position_manager.get_all_positions() if p.quantity != 0}
            is_new_symbol = tradingsymbol not in active_symbols
            if is_new_symbol and len(active_symbols) >= w._max_open_positions:
                return False, f"Max open positions limit reached ({w._max_open_positions})."

        if w._max_gross_open_quantity > 0:
            current_gross_qty = sum(abs(int(p.quantity)) for p in w.position_manager.get_all_positions() if p.quantity)
            if current_gross_qty + abs(int(quantity or 0)) > w._max_gross_open_quantity:
                return False, (
                    f"Gross quantity limit breached ({w._max_gross_open_quantity}). "
                    f"Current {current_gross_qty}, requested +{abs(int(quantity or 0))}."
                )

        return True, ""

    def reject_order_for_risk(self, reason: str):
        w = self.main_window
        logger.warning("Order blocked by risk control: %s", reason)
        w._publish_status(f"Risk block: {reason}", 5000, level="warning")
        QMessageBox.warning(w, "Risk Control", f"Order blocked by risk controls.\n\n{reason}")


class DialogCoordinator:
    """Coordinates lifecycle of singleton and multi-instance dialogs."""

    def __init__(self, main_window):
        self.main_window = main_window

    def show_order_history_dialog(self):
        w = self.main_window
        if not hasattr(w, 'order_history_dialog') or w.order_history_dialog is None:
            w.order_history_dialog = OrderHistoryDialog(w)
            w.order_history_dialog.refresh_requested.connect(self.refresh_order_history_from_ledger)
        self.refresh_order_history_from_ledger()
        w.order_history_dialog.show()
        w.order_history_dialog.activateWindow()

    def show_journal_dialog(self, enforce_read_time: bool = False):
        w = self.main_window
        if w.journal_dialog is None:
            w.journal_dialog = JournalDialog(
                config_manager=w.config_manager,
                parent=w,
                enforce_read_time=enforce_read_time,
            )
            w.journal_dialog.finished.connect(lambda: setattr(w, 'journal_dialog', None))
        elif enforce_read_time:
            w.journal_dialog._enforce_read_time = True
        w.journal_dialog.show()
        w.journal_dialog.activateWindow()
        w.journal_dialog.raise_()

    def refresh_order_history_from_ledger(self):
        w = self.main_window
        if w.order_history_dialog is None:
            return
        trades = w.trade_ledger.get_trades_for_date(date.today().isoformat())
        w.order_history_dialog.update_trades(trades)

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
            dialog.destroyed.connect(lambda: self.on_market_monitor_closed(dialog))
            dialog.show()
        except Exception as e:
            logger.error(f"Failed to create Market Monitor dialog: {e}", exc_info=True)
            QMessageBox.critical(w, "Error", f"Could not open Market Monitor:\n{e}")

    def show_watchlist_dialog(self):
        w = self.main_window
        if w.watchlist_dialog is None:
            symbols = sorted(w.instrument_data.keys()) if w.instrument_data else []
            w.watchlist_dialog = WatchlistDialog(symbols=symbols, parent=w)
            w.watchlist_dialog.symbol_selected.connect(self.on_watchlist_symbol_selected)
            w.watchlist_dialog.finished.connect(lambda: setattr(w, "watchlist_dialog", None))
        w.watchlist_dialog.show()
        w.watchlist_dialog.raise_()
        w.watchlist_dialog.activateWindow()

    def on_watchlist_symbol_selected(self, symbol: str):
        w = self.main_window
        if symbol and symbol in w.instrument_data:
            w.header.set_active_symbol(symbol)
        else:
            logger.warning("Watchlist selected symbol not available: %s", symbol)

    def on_market_monitor_closed(self, dialog: QDialog):
        w = self.main_window
        if dialog in w.market_monitor_dialogs:
            dialog.unsubscribe_all()
            w.market_monitor_dialogs.remove(dialog)
            logger.info("Closed a Market Monitor window. %s remain open.", len(w.market_monitor_dialogs))


class MarketDataOrchestrator:
    """Coordinates market ticks fanout, UI throttling and subscription deltas."""

    def __init__(self, main_window):
        self.main_window = main_window

    def on_market_data(self, data: list):
        w = self.main_window
        w.cvd_engine.process_ticks(data)
        for tick in data:
            if 'instrument_token' in tick:
                w._latest_market_data[tick['instrument_token']] = tick
        w._ui_update_needed = True

    def update_throttled_ui(self):
        w = self.main_window
        if not w._ui_update_needed:
            return

        ticks_to_process = list(w._latest_market_data.values())
        w.strike_ladder.update_prices(ticks_to_process)
        w.position_manager.update_pnl_from_market_data(ticks_to_process)
        w._update_account_summary_widget()

        if w.positions_dialog and w.positions_dialog.isVisible() and hasattr(w.positions_dialog, 'update_market_data'):
            w.positions_dialog.update_market_data(ticks_to_process)

        for tick in ticks_to_process:
            token = tick.get("instrument_token")
            if not token:
                continue
            current_symbol = w.header.get_current_settings().get("symbol")
            if current_symbol in w.instrument_data:
                index_token = w.instrument_data[current_symbol].get("instrument_token")
                if token == index_token:
                    w.strike_ladder.update_index_price(tick.get("last_price"))

        ladder_data = w.strike_ladder.get_ladder_data()
        if ladder_data:
            w.buy_exit_panel.update_strike_ladder(
                w.strike_ladder.atm_strike,
                w.strike_ladder.get_strike_interval(),
                ladder_data,
            )

        if w.performance_dialog and w.performance_dialog.isVisible():
            w._update_performance()

        w._ui_update_needed = False

    def update_market_subscriptions(self):
        self.main_window.subscription_policy.update_market_subscriptions()

    def on_cvd_market_monitor_closed(self):
        self.update_market_subscriptions()
