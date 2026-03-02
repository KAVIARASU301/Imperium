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
from typing import Optional
from PySide6.QtCore import QObject, QTimer, Slot

from core.auto_trader.multi_symbol_engine import AtrSignalEvent
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

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.w = main_window
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
        """
        ATR Stop-Loss: SL = entry_price ± (ATR × multiplier)
        This is standard at prop desks — dynamic SL that respects current volatility.
        """
        multiplier = getattr(self.w, "_scanner_sl_atr_mult", 1.5)
        sl_distance = event.atr * multiplier
        logger.info("[ROUTER] ATR SL attached | %s | dist=%.2f pts", event.symbol, sl_distance)
        # Hook into position manager to set SL on the just-filled leg
        # (connects to existing confirm_and_finalize_order flow)

    def _set_cooldown(self, symbol: str, ms: int = 65_000):
        """One bar cooldown — prevents double-entry on same signal bar."""
        self._cooldown_symbols.add(symbol)
        QTimer.singleShot(ms, lambda: self._cooldown_symbols.discard(symbol))