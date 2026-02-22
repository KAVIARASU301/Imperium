import numpy as np
import pyqtgraph as pg
from datetime import time
from core.auto_trader.indicators import (
    calculate_ema,
    calculate_atr,
    compute_adx,
    is_chop_regime,
    calculate_regime_trend_filter,
)
from core.auto_trader.stacker import StackerState

class SimulatorMixin:
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
        priorities = {
            "atr_reversal": 1,
            "atr_divergence": 2,
            "ema_cross": 3,
            "range_breakout": 4,
        }
        return priorities.get(strategy_type or "", 0)



    def _resolve_side_strategy_from_masks(self, idx: int, side: str, strategy_masks: dict | None) -> str | None:
        if not strategy_masks:
            return None

        side_masks = strategy_masks.get(side, {})
        # Higher-priority strategies first.
        for strategy_type in ("range_breakout", "ema_cross", "atr_divergence", "atr_reversal"):
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
    ) -> tuple[str | None, str | None]:
        candidate_short = idx < len(short_mask) and bool(short_mask[idx])
        candidate_long = idx < len(long_mask) and bool(long_mask[idx])

        if candidate_short and candidate_long:
            short_strategy = self._resolve_side_strategy_from_masks(idx, "short", strategy_masks)
            long_strategy = self._resolve_side_strategy_from_masks(idx, "long", strategy_masks)
            short_priority = self._strategy_priority(short_strategy)
            long_priority = self._strategy_priority(long_strategy)

            if short_priority == long_priority:
                return None, None
            return ("short", short_strategy) if short_priority > long_priority else ("long", long_strategy)

        if candidate_short:
            return "short", self._resolve_side_strategy_from_masks(idx, "short", strategy_masks)
        if candidate_long:
            return "long", self._resolve_side_strategy_from_masks(idx, "long", strategy_masks)
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
        summary = (
            f"Sim: Trades {results['trades']} | Skipped {results['skipped']} | "
            f"Wins {results['wins']} / Losses {results['losses']} | Pts {points:+.2f}"
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
            len(self.all_price_data), len(self.all_price_high_data),
            len(self.all_price_low_data), len(self.all_timestamps),
        )

        if length <= 0:
            return {
                "taken_long_x": [], "taken_long_y": [],
                "taken_short_x": [], "taken_short_y": [],
                "exit_win_x": [], "exit_win_y": [],
                "exit_loss_x": [], "exit_loss_y": [],
                "skipped_x": [], "skipped_y": [],
                "trade_path_x": [], "trade_path_y": [],
                "skipped_line_keys": set(),
                "total_points": 0.0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "skipped": 0,
                "stacked_positions": 0,  # ← PATCH STEP 6
            }

        close = np.array(self.all_price_data[:length], dtype=float)
        high = np.array(self.all_price_high_data[:length], dtype=float)
        low = np.array(self.all_price_low_data[:length], dtype=float)

        stop_points = float(max(0.1, self.automation_stoploss_input.value()))

        # ── PATCH STEP 2 ──────────────────────────────────────────
        stacker_enabled = getattr(self, "stacker_enabled_check", None) and \
                          self.stacker_enabled_check.isChecked()

        stacker_step = float(self.stacker_step_input.value()) \
            if hasattr(self, "stacker_step_input") else 20.0

        stacker_max = int(self.stacker_max_input.value()) \
            if hasattr(self, "stacker_max_input") else 2
        # ──────────────────────────────────────────────────────────

        result = {
            "taken_long_x": [], "taken_long_y": [],
            "taken_short_x": [], "taken_short_y": [],
            "exit_win_x": [], "exit_win_y": [],
            "exit_loss_x": [], "exit_loss_y": [],
            "skipped_x": [], "skipped_y": [],
            "trade_path_x": [], "trade_path_y": [],
            "skipped_line_keys": set(),
            "total_points": 0.0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "skipped": 0,
            "stacked_positions": 0,
        }

        active_trade = None
        sim_stacker: StackerState | None = None  # ← PATCH STEP 3

        def _close_trade(idx: int):
            nonlocal active_trade, sim_stacker

            if not active_trade:
                return

            exit_price = float(close[idx])

            if sim_stacker is not None:
                pnl = sim_stacker.compute_total_pnl(exit_price)
                result["stacked_positions"] += sim_stacker.total_positions - 1
            else:
                if active_trade["signal_side"] == "long":
                    pnl = exit_price - active_trade["entry_price"]
                else:
                    pnl = active_trade["entry_price"] - exit_price

            result["total_points"] += float(pnl)

            if pnl > 0:
                result["wins"] += 1
            else:
                result["losses"] += 1

            active_trade = None
            sim_stacker = None  # ← PATCH STEP 5 reset

        # ──────────────────────────────────────────────────────────

        for idx in range(length):

            ts = self.all_timestamps[idx]

            if active_trade:
                price_close = close[idx]

                # ── PATCH STEP 4: STACK CHECK ─────────────────────
                if sim_stacker is not None and not np.isnan(price_close):
                    while sim_stacker.should_add_stack(price_close):
                        sim_stacker.add_stack(entry_price=price_close, bar_idx=idx)
                        result["stacked_positions"] += 1
                        if not sim_stacker.can_stack_more:
                            break
                # ────────────────────────────────────────────────────

                sl = active_trade["sl_underlying"]
                hit_stop = (
                    price_close <= sl if active_trade["signal_side"] == "long"
                    else price_close >= sl
                )

                if hit_stop:
                    _close_trade(idx)

            signal_side = None
            if short_mask[idx]:
                signal_side = "short"
            elif long_mask[idx]:
                signal_side = "long"

            if signal_side is None:
                continue

            if active_trade:
                continue

            entry_price = float(close[idx])

            sl_underlying = (
                entry_price - stop_points
                if signal_side == "long"
                else entry_price + stop_points
            )

            active_trade = {
                "signal_side": signal_side,
                "entry_price": entry_price,
                "sl_underlying": sl_underlying,
            }

            # ── PATCH STEP 3: INIT STACKER ────────────────────────
            if stacker_enabled:
                sim_stacker = StackerState(
                    anchor_entry_price=entry_price,
                    anchor_bar_idx=idx,
                    signal_side=signal_side,
                    step_points=stacker_step,
                    max_stacks=stacker_max,
                )
            # ───────────────────────────────────────────────────────

            result["trades"] += 1

        if active_trade:
            _close_trade(length - 1)

        return result
