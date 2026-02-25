import numpy as np
import logging
import pyqtgraph as pg
from datetime import time
from copy import deepcopy
from core.auto_trader.indicators import (
    calculate_ema,
    calculate_atr,
    compute_adx,
    is_chop_regime,
    calculate_regime_trend_filter,
)
from core.auto_trader.regime_engine import RegimeEngine
from core.auto_trader.stacker import StackerState


logger = logging.getLogger(__name__)


class SimulatorMixin:
    @staticmethod
    def _sim_market_context(
            idx: int,
            close: np.ndarray,
            ema51: np.ndarray,
            atr_full: np.ndarray,
            adx_full: np.ndarray,
    ) -> dict:
        """
        Build market context from the *previous* completed bar to avoid using
        the current bar's trend/volatility for signal handling.
        """
        snapshot_idx = max(0, idx - 1)
        snapshot_price = float(close[snapshot_idx]) if snapshot_idx < len(close) and np.isfinite(close[snapshot_idx]) else 0.0
        ema51_snapshot = float(ema51[snapshot_idx]) if snapshot_idx < len(ema51) and np.isfinite(ema51[snapshot_idx]) else 0.0
        atr_snapshot = float(atr_full[snapshot_idx]) if snapshot_idx < len(atr_full) and np.isfinite(atr_full[snapshot_idx]) else 0.0
        adx_snapshot = float(adx_full[snapshot_idx]) if snapshot_idx < len(adx_full) and np.isfinite(adx_full[snapshot_idx]) else 0.0
        atr_pct_snapshot = (atr_snapshot / snapshot_price) * 100.0 if snapshot_price > 0 and atr_snapshot > 0 else 0.0

        trend = "neutral"
        if snapshot_price > 0 and ema51_snapshot > 0:
            trend = "up" if snapshot_price >= ema51_snapshot else "down"

        return {
            "snapshot_idx": snapshot_idx,
            "price": snapshot_price,
            "ema51": ema51_snapshot,
            "atr": atr_snapshot,
            "adx": adx_snapshot,
            "atr_pct": atr_pct_snapshot,
            "trend": trend,
        }



    def _on_simulator_run_clicked(self):
        x_arr = getattr(self, "_latest_sim_x_arr", None)
        short_mask = getattr(self, "_latest_sim_short_mask", None)
        long_mask = getattr(self, "_latest_sim_long_mask", None)
        strategy_masks = getattr(self, "_latest_sim_strategy_masks", None)
        if x_arr is None or short_mask is None or long_mask is None:
            self._set_simulator_summary_text("Simulator: no plotted signals yet", "#FFA726")
            return
        self._update_simulator_overlay(
            x_arr=x_arr,
            short_mask=short_mask,
            long_mask=long_mask,
            strategy_masks=strategy_masks,
        )



    def _clear_simulation_markers(self):
        for marker in (
                self.sim_taken_long_markers,
                self.sim_taken_short_markers,
                self.sim_exit_win_markers,
                self.sim_exit_loss_markers,
                self.sim_skipped_markers,
        ):
            marker.clear()
        self.sim_trade_path_lines.clear()
        self._reset_simulator_confluence_line_styles()



    def _reset_simulator_confluence_line_styles(self):
        line_map = getattr(self, "_confluence_line_map", {})
        if not line_map:
            return

        for key, pairs in line_map.items():
            is_short = str(key).startswith("S:")
            color = self._confluence_short_color if is_short else self._confluence_long_color
            pen = pg.mkPen(color, width=self._confluence_line_width)
            for _, line in pairs:
                line.setPen(pen)
                line.setOpacity(self._confluence_line_opacity)



    def _apply_simulator_confluence_line_styles(self, skipped_line_keys: set[str] | None):
        line_map = getattr(self, "_confluence_line_map", {})
        if not line_map:
            return

        skipped_line_keys = skipped_line_keys or set()
        skipped_pen = pg.mkPen("#CFD8DC", width=self._confluence_line_width)

        for key, pairs in line_map.items():
            is_short = str(key).startswith("S:")
            default_color = self._confluence_short_color if is_short else self._confluence_long_color
            pen = skipped_pen if key in skipped_line_keys else pg.mkPen(default_color, width=self._confluence_line_width)
            for _, line in pairs:
                line.setPen(pen)
                line.setOpacity(self._confluence_line_opacity)



    def _set_simulator_summary_text(self, text: str, color: str = "#8A9BA8"):
        self.simulator_summary_label.setText(text)
        self.simulator_summary_label.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600;"
        )



    def _strategy_priority(self, strategy_type: str) -> int:
        _, priorities = self._active_strategy_priorities()
        return int(priorities.get(strategy_type or "", 0))



    def _resolve_side_strategy_from_masks(
            self,
            idx: int,
            side: str,
            strategy_masks: dict | None,
            allowed_strategies: dict[str, bool] | None = None,
    ) -> str | None:
        if not strategy_masks:
            return None

        side_masks = strategy_masks.get(side, {})
        _, priorities = self._active_strategy_priorities()
        ordered_strategies = sorted(
            priorities.keys(),
            key=lambda strategy_key: priorities.get(strategy_key, 0),
        )
        for strategy_type in ordered_strategies:
            if allowed_strategies is not None and not allowed_strategies.get(strategy_type, True):
                continue
            mask = side_masks.get(strategy_type)
            if mask is not None and idx < len(mask) and bool(mask[idx]):
                return strategy_type
        return None



    def _resolve_signal_side_and_strategy(
            self,
            idx: int,
            short_mask: np.ndarray,
            long_mask: np.ndarray,
            strategy_masks: dict | None,
            allowed_strategies: dict[str, bool] | None = None,
    ) -> tuple[str | None, str | None]:
        short_strategy = self._resolve_side_strategy_from_masks(idx, "short", strategy_masks, allowed_strategies)
        long_strategy = self._resolve_side_strategy_from_masks(idx, "long", strategy_masks, allowed_strategies)
        candidate_short = short_strategy is not None
        candidate_long = long_strategy is not None

        if candidate_short and candidate_long:
            short_priority = self._strategy_priority(short_strategy)
            long_priority = self._strategy_priority(long_strategy)

            if short_priority == long_priority:
                return None, None
            return ("short", short_strategy) if short_priority < long_priority else ("long", long_strategy)

        if candidate_short:
            return "short", short_strategy
        if candidate_long:
            return "long", long_strategy
        return None, None



    def _update_simulator_overlay(
            self,
            x_arr: np.ndarray,
            short_mask: np.ndarray,
            long_mask: np.ndarray,
            strategy_masks: dict | None = None,
    ):
        if not len(x_arr) or not self.all_price_data:
            self._simulator_results = None
            self._clear_simulation_markers()
            self._set_simulator_summary_text("Simulator: waiting for data", "#FFA726")
            return

        results = self._run_trade_simulation(
            x_arr=x_arr,
            short_mask=short_mask,
            long_mask=long_mask,
            strategy_masks=strategy_masks,
        )
        self._simulator_results = results

        self.sim_taken_long_markers.setData(results["taken_long_x"], results["taken_long_y"])
        self.sim_taken_short_markers.setData(results["taken_short_x"], results["taken_short_y"])
        self.sim_exit_win_markers.setData(results["exit_win_x"], results["exit_win_y"])
        self.sim_exit_loss_markers.setData(results["exit_loss_x"], results["exit_loss_y"])
        self.sim_skipped_markers.setData(results["skipped_x"], results["skipped_y"])
        self.sim_trade_path_lines.setData(results["trade_path_x"], results["trade_path_y"])
        self._apply_simulator_confluence_line_styles(results.get("skipped_line_keys"))

        points = results["total_points"]
        color = "#66BB6A" if points >= 0 else "#EF5350"
        stacker_part = ""
        if results.get("stacked_positions", 0) > 0:
            ul = results.get("unwind_losses", 0)
            upts = results.get("unwind_points_lost", 0.0)
            sw = results.get("stack_exit_wins", 0)
            sl = results.get("stack_exit_losses", 0)
            # Unwinds = LIFO exits when market reversed through entry price (always losses/BE)
            # Stack exits = stacks closed profitably with anchor signal
            stacker_part = f" | Unwinds {ul}L ({upts:+.2f}pts) | StackExit {sw}W/{sl}L"
        summary = (
            f"Sim: Trades {results['trades']} | Skipped {results['skipped']} | "
            f"Wins {results['wins']} / Losses {results['losses']}{stacker_part} | "
            f"Pts {points:+.2f} (incl. stacks)"
        )
        self._set_simulator_summary_text(summary, color)



    def _run_trade_simulation(
            self,
            x_arr: np.ndarray,
            short_mask: np.ndarray,
            long_mask: np.ndarray,
            strategy_masks: dict | None = None,
    ) -> dict:
        length = min(
            len(x_arr), len(short_mask), len(long_mask),
            len(self.all_price_data), len(self.all_cvd_data),
            len(self.all_price_high_data), len(self.all_price_low_data),
            len(self.all_timestamps),
        )
        if length <= 0:
            return {
                "taken_long_x": [], "taken_long_y": [], "taken_short_x": [], "taken_short_y": [],
                "exit_win_x": [], "exit_win_y": [], "exit_loss_x": [], "exit_loss_y": [],
                "skipped_x": [], "skipped_y": [], "trade_path_x": [], "trade_path_y": [],
                "skipped_line_keys": set(), "total_points": 0.0,
                "trades": 0, "wins": 0, "losses": 0, "skipped": 0,
            }

        x_arr = np.array(x_arr[:length], dtype=float)
        short_mask = np.array(short_mask[:length], dtype=bool)
        long_mask = np.array(long_mask[:length], dtype=bool)
        close = np.array(self.all_price_data[:length], dtype=float)
        high = np.array(self.all_price_high_data[:length], dtype=float)
        low = np.array(self.all_price_low_data[:length], dtype=float)
        cvd_close = np.array(self.all_cvd_data[:length], dtype=float)

        price_fast_filter, _ = calculate_regime_trend_filter(close)
        cvd_fast_filter, _ = calculate_regime_trend_filter(cvd_close)
        ema10 = price_fast_filter
        ema51 = calculate_ema(close, 51)
        cvd_ema10 = cvd_fast_filter
        cvd_ema51 = calculate_ema(cvd_close, 51)

        # Pre-compute ATR and ADX once for the full array (used by is_chop_regime)
        price_high_full = np.array(self.all_price_high_data[:length], dtype=float)
        price_low_full = np.array(self.all_price_low_data[:length], dtype=float)
        atr_full = calculate_atr(price_high_full, price_low_full, close, 14)
        adx_full = compute_adx(price_high_full, price_low_full, close, 14)
        regime_enabled = bool(
            getattr(self, "regime_enabled_check", None)
            and self.regime_enabled_check.isChecked()
            and getattr(self, "regime_engine", None) is not None
        )
        sim_regime_engine = None
        if regime_enabled:
            sim_regime_engine = RegimeEngine(config=deepcopy(self.regime_engine.config))

        stop_points = float(max(0.1, self.automation_stoploss_input.value()))
        max_profit_giveback_points = float(max(0.0, self.max_profit_giveback_input.value()))
        open_drive_max_profit_giveback_points = float(max(0.0, getattr(self, "open_drive_max_profit_giveback_input", None).value() if getattr(self, "open_drive_max_profit_giveback_input", None) is not None else 0.0))
        max_profit_giveback_strategies = set(self._selected_max_giveback_strategies())
        atr_skip_limit = int(getattr(self, "atr_skip_limit_input", None) and
                             self.atr_skip_limit_input.value() or 0)

        # Pre-extract raw ATR masks for skip counting (may be None if not available)
        _sm = strategy_masks or {}
        short_atr_raw = _sm.get("short", {}).get("atr_reversal_raw")
        long_atr_raw = _sm.get("long", {}).get("atr_reversal_raw")
        if short_atr_raw is not None:
            short_atr_raw = np.array(short_atr_raw[:length], dtype=bool)
        if long_atr_raw is not None:
            long_atr_raw = np.array(long_atr_raw[:length], dtype=bool)

        result = {
            "taken_long_x": [], "taken_long_y": [], "taken_short_x": [], "taken_short_y": [],
            "exit_win_x": [], "exit_win_y": [], "exit_loss_x": [], "exit_loss_y": [],
            "skipped_x": [], "skipped_y": [], "trade_path_x": [], "trade_path_y": [],
            "skipped_line_keys": set(), "total_points": 0.0,
            "trades": 0, "wins": 0, "losses": 0, "skipped": 0,
            "stacked_positions": 0, "stacked_unwinds": 0,
            "unwind_wins": 0, "unwind_losses": 0, "unwind_points_lost": 0.0,
            "stack_exit_wins": 0, "stack_exit_losses": 0,
        }

        stacker_enabled = bool(
            getattr(self, "stacker_enabled_check", None)
            and self.stacker_enabled_check.isChecked()
        )
        open_drive_stack_enabled = bool(
            getattr(self, "open_drive_stack_enabled_check", None)
            and self.open_drive_stack_enabled_check.isChecked()
        )
        stacker_step = float(self.stacker_step_input.value()) \
            if hasattr(self, "stacker_step_input") else 20.0
        stacker_max = int(self.stacker_max_input.value()) \
            if hasattr(self, "stacker_max_input") else 2

        active_trade = None
        sim_stacker: StackerState | None = None
        stack_window_minutes = 15.0
        y_offset = np.maximum((high - low) * 0.2, 1.0)

        def _log_signal_event(idx: int, event: str, side: str | None, strategy: str | None, reason: str):
            ctx = self._sim_market_context(idx, close, ema51, atr_full, adx_full)
            logger.debug(
                "[SIM %s] idx=%d ts=%s side=%s strategy=%s reason=%s trend(prev)=%s adx(prev)=%.2f atr(prev)=%.2f atr%%(prev)=%.4f",
                event,
                idx,
                self.all_timestamps[idx],
                side or "none",
                strategy or "none",
                reason,
                ctx["trend"],
                ctx["adx"],
                ctx["atr"],
                ctx["atr_pct"],
            )

        def _close_trade(idx: int, reason: str = "rule"):
            nonlocal active_trade, sim_stacker
            if not active_trade:
                return
            exit_price = float(close[idx])
            if not np.isfinite(exit_price):
                exit_price = float(active_trade["entry_price"])

            trade_snapshot = dict(active_trade)
            # ── Always derive side from the trade being closed, never from the
            # outer loop's signal_side which may have been overwritten by the
            # incoming opposite signal that triggered this close.
            closed_side = trade_snapshot["signal_side"]
            market_ctx = self._sim_market_context(idx, close, ema51, atr_full, adx_full)

            # ── Anchor P&L (always) ──────────────────────────────────────────
            anchor_pnl = (
                exit_price - trade_snapshot["entry_price"]
                if closed_side == "long"
                else trade_snapshot["entry_price"] - exit_price
            )

            # ── Stack P&L: remaining stacks exit with anchor ─────────────────
            # Track each surviving stack's win/loss individually so the summary
            # shows "Stack exits W|L" separate from LIFO unwinds.
            if sim_stacker is not None and sim_stacker.stack_entries:
                for stk in sim_stacker.stack_entries:
                    stk_pnl = (
                        exit_price - stk.entry_price
                        if closed_side == "long"
                        else stk.entry_price - exit_price
                    )
                    result["total_points"] += float(stk_pnl)
                    if stk_pnl > 0:
                        result["stack_exit_wins"] += 1
                    elif stk_pnl < 0:
                        result["stack_exit_losses"] += 1
                    else:
                        result["stack_exit_losses"] += 1  # breakeven counts as loss/neutral

            result["total_points"] += float(anchor_pnl)
            result["trade_path_x"].extend([float(x_arr[trade_snapshot["entry_bar_idx"]]), float(x_arr[idx])])
            result["trade_path_y"].extend([float(trade_snapshot["entry_price"]), exit_price])
            if anchor_pnl > 0:
                result["wins"] += 1
                result["exit_win_x"].append(float(x_arr[idx]))
                result["exit_win_y"].append(exit_price)
            else:
                result["losses"] += 1
                result["exit_loss_x"].append(float(x_arr[idx]))
                result["exit_loss_y"].append(exit_price)
            logger.debug(
                "[SIM EXIT] idx=%d ts=%s side=%s strategy=%s entry=%.2f exit=%.2f pnl=%.2f reason=%s "
                "trend(prev)=%s adx(prev)=%.2f atr(prev)=%.2f atr%%(prev)=%.4f",
                idx,
                self.all_timestamps[idx],
                closed_side,
                trade_snapshot.get("strategy_type", "unknown"),
                float(trade_snapshot.get("entry_price", exit_price)),
                exit_price,
                float(anchor_pnl),
                reason,
                market_ctx["trend"],
                market_ctx["adx"],
                market_ctx["atr"],
                market_ctx["atr_pct"],
            )
            active_trade = None
            sim_stacker = None

        def _unwind_stacks(idx: int) -> bool:
            """
            LIFO unwind: exit any stacked positions whose entry price has been
            breached by current bar close. Returns True if any were unwound.
            P&L from unwound stacks is immediately booked into result.
            Each unwound stack is counted as unwind_wins or unwind_losses.
            The anchor is untouched — it waits for its own exit signal.
            """
            nonlocal sim_stacker
            if sim_stacker is None or not sim_stacker.stack_entries:
                return False
            to_unwind = sim_stacker.stacks_to_unwind(float(close[idx]))
            if not to_unwind:
                return False
            exit_price = float(close[idx])
            for entry in to_unwind:
                stack_pnl = sim_stacker.compute_partial_pnl([entry], exit_price)
                result["total_points"] += float(stack_pnl)
                if stack_pnl > 0:
                    result["unwind_wins"] += 1
                else:
                    result["unwind_losses"] += 1
                    result["unwind_points_lost"] += float(stack_pnl)  # stack_pnl is negative here
            sim_stacker.remove_stacks(to_unwind)
            logger.debug(
                "[SIM STACKER] Unwound %d stack(s) at bar %d price=%.2f",
                len(to_unwind), idx, exit_price,
            )
            return True

        for idx in range(length):
            ts = self.all_timestamps[idx]
            if ts.time() >= time(15, 0):
                if active_trade:
                    _close_trade(idx, reason="session_close")
                continue

            if active_trade:
                price_close = close[idx]
                if not np.isfinite(price_close):
                    continue

                if sim_stacker is not None:
                    # ── LIFO UNWIND first: exit breached stacks before anything else ──
                    # Must happen before the stack-add check so we don't re-add at a
                    # level we just unwound on this same bar.
                    _did_unwind = _unwind_stacks(idx)

                    # ── Add new stacks only if no unwind happened this bar ──────────
                    # Prevents immediate re-stacking at the exact level just unwound.
                    if not _did_unwind:
                        while sim_stacker.should_add_stack(float(price_close)):
                            sim_stacker.add_stack(entry_price=float(price_close), bar_idx=idx)
                            result["stacked_positions"] += 1
                            if not sim_stacker.can_stack_more:
                                break

                signal_side = active_trade["signal_side"]  # always read from trade, not outer loop
                sl_underlying = active_trade["sl_underlying"]

                favorable_move = (
                    price_close - active_trade["entry_price"]
                    if signal_side == "long"
                    else active_trade["entry_price"] - price_close
                )

                if not np.isfinite(favorable_move):
                    favorable_move = 0.0

                max_favorable_points = max(active_trade.get("max_favorable_points", 0.0), favorable_move)
                active_trade["max_favorable_points"] = max_favorable_points

                trail_offset = 0.0
                if active_trade.get("strategy_type") == "atr_reversal":
                    # Keep ATR reversal stop-loss fixed at user configured points.
                    trail_offset = 0.0
                elif active_trade.get("strategy_type") in {"ema_cross", "range_breakout", "cvd_range_breakout", "open_drive"}:
                    initial_trigger_points = 200.0
                    incremental_trigger_points = 100.0
                    trail_step_points = 100.0
                    if favorable_move >= initial_trigger_points:
                        trail_steps = 1 + int(
                            (favorable_move - initial_trigger_points) // incremental_trigger_points
                        )
                        trail_offset = trail_steps * trail_step_points

                if trail_offset > 0:
                    new_sl = (
                        active_trade["entry_price"] - stop_points + trail_offset
                        if signal_side == "long"
                        else active_trade["entry_price"] + stop_points - trail_offset
                    )
                    if signal_side == "long":
                        sl_underlying = max(sl_underlying, new_sl)
                    else:
                        sl_underlying = min(sl_underlying, new_sl)
                    active_trade["sl_underlying"] = sl_underlying

                hit_stop = price_close <= sl_underlying if signal_side == "long" else price_close >= sl_underlying

                prev_price = active_trade.get("last_price_close")
                prev_ema10 = active_trade.get("last_ema10")
                prev_ema51 = active_trade.get("last_ema51")
                prev_cvd = active_trade.get("last_cvd_close")
                prev_cvd_ema10 = active_trade.get("last_cvd_ema10")
                prev_cvd_ema51 = active_trade.get("last_cvd_ema51")

                has_price_ema10 = all(v is not None for v in (prev_price, prev_ema10)) and ema10[idx] > 0
                has_price_ema51 = all(v is not None for v in (prev_price, prev_ema51)) and ema51[idx] > 0
                has_cvd_ema10 = all(v is not None for v in (prev_cvd, prev_cvd_ema10)) and cvd_ema10[idx] != 0
                has_cvd_ema51 = all(v is not None for v in (prev_cvd, prev_cvd_ema51)) and cvd_ema51[idx] != 0

                price_cross_above_ema10 = has_price_ema10 and prev_price <= prev_ema10 and price_close > ema10[idx]
                price_cross_below_ema10 = has_price_ema10 and prev_price >= prev_ema10 and price_close < ema10[idx]
                price_cross_above_ema51 = has_price_ema51 and prev_price <= prev_ema51 and price_close > ema51[idx]
                price_cross_below_ema51 = has_price_ema51 and prev_price >= prev_ema51 and price_close < ema51[idx]
                cvd_cross_above_ema10 = has_cvd_ema10 and prev_cvd <= prev_cvd_ema10 and cvd_close[idx] > cvd_ema10[idx]
                cvd_cross_below_ema10 = has_cvd_ema10 and prev_cvd >= prev_cvd_ema10 and cvd_close[idx] < cvd_ema10[idx]
                cvd_cross_above_ema51 = has_cvd_ema51 and prev_cvd <= prev_cvd_ema51 and cvd_close[idx] > cvd_ema51[idx]
                cvd_cross_below_ema51 = has_cvd_ema51 and prev_cvd >= prev_cvd_ema51 and cvd_close[idx] < cvd_ema51[idx]

                active_strategy_type = active_trade.get("strategy_type") or "atr_reversal"

                adx_now = float(adx_full[idx]) if idx < len(adx_full) and np.isfinite(adx_full[idx]) else 0.0
                regime_is_chop = adx_now < 20.0

                exit_now = False
                exit_reason = "none"
                if hit_stop:
                    exit_now = True
                    exit_reason = "stop_loss"
                elif active_strategy_type == "atr_reversal":
                    ema51_simple = ema51[idx]
                    cvd_ema51_simple = cvd_ema51[idx]
                    exit_now = (
                        (signal_side == "long" and ((ema51_simple > 0 and price_close >= ema51_simple) or (cvd_ema51_simple != 0 and cvd_close[idx] >= cvd_ema51_simple)))
                        or (signal_side == "short" and ((ema51_simple > 0 and price_close <= ema51_simple) or (cvd_ema51_simple != 0 and cvd_close[idx] <= cvd_ema51_simple)))
                    )
                    if exit_now:
                        exit_reason = "atr_reversal_target"
                elif active_strategy_type != "atr_reversal" and max_favorable_points > 0:
                    use_open_drive_override = (
                        active_strategy_type == "open_drive"
                        and open_drive_max_profit_giveback_points > 0
                    )
                    effective_giveback_points = (
                        open_drive_max_profit_giveback_points
                        if use_open_drive_override
                        else max_profit_giveback_points
                    )
                    giveback_enabled_for_strategy = (
                        use_open_drive_override
                        or active_strategy_type in max_profit_giveback_strategies
                    )
                    if giveback_enabled_for_strategy and effective_giveback_points > 0:
                        giveback_points = max_favorable_points - favorable_move
                        exit_now = giveback_points >= effective_giveback_points
                        if exit_now:
                            exit_reason = "max_profit_giveback"
                if (not exit_now) and active_strategy_type == "ema_cross":
                    ema_cross_exit_long = cvd_cross_below_ema10 or (regime_is_chop and (price_cross_below_ema10 or price_close < ema10[idx]))
                    ema_cross_exit_short = cvd_cross_above_ema10 or (regime_is_chop and (price_cross_above_ema10 or price_close > ema10[idx]))
                    exit_now = (signal_side == "long" and ema_cross_exit_long) or (signal_side == "short" and ema_cross_exit_short)
                    if exit_now:
                        exit_reason = "ema_cross_exit_chop" if regime_is_chop else "ema_cross_exit"
                elif (not exit_now) and active_strategy_type == "atr_divergence":
                    exit_now = (signal_side == "long" and price_cross_above_ema51) or (signal_side == "short" and price_cross_below_ema51)
                    if exit_now:
                        exit_reason = "atr_divergence_exit"
                elif (not exit_now) and active_strategy_type in {"range_breakout", "cvd_range_breakout"}:
                    exit_now = (signal_side == "long" and (price_cross_below_ema10 or price_cross_below_ema51)) or (signal_side == "short" and (price_cross_above_ema10 or price_cross_above_ema51))
                    if exit_now:
                        exit_reason = "breakout_ema_reversal"
                elif (not exit_now) and active_strategy_type == "open_drive":
                    exit_now = (
                        (signal_side == "long" and (price_close < ema10[idx] or cvd_close[idx] < cvd_ema10[idx]))
                        or (signal_side == "short" and (price_close > ema10[idx] or cvd_close[idx] > cvd_ema10[idx]))
                    )
                    if exit_now:
                        exit_reason = "open_drive_ema_exit"

                if exit_now:
                    _close_trade(idx, reason=exit_reason if exit_reason != "none" else "rule_exit")

                if active_trade:
                    active_trade["last_price_close"] = float(close[idx])
                    active_trade["last_ema10"] = float(ema10[idx])
                    active_trade["last_ema51"] = float(ema51[idx])
                    active_trade["last_ema51_simple"] = float(ema51[idx])
                    active_trade["last_cvd_close"] = float(cvd_close[idx])
                    active_trade["last_cvd_ema10"] = float(cvd_ema10[idx])
                    active_trade["last_cvd_ema51"] = float(cvd_ema51[idx])
                    active_trade["last_cvd_ema51_simple"] = float(cvd_ema51[idx])

            allowed_for_bar = None
            if sim_regime_engine is not None:
                regime_snapshot = sim_regime_engine.classify(
                    adx=adx_full[:idx + 1],
                    atr=atr_full[:idx + 1],
                    bar_time=ts,
                )
                allowed_for_bar = regime_snapshot.allowed_strategies

            signal_side, signal_strategy = self._resolve_signal_side_and_strategy(
                idx=idx,
                short_mask=short_mask,
                long_mask=long_mask,
                strategy_masks=strategy_masks,
                allowed_strategies=allowed_for_bar,
            )

            if signal_side is None and allowed_for_bar is not None:
                had_raw_signal = (idx < len(short_mask) and bool(short_mask[idx])) or (idx < len(long_mask) and bool(long_mask[idx]))
                if had_raw_signal:
                    result["skipped"] += 1
                    skip_side = "short" if idx < len(short_mask) and bool(short_mask[idx]) else "long"
                    result["skipped_x"].append(float(x_arr[idx]))
                    result["skipped_y"].append(float((high[idx] + y_offset[idx]) if skip_side == "short" else (low[idx] - y_offset[idx])))
                    result["skipped_line_keys"].add(f"{'S' if skip_side == 'short' else 'L'}:{idx}")
                    _log_signal_event(idx, "SKIP", skip_side, None, "regime_strategy_not_allowed")

            if signal_side is None:
                _log_signal_event(idx, "NO_SIGNAL", None, None, "no_strategy_match")
                continue

            open_drive_entry_time = time(
                int(getattr(self, "open_drive_time_hour_input", None).value())
                if getattr(self, "open_drive_time_hour_input", None) is not None else 9,
                int(getattr(self, "open_drive_time_minute_input", None).value())
                if getattr(self, "open_drive_time_minute_input", None) is not None else 17,
            )
            intraday_start_time = open_drive_entry_time if signal_strategy == "open_drive" else time(9, 20)
            if ts.time() < intraday_start_time or ts.time() >= time(15, 0):
                _log_signal_event(idx, "SKIP", signal_side, signal_strategy, "outside_trading_window")
                continue

            if signal_strategy is None:
                signal_strategy = "atr_reversal"

            if is_chop_regime(
                    idx=idx,
                    strategy_type=signal_strategy,
                    price=close,
                    ema_slow=ema51,
                    atr=atr_full,
                    adx=adx_full,
                    chop_filter_atr_reversal=getattr(self, "_chop_filter_atr_reversal", True),
                    chop_filter_ema_cross=getattr(self, "_chop_filter_ema_cross", True),
                    chop_filter_atr_divergence=getattr(self, "_chop_filter_atr_divergence", True),
                    chop_filter_cvd_range_breakout=getattr(self, "_chop_filter_cvd_range_breakout", False),
            ):
                result["skipped"] += 1
                result["skipped_x"].append(float(x_arr[idx]))
                result["skipped_y"].append(float((high[idx] + y_offset[idx]) if signal_side == "short" else (low[idx] - y_offset[idx])))
                result["skipped_line_keys"].add(f"{'S' if signal_side == 'short' else 'L'}:{idx}")
                _log_signal_event(idx, "SKIP", signal_side, signal_strategy, "chop_filter")
                continue

            if active_trade:
                if active_trade["signal_side"] == signal_side:
                    # In live mode we do not open a fresh anchor in the same
                    # direction while one is already active; stacker handles
                    # scaling separately. Always skip same-side re-entry.
                    last_signal_time = active_trade.get("signal_timestamp")
                    elapsed_min = 0.0
                    if last_signal_time:
                        elapsed_min = (ts - last_signal_time).total_seconds() / 60.0
                    result["skipped"] += 1
                    result["skipped_x"].append(float(x_arr[idx]))
                    result["skipped_y"].append(float((high[idx] + y_offset[idx]) if signal_side == "short" else (low[idx] - y_offset[idx])))
                    result["skipped_line_keys"].add(f"{'S' if signal_side == 'short' else 'L'}:{idx}")
                    skip_reason = "same_side_stack_window" if elapsed_min < stack_window_minutes else "same_side_position_open"
                    _log_signal_event(idx, "SKIP", signal_side, signal_strategy, skip_reason)
                    continue
                else:
                    active_strategy = active_trade.get("strategy_type")
                    active_priority = self._strategy_priority(active_strategy)
                    new_priority = self._strategy_priority(signal_strategy)

                    # ── ATR Skip Limit override ────────────────────────────────
                    # When a breakout is active and ATR reversal keeps getting
                    # suppressed, count the raw (pre-suppression) opposite-side
                    # ATR signals. Once the count hits the user limit, force close
                    # the breakout and take the ATR entry instead.
                    if (
                        atr_skip_limit > 0
                        and active_strategy == "range_breakout"
                        and signal_strategy == "atr_reversal"
                    ):
                        # Count raw ATR signals on the opposing side that fired
                        # since the breakout entry (including this bar).
                        entry_idx = active_trade.get("entry_bar_idx", idx)
                        raw_mask = short_atr_raw if signal_side == "short" else long_atr_raw
                        if raw_mask is not None:
                            skipped_count = int(np.sum(raw_mask[entry_idx:idx + 1]))
                        else:
                            skipped_count = active_trade.get("atr_skip_count", 0) + 1
                            active_trade["atr_skip_count"] = skipped_count

                        if skipped_count >= atr_skip_limit:
                            # Threshold reached — close breakout, take ATR
                            _close_trade(idx, reason="atr_skip_limit_reached")
                            _log_signal_event(idx, "OVERRIDE", signal_side, signal_strategy, f"atr_skip_limit_reached:{skipped_count}/{atr_skip_limit}")
                            # Fall through to entry below
                        else:
                            result["skipped"] += 1
                            result["skipped_x"].append(float(x_arr[idx]))
                            result["skipped_y"].append(float((high[idx] + y_offset[idx]) if signal_side == "short" else (low[idx] - y_offset[idx])))
                            result["skipped_line_keys"].add(f"{'S' if signal_side == 'short' else 'L'}:{idx}")
                            _log_signal_event(idx, "SKIP", signal_side, signal_strategy, f"atr_skip_limit_wait:{skipped_count}/{atr_skip_limit}")
                            continue
                    # ──────────────────────────────────────────────────────────
                    elif new_priority >= active_priority:
                        result["skipped"] += 1
                        result["skipped_x"].append(float(x_arr[idx]))
                        result["skipped_y"].append(float((high[idx] + y_offset[idx]) if signal_side == "short" else (low[idx] - y_offset[idx])))
                        result["skipped_line_keys"].add(f"{'S' if signal_side == 'short' else 'L'}:{idx}")
                        _log_signal_event(idx, "SKIP", signal_side, signal_strategy, f"priority_blocked:new={new_priority}>=active={active_priority}")
                        continue
                    else:
                        _close_trade(idx, reason="higher_priority_signal")
                        _log_signal_event(idx, "CLOSE", signal_side, signal_strategy, "higher_priority_signal")

            entry_price = float(close[idx])
            if not np.isfinite(entry_price):
                continue
            sl_underlying = entry_price - stop_points if signal_side == "long" else entry_price + stop_points
            active_trade = {
                "signal_side": signal_side,
                "signal_timestamp": ts,
                "strategy_type": signal_strategy,
                "entry_price": entry_price,
                "entry_atr": float(atr_full[idx]) if idx < len(atr_full) and np.isfinite(atr_full[idx]) and atr_full[idx] > 0 else 0.0,
                "max_favorable_points": 0.0,
                "entry_bar_idx": idx,      # used by ATR skip counter
                "atr_skip_count": 0,       # fallback counter if raw masks unavailable
                "sl_underlying": sl_underlying,
                "last_price_close": entry_price,
                "last_ema10": float(ema10[idx]),
                "last_ema51": float(ema51[idx]),
                "last_ema51_simple": float(ema51[idx]),
                "last_cvd_close": float(cvd_close[idx]),
                "last_cvd_ema10": float(cvd_ema10[idx]),
                "last_cvd_ema51": float(cvd_ema51[idx]),
                "last_cvd_ema51_simple": float(cvd_ema51[idx]),
            }

            stacker_allowed_for_trade = stacker_enabled and (signal_strategy != "open_drive" or open_drive_stack_enabled)
            if stacker_allowed_for_trade:
                sim_stacker = StackerState(
                    anchor_entry_price=entry_price,
                    anchor_bar_idx=idx,
                    signal_side=signal_side,
                    step_points=stacker_step,
                    max_stacks=stacker_max,
                )
            else:
                sim_stacker = None

            if signal_side == "long":
                result["taken_long_x"].append(float(x_arr[idx]))
                result["taken_long_y"].append(entry_price)
            else:
                result["taken_short_x"].append(float(x_arr[idx]))
                result["taken_short_y"].append(entry_price)
            result["trades"] += 1
            _log_signal_event(idx, "TAKE", signal_side, signal_strategy, "entry_accepted")

            if active_trade:
                active_trade["last_price_close"] = float(close[idx])
                active_trade["last_ema10"] = float(ema10[idx])
                active_trade["last_ema51"] = float(ema51[idx])
                active_trade["last_ema51_simple"] = float(ema51[idx])
                active_trade["last_cvd_close"] = float(cvd_close[idx])
                active_trade["last_cvd_ema10"] = float(cvd_ema10[idx])
                active_trade["last_cvd_ema51"] = float(cvd_ema51[idx])
                active_trade["last_cvd_ema51_simple"] = float(cvd_ema51[idx])

        if active_trade:
            _close_trade(length - 1, reason="simulation_end")

        return result