import json
import logging
import math
from datetime import datetime, time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer

from core.execution.paper_trading_manager import PaperTradingManager
from core.auto_trader.stacker import StackerState
from utils.data_models import Contract, OptionType, Position

logger = logging.getLogger(__name__)


class CvdAutomationCoordinator:
    """Owns the single-chart CVD automation lifecycle for the auto trader."""

    def __init__(self, main_window, trading_mode: str, base_dir: Path):
        self.main_window = main_window
        self.trading_mode = trading_mode
        self.positions: dict[int, dict] = {}
        self.market_state: dict[int, dict] = {}
        self.state_file = base_dir / f"cvd_automation_state_{trading_mode}.json"
        # Stacker: one StackerState per token while an anchor trade is active
        self._stacker_states: dict[int, StackerState] = {}

    def handle_signal(self, payload: dict):
        w = self.main_window
        token = payload.get("instrument_token")
        if token is None:
            return

        dialog = w.cvd_single_chart_dialogs.get(token)
        if dialog and hasattr(dialog, "_record_detected_signal"):
            dialog._record_detected_signal(payload)

        if self.is_cutoff_reached():
            self.enforce_cutoff_exit(reason="AUTO_3PM_CUTOFF")
            logger.info("[AUTO] Ignoring CVD signal after 3:00 PM cutoff.")
            return

        state = self.market_state.get(token, {})
        if not state.get("enabled"):
            return

        signal_side = payload.get("signal_side")
        if signal_side not in {"long", "short"}:
            return

        is_stack = bool(payload.get("is_stack"))  # stacker pyramid entry flag
        is_stack_unwind = bool(payload.get("is_stack_unwind"))  # LIFO unwind exit
        route = payload.get("route") or state.get("route") or "buy_exit_panel"
        active_trade = self.positions.get(token)

        # ── LIFO UNWIND: exit a specific stacked position ────────────────────
        if is_stack_unwind:
            if not active_trade:
                logger.warning("[STACKER] Unwind signal but no active trade for token=%s", token)
                return
            stack_num = payload.get("stack_number", 0)
            stack_entry_price = payload.get("stack_entry_price", 0.0)
            # Retrieve the specific tradingsymbol for this stack slot (if tracked)
            stacked_syms = active_trade.get("stacked_tradingsymbols", [])
            # Try to find by stack number (1-based index)
            target_sym = None
            if stacked_syms and 0 < stack_num <= len(stacked_syms):
                target_sym = stacked_syms[stack_num - 1]
            elif stacked_syms:
                # fallback: unwind the last tracked stack symbol
                target_sym = stacked_syms[-1]

            if target_sym:
                position = w.position_manager.get_position(target_sym)
                if position:
                    self.exit_position_automated(position, reason=f"AUTO_STACK_UNWIND#{stack_num}")
                    # Remove from tracked list
                    try:
                        stacked_syms.remove(target_sym)
                    except ValueError:
                        pass
                    active_trade["stacked_tradingsymbols"] = stacked_syms
                    logger.info(
                        "[STACKER] Unwound stack #%d (%s) entry=%.2f current=%.2f",
                        stack_num, target_sym, stack_entry_price,
                        float(payload.get("price_close", 0)),
                    )
                else:
                    logger.warning("[STACKER] Unwind target %s not found in position manager", target_sym)
            else:
                logger.warning(
                    "[STACKER] No stacked symbol found for unwind #%d token=%s "
                    "(stacked_tradingsymbols=%s)",
                    stack_num, token, stacked_syms,
                )
            return  # always return — never place a new order on unwind

        if active_trade:
            active_side = active_trade.get("signal_side")
            last_signal_time = active_trade.get("signal_timestamp")
            if last_signal_time:
                try:
                    last_time = datetime.fromisoformat(last_signal_time)
                    current_time = datetime.fromisoformat(payload.get("timestamp"))
                    time_diff_minutes = (current_time - last_time).total_seconds() / 60
                except (ValueError, TypeError):
                    time_diff_minutes = 0
            else:
                time_diff_minutes = 0

            if active_side == signal_side:
                # Stack signals on the same side always pass through — they are
                # intentional pyramid additions, not duplicates.
                if not is_stack and time_diff_minutes < 15:
                    logger.info("[AUTO] Skipping same-side signal (%.1f mins since last). Need 15+ mins for stacking.", time_diff_minutes)
                    return
            else:
                # Opposite-side signal: check priority and exit if higher
                active_strategy = active_trade.get("strategy_type") or "atr_reversal"
                incoming_signal_type = payload.get("signal_type") or state.get("signal_filter") or "atr_reversal"
                incoming_strategy = {
                    "ema_cvd_cross": "ema_cross",
                    "ema_cross": "ema_cross",
                    "atr_divergence": "atr_divergence",
                    "range_breakout": "range_breakout",
                    "cvd_range_breakout": "cvd_range_breakout",
                    "open_drive": "open_drive",
                }.get(incoming_signal_type, "atr_reversal")
                strategy_priority = payload.get("strategy_priorities") or state.get("strategy_priorities") or {}
                try:
                    strategy_priority = {str(k): int(v) for k, v in dict(strategy_priority).items()}
                except Exception:
                    strategy_priority = {}
                if not strategy_priority:
                    strategy_priority = {
                        "open_drive": 1,
                        "cvd_range_breakout": 2,
                        "range_breakout": 3,
                        "ema_cross": 4,
                        "atr_divergence": 5,
                        "atr_reversal": 6,
                    }
                if strategy_priority.get(incoming_strategy, 99) >= strategy_priority.get(active_strategy, 99):
                    logger.info(
                        "[AUTO] Ignoring opposite lower-priority signal for token=%s (%s list=%s kept %s/%s over %s/%s).",
                        token,
                        state.get("symbol") or token,
                        payload.get("priority_list") or state.get("priority_list") or "fallback",
                        active_side,
                        active_strategy,
                        signal_side,
                        incoming_strategy,
                    )
                    return

                active_symbols = active_trade.get("tradingsymbols") or [active_trade.get("tradingsymbol")]
                for sym in [s for s in active_symbols if s]:
                    active_position = w.position_manager.get_position(sym)
                    if active_position:
                        self.exit_position_automated(active_position, reason="AUTO_REVERSE")
                if self.positions.pop(token, None) is not None:
                    # Reset stacker on reverse
                    self._stacker_states.pop(token, None)
                    self._notify_dialog_stacker_reset(token)
                    self.persist_state()

        contract = self.get_atm_contract_for_signal(signal_side)
        if not contract:
            logger.warning("[AUTO] ATM contract unavailable for signal: %s", signal_side)
            return

        lots = max(1, int(w.header.lot_size_spin.value()))
        quantity = max(1, int(contract.lot_size) * lots)
        stoploss_points = float(payload.get("stoploss_points") or state.get("stoploss_points") or 50.0)
        max_profit_giveback_points = float(payload.get("max_profit_giveback_points") or state.get("max_profit_giveback_points") or 0.0)
        open_drive_max_profit_giveback_points = float(payload.get("open_drive_max_profit_giveback_points") or state.get("open_drive_max_profit_giveback_points") or 0.0)
        max_profit_giveback_strategies = payload.get("max_profit_giveback_strategies") or state.get("max_profit_giveback_strategies") or ["atr_reversal", "ema_cross", "atr_divergence", "cvd_range_breakout", "range_breakout", "open_drive"]
        if not isinstance(max_profit_giveback_strategies, (list, tuple, set)):
            max_profit_giveback_strategies = ["atr_reversal", "ema_cross", "atr_divergence", "cvd_range_breakout", "range_breakout", "open_drive"]

        entry_underlying = float(payload.get("price_close") or state.get("price_close") or 0.0)
        signal_type = payload.get("signal_type") or state.get("signal_filter")
        strategy_type = {"ema_cross": "ema_cross", "atr_divergence": "atr_divergence", "range_breakout": "range_breakout", "cvd_range_breakout": "cvd_range_breakout", "open_drive": "open_drive"}.get(signal_type, "atr_reversal")
        if float(contract.ltp or 0.0) <= 0 or entry_underlying <= 0:
            return

        sl_underlying = entry_underlying - stoploss_points if signal_side == "long" else entry_underlying + stoploss_points

        order_params = {
            "contract": contract,
            "quantity": quantity,
            "order_type": w.trader.ORDER_TYPE_MARKET,
            "product": w.settings.get('default_product', w.trader.PRODUCT_MIS),
            "transaction_type": w.trader.TRANSACTION_TYPE_BUY,
            "stop_loss_price": None,
            "target_price": None,
            "group_name": f"CVD_AUTO_{token}",
            "auto_token": token,
        }

        tracked_tradingsymbol = contract.tradingsymbol
        all_tradingsymbols = [contract.tradingsymbol]
        order_details = None

        if route == "buy_exit_panel" and w.buy_exit_panel and w.strike_ladder:
            desired_option_type = OptionType.CALL if signal_side == "long" else OptionType.PUT
            if w.buy_exit_panel.option_type != desired_option_type:
                w.buy_exit_panel.option_type = desired_option_type
                w.buy_exit_panel._update_ui_for_option_type()
            order_details = w.buy_exit_panel.build_order_details()
            if order_details and order_details.get('strikes'):
                all_tradingsymbols = [s['contract'].tradingsymbol for s in order_details['strikes'] if s.get('contract') and getattr(s['contract'], 'tradingsymbol', None)]
                if all_tradingsymbols:
                    tracked_tradingsymbol = all_tradingsymbols[0]

        # ── Stack signals: place order only, do NOT overwrite anchor state ──
        if is_stack:
            logger.info(
                "[STACKER] Placing stack order #%s: token=%s side=%s",
                payload.get("stack_number", "?"), token, signal_side,
            )
            placed_syms: list[str] = []
            if route == "buy_exit_panel" and w.buy_exit_panel and w.strike_ladder:
                if order_details and order_details.get('strikes'):
                    symbol = order_details.get('symbol')
                    if symbol and symbol in w.instrument_data:
                        instrument_lot_quantity = w.instrument_data[symbol].get('lot_size', 1)
                        order_details['total_quantity_per_strike'] = order_details.get('lot_size', 1) * instrument_lot_quantity
                        order_details['product'] = w.settings.get('default_product', 'MIS')
                        w._execute_orders(order_details)
                        placed_syms = [s['contract'].tradingsymbol for s in order_details['strikes'] if s.get('contract') and getattr(s['contract'], 'tradingsymbol', None)]
                else:
                    logger.warning("[STACKER] Failed to build order details from buy_exit_panel for stack")
            else:
                w._execute_single_strike_order(order_params)
                placed_syms = [contract.tradingsymbol]

            # ── Track stacked symbols so unwind can exit the right position ──
            if active_trade is not None and placed_syms:
                stacked = active_trade.setdefault("stacked_tradingsymbols", [])
                stacked.extend(placed_syms)
                logger.debug("[STACKER] Tracked stack symbols: %s (total=%d)", placed_syms, len(stacked))

            return  # ← do NOT update positions dict for stack entries

        self.positions[token] = {
            "tradingsymbol": tracked_tradingsymbol,
            "tradingsymbols": all_tradingsymbols if route == "buy_exit_panel" else [tracked_tradingsymbol],
            "signal_side": signal_side,
            "route": route,
            "signal_timestamp": payload.get("timestamp"),
            "strategy_type": strategy_type,
            "stoploss_points": stoploss_points,
            "max_profit_giveback_points": max_profit_giveback_points,
            "open_drive_max_profit_giveback_points": open_drive_max_profit_giveback_points,
            "max_profit_giveback_strategies": list(max_profit_giveback_strategies),
            "atr_trailing_step_points": 10.0,
            "entry_underlying": entry_underlying,
            "max_favorable_points": 0.0,
            "sl_underlying": sl_underlying,
            "last_price_close": entry_underlying,
            "last_ema10": state.get("ema10"),
            "last_ema51": state.get("ema51"),
            "last_cvd_close": state.get("cvd_close"),
            "last_cvd_ema10": state.get("cvd_ema10"),
            "last_cvd_ema51": state.get("cvd_ema51"),
            "quantity": quantity,
            "product": w.settings.get('default_product', w.trader.PRODUCT_MIS),
            "transaction_type": w.trader.TRANSACTION_TYPE_BUY,
            "group_name": f"CVD_AUTO_{token}",
        }

        # ── Stacker: init state for anchor, or record stack entry ─────────
        if is_stack:
            stack_state = self._stacker_states.get(token)
            if stack_state is not None:
                stack_num = payload.get("stack_number", len(stack_state.stack_entries) + 1)
                logger.info(
                    "[STACKER] Stack #%d confirmed: token=%s side=%s price=%.2f",
                    stack_num, token, signal_side, entry_underlying,
                )
            # Stack signals don't reset the position anchor — just pass through to order
        else:
            # Anchor entry: init a fresh StackerState (dialog already emits stack signals,
            # we track here for logging and reset coordination only)
            dialog = w.cvd_single_chart_dialogs.get(token)
            stacker_enabled = (
                dialog is not None
                and hasattr(dialog, "stacker_enabled_check")
                and dialog.stacker_enabled_check.isChecked()
                and (strategy_type != "open_drive" or (hasattr(dialog, "open_drive_stack_enabled_check") and dialog.open_drive_stack_enabled_check.isChecked()))
            )
            if stacker_enabled:
                self._stacker_states[token] = StackerState(
                    anchor_entry_price=entry_underlying,
                    anchor_bar_idx=0,          # coordinator doesn't track bar idx
                    signal_side=signal_side,
                    step_points=float(dialog.stacker_step_input.value()),
                    max_stacks=int(dialog.stacker_max_input.value()),
                )
                logger.info(
                    "[STACKER] Anchor registered: token=%s side=%s price=%.2f step=%.0f max=%d",
                    token, signal_side, entry_underlying,
                    dialog.stacker_step_input.value(), dialog.stacker_max_input.value(),
                )
            else:
                self._stacker_states.pop(token, None)

        self.persist_state()

        if dialog and hasattr(dialog, "_set_live_trade_state"):
            dialog._set_live_trade_state("entered", self.positions[token])

        if route == "buy_exit_panel" and w.buy_exit_panel and w.strike_ladder:
            if order_details and order_details.get('strikes'):
                symbol = order_details.get('symbol')
                if symbol and symbol in w.instrument_data:
                    instrument_lot_quantity = w.instrument_data[symbol].get('lot_size', 1)
                    order_details['total_quantity_per_strike'] = order_details.get('lot_size', 1) * instrument_lot_quantity
                    order_details['product'] = w.settings.get('default_product', 'MIS')
                    w._execute_orders(order_details)
            else:
                logger.warning("[AUTO] Failed to build order details from buy_exit_panel")
        else:
            w._execute_single_strike_order(order_params)

        entry_signal_ts = payload.get("timestamp")
        QTimer.singleShot(2000, lambda t=token, s=tracked_tradingsymbol, ts=entry_signal_ts: self.reconcile_failed_entry(t, s, ts))

    def handle_market_state(self, payload: dict):
        w = self.main_window
        token = payload.get("instrument_token")
        if token is None:
            return

        self.market_state[token] = payload
        if self.is_cutoff_reached():
            self.enforce_cutoff_exit(reason="AUTO_3PM_CUTOFF")
            return

        active_trade = self.positions.get(token)
        if not active_trade:
            return

        tradingsymbols = [s for s in (active_trade.get("tradingsymbols") or [active_trade.get("tradingsymbol")]) if s]
        tradingsymbol = tradingsymbols[0] if tradingsymbols else None
        positions = [p for p in (w.position_manager.get_position(s) for s in tradingsymbols) if p]
        if not positions:
            if tradingsymbol and w._has_pending_order_for_symbol(tradingsymbol):
                w._start_cvd_pending_retry(token)
                return
            w._stop_cvd_pending_retry(token)
            if self.positions.pop(token, None) is not None:
                self.persist_state()
            return

        w._stop_cvd_pending_retry(token)
        position = positions[0]

        def _to_finite_float(value, default=0.0):
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None if default is None else float(default)
            return parsed if math.isfinite(parsed) else (None if default is None else float(default))

        price_close = _to_finite_float(payload.get("price_close"), 0.0)
        if price_close <= 0:
            return
        ema10 = _to_finite_float(payload.get("ema10"), 0.0)
        cvd_ema10 = _to_finite_float(payload.get("cvd_ema10"), 0.0)
        ema51 = _to_finite_float(payload.get("ema51"), 0.0)
        cvd_close = _to_finite_float(payload.get("cvd_close"), 0.0)
        cvd_ema51 = _to_finite_float(payload.get("cvd_ema51"), 0.0)

        signal_side = active_trade.get("signal_side")
        strategy_type = active_trade.get("strategy_type") or "atr_reversal"
        stoploss_points = _to_finite_float(active_trade.get("stoploss_points"), 0.0)
        max_profit_giveback_points = _to_finite_float(active_trade.get("max_profit_giveback_points"), 0.0)
        open_drive_max_profit_giveback_points = _to_finite_float(active_trade.get("open_drive_max_profit_giveback_points"), 0.0)
        max_profit_giveback_strategies = set(active_trade.get("max_profit_giveback_strategies") or ["atr_reversal", "ema_cross", "atr_divergence", "cvd_range_breakout", "range_breakout", "open_drive"])
        atr_trailing_step_points = _to_finite_float(active_trade.get("atr_trailing_step_points"), 10.0)
        entry_underlying = _to_finite_float(active_trade.get("entry_underlying"), 0.0)
        max_favorable_points = _to_finite_float(active_trade.get("max_favorable_points"), 0.0)
        sl_underlying = _to_finite_float(active_trade.get("sl_underlying"), None) if active_trade.get("sl_underlying") is not None else None

        favorable_move = 0.0
        if entry_underlying > 0:
            favorable_move = (price_close - entry_underlying) if signal_side == "long" else (entry_underlying - price_close)
            if not math.isfinite(favorable_move):
                favorable_move = 0.0
            max_favorable_points = max(max_favorable_points or 0.0, favorable_move)
            active_trade["max_favorable_points"] = max_favorable_points

        if stoploss_points > 0 and entry_underlying > 0:
            trail_offset = 0.0
            if strategy_type == "atr_reversal" and atr_trailing_step_points > 0:
                trail_steps = int(max(0.0, favorable_move) // atr_trailing_step_points)
                if trail_steps > 0:
                    trail_offset = trail_steps * atr_trailing_step_points
            elif strategy_type in {"ema_cross", "range_breakout", "cvd_range_breakout", "open_drive"} and favorable_move >= 200.0:
                trail_offset = (1 + int((favorable_move - 200.0) // 100.0)) * 100.0
            if trail_offset > 0:
                new_sl = (entry_underlying - stoploss_points + trail_offset) if signal_side == "long" else (entry_underlying + stoploss_points - trail_offset)
                sl_underlying = max(float(sl_underlying), new_sl) if (sl_underlying is not None and signal_side == "long") else min(float(sl_underlying), new_sl) if sl_underlying is not None else new_sl
                active_trade["sl_underlying"] = sl_underlying

        hit_stop = (price_close <= float(sl_underlying)) if (sl_underlying is not None and signal_side == "long") else (price_close >= float(sl_underlying)) if sl_underlying is not None else False

        prev_price, prev_ema10, prev_ema51 = active_trade.get("last_price_close"), active_trade.get("last_ema10"), active_trade.get("last_ema51")
        prev_cvd, prev_cvd_ema10, prev_cvd_ema51 = active_trade.get("last_cvd_close"), active_trade.get("last_cvd_ema10"), active_trade.get("last_cvd_ema51")
        price_cross_above_ema51 = all(v is not None for v in (prev_price, prev_ema51)) and ema51 > 0 and prev_price <= prev_ema51 and price_close > ema51
        price_cross_below_ema51 = all(v is not None for v in (prev_price, prev_ema51)) and ema51 > 0 and prev_price >= prev_ema51 and price_close < ema51
        price_cross_above_ema10 = all(v is not None for v in (prev_price, prev_ema10)) and ema10 > 0 and prev_price <= prev_ema10 and price_close > ema10
        price_cross_below_ema10 = all(v is not None for v in (prev_price, prev_ema10)) and ema10 > 0 and prev_price >= prev_ema10 and price_close < ema10
        cvd_cross_above_ema10 = all(v is not None for v in (prev_cvd, prev_cvd_ema10)) and cvd_ema10 != 0 and prev_cvd <= prev_cvd_ema10 and cvd_close > cvd_ema10
        cvd_cross_below_ema10 = all(v is not None for v in (prev_cvd, prev_cvd_ema10)) and cvd_ema10 != 0 and prev_cvd >= prev_cvd_ema10 and cvd_close < cvd_ema10
        cvd_cross_above_ema51 = all(v is not None for v in (prev_cvd, prev_cvd_ema51)) and cvd_ema51 != 0 and prev_cvd <= prev_cvd_ema51 and cvd_close > cvd_ema51
        cvd_cross_below_ema51 = all(v is not None for v in (prev_cvd, prev_cvd_ema51)) and cvd_ema51 != 0 and prev_cvd >= prev_cvd_ema51 and cvd_close < cvd_ema51

        use_open_drive_override = strategy_type == "open_drive" and (open_drive_max_profit_giveback_points or 0.0) > 0
        effective_giveback_points = (
            open_drive_max_profit_giveback_points if use_open_drive_override else max_profit_giveback_points
        )
        giveback_enabled_for_strategy = (
            use_open_drive_override
            or strategy_type in max_profit_giveback_strategies
        )

        exit_reason = None
        if hit_stop:
            exit_reason = "AUTO_SL"
        elif giveback_enabled_for_strategy and effective_giveback_points > 0 and max_favorable_points and (max_favorable_points - favorable_move) >= effective_giveback_points:
            exit_reason = "AUTO_MAX_PROFIT_GIVEBACK"
        elif strategy_type == "ema_cross" and ((signal_side == "long" and cvd_cross_below_ema10) or (signal_side == "short" and cvd_cross_above_ema10)):
            exit_reason = "AUTO_EMA10_CROSS"
        elif strategy_type == "atr_divergence" and ((signal_side == "long" and price_cross_above_ema51) or (signal_side == "short" and price_cross_below_ema51)):
            exit_reason = "AUTO_EMA51_CROSS"
        elif strategy_type in {"range_breakout", "cvd_range_breakout"} and ((signal_side == "long" and (price_cross_below_ema10 or price_cross_below_ema51)) or (signal_side == "short" and (price_cross_above_ema10 or price_cross_above_ema51))):
            exit_reason = "AUTO_BREAKOUT_EXIT"
        elif strategy_type == "open_drive" and (
                (signal_side == "long" and (price_close < ema10 or cvd_close < cvd_ema10))
                or (signal_side == "short" and (price_close > ema10 or cvd_close > cvd_ema10))
        ):
            exit_reason = "AUTO_OPEN_DRIVE_FAST_EMA_CLOSE_EXIT"
        elif strategy_type == "atr_reversal" and ema51 > 0 and (
                (signal_side == "long" and price_close >= ema51)
                or (signal_side == "short" and price_close <= ema51)
        ):
            exit_reason = "AUTO_ATR_REVERSAL_EXIT"

        if exit_reason:
            for pos in positions:
                self.exit_position_automated(pos, reason=exit_reason)
            # Also exit any remaining stacked positions that weren't unwound yet
            if active_trade:
                remaining_stacked = active_trade.get("stacked_tradingsymbols", [])
                for sym in remaining_stacked:
                    stk_pos = w.position_manager.get_position(sym)
                    if stk_pos:
                        self.exit_position_automated(stk_pos, reason=f"{exit_reason}_STACK")
                        logger.info("[STACKER] Exiting remaining stack on anchor exit: %s", sym)
            if self.positions.pop(token, None) is not None:
                # Reset stacker state and notify the dialog
                self._stacker_states.pop(token, None)
                self._notify_dialog_stacker_reset(token)
                self.persist_state()
            return

        active_trade.update({
            "last_price_close": price_close,
            "last_ema10": ema10,
            "last_ema51": ema51,
            "last_cvd_close": cvd_close,
            "last_cvd_ema10": cvd_ema10,
            "last_cvd_ema51": cvd_ema51,
        })

    def is_cutoff_reached(self) -> bool:
        return datetime.now().time() >= time(15, 0)

    def enforce_cutoff_exit(self, reason: str = "AUTO_3PM_CUTOFF"):
        w = self.main_window
        if not self.positions:
            return
        for token, active_trade in list(self.positions.items()):
            tradingsymbols = [s for s in (active_trade.get("tradingsymbols") or [active_trade.get("tradingsymbol")]) if s]
            for symbol in tradingsymbols:
                position = w.position_manager.get_position(symbol)
                if position:
                    self.exit_position_automated(position, reason=reason)
            w._stop_cvd_pending_retry(token)
            self.positions.pop(token, None)
            self._stacker_states.pop(token, None)
            self._notify_dialog_stacker_reset(token)
        self.persist_state()

    def persist_state(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": datetime.now().isoformat(),
                "trading_mode": self.trading_mode,
                "positions": {str(token): trade for token, trade in self.positions.items() if isinstance(trade, dict) and trade.get("tradingsymbol")},
            }
            self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("[AUTO] Failed to persist CVD automation state: %s", exc, exc_info=True)

    def load_state(self):
        if not self.state_file.exists():
            return
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            for token_raw, trade in (payload.get("positions", {}) or {}).items():
                if not isinstance(trade, dict) or not str(trade.get("tradingsymbol") or "").strip():
                    continue
                try:
                    token = int(token_raw)
                except (TypeError, ValueError):
                    continue
                self.positions[token] = trade
        except Exception as exc:
            logger.error("[AUTO] Failed to load CVD automation state: %s", exc, exc_info=True)

    def reconcile_failed_entry(self, token: int, tradingsymbol: str, signal_timestamp: str | None):
        w = self.main_window
        active_trade = self.positions.get(token)
        if not active_trade:
            return
        if signal_timestamp and active_trade.get("signal_timestamp") != signal_timestamp:
            return
        tracked_symbol = tradingsymbol or active_trade.get("tradingsymbol")
        if tracked_symbol and w.position_manager.get_position(tracked_symbol):
            return
        if tracked_symbol and w._has_pending_order_for_symbol(tracked_symbol):
            w._start_cvd_pending_retry(token)
            return
        self.positions.pop(token, None)
        w._stop_cvd_pending_retry(token)
        self.persist_state()

    def reconcile_positions(self):
        w = self.main_window
        removed_tokens = []
        for token, active_trade in list(self.positions.items()):
            tradingsymbol = active_trade.get("tradingsymbol") if isinstance(active_trade, dict) else None
            if not tradingsymbol:
                removed_tokens.append(token)
                continue
            if w.position_manager.get_position(tradingsymbol):
                continue
            if w._has_pending_order_for_symbol(tradingsymbol):
                w._start_cvd_pending_retry(token)
                continue
            removed_tokens.append(token)

        for token in removed_tokens:
            w._stop_cvd_pending_retry(token)
            self.positions.pop(token, None)
            self._stacker_states.pop(token, None)
            self._notify_dialog_stacker_reset(token)
        if removed_tokens:
            self.persist_state()

    def _notify_dialog_stacker_reset(self, token: int):
        """Tell the AutoTraderDialog for this token to clear its live stacker state."""
        try:
            dialog = self.main_window.cvd_single_chart_dialogs.get(token)
            if dialog and hasattr(dialog, "reset_stacker"):
                dialog.reset_stacker()
        except Exception as exc:
            logger.debug("[STACKER] Could not notify dialog reset for token=%s: %s", token, exc)

    def get_atm_contract_for_signal(self, signal_side: str) -> Optional[Contract]:
        w = self.main_window
        if not w.strike_ladder or w.strike_ladder.atm_strike is None:
            return None
        ladder_row = w.strike_ladder.contracts.get(w.strike_ladder.atm_strike, {})
        option_key = "CE" if signal_side == "long" else "PE"
        return ladder_row.get(option_key)

    def exit_position_automated(self, position: Position, reason: str = "AUTO"):
        w = self.main_window
        try:
            transaction_type = w.trader.TRANSACTION_TYPE_SELL if position.quantity > 0 else w.trader.TRANSACTION_TYPE_BUY
            w.trader.place_order(
                variety=w.trader.VARIETY_REGULAR,
                exchange=position.exchange,
                tradingsymbol=position.tradingsymbol,
                transaction_type=transaction_type,
                quantity=abs(position.quantity),
                product=position.product,
                order_type=w.trader.ORDER_TYPE_MARKET,
            )
            w._refresh_positions()
        except Exception as exc:
            logger.error("[AUTO] Failed automated exit for %s: %s", position.tradingsymbol, exc, exc_info=True)
