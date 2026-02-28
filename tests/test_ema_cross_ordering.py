import numpy as np
import importlib.util
import sys
import types
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "auto_trader" / "strategy_signal_detector.py"

# Provide a minimal stub for core.auto_trader.constants so this test can run
# without importing the full GUI-heavy core package.
core_pkg = types.ModuleType("core")
auto_trader_pkg = types.ModuleType("core.auto_trader")
constants_mod = types.ModuleType("core.auto_trader.constants")
constants_mod.TRADING_START = (9, 15)
constants_mod.TRADING_END = (15, 30)
constants_mod.MINUTES_PER_SESSION = 375
sys.modules.setdefault("core", core_pkg)
sys.modules.setdefault("core.auto_trader", auto_trader_pkg)
sys.modules["core.auto_trader.constants"] = constants_mod

SPEC = importlib.util.spec_from_file_location("strategy_signal_detector", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
StrategySignalDetector = MODULE.StrategySignalDetector


def _base_series(length: int = 40):
    timestamps = list(range(length))
    price = np.linspace(100, 140, length)
    price_ema10 = np.linspace(95, 135, length)
    price_ema51 = np.linspace(90, 130, length)
    cvd_ema10 = np.full(length, 100.0)
    cvd_ema51 = np.full(length, 110.0)
    return timestamps, price, price_ema10, price_ema51, cvd_ema10, cvd_ema51


def test_ema_cross_long_requires_cvd_cross_order():
    detector = StrategySignalDetector(timeframe_minutes=1)
    timestamps, price, price_ema10, price_ema51, cvd_ema10, cvd_ema51 = _base_series()

    cvd = np.full(len(price), 95.0)
    # Keep CVD below EMA10 before idx=35, then cross EMA10 at idx=35.
    cvd[34] = cvd_ema10[34] - 1.0
    cvd[35] = cvd_ema10[35] + 0.5
    # Cross EMA51 on the next bar after EMA10 cross.
    cvd[35] = min(cvd[35], cvd_ema51[35] - 0.2)
    cvd[36] = cvd_ema51[36] + 1.0

    short_sig, long_sig = detector.detect_ema_cvd_cross_strategy(
        timestamps=timestamps,
        price_data=price,
        price_ema10=price_ema10,
        price_ema51=price_ema51,
        cvd_data=cvd,
        cvd_ema10=cvd_ema10,
        cvd_ema51=cvd_ema51,
        cvd_ema_gap_threshold=0.0,
        use_parent_mask=False,
    )

    assert not np.any(short_sig)
    assert long_sig[36]


def test_ema_cross_long_rejects_same_bar_ema10_and_ema51_cross():
    detector = StrategySignalDetector(timeframe_minutes=1)
    timestamps, price, price_ema10, price_ema51, cvd_ema10, cvd_ema51 = _base_series()

    cvd = np.full(len(price), 95.0)
    # Keep CVD below both EMAs before idx=35.
    cvd[34] = min(cvd_ema10[34], cvd_ema51[34]) - 1.0
    # Cross both EMA10 and EMA51 on the same bar.
    cvd[35] = max(cvd_ema10[35], cvd_ema51[35]) + 1.0
    cvd[36:] = cvd[35]

    short_sig, long_sig = detector.detect_ema_cvd_cross_strategy(
        timestamps=timestamps,
        price_data=price,
        price_ema10=price_ema10,
        price_ema51=price_ema51,
        cvd_data=cvd,
        cvd_ema10=cvd_ema10,
        cvd_ema51=cvd_ema51,
        cvd_ema_gap_threshold=0.0,
        use_parent_mask=False,
    )

    assert not np.any(short_sig)
    assert not np.any(long_sig)
