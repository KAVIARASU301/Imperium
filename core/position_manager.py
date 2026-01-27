# core/position_manager.py

from typing import Dict, List, Optional, Union
from datetime import datetime
from datetime import timedelta
import logging
from PySide6.QtCore import QObject, Signal
from kiteconnect import KiteConnect

from utils.trade_logger import TradeLogger
from utils.data_models import Position, Contract
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
    portfolio_exit_triggered = Signal(str, float)

    # args: reason ("STOP_LOSS" / "TARGET"), pnl

    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], trade_logger: TradeLogger):
        super().__init__()
        self.trader = trader
        self.trade_logger = trade_logger
        self._positions: Dict[str, Position] = {}
        self._pending_orders: List[Dict] = []
        self.last_refresh_time: Optional[datetime] = None
        self._refresh_in_progress = False
        self._exit_in_progress: set[str] = set()

        mode = 'paper' if isinstance(self.trader, PaperTradingManager) else 'live'
        self.instrument_data: Dict = {}
        self.tradingsymbol_map: Dict[str, Dict] = {}

        self.portfolio_stop_loss: Optional[float] = None
        self.portfolio_target: Optional[float] = None
        self._portfolio_exit_triggered = False

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

    def set_portfolio_sl_tp(self, sl: float, tp: float):
        self.portfolio_stop_loss = sl if sl < 0 else None
        self.portfolio_target = tp if tp > 0 else None
        self._portfolio_exit_triggered = False

        logger.warning(
            f"PORTFOLIO SL/TP ARMED | SL={self.portfolio_stop_loss}, TP={self.portfolio_target}"
        )

    def clear_portfolio_sl_tp(self):
        self.portfolio_stop_loss = None
        self.portfolio_target = None
        self._portfolio_exit_triggered = False

        logger.info("Portfolio SL/TP cleared")

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
                    # ✅ PRESERVE ALL RISK MANAGEMENT DATA FROM EXISTING POSITION
                    if existing_pos := self._positions.get(pos.tradingsymbol):
                        pos.order_id = existing_pos.order_id
                        pos.stop_loss_order_id = existing_pos.stop_loss_order_id
                        pos.target_order_id = existing_pos.target_order_id
                        pos.pnl = existing_pos.pnl
                        # 🔥 FIX: Preserve SL/TP/TSL during API refresh
                        pos.stop_loss_price = existing_pos.stop_loss_price
                        pos.target_price = existing_pos.target_price
                        pos.trailing_stop_loss = existing_pos.trailing_stop_loss
                        pos.is_exiting = pos.tradingsymbol in self._exit_in_progress
                    current_positions[pos.tradingsymbol] = pos

        self._synchronize_positions(current_positions)
        self._pending_orders = pending_orders

        self.positions_updated.emit(self.get_all_positions())
        self.pending_orders_updated.emit(self.get_pending_orders())

    def _convert_api_to_position(self, api_pos: dict) -> Optional[Position]:
        """
        Converts position data from the API into a rich Position object,
        using the stored instrument data to create a full Contract object.

        🔥 FIX: Now includes stop_loss_price, target_price, trailing_stop_loss as None
        so they can be set later without AttributeError
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
                contract=contract,
                # 🔥 FIX: Initialize SL/TP fields as None (will be preserved from existing if available)
                stop_loss_price=None,
                target_price=None,
                trailing_stop_loss=None
            )
        except KeyError as e:
            logger.error(f"Missing key {e} in position data: {api_pos}")
            return None

    def _synchronize_positions(self, new_positions: Dict[str, Position]):
        old_symbols = set(self._positions.keys())
        new_symbols = set(new_positions.keys())

        for symbol in old_symbols - new_symbols:
            exited_pos = self._positions.pop(symbol, None)
            if not exited_pos:
                continue

            self._exit_in_progress.discard(symbol)
            self.position_removed.emit(symbol)

        self._positions = new_positions
        expired_count = self.remove_expired_positions()
        if expired_count > 0:
            self._emit_all()

    def update_pnl_from_market_data(self, data: Union[dict, list]):
        updated = False
        ticks = data if isinstance(data, list) else [data]
        ticks_by_token = {tick['instrument_token']: tick for tick in ticks}

        for pos in list(self._positions.values()):

            if pos.is_exiting:
                continue

            if pos.contract and pos.contract.instrument_token in ticks_by_token:
                tick = ticks_by_token[pos.contract.instrument_token]
                ltp = tick.get('last_price', pos.ltp)

                if ltp != pos.ltp:
                    pos.ltp = ltp
                    qty = pos.quantity
                    avg = pos.average_price
                    pos.pnl = (ltp - avg) * qty
                    updated = True

                    if pos.trailing_stop_loss and pos.trailing_stop_loss > 0:
                        pos.stop_loss_price = self._update_trailing_stop_loss(pos, ltp)

                    if pos.stop_loss_price:
                        if (qty > 0 and ltp <= pos.stop_loss_price) or \
                                (qty < 0 and ltp >= pos.stop_loss_price):
                            logger.warning(f"🛑 SL HIT: {pos.tradingsymbol} @ {ltp} (SL: {pos.stop_loss_price})")
                            self.exit_position(pos)
                            continue

                    if pos.target_price:
                        if (qty > 0 and ltp >= pos.target_price) or \
                                (qty < 0 and ltp <= pos.target_price):
                            logger.warning(f"🎯 TARGET HIT: {pos.tradingsymbol} @ {ltp} (Target: {pos.target_price})")
                            self.exit_position(pos)
                            continue

        if updated:
            self.positions_updated.emit(self.get_all_positions())

        self._check_portfolio_sl_tp()

    def _update_trailing_stop_loss(self, pos: Position, ltp: float) -> float:
        if not pos.stop_loss_price:
            if pos.quantity > 0:
                pos.stop_loss_price = ltp - pos.trailing_stop_loss
            else:
                pos.stop_loss_price = ltp + pos.trailing_stop_loss
            return pos.stop_loss_price

        if pos.quantity > 0:
            new_sl = ltp - pos.trailing_stop_loss
            if new_sl > pos.stop_loss_price:
                pos.stop_loss_price = new_sl
                logger.info(f"📈 TSL updated: {pos.tradingsymbol} SL={new_sl:.2f}")
        else:
            new_sl = ltp + pos.trailing_stop_loss
            if new_sl < pos.stop_loss_price:
                pos.stop_loss_price = new_sl
                logger.info(f"📉 TSL updated: {pos.tradingsymbol} SL={new_sl:.2f}")

        return pos.stop_loss_price

    def add_position(self, position: Position):
        self._positions[position.tradingsymbol] = position
        # if position.stop_loss_price or position.target_price:
        #     self.place_bracket_order(position)
        self.position_added.emit(position)
        self._emit_all()

    def exit_position(self, position: Position):
        symbol = position.tradingsymbol

        if symbol in self._exit_in_progress:
            logger.info(f"Exit already in progress for {symbol}")
            return

        self._exit_in_progress.add(symbol)
        position.is_exiting = True
        # 🔒 FIX: paper trading must NOT place orders here
        if isinstance(self.trader, PaperTradingManager):
            # UI already placed the exit order
            exited_pos = self._positions.pop(symbol, None)
            if exited_pos:
                self.position_removed.emit(symbol)
                self.positions_updated.emit(self.get_all_positions())
                self.refresh_completed.emit(True)

            self._exit_in_progress.discard(symbol)
            return
        try:
            self.trader.place_order(
                variety=self.trader.VARIETY_REGULAR,
                exchange=position.exchange,
                tradingsymbol=position.tradingsymbol,
                transaction_type=self.trader.TRANSACTION_TYPE_SELL,
                quantity=abs(position.quantity),
                product=position.product,
                order_type=self.trader.ORDER_TYPE_MARKET,
            )
            logger.info(f"Exit order placed for {position.tradingsymbol}")
            exited_pos = self._positions.pop(symbol, None)
            if exited_pos:
                self.position_removed.emit(symbol)
                self.positions_updated.emit(self.get_all_positions())
                self.refresh_completed.emit(True)

        except Exception as e:
            logger.error(f"Failed to exit position {position.tradingsymbol}: {e}", exc_info=True)
            self._emit_all()
        finally:
            self._exit_in_progress.discard(symbol)

    def remove_position(self, tradingsymbol: str):
        removed_pos = self._positions.pop(tradingsymbol, None)
        if removed_pos:
            self.position_removed.emit(tradingsymbol)
            self._emit_all()

    def get_all_positions(self) -> List[Position]:
        return list(self._positions.values())

    def has_positions(self) -> bool:
        """Check if there are any open positions"""
        return len(self._positions) > 0

    def get_pending_orders(self) -> List[Dict]:
        return self._pending_orders

    def get_total_pnl(self) -> float:
        return sum(p.pnl for p in self._positions.values() if p.pnl is not None)

    def _check_portfolio_sl_tp(self):
        if self._portfolio_exit_triggered:
            return

        if self.portfolio_stop_loss is None and self.portfolio_target is None:
            return

        total_pnl = self.get_total_pnl()

        if self.portfolio_stop_loss is not None and total_pnl <= self.portfolio_stop_loss:
            logger.critical(f"🚨 PORTFOLIO STOP-LOSS HIT: Total P&L={total_pnl:.2f}")
            self._portfolio_exit_triggered = True
            self.portfolio_exit_triggered.emit("STOP_LOSS", total_pnl)

        elif self.portfolio_target is not None and total_pnl >= self.portfolio_target:
            logger.critical(f"🎯 PORTFOLIO TARGET HIT: Total P&L={total_pnl:.2f}")
            self._portfolio_exit_triggered = True
            self.portfolio_exit_triggered.emit("TARGET", total_pnl)

    def get_position(self, tradingsymbol: str) -> Optional[Position]:
        return self._positions.get(tradingsymbol)

    def remove_expired_positions(self) -> int:
        expired_symbols = []
        from datetime import datetime

        for symbol, pos in self._positions.items():
            if pos.contract and pos.contract.expiry:
                # Check if expiry is before current date
                if isinstance(pos.contract.expiry, datetime):
                    expiry_date = pos.contract.expiry.date()
                else:
                    expiry_date = pos.contract.expiry

                if expiry_date < datetime.now().date():
                    expired_symbols.append(symbol)

        for symbol in expired_symbols:
            logger.info(f"Removing expired position: {symbol}")
            self._positions.pop(symbol, None)

        return len(expired_symbols)

    def _emit_all(self):
        self.positions_updated.emit(self.get_all_positions())
        self.pending_orders_updated.emit(self.get_pending_orders())

    def update_sl_tp_for_position(
            self,
            tradingsymbol: str,
            sl_price: Optional[float],
            tp_price: Optional[float],
            tsl_value: Optional[float]
    ):
        """
        Update SL/TP for an existing position
        """
        position = self.get_position(tradingsymbol)
        if not position:
            logger.warning(f"SL/TP update ignored — position already closed: {tradingsymbol}")
            return

        position.stop_loss_price = sl_price if sl_price and sl_price > 0 else None
        position.target_price = tp_price if tp_price and tp_price > 0 else None
        position.trailing_stop_loss = tsl_value if tsl_value and tsl_value > 0 else None

        logger.info(
            f"Local SL/TP updated for {tradingsymbol}: "
            f"SL={position.stop_loss_price}, "
            f"TP={position.target_price}, "
            f"TSL={position.trailing_stop_loss}"
        )

        self._emit_all()