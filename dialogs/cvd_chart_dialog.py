import logging
from datetime import datetime, timedelta

import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import QDialog, QVBoxLayout
from PySide6.QtCore import Qt, QTimer
from pyqtgraph import AxisItem

from kiteconnect import KiteConnect

from core.cvd.cvd_historical import CVDHistoricalBuilder
from core.cvd.cvd_engine import CVDEngine

logger = logging.getLogger(__name__)


class CVDChartDialog(QDialog):
    """
    CVD Chart Dialog
    - Previous day: grey, non-continuous
    - Current day: green
    - Adaptive time axis
    - Live updates
    """

    def __init__(
        self,
        kite: KiteConnect,
        instrument_token: int,
        symbol: str,
        cvd_engine: CVDEngine,
        parent=None,
    ):
        super().__init__(parent)

        self.kite = kite
        self.instrument_token = instrument_token
        self.symbol = symbol
        self.cvd_engine = cvd_engine

        self.engine_symbol = None
        self.cvd_df: pd.DataFrame | None = None

        self.setWindowTitle(f"CVD – {symbol}")
        self.setMinimumSize(900, 520)

        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )

        self._setup_ui()
        self._load_historical()
        self._start_live_timer()

    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.axis = AxisItem(orientation="bottom")

        self.plot = pg.PlotWidget(axisItems={"bottom": self.axis})
        self.plot.setBackground("#161A25")
        self.plot.showGrid(x=True, y=True, alpha=0.12)

        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=True)

        axis_pen = pg.mkPen("#8A9BA8")
        for a in ("left", "bottom"):
            ax = self.plot.getAxis(a)
            ax.setPen(axis_pen)
            ax.setTextPen(axis_pen)
            ax.setStyle(tickTextOffset=8)

        layout.addWidget(self.plot)

        zero_pen = pg.mkPen("#6C7386", style=Qt.DashLine, width=1)
        self.zero_line = pg.InfiniteLine(0, angle=0, pen=zero_pen)
        self.plot.addItem(self.zero_line)

    # ------------------------------------------------------------------

    def _load_historical(self):
        # ---- AUTH GUARD (IMPORTANT) ----
        if not self.kite or not getattr(self.kite, "access_token", None):
            logger.error("CVD chart: Kite client not authenticated, skipping historical load")
            return

        try:
            to_date = datetime.now()
            from_date = to_date - timedelta(days=2)

            hist = self.kite.historical_data(
                self.instrument_token,
                from_date,
                to_date,
                interval="minute",
            )

            if not hist:
                return

            df = pd.DataFrame(hist)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)

            # Keep only previous + current session
            cvd_df["session"] = cvd_df.index.date
            sessions = sorted(cvd_df["session"].unique())[-2:]
            cvd_df = cvd_df[cvd_df["session"].isin(sessions)]

            self.cvd_df = cvd_df
            self._plot()

        except Exception:
            logger.exception("Failed to load CVD chart")

    # ------------------------------------------------------------------

    def _plot(self):
        if self.cvd_df is None or self.cvd_df.empty:
            return

        self.plot.clear()
        self.plot.addItem(self.zero_line)

        sessions = sorted(self.cvd_df["session"].unique())
        timestamps = list(self.cvd_df.index)

        x_offset = 0
        all_times = []

        for i, sess in enumerate(sessions):
            df_sess = self.cvd_df[self.cvd_df["session"] == sess]
            y = df_sess["close"].values
            x = list(range(x_offset, x_offset + len(y)))

            all_times.extend(df_sess.index)

            pen = (
                pg.mkPen("#7A7A7A", width=2)  # previous day
                if i == 0 and len(sessions) == 2
                else pg.mkPen("#26A69A", width=2.5)  # today
            )

            self.plot.addItem(
                pg.PlotCurveItem(
                    x,
                    y,
                    pen=pen,
                    antialias=True,
                    skipFiniteCheck=True,
                )
            )

            x_offset += len(y)

        # ---- Adaptive time axis ----
        def time_formatter(values, *_):
            labels = []
            total = len(all_times)

            # dynamic step
            if total <= 300:
                step = 15
            elif total <= 600:
                step = 30
            else:
                step = 60

            for v in values:
                idx = int(v)
                if 0 <= idx < total:
                    ts = all_times[idx]
                    if ts.minute % step == 0:
                        labels.append(ts.strftime("%H:%M"))
                    else:
                        labels.append("")
                else:
                    labels.append("")
            return labels

        self.axis.tickStrings = time_formatter

        self.plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.plot.setXRange(0, x_offset, padding=0.02)

    # ------------------------------------------------------------------

    def _start_live_timer(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_live)
        self.timer.start(500)

    # ------------------------------------------------------------------

    def _update_live(self):
        if self.cvd_df is None or self.cvd_df.empty:
            return

        if self.engine_symbol is None:
            base = self.symbol.split()[0]
            for sym in self.cvd_engine.snapshot():
                if sym.startswith(base) and sym.endswith("FUT"):
                    self.engine_symbol = sym
                    break

        if not self.engine_symbol:
            return

        live_cvd = self.cvd_engine.get_cvd(self.engine_symbol)
        if live_cvd is None:
            return

        idx = self.cvd_df.index[-1]
        self.cvd_df.at[idx, "close"] = live_cvd
        self.cvd_df.at[idx, "high"] = max(self.cvd_df.at[idx, "high"], live_cvd)
        self.cvd_df.at[idx, "low"] = min(self.cvd_df.at[idx, "low"], live_cvd)

        self._plot()
