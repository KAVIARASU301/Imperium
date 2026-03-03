"""
Price & CVD Chart Dialog
========================
Standalone dialog showing Price (top) + CVD (bottom) with:
  • Date navigator  (previous / next trading day)
  • 1D / 2D toggle
  • EMA 10 / 21 / 51 toggles  (both charts)
  • VWAP toggle               (price chart)
  • Auto-refresh (live mode, every 3 s)
  • Linked X-axes + synchronised crosshair
  • Clean tick labels — no overlap, no scribbles
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)
from pyqtgraph import AxisItem

from core.auto_trader.constants import MINUTES_PER_SESSION
from core.auto_trader.data_worker import _DataFetchWorker
from core.auto_trader.indicators import calculate_ema, calculate_vwap

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour palette  (easy-on-the-eyes, dark slate)
# ---------------------------------------------------------------------------
_C = {
    "bg":          "#0B0F1A",
    "chart_bg":    "#0D1117",
    "toolbar_bg":  "#111722",
    "border":      "#1E2D40",
    "border_hi":   "#2A3F58",
    "text_1":      "#D8E0F0",
    "text_2":      "#8A99B3",
    "text_dim":    "#3A4A60",
    # lines
    "price":       "#FFE57F",   # warm amber – price
    "price_prev":  "#5A6070",   # dimmed grey – previous day price
    "cvd":         "#26C6DA",   # teal-cyan – CVD
    "cvd_prev":    "#3A5060",   # dimmed – previous day CVD
    # EMAs
    "ema10":       "#00D9FF",   # cyan  – fast
    "ema21":       "#FFD700",   # gold  – medium
    "ema51":       "#FF6B6B",   # salmon – slow
    "vwap":        "#00E676",   # green – VWAP
    # separator
    "day_sep":     "#2A3F58",
}

_TOOLBAR_BTN = """
    QPushButton {{
        background: #151D2B;
        color: {fg};
        border: 1px solid #1E2D40;
        border-radius: 4px;
        padding: 0px 8px;
        font-size: 11px;
        font-weight: 700;
        min-height: 22px;
        min-width: {mw}px;
    }}
    QPushButton:hover {{
        border: 1px solid #4D9FFF;
        background: #1C2638;
    }}
    QPushButton:checked {{
        background: {chk_bg};
        color: {chk_fg};
        border: 1px solid {chk_bg};
    }}
    QPushButton:pressed {{
        background: #0D1117;
    }}
