import pandas as pd
import numpy as np


class CVDHistoricalBuilder:
    """
    Builds TradingView-style CVD candles from historical OHLCV data.

    This approximates requestVolumeDelta() using candle direction:
    - close > open  → buy volume
    - close < open  → sell volume
    - close == open → neutral
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
        DataFrame with columns: open, high, low, close (CVD candles)
        """

        if df.empty:
            return pd.DataFrame()

        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(df.columns):
            raise ValueError("DataFrame must contain OHLCV columns")

        data = df.copy()

        # --------------------------------------------------------------
        # Step 1: Approximate delta per candle (SAFE vectorized logic)
        # --------------------------------------------------------------
        data["delta"] = np.where(
            data["close"] > data["open"],
            data["volume"],
            np.where(
                data["close"] < data["open"],
                -data["volume"],
                0.0
            )
        )

        # --------------------------------------------------------------
        # Step 2: Anchor logic (session reset)
        # --------------------------------------------------------------
        if anchor == "1D":
            data["anchor"] = data.index.date
            data["cvd"] = data.groupby("anchor")["delta"].cumsum()
        else:
            raise NotImplementedError("Only daily anchor (1D) is supported")

        # --------------------------------------------------------------
        # Step 3: Build CVD OHLC candles
        # --------------------------------------------------------------
        cvd_ohlc = data.groupby(data.index).agg(
            open=("cvd", "first"),
            high=("cvd", "max"),
            low=("cvd", "min"),
            close=("cvd", "last"),
        )

        return cvd_ohlc
