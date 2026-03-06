import pandas as pd
import numpy as np


class CVDHistoricalBuilder:
    """
    Builds TradingView-style CVD candles from historical OHLCV data.

    Approximates requestVolumeDelta() using candle direction:
      close > open  → +volume (buy pressure)
      close < open  → -volume (sell pressure)
      close == open → 0 (neutral)

    INSTITUTIONAL RULE — NO CVD GAPS:
      CVD is a continuous cumulative sum. The open of every bar equals
      the close of the previous bar within the same session.
      Session-start bars open at 0 (anchored reset).
    """

    @staticmethod
    def build_cvd_ohlc(
        df: pd.DataFrame,
        anchor: str = "1D"
    ) -> pd.DataFrame:
        """
        Parameters
        ----------
        df : DataFrame
            Must contain columns: open, high, low, close, volume
            Index must be datetime
        anchor : str
            Currently supports "1D" (daily reset)

        Returns
        -------
        DataFrame with columns: open, high, low, close (CVD candles, no gaps)
        """

        if df.empty:
            return pd.DataFrame()

        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(df.columns):
            raise ValueError("DataFrame must contain OHLCV columns")

        data = df.copy()

        # ── Step 1: Delta per candle ─────────────────────────────────────────
        # Vectorized: full-bar net delta approximation
        data["delta"] = np.where(
            data["close"] > data["open"],
             data["volume"],
            np.where(
                data["close"] < data["open"],
                -data["volume"],
                0.0
            )
        )

        # ── Step 2: Session-anchored cumsum (resets daily) ───────────────────
        if anchor == "1D":
            data["anchor"] = data.index.date
        else:
            raise NotImplementedError("Only daily anchor (1D) is supported")

        # cvd_close = cumulative delta up to and including this bar
        data["cvd_close"] = data.groupby("anchor")["delta"].cumsum()

        # ── Step 3: Gapless OHLC — open = previous bar's close ──────────────
        # Key insight: CVD is a running total. There is NO economic gap between
        # consecutive bars. open[i] must equal close[i-1]; session open = 0.
        data["cvd_open"] = (
            data.groupby("anchor")["cvd_close"]
            .shift(1)
            .fillna(0.0)           # first bar of each session opens at 0
        )

        # Intra-bar direction is monotonic (one net delta per OHLCV bar),
        # so high = max(open, close), low = min(open, close).
        data["cvd_high"] = np.maximum(data["cvd_open"], data["cvd_close"])
        data["cvd_low"]  = np.minimum(data["cvd_open"], data["cvd_close"])

        cvd_ohlc = pd.DataFrame({
            "open":  data["cvd_open"],
            "high":  data["cvd_high"],
            "low":   data["cvd_low"],
            "close": data["cvd_close"],
        }, index=data.index)

        return cvd_ohlc