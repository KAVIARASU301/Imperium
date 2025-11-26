# core/position_manager.py

from typing import Dict, List, Optional, Union
from datetime import datetime
from datetime import timedelta
import logging
from PySide6.QtCore import QObject, Signal
from kiteconnect import KiteConnect

from utils.trade_logger import TradeLogger
from utils.data_models import Position, Contract
from utils.pnl_logger import PnlLogger
from core.paper_trading_manager import PaperTradingManager

logger = logging.getLogger(__name__)


class PositionManager(QObject):
    """
    Manages both active positions and pending orders by fetching
    and differentiating them from the Kite API or a simulated trader.
    """
    positions_updated = Signal(list)
    pending_orders_updated = Signal(list)
    refresh_completed = Signal(bool)
    api_error_occurred = Signal(str)
    position_added = Signal(object)
    position_removed = Signal(str)

    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], trade_logger: TradeLogger):
        super().__init__()
        self.trader = trader
        self.trade_logger = trade_logger
        self._positions: Dict[str, Position] = {}
        self._pending_orders: List[Dict] = []
        self.last_refresh_time: Optional[datetime] = None
        self._refresh_in_progress = False

        mode = 'paper' if isinstance(self.trader, PaperTradingManager) else 'live'
        self.pnl_logger = PnlLogger(mode=mode)
        self.realized_day_pnl = 0.0
        self.trade_log: List[float] = []
        self.instrument_data: Dict = {}
        self.tradingsymbol_map: Dict[str, Dict] = {}

    def set_instrument_data(self, instrument_data: Dict):
        """
        Receives and processes the instrument data to create a quick
        lookup map from tradingsymbol to instrument details.
        """
        self.instrument_data = instrument_data
        self.tradingsymbol_map = {
            inst['tradingsymbol']: inst
            for symbol_info in instrument_data.values()
            for inst in symbol_info.get('instruments', [])
        }
        logger.info(f"PositionManager received instrument data with {len(self.tradingsymbol_map)} mappings.")

    def set_kite_client(self, kite_client: KiteConnect):
        self.trader = kite_client

    def refresh_from_api(self):
        if not self.trader or self._refresh_in_progress:
            return

        try:
            self._refresh_in_progress = True
            api_positions_data = self.trader.positions().get('net', [])
            api_orders_data = self.trader.orders()
            self._process_orders_and_positions(api_positions_data, api_orders_data)
            self.last_refresh_time = datetime.now()
            self.refresh_completed.emit(True)
        except Exception as e:
            logger.error(f"API refresh failed: {e}", exc_info=True)
            self.api_error_occurred.emit(str(e))
            self.refresh_completed.emit(False)
        finally:
            self._refresh_in_progress = False

    def _process_orders_and_positions(self, api_positions: List[Dict], api_orders: List[Dict]):
        current_positions = {}
        pending_orders = [o for o in api_orders if
                          o.get('status') in ['TRIGGER PENDING', 'OPEN', 'AMO REQ RECEIVED']]

        for pos_data in api_positions:
            if pos_data.get('quantity', 0) != 0:
                pos = self._convert_api_to_position(pos_data)
                if pos:
                    if existing_pos := self._positions.get(pos.tradingsymbol):
                        pos.order_id = existing_pos.order_id
                        pos.stop_loss_order_id = existing_pos.stop_loss_order_id
                        pos.target_order_id = existing_pos.target_order_id
                        pos.pnl = existing_pos.pnl
                        # --- ADD THESE THREE LINES ---
                        pos.stop_loss_price = existing_pos.stop_loss_price
                        pos.target_price = existing_pos.target_price
                        pos.trailing_stop_loss = existing_pos.trailing_stop_loss

                    current_positions[pos.tradingsymbol] = pos

        self._check_and_cancel_oco_orders(api_orders)
        self._synchronize_positions(current_positions)
        self._pending_orders = pending_orders

        self.positions_updated.emit(self.get_all_positions())
        self.pending_orders_updated.emit(self.get_pending_orders())
    def _convert_api_to_position(self, api_pos: dict) -> Optional[Position]:
        """
        Converts position data from the API into a rich Position object,
        using the stored instrument data to create a full Contract object.
        """
        tradingsymbol = api_pos.get('tradingsymbol')
        if not tradingsymbol:
            return None

        inst_details = self.tradingsymbol_map.get(tradingsymbol)
        if not inst_details:
            logger.warning(f"No instrument details found for position: {tradingsymbol}. Real-time P&L will not update.")
            contract = Contract(
                symbol=tradingsymbol, tradingsymbol=tradingsymbol,
                instrument_token=api_pos.get('instrument_token', 0),
                lot_size=1, strike=0, option_type="", expiry=datetime.now().date(),
            )
        else:
            contract = Contract(
                symbol=inst_details.get('name', ''),
                strike=inst_details.get('strike', 0.0),
                option_type=inst_details.get('instrument_type', ''),
                expiry=inst_details.get('expiry'),
                tradingsymbol=tradingsymbol,
                instrument_token=inst_details.get('instrument_token', 0),
                lot_size=inst_details.get('lot_size', 1)
            )

        try:
            return Position(
                symbol=tradingsymbol,
                tradingsymbol=tradingsymbol,
                quantity=api_pos.get('quantity', 0),
                average_price=api_pos.get('average_price', 0.0),
                ltp=api_pos.get('last_price', 0.0),
                pnl=api_pos.get('pnl', 0.0),
                order_id=None,
                exchange=api_pos.get('exchange', 'NFO'),
                product=api_pos.get('product', 'MIS'),
                contract=contract
            )
        except KeyError as e:
            logger.error(f"Missing key {e} in position data: {api_pos}")
            return None

    def _synchronize_positions(self, new_positions: Dict[str, Position]):
        old_symbols = set(self._positions.keys())
        new_symbols = set(new_positions.keys())

        for symbol in old_symbols - new_symbols:
            exited_pos = self._positions.pop(symbol)
            self._cancel_pending_legs(exited_pos, "Position closed")
            if exited_pos.pnl is not None:
                self.realized_day_pnl += exited_pos.pnl
                self.pnl_logger.log_pnl(datetime.today(), exited_pos.pnl)
            self.position_removed.emit(symbol)

        self._positions = new_positions
        expired_count = self.remove_expired_positions()
        if expired_count > 0:
            self._emit_all()

    def update_pnl_from_market_data(self, data: Union[dict, list]):
        updated = False
        ticks = data if isinstance(data, list) else [data]
        ticks_by_token = {tick['instrument_token']: tick for tick in ticks}

        for pos in self._positions.values():
            if pos.contract and pos.contract.instrument_token in ticks_by_token:
                tick = ticks_by_token[pos.contract.instrument_token]
                ltp = tick.get('last_price', pos.ltp)
                if abs(pos.ltp - ltp) > 1e-9:
                    pos.update_pnl(ltp)
                    updated = True

            if pos.stop_loss_price is not None and pos.pnl <= -pos.stop_loss_price:
                self.exit_position(pos)

            if pos.target_price is not None and pos.pnl >= pos.target_price:
                self.exit_position(pos)

            if pos.trailing_stop_loss and pos.stop_loss_order_id and pos.stop_loss_price:
                pnl_points = (pos.ltp - pos.average_price)
                if pnl_points > 0:
                    current_trail_level = (pos.average_price - pos.stop_loss_price) // pos.trailing_stop_loss
                    new_trail_level = pnl_points // pos.trailing_stop_loss
                    if new_trail_level > current_trail_level:
                        new_sl_price = pos.stop_loss_price + (
                                new_trail_level - current_trail_level) * pos.trailing_stop_loss
                        self.modify_stop_loss(pos.tradingsymbol, new_sl_price)

        if updated:
            self.positions_updated.emit(self.get_all_positions())

    def add_position(self, position: Position):
        self._positions[position.tradingsymbol] = position
        if position.stop_loss_price or position.target_price:
            self.place_bracket_order(position)
        self.position_added.emit(position)
        self._emit_all()

    def exit_position(self, position: Position):
        """Exit a single position."""
        try:
            order_id = self.trader.place_order(
                variety=self.trader.VARIETY_REGULAR,
                exchange=position.exchange,
                tradingsymbol=position.tradingsymbol,
                transaction_type=self.trader.TRANSACTION_TYPE_SELL,
                quantity=abs(position.quantity),
                product=position.product,
                order_type=self.trader.ORDER_TYPE_MARKET,
            )
            logger.info(f"Exit order placed for {position.tradingsymbol} -> Order ID: {order_id}")
            self.remove_position(position.tradingsymbol)
        except Exception as e:
            logger.error(f"Failed to exit position for {position.tradingsymbol}: {e}")

    def remove_position(self, tradingsymbol: str):
        if tradingsymbol in self._positions:
            exited_pos = self._positions.pop(tradingsymbol)
            self._cancel_pending_legs(exited_pos, f"Manual exit for {tradingsymbol}")
            self.position_removed.emit(tradingsymbol)
            self._emit_all()

    def get_all_positions(self) -> List[Position]:
        return list(self._positions.values())

    def get_pending_orders(self) -> List[Dict]:
        return self._pending_orders

    def get_total_pnl(self) -> float:
        return sum(p.pnl for p in self._positions.values() if p.pnl is not None)

    def get_position(self, tradingsymbol: str) -> Optional[Position]:
        return self._positions.get(tradingsymbol)

    def get_realized_day_pnl(self) -> float:
        return self.realized_day_pnl

    def has_positions(self) -> bool:
        """Checks if there are any open positions with non-zero quantity."""
        return any(pos.quantity != 0 for pos in self._positions.values())

    def _check_and_cancel_oco_orders(self, api_orders: List[Dict]):
        executed_exit_order_ids = {o['order_id'] for o in api_orders if o.get('status') == 'COMPLETE'}
        for pos in list(self._positions.values()):
            sl_id, tp_id = pos.stop_loss_order_id, pos.target_order_id
            if not (sl_id or tp_id): continue

            sl_executed = sl_id and sl_id in executed_exit_order_ids
            tp_executed = tp_id and tp_id in executed_exit_order_ids

            if sl_executed and tp_id:
                self._cancel_order(tp_id, f"SL hit for {pos.tradingsymbol}")
                pos.target_order_id = None
            elif tp_executed and sl_id:
                self._cancel_order(sl_id, f"TP hit for {pos.tradingsymbol}")
                pos.stop_loss_order_id = None

    def _cancel_pending_legs(self, position: Position, reason: str):
        """Cancels any open SL or TP orders associated with a position."""
        if position.stop_loss_order_id:
            self._cancel_order(position.stop_loss_order_id, reason)
            position.stop_loss_order_id = None
        if position.target_order_id:
            self._cancel_order(position.target_order_id, reason)
            position.target_order_id = None

    def _cancel_order(self, order_id: str, reason: str):
        if not order_id: return
        try:
            self.trader.cancel_order(self.trader.VARIETY_REGULAR, order_id)
            logger.info(f"Cancelled order {order_id} due to {reason}.")
        except Exception as e:
            logger.warning(
                f"Could not cancel order {order_id} (reason: {reason}). It may have already executed/cancelled. Error: {e}")

    def place_bracket_order(self, position: Position):
        if position.stop_loss_price: self.place_stop_loss_order(position)
        if position.target_price: self.place_target_order(position)
        self._emit_all()  # Refresh UI after setting SL/TP

    def place_stop_loss_order(self, position: Position):
        try:
            order_id = self.trader.place_order(
                variety=self.trader.VARIETY_REGULAR, exchange=position.exchange,
                tradingsymbol=position.tradingsymbol, transaction_type=self.trader.TRANSACTION_TYPE_SELL,
                quantity=abs(position.quantity), product=position.product, order_type=self.trader.ORDER_TYPE_SL,
                price=position.stop_loss_price, trigger_price=position.stop_loss_price
            )
            position.stop_loss_order_id = order_id
            logger.info(f"Placed SL order {order_id} for {position.tradingsymbol} at {position.stop_loss_price}")
        except Exception as e:
            logger.error(f"Failed to place SL order for {position.tradingsymbol}: {e}")

    def place_target_order(self, position: Position):
        try:
            order_id = self.trader.place_order(
                variety=self.trader.VARIETY_REGULAR, exchange=position.exchange,
                tradingsymbol=position.tradingsymbol, transaction_type=self.trader.TRANSACTION_TYPE_SELL,
                quantity=abs(position.quantity), product=position.product, order_type=self.trader.ORDER_TYPE_LIMIT,
                price=position.target_price
            )
            position.target_order_id = order_id
            logger.info(f"Placed TP order {order_id} for {position.tradingsymbol} at {position.target_price}")
        except Exception as e:
            logger.error(f"Failed to place TP order for {position.tradingsymbol}: {e}")

    def modify_stop_loss(self, tradingsymbol: str, new_sl_price: float):
        position = self.get_position(tradingsymbol)
        if not position or not position.stop_loss_order_id: return
        try:
            self.trader.cancel_order(self.trader.VARIETY_REGULAR, position.stop_loss_order_id)
            logger.info(f"Cancelled old SL order {position.stop_loss_order_id} for TSL.")
            position.stop_loss_price = new_sl_price
            self.place_stop_loss_order(position)
        except Exception as e:
            logger.error(f"Failed to modify SL for TSL on {tradingsymbol}: {e}")

    def _emit_all(self):
        self.positions_updated.emit(self.get_all_positions())

    def remove_expired_positions(self):
        import re
        from datetime import date, timedelta
        current_date = date.today()
        expired_symbols = []
        for symbol, position in list(self._positions.items()):
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
                logger.info(f"Removing expired position: {symbol}")
                if symbol in self._positions:
                    del self._positions[symbol]
                    self.position_removed.emit(symbol)
            logger.info(f"Auto-removed {len(expired_symbols)} expired positions")
            return len(expired_symbols)
        return 0

    def update_sl_tp_for_position(self, tradingsymbol: str, sl_price: Optional[float], tp_price: Optional[float],
                                  tsl_value: Optional[float]):
        """
        Updates SL/TP for an existing position, canceling old orders and placing new ones.
        """
        position = self.get_position(tradingsymbol)
        if not position:
            logger.error(f"Cannot update SL/TP: Position not found for {tradingsymbol}")
            return

        # Cancel any existing SL or TP orders before applying new ones.
        self._cancel_pending_legs(position, "SL/TP Modified by user")

        # Update the position object with the new values
        position.stop_loss_price = sl_price if sl_price and sl_price > 0 else None
        position.target_price = tp_price if tp_price and tp_price > 0 else None
        position.trailing_stop_loss = tsl_value if tsl_value and tsl_value > 0 else None

        logger.info(
            f"Updating SL/TP for {position.tradingsymbol}: SL={position.stop_loss_price}, TP={position.target_price}, TSL={position.trailing_stop_loss}")

        # Place new bracket orders based on the updated values.
        # This method automatically triggers the UI update by calling _emit_all().
        self.place_bracket_order(position)