"""

def _btn(text: str, fg="#8A99B3", mw=36, checkable=False,
         chk_bg="#26A69A", chk_fg="#000") -> QPushButton:
    b = QPushButton(text)
    b.setCheckable(checkable)
    b.setStyleSheet(_TOOLBAR_BTN.format(fg=fg, mw=mw, chk_bg=chk_bg, chk_fg=chk_fg))
    b.setFixedHeight(22)
    return b

def _ema_cb(label: str, color: str) -> QCheckBox:
    cb = QCheckBox(label)
    cb.setStyleSheet(f"""
        QCheckBox {{
            color: {color};
            font-weight: 700;
            font-size: 11px;
            spacing: 3px;
        }}
        QCheckBox::indicator {{
            width: 12px; height: 12px;
            border: 1px solid {color};
            border-radius: 2px;
            background: #0D1117;
        }}
        QCheckBox::indicator:checked {{
            background: {color};
        }}
    """)
    cb.setFixedHeight(22)
    return cb


# ---------------------------------------------------------------------------
# Custom axis: suppresses values, passes through to formatter override
# ---------------------------------------------------------------------------
class _TimeAxis(AxisItem):
    """Bottom axis that delegates to an external tickStrings callable."""

    def __init__(self, orientation="bottom"):
        super().__init__(orientation=orientation)
        self._formatter = None
        self.setHeight(28)
        self.setStyle(showValues=True, tickLength=-4)
        self.setTextPen(pg.mkPen(_C["text_2"]))
        self.setPen(pg.mkPen(_C["border"]))

    def set_formatter(self, fn):
        self._formatter = fn

    def tickStrings(self, values, scale, spacing):
        if self._formatter is None:
            return [""] * len(values)
        return self._formatter(values)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------
class PriceCVDChartDialog(QDialog):
    """Price (top) + CVD (bottom) chart with date navigation & indicator toggles."""

    REFRESH_INTERVAL_MS = 3000

    def __init__(
        self,
        kite,
        instrument_token: int,
        symbol: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)

        self.kite = kite
        self.instrument_token = instrument_token
        self.symbol = symbol

        # state
        self.live_mode: bool = True
        self._two_day: bool = False           # True = 2D sequential mode
        self.current_date: Optional[datetime] = None
        self.previous_date: Optional[datetime] = None

        # cached data for overlay recompute
        self._all_timestamps: list[datetime] = []
        self._all_price: list[float] = []
        self._all_price_high: list[float] = []
        self._all_price_low: list[float] = []
        self._all_volume: list[float] = []
        self._all_cvd: list[float] = []
        self._last_x_indices: list[float] = []

        # worker refs
        self._fetch_worker: Optional[_DataFetchWorker] = None
        self._fetch_thread: Optional[QThread] = None
        self._is_loading: bool = False

        self.setWindowTitle(f"Price & CVD Chart — {symbol}")
        self.setObjectName("priceCVDChartDialog")
        self.setMinimumSize(900, 580)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.setStyleSheet(f"""
            QDialog#priceCVDChartDialog {{
                background: {_C["bg"]};
            }}
            QLabel {{
                color: {_C["text_2"]};
                font-size: 11px;
                background: transparent;
            }}
        """)

        self._setup_ui()

        # initialise dates
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.current_date = today
        self.previous_date = self._prev_trading_day(today)
        self._update_date_label()

        # initial load
        self._load_and_plot()

        # live auto-refresh
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_live_refresh)
        self._refresh_timer.start(self.REFRESH_INTERVAL_MS)

    # ------------------------------------------------------------------ UI ---

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        self._build_toolbar(root)
        self._build_charts(root)

    def _build_toolbar(self, root: QVBoxLayout):
        bar = QWidget(self)
        bar.setFixedHeight(30)
        bar.setStyleSheet(f"""
            background: {_C["toolbar_bg"]};
            border-bottom: 1px solid {_C["border"]};
            border-radius: 4px;
        """)

        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(6)

        # — Date navigator —
        self.btn_back = _btn("◀", mw=24)
        self.btn_back.setToolTip("Previous trading day  (←)")
        self.btn_back.clicked.connect(self._go_back)

        self.lbl_dates = QLabel("—")
        self.lbl_dates.setAlignment(Qt.AlignCenter)
        self.lbl_dates.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #C0CCE0; background: transparent;"
        )
        self.lbl_dates.setMinimumWidth(280)

        self.btn_fwd = _btn("▶", mw=24)
        self.btn_fwd.setToolTip("Next trading day  (→)")
        self.btn_fwd.clicked.connect(self._go_forward)

        row.addWidget(self.btn_back)
        row.addWidget(self.lbl_dates)
        row.addWidget(self.btn_fwd)

        # — Separator pill —
        sep1 = QLabel("|")
        sep1.setStyleSheet(f"color: {_C['border_hi']}; font-size: 12px; background: transparent;")
        row.addWidget(sep1)

        # — 1D / 2D toggle —
        self.btn_1d = _btn("1D", fg=_C["text_1"], mw=30, checkable=True,
                           chk_bg="#26A69A", chk_fg="#000")
        self.btn_1d.setChecked(True)
        self.btn_1d.setToolTip("Toggle 1-day / 2-day view")
        self.btn_1d.clicked.connect(self._on_day_mode_toggled)

        row.addWidget(self.btn_1d)

        # — Separator pill —
        sep2 = QLabel("|")
        sep2.setStyleSheet(sep1.styleSheet())
        row.addWidget(sep2)

        # — EMA label —
        lbl_ema = QLabel("EMA")
        lbl_ema.setStyleSheet("color: #6A7A90; font-size: 10px; font-weight: 700; background: transparent;")
        row.addWidget(lbl_ema)

        # — EMA toggles —
        self.cb_ema10 = _ema_cb("10", _C["ema10"])
        self.cb_ema21 = _ema_cb("21", _C["ema21"])
        self.cb_ema51 = _ema_cb("51", _C["ema51"])
        self.cb_ema51.setChecked(True)  # default: only 51

        for cb in (self.cb_ema10, self.cb_ema21, self.cb_ema51):
            cb.toggled.connect(self._on_indicator_toggled)
            row.addWidget(cb)

        # — Separator pill —
        sep3 = QLabel("|")
        sep3.setStyleSheet(sep1.styleSheet())
        row.addWidget(sep3)

        # — VWAP toggle —
        self.cb_vwap = _ema_cb("VWAP", _C["vwap"])
        self.cb_vwap.toggled.connect(self._on_indicator_toggled)
        row.addWidget(self.cb_vwap)

        row.addStretch()

        # — Status dot (loading indicator) —
        self.lbl_status = QLabel("●")
        self.lbl_status.setStyleSheet(
            f"color: {_C['ema51']}; font-size: 12px; background: transparent;"
        )
        self.lbl_status.setToolTip("Chart loading status")
        row.addWidget(self.lbl_status)

        root.addWidget(bar)

    def _build_charts(self, root: QVBoxLayout):
        # ── Price chart ────────────────────────────────────────────────────
        self._price_axis = _TimeAxis("bottom")
        self._price_axis.setStyle(showValues=False)   # x labels only on CVD

        self.price_plot = pg.PlotWidget(axisItems={"bottom": self._price_axis})
        self.price_plot.setBackground(_C["chart_bg"])
        self.price_plot.showGrid(x=True, y=True, alpha=0.06)
        self.price_plot.setMenuEnabled(False)
        self.price_plot.setMinimumHeight(200)

        py_ax = self.price_plot.getAxis("left")
        py_ax.setWidth(72)
        py_ax.setTextPen(pg.mkPen(_C["price"]))
        py_ax.setPen(pg.mkPen(_C["border"]))
        py_ax.enableAutoSIPrefix(False)

        # price curves
        self._price_prev   = pg.PlotCurveItem(pen=pg.mkPen(_C["price_prev"], width=1.8, style=Qt.DashLine))
        self._price_today  = pg.PlotCurveItem(pen=pg.mkPen(_C["price"],      width=2.2))
        self._price_ema10  = pg.PlotCurveItem(pen=pg.mkPen(_C["ema10"],      width=1.6))
        self._price_ema21  = pg.PlotCurveItem(pen=pg.mkPen(_C["ema21"],      width=1.6))
        self._price_ema51  = pg.PlotCurveItem(pen=pg.mkPen(_C["ema51"],      width=1.8))
        self._price_vwap   = pg.PlotCurveItem(pen=pg.mkPen(_C["vwap"],       width=1.5, style=Qt.DashDotLine))

        for item in (self._price_prev, self._price_today,
                     self._price_ema10, self._price_ema21, self._price_ema51,
                     self._price_vwap):
            self.price_plot.addItem(item)

        # day separator
        self._price_day_sep = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(_C["day_sep"], width=1, style=Qt.DashLine),
        )
        self._price_day_sep.hide()
        self.price_plot.addItem(self._price_day_sep)

        # price crosshair
        self._price_vline = pg.InfiniteLine(angle=90, movable=False,
                                            pen=pg.mkPen("#FFFFFF", width=0.6, style=Qt.DotLine))
        self._price_hline = pg.InfiniteLine(angle=0,  movable=False,
                                            pen=pg.mkPen("#FFFFFF", width=0.6, style=Qt.DotLine))
        self._price_vline.hide(); self._price_hline.hide()
        self.price_plot.addItem(self._price_vline)
        self.price_plot.addItem(self._price_hline)

        root.addWidget(self.price_plot, 3)

        # ── CVD chart ──────────────────────────────────────────────────────
        self._cvd_axis = _TimeAxis("bottom")

        self.cvd_plot = pg.PlotWidget(axisItems={"bottom": self._cvd_axis})
        self.cvd_plot.setBackground(_C["chart_bg"])
        self.cvd_plot.showGrid(x=True, y=True, alpha=0.06)
        self.cvd_plot.setMenuEnabled(False)
        self.cvd_plot.setMinimumHeight(150)

        cy_ax = self.cvd_plot.getAxis("left")
        cy_ax.setWidth(72)
        cy_ax.setTextPen(pg.mkPen(_C["cvd"]))
        cy_ax.setPen(pg.mkPen(_C["border"]))
        cy_ax.enableAutoSIPrefix(False)
        cy_ax.tickStrings = self._cvd_y_tick_strings  # K / M formatter

        cx_ax = self.cvd_plot.getAxis("bottom")
        cx_ax.setHeight(28)
        cx_ax.setTextPen(pg.mkPen(_C["text_2"]))
        cx_ax.setPen(pg.mkPen(_C["border"]))

        # CVD curves
        self._cvd_prev  = pg.PlotCurveItem(pen=pg.mkPen(_C["cvd_prev"], width=1.8, style=Qt.DashLine))
        self._cvd_today = pg.PlotCurveItem(pen=pg.mkPen(_C["cvd"],      width=2.2))
        self._cvd_ema10 = pg.PlotCurveItem(pen=pg.mkPen(_C["ema10"],    width=1.5))
        self._cvd_ema21 = pg.PlotCurveItem(pen=pg.mkPen(_C["ema21"],    width=1.5))
        self._cvd_ema51 = pg.PlotCurveItem(pen=pg.mkPen(_C["ema51"],    width=1.6))

        for item in (self._cvd_prev, self._cvd_today,
                     self._cvd_ema10, self._cvd_ema21, self._cvd_ema51):
            self.cvd_plot.addItem(item)

        # day separator
        self._cvd_day_sep = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(_C["day_sep"], width=1, style=Qt.DashLine),
        )
        self._cvd_day_sep.hide()
        self.cvd_plot.addItem(self._cvd_day_sep)

        # CVD crosshair
        self._cvd_vline = pg.InfiniteLine(angle=90, movable=False,
                                          pen=pg.mkPen("#FFFFFF", width=0.6, style=Qt.DotLine))
        self._cvd_hline = pg.InfiniteLine(angle=0,  movable=False,
                                          pen=pg.mkPen("#FFFFFF", width=0.6, style=Qt.DotLine))
        self._cvd_vline.hide(); self._cvd_hline.hide()
        self.cvd_plot.addItem(self._cvd_vline)
        self.cvd_plot.addItem(self._cvd_hline)

        root.addWidget(self.cvd_plot, 2)

        # ── Link X-axes (price follows CVD and vice-versa) ─────────────────
        self.price_plot.setXLink(self.cvd_plot)

        # ── Mouse events ───────────────────────────────────────────────────
        self.price_plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.cvd_plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.price_plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        self.cvd_plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)

        # hide EMA curves that are off by default
        self._price_ema10.hide(); self._cvd_ema10.hide()
        self._price_ema21.hide(); self._cvd_ema21.hide()
        self._price_vwap.hide()

    # ---------------------------------------------------------------- dates --

    @staticmethod
    def _prev_trading_day(date: datetime) -> datetime:
        prev = date - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev

    @staticmethod
    def _next_trading_day(date: datetime) -> datetime:
        nxt = date + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt

    def _update_date_label(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cd = self.current_date
        pd_ = self.previous_date
        c_str = cd.strftime("%a %b %d") if cd else "—"
        p_str = pd_.strftime("%a %b %d") if pd_ else "—"
        year  = cd.strftime("%Y") if cd else ""

        self.lbl_dates.setText(
            f"<span style='color:#5588BB'>{p_str}</span>"
            f"<span style='color:#3A4A60'> ▷ </span>"
            f"<span style='color:#A0BFD0'>{c_str}, {year}</span>"
        )
        self.btn_fwd.setEnabled(
            bool(cd) and cd < today
        )

    def _go_back(self):
        if self.current_date is None:
            return
        self.live_mode = False
        self.current_date = self._prev_trading_day(self.current_date)
        self.previous_date = self._prev_trading_day(self.current_date)
        self._update_date_label()
        self._load_and_plot()

    def _go_forward(self):
        if self.current_date is None:
            return
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        nxt = self._next_trading_day(self.current_date)
        if nxt > today:
            return
        self.current_date = nxt
        self.live_mode = (nxt >= today)
        self.previous_date = self._prev_trading_day(self.current_date)
        self._update_date_label()
        self._load_and_plot()

    # ---------------------------------------------------------------- modes --

    def _on_day_mode_toggled(self):
        """1D ↔ 2D switch — re-renders in-memory data without a re-fetch."""
        self._two_day = not self.btn_1d.isChecked()
        self.btn_1d.setText("2D" if self._two_day else "1D")
        # Only re-plot if we already have data
        if self._all_timestamps:
            self._render_from_cache()

    def _on_indicator_toggled(self, *_):
        """EMA / VWAP checkbox changed — update overlay visibility only."""
        if self._all_timestamps:
            self._render_overlays()

    # --------------------------------------------------------------- loading -

    def _load_and_plot(self):
        if self._is_loading:
            return
        if not self.kite or not getattr(self.kite, "access_token", None):
            return

        self._set_status("loading")

        if self.live_mode:
            to_dt   = datetime.now()
            from_dt = to_dt - timedelta(days=5)
        else:
            to_dt   = self.current_date + timedelta(days=1)
            from_dt = self.previous_date

        self._is_loading = True

        # focus_mode=True → pass 1-day data only; we always want 2 sessions
        # so pass focus_mode=False (worker still returns 2 sessions)
        self._fetch_thread = QThread(self)
        self._fetch_worker = _DataFetchWorker(
            self.kite,
            self.instrument_token,
            from_dt,
            to_dt,
            1,      # 1-minute timeframe
            False,  # always fetch two sessions; we handle 1D/2D in rendering
        )
        self._fetch_worker.moveToThread(self._fetch_thread)

        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.finished.connect(self._fetch_thread.quit)
        self._fetch_thread.finished.connect(self._fetch_worker.deleteLater)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)

        self._fetch_worker.result_ready.connect(self._on_fetch_result)
        self._fetch_worker.error.connect(self._on_fetch_error)

        self._fetch_thread.start()

    def _on_fetch_result(self, cvd_df, price_df, _prev_close, _prev_cpr):
        self._is_loading = False
        self._set_status("live" if self.live_mode else "hist")

        if cvd_df is None or cvd_df.empty:
            return

        # ── Cache session-separated data ─────────────────────────────────
        sessions = sorted(cvd_df["session"].unique())

        self._all_timestamps = []
        self._all_price      = []
        self._all_price_high = []
        self._all_price_low  = []
        self._all_volume     = []
        self._all_cvd        = []
        self._session_x_break: Optional[float] = None   # x where new session starts

        for i, sess in enumerate(sessions):
            cvd_sess   = cvd_df[cvd_df["session"] == sess]
            price_sess = price_df[price_df["session"] == sess]

            self._all_timestamps.extend(cvd_sess.index.tolist())
            self._all_cvd.extend(cvd_sess["close"].tolist())
            self._all_price.extend(price_sess["close"].tolist())

            if "high" in price_sess.columns:
                self._all_price_high.extend(price_sess["high"].tolist())
            else:
                self._all_price_high.extend(price_sess["close"].tolist())

            if "low" in price_sess.columns:
                self._all_price_low.extend(price_sess["low"].tolist())
            else:
                self._all_price_low.extend(price_sess["close"].tolist())

            if "volume" in price_sess.columns:
                self._all_volume.extend(price_sess["volume"].tolist())
            else:
                self._all_volume.extend([1.0] * len(price_sess))

            # record where session 1 (current day) starts
            if i == 0 and len(sessions) == 2:
                self._session_x_break = float(len(self._all_timestamps))

        self._render_from_cache()

    def _on_fetch_error(self, msg: str):
        self._is_loading = False
        self._set_status("error")
        logger.warning("[PriceCVDChart] fetch error: %s", msg)

    def _on_live_refresh(self):
        if not self.isVisible():
            return
        if self.live_mode:
            self._load_and_plot()

    # ------------------------------------------------------------ rendering --

    def _render_from_cache(self):
        """Build x-indices and plot everything from in-memory lists."""
        if not self._all_timestamps:
            return

        n = len(self._all_timestamps)
        sessions = sorted({ts.date() for ts in self._all_timestamps})
        has_two   = len(sessions) == 2

        if not self._two_day:
            # ── 1D mode: map each bar to its session minute (0-based) ──────
            # Show ONLY the current (last) session
            if has_two:
                # determine split index
                split = next(
                    (k for k, ts in enumerate(self._all_timestamps)
                     if ts.date() == sessions[1]),
                    0,
                )
                ts_today  = self._all_timestamps[split:]
                cvd_today = self._all_cvd[split:]
                px_today  = self._all_price[split:]
                vol_today = self._all_volume[split:]
            else:
                split     = 0
                ts_today  = self._all_timestamps
                cvd_today = self._all_cvd
                px_today  = self._all_price
                vol_today = self._all_volume

            base = ts_today[0].replace(hour=9, minute=15, second=0, microsecond=0) if ts_today else None

            def _to_min(ts):
                if base is None:
                    return 0.0
                t = ts.replace(tzinfo=None)
                b = base.replace(tzinfo=None)
                return (t - b).total_seconds() / 60.0

            xs_today = [_to_min(ts) for ts in ts_today]
            xs_all   = xs_today   # only today in 1D mode

            # tick formatter: minute index → "HH:MM"
            _ref = base or datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)
            def _fmt_1d(values):
                labels = []
                for v in values:
                    m = int(round(v))
                    if 0 <= m < MINUTES_PER_SESSION:
                        labels.append((_ref + timedelta(minutes=m)).strftime("%H:%M"))
                    else:
                        labels.append("")
                return labels

            self._price_axis.set_formatter(_fmt_1d)
            self._cvd_axis.set_formatter(_fmt_1d)

            self._last_x_indices = list(xs_today)

            # Plot: only today (prev curve hidden)
            self._price_prev.clear()
            self._cvd_prev.clear()
            self._price_day_sep.hide()
            self._cvd_day_sep.hide()

            if xs_today and len(xs_today) == len(px_today):
                self._price_today.setData(xs_today, list(px_today))
                self._cvd_today.setData(xs_today, list(cvd_today))
            else:
                self._price_today.clear()
                self._cvd_today.clear()

            # Set X range to full session
            self.cvd_plot.setXRange(0, MINUTES_PER_SESSION - 1, padding=0.01)

            # Build arrays for overlay (full: prev + today if 1D we use today only)
            _ts_for_overlay   = ts_today
            _px_for_overlay   = px_today
            _vol_for_overlay  = vol_today
            _cvd_for_overlay  = cvd_today
            _xs_for_overlay   = xs_today

        else:
            # ── 2D mode: sequential index 0…N-1 ──────────────────────────
            xs_all = list(range(n))
            self._last_x_indices = xs_all

            # day separator
            if has_two and self._session_x_break is not None:
                sx = int(self._session_x_break)
                self._price_day_sep.setValue(sx - 0.5)
                self._cvd_day_sep.setValue(sx - 0.5)
                self._price_day_sep.show()
                self._cvd_day_sep.show()
            else:
                self._price_day_sep.hide()
                self._cvd_day_sep.hide()

            if has_two and self._session_x_break is not None:
                split = int(self._session_x_break)
                xs_prev  = xs_all[:split]
                xs_cur   = xs_all[split:]
                px_prev  = self._all_price[:split]
                px_cur   = self._all_price[split:]
                cvd_prev = self._all_cvd[:split]
                cvd_cur  = self._all_cvd[split:]
            else:
                xs_prev = []; px_prev = []; cvd_prev = []
                xs_cur  = xs_all
                px_cur  = self._all_price
                cvd_cur = self._all_cvd

            # tick formatter: sequential index → "HH:MM"
            _ts_snap = list(self._all_timestamps)
            def _fmt_2d(values):
                labels = []
                for v in values:
                    idx = int(round(v))
                    if 0 <= idx < len(_ts_snap):
                        labels.append(_ts_snap[idx].strftime("%H:%M"))
                    else:
                        labels.append("")
                return labels

            self._price_axis.set_formatter(_fmt_2d)
            self._cvd_axis.set_formatter(_fmt_2d)

            if xs_prev and len(xs_prev) == len(px_prev):
                self._price_prev.setData(xs_prev, px_prev)
                self._cvd_prev.setData(xs_prev, cvd_prev)
            else:
                self._price_prev.clear()
                self._cvd_prev.clear()

            if xs_cur and len(xs_cur) == len(px_cur):
                self._price_today.setData(xs_cur, px_cur)
                self._cvd_today.setData(xs_cur, cvd_cur)
            else:
                self._price_today.clear()
                self._cvd_today.clear()

            self.cvd_plot.setXRange(0, n - 1, padding=0.02)

            _ts_for_overlay   = self._all_timestamps
            _px_for_overlay   = self._all_price
            _vol_for_overlay  = self._all_volume
            _cvd_for_overlay  = self._all_cvd
            _xs_for_overlay   = xs_all

        # Auto-range Y axes
        self.cvd_plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.price_plot.enableAutoRange(axis=pg.ViewBox.YAxis)

        # Render EMAs + VWAP overlay
        self._render_overlays_with(
            _xs_for_overlay, _px_for_overlay, _vol_for_overlay,
            _cvd_for_overlay, _ts_for_overlay,
        )

    def _render_overlays(self):
        """Re-render EMAs / VWAP from whatever data _render_from_cache last used."""
        if not self._all_timestamps:
            return
        # determine current split
        n = len(self._all_timestamps)
        sessions = sorted({ts.date() for ts in self._all_timestamps})
        has_two  = len(sessions) == 2

        if not self._two_day:
            if has_two and self._session_x_break is not None:
                split = int(self._session_x_break)
                ts    = self._all_timestamps[split:]
                px    = self._all_price[split:]
                vol   = self._all_volume[split:]
                cvd   = self._all_cvd[split:]

                base = ts[0].replace(hour=9, minute=15, second=0, microsecond=0) if ts else None
                def _to_min2(t_):
                    if base is None: return 0.0
                    return (t_.replace(tzinfo=None) - base.replace(tzinfo=None)).total_seconds() / 60.0
                xs = [_to_min2(t_) for t_ in ts]
            else:
                ts  = self._all_timestamps; px = self._all_price
                vol = self._all_volume;     cvd = self._all_cvd
                base = ts[0].replace(hour=9, minute=15, second=0, microsecond=0) if ts else None
                def _to_min3(t_):
                    if base is None: return 0.0
                    return (t_.replace(tzinfo=None) - base.replace(tzinfo=None)).total_seconds() / 60.0
                xs = [_to_min3(t_) for t_ in ts]
        else:
            ts  = self._all_timestamps; px = self._all_price
            vol = self._all_volume;     cvd = self._all_cvd
            xs  = list(range(n))

        self._render_overlays_with(xs, px, vol, cvd, ts)

    def _render_overlays_with(self, xs, px, vol, cvd, ts):
        """Plot EMA + VWAP curves given pre-computed x and data arrays."""
        if not xs or len(xs) != len(px):
            return

        px_arr  = np.asarray(px,  dtype=float)
        cvd_arr = np.asarray(cvd, dtype=float)
        vol_arr = np.asarray(vol, dtype=float)

        show10 = self.cb_ema10.isChecked()
        show21 = self.cb_ema21.isChecked()
        show51 = self.cb_ema51.isChecked()
        showvw = self.cb_vwap.isChecked()

        def _update(curve, data, show):
            if show and len(data) == len(xs):
                curve.setData(xs, data)
                curve.show()
            else:
                curve.clear()
                curve.hide()

        _update(self._price_ema10, calculate_ema(px_arr,  10) if show10 else [], show10)
        _update(self._price_ema21, calculate_ema(px_arr,  21) if show21 else [], show21)
        _update(self._price_ema51, calculate_ema(px_arr,  51) if show51 else [], show51)
        _update(self._cvd_ema10,   calculate_ema(cvd_arr, 10) if show10 else [], show10)
        _update(self._cvd_ema21,   calculate_ema(cvd_arr, 21) if show21 else [], show21)
        _update(self._cvd_ema51,   calculate_ema(cvd_arr, 51) if show51 else [], show51)

        if showvw and len(vol_arr) == len(px_arr):
            sk = [t.date() for t in ts] if ts else None
            vwap = calculate_vwap(px_arr, vol_arr, session_keys=sk)
            self._price_vwap.setData(xs, vwap)
            self._price_vwap.show()
        else:
            self._price_vwap.clear()
            self._price_vwap.hide()

    # -------------------------------------------------------- crosshair ------

    def _on_mouse_moved(self, pos):
        # figure out which chart the event came from
        sender = self.sender()
        if sender is self.price_plot.scene():
            vb = self.price_plot.getViewBox()
        else:
            vb = self.cvd_plot.getViewBox()

        if not vb.sceneBoundingRect().contains(pos):
            self._hide_crosshair()
            return

        mp = vb.mapSceneToView(pos)
        x  = mp.x()
        y  = mp.y()

        self._price_vline.setValue(x); self._price_vline.show()
        self._cvd_vline.setValue(x);   self._cvd_vline.show()
        self._price_hline.setValue(y if sender is self.price_plot.scene() else
                                   self.price_plot.getViewBox().mapSceneToView(pos).y())
        self._cvd_hline.setValue(y if sender is self.cvd_plot.scene() else
                                 self.cvd_plot.getViewBox().mapSceneToView(pos).y())
        self._price_hline.show(); self._cvd_hline.show()

    def _on_mouse_clicked(self, *_):
        self._hide_crosshair()

    def _hide_crosshair(self):
        for l in (self._price_vline, self._price_hline,
                  self._cvd_vline,   self._cvd_hline):
            l.hide()

    # --------------------------------------------------------- helpers ------

    @staticmethod
    def _cvd_y_tick_strings(values, *_):
        labels = []
        for v in values:
            if abs(v) >= 1_000_000:
                labels.append(f"{v/1_000_000:.1f}M")
            elif abs(v) >= 1_000:
                labels.append(f"{v/1_000:.0f}K")
            else:
                labels.append(f"{int(v)}")
        return labels

    def _set_status(self, state: str):
        colours = {
            "loading": _C["ema21"],   # amber
            "live":    _C["vwap"],    # green
            "hist":    _C["ema10"],   # cyan
            "error":   _C["ema51"],   # red
        }
        self.lbl_status.setStyleSheet(
            f"color: {colours.get(state, _C['text_dim'])}; "
            "font-size: 12px; background: transparent;"
        )

    # -------------------------------------------------------- keyboard -------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Left:
            self._go_back()
        elif event.key() == Qt.Key_Right:
            self._go_forward()
        elif event.key() in (Qt.Key_1,):
            self.btn_1d.setChecked(True)
            self._on_day_mode_toggled()
        elif event.key() in (Qt.Key_2,):
            self.btn_1d.setChecked(False)
            self._on_day_mode_toggled()
        else:
            super().keyPressEvent(event)

    # -------------------------------------------------------- cleanup --------

    def closeEvent(self, event):
        self._refresh_timer.stop()
        if self._fetch_worker is not None:
            with suppress(Exception):
                self._fetch_worker.quit_thread()
        super().closeEvent(event)


# keep suppress import available
from contextlib import suppress  # noqa: E402  (imported at bottom intentionally)