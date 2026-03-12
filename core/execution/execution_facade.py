import logging
from copy import copy
from datetime import datetime
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
        # Symbol -> FIFO list of entry snapshots used to map exits deterministically
        self._paper_entry_ledger: dict[str, list] = {}

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
            exit_qty = int(order_data.get("exit_qty", 0) or 0)
            entry_qty = int(order_data.get("entry_qty", 0) or 0)

            if entry_qty > 0:
                self._capture_paper_entry_snapshot(order_data=order_data, qty=entry_qty)

            if exit_qty > 0:
                if order_data.get("_ledger_recorded"):
                    return

                confirmed_order = {**order_data, "filled_quantity": exit_qty}
                matched_entries = self._consume_paper_entry_snapshots(order_data=order_data, exit_qty=exit_qty)

                for entry in matched_entries:
                    self._record_completed_exit_trade(
                        confirmed_order={**confirmed_order, "filled_quantity": abs(int(entry.quantity))},
                        original_position=entry,
                        trading_mode="PAPER",
                    )

                if matched_entries:
                    order_data["_ledger_recorded"] = True
                    return

                original_position = self._get_position(tradingsymbol)
                if original_position:
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

    def _capture_paper_entry_snapshot(self, *, order_data: dict, qty: int) -> None:
        tradingsymbol = str(order_data.get("tradingsymbol") or "").strip()
        if not tradingsymbol or qty <= 0:
            return

        current_position = self._get_position(tradingsymbol)
        if not current_position:
            return

        txn = str(order_data.get("transaction_type") or "").upper()
        signed_qty = qty if txn == "BUY" else -qty

        entry_time_raw = (
            order_data.get("exchange_timestamp")
            or order_data.get("order_timestamp")
            or datetime.now().isoformat()
        )
        try:
            entry_time = datetime.fromisoformat(str(entry_time_raw).replace("Z", "+00:00"))
        except Exception:
            entry_time = datetime.now()

        snapshot = copy(current_position)
        snapshot.quantity = signed_qty
        snapshot.average_price = float(order_data.get("average_price") or current_position.average_price)
        snapshot.order_id = order_data.get("order_id") or current_position.order_id
        snapshot.entry_time = entry_time

        self._paper_entry_ledger.setdefault(tradingsymbol, []).append(snapshot)

    def _consume_paper_entry_snapshots(self, *, order_data: dict, exit_qty: int) -> list:
        tradingsymbol = str(order_data.get("tradingsymbol") or "").strip()
        if not tradingsymbol or exit_qty <= 0:
            return []

        entries = self._paper_entry_ledger.get(tradingsymbol, [])
        if not entries:
            return []

        exit_txn = str(order_data.get("transaction_type") or "").upper()
        expected_sign = 1 if exit_txn == "SELL" else -1

        remaining = exit_qty
        matched: list = []
        idx = 0

        while idx < len(entries) and remaining > 0:
            entry = entries[idx]
            entry_sign = 1 if int(entry.quantity) > 0 else -1
            if entry_sign != expected_sign:
                idx += 1
                continue

            available_qty = abs(int(entry.quantity))
            take_qty = min(available_qty, remaining)
            consumed = copy(entry)
            consumed.quantity = expected_sign * take_qty
            matched.append(consumed)

            remaining -= take_qty
            if take_qty == available_qty:
                entries.pop(idx)
            else:
                entry.quantity = expected_sign * (available_qty - take_qty)
                idx += 1

        if not entries:
            self._paper_entry_ledger.pop(tradingsymbol, None)

        return matched

    def on_paper_order_rejected(self, *, data: dict, show_modal: Callable[[str, str], None]) -> None:
        reason = data.get("reason", "Order rejected by RMS")
        symbol = data.get("tradingsymbol", "")
        qty = data.get("quantity", 0)

        message = f"❌ PAPER RMS REJECTED\n{symbol} × {qty}\n\n{reason}"

        self._publish_status(message, 7000, "error")
        show_modal("Paper RMS Rejection", message)

        logger.warning("Paper RMS rejection shown to user: %s", reason)
