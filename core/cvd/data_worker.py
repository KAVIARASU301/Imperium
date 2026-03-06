import time
import logging

import numpy as np
import pandas as pd
from PySide6.QtCore import QObject, Signal

from core.cvd.cvd_historical import CVDHistoricalBuilder
from core.account.token_manager import TokenManager
from core.utils.cpr_calculator import CPRCalculator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gapless CVD open enforcement
# ---------------------------------------------------------------------------

def _fix_cvd_opens(cvd_df: pd.DataFrame) -> pd.DataFrame:
    """
    Enforce the institutional rule: open[i] == close[i-1] within each session.

    CVD is a continuous cumulative sum — there are no economic gaps between
    consecutive bars. When resampling from 1m to a higher timeframe the
    standard resample().agg(open='first') picks the FIRST 1m bar's post-delta
    value as the HTF open, which differs from the previous HTF bar's close.
    This function corrects that by overwriting open with the shifted close
    (session-aware: the first bar of each session always opens at 0, matching
    the daily CVD anchor reset).

    Also recomputes high/low so they always contain the open, which is a
    requirement for valid candlestick rendering (high >= open and close,
    low <= open and close).
    """
    if cvd_df.empty:
        return cvd_df

    df = cvd_df.copy()

    session_col = "session" if "session" in df.columns else None

    if session_col:
        # Shift close within each session → becomes the next bar's open.
        df["open"] = df.groupby(session_col)["close"].shift(1)
        # First bar of each session: CVD resets to 0, so open = 0.
        df["open"] = df["open"].fillna(0.0)
    else:
        df["open"] = df["close"].shift(1).fillna(0.0)

    # Re-clamp high/low to contain the corrected open.
    df["high"] = np.maximum(df["high"], df["open"])
    df["low"]  = np.minimum(df["low"],  df["open"])

    return df


# ---------------------------------------------------------------------------
# Tick-CSV utilities
# ---------------------------------------------------------------------------

def load_tick_csv(csv_path: str) -> pd.DataFrame:
    """Load tick CSV with columns timestamp, ltp, volume."""
    raw = pd.read_csv(
        csv_path,
        header=None,
        usecols=[0, 1, 2],
        names=["timestamp", "ltp", "volume"],
        skipinitialspace=True,
        on_bad_lines="skip",
    )

    if raw.empty:
        return pd.DataFrame(columns=["timestamp", "ltp", "volume"])

    raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce")
    raw["ltp"]       = pd.to_numeric(raw["ltp"],       errors="coerce")
    raw["volume"]    = pd.to_numeric(raw["volume"],    errors="coerce")

    tick_df = raw.dropna(subset=["timestamp", "ltp", "volume"]).copy()
    if tick_df.empty:
        return tick_df

    tick_df.sort_values("timestamp", inplace=True)
    tick_df.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
    tick_df.reset_index(drop=True, inplace=True)
    return tick_df


