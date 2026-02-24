import logging
import numpy as np
import pyqtgraph as pg
import pandas as pd
from datetime import time

from PySide6.QtCore import QTimer

from core.auto_trader.indicators import (
    calculate_ema,
    calculate_atr,
    compute_adx,
    is_chop_regime,
    calculate_regime_trend_filter,
    calculate_vwap,
)
from core.auto_trader.stacker import StackerState

logger = logging.getLogger(__name__)



class SignalRendererMixin:
    def _on_atr_settings_changed(self, *_):
        """Recompute ATR markers from plotted data without refetching history."""
        self._update_atr_reversal_markers()
        self._persist_setup_values()



    def _update_atr_reversal_markers(self):
        """Update ATR reversal triangles using currently plotted price and CVD series."""
        has_price = getattr(self, "all_price_data", None) and self._last_plot_x_indices
        has_cvd = getattr(self, "all_cvd_data", None) and self._last_plot_x_indices

        if not has_price:
            self.price_atr_above_markers.clear()
            self.price_atr_below_markers.clear()
        if not has_cvd:
            self.cvd_atr_above_markers.clear()
            self.cvd_atr_below_markers.clear()
        if not has_price and not has_cvd:
            return

        base_ema_period = int(self.atr_base_ema_input.value())
        distance_threshold = float(self.atr_distance_input.value())
        x_arr = np.array(self._last_plot_x_indices, dtype=float)

        # ── Price markers ────────────────────────────────────────────────
        if has_price:
            price_data_array = np.array(self.all_price_data, dtype=float)
            high_data_array = np.array(self.all_price_high_data, dtype=float)
            low_data_array = np.array(self.all_price_low_data, dtype=float)

            atr_values = calculate_atr(high_data_array, low_data_array, price_data_array, period=14)
            base_ema = calculate_ema(price_data_array, base_ema_period)
            safe_atr = np.where(atr_values <= 0, np.nan, atr_values)
            distance = np.abs(price_data_array - base_ema) / safe_atr

            above_mask = (distance >= distance_threshold) & (price_data_array > base_ema)
            below_mask = (distance >= distance_threshold) & (price_data_array < base_ema)
            atr_offset = np.nan_to_num(atr_values, nan=0.0) * 0.15

            price_prev = np.concatenate(([price_data_array[0]], price_data_array[:-1]))
            ema_prev = np.concatenate(([base_ema[0]], base_ema[:-1]))
            price_cross_above_ema = (price_prev <= ema_prev) & (price_data_array > base_ema)
            price_cross_below_ema = (price_prev >= ema_prev) & (price_data_array < base_ema)

            self.price_atr_above_markers.setData(
                x_arr[above_mask],
                high_data_array[above_mask] + atr_offset[above_mask],
            )
            self.price_atr_below_markers.setData(
                x_arr[below_mask],
                low_data_array[below_mask] - atr_offset[below_mask],
            )

        # ── CVD markers — EMA 51 + configurable ATR distance + raw gap gate ──
        if has_cvd:
            CVD_ATR_EMA = 51
            cvd_atr_distance_threshold = float(self.cvd_atr_distance_input.value())

            cvd_data_array = np.array(self.all_cvd_data, dtype=float)

            if getattr(self, "all_cvd_high_data", None) and getattr(self, "all_cvd_low_data", None):
                cvd_high = np.array(self.all_cvd_high_data, dtype=float)
                cvd_low = np.array(self.all_cvd_low_data, dtype=float)
            else:
                cvd_high = cvd_data_array.copy()
                cvd_low = cvd_data_array.copy()

            atr_cvd = calculate_atr(cvd_high, cvd_low, cvd_data_array, period=14)
            base_ema_c = calculate_ema(cvd_data_array, CVD_ATR_EMA)
            safe_atr_c = np.where(atr_cvd <= 0, np.nan, atr_cvd)
            distance_c = np.abs(cvd_data_array - base_ema_c) / safe_atr_c

            # ── Extra gate: raw gap between CVD and its EMA must exceed threshold ──
            cvd_ema_gap_threshold = float(self.cvd_ema_gap_input.value())
            raw_gap_c = np.abs(cvd_data_array - base_ema_c)
            gap_mask_c = raw_gap_c > cvd_ema_gap_threshold  # BOTH conditions must hold

            above_mask_c = (distance_c >= cvd_atr_distance_threshold) & (cvd_data_array > base_ema_c) & gap_mask_c
            below_mask_c = (distance_c >= cvd_atr_distance_threshold) & (cvd_data_array < base_ema_c) & gap_mask_c
            atr_offset_c = np.nan_to_num(atr_cvd, nan=0.0) * 0.15

            # Simple EMA-side masks (no ATR distance required) — used for weak confluence
            cvd_above_ema51 = cvd_data_array > base_ema_c
            cvd_below_ema51 = cvd_data_array < base_ema_c

            self.cvd_atr_above_markers.setData(
                x_arr[above_mask_c],
                cvd_high[above_mask_c] + atr_offset_c[above_mask_c],
            )
            self.cvd_atr_below_markers.setData(
                x_arr[below_mask_c],
                cvd_low[below_mask_c] - atr_offset_c[below_mask_c],
            )

        # ── Apply ATR Marker Display Filter ──────────────────────────────
        marker_filter = self.atr_marker_filter_combo.currentData()

        if marker_filter == self.ATR_MARKER_HIDE_ALL:
            # Hide all markers
            self.price_atr_above_markers.clear()
            self.price_atr_below_markers.clear()
            self.cvd_atr_above_markers.clear()
            self.cvd_atr_below_markers.clear()
        elif marker_filter == self.ATR_MARKER_GREEN_ONLY:
            # Show only green (below) markers
            self.price_atr_above_markers.clear()
            self.cvd_atr_above_markers.clear()
        elif marker_filter == self.ATR_MARKER_RED_ONLY:
            # Show only red (above) markers
            self.price_atr_below_markers.clear()
            self.cvd_atr_below_markers.clear()
        elif marker_filter == self.ATR_MARKER_CONFLUENCE_ONLY and has_price and has_cvd:
            # Show only markers where both price and CVD have signals at same bar
            if len(above_mask) == len(above_mask_c) == len(x_arr):
                confluence_above = above_mask & above_mask_c
                confluence_below = below_mask & below_mask_c

                if has_price:
                    atr_offset = np.nan_to_num(atr_values, nan=0.0) * 0.15
                    self.price_atr_above_markers.setData(
                        x_arr[confluence_above],
                        high_data_array[confluence_above] + atr_offset[confluence_above],
                    )
                    self.price_atr_below_markers.setData(
                        x_arr[confluence_below],
                        low_data_array[confluence_below] - atr_offset[confluence_below],
                    )

                if has_cvd:
                    atr_offset_c = np.nan_to_num(atr_cvd, nan=0.0) * 0.15
                    self.cvd_atr_above_markers.setData(
                        x_arr[confluence_above],
                        cvd_high[confluence_above] + atr_offset_c[confluence_above],
                    )
                    self.cvd_atr_below_markers.setData(
                        x_arr[confluence_below],
                        cvd_low[confluence_below] - atr_offset_c[confluence_below],
                    )
        # else: ATR_MARKER_SHOW_ALL - markers already set above, do nothing

        # ── Confluence: price reversal + CVD confirmation ─────────────────
        if has_price and has_cvd:
            if len(above_mask) == len(above_mask_c) == len(x_arr):
                self._draw_confluence_lines(
                    price_above_mask=above_mask,
                    price_below_mask=below_mask,
                    price_cross_above_ema=price_cross_above_ema,
                    price_cross_below_ema=price_cross_below_ema,
                    cvd_above_mask=above_mask_c,
                    cvd_below_mask=below_mask_c,
                    cvd_above_ema51=cvd_above_ema51,
                    cvd_below_ema51=cvd_below_ema51,
                    x_arr=x_arr,
                )

        self._emit_automation_market_state()



    def _emit_automation_market_state(self):
        if not self._last_plot_x_indices or not self.all_price_data or not self.all_cvd_data:
            return

        x_arr = np.array(self._last_plot_x_indices, dtype=float)
        price_data_array = np.array(self.all_price_data, dtype=float)
        cvd_data_array = np.array(self.all_cvd_data, dtype=float)
        price_fast_filter, price_slow_filter = calculate_regime_trend_filter(price_data_array)
        cvd_fast_filter, cvd_slow_filter = calculate_regime_trend_filter(cvd_data_array)
        ema10 = price_fast_filter   # adaptive fast — replaces fixed EMA10
        ema51 = price_slow_filter   # adaptive slow (KAMA) — replaces fixed EMA51
        cvd_ema10 = cvd_fast_filter
        cvd_ema51 = cvd_slow_filter
        idx = self._latest_closed_bar_index()
        if idx is None:
            return

        # Only emit when the closed bar advances or key values change.
        # Emitting on every 3-second refresh floods _on_cvd_automation_market_state
        # which triggers position checks and potentially order placement each time.
        ts_str = self.all_timestamps[idx].isoformat() if idx < len(self.all_timestamps) else None
        new_price_close = float(price_data_array[idx])
        new_ema10 = float(ema10[idx])
        new_ema51 = float(ema51[idx])
        new_cvd_close = float(cvd_data_array[idx])
        new_cvd_ema10 = float(cvd_ema10[idx])
        new_cvd_ema51 = float(cvd_ema51[idx])
        atr_values = calculate_atr(
            np.array(self.all_price_high_data, dtype=float),
            np.array(self.all_price_low_data, dtype=float),
            price_data_array,
            period=14,
        )
        current_atr = float(atr_values[idx]) if idx < len(atr_values) else 0.0
        if not np.isfinite(current_atr) or current_atr <= 0:
            current_atr = 0.0
        state_key = (
            ts_str,
            round(new_price_close, 4),
            round(new_ema10, 4),
            round(new_ema51, 4),
            round(new_cvd_close, 4),
            round(new_cvd_ema10, 4),
            round(new_cvd_ema51, 4),
        )
        if getattr(self, "_last_emitted_state_key", None) == state_key:
            return
        self._last_emitted_state_key = state_key

        active_priority_list, strategy_priorities = self._active_strategy_priorities()
        self._log_active_priority_list_if_needed()
        self.automation_state_signal.emit({
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "enabled": self.automate_toggle.isChecked(),
            "stoploss_points": float(self.automation_stoploss_input.value()),
            "max_profit_giveback_points": float(self.max_profit_giveback_input.value()),
            "max_profit_giveback_strategies": self._selected_max_giveback_strategies(),
            "open_drive_max_profit_giveback_points": float(self.open_drive_max_profit_giveback_input.value()),
            "open_drive_tick_drawdown_limit_points": float(self.open_drive_tick_drawdown_limit_input.value()),
            "atr_trailing_step_points": float(self.atr_trailing_step_input.value()),
            "atr": current_atr,
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "signal_filter": self._selected_signal_filter(),
            "priority_list": active_priority_list,
            "strategy_priorities": strategy_priorities,
            "bar_x": float(x_arr[idx]),
            "price_close": new_price_close,
            "ema10": new_ema10,
            "ema51": new_ema51,
            "cvd_close": new_cvd_close,
            "cvd_ema10": new_cvd_ema10,
            "cvd_ema51": new_cvd_ema51,
            "timestamp": ts_str,
        })



    def _latest_closed_bar_index(self) -> int | None:
        if not self.all_timestamps:
            return None

        idx = len(self.all_timestamps) - 1
        if not self.live_mode:
            return idx

        latest_ts = pd.Timestamp(self.all_timestamps[idx])
        now_ts = (
            pd.Timestamp.now(tz=latest_ts.tz)
            if latest_ts.tz is not None
            else pd.Timestamp.now()
        )

        # In live mode, treat current minute candle as open/incomplete.
        if latest_ts.floor("min") >= now_ts.floor("min"):
            idx -= 1

        if idx < 0:
            return None
        return idx

    # ------------------------------------------------------------------



    def _on_atr_marker_filter_changed(self, *_):
        """Handle ATR marker display filter changes"""
        if hasattr(self, "setup_atr_marker_filter_combo"):
            self.setup_atr_marker_filter_combo.blockSignals(True)
            self.setup_atr_marker_filter_combo.setCurrentIndex(self.atr_marker_filter_combo.currentIndex())
            self.setup_atr_marker_filter_combo.blockSignals(False)
        self._update_atr_reversal_markers()
        self._persist_setup_values()



    def _on_setup_atr_marker_filter_changed(self, *_):
        self.atr_marker_filter_combo.blockSignals(True)
        self.atr_marker_filter_combo.setCurrentIndex(self.setup_atr_marker_filter_combo.currentIndex())
        self.atr_marker_filter_combo.blockSignals(False)
        self._update_atr_reversal_markers()
        self._persist_setup_values()



    def _clear_confluence_lines(self):
        """Remove all confluence vertical lines from both charts."""
        for line_pair in self._confluence_lines:
            for plot, line in line_pair:
                plot.removeItem(line)
        self._confluence_lines.clear()

        # Current confluence rendering uses _confluence_line_map.
        # Ensure full cleanup between re-plots so stale keys don't
        # suppress fresh ATR/confluence line rendering.
        line_map = getattr(self, "_confluence_line_map", None)
        if line_map:
            for pairs in line_map.values():
                for plot, line in pairs:
                    try:
                        plot.removeItem(line)
                    except Exception:
                        pass
            line_map.clear()

    def _draw_confluence_lines(
            self,
            price_above_mask: np.ndarray,
            price_below_mask: np.ndarray,
            price_cross_above_ema: np.ndarray,
            price_cross_below_ema: np.ndarray,
            cvd_above_mask: np.ndarray,
            cvd_below_mask: np.ndarray,
            cvd_above_ema51: np.ndarray,
            cvd_below_ema51: np.ndarray,
            x_arr: np.ndarray,
    ):
        if not hasattr(self, "_confluence_line_map"):
            self._confluence_line_map = {}

        cvd_data = np.array(self.all_cvd_data, dtype=float)
        cvd_fast_filter, cvd_slow_filter = calculate_regime_trend_filter(cvd_data)
        cvd_above_fast = cvd_data > cvd_fast_filter
        cvd_below_fast = cvd_data < cvd_fast_filter

        price_data = np.array(self.all_price_data, dtype=float)
        price_fast_filter, price_slow_filter = calculate_regime_trend_filter(price_data)

        short_ema_cross, long_ema_cross = self.strategy_detector.detect_ema_cvd_cross_strategy(
            timestamps=self.all_timestamps,
            price_data=price_data,
            price_ema10=price_fast_filter,
            price_ema51=price_slow_filter,
            cvd_data=cvd_data,
            cvd_ema10=cvd_fast_filter,
            cvd_ema51=cvd_slow_filter,
            cvd_ema_gap_threshold=self.cvd_ema_gap_input.value()
        )

        short_divergence, long_divergence = self.strategy_detector.detect_atr_cvd_divergence_strategy(
            price_atr_above=price_above_mask,
            price_atr_below=price_below_mask,
            cvd_above_ema10=cvd_above_fast,
            cvd_below_ema10=cvd_below_fast,
            cvd_above_ema51=cvd_above_ema51,
            cvd_below_ema51=cvd_below_ema51,
            cvd_data=cvd_data,
            ema_cross_short=short_ema_cross,
            ema_cross_long=long_ema_cross
        )

        price_high = np.array(self.all_price_high_data, dtype=float)
        price_low = np.array(self.all_price_low_data, dtype=float)
        volume_data = np.array(self.all_volume_data, dtype=float)
        atr_values = calculate_atr(price_high, price_low, price_data, period=14)

        long_breakout, short_breakout, range_highs, range_lows = \
            self.strategy_detector.detect_range_breakout_strategy(
                price_high=price_high,
                price_low=price_low,
                price_close=price_data,
                price_ema10=price_fast_filter,
                cvd_data=cvd_data,
                cvd_ema10=cvd_fast_filter,
                volume=volume_data,
                range_lookback_minutes=self.range_lookback_input.value(),
                breakout_threshold_multiplier=1.5
            )

        short_cvd_range_breakout, long_cvd_range_breakout = self.strategy_detector.detect_cvd_range_breakout_strategy(
            price_high=price_high,
            price_low=price_low,
            price_close=price_data,
            price_ema10=price_fast_filter,
            cvd_data=cvd_data,
            cvd_ema10=cvd_fast_filter,
            cvd_range_lookback_bars=int(self.cvd_range_lookback_input.value()),
            cvd_breakout_buffer=float(self.cvd_breakout_buffer_input.value()),
            cvd_min_consol_bars=int(self.cvd_min_consol_bars_input.value()),
            cvd_max_range_ratio=float(self.cvd_max_range_ratio_input.value()),
            min_consolidation_adx=float(self.cvd_breakout_min_adx_input.value()),
        )

        session_keys = [ts.date() for ts in self.all_timestamps]
        price_vwap = calculate_vwap(price_data, volume_data, session_keys)

        # ── FIX: Always detect open_drive using the user's configured time ──
        # Previously open_drive was computed elsewhere and not passed here,
        # so confluence lines never rendered for open_drive signals.
        short_open_drive, long_open_drive = self.strategy_detector.detect_open_drive_strategy(
            timestamps=self.all_timestamps,
            price_data=price_data,
            price_ema10=price_fast_filter,
            price_ema51=price_slow_filter,
            price_vwap=price_vwap,
            cvd_data=cvd_data,
            cvd_ema10=cvd_fast_filter,
            trigger_hour=int(self.open_drive_time_hour_input.value()),
            trigger_minute=int(self.open_drive_time_minute_input.value()),
            enabled=bool(self.open_drive_enabled_check.isChecked()),
        )

        breakout_long_context, breakout_short_context = self.strategy_detector.build_breakout_context_masks(
            long_breakout=long_breakout,
            short_breakout=short_breakout,
            hold_bars=max(2, int(round(6 / max(self.timeframe_minutes, 1))))
        )

        breakout_long_strong, breakout_short_strong = self.strategy_detector.evaluate_breakout_momentum_strength(
            price_close=price_data,
            price_ema10=price_fast_filter,
            cvd_data=cvd_data,
            cvd_ema10=cvd_fast_filter,
            volume=volume_data,
            long_context=breakout_long_context,
            short_context=breakout_short_context,
            slope_lookback_bars=max(1, int(round(3 / max(self.timeframe_minutes, 1))))
        )

        short_atr_reversal, long_atr_reversal, short_atr_reversal_raw, long_atr_reversal_raw = \
            self.strategy_detector.detect_atr_reversal_strategy(
                price_atr_above=price_above_mask,
                price_atr_below=price_below_mask,
                cvd_atr_above=cvd_above_mask,
                cvd_atr_below=cvd_below_mask,
                atr_values=atr_values,
                timestamps=self.all_timestamps,
                active_breakout_long=breakout_long_context,
                active_breakout_short=breakout_short_context,
                breakout_long_momentum_strong=breakout_long_strong,
                breakout_short_momentum_strong=breakout_short_strong,
                breakout_switch_mode=self._selected_breakout_switch_mode(),
            )

        selected_filters = set(self._selected_signal_filters())
        all_filters = {
            self.SIGNAL_FILTER_ATR_ONLY,
            self.SIGNAL_FILTER_EMA_CROSS_ONLY,
            self.SIGNAL_FILTER_BREAKOUT_ONLY,
            self.SIGNAL_FILTER_CVD_BREAKOUT_ONLY,
            self.SIGNAL_FILTER_OTHERS,
            self.SIGNAL_FILTER_OPEN_DRIVE_ONLY,
        }

        if selected_filters >= all_filters:
            short_mask = short_atr_reversal | short_ema_cross | short_divergence | short_breakout | short_cvd_range_breakout | short_open_drive
            long_mask = long_atr_reversal | long_ema_cross | long_divergence | long_breakout | long_cvd_range_breakout | long_open_drive
        else:
            short_mask = np.zeros_like(short_atr_reversal, dtype=bool)
            long_mask = np.zeros_like(long_atr_reversal, dtype=bool)

            if self.SIGNAL_FILTER_ATR_ONLY in selected_filters:
                short_mask |= short_atr_reversal
                long_mask |= long_atr_reversal
            if self.SIGNAL_FILTER_EMA_CROSS_ONLY in selected_filters:
                short_mask |= short_ema_cross
                long_mask |= long_ema_cross
            if self.SIGNAL_FILTER_BREAKOUT_ONLY in selected_filters:
                short_mask |= short_breakout
                long_mask |= long_breakout
            if self.SIGNAL_FILTER_CVD_BREAKOUT_ONLY in selected_filters:
                short_mask |= short_cvd_range_breakout
                long_mask |= long_cvd_range_breakout
            if self.SIGNAL_FILTER_OTHERS in selected_filters:
                short_mask |= short_divergence
                long_mask |= long_divergence
            if self.SIGNAL_FILTER_OPEN_DRIVE_ONLY in selected_filters:
                short_mask |= short_open_drive
                long_mask |= long_open_drive

        # ── FIX: Open drive lines must ALWAYS draw when enabled, regardless
        # of the active signal_filter. They represent a time-specific trigger
        # so they should always be visible on the chart even when a filter is
        # narrowing the general signal set.
        if bool(self.open_drive_enabled_check.isChecked()):
            short_mask = short_mask | short_open_drive
            long_mask = long_mask | long_open_drive

        length = min(len(x_arr), len(short_mask), len(long_mask))
        x_arr = x_arr[:length]
        short_mask = short_mask[:length]
        long_mask = long_mask[:length]

        strategy_masks = {
            "short": {
                "atr_reversal": short_atr_reversal[:length],
                "atr_divergence": short_divergence[:length],
                "ema_cross": short_ema_cross[:length],
                "range_breakout": short_breakout[:length],
                "cvd_range_breakout": short_cvd_range_breakout[:length],
                "open_drive": short_open_drive[:length],
            },
            "long": {
                "atr_reversal": long_atr_reversal[:length],
                "atr_divergence": long_divergence[:length],
                "ema_cross": long_ema_cross[:length],
                "range_breakout": long_breakout[:length],
                "cvd_range_breakout": long_cvd_range_breakout[:length],
                "open_drive": long_open_drive[:length],
            },
        }

        # Keep the latest plotted masks available for simulator replay.
        self._latest_sim_x_arr = x_arr
        self._latest_sim_short_mask = short_mask
        self._latest_sim_long_mask = long_mask
        self._latest_sim_strategy_masks = strategy_masks

        # ───────────────────────── DRAW LINES ─────────────────────────
        new_keys = set()

        # ── FIX: Use strategy-aware key prefix so open_drive lines never get
        # suppressed by a coincident ATR/EMA line at the same bar index.
        # Previously all strategies shared "S:{idx}"/"L:{idx}" keys, so if
        # any strategy already drew at that bar, open_drive was silently skipped.
        def _resolve_strategy_at(idx: int, side: str) -> str:
            side_masks = strategy_masks.get(side, {})
            _, priorities = self._active_strategy_priorities()
            ordered_strategies = sorted(
                priorities.keys(),
                key=lambda strategy_key: priorities.get(strategy_key, 0),
                reverse=True,
            )
            for st in ordered_strategies:
                m = side_masks.get(st)
                if m is not None and idx < len(m) and bool(m[idx]):
                    return st
            return "atr_reversal"

        def _add_line(key: str, x: float, color: str):
            if key in self._confluence_line_map:
                return
            pen = pg.mkPen(color, width=self._confluence_line_width)
            pairs = []
            for plot in (self.price_plot, self.plot):
                line = pg.InfiniteLine(pos=x, angle=90, movable=False, pen=pen)
                line.setOpacity(self._confluence_line_opacity)
                line.setZValue(-10)
                plot.addItem(line)
                pairs.append((plot, line))
            self._confluence_line_map[key] = pairs

        for idx in np.where(short_mask)[0]:
            strategy = _resolve_strategy_at(int(idx), "short")
            key = f"S:{idx}:{strategy}"
            new_keys.add(key)
            _add_line(key, float(x_arr[idx]), self._confluence_short_color)

        for idx in np.where(long_mask)[0]:
            strategy = _resolve_strategy_at(int(idx), "long")
            key = f"L:{idx}:{strategy}"
            new_keys.add(key)
            _add_line(key, float(x_arr[idx]), self._confluence_long_color)

        obsolete = set(self._confluence_line_map.keys()) - new_keys
        for key in obsolete:
            for plot, line in self._confluence_line_map[key]:
                plot.removeItem(line)
            del self._confluence_line_map[key]

        # ───────────────────────── AUTOMATION ─────────────────────────
        if not self.automate_toggle.isChecked():
            return

        closed_idx = self._latest_closed_bar_index()
        if closed_idx is None or closed_idx >= length:
            return

        side, strategy_type = self._resolve_signal_side_and_strategy(
            idx=closed_idx,
            short_mask=short_mask,
            long_mask=long_mask,
            strategy_masks=strategy_masks,
        )

        if side is None or strategy_type is None:
            return

        closed_bar_ts = self.all_timestamps[closed_idx].isoformat()

        if self._last_emitted_closed_bar_ts == closed_bar_ts:
            return

        self._last_emitted_closed_bar_ts = closed_bar_ts

        atr_values = calculate_atr(
            np.array(self.all_price_high_data, dtype=float),
            np.array(self.all_price_low_data, dtype=float),
            np.array(self.all_price_data, dtype=float),
            period=14,
        )
        current_atr = float(atr_values[closed_idx]) if closed_idx < len(atr_values) else 0.0
        if not np.isfinite(current_atr) or current_atr <= 0:
            current_atr = 0.0

        active_priority_list, strategy_priorities = self._active_strategy_priorities()
        payload = {
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "signal_side": side,
            "signal_type": strategy_type,
            "priority_list": active_priority_list,
            "strategy_priorities": strategy_priorities,
            "signal_x": float(x_arr[closed_idx]),
            "price_close": float(self.all_price_data[closed_idx]),
            "stoploss_points": float(self.automation_stoploss_input.value()),
            "open_drive_tick_drawdown_limit_points": float(self.open_drive_tick_drawdown_limit_input.value()),
            "atr_trailing_step_points": float(self.atr_trailing_step_input.value()),
            "atr": current_atr,
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "timestamp": closed_bar_ts,
        }

        QTimer.singleShot(0, lambda p=payload: self.automation_signal.emit(p))

        # ───────────────────────── STACKER INIT ─────────────────────────
        stacker_allowed = bool(
            getattr(self, "stacker_enabled_check", None)
            and self.stacker_enabled_check.isChecked()
        )
        if strategy_type == "open_drive":
            stacker_allowed = stacker_allowed and bool(
                getattr(self, "open_drive_stack_enabled_check", None)
                and self.open_drive_stack_enabled_check.isChecked()
            )

        if stacker_allowed:
            self._live_stacker_state = StackerState(
                anchor_entry_price=float(self.all_price_data[closed_idx]),
                anchor_bar_idx=closed_idx,
                signal_side=side,
                step_points=float(self.stacker_step_input.value()),
                max_stacks=int(self.stacker_max_input.value()),
            )
        else:
            self._live_stacker_state = None

        # ───────────────────────── STACKER CHECK ─────────────────────────
        if (
                self._live_stacker_state is not None
                and side is not None
                and closed_idx is not None
        ):
            self._check_and_emit_stack_signals(
                side=side,
                strategy_type=strategy_type,
                current_price=float(self.all_price_data[closed_idx]),
                current_bar_idx=closed_idx,
                closed_bar_ts=closed_bar_ts,
                x_arr_val=float(x_arr[closed_idx]),
            )
