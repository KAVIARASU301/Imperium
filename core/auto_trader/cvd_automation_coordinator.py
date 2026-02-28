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
from utils.pricing_utils import calculate_smart_limit_price

logger = logging.getLogger(__name__)


def _to_finite_float(value, default=0.0):
    """Convert value to a finite float, returning default if invalid/infinite."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None if default is None else float(default)
    return parsed if math.isfinite(parsed) else (None if default is None else float(default))


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

    @staticmethod
    def _is_regime_breakdown(active_trade: dict) -> bool:
        adx_hist = active_trade.get("regime_adx_hist") or []
        vol_hist = active_trade.get("regime_vol_hist") or []

        # Read tunable knobs (with safe fallbacks to old hardcoded values)
        breakdown_bars = max(2, int(_to_finite_float(active_trade.get("trend_exit_breakdown_bars"), 3)))
        vol_drop_pct = float(_to_finite_float(active_trade.get("trend_exit_vol_drop_pct"), 0.85))
        lookback = breakdown_bars + 2   # dynamic lookback (old hardcoded = 5 when breakdown_bars=3)

        if len(adx_hist) < lookback or len(vol_hist) < lookback:
            return False

        # ADX must be falling for `breakdown_bars` consecutive bars
        adx_falling = all(adx_hist[-i] < adx_hist[-i - 1] for i in range(1, breakdown_bars + 1))
        adx_below_lookback = adx_hist[-1] < adx_hist[-lookback]
        vol_below_lookback = vol_hist[-1] < vol_hist[-lookback]
        peak_vol = float(active_trade.get("trend_mode_peak_vol") or 0.0)
        vol_contracting = vol_hist[-1] < (vol_drop_pct * peak_vol) if peak_vol > 0 else False
        return adx_falling and adx_below_lookback and vol_below_lookback and vol_contracting

    @staticmethod
    def _to_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _automation_window_for_token(self, token: int | None = None) -> tuple[time, time]:
        state = self.market_state.get(token, {}) if token is not None else {}
        start_hour = max(0, min(23, self._to_int(state.get("automation_start_hour"), 9)))
        start_minute = max(0, min(59, self._to_int(state.get("automation_start_minute"), 15)))
        cutoff_hour = max(0, min(23, self._to_int(state.get("automation_cutoff_hour"), 15)))
        cutoff_minute = max(0, min(59, self._to_int(state.get("automation_cutoff_minute"), 15)))
        return time(start_hour, start_minute), time(cutoff_hour, cutoff_minute)

    def is_before_start(self, token: int | None = None) -> bool:
        start_time, _ = self._automation_window_for_token(token)
        return datetime.now().time() < start_time

    def handle_signal(self, payload: dict):
        w = self.main_window
        token = payload.get("instrument_token")
        if token is None:
            return

        dialog = w.cvd_single_chart_dialogs.get(token)
        if dialog and hasattr(dialog, "_record_detected_signal"):
            dialog._record_detected_signal(payload)

        if self.is_cutoff_reached(token):
            self.enforce_cutoff_exit(reason="AUTO_3PM_CUTOFF", token=token)
            logger.info("[AUTO] Ignoring CVD signal after configured cutoff.")
            return

        if self.is_before_start(token):
            logger.debug("[AUTO] Ignoring CVD signal before configured start time.")
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
        order_type = str(payload.get("order_type") or state.get("order_type") or w.trader.ORDER_TYPE_MARKET).upper()
        if order_type not in {w.trader.ORDER_TYPE_MARKET, w.trader.ORDER_TYPE_LIMIT}:
            order_type = w.trader.ORDER_TYPE_MARKET
        active_trade = self.positions.get(token)

        # ── LIFO UNWIND: exit a specific stacked position ────────────────────
        if is_stack_unwind:
            if not active_trade:
                logger.warning("[STACKER] Unwind signal but no active trade for token=%s", token)
                return
            stack_num = payload.get("stack_number", 0)
            stack_entry_price = payload.get("stack_entry_price", 0.0)
            stack_layers = active_trade.get("stack_layers", [])
            if not stack_layers and active_trade.get("stacked_tradingsymbols"):
                stack_layers = [
                    {
                        "stack_number": idx + 1,
                        "layer_tag": f"STACK_{idx + 1}",
                        "tradingsymbols": [sym],
                        "qty_per_symbol": int(active_trade.get("quantity") or 0),
                        "entry_price": 0.0,
                    }
                    for idx, sym in enumerate(active_trade.get("stacked_tradingsymbols", []))
                    if sym
                ]
                active_trade["stack_layers"] = stack_layers
            target_layer = next((layer for layer in stack_layers if layer.get("stack_number") == stack_num), None)
            if target_layer is None and stack_layers:
                target_layer = stack_layers[-1]

            if target_layer:
                anchor_syms = set(active_trade.get("tradingsymbols", []))
                for target_sym in target_layer.get("tradingsymbols", []):
                    position = w.position_manager.get_position(target_sym)
                    if not position:
                        logger.warning("[STACKER] Unwind target %s not found in position manager", target_sym)
                        continue
                    if target_sym in anchor_syms:
                        self._exit_partial_qty(
                            w,
                            target_sym,
                            qty_to_exit=int(target_layer.get("qty_per_symbol") or 0),
                            reason=f"AUTO_STACK_UNWIND#{stack_num}",
                        )
                    else:
                        self.exit_position_automated(position, reason=f"AUTO_STACK_UNWIND#{stack_num}")
                active_trade["stack_layers"] = [
                    layer for layer in stack_layers if layer.get("stack_number") != target_layer.get("stack_number")
                ]
                logger.info(
                    "[STACKER] Unwound stack #%d (%s) entry=%.2f current=%.2f",
                    stack_num,
                    target_layer.get("layer_tag", "UNKNOWN"),
                    stack_entry_price,
                    float(payload.get("price_close", 0)),
                )
            else:
                logger.warning(
                    "[STACKER] No stack layer found for unwind #%d token=%s (stack_layers=%s)",
                    stack_num,
                    token,
                    stack_layers,
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
        open_drive_tick_drawdown_limit_points = float(payload.get("open_drive_tick_drawdown_limit_points") or state.get("open_drive_tick_drawdown_limit_points") or 100.0)
        atr_trailing_step_points = float(payload.get("atr_trailing_step_points") or state.get("atr_trailing_step_points") or 10.0)
        current_atr = float(payload.get("atr") or state.get("atr") or 0.0)
        max_profit_giveback_strategies = payload.get("max_profit_giveback_strategies") or state.get("max_profit_giveback_strategies") or ["atr_reversal", "ema_cross", "atr_divergence", "cvd_range_breakout", "range_breakout", "open_drive"]
        if not isinstance(max_profit_giveback_strategies, (list, tuple, set)):
            max_profit_giveback_strategies = ["atr_reversal", "ema_cross", "atr_divergence", "cvd_range_breakout", "range_breakout", "open_drive"]
        dynamic_exit_trend_following_strategies = payload.get("dynamic_exit_trend_following_strategies") or state.get("dynamic_exit_trend_following_strategies") or ["ema_cross", "range_breakout", "cvd_range_breakout"]
        if not isinstance(dynamic_exit_trend_following_strategies, (list, tuple, set)):
            dynamic_exit_trend_following_strategies = ["ema_cross", "range_breakout", "cvd_range_breakout"]
        trend_exit_adx_min = _to_finite_float(payload.get("trend_exit_adx_min"), _to_finite_float(state.get("trend_exit_adx_min"), 28.0))
        trend_exit_atr_ratio_min = _to_finite_float(payload.get("trend_exit_atr_ratio_min"), _to_finite_float(state.get("trend_exit_atr_ratio_min"), 1.15))
        trend_exit_confirm_bars = max(1, int(_to_finite_float(payload.get("trend_exit_confirm_bars"), _to_finite_float(state.get("trend_exit_confirm_bars"), 3))))
        trend_exit_min_profit = _to_finite_float(payload.get("trend_exit_min_profit"), _to_finite_float(state.get("trend_exit_min_profit"), 0.0))
        trend_exit_vol_drop_pct = _to_finite_float(payload.get("trend_exit_vol_drop_pct"), _to_finite_float(state.get("trend_exit_vol_drop_pct"), 0.85))
        trend_exit_breakdown_bars = max(2, int(_to_finite_float(payload.get("trend_exit_breakdown_bars"), _to_finite_float(state.get("trend_exit_breakdown_bars"), 3))))

        entry_underlying = float(payload.get("price_close") or state.get("price_close") or 0.0)
        signal_type = payload.get("signal_type") or state.get("signal_filter")
        strategy_type = {"ema_cross": "ema_cross", "atr_divergence": "atr_divergence", "range_breakout": "range_breakout", "cvd_range_breakout": "cvd_range_breakout", "open_drive": "open_drive"}.get(signal_type, "atr_reversal")
        if float(contract.ltp or 0.0) <= 0 or entry_underlying <= 0:
            return

        sl_underlying = entry_underlying - stoploss_points if signal_side == "long" else entry_underlying + stoploss_points

        order_params = {
            "contract": contract,
            "quantity": quantity,
            "order_type": order_type,
            "price": calculate_smart_limit_price(contract) if order_type == w.trader.ORDER_TYPE_LIMIT else None,
            "product": w.settings.get('default_product', w.trader.PRODUCT_MIS),
            "transaction_type": w.trader.TRANSACTION_TYPE_BUY,
            "stop_loss_price": None,
            "target_price": None,
            "group_name": f"CVD_AUTO_{token}",
            "auto_token": token,
            "trade_status": "ALGO",
            "strategy_name": strategy_type,
        }

        tracked_tradingsymbol = contract.tradingsymbol
        all_tradingsymbols = [contract.tradingsymbol]
        order_details = None

        if route == "buy_exit_panel" and w.buy_exit_panel and w.strike_ladder:
            desired_option_type = OptionType.CALL if signal_side == "long" else OptionType.PUT
            if w.buy_exit_panel.option_type != desired_option_type:
                w.buy_exit_panel.option_type = desired_option_type
                w.buy_exit_panel._update_ui_for_option_type()
            if hasattr(w.buy_exit_panel, "order_type_combo"):
                combo_idx = w.buy_exit_panel.order_type_combo.findData(order_type)
                if combo_idx >= 0:
                    w.buy_exit_panel.order_type_combo.setCurrentIndex(combo_idx)
            order_details = w.buy_exit_panel.build_order_details()
            if order_details and order_details.get('strikes'):
                all_tradingsymbols = [s['contract'].tradingsymbol for s in order_details['strikes'] if s.get('contract') and getattr(s['contract'], 'tradingsymbol', None)]
                if all_tradingsymbols:
                    tracked_tradingsymbol = all_tradingsymbols[0]

        # ── Stack signals: place order only, do NOT overwrite anchor state ──
        if is_stack:
            stack_num = int(payload.get("stack_number") or 0) or len((active_trade or {}).get("stack_layers", [])) + 1
            stack_label = f"STACK_{stack_num}"
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
                        order_details['order_type'] = order_type
                        order_details['trade_status'] = 'ALGO'
                        order_details['strategy_name'] = stack_label
                        w._execute_orders(order_details)
                        placed_syms = [s['contract'].tradingsymbol for s in order_details['strikes'] if s.get('contract') and getattr(s['contract'], 'tradingsymbol', None)]
                else:
                    logger.warning("[STACKER] Failed to build order details from buy_exit_panel for stack")
            else:
                order_params["strategy_name"] = stack_label
                w._execute_single_strike_order(order_params)
                placed_syms = [contract.tradingsymbol]

            # ── Track each stack as a tagged layer (not flat index list) ──
            if active_trade is not None and placed_syms:
                layer_record = {
                    "stack_number": stack_num,
                    "layer_tag": f"STACK_{stack_num}",
                    "tradingsymbols": placed_syms,
                    "qty_per_symbol": int(order_details.get("total_quantity_per_strike", 0)) if order_details else int(quantity),
                    "entry_price": float(payload.get("price_close", 0.0)),
                }
                active_trade.setdefault("stack_layers", []).append(layer_record)
                logger.debug("[STACKER] Tracked stack layer: %s", layer_record)

            return  # ← do NOT update positions dict for stack entries

        self.positions[token] = {
            "tradingsymbol": tracked_tradingsymbol,
            "tradingsymbols": all_tradingsymbols if route == "buy_exit_panel" else [tracked_tradingsymbol],
            "signal_side": signal_side,
            "route": route,
            "order_type": order_type,
            "signal_timestamp": payload.get("timestamp"),
            "strategy_type": strategy_type,
            "stoploss_points": stoploss_points,
            "max_profit_giveback_points": max_profit_giveback_points,
            "open_drive_max_profit_giveback_points": open_drive_max_profit_giveback_points,
            "open_drive_tick_drawdown_limit_points": open_drive_tick_drawdown_limit_points,
            "max_profit_giveback_strategies": list(max_profit_giveback_strategies),
            "dynamic_exit_trend_following_strategies": list(dynamic_exit_trend_following_strategies),
            "trend_exit_adx_min": trend_exit_adx_min,
            "trend_exit_atr_ratio_min": trend_exit_atr_ratio_min,
            "trend_exit_confirm_bars": trend_exit_confirm_bars,
            "trend_exit_min_profit": trend_exit_min_profit,
            "trend_exit_vol_drop_pct": trend_exit_vol_drop_pct,
            "trend_exit_breakdown_bars": trend_exit_breakdown_bars,
            "atr_trailing_step_points": atr_trailing_step_points,
            "entry_underlying": entry_underlying,
            "entry_atr": current_atr if math.isfinite(current_atr) and current_atr > 0 else 0.0,
            "max_favorable_points": 0.0,
            "sl_underlying": sl_underlying,
            "last_price_close": entry_underlying,
            "last_ema10": state.get("ema10"),
            "last_ema51": state.get("ema51"),
            "last_ema51_simple": state.get("ema51_simple", state.get("ema51")),
            "last_cvd_close": state.get("cvd_close"),
            "last_cvd_ema10": state.get("cvd_ema10"),
            "last_cvd_ema51": state.get("cvd_ema51"),
            "last_cvd_ema51_simple": state.get("cvd_ema51_simple", state.get("cvd_ema51")),
            "quantity": quantity,
            "product": w.settings.get('default_product', w.trader.PRODUCT_MIS),
            "transaction_type": w.trader.TRANSACTION_TYPE_BUY,
            "group_name": f"CVD_AUTO_{token}",
            "exit_mode": "default",
            "trend_mode_unlock_bar_count": 0,
            "trend_mode_peak_vol": 0.0,
            "regime_adx_hist": [],
            "regime_vol_hist": [],
            "stack_layers": [],
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
                    anchor_tradingsymbols=list(all_tradingsymbols if route == "buy_exit_panel" else [tracked_tradingsymbol]),
                    anchor_qty_per_symbol=int(quantity),
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
                    order_details['order_type'] = order_type
                    w._execute_orders(order_details)
            else:
                logger.warning("[AUTO] Failed to build order details from buy_exit_panel")
        else:
            w._execute_single_strike_order(order_params)

        entry_signal_ts = payload.get("timestamp")
        QTimer.singleShot(2000, lambda t=token, s=tracked_tradingsymbol, ts=entry_signal_ts: self.reconcile_failed_entry(t, s, ts))

    def handle_tick_data(self, ticks: list[dict]):
        w = self.main_window
        if not ticks or not self.positions:
            return

        latest_ticks: dict[int, dict] = {}
        for tick in ticks:
            token = tick.get("instrument_token")
            if token is not None:
                latest_ticks[token] = tick

        if not latest_ticks:
            return

        for token, active_trade in list(self.positions.items()):
            if active_trade.get("strategy_type") != "open_drive":
                continue

            limit_points = float(active_trade.get("open_drive_tick_drawdown_limit_points") or 0.0)
            if limit_points <= 0:
                continue

            tick = latest_ticks.get(token)
            if not tick:
                continue

            try:
                last_price = float(tick.get("last_price") or 0.0)
                entry_underlying = float(active_trade.get("entry_underlying") or 0.0)
            except (TypeError, ValueError):
                continue

            if last_price <= 0 or entry_underlying <= 0:
                continue

            signal_side = active_trade.get("signal_side")
            adverse_move = (entry_underlying - last_price) if signal_side == "long" else (last_price - entry_underlying)
            if adverse_move < limit_points:
                continue

            tradingsymbols = [s for s in (active_trade.get("tradingsymbols") or [active_trade.get("tradingsymbol")]) if s]
            for symbol in tradingsymbols:
                position = w.position_manager.get_position(symbol)
                if position:
                    self.exit_position_automated(position, reason="AUTO_OPEN_DRIVE_TICK_DRAWDOWN")

            if self.positions.pop(token, None) is not None:
                self._stacker_states.pop(token, None)
                self._notify_dialog_stacker_reset(token)
                self.persist_state()

    def handle_market_state(self, payload: dict):
        w = self.main_window
        token = payload.get("instrument_token")
        if token is None:
            return

        self.market_state[token] = payload
        if self.is_cutoff_reached(token):
            self.enforce_cutoff_exit(reason="AUTO_3PM_CUTOFF", token=token)
            return

        active_trade = self.positions.get(token)
        if not active_trade:
            return

        tradingsymbols = [s for s in (active_trade.get("tradingsymbols") or [active_trade.get("tradingsymbol")]) if s]
        tradingsymbol = tradingsymbols[0] if tradingsymbols else None
        positions = [p for p in (w.position_manager.get_position(s) for s in tradingsymbols) if p]
        if not positions:
            strategy_type = active_trade.get("strategy_type")
            if strategy_type != "open_drive" and tradingsymbol and w._has_pending_order_for_symbol(tradingsymbol):
                w._start_cvd_pending_retry(token)
                return
            w._stop_cvd_pending_retry(token)
            if self.positions.pop(token, None) is not None:
                self.persist_state()
            return

        w._stop_cvd_pending_retry(token)
        position = positions[0]

        price_close = _to_finite_float(payload.get("price_close"), 0.0)
        if price_close <= 0:
            return
        ema10 = _to_finite_float(payload.get("ema10"), 0.0)
        cvd_ema10 = _to_finite_float(payload.get("cvd_ema10"), 0.0)
        ema51 = _to_finite_float(payload.get("ema51"), 0.0)
        ema51_simple = _to_finite_float(payload.get("ema51_simple"), ema51)
        cvd_close = _to_finite_float(payload.get("cvd_close"), 0.0)
        cvd_ema51 = _to_finite_float(payload.get("cvd_ema51"), 0.0)
        cvd_ema51_simple = _to_finite_float(payload.get("cvd_ema51_simple"), cvd_ema51)
        adx = _to_finite_float(payload.get("adx"), 0.0)
        atr_normalized = _to_finite_float(payload.get("atr_normalized"), 0.0)
        regime_trend = payload.get("regime_trend")
        regime_is_chop = (regime_trend == "CHOP") or (regime_trend is None and adx < 20.0)

        signal_side = active_trade.get("signal_side")
        strategy_type = active_trade.get("strategy_type") or "atr_reversal"
        stoploss_points = _to_finite_float(active_trade.get("stoploss_points"), 0.0)
        max_profit_giveback_points = _to_finite_float(active_trade.get("max_profit_giveback_points"), 0.0)
        open_drive_max_profit_giveback_points = _to_finite_float(active_trade.get("open_drive_max_profit_giveback_points"), 0.0)
        max_profit_giveback_strategies = set(active_trade.get("max_profit_giveback_strategies") or ["atr_reversal", "ema_cross", "atr_divergence", "cvd_range_breakout", "range_breakout", "open_drive"])
        dynamic_exit_trend_following_strategies = set(active_trade.get("dynamic_exit_trend_following_strategies") or ["ema_cross", "range_breakout", "cvd_range_breakout"])
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
            if strategy_type == "atr_reversal":
                # Keep ATR reversal SL simple and fixed from entry.
                trail_offset = 0.0
            elif strategy_type in {"ema_cross", "range_breakout", "cvd_range_breakout", "open_drive"} and favorable_move >= 200.0:
                trail_offset = (1 + int((favorable_move - 200.0) // 100.0)) * 100.0
            if trail_offset > 0:
                new_sl = (entry_underlying - stoploss_points + trail_offset) if signal_side == "long" else (entry_underlying + stoploss_points - trail_offset)
                sl_underlying = max(float(sl_underlying), new_sl) if (sl_underlying is not None and signal_side == "long") else min(float(sl_underlying), new_sl) if sl_underlying is not None else new_sl
                active_trade["sl_underlying"] = sl_underlying

        adx_hist = active_trade.setdefault("regime_adx_hist", [])
        vol_hist = active_trade.setdefault("regime_vol_hist", [])
        if adx > 0:
            adx_hist.append(adx)
        if atr_normalized > 0:
            vol_hist.append(atr_normalized)
        if len(adx_hist) > 50:
            del adx_hist[:-50]
        if len(vol_hist) > 50:
            del vol_hist[:-50]

        hit_stop = (price_close <= float(sl_underlying)) if (sl_underlying is not None and signal_side == "long") else (price_close >= float(sl_underlying)) if sl_underlying is not None else False

        prev_price, prev_ema10, prev_ema51 = active_trade.get("last_price_close"), active_trade.get("last_ema10"), active_trade.get("last_ema51")
        prev_ema51_simple = active_trade.get("last_ema51_simple", prev_ema51)
        prev_cvd, prev_cvd_ema10, prev_cvd_ema51 = active_trade.get("last_cvd_close"), active_trade.get("last_cvd_ema10"), active_trade.get("last_cvd_ema51")
        prev_cvd_ema51_simple = active_trade.get("last_cvd_ema51_simple", prev_cvd_ema51)
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
        elif strategy_type != "atr_reversal" and giveback_enabled_for_strategy and effective_giveback_points > 0 and max_favorable_points and (max_favorable_points - favorable_move) >= effective_giveback_points:
            exit_reason = "AUTO_MAX_PROFIT_GIVEBACK"

        trend_mode_eligible = strategy_type in dynamic_exit_trend_following_strategies
        stacked_active = bool(active_trade.get("stack_layers") or active_trade.get("stacked_tradingsymbols"))
        adx_slope = (adx_hist[-1] - adx_hist[-2]) if len(adx_hist) >= 2 else 0.0
        vol_slope = (vol_hist[-1] - vol_hist[-2]) if len(vol_hist) >= 2 else 0.0
        unlock_profit_buffer = max(stoploss_points or 0.0, 1.0)

        trend_exit_adx_min = _to_finite_float(active_trade.get("trend_exit_adx_min"), 28.0)
        trend_exit_atr_ratio_min = _to_finite_float(active_trade.get("trend_exit_atr_ratio_min"), 1.15)
        trend_exit_confirm_bars = max(1, int(_to_finite_float(active_trade.get("trend_exit_confirm_bars"), 3)))
        trend_exit_min_profit = _to_finite_float(active_trade.get("trend_exit_min_profit"), 0.0)

        # Min profit floor: if user set > 0, use that; otherwise fall back to stoploss (legacy)
        effective_min_profit = trend_exit_min_profit if trend_exit_min_profit > 0 else unlock_profit_buffer

        # Consecutive bars both ADX and vol must have been rising (uses confirm_bars as window)
        confirm_window = max(2, trend_exit_confirm_bars)
        adx_hist_ok = (
            len(adx_hist) >= confirm_window
            and all(adx_hist[-i] > adx_hist[-i - 1] for i in range(1, confirm_window))
        )
        vol_hist_ok = (
            len(vol_hist) >= confirm_window
            and all(vol_hist[-i] > vol_hist[-i - 1] for i in range(1, confirm_window))
        )

        trend_unlock = (
            trend_mode_eligible
            and not stacked_active
            and favorable_move >= effective_min_profit
            and adx >= trend_exit_adx_min
            and atr_normalized >= trend_exit_atr_ratio_min
            and adx_slope > 0
            and vol_slope > 0
            and adx_hist_ok
            and vol_hist_ok
        )
        unlock_bar_count = int(active_trade.get("trend_mode_unlock_bar_count") or 0)
        if trend_unlock:
            unlock_bar_count += 1
        else:
            unlock_bar_count = 0
            if active_trade.get("exit_mode") == "trend_unlock":
                active_trade["exit_mode"] = "default"
                active_trade["trend_mode_peak_vol"] = 0.0
        active_trade["trend_mode_unlock_bar_count"] = unlock_bar_count
        if unlock_bar_count >= trend_exit_confirm_bars and active_trade.get("exit_mode") != "trend_unlock":
            active_trade["exit_mode"] = "trend_unlock"
            active_trade["trend_mode_peak_vol"] = atr_normalized
            logger.info("[AUTO] Exit mode switched to trend_unlock token=%s strategy=%s", token, strategy_type)

        if active_trade.get("exit_mode") == "trend_unlock":
            active_trade["trend_mode_peak_vol"] = max(float(active_trade.get("trend_mode_peak_vol") or 0.0), atr_normalized)
            if self._is_regime_breakdown(active_trade):
                exit_reason = "AUTO_REGIME_BREAKDOWN"

        use_default_ema_exits = active_trade.get("exit_mode") != "trend_unlock"

        ema_cross_exit_long = cvd_cross_below_ema10 or (regime_is_chop and (price_cross_below_ema10 or price_close < ema10))
        ema_cross_exit_short = cvd_cross_above_ema10 or (regime_is_chop and (price_cross_above_ema10 or price_close > ema10))

        if not exit_reason and strategy_type == "ema_cross" and use_default_ema_exits and (
                (signal_side == "long" and ema_cross_exit_long)
                or (signal_side == "short" and ema_cross_exit_short)
        ):
            exit_reason = "AUTO_EMA10_CROSS_CHOP" if regime_is_chop else "AUTO_EMA10_CROSS"
        elif not exit_reason and strategy_type == "atr_divergence" and ((signal_side == "long" and price_cross_above_ema51) or (signal_side == "short" and price_cross_below_ema51)):
            exit_reason = "AUTO_EMA51_CROSS"
        elif not exit_reason and strategy_type in {"range_breakout", "cvd_range_breakout"} and use_default_ema_exits and ((signal_side == "long" and (price_cross_below_ema10 or price_cross_below_ema51)) or (signal_side == "short" and (price_cross_above_ema10 or price_cross_above_ema51))):
            exit_reason = "AUTO_BREAKOUT_EXIT"
        elif not exit_reason and strategy_type == "open_drive" and use_default_ema_exits and (
                (signal_side == "long" and (price_close < ema10 or cvd_close < cvd_ema10))
                or (signal_side == "short" and (price_close > ema10 or cvd_close > cvd_ema10))
        ):
            exit_reason = "AUTO_OPEN_DRIVE_FAST_EMA_CLOSE_EXIT"
        elif not exit_reason and strategy_type == "atr_reversal" and (
                (signal_side == "long" and ((ema51_simple > 0 and price_close >= ema51_simple) or (cvd_ema51_simple != 0 and cvd_close >= cvd_ema51_simple)))
                or (signal_side == "short" and ((ema51_simple > 0 and price_close <= ema51_simple) or (cvd_ema51_simple != 0 and cvd_close <= cvd_ema51_simple)))
        ):
            logger.debug(
                "[AUTO] ATR reversal target hit at EMA51: side=%s price=%.2f ema51=%.2f cvd=%.2f cvd_ema51=%.2f",
                signal_side,
                price_close,
                ema51,
                cvd_close,
                cvd_ema51,
            )
            exit_reason = "AUTO_ATR_REVERSAL_EXIT"

        if exit_reason:
            self._exit_anchor_and_stack_layers(token=token, w=w, reason=exit_reason, positions=positions)
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
            "last_ema51_simple": ema51_simple,
            "last_cvd_close": cvd_close,
            "last_cvd_ema10": cvd_ema10,
            "last_cvd_ema51": cvd_ema51,
            "last_cvd_ema51_simple": cvd_ema51_simple,
        })

    def is_cutoff_reached(self, token: int | None = None) -> bool:
        _, cutoff_time = self._automation_window_for_token(token)
        return datetime.now().time() >= cutoff_time

    def enforce_cutoff_exit(self, reason: str = "AUTO_3PM_CUTOFF", token: int | None = None):
        w = self.main_window
        if not self.positions:
            return
        tokens_to_exit = [token] if token is not None else list(self.positions.keys())
        for token in tokens_to_exit:
            active_trade = self.positions.get(token)
            if not active_trade:
                continue
            self._exit_anchor_and_stack_layers(token=token, w=w, reason=reason)
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
                if "stack_layers" not in trade:
                    legacy_stacked = trade.get("stacked_tradingsymbols", []) or []
                    trade["stack_layers"] = [
                        {
                            "stack_number": idx + 1,
                            "layer_tag": f"STACK_{idx + 1}",
                            "tradingsymbols": [sym],
                            "qty_per_symbol": int(trade.get("quantity") or 0),
                            "entry_price": 0.0,
                        }
                        for idx, sym in enumerate(legacy_stacked)
                        if sym
                    ]
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
        if active_trade.get("strategy_type") != "open_drive" and tracked_symbol and w._has_pending_order_for_symbol(tracked_symbol):
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
            if active_trade.get("strategy_type") != "open_drive" and w._has_pending_order_for_symbol(tradingsymbol):
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

    def _exit_anchor_and_stack_layers(self, token: int, w, reason: str, positions: list[Position] | None = None):
        active_trade = self.positions.get(token)
        if not active_trade:
            return

        exited_symbols: set[str] = set()
        if positions is not None:
            for pos in positions:
                exited_symbols.add(pos.tradingsymbol)
                self.exit_position_automated(pos, reason=reason)
        else:
            for symbol in [s for s in (active_trade.get("tradingsymbols") or [active_trade.get("tradingsymbol")]) if s]:
                position = w.position_manager.get_position(symbol)
                if position:
                    exited_symbols.add(symbol)
                    self.exit_position_automated(position, reason=reason)

        for layer in active_trade.get("stack_layers", []):
            layer_tag = layer.get("layer_tag", "STACK")
            for sym in layer.get("tradingsymbols", []):
                if sym in exited_symbols:
                    continue
                stk_pos = w.position_manager.get_position(sym)
                if stk_pos:
                    self.exit_position_automated(stk_pos, reason=f"{reason}_CLEANUP_{layer_tag}")
                    logger.info("[STACKER] Exiting remaining stack on anchor exit: %s (%s)", sym, layer_tag)

    def _exit_partial_qty(self, w, tradingsymbol: str, qty_to_exit: int, reason: str):
        if qty_to_exit <= 0:
            logger.warning("[STACKER] Invalid partial unwind qty=%s for %s", qty_to_exit, tradingsymbol)
            return
        try:
            order_id = w.execution_service.exit_methods.exit_partial_qty(
                tradingsymbol=tradingsymbol,
                qty_to_exit=qty_to_exit,
                reason=reason,
            )
            if order_id:
                w._refresh_positions()
        except Exception as exc:
            logger.error("[STACKER] Partial unwind failed for %s: %s", tradingsymbol, exc, exc_info=True)

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
