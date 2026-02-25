"""
Trend Change Markers Mixin
===========================
Draws vertical lines on both price + CVD charts whenever the market regime
transitions (trend or volatility bucket changes).  Each line carries a
floating label showing:

    ▲▲ STRONG_TREND  🔥 HIGH_VOL  |  MORNING  ADX: 31.4  Vol: 1.72×

HOW TO INTEGRATE
-----------------
1.  Add ``TrendChangeMarkersMixin`` to AutoTraderDialog's MRO (before QDialog).
2.  Call ``self._init_trend_change_markers()`` inside ``__init__`` (after plots exist).
3.  In ``setup_panel.py`` → ``_build_setup_dialog`` → "Chart Appearance" group add:
        self.show_trend_change_markers_check = QCheckBox("Show trend change markers")
        self.show_trend_change_markers_check.setChecked(False)
        self.show_trend_change_markers_check.toggled.connect(
            self._on_trend_change_markers_toggled)
        app_frm.addRow("Trend Change", self.show_trend_change_markers_check)
4.  In ``_persist_setup_values`` add:
        "show_trend_change_markers": self.show_trend_change_markers_check.isChecked(),
5.  In ``_restore_setup_values`` add:
        self.show_trend_change_markers_check.setChecked(
            _read_setting("show_trend_change_markers", False, bool))
6.  In ``signal_renderer.py``, at the end of ``_refresh_plot_only`` (after regime
    is classified), call:
        self._refresh_trend_change_markers()
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

# ── colour palette ────────────────────────────────────────────────────────────
_TREND_COLORS = {
    "STRONG_TREND": "#00E676",   # green
    "WEAK_TREND":   "#FFB300",   # amber
    "CHOP":         "#FF4D4D",   # red
}
_VOL_COLORS = {
    "HIGH_VOL":   "#FF4D4D",
    "NORMAL_VOL": "#4D9FFF",
    "LOW_VOL":    "#8A99B3",
}
_SESSION_COLORS = {
    "OPEN_DRIVE":  "#FF9100",
    "MORNING":     "#4D9FFF",
    "MIDDAY":      "#9C27B0",
    "AFTERNOON":   "#26C6DA",
    "PRE_CLOSE":   "#FF7043",
}

_LINE_WIDTH  = 1.2
_LINE_ALPHA  = 180          # 0-255
_LABEL_SIZE  = "8px"
_LABEL_ALPHA = 210


def _blend_color(hex_color: str, alpha: int) -> tuple:
    """Return (r, g, b, a) from hex string."""
    c = QColor(hex_color)
    return (c.red(), c.green(), c.blue(), alpha)


class TrendChangeMarkersMixin:
    """
    Mixin that overlays vertical lines on both charts at every regime
    transition.  Requires that the host class exposes:
      - self.price_plot   (pg.PlotWidget)
      - self.plot         (pg.PlotWidget, CVD)
      - self.all_timestamps (list[datetime])
      - self._current_regime (MarketRegime | None)
      - self.regime_engine   (RegimeEngine | None)
      - self.all_price_data  (list[float]) — for x-axis index mapping
      - self.adx_arr / self.atr_values — computed in _refresh_plot_only
      - self.show_trend_change_markers_check (QCheckBox) — added to setup panel
    """

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _init_trend_change_markers(self):
        """Call once after plots are constructed."""
        self._trend_change_items: list = []   # (price_line, cvd_line, price_label, cvd_label)
        self._last_regime_sequence: list = [] # cache: list of (bar_idx, MarketRegime)
        self._trend_markers_enabled: bool = False

    # ── public entry point ───────────────────────────────────────────────────

    def _refresh_trend_change_markers(self):
        """
        Called from _refresh_plot_only after regime classification.
        Compares regime at every bar and draws a vertical line at each transition.
        """
        enabled = (
            getattr(self, "show_trend_change_markers_check", None) is not None
            and self.show_trend_change_markers_check.isChecked()
        )
        if not enabled:
            self._clear_trend_change_markers()
            return

        regime_engine = getattr(self, "regime_engine", None)
        regime_enabled = (
            regime_engine is not None
            and getattr(self, "regime_enabled_check", None) is not None
            and self.regime_enabled_check.isChecked()
        )
        if not regime_enabled:
            self._clear_trend_change_markers()
            return

        # ── build full regime sequence for all bars ──────────────────────────
        timestamps = getattr(self, "all_timestamps", [])
        adx_arr    = getattr(self, "_latest_adx_arr", None)
        atr_arr    = getattr(self, "_latest_atr_arr", None)
        x_arr      = getattr(self, "_latest_x_arr", None)

        if not timestamps or adx_arr is None or atr_arr is None or x_arr is None:
            return
        if len(x_arr) == 0:
            return

        n = min(len(timestamps), len(adx_arr), len(atr_arr), len(x_arr))

        # Classify each bar using a *fresh* engine so we don't disturb live state
        from copy import deepcopy
        from core.auto_trader.regime_engine import RegimeEngine
        scan_engine = RegimeEngine(config=deepcopy(regime_engine.config))
        scan_engine.reset_session()

        regimes: list = []          # (bar_idx, MarketRegime)
        scan_day = None
        for i in range(n):
            ts = timestamps[i]
            day = ts.date()
            if day != scan_day:
                scan_engine.reset_session()
                scan_day = day
            reg = scan_engine.classify(
                adx=adx_arr[:i + 1],
                atr=atr_arr[:i + 1],
                bar_time=ts,
            )
            regimes.append(reg)

        # ── detect transitions ────────────────────────────────────────────────
        transitions: list[tuple[int, object, object]] = []   # (bar_idx, prev_regime, new_regime)
        for i in range(1, n):
            prev = regimes[i - 1]
            curr = regimes[i]
            if prev is None or curr is None:
                continue
            if prev.trend != curr.trend or prev.volatility != curr.volatility:
                transitions.append((i, prev, curr))

        # ── redraw ────────────────────────────────────────────────────────────
        self._clear_trend_change_markers()
        for bar_idx, prev_reg, curr_reg in transitions:
            if bar_idx >= len(x_arr):
                continue
            x_pos = float(x_arr[bar_idx])
            self._draw_trend_change_marker(x_pos, curr_reg, prev_reg)

    # ── drawing helpers ───────────────────────────────────────────────────────

    def _draw_trend_change_marker(self, x_pos: float, curr_reg, prev_reg):
        """Add one vertical line pair (price + CVD) with an inline label."""
        trend_color   = _TREND_COLORS.get(curr_reg.trend, "#FFFFFF")
        vol_color     = _VOL_COLORS.get(curr_reg.volatility, "#FFFFFF")
        session_color = _SESSION_COLORS.get(curr_reg.session, "#FFFFFF")

        # Line color = trend color, opacity based on vol
        line_color = QColor(trend_color)
        line_color.setAlpha(_LINE_ALPHA)

        pen = pg.mkPen(line_color, width=_LINE_WIDTH, style=Qt.DashLine)

        label_text = self._make_marker_label(curr_reg, prev_reg)

        for plot in (self.price_plot, self.plot):
            vline = pg.InfiniteLine(
                pos=x_pos,
                angle=90,
                pen=pen,
                movable=False,
            )
            vline.setZValue(10)
            plot.addItem(vline)

            # TextItem anchored at the top of the line
            txt = pg.TextItem(
                html=label_text,
                anchor=(0, 1),   # bottom-left of text sits at anchor point
                angle=90,
            )
            txt.setZValue(11)
            plot.addItem(txt)
            # Position: x = line pos, y = will be updated in sigRangeChanged
            # Use a lambda to keep the text pinned to top on zoom
            def _pin_label(_, t=txt, p=plot, xp=x_pos):
                y_range = p.plotItem.vb.viewRange()[1]
                y_top = y_range[1] - (y_range[1] - y_range[0]) * 0.02
                t.setPos(xp, y_top)

            _pin_label(None)   # position immediately
            plot.plotItem.vb.sigRangeChanged.connect(_pin_label)

            self._trend_change_items.append((vline, txt, plot, _pin_label))

    def _make_marker_label(self, curr_reg, prev_reg) -> str:
        """Build rich-text HTML label for the vertical line."""
        trend_color   = _TREND_COLORS.get(curr_reg.trend, "#FFFFFF")
        vol_color     = _VOL_COLORS.get(curr_reg.volatility, "#FFFFFF")
        session_color = _SESSION_COLORS.get(curr_reg.session, "#888888")

        trend_icons = {"STRONG_TREND": "▲▲", "WEAK_TREND": "▲", "CHOP": "↔"}
        vol_icons   = {"HIGH_VOL": "🔥", "NORMAL_VOL": "●", "LOW_VOL": "❄"}

        trend_label   = curr_reg.trend.replace("_", " ")
        vol_label     = curr_reg.volatility.replace("_", " ")
        session_label = curr_reg.session.replace("_", " ")

        adx_str = f"{curr_reg.adx_value:.1f}"
        vol_str = f"{curr_reg.atr_ratio:.2f}×"

        # Show what changed with an arrow
        prev_trend_icon = trend_icons.get(prev_reg.trend, "?") if prev_reg else ""
        change_arrow = f"<span style='color:#8A99B3'>{prev_trend_icon}→</span>" if prev_reg and prev_reg.trend != curr_reg.trend else ""

        return (
            f"<div style='background:rgba(13,17,23,0.88); padding:2px 4px; "
            f"border-left:2px solid {trend_color}; font-family:monospace; font-size:{_LABEL_SIZE};'>"
            f"{change_arrow}"
            f"<span style='color:{trend_color}; font-weight:bold'>{trend_icons.get(curr_reg.trend,'')} {trend_label}</span>"
            f"  <span style='color:{vol_color}'>{vol_icons.get(curr_reg.volatility,'')} {vol_label}</span>"
            f"<br/>"
            f"<span style='color:{session_color}'>{session_label}</span>"
            f"  <span style='color:#8A99B3'>ADX:</span><span style='color:#E8EDF5'>{adx_str}</span>"
            f"  <span style='color:#8A99B3'>Vol:</span><span style='color:#E8EDF5'>{vol_str}</span>"
            f"</div>"
        )

    # ── cleanup ───────────────────────────────────────────────────────────────

    def _clear_trend_change_markers(self):
        """Remove all vertical lines + labels from both charts."""
        for item_tuple in self._trend_change_items:
            vline, txt, plot, slot = item_tuple
            try:
                plot.plotItem.vb.sigRangeChanged.disconnect(slot)
            except Exception:
                pass
            try:
                plot.removeItem(vline)
                plot.removeItem(txt)
            except Exception:
                pass
        self._trend_change_items.clear()

    # ── settings toggle ───────────────────────────────────────────────────────

    def _on_trend_change_markers_toggled(self, *_):
        self._persist_setup_values()
        self._refresh_trend_change_markers()