# chop_filter.py
import numpy as np


class ChopFilter:
    """
    Institutional concept: Choppiness Index filters ranging/sideways markets.
    CHOP = 100 * log10(ATR14_sum / (High_n - Low_n)) / log10(n)
    < 38.2 = trending (allow signals)
    > 61.8 = choppy (block signals)
    """

    def __init__(self, period: int = 14, threshold: float = 61.8):
        self.period = period
        self.threshold = threshold

    def is_choppy(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> bool:
        n = self.period
        if len(closes) < n + 1:
            return False  # not enough data → don't block

        tr = np.maximum(
            highs[-n:] - lows[-n:],
            np.maximum(
                np.abs(highs[-n:] - closes[-n - 1:-1]),
                np.abs(lows[-n:] - closes[-n - 1:-1])
            )
        )
        atr_sum = tr.sum()
        range_hl = highs[-n:].max() - lows[-n:].min()

        if range_hl <= 0:
            return True  # flat market = choppy

        chop = 100 * np.log10(atr_sum / range_hl) / np.log10(n)
        return float(chop) > self.threshold