def build_price_cvd_from_ticks(
    tick_df: pd.DataFrame,
    timeframe_minutes: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build timeframe OHLCV and gapless CVD OHLC from tick data."""
    if tick_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    data = tick_df.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    data["ltp"]       = pd.to_numeric(data["ltp"],       errors="coerce")
    data["volume"]    = pd.to_numeric(data["volume"],    errors="coerce")
    data = data.dropna(subset=["timestamp", "ltp", "volume"]).copy()
    if data.empty:
        return pd.DataFrame(), pd.DataFrame()

    data.sort_values("timestamp", inplace=True)
    data.set_index("timestamp", inplace=True)

    volume_diff     = data["volume"].diff()
    looks_cumulative = float((volume_diff >= 0).mean()) > 0.95
    if looks_cumulative:
        tick_volume = volume_diff.clip(lower=0).fillna(0.0)
    else:
        tick_volume = data["volume"].clip(lower=0).fillna(0.0)

    price_diff    = data["ltp"].diff().fillna(0.0)
    signed_volume = np.where(
        price_diff > 0, tick_volume,
        np.where(price_diff < 0, -tick_volume, 0.0)
    )

    data["tick_volume"]    = tick_volume
    data["signed_volume"]  = signed_volume
    data["session"]        = data.index.date
    data["cvd"]            = data.groupby("session")["signed_volume"].cumsum()

    rule = "1min" if timeframe_minutes <= 1 else f"{timeframe_minutes}min"

    price_df = data.resample(rule).agg(
        open   = ("ltp",         "first"),
        high   = ("ltp",         "max"),
        low    = ("ltp",         "min"),
        close  = ("ltp",         "last"),
        volume = ("tick_volume", "sum"),
    )
    price_df = price_df.dropna(subset=["open", "high", "low", "close"])

    # Build raw CVD OHLC then enforce gapless opens.
    cvd_df = data.resample(rule).agg(
        open  = ("cvd", "first"),
        high  = ("cvd", "max"),
        low   = ("cvd", "min"),
        close = ("cvd", "last"),
    )
    cvd_df = cvd_df.dropna(subset=["open", "high", "low", "close"])
    cvd_df["session"] = cvd_df.index.date
    cvd_df = _fix_cvd_opens(cvd_df)   # ← gapless open enforcement

    return cvd_df, price_df


# ---------------------------------------------------------------------------
# Background data-fetch worker
# ---------------------------------------------------------------------------

class _DataFetchWorker(QObject):
    result_ready = Signal(object, object, float, object)
    error        = Signal(str)
    finished     = Signal()

    def __init__(
        self,
        kite,
        instrument_token,
        from_dt,
        to_dt,
        timeframe_minutes,
        focus_mode,
        price_instrument_token=None,
    ):
        super().__init__()
        self.kite               = kite
        self.instrument_token   = instrument_token
        self.price_instrument_token = price_instrument_token or instrument_token
        self.from_dt            = from_dt
        self.to_dt              = to_dt
        self.timeframe_minutes  = timeframe_minutes
        self.focus_mode         = focus_mode
        self._cancelled         = False
        self._auth_refresh_attempted = False

    def cancel(self):
        self._cancelled = True

    def quit_thread(self):
        self._cancelled = True

    # ── Internal helpers ────────────────────────────────────────────────────

    def _fetch_historical_with_retry(self, instrument_token, from_dt, to_dt):
        max_attempts      = 3
        base_delay_seconds = 0.25

        for attempt in range(max_attempts):
            if self._cancelled:
                return []
            try:
                return self.kite.historical_data(
                    instrument_token,
                    from_dt,
                    to_dt,
                    interval="minute",
                )
            except Exception as exc:
                if self._is_auth_error(exc) and self._refresh_access_token_if_possible():
                    continue
                if attempt == max_attempts - 1:
                    raise
                time.sleep(base_delay_seconds * (2 ** attempt))

        return []

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        msg = str(exc)
        return (
            "Incorrect `api_key` or `access_token`." in msg
            or "TokenException" in msg
        )

    def _refresh_access_token_if_possible(self) -> bool:
        if self._auth_refresh_attempted:
            return False
        self._auth_refresh_attempted = True
        try:
            token_data   = TokenManager().load_token_data() or {}
            fresh_token  = token_data.get("access_token")
            if not fresh_token:
                return False
            current_token = getattr(self.kite, "access_token", None)
            if fresh_token == current_token:
                return False
            self.kite.set_access_token(fresh_token)
            logger.info("[AUTO] Refreshed access token for CVD historical fetch; retrying.")
            return True
        except Exception as exc:
            logger.warning(
                "[AUTO] Failed to refresh access token for CVD historical fetch: %s", exc
            )
            return False

    def _load_minute_history(self):
        required_sessions = 2
        max_lookback_days = 30
        chunk_days        = 5

        aggregate_df = pd.DataFrame()
        range_end    = self.to_dt
        range_start  = self.from_dt

        while (self.to_dt - range_start).days <= max_lookback_days:
            if self._cancelled:
                return pd.DataFrame()
            hist = self._fetch_historical_with_retry(self.instrument_token, range_start, range_end)

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

            range_end   = range_start
            range_start = range_start - pd.Timedelta(days=chunk_days)

        return aggregate_df

    def _load_price_minute_history(self):
        required_sessions = 2
        max_lookback_days = 30
        chunk_days = 5

        aggregate_df = pd.DataFrame()
        range_end = self.to_dt
        range_start = self.from_dt

        while (self.to_dt - range_start).days <= max_lookback_days:
            if self._cancelled:
                return pd.DataFrame()

            hist = self._fetch_historical_with_retry(
                self.price_instrument_token,
                range_start,
                range_end,
            )

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

    # ── Main run ─────────────────────────────────────────────────────────────

    def run(self):
        try:
            if self._cancelled:
                return

            # ── Step 1: Always fetch at 1-minute granularity ─────────────────
            df = self._load_minute_history()
            price_df_1m = self._load_price_minute_history()
            if self._cancelled:
                return
            if df.empty or price_df_1m.empty:
                self.error.emit("empty_df")
                return

            # ── Step 2: Build CVD at 1m resolution ───────────────────────────
            # CVDHistoricalBuilder now produces gapless 1m OHLC:
            #   open[i] = close[i-1]  (within session),  open[session_start] = 0
            cvd_1m = CVDHistoricalBuilder.build_cvd_ohlc(df)
            if self._cancelled:
                return

            cvd_1m["session"] = cvd_1m.index.date

            # ── Step 3: Resample to target timeframe ──────────────────────────
            if self.timeframe_minutes > 1:
                rule = f"{self.timeframe_minutes}min"

                price_df = price_df_1m.resample(rule).agg(
                    open   = ("open",   "first"),
                    high   = ("high",   "max"),
                    low    = ("low",    "min"),
                    close  = ("close",  "last"),
                    volume = ("volume", "sum"),
                )
                price_df["volume"] = price_df["volume"].fillna(0)
                price_df = price_df.dropna(subset=["open", "high", "low", "close"])

                # Resample CVD from 1m OHLC.
                # CVD is a running cumsum: first/max/min/last correctly captures
                # the buyer/seller dominance range within each HTF bar.
                cvd_df = (
                    cvd_1m
                    .drop(columns=["session"], errors="ignore")
                    .resample(rule)
                    .agg(
                        open  = ("open",  "first"),
                        high  = ("high",  "max"),
                        low   = ("low",   "min"),
                        close = ("close", "last"),
                    )
                    .dropna(subset=["open", "high", "low", "close"])
                )
                cvd_df["session"] = cvd_df.index.date

                # ── CRITICAL: enforce gapless opens after resampling ──────────
                # Resampling collapses multiple 1m bars per HTF bar. The first
                # 1m bar's open (= prev 1m close) becomes the HTF open — correct.
                # But if there are gaps in 1m data or the first 1m bar was the
                # session opener, the HTF open must still equal the previous HTF
                # bar's close. _fix_cvd_opens guarantees this invariant.
                cvd_df = _fix_cvd_opens(cvd_df)

            else:
                # 1m: CVDHistoricalBuilder already produces gapless candles.
                cvd_df = cvd_1m
                price_df = price_df_1m.copy()

            # ── Step 4: Session filtering and output ──────────────────────────
            sessions = sorted(cvd_df["session"].unique())
            if not sessions:
                self.error.emit("no_sessions")
                return

            prev_close       = 0.0
            previous_day_cpr = None

            if len(sessions) >= 2:
                prev_data = cvd_df[cvd_df["session"] == sessions[-2]]
                if not prev_data.empty:
                    prev_close = float(prev_data["close"].iloc[-1])

            price_df["session"] = price_df.index.date

            if self.focus_mode:
                cvd_out   = cvd_df[cvd_df["session"] == sessions[-1]].copy()
                price_out = price_df[price_df["session"] == sessions[-1]].copy()
            else:
                cvd_out   = cvd_df[cvd_df["session"].isin(sessions[-2:])].copy()
                price_out = price_df[price_df["session"].isin(sessions[-2:])].copy()

            if len(sessions) >= 2:
                prev_day_price   = price_df[price_df["session"] == sessions[-2]]
                previous_day_cpr = CPRCalculator.get_previous_day_cpr(prev_day_price)

            if self._cancelled:
                return

            self.result_ready.emit(cvd_out, price_out, prev_close, previous_day_cpr)

        except Exception as exc:
            if self._is_auth_error(exc):
                self.error.emit("auth_failed")
            else:
                self.error.emit(str(exc))
        finally:
            self.finished.emit()
