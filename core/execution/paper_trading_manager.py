import logging
import json
import os
from datetime import datetime
from typing import Dict, List
from PySide6.QtCore import QObject, QTimer, Signal
from utils.paper_rms import PaperRMS

logger = logging.getLogger(__name__)


class PaperTradingManager(QObject):
    """
    Simulates a trading environment for paper trading. It mimics the key methods
    of the KiteConnect client, using live market data to simulate order execution.
    """
    PRODUCT_MIS = "MIS"
    PRODUCT_NRML = "NRML"
    ORDER_TYPE_MARKET = "MARKET"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_SL = "SL"
    ORDER_TYPE_SLM = "SL-M"
    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    EXCHANGE_NFO = "NFO"
    EXCHANGE_NSE = "NSE"
    VARIETY_REGULAR = "regular"

    order_update = Signal(dict)
    order_rejected = Signal(dict)

    def __init__(self):
        super().__init__()
        self.market_data: Dict[int, Dict] = {}
        self.tradingsymbol_to_token: Dict[str, int] = {}
        self.config_path = os.path.join(os.path.expanduser("~"), ".imperium_desk", "paper_account.json")

        self._positions: Dict[str, Dict] = {}
        self._orders: List[Dict] = []
        self.balance = 1_000_000.0
        self.rms = PaperRMS(starting_balance=self.balance)

        self._load_state()
        # Each position structure:
        # {
        #   tradingsymbol: {
        #       "tradingsymbol": str,
        #       "quantity": int,
        #       "average_price": float,
        #       "last_price": float,
        #       "realized_pnl": float,
        #       "unrealized_pnl": float,
        #       "product": str,
        #       "exchange": str,
        #       "timestamp": str
        #   }
        # }
        self.order_execution_timer = QTimer(self)
        self.order_execution_timer.timeout.connect(self._process_pending_orders)
        self.order_execution_timer.start(1000)

    def set_instrument_data(self, instrument_data: Dict):
        if not instrument_data:
            logger.warning("PaperTradingManager received empty instrument data.")
            return

        for symbol_info in instrument_data.values():
            if 'instruments' in symbol_info:
                for instrument in symbol_info['instruments']:
                    self.tradingsymbol_to_token[instrument['tradingsymbol']] = instrument['instrument_token']
        logger.info(f"PaperTradingManager populated with {len(self.tradingsymbol_to_token)} instrument mappings.")

    def update_market_data(self, data: list):
        for tick in data:
            if 'instrument_token' in tick:
                self.market_data[tick['instrument_token']] = tick

    def _load_state(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    state = json.load(f)
                    self.balance = state.get('balance', 100000.0)
                    self._positions = state.get('positions', {})
                    logger.info("Paper trading state loaded.")
                    self.rms.used_margin = state.get('rms_used_margin', 0.0)

            except Exception as e:
                logger.error(f"Could not load paper trading state: {e}")
        self._save_state()

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump({
                    'balance': self.balance,
                    'positions': self._positions,
                    'rms_used_margin': self.rms.used_margin
                }, f, indent=4)
        except Exception as e:
            logger.error(f"Could not save paper trading state: {e}")

    def place_order(self, variety, exchange, tradingsymbol, transaction_type, quantity, product, order_type, price=None,
                    **kwargs):
        # 🔒 Resolve price safely (paper trading)
        if price is None:
            token = self.tradingsymbol_to_token.get(tradingsymbol)
            if token and token in self.market_data:
                price = self.market_data[token].get("last_price")

        # If still no price → reject cleanly
        if price is None:
            reason = "Price unavailable for margin calculation"
            logger.warning(f"RMS rejected order: {reason}")

            self.order_rejected.emit({
                "reason": reason,
                "tradingsymbol": tradingsymbol,
                "quantity": quantity
            })
            return None

        position = self._positions.get(tradingsymbol)
        opening_qty = 0
        if transaction_type == self.TRANSACTION_TYPE_BUY:
            if position and position["quantity"] < 0:
                opening_qty = max(0, quantity - abs(position["quantity"]))
            else:
                opening_qty = quantity
        else:
            if position and position["quantity"] > 0:
                opening_qty = max(0, quantity - position["quantity"])
            else:
                opening_qty = quantity

        if opening_qty > 0:
            allowed, reason = self.rms.can_place_order(price, opening_qty)
            if not allowed:
                logger.warning(f"RMS rejected order: {reason}")

                # Emit rejection event (UI listens to this)
                self.order_rejected.emit({
                    "reason": reason,
                    "tradingsymbol": tradingsymbol,
                    "quantity": quantity
                })

                return None

        order_id = f"paper_{int(datetime.now().timestamp() * 1000)}"
        order = {
            "order_id": order_id,
            "tradingsymbol": tradingsymbol,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "price": price,
            "order_type": order_type,
            "product": product,
            "exchange": exchange,
            "status": "OPEN",
            "order_timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "average_price": 0.0,
            "filled_quantity": 0,
            "group_name": kwargs.get("group_name"),
        }

        instrument_token = self.tradingsymbol_to_token.get(tradingsymbol)
        ltp = 0
        if instrument_token and instrument_token in self.market_data:
            ltp = self.market_data[instrument_token].get('last_price', 0)

        logger.info(
            f"📦 Placing {order_type} {transaction_type} order: {tradingsymbol} qty={quantity} price={price} ltp={ltp}")

        if order_type == self.ORDER_TYPE_MARKET:
            if ltp > 0:
                self._execute_trade(order, ltp)
                logger.info(f"✅ MARKET order executed immediately @ {ltp}")
            else:
                order['status'] = 'PENDING_EXECUTION'
                logger.warning(f"⏳ MARKET order pending - no LTP available")

        elif order_type == self.ORDER_TYPE_LIMIT:
            limit_price = order['price']
            is_buy = transaction_type == self.TRANSACTION_TYPE_BUY
            if ltp > 0 and ((is_buy and limit_price >= ltp) or (not is_buy and limit_price <= ltp)):
                self._execute_trade(order, ltp)
                logger.info(f"✅ LIMIT order executed immediately @ {ltp} (limit: {limit_price})")
            else:
                order['status'] = 'TRIGGER PENDING'
                logger.info(f"⏳ LIMIT order pending @ {limit_price} (current LTP: {ltp})")

        elif order_type in [self.ORDER_TYPE_SL, self.ORDER_TYPE_SLM]:
            # 🔒 SL/SLM orders ALWAYS start as TRIGGER PENDING
            # They should NOT execute immediately at placement
            order['status'] = 'TRIGGER PENDING'
            order['trigger_price'] = kwargs.get('trigger_price')
            logger.info(f"⏳ {order_type} order pending with trigger @ {order['trigger_price']}")

        self._orders.append(order)
        # DO NOT emit here if already executed
        if order["status"] != "COMPLETE":
            self.order_update.emit(order)

        return order_id

    def cancel_order(self, variety, order_id, **kwargs):
        for order in self._orders:
            if order['order_id'] == order_id and order['status'] in ['OPEN', 'PENDING_EXECUTION', 'TRIGGER PENDING']:
                order['status'] = 'CANCELLED'
                logger.info(f"Paper order {order_id} cancelled.")
                self.order_update.emit(order)
                return order_id
        raise ValueError(f"Could not find cancellable paper order with ID: {order_id}")

    def orders(self):
        return self._orders

    def margins(self):
        """
        Kite-compatible margins structure for PAPER trading
        """
        return {
            "equity": {
                "available": {
                    "live_balance": self.available_margin
                },
                "utilised": {
                    "total": self.used_margin
                },
                "net": self.available_margin + self.used_margin
            }
        }

    @staticmethod
    def profile():
        return {"user_id": "PAPER"}

    def positions(self):
        self._remove_expired_positions()

        for pos in self._positions.values():
            token = self.tradingsymbol_to_token.get(pos["tradingsymbol"])
            if token and token in self.market_data:
                ltp = self.market_data[token].get("last_price", pos["last_price"])
                pos["last_price"] = ltp
                pos["unrealized_pnl"] = (ltp - pos["average_price"]) * pos["quantity"]

        return {"net": list(self._positions.values())}

    def place_protective_orders(self, tradingsymbol: str, sl_price: float = None, tp_price: float = None):
        """
        Place SL/TP orders AFTER position is created.
        Called with actual prices (not amounts).
        """
        position = self._positions.get(tradingsymbol)
        if not position:
            logger.warning(f"Cannot place protective orders - position {tradingsymbol} not found")
            return

        qty = position["quantity"]
        avg_price = position["average_price"]
        exit_transaction_type = (
            self.TRANSACTION_TYPE_SELL if qty > 0 else self.TRANSACTION_TYPE_BUY
        )

        logger.info(f"📌 Placing protective orders for {tradingsymbol}: SL={sl_price}, TP={tp_price}")

        # Place SL order if provided
        if sl_price and sl_price > 0:
            try:
                sl_order_id = self.place_order(
                    variety=self.VARIETY_REGULAR,
                    exchange=position["exchange"],
                    tradingsymbol=tradingsymbol,
                    transaction_type=exit_transaction_type,  # Close the position
                    quantity=abs(qty),
                    product=position["product"],
                    order_type=self.ORDER_TYPE_SLM,
                    trigger_price=sl_price
                )
                logger.info(f"✅ SL order placed: {sl_order_id} @ trigger {sl_price}")
            except Exception as e:
                logger.error(f"Failed to place SL order: {e}")

        # Place TP order if provided
        if tp_price and tp_price > 0:
            try:
                tp_order_id = self.place_order(
                    variety=self.VARIETY_REGULAR,
                    exchange=position["exchange"],
                    tradingsymbol=tradingsymbol,
                    transaction_type=exit_transaction_type,  # Close the position
                    quantity=abs(qty),
                    product=position["product"],
                    order_type=self.ORDER_TYPE_LIMIT,
                    price=tp_price
                )
                logger.info(f"✅ TP order placed: {tp_order_id} @ {tp_price}")
            except Exception as e:
                logger.error(f"Failed to place TP order: {e}")

    def _process_pending_orders(self):
        for order in self._orders:
            if order['status'] not in ['OPEN', 'PENDING_EXECUTION', 'TRIGGER PENDING']:
                continue

            instrument_token = self.tradingsymbol_to_token.get(order['tradingsymbol'])
            if not instrument_token or instrument_token not in self.market_data:
                continue

            ltp = self.market_data[instrument_token].get('last_price', 0.0)
            if ltp <= 0:
                continue

            order_type = order['order_type']
            transaction_type = order['transaction_type']

            # Handle LIMIT orders
            if order_type == self.ORDER_TYPE_LIMIT:
                limit_price = order['price']
                if transaction_type == self.TRANSACTION_TYPE_BUY and ltp <= limit_price:
                    # BUY limit: execute at current LTP (which is <= limit, so it's a better price)
                    self._execute_trade(order, ltp)
                    logger.info(f"✅ LIMIT BUY executed: {order['tradingsymbol']} @ {ltp} (limit was {limit_price})")
                elif transaction_type == self.TRANSACTION_TYPE_SELL and ltp >= limit_price:
                    # SELL limit: execute at current LTP (which is >= limit, so it's a better price)
                    self._execute_trade(order, ltp)
                    logger.info(f"✅ LIMIT SELL executed: {order['tradingsymbol']} @ {ltp} (limit was {limit_price})")

            # Handle SL/SLM orders (protective orders)
            elif order_type in [self.ORDER_TYPE_SL, self.ORDER_TYPE_SLM]:
                trigger_price = order.get('trigger_price')
                if not trigger_price:
                    continue

                # SL orders are typically SELL orders that trigger when price falls
                # OR BUY orders (for short positions) that trigger when price rises
                if transaction_type == self.TRANSACTION_TYPE_SELL and ltp <= trigger_price:
                    # Stop loss hit for long position
                    exec_price = order.get('price',
                                           trigger_price) if order_type == self.ORDER_TYPE_SL else trigger_price
                    self._execute_trade(order, exec_price)
                    logger.info(f"🛑 SL HIT: {order['tradingsymbol']} @ {exec_price}")

                elif transaction_type == self.TRANSACTION_TYPE_BUY and ltp >= trigger_price:
                    # Stop loss hit for short position OR target for long
                    exec_price = order.get('price',
                                           trigger_price) if order_type == self.ORDER_TYPE_SL else trigger_price
                    self._execute_trade(order, exec_price)
                    logger.info(f"🎯 TARGET HIT: {order['tradingsymbol']} @ {exec_price}")

            # Handle pending market orders
            elif order['status'] == 'PENDING_EXECUTION':
                self._execute_trade(order, ltp)

    def _execute_trade(self, order: dict, price: float):
        tradingsymbol = order["tradingsymbol"]
        qty = order["quantity"]
        side = order["transaction_type"]

        order["status"] = "COMPLETE"
        order["average_price"] = price
        order["filled_quantity"] = qty
        order["exchange_timestamp"] = datetime.now().isoformat()

        position = self._positions.get(tradingsymbol)
        exit_qty = 0
        entry_qty = 0
        realized = 0.0

        if side == self.TRANSACTION_TYPE_BUY:
            if position and position["quantity"] < 0:
                cover_qty = min(qty, abs(position["quantity"]))
                entry_price = position["average_price"]
                realized = (entry_price - price) * cover_qty
                exit_qty = cover_qty
                position["realized_pnl"] += realized
                self.balance += realized
                self.rms.release_margin(entry_price, cover_qty)
                logger.info(
                    f"RMS release | price={entry_price} qty={cover_qty} "
                    f"used={self.rms.used_margin:.2f}"
                )
                position["quantity"] += cover_qty
                if position["quantity"] == 0:
                    del self._positions[tradingsymbol]
                    position = None
                qty -= cover_qty

            if qty > 0:
                entry_qty = qty
                self.rms.reserve_margin(price, qty)
                if not position:
                    self._positions[tradingsymbol] = {
                        "tradingsymbol": tradingsymbol,
                        "quantity": qty,
                        "average_price": price,
                        "last_price": price,
                        "realized_pnl": 0.0,
                        "unrealized_pnl": 0.0,
                        "product": order["product"],
                        "exchange": order["exchange"],
                        "timestamp": order["exchange_timestamp"],
                        "group_name": order.get("group_name"),
                    }
                else:
                    total_qty = position["quantity"] + qty
                    position["average_price"] = (
                            (position["average_price"] * position["quantity"] + price * qty)
                            / total_qty
                    )
                    position["quantity"] = total_qty
                    if order.get("group_name"):
                        position["group_name"] = order.get("group_name")

        else:  # SELL
            if position and position["quantity"] > 0:
                close_qty = min(qty, position["quantity"])
                entry_price = position["average_price"]
                realized = (price - entry_price) * close_qty
                exit_qty = close_qty
                position["realized_pnl"] += realized
                self.balance += realized
                self.rms.release_margin(entry_price, close_qty)
                logger.info(
                    f"RMS release | price={entry_price} qty={close_qty} "
                    f"used={self.rms.used_margin:.2f}"
                )
                position["quantity"] -= close_qty
                if position["quantity"] == 0:
                    del self._positions[tradingsymbol]
                    position = None
                qty -= close_qty

            if qty > 0:
                entry_qty = qty
                self.rms.reserve_margin(price, qty)
                if not position:
                    self._positions[tradingsymbol] = {
                        "tradingsymbol": tradingsymbol,
                        "quantity": -qty,
                        "average_price": price,
                        "last_price": price,
                        "realized_pnl": 0.0,
                        "unrealized_pnl": 0.0,
                        "product": order["product"],
                        "exchange": order["exchange"],
                        "timestamp": order["exchange_timestamp"],
                        "group_name": order.get("group_name"),
                    }
                else:
                    total_qty = abs(position["quantity"]) + qty
                    position["average_price"] = (
                            (position["average_price"] * abs(position["quantity"]) + price * qty)
                            / total_qty
                    )
                    position["quantity"] = -total_qty
                    if order.get("group_name"):
                        position["group_name"] = order.get("group_name")

        if exit_qty > 0:
            order["realized_pnl"] = realized
        order["exit_qty"] = exit_qty
        order["entry_qty"] = entry_qty

        self._save_state()
        self.order_update.emit(order)

    def _remove_expired_positions(self):
        import re
        from datetime import date, timedelta
        current_date = date.today()
        expired_symbols = []
        for symbol in list(self._positions.keys()):
            try:
                expiry_date = None
                month_match = re.search(r'(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)', symbol)
                if month_match:
                    year_str, month_str = month_match.groups()
                    month_map = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
                                 'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
                    month = month_map[month_str]
                    year = 2000 + int(year_str)
                    if month == 12:
                        expiry_date = date(year + 1, 1, 1) - timedelta(days=1)
                    else:
                        expiry_date = date(year, month + 1, 1) - timedelta(days=1)
                else:
                    weekly_match = re.search(r'(\d{5})', symbol)
                    if weekly_match:
                        date_str = weekly_match.group(1)
                        year, month, day = 2000 + int(date_str[0:2]), int(date_str[2:3]), int(date_str[3:5])
                        expiry_date = date(year, month, day)
                if expiry_date and expiry_date < current_date:
                    expired_symbols.append(symbol)
            except (ValueError, IndexError):
                continue
        if expired_symbols:
            for symbol in expired_symbols:
                if symbol in self._positions:
                    del self._positions[symbol]
                    logger.info(f"PaperTradingManager: Removed expired position {symbol} from state.")
            self._save_state()

    @property
    def used_margin(self) -> float:
        return self.rms.used_margin

    @property
    def available_margin(self) -> float:
        return self.rms.available_margin
