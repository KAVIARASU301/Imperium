import pandas as pd
from PySide6.QtCore import QObject, Signal

from core.cvd.cvd_historical import CVDHistoricalBuilder
from utils.cpr_calculator import CPRCalculator


class _DataFetchWorker(QObject):
    result_ready = Signal(object, object, float, object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, kite, instrument_token, from_dt, to_dt, timeframe_minutes, focus_mode):
        super().__init__()
        self.kite = kite
        self.instrument_token = instrument_token
        self.from_dt = from_dt
        self.to_dt = to_dt
        self.timeframe_minutes = timeframe_minutes
        self.focus_mode = focus_mode

    def run(self):
        try:
            hist = self.kite.historical_data(
                self.instrument_token,
                self.from_dt,
                self.to_dt,
                interval="minute",
            )

            if not hist:
                self.error.emit("no_data")
                return

            df = pd.DataFrame(hist)
            if df.empty:
                self.error.emit("empty_df")
                return

            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

            if self.timeframe_minutes > 1:
                rule = f"{self.timeframe_minutes}min"
                df = df.resample(rule).agg(
                    {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volume": "sum",
                    }
                ).dropna()

            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)
            cvd_df["session"] = cvd_df.index.date

            sessions = sorted(cvd_df["session"].unique())
            if not sessions:
                self.error.emit("no_sessions")
                return

            prev_close = 0.0
            previous_day_cpr = None
            if len(sessions) >= 2:
                prev_data = cvd_df[cvd_df["session"] == sessions[-2]]
                if not prev_data.empty:
                    prev_close = prev_data["close"].iloc[-1]

            df["session"] = df.index.date

            if self.focus_mode:
                cvd_out = cvd_df[cvd_df["session"] == sessions[-1]].copy()
                price_out = df[df["session"] == sessions[-1]].copy()
            else:
                cvd_out = cvd_df[cvd_df["session"].isin(sessions[-2:])].copy()
                price_out = df[df["session"].isin(sessions[-2:])].copy()

            if len(sessions) >= 2:
                prev_day_price = df[df["session"] == sessions[-2]]
                previous_day_cpr = CPRCalculator.get_previous_day_cpr(prev_day_price)

            self.result_ready.emit(cvd_out, price_out, prev_close, previous_day_cpr)

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()
