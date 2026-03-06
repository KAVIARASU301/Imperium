"""
Price & CVD Chart Dialog
========================
Standalone dialog showing Price (top) + CVD (bottom) with:
  • Date navigator  (previous / next trading day)
  • 1D / 2D toggle
  • Timeframe selector: 1m | 3m | 5m | 15m | 30m
  • Chart type toggle: line ↔ candlestick (for any timeframe)
  • EMA 10 / 21 / 51 toggles  (both charts, always on close)
  • VWAP toggle               (price chart)
  • Auto-refresh (live mode, every 3 s)
  • Linked X-axes + synchronised crosshair
  • Explicit Y-range: chart always fills available space correctly
"""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime, timedelta

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread, QTimer, QRectF, QPointF, QEvent
from PySide6.QtGui import QPicture, QPainter, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)
from pyqtgraph import AxisItem

from core.cvd.constants import MINUTES_PER_SESSION
from core.cvd.data_worker import _DataFetchWorker
from core.cvd.indicators import calculate_ema, calculate_vwap

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour palette
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
    "price":       "#FFE57F",
    "price_prev":  "#5A6070",
    "cvd":         "#26C6DA",
    "cvd_prev":    "#3A5060",
    "bull":        "#26A69A",
    "bear":        "#EF5350",
    "bull_prev":   "#1A4A46",
    "bear_prev":   "#5A2022",
    "cvd_bull":    "#26C6DA",
    "cvd_bear":    "#FF6B6B",
    "ema10":       "#00D9FF",
    "ema21":       "#FFD700",
    "ema51":       "#FF6B6B",
    "vwap":        "#00E676",
    "day_sep":     "#2A3F58",
}

_TOOLBAR_BTN = """
    QPushButton {{
        background: #151D2B; color: {fg};
        border: 1px solid #1E2D40; border-radius: 4px;
        padding: 0px 8px; font-size: 11px; font-weight: 700;
        min-height: 22px; min-width: {mw}px;
    }}
    QPushButton:hover {{ border: 1px solid #4D9FFF; background: #1C2638; }}
    QPushButton:checked {{ background: {chk_bg}; color: {chk_fg}; border: 1px solid {chk_bg}; }}
    QPushButton:pressed {{ background: #0D1117; }}
"""

def _btn(text, fg="#8A99B3", mw=36, checkable=False, chk_bg="#26A69A", chk_fg="#000"):
    b = QPushButton(text)
    b.setCheckable(checkable)
    b.setStyleSheet(_TOOLBAR_BTN.format(fg=fg, mw=mw, chk_bg=chk_bg, chk_fg=chk_fg))
    b.setFixedHeight(22)
    return b

def _ema_cb(label, color):
    cb = QCheckBox(label)
    cb.setStyleSheet(f"""
        QCheckBox {{ color: {color}; font-weight: 700; font-size: 11px; spacing: 3px; }}
        QCheckBox::indicator {{
            width: 12px; height: 12px; border: 1px solid {color};
            border-radius: 2px; background: #0D1117;
        }}
        QCheckBox::indicator:checked {{ background: {color}; }}
    """)
    cb.setFixedHeight(22)
    return cb

def _sep():
    s = QLabel("|")
    s.setStyleSheet("color: #2A3F58; font-size: 12px; background: transparent;")
    return s


# ---------------------------------------------------------------------------
# Y-range helper — explicit bounds from data so chart always fills viewport
# ---------------------------------------------------------------------------
def _y_range(values: list, padding: float = 0.04) -> tuple[float, float]:
    """
    Compute explicit Y min/max with percentage padding.
    Pass combined high+low lists for OHLC, or close list for lines.
    padding=0.04 → 4% headroom above and below.
    """
    if not values:
        return 0.0, 1.0
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if not len(arr):
        return 0.0, 1.0
    ymin, ymax = float(arr.min()), float(arr.max())
    span = ymax - ymin
    if span < 1e-9:
        span = abs(ymin) * 0.02 or 1.0
    pad = span * padding
    return ymin - pad, ymax + pad


def _fmt_axis_marker(value: float, for_cvd: bool = False) -> str:
    if not np.isfinite(value):
        return ""
    if not for_cvd:
        return f"{value:.0f}"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


