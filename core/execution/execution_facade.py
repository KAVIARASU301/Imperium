import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ExecutionFacade:
    """UI-independent orchestration for order placement and paper execution callbacks."""

    def __init__(
        self,
        *,
        get_instrument_data: Callable[[], dict],
        get_settings: Callable[[], dict],
        get_active_order_confirmation_dialog: Callable[[], object],
        set_active_order_confirmation_dialog: Callable[[Optional[object]], None],
        create_order_confirmation_dialog: Callable[[dict], object],
        warning_user: Callable[[str, str], None],
        execute_orders: Callable[[dict], None],
        get_position: Callable[[str], object],
        record_completed_exit_trade: Callable[[dict, object, str], None],
        update_account_info: Callable[[], None],
        update_account_summary_widget: Callable[[], None],
        refresh_positions: Callable[[], None],
        publish_status: Callable[[str, int, str], None],
    ):
        self._get_instrument_data = get_instrument_data
        self._get_settings = get_settings
        self._get_active_order_confirmation_dialog = get_active_order_confirmation_dialog
        self._set_active_order_confirmation_dialog = set_active_order_confirmation_dialog
        self._create_order_confirmation_dialog = create_order_confirmation_dialog
        self._warning_user = warning_user
        self._execute_orders = execute_orders
        self._get_position = get_position
        self._record_completed_exit_trade = record_completed_exit_trade
        self._update_account_info = update_account_info
        self._update_account_summary_widget = update_account_summary_widget
        self._refresh_positions = refresh_positions
        self._publish_status = publish_status

    def place_order(self, *, order_details_from_panel: dict, auto_confirm: bool) -> bool:
        """Validate/place order through confirmation flow; returns whether execution was triggered."""
        if not order_details_from_panel.get("strikes"):
            self._warning_user("Error", "No valid strikes found for the order.")
            logger.warning("place_order called with no strikes in details.")
            return False

        active_dialog = self._get_active_order_confirmation_dialog()
        if active_dialog:
            active_dialog.reject()

        order_details_for_dialog = order_details_from_panel.copy()

        symbol = order_details_for_dialog.get("symbol")
        instrument_data = self._get_instrument_data()
        if not symbol or symbol not in instrument_data:
            self._warning_user("Error", "Symbol data not found.")
            return False

        instrument_lot_quantity = instrument_data[symbol].get("lot_size", 1)
        num_lots = order_details_for_dialog.get("lot_size", 1)
        order_details_for_dialog["total_quantity_per_strike"] = num_lots * instrument_lot_quantity
        order_details_for_dialog["product"] = self._get_settings().get("default_product", "MIS")
        order_details_for_dialog["order_type"] = str(order_details_from_panel.get("order_type") or "MARKET").upper()
        order_details_for_dialog["stop_loss_price"] = order_details_from_panel.get("stop_loss_price")
        order_details_for_dialog["target_price"] = order_details_from_panel.get("target_price")
        order_details_for_dialog["trailing_stop_loss"] = order_details_from_panel.get("trailing_stop_loss")

        dialog = self._create_order_confirmation_dialog(order_details_for_dialog)
        self._set_active_order_confirmation_dialog(dialog)

        if auto_confirm:
            logger.info("[AUTO] Auto-confirming Buy/Exit panel order for %s", symbol)
            self._execute_orders(order_details_for_dialog)
            return True

        if dialog.exec() == dialog.DialogCode.Accepted:
            self._execute_orders(order_details_for_dialog)
            return True

        return False

    def on_paper_trade_update(self, *, order_data: dict, processed_order_ids: set) -> None:
        """Track completed paper trades and trigger follow-up updates."""
        order_id = order_data.get("order_id")
        if order_id in processed_order_ids:
            return

        processed_order_ids.add(order_id)

        if order_data and order_data.get("status") == "COMPLETE":
            tradingsymbol = order_data.get("tradingsymbol")
            exit_qty = order_data.get("exit_qty", 0)

            if exit_qty > 0:
                if order_data.get("_ledger_recorded"):
                    return

                original_position = self._get_position(tradingsymbol)
                if original_position:
                    confirmed_order = {**order_data, "filled_quantity": exit_qty}
                    self._record_completed_exit_trade(
                        confirmed_order=confirmed_order,
                        original_position=original_position,
                        trading_mode="PAPER",
                    )
                    order_data["_ledger_recorded"] = True
                return

            logger.debug("Paper trade complete, triggering immediate account info refresh.")
            self._update_account_info()
            self._update_account_summary_widget()
            self._refresh_positions()

    def on_paper_order_rejected(self, *, data: dict, show_modal: Callable[[str, str], None]) -> None:
        reason = data.get("reason", "Order rejected by RMS")
        symbol = data.get("tradingsymbol", "")
        qty = data.get("quantity", 0)

        message = f"❌ PAPER RMS REJECTED\n{symbol} × {qty}\n\n{reason}"

        self._publish_status(message, 7000, "error")
        show_modal("Paper RMS Rejection", message)

        logger.warning("Paper RMS rejection shown to user: %s", reason)
