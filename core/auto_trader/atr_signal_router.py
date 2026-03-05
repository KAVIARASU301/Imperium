"""
atr_signal_router.py
====================
Bridges the multi-symbol scanner to the options execution stack.

Institutional pattern: "Signal → Instrument Resolver → Order Router"
The scanner deals in underlying symbols (NIFTY, BANKNIFTY).
Options are derivatives — we resolve ATM/OTM strikes at signal time,
subscribe them for live data, then route through the existing
buy_exit_panel execution path.
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional
from PySide6.QtCore import QObject, QTimer, Slot

from core.auto_trader.multi_symbol_engine import AtrSignalEvent
from core.auto_trader.targeting import compute_target_price
from utils.data_models import OptionType

logger = logging.getLogger(__name__)


class AtrSignalRouter(QObject):
    """
    On signal_fired from MultiSymbolEngine:
      1. Resolve ATM strike for the signalling underlying
      2. Subscribe strike tokens for live data (N above, M below per user config)
      3. Route order to buy_exit_panel with correct CE/PE
      4. Attach ATR-based stop-loss to the position
    """

    def __init__(self, main_window, scanner_panel=None, parent=None):
        super().__init__(parent)
        self.w = main_window
        self._panel = scanner_panel              # direct reference — no tree walk, no circular import
        self._cooldown_symbols: set[str] = set()   # prevent re-entry same bar

    @Slot(object)
    def on_signal(self, event: AtrSignalEvent):
        """Entry point — called from AtrScannerPanel when AUTOMATE is ON."""
        if event.chop_filtered:
            logger.info("[ROUTER] %s signal chop-filtered, skipping order", event.symbol)
            return
        if event.symbol in self._cooldown_symbols:
            logger.info("[ROUTER] %s in cooldown, skipping", event.symbol)
            return

        panel = self._get_scanner_panel()
        if panel:
            min_conf = panel._min_confidence_spin.value()
            min_adx = panel._min_adx_spin.value()
            session_start = panel._session_start_spin.value()
            session_end = panel._session_end_spin.value()

            if event.confidence < min_conf:
                logger.info("[ROUTER] %s conf=%.2f below gate %.2f, skipping", event.symbol, event.confidence, min_conf)
                return

            if event.adx < min_adx:
                logger.info("[ROUTER] %s adx=%.1f below gate %.1f, skipping", event.symbol, event.adx, min_adx)
                return

            now_hhmm = int(datetime.now().strftime("%H%M"))
            if not (session_start <= now_hhmm <= session_end):
                logger.info("[ROUTER] %s outside session window %d-%d", event.symbol, session_start, session_end)
                return

        option_type = OptionType.CALL if event.side == "long" else OptionType.PUT
        self._subscribe_strikes_and_execute(event, option_type)
        self._set_cooldown(event.symbol)

    def _subscribe_strikes_and_execute(self, event: AtrSignalEvent, option_type: OptionType):
        w = self.w

        # 1. Get current spot price for ATM resolution
        spot = w._get_current_price(event.symbol)
        if not spot:
            logger.error("[ROUTER] No spot price for %s, cannot route order", event.symbol)
            return

        # 2. Update strike ladder to this symbol
        expiry_str = w.header.expiry_combo.currentText()
        from datetime import datetime
        expiry_date = datetime.strptime(expiry_str, '%d%b%y').date()
        w._update_strike_ladder_with_fallback(event.symbol, expiry_date)

        # 3. Set correct option type in buy_exit_panel
        if w.buy_exit_panel.option_type != option_type:
            w.buy_exit_panel.option_type = option_type
            w.buy_exit_panel._update_ui_for_option_type()

        # 4. Build order and execute
        order_details = w.buy_exit_panel.build_order_details()
        if not order_details or not order_details.get("strikes"):
            logger.error("[ROUTER] buy_exit_panel returned empty order_details for %s", event.symbol)
            return

        symbol = order_details.get("symbol") or event.symbol
        instrument_lot_quantity = 1
        if symbol and hasattr(w, "instrument_data") and symbol in w.instrument_data:
            instrument_lot_quantity = w.instrument_data[symbol].get("lot_size", 1)
        elif hasattr(w.buy_exit_panel, "lot_quantity"):
            instrument_lot_quantity = w.buy_exit_panel.lot_quantity

        num_lots = order_details.get("lot_size", 1)
        order_details["total_quantity_per_strike"] = num_lots * instrument_lot_quantity
        order_details["product"] = w.settings.get("default_product", w.trader.PRODUCT_MIS)

        # Tag with strategy metadata for trade ledger
        order_details["trade_status"] = "ALGO"
        order_details["strategy_name"] = f"ATR_REVERSAL_{event.side.upper()}"

        # 5. Execute
        w.execution_service.execute_orders(order_details)
        logger.info(
            "[ROUTER] Order routed | %s %s | spot=%.2f | conf=%s | atr=%.2f",
            event.symbol, event.side.upper(), spot, event.confidence_pct, event.atr
        )

        # 6. Attach ATR-based stop-loss (after fill confirmation)
        QTimer.singleShot(1500, lambda: self._attach_atr_stoploss(event))

    def _attach_atr_stoploss(self, event: AtrSignalEvent):
        """Find the just-filled position and apply ATR-based SL/TP."""
        panel = self._get_scanner_panel()
        sl_multiplier = panel._sl_atr_mult_spin.value() if panel else 1.5
        tp_multiplier = panel._tp_atr_mult_spin.value() if panel else 2.0
        target_mode = panel._target_mode_combo.currentData() if panel and hasattr(panel, "_target_mode_combo") else "atr"
        sl_distance = event.atr * sl_multiplier

        positions = self.w.position_manager.get_all_positions()
        for pos in positions:
            symbol_match = event.symbol.upper() in (getattr(pos, "tradingsymbol", "") or "").upper()
            entry_time = getattr(pos, "entry_time", None)
            fresh = (datetime.now() - entry_time).total_seconds() < 30 if entry_time else True
            if not symbol_match or not fresh:
                continue

            if event.side == "long":
                sl_price = pos.average_price - sl_distance
            else:
                sl_price = pos.average_price + sl_distance

            tp_price = compute_target_price(
                side=event.side,
                average_price=pos.average_price,
                sl_distance=sl_distance,
                tp_multiplier=tp_multiplier,
                target_mode=str(target_mode or "atr"),
                ema51=float(getattr(event, "ema51", 0.0) or 0.0),
            )

            try:
                self.w.position_manager.update_position_risk(
                    tradingsymbol=pos.tradingsymbol,
                    stop_loss=round(sl_price, 2),
                    target=round(tp_price, 2),
                )
                logger.info(
                    "[ROUTER] SL/TP set | %s | SL=%.2f TP=%.2f | dist=%.2f pts",
                    pos.tradingsymbol,
                    sl_price,
                    tp_price,
                    sl_distance,
                )
            except Exception as exc:
                logger.error("[ROUTER] Failed to attach SL/TP for %s: %s", pos.tradingsymbol, exc)
            break

    def _set_cooldown(self, symbol: str, ms: int = 65_000):
        """One bar cooldown — prevents double-entry on same signal bar."""
        self._cooldown_symbols.add(symbol)
        QTimer.singleShot(ms, lambda: self._cooldown_symbols.discard(symbol))

    def _get_scanner_panel(self):
        """Return the AtrScannerPanel reference passed at construction time.
        Falls back to parent-tree walk only if not wired directly (legacy path).
        """
        if self._panel is not None:
            return self._panel
        # Fallback: walk parent chain (no import needed — panel passed as TYPE check avoided)
        obj = self.parent()
        while obj is not None:
            # Check by class name to avoid circular import
            if type(obj).__name__ == "AtrScannerPanel":
                return obj
            obj = obj.parent() if callable(getattr(obj, "parent", None)) else None
        return None