# ---------------------------------------------------------------------------
# OHLC Candlestick — QPicture based (single paint replay, zero per-bar cost)
# ---------------------------------------------------------------------------
class _OHLCItem(pg.GraphicsObject):
    """
    Institutional-grade OHLC renderer.

    Visual rules (same as high-quality HTML canvas charts):
    ─ Wick   : 1 px, full height, center-aligned
    ─ Body   : 82% of allocated slot width (16% gap between candles)
    ─ Fill   : solid color with slight alpha for depth
    ─ Border : 1 px outline, same hue, no extra width
    ─ Doji   : horizontal tick across full body width (no invisible rect)
    ─ AA     : ON  → smooth wick tips, clean diagonal sub-pixels
    """

    def __init__(self, bull_color: str, bear_color: str, alpha: int = 225):
        super().__init__()

        bull_qc = QColor(bull_color)
        bear_qc = QColor(bear_color)

        bull_fill = QColor(bull_qc)
        bull_fill.setAlpha(alpha)
        bear_fill = QColor(bear_qc)
        bear_fill.setAlpha(alpha)

        self._bull_wick = pg.mkPen(bull_qc, width=1)
        self._bear_wick = pg.mkPen(bear_qc, width=1)

        self._bull_body_pen = pg.mkPen(bull_qc, width=1)
        self._bear_body_pen = pg.mkPen(bear_qc, width=1)

        self._bull_brush = pg.mkBrush(bull_fill)
        self._bear_brush = pg.mkBrush(bear_fill)

        self._data: list = []
        self._half_w: float = 0.35
        self._picture = None
        self._bounds = QRectF()

    def setData(self, data: list, half_width: float = 0.35):
        """data: list of (x, open, high, low, close)  — x in chart units"""
        self._data   = data
        self._half_w = half_width
        self._build()
        self.prepareGeometryChange()
        self.update()

    def clear(self):
        self._data    = []
        self._picture = None
        self._bounds  = QRectF()
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter, *args):
        if self._picture:
            self._picture.play(painter)

    def boundingRect(self) -> QRectF:
        return self._bounds

    def _build(self):
        if not self._data:
            self._picture = None
            self._bounds  = QRectF()
            return

        pic = QPicture()
        p   = QPainter(pic)
        p.setRenderHint(QPainter.Antialiasing, True)

        body_hw = self._half_w * 0.82

        all_x: list = []
        all_h: list = []
        all_l: list = []
        for x, o, h, l, c in self._data:
            bull = c >= o
            wick_pen = self._bull_wick if bull else self._bear_wick
            body_pen = self._bull_body_pen if bull else self._bear_body_pen
            brush = self._bull_brush if bull else self._bear_brush

            p.setPen(wick_pen)
            p.drawLine(QPointF(x, l), QPointF(x, h))

            all_x.append(x)
            all_h.append(h)
            all_l.append(l)

            bt = max(o, c)
            bb = min(o, c)
            bh = bt - bb

            p.setPen(body_pen)
            if bh < 1e-9:
                p.drawLine(QPointF(x - body_hw, c), QPointF(x + body_hw, c))
            else:
                rect = QRectF(x - body_hw, bb, body_hw * 2, bh)
                p.fillRect(rect, brush)
                p.drawRect(rect)

        p.end()
        self._picture = pic

        if all_x:
            fw = self._half_w
            self._bounds = QRectF(
                min(all_x) - fw,
                min(all_l),
                max(all_x) - min(all_x) + fw * 2,
                max(all_h) - min(all_l),
            )


# ---------------------------------------------------------------------------
# Custom time axis
# ---------------------------------------------------------------------------
class _TimeAxis(AxisItem):
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


_TIMEFRAMES = [(1, "1m"), (3, "3m"), (5, "5m"), (15, "15m"), (30, "30m")]


