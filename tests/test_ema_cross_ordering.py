import importlib.util
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "auto_trader" / "strategy_signal_detector.py"

SPEC = importlib.util.spec_from_file_location("strategy_signal_detector", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
StrategySignalDetector = MODULE.StrategySignalDetector


def _base_inputs(length: int = 40):
    price_atr_above = np.zeros(length, dtype=bool)
    price_atr_below = np.zeros(length, dtype=bool)
    cvd_atr_above = np.zeros(length, dtype=bool)
    cvd_atr_below = np.zeros(length, dtype=bool)

    atr_values = np.full(length, 2.0)
    price_close = np.linspace(100.0, 110.0, length)
    price_open = price_close - 0.2
    return price_atr_above, price_atr_below, cvd_atr_above, cvd_atr_below, atr_values, price_close, price_open


def test_atr_reversal_requires_price_and_cvd_overextension_for_short_setup():
    detector = StrategySignalDetector(timeframe_minutes=1)
    p_above, p_below, c_above, c_below, atr, close, open_ = _base_inputs()

    idx = 20
    p_above[idx] = True
    # No CVD extension -> should not trigger even with relaxed exhaustion gate.
    c_above[idx] = False
    close[idx] = close[idx - 1] - 1.0
    open_[idx] = close[idx] + 1.0

    short_sig, long_sig, _, _ = detector.detect_atr_reversal_strategy(
        price_atr_above=p_above,
        price_atr_below=p_below,
        cvd_atr_above=c_above,
        cvd_atr_below=c_below,
        atr_values=atr,
        price_close=close,
        price_open=open_,
        exhaustion_min_score=0,
    )

    assert not np.any(short_sig)
    assert not np.any(long_sig)


def test_atr_reversal_short_fires_when_price_and_cvd_overextension_align():
    detector = StrategySignalDetector(timeframe_minutes=1)
    p_above, p_below, c_above, c_below, atr, close, open_ = _base_inputs()

    idx = 22
    p_above[idx] = True
    c_above[idx] = True
    close[idx] = close[idx - 1] - 1.5
    open_[idx] = close[idx] + 1.0

    short_sig, long_sig, _, _ = detector.detect_atr_reversal_strategy(
        price_atr_above=p_above,
        price_atr_below=p_below,
        cvd_atr_above=c_above,
        cvd_atr_below=c_below,
        atr_values=atr,
        price_close=close,
        price_open=open_,
        exhaustion_min_score=0,
    )

    assert short_sig[idx]
    assert not np.any(long_sig)
