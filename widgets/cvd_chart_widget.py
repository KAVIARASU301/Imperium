import pyqtgraph as pg
import pandas as pd
from datetime import datetime, timedelta, date

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton
)
from PySide6.QtCore import Qt, QTimer

from core.cvd.cvd_historical import CVDHistoricalBuilder


class CVDChartWidget(QWidget):
    """
    CVD Chart Widget (Market Monitor Style)

    Default view:
    - Previous day visually shifted so its close = 0
    - Today starts naturally from 0 (true session CVD)

    Toggle:
    - Session CVD (raw, no visual shift)
    """

    def __init__(
        self,
        kite,
        instrument_token,
        cvd_engine,
        symbol: str,
        parent=None
    ):
        super().__init__(parent)

        self.kite = kite
        self.instrument_token = instrument_token
        self.cvd_engine = cvd_engine
        self.symbol = symbol

        self.engine_symbol = None
        self.cvd_df = None
        self.prev_day_close_cvd = 0.0

        # default = visual rebased
        self.rebased_mode = True

        self.axis = pg.AxisItem(orientation="bottom")

        self._setup_ui()
        self._load_historical()
        self._start_live_updates()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)

        self.title_label = QLabel(f"{self.symbol} (Rebased)")
        self.title_label.setStyleSheet("""
            QLabel {
                color: #E0E0E0;
                font-size: 14px;
                font-weight: 600;
            }
        """)
        header.addWidget(self.title_label)
        header.addStretch()

        self.toggle_btn = QPushButton("Rebased CVD")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(True)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #212635;
                border: 1px solid #3A4458;
                border-radius: 6px;
                padding: 4px 8px;
                color: #A9B1C3;
                font-size: 11px;
            }
            QPushButton:checked {
                background-color: #2A3B5C;
                color: #FFFFFF;
            }
        """)
        self.toggle_btn.clicked.connect(self._toggle_mode)
        header.addWidget(self.toggle_btn)

        root.addLayout(header)

        self.plot = pg.PlotWidget(axisItems={"bottom": self.axis})
        self.plot.setBackground("#161A25")
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setMouseEnabled(x=False, y=True)
        self.plot.setMenuEnabled(False)

        zero_pen = pg.mkPen("#8A9BA8", style=Qt.DashLine, width=1)
        self.zero_line = pg.InfiniteLine(0, angle=0, pen=zero_pen)
        self.plot.addItem(self.zero_line)

        axis_pen = pg.mkPen("#8A9BA8")
        self.plot.getAxis("left").setPen(axis_pen)
        self.plot.getAxis("bottom").setPen(axis_pen)

        root.addWidget(self.plot)

    # ------------------------------------------------------------------
    # Historical load
    # ------------------------------------------------------------------

    def _load_historical(self):
        # ---- AUTH GUARD (CRITICAL) ----
        if not self.kite or not getattr(self.kite, "access_token", None):
            # Paper mode OR expired session → skip historical safely
            return

        try:
            to_date = datetime.now()
            from_date = to_date - timedelta(days=2)

            hist = self.kite.historical_data(
                self.instrument_token,
                from_date,
                to_date,
                interval="minute"
            )

            if not hist:
                return

            df = pd.DataFrame(hist)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)

            cvd_df["session"] = cvd_df.index.date
            sessions = sorted(cvd_df["session"].unique())[-2:]
            cvd_df = cvd_df[cvd_df["session"].isin(sessions)]

            if len(sessions) == 2:
                prev_sess = sessions[0]
                self.prev_day_close_cvd = (
                    cvd_df[cvd_df["session"] == prev_sess]["close"].iloc[-1]
                )
            else:
                self.prev_day_close_cvd = 0.0

            self.cvd_df = cvd_df
            self._plot()

        except Exception:
            # Never crash Market Monitor widgets
            pass

    # ------------------------------------------------------------------
    # Plotting (CORRECT SCALE LOGIC)
    # ------------------------------------------------------------------

    def _plot(self):
        if self.cvd_df is None or self.cvd_df.empty:
            return

        self.plot.clear()
        self.plot.addItem(self.zero_line)

        sessions = sorted(self.cvd_df["session"].unique())
        all_times = list(self.cvd_df.index)

        x_offset = 0

        for i, sess in enumerate(sessions):
            df_sess = self.cvd_df[self.cvd_df["session"] == sess]
            y_raw = df_sess["close"].values

            # 🔑 FIX: shift ONLY previous day
            if self.rebased_mode and i == 0 and len(sessions) == 2:
                y = y_raw - self.prev_day_close_cvd
            else:
                y = y_raw

            x = list(range(x_offset, x_offset + len(y)))

            pen = (
                pg.mkPen("#7A7A7A", width=1.2)
                if i == 0 and len(sessions) == 2
                else pg.mkPen("#26A69A", width=1.6)
            )

            self.plot.addItem(pg.PlotCurveItem(x, y, pen=pen))
            x_offset += len(y)

        def time_formatter(values, *_):
            out = []
            for v in values:
                idx = int(v)
                if 0 <= idx < len(all_times):
                    out.append(all_times[idx].strftime("%H:%M"))
                else:
                    out.append("")
            return out

        self.axis.tickStrings = time_formatter
        self.axis.setTickSpacing(major=60, minor=15)

        self.plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.plot.setXRange(0, x_offset, padding=0.02)

    # ------------------------------------------------------------------
    # Live updates
    # ------------------------------------------------------------------

    def _start_live_updates(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_live)
        self.timer.start(500)

    def _resolve_engine_symbol(self):
        if self.engine_symbol:
            return

        base = self.symbol.split()[0]
        for sym in self.cvd_engine.snapshot().keys():
            if sym.startswith(base) and sym.endswith("FUT"):
                self.engine_symbol = sym
                break

    def _update_live(self):
        if self.cvd_df is None:
            return

        self._resolve_engine_symbol()
        if not self.engine_symbol:
            return

        live_cvd = self.cvd_engine.get_cvd(self.engine_symbol)
        if live_cvd is None:
            return

        today = date.today()

        if today not in self.cvd_df["session"].values:
            ts = datetime.now()
            self.cvd_df.loc[ts] = {
                "open": live_cvd,
                "high": live_cvd,
                "low": live_cvd,
                "close": live_cvd,
                "session": today,
            }
        else:
            idx = self.cvd_df[self.cvd_df["session"] == today].index[-1]
            self.cvd_df.at[idx, "close"] = live_cvd
            self.cvd_df.at[idx, "high"] = max(self.cvd_df.at[idx, "high"], live_cvd)
            self.cvd_df.at[idx, "low"] = min(self.cvd_df.at[idx, "low"], live_cvd)

        self._plot()

    # ------------------------------------------------------------------
    # Toggle
    # ------------------------------------------------------------------

    def _toggle_mode(self):
        self.rebased_mode = self.toggle_btn.isChecked()

        if self.rebased_mode:
            self.toggle_btn.setText("Rebased CVD")
            self.title_label.setText(f"{self.symbol} (Rebased)")
        else:
            self.toggle_btn.setText("Session CVD")
            self.title_label.setText(self.symbol)

        self._plot()
