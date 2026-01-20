import logging
from datetime import datetime, timedelta

import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QHBoxLayout,
    QPushButton, QWidget
)
from PySide6.QtCore import Qt, QTimer, Signal
from pyqtgraph import AxisItem, TextItem

from kiteconnect import KiteConnect
from core.cvd.cvd_historical import CVDHistoricalBuilder

logger = logging.getLogger(__name__)


# =============================================================================
# Date Navigator (same behavior as multi-chart)
# =============================================================================

class DateNavigator(QWidget):
    date_changed = Signal(datetime, datetime)  # current_date, previous_date

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.btn_back = QPushButton("◀")
        self.btn_back.setFixedSize(40, 32)
        self.btn_back.clicked.connect(self._go_backward)

        self.lbl_dates = QLabel()
        self.lbl_dates.setAlignment(Qt.AlignCenter)
        self.lbl_dates.setMinimumWidth(500)
        self.lbl_dates.setStyleSheet("""
            QLabel {
                color: #E0E0E0;
                font-size: 13px;
                font-weight: 600;
            }
        """)

        self.btn_forward = QPushButton("▶")
        self.btn_forward.setFixedSize(40, 32)
        self.btn_forward.clicked.connect(self._go_forward)

        layout.addStretch()
        layout.addWidget(self.btn_back)
        layout.addWidget(self.lbl_dates)
        layout.addWidget(self.btn_forward)
        layout.addStretch()

    def _get_previous_trading_day(self, date: datetime) -> datetime:
        prev = date - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev

    def _get_next_trading_day(self, date: datetime) -> datetime:
        nxt = date + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt

    def _update_display(self):
        prev = self._get_previous_trading_day(self._current_date)
        cur_str = self._current_date.strftime("%A, %b %d, %Y")
        prev_str = prev.strftime("%A, %b %d, %Y")

        self.lbl_dates.setText(
            f"<span style='color:#5B9BD5;'>Previous: {prev_str}</span>"
            f"  |  "
            f"<span style='color:#26A69A;'>Current: {cur_str}</span>"
        )

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.btn_forward.setEnabled(self._current_date < today)

    def _go_backward(self):
        self._current_date = self._get_previous_trading_day(self._current_date)
        self._update_display()
        self.date_changed.emit(
            self._current_date,
            self._get_previous_trading_day(self._current_date)
        )

    def _go_forward(self):
        self._current_date = self._get_next_trading_day(self._current_date)
        self._update_display()
        self.date_changed.emit(
            self._current_date,
            self._get_previous_trading_day(self._current_date)
        )

    def get_dates(self):
        return (
            self._current_date,
            self._get_previous_trading_day(self._current_date)
        )


# =============================================================================
# Single CVD Chart Dialog (with navigator)
# =============================================================================

