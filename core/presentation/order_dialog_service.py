import logging
from datetime import date
from typing import Dict, List

from PySide6.QtWidgets import QMessageBox

from dialogs.order_history_dialog import OrderHistoryDialog
from dialogs.pending_orders_dialog import PendingOrdersDialog
from widgets.order_status_widget import OrderStatusWidget

logger = logging.getLogger(__name__)


class OrderDialogService:
    """Presentation orchestration for order and history dialogs/widgets."""

    def __init__(self, main_window):
        self.main_window = main_window

    def show_order_history_dialog(self):
        w = self.main_window
        if not hasattr(w, "order_history_dialog") or w.order_history_dialog is None:
            w.order_history_dialog = OrderHistoryDialog(w)
            w.order_history_dialog.refresh_requested.connect(self.refresh_order_history_from_ledger)
        self.refresh_order_history_from_ledger()
        w.order_history_dialog.show()
        w.order_history_dialog.activateWindow()

    def refresh_order_history_from_ledger(self):
        w = self.main_window
        if w.order_history_dialog is None:
            return
        trades = w.trade_ledger.get_trades_for_date(date.today().isoformat())
        w.order_history_dialog.update_trades(trades)

    def show_pending_orders_dialog(self):
        w = self.main_window
        if w.pending_orders_dialog is None:
            w.pending_orders_dialog = PendingOrdersDialog(w)
            w.position_manager.pending_orders_updated.connect(w.pending_orders_dialog.update_orders)
        w.pending_orders_dialog.update_orders(w.position_manager.get_pending_orders())
        w.pending_orders_dialog.show()
        w.pending_orders_dialog.activateWindow()

    def update_pending_order_widgets(self, pending_orders: List[Dict]):
        w = self.main_window
        spacing = 12
        edge_margin = 16
        current_order_ids = {order['order_id'] for order in pending_orders}
        existing_widget_ids = set(w.pending_order_widgets.keys())

        for order_id in existing_widget_ids - current_order_ids:
            widget = w.pending_order_widgets.pop(order_id)
            widget.close_widget()

        widgets_in_order = []
        for order_data in pending_orders:
            order_id = order_data['order_id']
            if order_id not in w.pending_order_widgets:
                widget = OrderStatusWidget(order_data, w)
                widget.cancel_requested.connect(self.cancel_order_by_id)
                widget.modify_requested.connect(w._show_modify_order_dialog)
                w.pending_order_widgets[order_id] = widget

            widgets_in_order.append(w.pending_order_widgets[order_id])

        if widgets_in_order:
            screen_geometry = w.screen().availableGeometry()
            anchor_widget = widgets_in_order[0]
            bottom_gap = max(anchor_widget.height() // 2, 24)

            y_pos = screen_geometry.bottom() - bottom_gap - anchor_widget.height()
            for widget in widgets_in_order:
                x_pos = screen_geometry.right() - widget.width() - edge_margin
                widget.move(x_pos, y_pos)
                y_pos -= widget.height() + spacing

        if pending_orders and not w.pending_order_refresh_timer.isActive():
            logger.info("Pending orders detected. Starting 1-second position refresh timer.")
            w.pending_order_refresh_timer.start()
        elif not pending_orders and w.pending_order_refresh_timer.isActive():
            logger.info("No more pending orders. Stopping refresh timer.")
            w.pending_order_refresh_timer.stop()

    def cancel_order_by_id(self, order_id: str):
        w = self.main_window
        try:
            w.trader.cancel_order(w.trader.VARIETY_REGULAR, order_id)
            logger.info("Cancellation request sent for order ID: %s", order_id)
            w.position_manager.refresh_from_api()
        except Exception as e:
            logger.error("Failed to cancel order %s: %s", order_id, e)
            QMessageBox.critical(w, "Cancel Failed", f"Could not cancel order {order_id}:\n{e}")
