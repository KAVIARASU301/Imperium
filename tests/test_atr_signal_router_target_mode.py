import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "auto_trader" / "targeting.py"
SPEC = importlib.util.spec_from_file_location("targeting", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
compute_target_price = MODULE.compute_target_price


def test_compute_target_price_uses_atr_multiplier_for_long():
    tp = compute_target_price(
        side="long",
        average_price=100.0,
        sl_distance=5.0,
        tp_multiplier=2.0,
        target_mode="atr",
        ema51=108.0,
    )
    assert tp == 110.0


def test_compute_target_price_uses_atr_multiplier_for_short():
    tp = compute_target_price(
        side="short",
        average_price=100.0,
        sl_distance=5.0,
        tp_multiplier=2.0,
        target_mode="atr",
        ema51=92.0,
    )
    assert tp == 90.0


def test_compute_target_price_uses_ema51_when_mode_selected():
    tp = compute_target_price(
        side="long",
        average_price=100.0,
        sl_distance=5.0,
        tp_multiplier=2.0,
        target_mode="ema51_cross",
        ema51=104.5,
    )
    assert tp == 104.5