class CVDSingleChartDialog(QDialog):
    REFRESH_INTERVAL_MS = 3000

    def __init__(
            self,
            kite: KiteConnect,
            instrument_token: int,
            symbol: str,
            cvd_engine,  # ✅ ADD THIS
            parent=None,
    ):

        super().__init__(parent)

        self.kite = kite
        self.instrument_token = instrument_token
        self.symbol = symbol

        self.live_mode = True
        self.current_date = None
        self.previous_date = None

        self.setWindowTitle(f"CVD Chart — {symbol}")
        self.setMinimumSize(1300, 720)
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )

        self._setup_ui()
        self._connect_signals()

        # Init in LIVE mode
        self.current_date, self.previous_date = self.navigator.get_dates()
        self._load_and_plot()
        self._start_refresh_timer()

        self.all_timestamps = []

    # ------------------------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Navigator
        self.navigator = DateNavigator(self)
        root.addWidget(self.navigator)

        # Status
        status_layout = QHBoxLayout()
        self.status_label = QLabel("Loading...")
        self.status_label.setStyleSheet("color:#8A9BA8; font-size:11px;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        root.addLayout(status_layout)



        # Chart
        self.axis = AxisItem(orientation="bottom")
        self.plot = pg.PlotWidget(axisItems={"bottom": self.axis})
        bottom_axis = self.plot.getAxis("bottom")
        bottom_axis.setHeight(32)  # 🔥 THIS FIXES VISIBILITY
        bottom_axis.setStyle(showValues=True)
        bottom_axis.setTextPen(pg.mkPen("#8A9BA8"))
        bottom_axis.setPen(pg.mkPen("#8A9BA8"))

        self.plot.setBackground("#161A25")
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.setMenuEnabled(False)

        root.addWidget(self.plot)

        zero_pen = pg.mkPen("#6C7386", style=Qt.DashLine, width=1)
        self.plot.addItem(pg.InfiniteLine(0, angle=0, pen=zero_pen))

        self.prev_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#7A7A7A", width=2, style=Qt.DashLine)
        )
        self.today_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#26A69A", width=2.5)
        )

        self.plot.addItem(self.prev_curve)
        self.plot.addItem(self.today_curve)

        self.live_dot = pg.ScatterPlotItem(
            size=10,
            brush=pg.mkBrush(38, 166, 154, 200),
            pen=pg.mkPen("#FFFFFF", width=1)
        )
        self.plot.addItem(self.live_dot)
        # Crosshair
        pen = pg.mkPen((255, 255, 255, 120), width=1, style=Qt.DashLine)
        self.crosshair_line = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.crosshair_line.hide()
        self.plot.addItem(self.crosshair_line)
        # --- X-axis floating time label (TradingView style) ---
        self.x_time_label = pg.TextItem(
            "",
            anchor=(0.5, 0),  # center horizontally, stick to axis
            color="#E0E0E0",
            fill=pg.mkBrush("#212635"),
            border=pg.mkPen("#3A4458")
        )
        self.x_time_label.hide()
        self.plot.addItem(self.x_time_label, ignoreBounds=True)

        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.dot_timer = QTimer(self)
        self.dot_timer.timeout.connect(self._blink_dot)
        self.dot_timer.start(500)
        self._dot_visible = True

    # ------------------------------------------------------------------

    def _connect_signals(self):
        self.navigator.date_changed.connect(self._on_date_changed)

    # ------------------------------------------------------------------
    def _on_mouse_moved(self, pos):
        if not self.plot.sceneBoundingRect().contains(pos):
            self.crosshair_line.hide()
            self.x_time_label.hide()
            return

        mouse_point = self.plot.plotItem.vb.mapSceneToView(pos)
        x = int(round(mouse_point.x()))

        total = len(self.all_timestamps)
        if not (0 <= x < total):
            self.crosshair_line.hide()
            self.x_time_label.hide()
            return

        # Move crosshair
        self.crosshair_line.setPos(x)
        self.crosshair_line.show()

        # Timestamp for this candle
        ts = self.all_timestamps[x]
        time_text = ts.strftime("%H:%M")

        # --- Position label on X-axis ---
        vb = self.plot.plotItem.vb
        y_min = vb.viewRange()[1][0]
        y_max = vb.viewRange()[1][1]
        y_pos = y_min + (y_max - y_min) * 0.03  # 6% above bottom

        self.x_time_label.setText(time_text)
        self.x_time_label.setPos(x, y_pos)
        self.x_time_label.show()

    def _on_date_changed(self, current_date: datetime, previous_date: datetime):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self.current_date = current_date
        self.previous_date = previous_date

        if current_date >= today:
            self.live_mode = True
            self.status_label.setText("LIVE mode")
            if not self.refresh_timer.isActive():
                self.refresh_timer.start(self.REFRESH_INTERVAL_MS)
        else:
            self.live_mode = False
            self.status_label.setText("Historical mode")
            self.refresh_timer.stop()

        self._load_and_plot()

    # ------------------------------------------------------------------

    def _load_and_plot(self):
        if not self.kite or not getattr(self.kite, "access_token", None):
            return

        try:
            if self.live_mode:
                to_dt = datetime.now()
                from_dt = to_dt - timedelta(days=5)
            else:
                to_dt = self.current_date + timedelta(days=1)
                from_dt = self.previous_date

            hist = self.kite.historical_data(
                self.instrument_token,
                from_dt,
                to_dt,
                interval="minute"
            )
            if not hist:
                return

            df = pd.DataFrame(hist)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)
            cvd_df["session"] = cvd_df.index.date

            sessions = sorted(cvd_df["session"].unique())
            if not sessions:
                return

            prev_close = 0.0
            if len(sessions) >= 2:
                prev_data = cvd_df[cvd_df["session"] == sessions[-2]]
                if not prev_data.empty:
                    prev_close = prev_data["close"].iloc[-1]

            cvd_df = cvd_df[cvd_df["session"].isin(sessions[-2:])]
            self._plot_data(cvd_df, prev_close)

        except Exception:
            logger.exception("Failed to load CVD data")

    # ------------------------------------------------------------------

    def _plot_data(self, cvd_df: pd.DataFrame, prev_close: float):
        self.prev_curve.clear()
        self.today_curve.clear()
        self.live_dot.clear()

        self.all_timestamps = []

        x_offset = 0
        sessions = sorted(cvd_df["session"].unique())

        for i, sess in enumerate(sessions):
            df_sess = cvd_df[cvd_df["session"] == sess]
            y_raw = df_sess["close"].values

            # SAME rebasing logic as multi chart
            if i == 0 and len(sessions) == 2:
                y = y_raw - prev_close
            else:
                y = y_raw

            xs = list(range(x_offset, x_offset + len(y)))
            self.all_timestamps.extend(df_sess.index.tolist())

            if i == 0 and len(sessions) == 2:
                self.prev_curve.setData(xs, y)
            else:
                self.today_curve.setData(xs, y)
                if xs:
                    self.live_dot.setData([xs[-1]], [y[-1]])

            x_offset += len(y)

        # ---- TIME AXIS FORMATTER (CRITICAL) ----
        def time_formatter(values, *_):
            labels = []
            total = len(self.all_timestamps)

            for v in values:
                idx = int(v)
                if 0 <= idx < total:
                    ts = self.all_timestamps[idx]
                    labels.append(ts.strftime("%H:%M"))
                else:
                    labels.append("")
            return labels

        self.axis.tickStrings = time_formatter

        self.plot.setXRange(0, x_offset, padding=0.02)
        self.plot.enableAutoRange(axis=pg.ViewBox.YAxis)

    # ------------------------------------------------------------------

    def _blink_dot(self):
        self._dot_visible = not self._dot_visible
        alpha = 220 if self._dot_visible else 60
        self.live_dot.setBrush(pg.mkBrush(38, 166, 154, alpha))

    # ------------------------------------------------------------------

    def _start_refresh_timer(self):
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._load_and_plot)
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)

    def _fix_axis_after_show(self):
        bottom_axis = self.plot.getAxis("bottom")
        bottom_axis.setHeight(32)
        bottom_axis.update()
        self.plot.updateGeometry()

    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)

        # Force axis layout AFTER the dialog is shown
        QTimer.singleShot(0, self._fix_axis_after_show)

    def closeEvent(self, event):
        if hasattr(self, "refresh_timer"):
            self.refresh_timer.stop()
        if hasattr(self, "dot_timer"):
            self.dot_timer.stop()
        super().closeEvent(event)
