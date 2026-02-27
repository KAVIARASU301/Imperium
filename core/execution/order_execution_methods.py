import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from utils.data_models import Contract, Position
from core.execution.execution_stack import ExecutionRequest
from core.execution.paper_trading_manager import PaperTradingManager
from utils.pricing_utils import calculate_smart_limit_price

logger = logging.getLogger(__name__)


class OrderExecutionMethods:
    """Extracted order execution operations used by the main window."""

    def __init__(self, window):
        self.window = window

    def execute_orders(self, confirmed_order_details: dict):
        successful_orders_info = []
        failed_orders_info = []
        order_product = confirmed_order_details.get('product', self.window.trader.PRODUCT_MIS)
        total_quantity_per_strike = confirmed_order_details.get('total_quantity_per_strike', 0)
        trade_status = str(confirmed_order_details.get("trade_status") or "MANUAL").upper()
        strategy_name = str(confirmed_order_details.get("strategy_name") or "N/A")
        order_type = str(
            confirmed_order_details.get("order_type")
            or self.window.trader.ORDER_TYPE_MARKET
        ).upper()

        if total_quantity_per_strike == 0:
            logger.error("Total quantity per strike is zero in confirmed_order_details.")
            QMessageBox.critical(self.window, "Order Error", "Order quantity is zero. Cannot place order.")
            return

        for strike_detail in confirmed_order_details.get('strikes', []):
            contract = strike_detail.get('contract')
            ok, reason = self.window._validate_pre_trade_risk(
                transaction_type=self.window.trader.TRANSACTION_TYPE_BUY,
                quantity=total_quantity_per_strike,
                tradingsymbol=getattr(contract, 'tradingsymbol', None),
            )
            if not ok:
                self.window._reject_order_for_risk(reason)
                return

        self.window._publish_status("Placing orders...", 2000, level="action")
        for strike_detail in confirmed_order_details.get('strikes', []):
            contract_to_trade: Optional[Contract] = strike_detail.get('contract')
            if not contract_to_trade or not contract_to_trade.tradingsymbol:
                logger.warning(f"Missing contract or tradingsymbol for strike {strike_detail.get('strike')}. Skipping.")
                failed_orders_info.append(
                    {'symbol': f"Strike {strike_detail.get('strike')}", 'error': "Missing contract data"})
                continue
            try:
                order_args = {
                    'variety': self.window.trader.VARIETY_REGULAR,
                    'exchange': self.window.trader.EXCHANGE_NFO,
                    'tradingsymbol': contract_to_trade.tradingsymbol,
                    'transaction_type': self.window.trader.TRANSACTION_TYPE_BUY,
                    'quantity': total_quantity_per_strike,
                    'product': order_product,
                    'order_type': order_type,
                }
                limit_price = None
                if order_type == self.window.trader.ORDER_TYPE_LIMIT:
                    limit_price = float(calculate_smart_limit_price(contract_to_trade))
                    order_args['price'] = limit_price
                execution_request = ExecutionRequest(
                    tradingsymbol=contract_to_trade.tradingsymbol,
                    transaction_type=self.window.trader.TRANSACTION_TYPE_BUY,
                    quantity=int(total_quantity_per_strike),
                    order_type=order_type,
                    product=order_product,
                    ltp=float(getattr(contract_to_trade, 'ltp', 0.0) or 0.0),
                    bid=float(getattr(contract_to_trade, 'bid', 0.0) or 0.0),
                    ask=float(getattr(contract_to_trade, 'ask', 0.0) or 0.0),
                    limit_price=limit_price,
                    urgency=str(confirmed_order_details.get('execution_urgency') or 'normal'),
                    participation_rate=float(confirmed_order_details.get('participation_rate') or 0.15),
                    execution_algo=str(confirmed_order_details.get('execution_algo') or 'IMMEDIATE'),
                    max_child_orders=int(confirmed_order_details.get('max_child_orders') or 1),
                    randomize_slices=bool(confirmed_order_details.get('randomize_slices', True)),
                    metadata={'source': 'buy_exit_panel'},
                )
                placed_order_ids = self.window.execution_stack.execute(
                    request=execution_request,
                    place_order_fn=self.window.trader.place_order,
                    base_order_args=order_args,
                )

                logger.info(
                    "Execution stack placed %s child order(s) for panel order %s, Qty: %s",
                    len(placed_order_ids),
                    contract_to_trade.tradingsymbol,
                    total_quantity_per_strike,
                )
                if isinstance(self.window.trader, PaperTradingManager):
                    successful_orders_info.append(
                        {'order_id': placed_order_ids[-1], 'symbol': contract_to_trade.tradingsymbol,
                         'quantity': total_quantity_per_strike,
                         'price': limit_price if limit_price is not None else contract_to_trade.ltp})
                    continue

                for order_id in placed_order_ids:
                    time.sleep(0.5)
                    confirmed_order_api_data = self.window._confirm_order_success(order_id)
                    if confirmed_order_api_data:
                        order_status = confirmed_order_api_data.get('status')
                        if order_status in ['OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED']:
                            logger.info(f"Order {order_id} is pending with status: {order_status}. Triggering refresh.")
                            self.window._refresh_positions()
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
                                exchange=self.window.trader.EXCHANGE_NFO,
                                product=order_product,
                                stop_loss_price=confirmed_order_details.get("stop_loss_price"),
                                target_price=confirmed_order_details.get("target_price"),
                                trailing_stop_loss=tsl if tsl > 0 else None,
                                entry_time=datetime.now(),
                                trade_status=trade_status,
                                strategy_name=strategy_name,
                            )

                            self.window.position_manager.add_position(new_position)
                            self.window.trade_logger.log_trade(confirmed_order_api_data)
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

        self.window._refresh_positions()
        self.window._play_sound(success=not failed_orders_info)
        self.show_order_results(successful_orders_info, failed_orders_info)
        self.window._publish_status("Order placement flow completed.", 3000, level="info")

    def show_order_results(self, successful_list: List[Dict], failed_list: List[Dict]):
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

        QMessageBox.warning(self.window, "Order Placement Issue", msg)

    def execute_single_strike_order(self, order_params: dict):
        contract_to_trade: Contract = order_params.get('contract')
        quantity = order_params.get('quantity')
        price = order_params.get('price')
        order_type = order_params.get('order_type', self.window.trader.ORDER_TYPE_MARKET)
        product = order_params.get('product', self.window.settings.get('default_product', self.window.trader.PRODUCT_MIS))
        transaction_type = order_params.get('transaction_type', self.window.trader.TRANSACTION_TYPE_BUY)
        stop_loss_price = order_params.get('stop_loss_price')
        target_price = order_params.get('target_price')
        trailing_stop_loss = order_params.get('trailing_stop_loss')
        stop_loss_amount = float(order_params.get('stop_loss_amount') or 0)
        target_amount = float(order_params.get('target_amount') or 0)
        trailing_stop_loss_amount = float(order_params.get('trailing_stop_loss_amount') or 0)
        group_name = order_params.get('group_name')
        auto_token = order_params.get('auto_token')
        trade_status = str(order_params.get("trade_status") or ("ALGO" if auto_token is not None else "MANUAL")).upper()
        strategy_name = str(order_params.get("strategy_name") or "N/A")

        if group_name and contract_to_trade:
            self.window.position_manager.set_group_name_hint(contract_to_trade.tradingsymbol, group_name)

        if not contract_to_trade or not quantity:
            logger.error("Invalid parameters for single strike order.")
            QMessageBox.critical(self.window, "Order Error", "Missing contract or quantity for the order.")
            return

        ok, reason = self.window._validate_pre_trade_risk(
            transaction_type=transaction_type,
            quantity=quantity,
            tradingsymbol=getattr(contract_to_trade, 'tradingsymbol', None),
        )
        if not ok:
            self.window._reject_order_for_risk(reason)
            return

        try:
            order_args = {
                'variety': self.window.trader.VARIETY_REGULAR,
                'exchange': self.window.trader.EXCHANGE_NFO,
                'tradingsymbol': contract_to_trade.tradingsymbol,
                'transaction_type': transaction_type,
                'quantity': quantity,
                'product': product,
                'order_type': order_type,
            }
            if isinstance(self.window.trader, PaperTradingManager):
                order_args['group_name'] = group_name
            if order_type == self.window.trader.ORDER_TYPE_LIMIT and price is not None:
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
            placed_order_ids = self.window.execution_stack.execute(
                request=execution_request,
                place_order_fn=self.window.trader.place_order,
                base_order_args=order_args,
            )
            order_id = placed_order_ids[-1]
            logger.info(
                "Execution stack placed %s child order(s) for %s. Last order id: %s",
                len(placed_order_ids),
                contract_to_trade.tradingsymbol,
                order_id,
            )

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
                    anchored_sl = avg_fill_price - sl_per_unit if is_buy_position else avg_fill_price + sl_per_unit

                if target_amount > 0:
                    tp_per_unit = target_amount / qty
                    anchored_tp = avg_fill_price + tp_per_unit if is_buy_position else avg_fill_price - tp_per_unit

                if trailing_stop_loss_amount > 0:
                    anchored_tsl = trailing_stop_loss_amount / qty

                return anchored_sl, anchored_tp, anchored_tsl

            def _apply_risk_after_fill():
                position = self.window.position_manager.get_position(contract_to_trade.tradingsymbol)
                if not position:
                    logger.warning(
                        "Position not yet available for risk application: %s",
                        contract_to_trade.tradingsymbol,
                    )
                    return

                anchored_sl, anchored_tp, anchored_tsl = _build_fill_anchored_risk_values(position)
                self.window.position_manager.update_sl_tp_for_position(
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

            if isinstance(self.window.trader, PaperTradingManager):
                QTimer.singleShot(500, self.window._refresh_positions)
                QTimer.singleShot(1000, _apply_risk_after_fill)
                self.window._play_sound(success=True)
                return

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
                           at=auto_token, ts=trade_status, sn=strategy_name:
                    self.confirm_and_finalize_order(oid, c, qty, p, tt, prod, sl, tp, tsl,
                                                    sl_amt, tp_amt, tsl_amt, gn, at,
                                                    trade_status=ts, strategy_name=sn)
                )

        except Exception as e:
            self.window._play_sound(success=False)
            logger.error(f"Single strike order execution failed for {contract_to_trade.tradingsymbol}: {e}",
                         exc_info=True)
            self.window._handle_order_error(e, order_params)
            self.show_order_results([], [{'symbol': contract_to_trade.tradingsymbol, 'error': str(e)}])

    def confirm_and_finalize_order(
        self, order_id, contract_to_trade, quantity, price,
        transaction_type, product, stop_loss_price, target_price,
        trailing_stop_loss, stop_loss_amount, target_amount,
        trailing_stop_loss_amount, group_name, auto_token=None,
        trade_status=None, strategy_name=None,
    ):
        self.window._refresh_positions()
        confirmed_order_api_data = self.window._confirm_order_success(order_id)
        if confirmed_order_api_data:
            order_status = confirmed_order_api_data.get('status')
            if order_status in ['OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED']:
                if auto_token is not None:
                    self.start_cvd_pending_retry(auto_token)
                self.window._play_sound(success=True)
                return

            if order_status == 'COMPLETE':
                avg_price_from_order = confirmed_order_api_data.get('average_price', price if price else contract_to_trade.ltp)
                filled_quantity = confirmed_order_api_data.get('filled_quantity', quantity)

                if transaction_type == self.window.trader.TRANSACTION_TYPE_BUY:
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

                    resolved_trade_status = str(
                        trade_status or ("ALGO" if auto_token is not None else "MANUAL")
                    ).upper()
                    resolved_strategy_name = str(strategy_name or "N/A")

                    new_position = Position(
                        symbol=f"{contract_to_trade.symbol}{contract_to_trade.strike}{contract_to_trade.option_type}",
                        tradingsymbol=contract_to_trade.tradingsymbol,
                        quantity=filled_quantity,
                        average_price=avg_price_from_order,
                        ltp=avg_price_from_order,
                        pnl=0,
                        contract=contract_to_trade,
                        order_id=order_id,
                        exchange=self.window.trader.EXCHANGE_NFO,
                        product=product,
                        stop_loss_price=stop_loss_price,
                        target_price=target_price,
                        trailing_stop_loss=trailing_stop_loss if trailing_stop_loss and trailing_stop_loss > 0 else None,
                        group_name=group_name,
                        entry_time=datetime.now(),
                        trade_status=resolved_trade_status,
                        strategy_name=resolved_strategy_name,
                    )
                    self.window.position_manager.add_position(new_position)
                    self.window.trade_logger.log_trade(confirmed_order_api_data)
                    action_msg = "bought"
                else:
                    action_msg = "sold"

                self.window._play_sound(success=True)
                if auto_token is not None:
                    self.stop_cvd_pending_retry(auto_token)
                self.window._publish_status(
                    f"Order {order_id} {action_msg} {filled_quantity} {contract_to_trade.tradingsymbol} @ {avg_price_from_order:.2f}.",
                    5000,
                    level="success")
                self.show_order_results(
                    [{'order_id': order_id, 'symbol': contract_to_trade.tradingsymbol}], [])
        else:
            self.window._play_sound(success=False)
            if auto_token is not None:
                self.start_cvd_pending_retry(auto_token)
            logger.warning(
                f"Single strike order {order_id} for {contract_to_trade.tradingsymbol} failed or not confirmed.")
            self.show_order_results(
                [], [{'symbol': contract_to_trade.tradingsymbol,
                      'error': "Order rejected or status not confirmed"}])

    def _is_retry_window_open(self, active_trade: dict) -> bool:
        strategy_type = str(active_trade.get("strategy_type") or "").lower()
        if strategy_type != "open_drive":
            return True

        signal_timestamp = active_trade.get("signal_timestamp")
        if not signal_timestamp:
            return False

        try:
            parsed_ts = datetime.fromisoformat(str(signal_timestamp).replace("Z", "+00:00"))
        except Exception:
            return False

        return (datetime.now(parsed_ts.tzinfo) - parsed_ts) <= timedelta(minutes=3)

    def has_pending_order_for_symbol(self, tradingsymbol: str | None) -> bool:
        if not tradingsymbol:
            return False

        pending_orders = self.window.position_manager.get_pending_orders() or []
        return any(
            order.get("tradingsymbol") == tradingsymbol
            and order.get("status") in {'OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'}
            for order in pending_orders
        )

    def start_cvd_pending_retry(self, token: int):
        if isinstance(self.window.trader, PaperTradingManager):
            return

        active_trade = self.window._cvd_automation_positions.get(token)
        if not active_trade:
            self.stop_cvd_pending_retry(token)
            return

        if active_trade.get("pending_retry_disabled"):
            return

        if not self._is_retry_window_open(active_trade):
            active_trade["pending_retry_disabled"] = True
            logger.info("[AUTO] Skipping pending-order retry outside window for token=%s", token)
            self.stop_cvd_pending_retry(token)
            return

        timer = self.window._cvd_pending_retry_timers.get(token)
        if timer and timer.isActive():
            return

        if timer is None:
            timer = QTimer(self.window)
            timer.setInterval(10_000)
            timer.timeout.connect(lambda t=token: self.retry_cvd_pending_order(t))
            self.window._cvd_pending_retry_timers[token] = timer

        logger.info("[AUTO] Started 10s pending-order retry for token=%s", token)
        timer.start()

    def stop_cvd_pending_retry(self, token: int):
        timer = self.window._cvd_pending_retry_timers.pop(token, None)
        if timer:
            timer.stop()
            timer.deleteLater()
            logger.info("[AUTO] Stopped pending-order retry for token=%s", token)

    def retry_cvd_pending_order(self, token: int):
        active_trade = self.window._cvd_automation_positions.get(token)
        if not active_trade:
            self.stop_cvd_pending_retry(token)
            return

        if active_trade.get("pending_retry_disabled"):
            self.stop_cvd_pending_retry(token)
            return

        attempts = int(active_trade.get("pending_retry_attempts") or 0)
        if attempts >= 6:
            active_trade["pending_retry_disabled"] = True
            self.stop_cvd_pending_retry(token)
            logger.warning("[AUTO] Pending retry limit reached for token=%s; stopping retries", token)
            return

        if not self._is_retry_window_open(active_trade):
            active_trade["pending_retry_disabled"] = True
            self.stop_cvd_pending_retry(token)
            logger.info("[AUTO] Pending retry window expired for token=%s", token)
            return

        tradingsymbol = active_trade.get("tradingsymbol")
        if not tradingsymbol:
            self.stop_cvd_pending_retry(token)
            return

        if self.window.position_manager.get_position(tradingsymbol):
            self.stop_cvd_pending_retry(token)
            return

        self.window._refresh_positions()

        pending_candidates = [
            order for order in (self.window.position_manager.get_pending_orders() or [])
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
                self.window.trader.cancel_order(self.window.trader.VARIETY_REGULAR, pending_order_id)
                logger.info("[AUTO] Cancelled pending order %s for retry", pending_order_id)
        except Exception as exc:
            logger.warning("[AUTO] Could not cancel pending order %s: %s", pending_order_id, exc)
            return

        latest_contract = self.window._get_latest_contract_from_ladder(tradingsymbol)
        if not latest_contract:
            logger.warning("[AUTO] Latest contract unavailable for retry: %s", tradingsymbol)
            return

        retry_price = self.window._calculate_smart_limit_price(latest_contract)
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
            "order_type": self.window.trader.ORDER_TYPE_LIMIT,
            "price": retry_price,
            "product": pending_order.get("product") or active_trade.get("product") or self.window.trader.PRODUCT_MIS,
            "transaction_type": pending_order.get("transaction_type") or active_trade.get(
                "transaction_type") or self.window.trader.TRANSACTION_TYPE_BUY,
            "group_name": active_trade.get("group_name") or f"CVD_AUTO_{token}",
            "auto_token": token,
            "trade_status": "ALGO",
            "strategy_name": active_trade.get("strategy_type") or "N/A",
        }
        logger.info(
            "[AUTO] Replacing pending order for %s every 10s with refreshed LTP %.2f",
            tradingsymbol,
            retry_price,
        )
        active_trade["pending_retry_attempts"] = attempts + 1
        self.execute_single_strike_order(retry_params)

    def execute_strategy_orders(self, order_params_list: List[dict], strategy_name: Optional[str] = None):
        if not order_params_list:
            return

        for order_params in order_params_list:
            side = order_params.get("side", "BUY")
            transaction_type = (
                self.window.trader.TRANSACTION_TYPE_BUY
                if side.upper() == "BUY"
                else self.window.trader.TRANSACTION_TYPE_SELL
            )
            mapped_params = {
                **order_params,
                "transaction_type": transaction_type,
                "order_type": self.window.trader.ORDER_TYPE_MARKET,
                "product": order_params.get("product", self.window.settings.get("default_product", self.window.trader.PRODUCT_MIS)),
                "group_name": strategy_name or order_params.get("group_name"),
            }
            self.execute_single_strike_order(mapped_params)

        if not isinstance(self.window.trader, PaperTradingManager):
            self.window._refresh_positions()
