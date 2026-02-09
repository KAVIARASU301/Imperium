import pyqtgraph as pg
import pandas as pd
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor

from core.cvd.cvd_historical import CVDHistoricalBuilder


class CVDChartWidget(QWidget):
    """
    Professional CVD Chart Widget with synchronized crosshairs.

    Features:
    - Historical minute candles with date navigation
    - Synchronized crosshair across multiple charts
    - Rebased / Session toggle
    - Momentum-based color indicators
    - Smart date handling (weekend-aware)
    """

    # Signal for crosshair synchronization (x_position, timestamp)
    crosshair_moved = Signal(float, datetime)

    REFRESH_INTERVAL_MS = 3000  # 3 seconds (live mode)

    COLOR_UP = "#26A69A"  # green
    COLOR_DOWN = "#EF5350"  # red
    COLOR_FLAT = "#8A9BA8"  # grey

    def __init__(
            self,
            kite,
            instrument_token,
            symbol: str,
            parent=None,
            auto_refresh: bool = True,
    ):
        super().__init__(parent)

        self.kite = kite
        self.instrument_token = instrument_token
        self.symbol = symbol

        self.cvd_df = None
        self.prev_day_close_cvd = 0.0
        self.rebased_mode = True

        # Date navigation support
        self.current_date = None
        self.previous_date = None
        self.live_mode = True
        self._historical_loaded = False
        self._historical_failed = False

        # --- Live dot pulse state ---
        self._pulse_size = 6
        self._pulse_target = 6
        self._pulse_velocity = 0
        self._last_slope = None

        # Crosshair state
        self.all_timestamps = []
        self.x_offset_map = {}  # session -> x_offset
        self.crosshair_line = None
        self.crosshair_label = None
        self.external_update = False  # Flag to prevent feedback loop

        self.axis = pg.AxisItem(orientation="bottom")
        self._auto_refresh = auto_refresh

        self._setup_ui()
        self._setup_crosshair()
        if self._auto_refresh and self.instrument_token and isinstance(self.instrument_token, int):
            self._start_refresh_timer()

        # Pulse animation timer (smooth decay)
        self.pulse_timer = QTimer(self)
        self.pulse_timer.timeout.connect(self._update_pulse)
        self.pulse_timer.start(40)  # ~25 FPS, light and smooth


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
        self.crosshair_time_label = QLabel("--:--:--")
        self.crosshair_time_label.setStyleSheet("""
            QLabel {
                color: #7FD6DB;
                font-size: 11px;
                font-weight: 600;
                padding: 2px 6px;
                background-color: #1E2230;
                border: 1px solid #3A4458;
                border-radius: 4px;
            }
        """)
        self.crosshair_time_label.setFixedHeight(20)
        header.addWidget(self.crosshair_time_label)

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
            QPushButton:hover {
                border-color: #4A5468;
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

        # Moving dot (color updated dynamically)
        self.end_dot = pg.ScatterPlotItem(
            size=6,
            brush=pg.mkBrush(self.COLOR_FLAT),
            pen=pg.mkPen(None)
        )
        self.plot.addItem(self.end_dot)

        root.addWidget(self.plot)

    def set_instrument(self, token: int, symbol: str):
        if not token or not isinstance(token, int):
            return

        self.instrument_token = token
        self.symbol = symbol
        self.title_label.setText(f"{symbol} (Rebased)")

        # Reset historical state
        self._historical_loaded = False
        self._historical_failed = False
        self._last_hist_range = None

        # ✅ Start ALL timers (refresh, pulse, blink)
        if self._auto_refresh and (not hasattr(self, "timer") or not self.timer.isActive()):
            self._start_refresh_timer()

        # Restart pulse and blink timers if they were stopped
        if hasattr(self, "pulse_timer") and not self.pulse_timer.isActive():
            self.pulse_timer.start(40)

        # Load now (safe)
        self._load_historical()

    def _setup_crosshair(self):
        """Setup synchronized crosshair."""
        # Vertical line
        pen = pg.mkPen(QColor(255, 255, 255, 100), width=1, style=Qt.DashLine)
        self.crosshair_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pen
        )
        self.crosshair_line.hide()
        self.plot.addItem(self.crosshair_line)
        # ---- Floating X-axis time label (TradingView style) ----
        self.x_time_label = pg.TextItem(
            "",
            anchor=(0.5, 0),
            color="#E6EAF2",
            fill=pg.mkBrush("#212635"),
            border=pg.mkPen("#3A4458")
        )
        self.plot.addItem(self.x_time_label, ignoreBounds=True)

        # Connect mouse move event
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

    # ------------------------------------------------------------------
    # Crosshair Sync
    # ------------------------------------------------------------------

    def _on_mouse_moved(self, pos):
        """Handle mouse movement for crosshair."""

        if self.external_update:
            return

        if self.plot.sceneBoundingRect().contains(pos):
            mouse_point = self.plot.plotItem.vb.mapSceneToView(pos)
            x = mouse_point.x()

            # Find closest timestamp
            if self.all_timestamps:
                idx = int(round(x))
                if 0 <= idx < len(self.all_timestamps):
                    ts = self.all_timestamps[idx]

                    self.crosshair_line.setPos(idx)
                    self.crosshair_line.show()

                    self.crosshair_time_label.setText(ts.strftime("%H:%M:%S"))
                    self.crosshair_moved.emit(idx, ts)

        else:
            self.crosshair_line.hide()
            self.crosshair_time_label.setText("--:--:--")

    def update_crosshair(self, x_pos: float, timestamp: datetime):
        """Update crosshair from external signal."""
        self.external_update = True

        # Map timestamp to local x coordinate
        if self.all_timestamps:
            try:
                # Find matching timestamp or closest
                local_idx = None
                for i, ts in enumerate(self.all_timestamps):
                    if ts == timestamp:
                        local_idx = i
                        break

                if local_idx is None:
                    # Find closest timestamp
                    time_diffs = [abs((ts - timestamp).total_seconds())
                                  for ts in self.all_timestamps]
                    local_idx = time_diffs.index(min(time_diffs))

                if local_idx is not None:
                    self.crosshair_line.setPos(local_idx)
                    self.crosshair_line.show()
                    self.crosshair_line.setPos(local_idx)
                    self.crosshair_line.show()
                    self.crosshair_time_label.setText(timestamp.strftime("%H:%M:%S"))

            except Exception:
                pass

        self.external_update = False

    def _update_pulse(self):
        # Do nothing if dot is not visible / no data
        if not self.end_dot.data:
            return

        if self._pulse_size == self._pulse_target:
            return

        diff = self._pulse_target - self._pulse_size
        self._pulse_velocity += diff * 0.25
        self._pulse_velocity *= 0.6

        self._pulse_size += self._pulse_velocity

        if abs(diff) < 0.1:
            self._pulse_size = self._pulse_target
            self._pulse_velocity = 0

        self.end_dot.setSize(max(4, int(self._pulse_size)))

    # ------------------------------------------------------------------
    # Date Navigation
    # ------------------------------------------------------------------

    def load_historical_dates(self, current_date: datetime, previous_date: datetime):
        """Load data for specific dates (used by navigator)."""
        if not self.instrument_token or not isinstance(self.instrument_token, int):
            return

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self.current_date = current_date
        self.previous_date = previous_date

        # ✅ Decide mode based on date
        if current_date >= today:
            # LIVE MODE - restart ALL timers
            self.live_mode = True

            if hasattr(self, "timer") and not self.timer.isActive():
                self.timer.start(self.REFRESH_INTERVAL_MS)

            # Restart pulse and blink timers
            if hasattr(self, "pulse_timer") and not self.pulse_timer.isActive():
                self.pulse_timer.start(40)

        else:
            # HISTORICAL MODE
            self.live_mode = False

            if hasattr(self, "timer"):
                self.timer.stop()

        self._load_historical()

    # ------------------------------------------------------------------
    # Historical load
    # ------------------------------------------------------------------

    def _load_historical(self):
        """
        Load historical data ONCE per date-range.

        Design rules:
        - Never spam REST API
        - Fail once, then stop retrying
        - Reload ONLY if date range actually changes
        - Safe for multi-chart dialogs
        """

        # --- Hard guards ---
        if not self.instrument_token or not isinstance(self.instrument_token, int):
            return

        if self._historical_failed:
            return

        if not self.kite or not getattr(self.kite, "access_token", None):
            self._historical_failed = True
            return

        try:
            # --- Determine date range ---
            if self.live_mode:
                to_dt = datetime.now()
                from_dt = to_dt - timedelta(days=5)
            else:
                if not self.current_date or not self.previous_date:
                    return
                to_dt = self.current_date + timedelta(days=1)
                from_dt = self.previous_date

            date_key = (from_dt, to_dt)

            # --- Prevent duplicate reloads ---
            if self._historical_loaded and self._last_hist_range == date_key:
                return

            self._last_hist_range = date_key

            # --- Fetch historical ---
            hist = self.kite.historical_data(
                self.instrument_token,
                from_dt,
                to_dt,
                interval="minute"
            )

            if not hist:
                self._historical_failed = True
                return

            # --- Build dataframe ---
            df = pd.DataFrame(hist)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)
            cvd_df["session"] = cvd_df.index.date

            # --- Filter sessions ---
            if self.live_mode:
                sessions = sorted(cvd_df["session"].unique())[-2:]
            else:
                target_dates = {
                    self.previous_date.date(),
                    self.current_date.date()
                }
                sessions = [
                    d for d in sorted(cvd_df["session"].unique())
                    if d in target_dates
                ]

            cvd_df = cvd_df[cvd_df["session"].isin(sessions)]

            # --- Previous day close ---
            if len(sessions) >= 2:
                prev_data = cvd_df[cvd_df["session"] == sessions[0]]
                self.prev_day_close_cvd = (
                    prev_data["close"].iloc[-1]
                    if not prev_data.empty else 0.0
                )
            else:
                self.prev_day_close_cvd = 0.0

            # --- Commit ---
            self.cvd_df = cvd_df
            self._historical_loaded = True
            self._plot()

        except Exception as e:
            self._historical_failed = True
            import logging
            logging.getLogger(__name__).exception(
                f"CVD historical failed once for {self.symbol}. Disabling retries."
            )

    # ------------------------------------------------------------------
    # Plotting + Momentum Dot
    # ------------------------------------------------------------------

    def _plot(self):
        if self.cvd_df is None or self.cvd_df.empty:
            return

        self.plot.clear()
        self.plot.addItem(self.zero_line)
        self.plot.addItem(self.end_dot)
        self.plot.addItem(self.crosshair_line)

        sessions = sorted(self.cvd_df["session"].unique())
        self.all_timestamps = []
        self.x_offset_map = {}

        x_offset = 0
        last_two_y = []
        last_x = None
        last_y = None

        for i, sess in enumerate(sessions):
            df_sess = self.cvd_df[self.cvd_df["session"] == sess]
            y_raw = df_sess["close"].values

            # Rebasing logic
            if self.rebased_mode and i == 0 and len(sessions) == 2:
                y = y_raw - self.prev_day_close_cvd
            else:
                y = y_raw

            # Prepend zero point for current session (fills gap from zero line)
            is_current_session = (i == len(sessions) - 1)
            if is_current_session:
                import numpy as np
                y = np.insert(y, 0, 0.0)
                first_ts = df_sess.index[0]
                self.all_timestamps.append(first_ts)

            # Store timestamps
            if not is_current_session:
                self.all_timestamps.extend(df_sess.index.tolist())
            else:
                self.all_timestamps.extend(df_sess.index.tolist())

            self.x_offset_map[sess] = x_offset

            x = list(range(x_offset, x_offset + len(y)))

            # Styling: previous day dimmed, current day bright
            pen = (
                pg.mkPen("#7A7A7A", width=1.2)
                if i == 0 and len(sessions) == 2
                else pg.mkPen("#26A69A", width=1.6)
            )

            self.plot.addItem(pg.PlotCurveItem(x, y, pen=pen))

            if i == len(sessions) - 1 and len(y) >= 2:
                last_two_y = y[-2:].tolist()
                last_x = x[-1]
                last_y = y[-1]

            x_offset += len(y)

        # --- Momentum-based dot color ---
        if len(last_two_y) == 2 and last_x is not None:
            prev_y, curr_y = last_two_y
            slope = curr_y - prev_y

            # Momentum color
            if slope > 0:
                color = self.COLOR_UP
            elif slope < 0:
                color = self.COLOR_DOWN
            else:
                color = self.COLOR_FLAT

            self.end_dot.setBrush(pg.mkBrush(color))
            self.end_dot.setData([last_x], [last_y])

            # --- Pulse trigger ---
            # --- Institutional pulse trigger ---
            if self._last_slope is not None:

                slope_flip = (slope > 0 > self._last_slope) or (slope < 0 < self._last_slope)

                acceleration = abs(slope) > abs(self._last_slope) * 2.0

                if slope_flip or acceleration:
                    self._pulse_size = 14  # instant expansion
                    self._pulse_target = 6  # decay back
                    self._pulse_velocity = 0

            self._last_slope = slope
        else:
            self.end_dot.clear()

        # --- Time axis formatter ---
        def time_formatter(values, *_):
            out = []
            for v in values:
                idx = int(v)
                if 0 <= idx < len(self.all_timestamps):
                    out.append(self.all_timestamps[idx].strftime("%H:%M"))
                else:
                    out.append("")
            return out

        self.axis.tickStrings = time_formatter
        self.axis.setTickSpacing(major=60, minor=15)

        self.plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.plot.setXRange(0, x_offset, padding=0.02)

    # ------------------------------------------------------------------
    # Timer
    # ------------------------------------------------------------------

    def _start_refresh_timer(self):
        """Start auto-refresh timer (only in live mode)."""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_if_live)
        self.timer.start(self.REFRESH_INTERVAL_MS)

    def _refresh_if_live(self):
        """Refresh only if in live mode."""
        if self.live_mode and self._is_refresh_allowed():
            self._load_historical()

    def _is_refresh_allowed(self) -> bool:
        if not self.isVisible():
            return False
        window = self.window()
        if window is None:
            return False
        return window.isActiveWindow()

    def refresh_if_live(self, force: bool = False):
        """External refresh hook for shared timers."""
        if not self.live_mode:
            return
        if force or self._is_refresh_allowed():
            self._load_historical()

    def stop_updates(self):
        """Stop timer (called on cleanup)."""
        if hasattr(self, "timer"):
            self.timer.stop()
        if hasattr(self, "pulse_timer"):
            self.pulse_timer.stop()


    def start_updates(self):
        """Start all timers (called when activating widget)."""
        if self._auto_refresh and hasattr(self, "timer") and not self.timer.isActive():
            self.timer.start(self.REFRESH_INTERVAL_MS)
        if hasattr(self, "pulse_timer") and not self.pulse_timer.isActive():
            self.pulse_timer.start(40)

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

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self.stop_updates()
        super().closeEvent(event)