# ---------------------------------------------------------------------------
# Main Dialog
# ---------------------------------------------------------------------------
class PriceCVDChartDialog(QDialog):
    REFRESH_INTERVAL_MS = 3000

    def __init__(self, kite, instrument_token: int, symbol: str, parent=None):
        super().__init__(parent)

        self.kite             = kite
        self.instrument_token = instrument_token
        self.symbol           = symbol

        self.live_mode        = True
        self._two_day         = False
        self._cvd_rebased     = True
        self._selected_tf     = 1
        self._use_ohlc        = False
        self._chart_style_overridden = False
        self.current_date     = None
        self.previous_date    = None

        self._all_timestamps  = []
        self._all_price       = []
        self._all_price_open  = []
        self._all_price_high  = []
        self._all_price_low   = []
        self._all_volume      = []
        self._all_cvd         = []
        self._all_cvd_open    = []
        self._all_cvd_high    = []
        self._all_cvd_low     = []
        self._session_x_break = None
        self._price_last_value = None
        self._cvd_last_value = None

        self._fetch_worker = None
        self._fetch_thread = None
        self._is_loading   = False

        self.setWindowTitle(f"Price & CVD Chart — {symbol}")
        self.setObjectName("priceCVDChartDialog")
        self.setMinimumSize(960, 580)
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
        )
        self.setStyleSheet(f"""
            QDialog#priceCVDChartDialog {{ background: {_C["bg"]}; }}
            QLabel {{ color: {_C["text_2"]}; font-size: 11px; background: transparent; }}
        """)

        self._setup_ui()

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.current_date  = today
        self.previous_date = self._prev_trading_day(today)
        self._update_date_label()
        self._load_and_plot()

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

    def _build_toolbar(self, root):
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

        left = QHBoxLayout()
        left.setSpacing(6)

        lbl_ema = QLabel("EMA")
        lbl_ema.setStyleSheet(
            "color:#6A7A90;font-size:10px;font-weight:700;background:transparent;")
        left.addWidget(lbl_ema)

        self.cb_ema10 = _ema_cb("10", _C["ema10"])
        self.cb_ema21 = _ema_cb("21", _C["ema21"])
        self.cb_ema51 = _ema_cb("51", _C["ema51"])
        self.cb_ema51.setChecked(True)
        for cb in (self.cb_ema10, self.cb_ema21, self.cb_ema51):
            cb.toggled.connect(self._on_indicator_toggled)
            left.addWidget(cb)

        left.addWidget(_sep())

        self.cb_vwap = _ema_cb("VWAP", _C["vwap"])
        self.cb_vwap.toggled.connect(self._on_indicator_toggled)
        left.addWidget(self.cb_vwap)

        center = QHBoxLayout()
        center.setSpacing(6)

        self.btn_back = _btn("◀", mw=24)
        self.btn_back.clicked.connect(self._go_back)

        self.lbl_dates = QLabel("—")
        self.lbl_dates.setAlignment(Qt.AlignCenter)
        self.lbl_dates.setStyleSheet(
            "font-size:11px;font-weight:600;color:#C0CCE0;background:transparent;")
        self.lbl_dates.setMinimumWidth(280)

        self.btn_fwd = _btn("▶", mw=24)
        self.btn_fwd.clicked.connect(self._go_forward)

        center.addWidget(self.btn_back)
        center.addWidget(self.lbl_dates)
        center.addWidget(self.btn_fwd)

        right = QHBoxLayout()
        right.setSpacing(6)

        lbl_tf = QLabel("TF")
        lbl_tf.setStyleSheet(
            "color:#6A7A90;font-size:10px;font-weight:700;background:transparent;")
        right.addWidget(lbl_tf)

        self.cb_tf = QComboBox()
        self.cb_tf.setFixedHeight(22)
        self.cb_tf.setStyleSheet("""
            QComboBox {
                background: #151D2B;
                color: #8A99B3;
                border: 1px solid #1E2D40;
                border-radius: 4px;
                padding: 0px 18px 0px 8px;
                font-size: 11px;
                font-weight: 700;
                min-width: 56px;
            }
            QComboBox:hover { border: 1px solid #4D9FFF; background: #1C2638; }
            QComboBox::drop-down { border: none; }
        """)
        for tf_min, tf_label in _TIMEFRAMES:
            self.cb_tf.addItem(tf_label, tf_min)
        self.cb_tf.currentIndexChanged.connect(self._on_tf_combo_changed)
        right.addWidget(self.cb_tf)

        self.btn_chart_style = _btn("Line", fg=_C["text_1"], mw=78, checkable=True,
                                    chk_bg="#26A69A", chk_fg="#000")
        self.btn_chart_style.setToolTip("Toggle between line and candlestick charts")
        self.btn_chart_style.toggled.connect(self._on_chart_style_toggled)
        right.addWidget(self.btn_chart_style)

        right.addWidget(_sep())

        self.btn_1d = _btn("1D", fg=_C["text_1"], mw=34, checkable=True,
                           chk_bg="#26A69A", chk_fg="#000")
        self.btn_2d = _btn("2D", fg=_C["text_1"], mw=34, checkable=True,
                           chk_bg="#26A69A", chk_fg="#000")
        self.btn_1d.clicked.connect(lambda: self._set_day_mode(False))
        self.btn_2d.clicked.connect(lambda: self._set_day_mode(True))
        right.addWidget(self.btn_1d)
        right.addWidget(self.btn_2d)

        self.btn_cvd_rebase = _btn("Rebased CVD", fg=_C["text_2"], mw=90, checkable=True,
                                   chk_bg="#2A3B5C", chk_fg="#FFFFFF")
        self.btn_cvd_rebase.setChecked(True)
        self.btn_cvd_rebase.setEnabled(False)
        self.btn_cvd_rebase.setToolTip("Available in 2D comparison mode")
        self.btn_cvd_rebase.toggled.connect(self._on_cvd_rebase_toggled)
        right.addWidget(self.btn_cvd_rebase)

        self._set_day_mode(False, reload=False)

        row.addLayout(left)
        row.addStretch(1)
        row.addLayout(center)
        row.addStretch(1)
        row.addLayout(right)

        self.lbl_status = QLabel("●")
        self.lbl_status.setStyleSheet(
            f"color:{_C['ema51']};font-size:12px;background:transparent;")
        row.addSpacing(6)
        row.addWidget(self.lbl_status)
        root.addWidget(bar)

    def _build_charts(self, root):
        # ── Price chart ────────────────────────────────────────────────────
        self._price_axis = _TimeAxis("bottom")
        self._price_axis.setStyle(showValues=False)

        self.price_plot = pg.PlotWidget(axisItems={"bottom": self._price_axis})
        self.price_plot.setBackground(_C["chart_bg"])
        self.price_plot.showGrid(x=True, y=True, alpha=0.09)
        self.price_plot.setMenuEnabled(False)
        self.price_plot.setMinimumHeight(200)
        # Disable pyqtgraph's auto-range — we compute and set it explicitly
        self.price_plot.getViewBox().disableAutoRange()

        py_ax = self.price_plot.getAxis("left")
        py_ax.setStyle(showValues=False)
        py_ax.setPen(pg.mkPen(_C["border"]))

        self.price_plot.showAxis("right", show=True)
        py_right = self.price_plot.getAxis("right")
        py_right.setWidth(72)
        py_right.setTextPen(pg.mkPen(_C["price"]))
        py_right.setPen(pg.mkPen(_C["border"]))
        py_right.enableAutoSIPrefix(False)

        self._price_prev       = pg.PlotCurveItem(pen=pg.mkPen(_C["price_prev"], width=1.8, style=Qt.DashLine))
        self._price_today      = pg.PlotCurveItem(pen=pg.mkPen(_C["price"],      width=2.2))
        self._price_ohlc_prev  = _OHLCItem(_C["bull_prev"], _C["bear_prev"])
        self._price_ohlc_today = _OHLCItem(_C["bull"],      _C["bear"])
        self._price_ema10      = pg.PlotCurveItem(pen=pg.mkPen(_C["ema10"], width=1.6))
        self._price_ema21      = pg.PlotCurveItem(pen=pg.mkPen(_C["ema21"], width=1.6))
        self._price_ema51      = pg.PlotCurveItem(pen=pg.mkPen(_C["ema51"], width=1.8))
        self._price_vwap       = pg.PlotCurveItem(pen=pg.mkPen(_C["vwap"],  width=1.5, style=Qt.DashDotLine))

        for item in (self._price_prev, self._price_today,
                     self._price_ohlc_prev, self._price_ohlc_today,
                     self._price_ema10, self._price_ema21, self._price_ema51,
                     self._price_vwap):
            self.price_plot.addItem(item)

        self._price_day_sep = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(_C["day_sep"], width=1, style=Qt.DashLine))
        self._price_day_sep.hide()
        self.price_plot.addItem(self._price_day_sep)

        self._price_vline = pg.InfiniteLine(angle=90, movable=False,
                                            pen=pg.mkPen("#FFFFFF", width=0.6, style=Qt.DotLine))
        self._price_hline = pg.InfiniteLine(angle=0, movable=False,
                                            pen=pg.mkPen("#FFFFFF", width=0.6, style=Qt.DotLine))
        self._price_vline.hide(); self._price_hline.hide()
        self.price_plot.addItem(self._price_vline, ignoreBounds=True)
        self.price_plot.addItem(self._price_hline, ignoreBounds=True)

        self._price_level_badge = QLabel(self.price_plot)
        self._price_level_badge.hide()
        self._price_level_badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._price_level_badge.setAlignment(Qt.AlignCenter)
        self._price_level_badge.setStyleSheet(
            f"background:{_C['price']};color:#0A0F17;border:1px solid {_C['price']};"
            "font-size:11px;font-weight:700;padding:1px 4px;")
        self.price_plot.getViewBox().sigRangeChanged.connect(self._position_level_badges)
        self.price_plot.installEventFilter(self)
        root.addWidget(self.price_plot, 3)

        # ── CVD chart ──────────────────────────────────────────────────────
        self._cvd_axis = _TimeAxis("bottom")
        self.cvd_plot  = pg.PlotWidget(axisItems={"bottom": self._cvd_axis})
        self.cvd_plot.setBackground(_C["chart_bg"])
        self.cvd_plot.showGrid(x=True, y=True, alpha=0.09)
        self.cvd_plot.setMenuEnabled(False)
        self.cvd_plot.setMinimumHeight(150)
        self.cvd_plot.getViewBox().disableAutoRange()

        cy_ax = self.cvd_plot.getAxis("left")
        cy_ax.setStyle(showValues=False)
        cy_ax.setPen(pg.mkPen(_C["border"]))

        self.cvd_plot.showAxis("right", show=True)
        cy_right = self.cvd_plot.getAxis("right")
        cy_right.setWidth(72)
        cy_right.setTextPen(pg.mkPen(_C["cvd"]))
        cy_right.setPen(pg.mkPen(_C["border"]))
        cy_right.enableAutoSIPrefix(False)
        cy_right.tickStrings = self._cvd_y_tick_strings

        cx_ax = self.cvd_plot.getAxis("bottom")
        cx_ax.setHeight(28)
        cx_ax.setTextPen(pg.mkPen(_C["text_2"]))
        cx_ax.setPen(pg.mkPen(_C["border"]))

        self._cvd_prev       = pg.PlotCurveItem(pen=pg.mkPen(_C["cvd_prev"], width=1.8, style=Qt.DashLine))
        self._cvd_today      = pg.PlotCurveItem(pen=pg.mkPen(_C["cvd"],      width=2.2))
        self._cvd_ohlc_prev  = _OHLCItem(_C["bull_prev"], _C["bear_prev"])
        self._cvd_ohlc_today = _OHLCItem(_C["cvd_bull"],  _C["cvd_bear"])
        self._cvd_ema10      = pg.PlotCurveItem(pen=pg.mkPen(_C["ema10"], width=1.5))
        self._cvd_ema21      = pg.PlotCurveItem(pen=pg.mkPen(_C["ema21"], width=1.5))
        self._cvd_ema51      = pg.PlotCurveItem(pen=pg.mkPen(_C["ema51"], width=1.6))

        for item in (self._cvd_prev, self._cvd_today,
                     self._cvd_ohlc_prev, self._cvd_ohlc_today,
                     self._cvd_ema10, self._cvd_ema21, self._cvd_ema51):
            self.cvd_plot.addItem(item)

        self._cvd_day_sep = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(_C["day_sep"], width=1, style=Qt.DashLine))
        self._cvd_day_sep.hide()
        self.cvd_plot.addItem(self._cvd_day_sep)

        self._cvd_vline = pg.InfiniteLine(angle=90, movable=False,
                                          pen=pg.mkPen("#FFFFFF", width=0.6, style=Qt.DotLine))
        self._cvd_hline = pg.InfiniteLine(angle=0, movable=False,
                                          pen=pg.mkPen("#FFFFFF", width=0.6, style=Qt.DotLine))
        self._cvd_vline.hide(); self._cvd_hline.hide()
        self.cvd_plot.addItem(self._cvd_vline, ignoreBounds=True)
        self.cvd_plot.addItem(self._cvd_hline, ignoreBounds=True)

        self._cvd_level_badge = QLabel(self.cvd_plot)
        self._cvd_level_badge.hide()
        self._cvd_level_badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._cvd_level_badge.setAlignment(Qt.AlignCenter)
        self._cvd_level_badge.setStyleSheet(
            f"background:{_C['cvd']};color:#0A0F17;border:1px solid {_C['cvd']};"
            "font-size:11px;font-weight:700;padding:1px 4px;")
        self.cvd_plot.getViewBox().sigRangeChanged.connect(self._position_level_badges)
        self.cvd_plot.installEventFilter(self)
        root.addWidget(self.cvd_plot, 2)

        self.price_plot.setXLink(self.cvd_plot)

        self.price_plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.cvd_plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.price_plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        self.cvd_plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)

        self._price_ema10.hide(); self._cvd_ema10.hide()
        self._price_ema21.hide(); self._cvd_ema21.hide()
        self._price_vwap.hide()

    # ---------------------------------------------------------------- dates --

    @staticmethod
    def _prev_trading_day(date):
        d = date - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    @staticmethod
    def _next_trading_day(date):
        d = date + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    def _update_date_label(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cd, pd_ = self.current_date, self.previous_date
        self.lbl_dates.setText(
            f"<span style='color:#5588BB'>{pd_.strftime('%a %b %d') if pd_ else '—'}</span>"
            f"<span style='color:#3A4A60'> ▷ </span>"
            f"<span style='color:#A0BFD0'>{cd.strftime('%a %b %d, %Y') if cd else '—'}</span>"
        )
        self.btn_fwd.setEnabled(bool(cd) and cd < today)

    def _go_back(self):
        if not self.current_date: return
        self.live_mode     = False
        self.current_date  = self._prev_trading_day(self.current_date)
        self.previous_date = self._prev_trading_day(self.current_date)
        self._update_date_label()
        self._load_and_plot()

    def _go_forward(self):
        if not self.current_date: return
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        nxt = self._next_trading_day(self.current_date)
        if nxt > today: return
        self.current_date  = nxt
        self.live_mode     = (nxt >= today)
        self.previous_date = self._prev_trading_day(self.current_date)
        self._update_date_label()
        self._load_and_plot()

    # ---------------------------------------------------------------- modes --

    def _set_day_mode(self, two_day: bool, reload: bool = True):
        if self._two_day == two_day and self.btn_1d.isChecked() == (not two_day):
            return
        self._two_day = two_day
        self.btn_1d.setChecked(not two_day)
        self.btn_2d.setChecked(two_day)
        self.btn_cvd_rebase.setEnabled(self._two_day)
        if reload and self._all_timestamps:
            self._render_from_cache()

    def _on_cvd_rebase_toggled(self, checked: bool):
        self._cvd_rebased = checked
        self.btn_cvd_rebase.setText("Rebased CVD" if checked else "Session CVD")
        if self._all_timestamps and self._two_day:
            self._render_from_cache()

    def _on_indicator_toggled(self, *_):
        if self._all_timestamps:
            self._render_overlays()

    def _on_tf_changed(self, tf_minutes: int):
        if tf_minutes == self._selected_tf: return
        self._selected_tf = tf_minutes
        if not self._chart_style_overridden:
            self._set_chart_style(tf_minutes > 1)
        self._load_and_plot()

    def _set_chart_style(self, use_ohlc: bool):
        self._use_ohlc = use_ohlc
        with suppress(Exception):
            self.btn_chart_style.blockSignals(True)
            self.btn_chart_style.setChecked(use_ohlc)
            self.btn_chart_style.blockSignals(False)
        self.btn_chart_style.setText("Candles" if use_ohlc else "Line")

    def _on_chart_style_toggled(self, checked: bool):
        self._chart_style_overridden = True
        self._set_chart_style(checked)
        if self._all_timestamps:
            self._render_from_cache()

    def _on_tf_combo_changed(self, index: int):
        tf_minutes = self.cb_tf.itemData(index)
        if tf_minutes is not None:
            self._on_tf_changed(int(tf_minutes))

    # --------------------------------------------------------------- loading -

    def _load_and_plot(self):
        if self._is_loading: return
        if not self.kite or not getattr(self.kite, "access_token", None): return

        self._set_status("loading")

        if self.live_mode:
            to_dt   = datetime.now()
            from_dt = to_dt - timedelta(days=5)
        else:
            to_dt   = self.current_date + timedelta(days=1)
            from_dt = self.previous_date

        self._is_loading   = True
        self._fetch_thread = QThread(self)
        self._fetch_worker = _DataFetchWorker(
            self.kite, self.instrument_token,
            from_dt, to_dt,
            self._selected_tf, False,
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

        sessions = sorted(cvd_df["session"].unique())

        self._all_timestamps = []
        self._all_price      = []
        self._all_price_open = []
        self._all_price_high = []
        self._all_price_low  = []
        self._all_volume     = []
        self._all_cvd        = []
        self._all_cvd_open   = []
        self._all_cvd_high   = []
        self._all_cvd_low    = []
        self._session_x_break = None
        self._price_last_value = None
        self._cvd_last_value = None

        for i, sess in enumerate(sessions):
            cvd_s   = cvd_df[cvd_df["session"] == sess]
            price_s = price_df[price_df["session"] == sess]

            self._all_timestamps.extend(cvd_s.index.tolist())

            # price OHLC
            self._all_price.extend(price_s["close"].tolist())
            self._all_price_open.extend(
                price_s["open"].tolist() if "open" in price_s.columns
                else price_s["close"].tolist())
            self._all_price_high.extend(
                price_s["high"].tolist() if "high" in price_s.columns
                else price_s["close"].tolist())
            self._all_price_low.extend(
                price_s["low"].tolist() if "low" in price_s.columns
                else price_s["close"].tolist())
            self._all_volume.extend(
                price_s["volume"].tolist() if "volume" in price_s.columns
                else [1.0] * len(price_s))

            # CVD OHLC — data_worker now always emits full OHLC (1m built first, then resampled)
            self._all_cvd.extend(cvd_s["close"].tolist())
            self._all_cvd_open.extend(
                cvd_s["open"].tolist() if "open" in cvd_s.columns
                else cvd_s["close"].tolist())
            self._all_cvd_high.extend(
                cvd_s["high"].tolist() if "high" in cvd_s.columns
                else cvd_s["close"].tolist())
            self._all_cvd_low.extend(
                cvd_s["low"].tolist() if "low" in cvd_s.columns
                else cvd_s["close"].tolist())

            if i == 0 and len(sessions) == 2:
                self._session_x_break = float(len(self._all_timestamps))

        self._render_from_cache()

    def _on_fetch_error(self, msg: str):
        self._is_loading = False
        self._set_status("error")
        logger.warning("[PriceCVDChart] fetch error: %s", msg)

    def _on_live_refresh(self):
        if self.isVisible() and self.live_mode:
            self._load_and_plot()

    # ------------------------------------------------------------ rendering --

    def _render_from_cache(self):
        if not self._all_timestamps:
            return

        tf       = self._selected_tf
        use_ohlc = self._use_ohlc
        sessions = sorted({ts.date() for ts in self._all_timestamps})
        has_two  = len(sessions) == 2

        # Toggle which item type is visible
        self._price_prev.setVisible(not use_ohlc)
        self._price_today.setVisible(not use_ohlc)
        self._price_ohlc_prev.setVisible(use_ohlc)
        self._price_ohlc_today.setVisible(use_ohlc)
        self._cvd_prev.setVisible(not use_ohlc)
        self._cvd_today.setVisible(not use_ohlc)
        self._cvd_ohlc_prev.setVisible(use_ohlc)
        self._cvd_ohlc_today.setVisible(use_ohlc)

        if not self._two_day:
            # ── 1D mode ──────────────────────────────────────────────────
            split = (int(self._session_x_break)
                     if has_two and self._session_x_break is not None else 0)

            ts_cur  = self._all_timestamps[split:]
            px_cur  = self._all_price[split:]
            po_cur  = self._all_price_open[split:]
            ph_cur  = self._all_price_high[split:]
            pl_cur  = self._all_price_low[split:]
            vol_cur = self._all_volume[split:]
            cvd_cur = self._all_cvd[split:]
            co_cur  = self._all_cvd_open[split:]
            ch_cur  = self._all_cvd_high[split:]
            cl_cur  = self._all_cvd_low[split:]

            base   = (ts_cur[0].replace(hour=9, minute=15, second=0, microsecond=0)
                      if ts_cur else None)
            offset = tf / 2.0 if use_ohlc else 0.0

            def _to_min(ts):
                if not base: return 0.0
                return (ts.replace(tzinfo=None) - base.replace(tzinfo=None)).total_seconds() / 60.0

            xs = [_to_min(ts) + offset for ts in ts_cur]

            _ref = base or datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)
            def _fmt_1d(values):
                return [
                    (_ref + timedelta(minutes=int(round(v - offset)))).strftime("%H:%M")
                    if 0 <= int(round(v - offset)) < MINUTES_PER_SESSION else ""
                    for v in values
                ]

            self._price_axis.set_formatter(_fmt_1d)
            self._cvd_axis.set_formatter(_fmt_1d)

            self._price_prev.clear();      self._price_ohlc_prev.clear()
            self._cvd_prev.clear();        self._cvd_ohlc_prev.clear()
            self._price_day_sep.hide();    self._cvd_day_sep.hide()

            if xs:
                hw = tf * 0.40 if use_ohlc else 0.35
                if use_ohlc:
                    self._price_ohlc_today.setData(
                        list(zip(xs, po_cur, ph_cur, pl_cur, px_cur)), hw)
                    self._cvd_ohlc_today.setData(
                        list(zip(xs, co_cur, ch_cur, cl_cur, cvd_cur)), hw)
                else:
                    self._price_today.setData(xs, list(px_cur))
                    self._cvd_today.setData(xs, list(cvd_cur))
            else:
                self._price_today.clear();      self._price_ohlc_today.clear()
                self._cvd_today.clear();        self._cvd_ohlc_today.clear()

            self.cvd_plot.setXRange(offset, MINUTES_PER_SESSION - 1 + offset, padding=0.01)

            # ── Explicit Y-range — use H+L so no wick gets clipped ───────
            p_ymin, p_ymax = _y_range(ph_cur + pl_cur if use_ohlc else px_cur)
            c_ymin, c_ymax = _y_range(ch_cur + cl_cur if use_ohlc else cvd_cur)
            self.price_plot.setYRange(p_ymin, p_ymax, padding=0)
            self.cvd_plot.setYRange(c_ymin,   c_ymax, padding=0)

            _ts_ov = ts_cur; _px_ov = px_cur; _vol_ov = vol_cur
            _cvd_ov = cvd_cur; _xs_ov = xs

        else:
            # ── 2D mode ───────────────────────────────────────────────────
            n = len(self._all_timestamps)
            offset = 0.5 if use_ohlc else 0.0
            xs_all = [i + offset for i in range(n)]

            if has_two and self._session_x_break is not None:
                sx = int(self._session_x_break)
                self._price_day_sep.setValue(sx - 0.5); self._price_day_sep.show()
                self._cvd_day_sep.setValue(sx - 0.5);   self._cvd_day_sep.show()
                sp = sx
                xs_prev  = xs_all[:sp];   xs_cur  = xs_all[sp:]
                px_prev  = self._all_price[:sp];       px_cur  = self._all_price[sp:]
                po_prev  = self._all_price_open[:sp];  po_cur  = self._all_price_open[sp:]
                ph_prev  = self._all_price_high[:sp];  ph_cur  = self._all_price_high[sp:]
                pl_prev  = self._all_price_low[:sp];   pl_cur  = self._all_price_low[sp:]
                vol_cur  = self._all_volume[sp:]
                cvd_prev = self._all_cvd[:sp];         cvd_cur = self._all_cvd[sp:]
                co_prev  = self._all_cvd_open[:sp];    co_cur  = self._all_cvd_open[sp:]
                ch_prev  = self._all_cvd_high[:sp];    ch_cur  = self._all_cvd_high[sp:]
                cl_prev  = self._all_cvd_low[:sp];     cl_cur  = self._all_cvd_low[sp:]

                if self._cvd_rebased and cvd_prev:
                    # Compare session deltas directly:
                    # - previous session closes at 0
                    # - current session opens at 0
                    prev_offset = cvd_prev[-1]
                    cur_offset = cvd_cur[0] if cvd_cur else prev_offset

                    cvd_prev = [v - prev_offset for v in cvd_prev]
                    cvd_cur = [v - cur_offset for v in cvd_cur]

                    co_prev = [v - prev_offset for v in co_prev]
                    co_cur = [v - cur_offset for v in co_cur]
                    ch_prev = [v - prev_offset for v in ch_prev]
                    ch_cur = [v - cur_offset for v in ch_cur]
                    cl_prev = [v - prev_offset for v in cl_prev]
                    cl_cur = [v - cur_offset for v in cl_cur]
            else:
                self._price_day_sep.hide(); self._cvd_day_sep.hide()
                xs_prev = []; px_prev = []; po_prev = []; ph_prev = []; pl_prev = []
                cvd_prev = []; co_prev = []; ch_prev = []; cl_prev = []
                xs_cur  = xs_all
                px_cur  = self._all_price;       po_cur = self._all_price_open
                ph_cur  = self._all_price_high;  pl_cur = self._all_price_low
                vol_cur = self._all_volume
                cvd_cur = self._all_cvd;   co_cur = self._all_cvd_open
                ch_cur  = self._all_cvd_high;    cl_cur = self._all_cvd_low

            _ts_snap = list(self._all_timestamps)
            def _fmt_2d(values):
                out = []
                for v in values:
                    idx = int(round(v - offset))
                    out.append(_ts_snap[idx].strftime("%H:%M")
                               if 0 <= idx < len(_ts_snap) else "")
                return out

            self._price_axis.set_formatter(_fmt_2d)
            self._cvd_axis.set_formatter(_fmt_2d)

            hw = 0.38
            if use_ohlc:
                if xs_prev:
                    self._price_ohlc_prev.setData(
                        list(zip(xs_prev, po_prev, ph_prev, pl_prev, px_prev)), hw)
                    self._cvd_ohlc_prev.setData(
                        list(zip(xs_prev, co_prev, ch_prev, cl_prev, cvd_prev)), hw)
                else:
                    self._price_ohlc_prev.clear(); self._cvd_ohlc_prev.clear()
                if xs_cur:
                    self._price_ohlc_today.setData(
                        list(zip(xs_cur, po_cur, ph_cur, pl_cur, px_cur)), hw)
                    self._cvd_ohlc_today.setData(
                        list(zip(xs_cur, co_cur, ch_cur, cl_cur, cvd_cur)), hw)
                else:
                    self._price_ohlc_today.clear(); self._cvd_ohlc_today.clear()
            else:
                if xs_prev:
                    self._price_prev.setData(xs_prev, px_prev)
                    self._cvd_prev.setData(xs_prev, cvd_prev)
                else:
                    self._price_prev.clear(); self._cvd_prev.clear()
                if xs_cur:
                    self._price_today.setData(xs_cur, px_cur)
                    self._cvd_today.setData(xs_cur, cvd_cur)
                else:
                    self._price_today.clear(); self._cvd_today.clear()

            self.cvd_plot.setXRange(0, n - 1 + offset, padding=0.02)

            # ── Explicit Y-range for 2D (prev + today combined) ───────────
            all_ph = ph_prev + ph_cur; all_pl = pl_prev + pl_cur
            all_ch = ch_prev + ch_cur; all_cl = cl_prev + cl_cur
            p_ymin, p_ymax = _y_range(
                (all_ph + all_pl) if use_ohlc else (px_prev + px_cur))
            c_ymin, c_ymax = _y_range(
                (all_ch + all_cl) if use_ohlc else (cvd_prev + cvd_cur))
            self.price_plot.setYRange(p_ymin, p_ymax, padding=0)
            self.cvd_plot.setYRange(c_ymin,   c_ymax, padding=0)

            # In 2D mode overlays (EMA/VWAP) must span both sessions.
            _ts_ov  = list(self._all_timestamps)
            _px_ov  = list(self._all_price)
            _vol_ov = list(self._all_volume)
            _cvd_ov = cvd_prev + cvd_cur
            _xs_ov  = list(xs_all)

        self._render_overlays_with(_xs_ov, _px_ov, _vol_ov, _cvd_ov, _ts_ov)
        self._update_last_value_markers(_px_ov, _cvd_ov)

    def _update_last_value_markers(self, px_values, cvd_values):
        if px_values:
            last_px = float(px_values[-1])
            self._price_last_value = last_px
            self._price_level_badge.setText(_fmt_axis_marker(last_px, for_cvd=False))
            self._price_level_badge.adjustSize()
            self._price_level_badge.show()
        else:
            self._price_last_value = None
            self._price_level_badge.hide()

        if cvd_values:
            last_cvd = float(cvd_values[-1])
            self._cvd_last_value = last_cvd
            self._cvd_level_badge.setText(_fmt_axis_marker(last_cvd, for_cvd=True))
            self._cvd_level_badge.adjustSize()
            self._cvd_level_badge.show()
        else:
            self._cvd_last_value = None
            self._cvd_level_badge.hide()

        self._position_level_badges()

    def eventFilter(self, watched, event):
        price_plot = getattr(self, "price_plot", None)
        cvd_plot = getattr(self, "cvd_plot", None)
        if watched in (price_plot, cvd_plot) and event.type() == QEvent.Resize:
            self._position_level_badges()
        return super().eventFilter(watched, event)

    def _position_level_badges(self, *_):
        self._position_badge(
            plot=self.price_plot,
            axis=self.price_plot.getAxis("right"),
            value=self._price_last_value,
            badge=self._price_level_badge,
        )
        self._position_badge(
            plot=self.cvd_plot,
            axis=self.cvd_plot.getAxis("right"),
            value=self._cvd_last_value,
            badge=self._cvd_level_badge,
        )

    def _position_badge(self, plot, axis, value, badge):
        if value is None or not np.isfinite(value) or not badge.isVisible():
            return

        vb = plot.getViewBox()
        vr = vb.viewRange()
        if not vr or len(vr) < 2:
            return
        y_min, y_max = vr[1]
        if y_max <= y_min:
            return

        clamped_value = min(max(value, y_min), y_max)
        scene_pt = vb.mapViewToScene(QPointF(vr[0][1], clamped_value))
        local_pt = plot.mapFromScene(scene_pt)

        axis_rect = plot.mapFromScene(axis.sceneBoundingRect()).boundingRect()
        x = int(axis_rect.right() - badge.width() - 1)
        y = int(local_pt.y() - badge.height() / 2)
        y = max(int(axis_rect.top()), min(y, int(axis_rect.bottom() - badge.height())))
        badge.move(x, y)

    def _render_overlays(self):
        if not self._all_timestamps: return
        tf       = self._selected_tf
        use_ohlc = self._use_ohlc
        sessions = sorted({ts.date() for ts in self._all_timestamps})
        has_two  = len(sessions) == 2

        if not self._two_day:
            split  = int(self._session_x_break) if has_two and self._session_x_break else 0
            ts     = self._all_timestamps[split:]
            offset = tf / 2.0 if use_ohlc else 0.0
            base   = ts[0].replace(hour=9, minute=15, second=0, microsecond=0) if ts else None
            def _m(t):
                if not base: return 0.0
                return (t.replace(tzinfo=None) - base.replace(tzinfo=None)).total_seconds() / 60.0
            xs = [_m(t) + offset for t in ts]
        else:
            offset = 0.5 if use_ohlc else 0.0
            ts     = list(self._all_timestamps)
            xs     = [i + offset for i in range(len(ts))]
            px     = list(self._all_price)
            vol    = list(self._all_volume)
            cvd    = list(self._all_cvd)

            if has_two and self._session_x_break is not None and self._cvd_rebased:
                split = int(self._session_x_break)
                if split > 0:
                    prev_offset = cvd[split - 1]
                    cur_offset = cvd[split] if split < len(cvd) else prev_offset
                    cvd = [
                        (v - prev_offset) if i < split else (v - cur_offset)
                        for i, v in enumerate(cvd)
                    ]

            self._render_overlays_with(xs, px, vol, cvd, ts)
            return

        px  = self._all_price[split:]
        vol = self._all_volume[split:]
        cvd = self._all_cvd[split:]
        self._render_overlays_with(xs, px, vol, cvd, ts)

    def _render_overlays_with(self, xs, px, vol, cvd, ts):
        if not xs or len(xs) != len(px): return

        px_arr  = np.asarray(px,  dtype=float)
        cvd_arr = np.asarray(cvd, dtype=float)
        vol_arr = np.asarray(vol, dtype=float)

        show10 = self.cb_ema10.isChecked()
        show21 = self.cb_ema21.isChecked()
        show51 = self.cb_ema51.isChecked()
        showvw = self.cb_vwap.isChecked()

        def _upd(curve, data, show):
            if show and len(data) == len(xs):
                curve.setData(xs, data); curve.show()
            else:
                curve.clear(); curve.hide()

        _upd(self._price_ema10, calculate_ema(px_arr,  10) if show10 else [], show10)
        _upd(self._price_ema21, calculate_ema(px_arr,  21) if show21 else [], show21)
        _upd(self._price_ema51, calculate_ema(px_arr,  51) if show51 else [], show51)
        _upd(self._cvd_ema10,   calculate_ema(cvd_arr, 10) if show10 else [], show10)
        _upd(self._cvd_ema21,   calculate_ema(cvd_arr, 21) if show21 else [], show21)
        _upd(self._cvd_ema51,   calculate_ema(cvd_arr, 51) if show51 else [], show51)

        if showvw and len(vol_arr) == len(px_arr):
            sk   = [t.date() for t in ts] if ts else None
            vwap = calculate_vwap(px_arr, vol_arr, session_keys=sk)
            self._price_vwap.setData(xs, vwap); self._price_vwap.show()
        else:
            self._price_vwap.clear(); self._price_vwap.hide()

    # -------------------------------------------------------- crosshair ------

    def _on_mouse_moved(self, pos):
        sender = self.sender()
        vb = (self.price_plot.getViewBox()
              if sender is self.price_plot.scene()
              else self.cvd_plot.getViewBox())

        if not vb.sceneBoundingRect().contains(pos):
            self._hide_crosshair()
            return

        mp = vb.mapSceneToView(pos)
        self._price_vline.setValue(mp.x()); self._price_vline.show()
        self._cvd_vline.setValue(mp.x());   self._cvd_vline.show()
        self._price_hline.setValue(
            mp.y() if sender is self.price_plot.scene()
            else self.price_plot.getViewBox().mapSceneToView(pos).y())
        self._cvd_hline.setValue(
            mp.y() if sender is self.cvd_plot.scene()
            else self.cvd_plot.getViewBox().mapSceneToView(pos).y())
        self._price_hline.show(); self._cvd_hline.show()

    def _on_mouse_clicked(self, *_):
        self._hide_crosshair()

    def _hide_crosshair(self):
        for ln in (self._price_vline, self._price_hline,
                   self._cvd_vline,   self._cvd_hline):
            ln.hide()

    # --------------------------------------------------------- helpers ------

    @staticmethod
    def _cvd_y_tick_strings(values, *_):
        out = []
        for v in values:
            if abs(v) >= 1_000_000: out.append(f"{v/1_000_000:.1f}M")
            elif abs(v) >= 1_000:   out.append(f"{v/1_000:.0f}K")
            else:                   out.append(f"{int(v)}")
        return out

    def _set_status(self, state: str):
        colours = {
            "loading": _C["ema21"], "live":  _C["vwap"],
            "hist":    _C["ema10"], "error": _C["ema51"],
        }
        self.lbl_status.setStyleSheet(
            f"color:{colours.get(state, _C['text_dim'])};font-size:12px;background:transparent;")

    # -------------------------------------------------------- keyboard -------

    def keyPressEvent(self, event):
        k = event.key()
        if   k == Qt.Key_Left:  self._go_back()
        elif k == Qt.Key_Right: self._go_forward()
        elif k == Qt.Key_1:     self._set_day_mode(False)
        elif k == Qt.Key_2:     self._set_day_mode(True)
        else: super().keyPressEvent(event)

    def closeEvent(self, event):
        self._refresh_timer.stop()
        if self._fetch_worker:
            with suppress(Exception):
                self._fetch_worker.cancel()
        super().closeEvent(event)
