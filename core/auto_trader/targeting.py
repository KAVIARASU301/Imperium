"""Target-price helpers for auto-trader routing."""


def compute_target_price(*, side: str, average_price: float, sl_distance: float, tp_multiplier: float, target_mode: str, ema51: float) -> float:
    """Compute target price from configured mode.

    atr: use ATR multiple from entry.
    ema51_cross: use latest EMA51 level as target.
    """
    if target_mode == "ema51_cross" and ema51 > 0:
        return ema51

    if side == "long":
        return average_price + (sl_distance * tp_multiplier)
    return average_price - (sl_distance * tp_multiplier)
