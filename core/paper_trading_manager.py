import logging
import json
import os
from datetime import datetime
from typing import Dict, List
from PySide6.QtCore import QObject, QTimer, Signal

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

    def __init__(self):
        super().__init__()
        self.market_data: Dict[int, Dict] = {}
        self.tradingsymbol_to_token: Dict[str, int] = {}
        self.config_path = os.path.join(os.path.expanduser("~"), ".options_scalper", "paper_account.json")

        self.balance = 100000.0
        self._positions: Dict[str, Dict] = {}
        self._orders: List[Dict] = []

        self._load_state()

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
            except Exception as e:
                logger.error(f"Could not load paper trading state: {e}")
        self._save_state()

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump({'balance': self.balance, 'positions': self._positions}, f, indent=4)
        except Exception as e:
            logger.error(f"Could not save paper trading state: {e}")

    def place_order(self, variety, exchange, tradingsymbol, transaction_type, quantity, product, order_type, price=None,
                    **kwargs):
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
            "filled_quantity": 0
        }

        instrument_token = self.tradingsymbol_to_token.get(tradingsymbol)
        ltp = 0
        if instrument_token and instrument_token in self.market_data:
            ltp = self.market_data[instrument_token].get('last_price', 0)

        if order_type == self.ORDER_TYPE_MARKET:
            if ltp > 0:
                self._execute_trade(order, ltp)
            else:
                order['status'] = 'PENDING_EXECUTION'

        elif order_type == self.ORDER_TYPE_LIMIT:
            limit_price = order['price']
            is_buy = transaction_type == self.TRANSACTION_TYPE_BUY
            if ltp > 0 and ((is_buy and limit_price >= ltp) or (not is_buy and limit_price <= ltp)):
                self._execute_trade(order, ltp)

        elif order_type == self.ORDER_TYPE_SL:
            trigger_price = kwargs.get('trigger_price')
            is_sell = transaction_type == self.TRANSACTION_TYPE_SELL
            if ltp > 0 and is_sell and ltp <= trigger_price:
                self._execute_trade(order, price or trigger_price)

        self._orders.append(order)
        self.order_update.emit(order)
        return order_id

    def cancel_order(self, variety, order_id, **kwargs):
        for order in self._orders:
            if order['order_id'] == order_id and order['status'] in ['OPEN', 'PENDING_EXECUTION']:
                order['status'] = 'CANCELLED'
                logger.info(f"Paper order {order_id} cancelled.")
                self.order_update.emit(order)
                return order_id
        raise ValueError(f"Could not find cancellable paper order with ID: {order_id}")

    def orders(self):
        return self._orders

    def margins(self):
        used_margin = 0.0
        for position in self._positions.values():
            used_margin += abs(position['quantity'] * position['average_price'])
        return {
            "equity": {
                "net": self.balance,
                "utilised": {"total": used_margin},
                "available": {"live_balance": self.balance - used_margin}
            },
            "commodity": {}
        }

    @staticmethod
    def profile():
        return {"user_id": "PAPER"}

    def positions(self):
        self._remove_expired_positions()
        for symbol, pos in self._positions.items():
            instrument_token = self.tradingsymbol_to_token.get(symbol)
            if instrument_token and instrument_token in self.market_data:
                ltp = self.market_data[instrument_token].get('last_price', pos.get('last_price', 0))
                pos['last_price'] = ltp
                pos['pnl'] = (ltp - pos['average_price']) * pos['quantity']
        return {"net": list(self._positions.values())}

    def _process_pending_orders(self):
        for order in self._orders:
            if order['status'] in ['OPEN', 'PENDING_EXECUTION']:
                instrument_token = self.tradingsymbol_to_token.get(order['tradingsymbol'])
                if instrument_token and instrument_token in self.market_data:
                    ltp = self.market_data[instrument_token].get('last_price', 0.0)
                    if ltp <= 0: continue
                    if order['order_type'] == self.ORDER_TYPE_LIMIT:
                        if (order['transaction_type'] == self.TRANSACTION_TYPE_BUY and ltp <= order['price']) or \
                                (order['transaction_type'] == self.TRANSACTION_TYPE_SELL and ltp >= order['price']):
                            self._execute_trade(order, order['price'])
                    elif order['status'] == 'PENDING_EXECUTION':
                        self._execute_trade(order, ltp)

    def _execute_trade(self, order, price):
        if price <= 0:
            instrument_token = self.tradingsymbol_to_token.get(order['tradingsymbol'])
            if instrument_token and instrument_token in self.market_data:
                last_known_ltp = self.market_data[instrument_token].get('last_price', 0.0)
                if last_known_ltp > 0:
                    price = last_known_ltp
        symbol, quantity, is_buy = order['tradingsymbol'], order['quantity'], order[
                                                                                  'transaction_type'] == self.TRANSACTION_TYPE_BUY
        trade_value = quantity * price
        pos = self._positions.get(symbol)
        if is_buy:
            self.balance -= trade_value
            if not pos:
                pos = self._positions[symbol] = {'tradingsymbol': symbol, 'quantity': 0, 'average_price': 0.0,
                                                 'exchange': order['exchange'], 'product': order['product'], 'pnl': 0,
                                                 'last_price': price}
            new_total_cost = (pos['average_price'] * pos['quantity']) + trade_value
            pos['quantity'] += quantity
            pos['average_price'] = new_total_cost / pos['quantity'] if pos['quantity'] != 0 else 0
        else:
            self.balance += trade_value
            if pos:
                realized_pnl = (price - pos['average_price']) * quantity
                order['pnl'] = realized_pnl
                pos['quantity'] -= quantity
                if pos['quantity'] == 0:
                    del self._positions[symbol]
        order.update({'status': 'COMPLETE', 'average_price': price, 'filled_quantity': quantity})
        logger.info(f"Paper trade executed: {order['transaction_type']} {quantity} {symbol} @ {price:.2f}")
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

