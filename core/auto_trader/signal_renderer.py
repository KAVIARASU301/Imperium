import logging
import numpy as np
import pyqtgraph as pg
import pandas as pd
from datetime import time

from PySide6.QtCore import QTimer

from core.auto_trader.indicators import calculate_ema, calculate_atr, is_chop_regime

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
        ema10 = calculate_ema(price_data_array, 10)
        ema51 = calculate_ema(price_data_array, 51)
        cvd_ema10 = calculate_ema(cvd_data_array, 10)
        cvd_ema51 = calculate_ema(cvd_data_array, 51)
        idx = self._latest_closed_bar_index()
        if idx is None:
            return

        # Only emit when the closed bar advances or key values change.
        # Emitting on every 3-second refresh floods _on_cvd_automation_market_state
        # which triggers position checks and potentially order placement each time.
        ts_str = self.all_timestamps[idx].isoformat() if idx < len(self.all_timestamps) else None
        new_price_close = float(price_data_array[idx])
        state_key = (ts_str, round(new_price_close, 4))
        if getattr(self, "_last_emitted_state_key", None) == state_key:
            return
        self._last_emitted_state_key = state_key

        self.automation_state_signal.emit({
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "enabled": self.automate_toggle.isChecked(),
            "stoploss_points": float(self.automation_stoploss_input.value()),
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "signal_filter": self._selected_signal_filter(),
            "bar_x": float(x_arr[idx]),
            "price_close": new_price_close,
            "ema10": float(ema10[idx]),
            "ema51": float(ema51[idx]),
            "cvd_close": float(cvd_data_array[idx]),
            "cvd_ema10": float(cvd_ema10[idx]),
            "cvd_ema51": float(cvd_ema51[idx]),
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

        # Calculate CVD EMAs and position masks
        cvd_data = np.array(self.all_cvd_data, dtype=float)
        cvd_ema10 = calculate_ema(cvd_data, 10)
        cvd_ema51 = calculate_ema(cvd_data, 51)

        cvd_above_ema10 = cvd_data > cvd_ema10
        cvd_below_ema10 = cvd_data < cvd_ema10

        # Calculate price EMAs
        price_data = np.array(self.all_price_data, dtype=float)
        price_ema10 = calculate_ema(price_data, 10)
        price_ema51 = calculate_ema(price_data, 51)

        # ----------------------------------------------------------
        # STRATEGY 2: EMA & CVD CROSS
        # ----------------------------------------------------------
        short_ema_cross, long_ema_cross = self.strategy_detector.detect_ema_cvd_cross_strategy(
            price_data=price_data,
            price_ema10=price_ema10,
            price_ema51=price_ema51,
            cvd_data=cvd_data,
            cvd_ema10=cvd_ema10,
            cvd_ema51=cvd_ema51,
            cvd_ema_gap_threshold=self.cvd_ema_gap_input.value()
        )

        # ----------------------------------------------------------
        # STRATEGY 3: ATR & CVD DIVERGENCE
        # ----------------------------------------------------------
        short_divergence, long_divergence = self.strategy_detector.detect_atr_cvd_divergence_strategy(
            price_atr_above=price_above_mask,
            price_atr_below=price_below_mask,
            cvd_above_ema10=cvd_above_ema10,
            cvd_below_ema10=cvd_below_ema10,
            cvd_above_ema51=cvd_above_ema51,
            cvd_below_ema51=cvd_below_ema51,
            cvd_data=cvd_data,
            ema_cross_short=short_ema_cross,
            ema_cross_long=long_ema_cross
        )

        # ----------------------------------------------------------
        # STRATEGY 4: RANGE BREAKOUT 🆕 NEW
        # ----------------------------------------------------------
        price_high = np.array(self.all_price_high_data, dtype=float)
        price_low = np.array(self.all_price_low_data, dtype=float)
        volume_data = np.array(self.all_volume_data, dtype=float)
        atr_values = calculate_atr(price_high, price_low, price_data, period=14)

        long_breakout, short_breakout, range_highs, range_lows = \
            self.strategy_detector.detect_range_breakout_strategy(
                price_high=price_high,
                price_low=price_low,
                price_close=price_data,
                price_ema10=price_ema10,
                cvd_data=cvd_data,
                cvd_ema10=cvd_ema10,
                volume=volume_data,
                range_lookback_minutes=self.range_lookback_input.value(),
                breakout_threshold_multiplier=1.5
            )

        # ----------------------------------------------------------
        # STRATEGY 1: ATR REVERSAL (Confluence of Price + CVD ATR signals)
        # with breakout-vs-reversal switch logic
        # ----------------------------------------------------------
        breakout_long_context, breakout_short_context = self.strategy_detector.build_breakout_context_masks(
            long_breakout=long_breakout,
            short_breakout=short_breakout,
            hold_bars=max(2, int(round(6 / max(self.timeframe_minutes, 1))))
        )
        breakout_long_strong, breakout_short_strong = self.strategy_detector.evaluate_breakout_momentum_strength(
            price_close=price_data,
            price_ema10=price_ema10,
            cvd_data=cvd_data,
            cvd_ema10=cvd_ema10,
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
            active_breakout_long=breakout_long_context,
            active_breakout_short=breakout_short_context,
            breakout_long_momentum_strong=breakout_long_strong,
            breakout_short_momentum_strong=breakout_short_strong,
            breakout_switch_mode=self._selected_breakout_switch_mode(),
        )

        # ----------------------------------------------------------
        # Combine signals based on selected filter
        # ----------------------------------------------------------
        signal_filter = self._selected_signal_filter()

        if signal_filter == self.SIGNAL_FILTER_ATR_ONLY:
            short_mask = short_atr_reversal
            long_mask = long_atr_reversal

        elif signal_filter == self.SIGNAL_FILTER_EMA_CROSS_ONLY:
            short_mask = short_ema_cross
            long_mask = long_ema_cross

        elif signal_filter == self.SIGNAL_FILTER_BREAKOUT_ONLY:  # 🆕 NEW
            short_mask = short_breakout
            long_mask = long_breakout

        elif signal_filter == self.SIGNAL_FILTER_OTHERS:
            short_mask = short_divergence
            long_mask = long_divergence

        else:  # SIGNAL_FILTER_ALL
            short_mask = short_atr_reversal | short_ema_cross | short_divergence | short_breakout  # 🆕 Added breakout
            long_mask = long_atr_reversal | long_ema_cross | long_divergence | long_breakout  # 🆕 Added breakout

        # Ensure array alignment
        length = min(len(x_arr), len(short_mask), len(long_mask))
        x_arr = x_arr[:length]
        short_mask = short_mask[:length]
        long_mask = long_mask[:length]

        # ----------------------------------------------------------
        # Draw confluence lines
        # ----------------------------------------------------------
        new_keys = set()

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

        # Use customizable signal colors across all strategies
        short_color = self._confluence_short_color
        long_color = self._confluence_long_color

        for idx in np.where(short_mask)[0]:
            key = f"S:{idx}"
            new_keys.add(key)
            _add_line(key, float(x_arr[idx]), short_color)

        for idx in np.where(long_mask)[0]:
            key = f"L:{idx}"
            new_keys.add(key)
            _add_line(key, float(x_arr[idx]), long_color)

        # Remove obsolete lines
        obsolete = set(self._confluence_line_map.keys()) - new_keys
        for key in obsolete:
            for plot, line in self._confluence_line_map[key]:
                plot.removeItem(line)
            del self._confluence_line_map[key]

        strategy_masks = {
            "short": {
                "atr_reversal": np.array(short_atr_reversal, dtype=bool),
                "atr_reversal_raw": np.array(short_atr_reversal_raw, dtype=bool),  # pre-suppression
                "ema_cross": np.array(short_ema_cross, dtype=bool),
                "atr_divergence": np.array(short_divergence, dtype=bool),
                "range_breakout": np.array(short_breakout, dtype=bool),
            },
            "long": {
                "atr_reversal": np.array(long_atr_reversal, dtype=bool),
                "atr_reversal_raw": np.array(long_atr_reversal_raw, dtype=bool),  # pre-suppression
                "ema_cross": np.array(long_ema_cross, dtype=bool),
                "atr_divergence": np.array(long_divergence, dtype=bool),
                "range_breakout": np.array(long_breakout, dtype=bool),
            },
        }

        self._latest_sim_x_arr = np.array(x_arr, dtype=float)
        self._latest_sim_short_mask = np.array(short_mask, dtype=bool)
        self._latest_sim_long_mask = np.array(long_mask, dtype=bool)
        self._latest_sim_strategy_masks = strategy_masks

        # ----------------------------------------------------------
        # AUTOMATION signal emission (existing code)
        # ----------------------------------------------------------
        if not self.automate_toggle.isChecked():
            return

        closed_idx = self._latest_closed_bar_index()
        if closed_idx is None or closed_idx >= length:
            return

        # Time filter: skip first 5 minutes (9:15-9:20) and last 30 minutes (15:00 onwards)
        bar_ts = self.all_timestamps[closed_idx]
        bar_time = bar_ts.time()
        if bar_time < time(9, 20) or bar_time >= time(15, 0):
            return

        side, strategy_type = self._resolve_signal_side_and_strategy(
            idx=closed_idx,
            short_mask=short_mask,
            long_mask=long_mask,
            strategy_masks=strategy_masks,
        )

        # ── ATR Skip Limit — live override ────────────────────────────────────
        # If no signal passed the filter but a raw ATR reversal exists at this bar,
        # check if we've suppressed enough signals to force the ATR entry.
        atr_skip_limit = int(self.atr_skip_limit_input.value()) if hasattr(self, "atr_skip_limit_input") else 0
        if side is None and atr_skip_limit > 0:
            raw_short = strategy_masks.get("short", {}).get("atr_reversal_raw") if strategy_masks else None
            raw_long  = strategy_masks.get("long",  {}).get("atr_reversal_raw") if strategy_masks else None
            raw_short_hit = raw_short is not None and closed_idx < len(raw_short) and bool(raw_short[closed_idx])
            raw_long_hit  = raw_long  is not None and closed_idx < len(raw_long)  and bool(raw_long[closed_idx])

            if raw_short_hit or raw_long_hit:
                raw_side = "short" if raw_short_hit else "long"
                # Only override if the ACTIVE breakout is on the opposite side
                if self._live_active_breakout_side and self._live_active_breakout_side != raw_side:
                    self._live_atr_skip_count += 1
                    if self._live_atr_skip_count >= atr_skip_limit:
                        side = raw_side
                        strategy_type = "atr_reversal"
                        self._live_atr_skip_count = 0
                        self._live_active_breakout_side = None
                else:
                    # New or same-side breakout — reset counter
                    self._live_atr_skip_count = 0
        # ─────────────────────────────────────────────────────────────────────

        # Track breakout side for skip counting
        if strategy_type == "range_breakout" and side is not None:
            self._live_active_breakout_side = side
            self._live_atr_skip_count = 0
        elif strategy_type != "range_breakout":
            # Non-breakout signal closed any breakout context
            self._live_active_breakout_side = None
            self._live_atr_skip_count = 0

        if side is None or strategy_type is None:
            return

        governance = getattr(self, "signal_governance", None)
        governance_decision = None
        if governance is not None:
            governance_decision = governance.fuse_signal(
                strategy_type=strategy_type,
                side=side,
                strategy_masks=strategy_masks,
                closed_idx=closed_idx,
                price_close=np.asarray(self.all_price_data, dtype=float),
                ema10=price_ema10,
                ema51=price_ema51,
                atr=np.asarray(atr_values, dtype=float),
                cvd_close=np.asarray(self.all_cvd_data, dtype=float),
                cvd_ema10=cvd_ema10,
            )
            if not governance_decision.enabled:
                return
            if not governance_decision.can_trade_live:
                logger.info(
                        "[AUTO][GOV] Signal held token=%s strategy=%s confidence=%.2f reasons=%s",
                        self.instrument_token,
                        strategy_type,
                        governance_decision.confidence,
                        governance_decision.reasons,
                    )
                return

        if is_chop_regime(self, closed_idx, strategy_type=strategy_type):
            return

        closed_bar_ts = self.all_timestamps[closed_idx].isoformat()

        if self._last_emitted_closed_bar_ts == closed_bar_ts:
            return

        self._last_emitted_closed_bar_ts = closed_bar_ts

        payload = {
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "signal_side": side,
            "signal_type": strategy_type,
            "signal_x": float(x_arr[closed_idx]),
            "price_close": float(self.all_price_data[closed_idx]),
            "stoploss_points": float(self.automation_stoploss_input.value()),
            "route": self.automation_route_combo.currentData() or self.ROUTE_BUY_EXIT_PANEL,
            "timestamp": closed_bar_ts,
        }

        if governance_decision is not None:
            payload.update({
                "signal_confidence": governance_decision.confidence,
                "market_regime": governance_decision.regime,
                "governance_reasons": governance_decision.reasons,
                "deploy_mode": governance_decision.deploy_mode,
                "drift_score": governance_decision.drift_score,
                "health_score": governance_decision.health_score,
            })

        QTimer.singleShot(0, lambda p=payload: self.automation_signal.emit(p))
