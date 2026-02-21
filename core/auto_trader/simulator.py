import numpy as np
import pyqtgraph as pg
from core.auto_trader.indicators import calculate_ema, is_chop_regime



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
            len(self.all_price_data), len(self.all_cvd_data),
            len(self.all_price_high_data), len(self.all_price_low_data),
            len(self.all_timestamps),
        )
        if length <= 0:
            return {
                "taken_long_x": [], "taken_long_y": [], "taken_short_x": [], "taken_short_y": [],
                "exit_win_x": [], "exit_win_y": [], "exit_loss_x": [], "exit_loss_y": [],
                "skipped_x": [], "skipped_y": [], "total_points": 0.0,
                "trades": 0, "wins": 0, "losses": 0, "skipped": 0,
            }

        x_arr = np.array(x_arr[:length], dtype=float)
        short_mask = np.array(short_mask[:length], dtype=bool)
        long_mask = np.array(long_mask[:length], dtype=bool)
        close = np.array(self.all_price_data[:length], dtype=float)
        high = np.array(self.all_price_high_data[:length], dtype=float)
        low = np.array(self.all_price_low_data[:length], dtype=float)
        cvd_close = np.array(self.all_cvd_data[:length], dtype=float)

        ema10 = calculate_ema(close, 10)
        ema51 = calculate_ema(close, 51)
        cvd_ema10 = calculate_ema(cvd_close, 10)
        cvd_ema51 = calculate_ema(cvd_close, 51)

        stop_points = float(max(0.1, self.automation_stoploss_input.value()))
        atr_trailing_step_points = 10.0
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
            "skipped_x": [], "skipped_y": [], "total_points": 0.0,
            "trades": 0, "wins": 0, "losses": 0, "skipped": 0,
        }

        active_trade = None
        stack_window_minutes = 15.0
        y_offset = np.maximum((high - low) * 0.2, 1.0)

        def _close_trade(idx: int):
            nonlocal active_trade
            if not active_trade:
                return
            exit_price = float(close[idx])
            if not np.isfinite(exit_price):
                exit_price = float(active_trade["entry_price"])
            pnl = exit_price - active_trade["entry_price"] if active_trade["signal_side"] == "long" else active_trade["entry_price"] - exit_price
            result["total_points"] += float(pnl)
            if pnl > 0:
                result["wins"] += 1
                result["exit_win_x"].append(float(x_arr[idx]))
                result["exit_win_y"].append(exit_price)
            else:
                result["losses"] += 1
                result["exit_loss_x"].append(float(x_arr[idx]))
                result["exit_loss_y"].append(exit_price)
            active_trade = None

        for idx in range(length):
            ts = self.all_timestamps[idx]
            if ts.time() >= time(15, 0):
                if active_trade:
                    _close_trade(idx)
                continue

            if active_trade:
                price_close = close[idx]
                if not np.isfinite(price_close):
                    continue
                signal_side = active_trade["signal_side"]
                sl_underlying = active_trade["sl_underlying"]

                favorable_move = (
                    price_close - active_trade["entry_price"]
                    if signal_side == "long"
                    else active_trade["entry_price"] - price_close
                )

                if not np.isfinite(favorable_move):
                    favorable_move = 0.0

                trail_offset = 0.0
                if active_trade.get("strategy_type") == "atr_reversal":
                    if atr_trailing_step_points > 0:
                        trail_steps = int(max(0.0, favorable_move) // atr_trailing_step_points)
                        if trail_steps > 0:
                            trail_offset = trail_steps * atr_trailing_step_points
                elif active_trade.get("strategy_type") in {"ema_cross", "range_breakout"}:
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
                exit_now = False
                if hit_stop:
                    exit_now = True
                elif active_strategy_type == "ema_cross":
                    exit_now = (signal_side == "long" and cvd_cross_below_ema10) or (signal_side == "short" and cvd_cross_above_ema10)
                elif active_strategy_type == "atr_divergence":
                    exit_now = (signal_side == "long" and price_cross_above_ema51) or (signal_side == "short" and price_cross_below_ema51)
                elif active_strategy_type == "range_breakout":
                    exit_now = (signal_side == "long" and (price_cross_below_ema10 or price_cross_below_ema51)) or (signal_side == "short" and (price_cross_above_ema10 or price_cross_above_ema51))
                else:
                    exit_now = (signal_side == "long" and (price_cross_above_ema51 or cvd_cross_above_ema51)) or (signal_side == "short" and (price_cross_below_ema51 or cvd_cross_below_ema51))

                if exit_now:
                    _close_trade(idx)

                if active_trade:
                    active_trade["last_price_close"] = float(close[idx])
                    active_trade["last_ema10"] = float(ema10[idx])
                    active_trade["last_ema51"] = float(ema51[idx])
                    active_trade["last_cvd_close"] = float(cvd_close[idx])
                    active_trade["last_cvd_ema10"] = float(cvd_ema10[idx])
                    active_trade["last_cvd_ema51"] = float(cvd_ema51[idx])

            signal_side, signal_strategy = self._resolve_signal_side_and_strategy(
                idx=idx,
                short_mask=short_mask,
                long_mask=long_mask,
                strategy_masks=strategy_masks,
            )
            if signal_side is None:
                continue

            if ts.time() < time(9, 20) or ts.time() >= time(15, 0):
                continue

            if signal_strategy is None:
                signal_strategy = "atr_reversal"

            if is_chop_regime(self, idx, strategy_type=signal_strategy):
                result["skipped"] += 1
                result["skipped_x"].append(float(x_arr[idx]))
                result["skipped_y"].append(float((high[idx] + y_offset[idx]) if signal_side == "short" else (low[idx] - y_offset[idx])))
                continue

            if active_trade:
                if active_trade["signal_side"] == signal_side:
                    last_signal_time = active_trade.get("signal_timestamp")
                    elapsed_min = 0.0
                    if last_signal_time:
                        elapsed_min = (ts - last_signal_time).total_seconds() / 60.0
                    if elapsed_min < stack_window_minutes:
                        result["skipped"] += 1
                        result["skipped_x"].append(float(x_arr[idx]))
                        result["skipped_y"].append(float((high[idx] + y_offset[idx]) if signal_side == "short" else (low[idx] - y_offset[idx])))
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
                            _close_trade(idx)
                            # Fall through to entry below
                        else:
                            result["skipped"] += 1
                            result["skipped_x"].append(float(x_arr[idx]))
                            result["skipped_y"].append(float((high[idx] + y_offset[idx]) if signal_side == "short" else (low[idx] - y_offset[idx])))
                            continue
                    # ──────────────────────────────────────────────────────────
                    elif new_priority <= active_priority:
                        result["skipped"] += 1
                        result["skipped_x"].append(float(x_arr[idx]))
                        result["skipped_y"].append(float((high[idx] + y_offset[idx]) if signal_side == "short" else (low[idx] - y_offset[idx])))
                        continue
                    else:
                        _close_trade(idx)

            entry_price = float(close[idx])
            if not np.isfinite(entry_price):
                continue
            sl_underlying = entry_price - stop_points if signal_side == "long" else entry_price + stop_points
            active_trade = {
                "signal_side": signal_side,
                "signal_timestamp": ts,
                "strategy_type": signal_strategy,
                "entry_price": entry_price,
                "entry_bar_idx": idx,      # used by ATR skip counter
                "atr_skip_count": 0,       # fallback counter if raw masks unavailable
                "sl_underlying": sl_underlying,
                "last_price_close": entry_price,
                "last_ema10": float(ema10[idx]),
                "last_ema51": float(ema51[idx]),
                "last_cvd_close": float(cvd_close[idx]),
                "last_cvd_ema10": float(cvd_ema10[idx]),
                "last_cvd_ema51": float(cvd_ema51[idx]),
            }

            if signal_side == "long":
                result["taken_long_x"].append(float(x_arr[idx]))
                result["taken_long_y"].append(entry_price)
            else:
                result["taken_short_x"].append(float(x_arr[idx]))
                result["taken_short_y"].append(entry_price)
            result["trades"] += 1

            if active_trade:
                active_trade["last_price_close"] = float(close[idx])
                active_trade["last_ema10"] = float(ema10[idx])
                active_trade["last_ema51"] = float(ema51[idx])
                active_trade["last_cvd_close"] = float(cvd_close[idx])
                active_trade["last_cvd_ema10"] = float(cvd_ema10[idx])
                active_trade["last_cvd_ema51"] = float(cvd_ema51[idx])

        if active_trade:
            _close_trade(length - 1)

        return result


