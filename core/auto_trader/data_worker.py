import time

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
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _fetch_historical_with_retry(self, from_dt, to_dt):
        max_attempts = 3
        base_delay_seconds = 0.25

        for attempt in range(max_attempts):
            if self._cancelled:
                return []
            try:
                return self.kite.historical_data(
                    self.instrument_token,
                    from_dt,
                    to_dt,
                    interval="minute",
                )
            except Exception:
                if attempt == max_attempts - 1:
                    raise
                delay_seconds = base_delay_seconds * (2 ** attempt)
                time.sleep(delay_seconds)

        return []

    def _load_minute_history(self):
        required_sessions = 2
        max_lookback_days = 30
        chunk_days = 5

        aggregate_df = pd.DataFrame()
        range_end = self.to_dt
        range_start = self.from_dt

        while (self.to_dt - range_start).days <= max_lookback_days:
            if self._cancelled:
                return pd.DataFrame()
            hist = self._fetch_historical_with_retry(range_start, range_end)

            if hist:
                chunk_df = pd.DataFrame(hist)
                if not chunk_df.empty:
                    chunk_df["date"] = pd.to_datetime(chunk_df["date"])
                    chunk_df.set_index("date", inplace=True)
                    aggregate_df = pd.concat([chunk_df, aggregate_df])
                    aggregate_df = aggregate_df[~aggregate_df.index.duplicated(keep="last")]
                    aggregate_df.sort_index(inplace=True)
                    sessions_count = aggregate_df.index.normalize().nunique()
                    if sessions_count >= required_sessions:
                        return aggregate_df

            range_end = range_start
            range_start = range_start - pd.Timedelta(days=chunk_days)

        return aggregate_df

    def run(self):
        try:
            if self._cancelled:
                return
            df = self._load_minute_history()
            if self._cancelled:
                return
            if df.empty:
                self.error.emit("empty_df")
                return

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
                )
                df["volume"] = df["volume"].fillna(0)
                df = df.dropna(subset=["open", "high", "low", "close"])

            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)
            if self._cancelled:
                return
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

            if self._cancelled:
                return
            self.result_ready.emit(cvd_out, price_out, prev_close, previous_day_cpr)

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()
