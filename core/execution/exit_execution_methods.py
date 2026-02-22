import logging
import time
from typing import List

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from core.execution.paper_trading_manager import PaperTradingManager
from utils.data_models import OptionType, Position

logger = logging.getLogger(__name__)


class ExitExecutionMethods:
    """Encapsulates position exit and bulk-exit flows."""

    def __init__(self, window):
        self.window = window

    def exit_all_positions(self):
        all_positions = self.window.position_manager.get_all_positions()
        positions_to_exit = [p for p in all_positions if p.quantity != 0]

        if not positions_to_exit:
            QMessageBox.information(self.window, "No Positions", "No open positions to exit.")
            return

        total_pnl_all = sum(p.pnl for p in positions_to_exit)
        reply = QMessageBox.question(
            self.window,
            "Confirm Exit All Positions",
            f"Are you sure you want to exit ALL {len(positions_to_exit)} open positions?\n\n"
            f"Total P&L for all positions: ₹{total_pnl_all:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.execute_bulk_exit(positions_to_exit)

    def execute_bulk_exit(self, positions_list: List[Position]):
        if not positions_list:
            return

        positions_to_exit = [p for p in positions_list if p.quantity != 0 and not p.is_exiting]

        if not positions_to_exit:
            self.window._publish_status("No valid positions to exit.", 2500, level="warning")
            return

        self.window._publish_status(f"Exiting {len(positions_to_exit)} positions...", 2500, level="action")

        for pos in positions_to_exit:
            try:
                pos.is_exiting = True

                if self.window.trading_mode == "live":
                    self.window._position_snapshots_for_exit[pos.tradingsymbol] = pos
                    logger.debug("Cached position snapshot for %s (Bulk exit)", pos.tradingsymbol)

                transaction_type = (
                    self.window.trader.TRANSACTION_TYPE_SELL
                    if pos.quantity > 0
                    else self.window.trader.TRANSACTION_TYPE_BUY
                )

                order_id = self.window.trader.place_order(
                    variety=self.window.trader.VARIETY_REGULAR,
                    exchange=pos.exchange,
                    tradingsymbol=pos.tradingsymbol,
                    transaction_type=transaction_type,
                    quantity=abs(pos.quantity),
                    product=pos.product,
                    order_type=self.window.trader.ORDER_TYPE_MARKET,
                )

                if not order_id:
                    pos.is_exiting = False
                    logger.error("Bulk exit failed for %s", pos.tradingsymbol)
                else:
                    logger.info(
                        "Bulk exit order placed for %s (Qty: %s) → %s",
                        pos.tradingsymbol,
                        abs(pos.quantity),
                        order_id,
                    )

            except Exception as exc:
                pos.is_exiting = False
                logger.error("Bulk exit initiation failed for %s: %s", pos.tradingsymbol, exc, exc_info=True)

        QTimer.singleShot(1500, self.finalize_bulk_exit_result)

    def finalize_bulk_exit_result(self):
        remaining_positions = [
            p for p in self.window.position_manager.get_all_positions() if p.quantity != 0 and not p.is_exiting
        ]

        if not remaining_positions:
            self.window._publish_status("All positions exited successfully.", 5000, level="success")
            self.window._refresh_positions()
            self.window._play_sound(success=True)
            logger.info("Bulk exit completed successfully — no open positions remaining.")
            return

        symbols = ", ".join(p.tradingsymbol for p in remaining_positions[:5])
        QMessageBox.warning(
            self.window,
            "Partial Exit",
            (
                "Some positions are still open:\n\n"
                f"{symbols}\n\n"
                "Please review them manually."
            ),
        )

        self.window._play_sound(success=False)
        self.window._refresh_positions()
        logger.warning("Bulk exit incomplete — remaining positions: %s", symbols)

    def exit_position(self, position_data_to_exit: dict):
        tradingsymbol = position_data_to_exit.get("tradingsymbol")
        current_quantity = position_data_to_exit.get("quantity", 0)
        entry_price = position_data_to_exit.get("average_price", 0.0)
        pnl = position_data_to_exit.get("pnl", 0.0)
        exchange = position_data_to_exit.get("exchange", "NFO")
        product = position_data_to_exit.get("product", "MIS")

        if not tradingsymbol or current_quantity == 0:
            QMessageBox.warning(
                self.window,
                "Exit Failed",
                "Invalid position data for exit (missing symbol or zero quantity).",
            )
            logger.warning("Invalid exit request: %s", position_data_to_exit)
            return

        exit_quantity = abs(current_quantity)
        original_position = self.window.position_manager.get_position(tradingsymbol)
        if not original_position:
            QMessageBox.warning(
                self.window,
                "Exit Failed",
                f"Position {tradingsymbol} not found. It may have already been exited.",
            )
            logger.warning("Exit aborted — position not found: %s", tradingsymbol)
            return

        reply = QMessageBox.question(
            self.window,
            "Confirm Exit Position",
            f"Are you sure you want to exit the position for {tradingsymbol}?\n\n"
            f"Quantity: {exit_quantity}\n"
            f"Current P&L: ₹{pnl:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.window._publish_status(f"Exiting position {tradingsymbol}...", 2000, level="action")

        try:
            transaction_type = (
                self.window.trader.TRANSACTION_TYPE_SELL
                if current_quantity > 0
                else self.window.trader.TRANSACTION_TYPE_BUY
            )

            order_id = self.window.trader.place_order(
                variety=self.window.trader.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=exit_quantity,
                product=product,
                order_type=self.window.trader.ORDER_TYPE_MARKET,
            )

            logger.info("Exit order placed for %s (Qty: %s) | Order ID: %s", tradingsymbol, exit_quantity, order_id)

            if self.window.trading_mode == "live":
                self.window._position_snapshots_for_exit[tradingsymbol] = original_position
                logger.debug("Cached position snapshot for %s (Live exit)", tradingsymbol)

            if isinstance(self.window.trader, PaperTradingManager):
                self.window._play_sound(success=True)
                return

            time.sleep(0.5)
            confirmed_order = self.window._confirm_order_success(order_id)

            if confirmed_order and confirmed_order.get("status") == "COMPLETE":
                exit_price = confirmed_order.get("average_price", 0.0)
                filled_qty = confirmed_order.get("filled_quantity", exit_quantity)

                if current_quantity > 0:
                    realized_pnl = (exit_price - entry_price) * filled_qty
                else:
                    realized_pnl = (entry_price - exit_price) * filled_qty

                self.window._publish_status(
                    f"Exit confirmed for {tradingsymbol}. Realized P&L: ₹{realized_pnl:,.2f}",
                    5000,
                    level="success",
                )
                self.window._play_sound(success=True)
            else:
                logger.warning(
                    "Exit order %s for %s placed but confirmation pending or failed.",
                    order_id,
                    tradingsymbol,
                )
                self.window._publish_status(
                    f"Exit order {order_id} placed for {tradingsymbol}; confirmation pending.",
                    5000,
                    level="warning",
                )
                self.window._play_sound(success=False)

        except Exception as exc:
            logger.error("Failed to exit position %s: %s", tradingsymbol, exc, exc_info=True)
            QMessageBox.critical(
                self.window,
                "Exit Order Failed",
                f"Failed to place exit order for {tradingsymbol}:\n{exc}",
            )
            self.window._play_sound(success=False)
        finally:
            self.window._refresh_positions()

    def exit_position_from_dialog(self, symbol_or_pos_data):
        position_to_exit_data = None
        if isinstance(symbol_or_pos_data, str):
            position_obj = self.window.position_manager.get_position(symbol_or_pos_data)
            if position_obj:
                position_to_exit_data = self.window._position_to_dict(position_obj)
            else:
                logger.warning("Cannot exit: Position %s not found in PositionManager.", symbol_or_pos_data)
                QMessageBox.warning(self.window, "Exit Error", f"Position {symbol_or_pos_data} not found.")
                return
        elif isinstance(symbol_or_pos_data, dict):
            position_to_exit_data = symbol_or_pos_data
        else:
            logger.error("Invalid data type for exiting position: %s", type(symbol_or_pos_data))
            return

        if position_to_exit_data:
            self.exit_position(position_to_exit_data)
        else:
            logger.warning("Could not prepare position data for exit from dialog signal.")

    def exit_option_positions(self, option_type: OptionType):
        positions_to_exit = [
            pos
            for pos in self.window.position_manager.get_all_positions()
            if hasattr(pos, "contract")
            and pos.contract
            and hasattr(pos.contract, "option_type")
            and pos.contract.option_type == option_type.value
        ]
        if not positions_to_exit:
            QMessageBox.information(
                self.window,
                "No Positions",
                f"No open {option_type.name} positions to exit.",
            )
            return

        total_pnl_of_selection = sum(p.pnl for p in positions_to_exit)
        reply = QMessageBox.question(
            self.window,
            f"Exit All {option_type.name} Positions",
            f"Are you sure you want to exit all {len(positions_to_exit)} {option_type.name} positions?\n\n"
            f"Approximate P&L for these positions: ₹{total_pnl_of_selection:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.execute_bulk_exit(positions_to_exit)
