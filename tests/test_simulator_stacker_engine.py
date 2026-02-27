import numpy as np
from datetime import datetime, timedelta

from core.auto_trader.simulator import SimulatorMixin


# ─────────────────────────────────────────────
# Minimal Dummy Simulator
# ─────────────────────────────────────────────

class DummyInput:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class DummyCheck:
    def __init__(self, checked):
        self._checked = checked

    def isChecked(self):
        return self._checked


class DummySimulator(SimulatorMixin):

    def __init__(self):
        # Minimal required attributes

        prices = np.array([100, 110, 120, 115, 130], dtype=float)

        self.all_price_data = prices
        self.all_price_high_data = prices
        self.all_price_low_data = prices
        self.all_cvd_data = prices  # keep identical for simplicity

        base_time = datetime(2024, 1, 1, 9, 15)
        self.all_timestamps = [
            base_time + timedelta(minutes=i) for i in range(len(prices))
        ]

        self.stacker_enabled_check = DummyCheck(True)
        self.open_drive_stack_enabled_check = DummyCheck(False)

        self.stacker_step_input = DummyInput(10.0)
        self.stacker_max_input = DummyInput(3)

        self.automation_stoploss_input = DummyInput(1000)
        self.max_profit_giveback_input = DummyInput(0)
        self.open_drive_max_profit_giveback_input = DummyInput(0)

        self.max_profit_giveback_strategies = set()

        self._selected_max_giveback_strategies = lambda: set()

        self._active_strategy_priorities = lambda: ([], {})
        self._selected_dynamic_exit_strategies = lambda: set()

        self._selected_signal_filter = lambda: None

        self.regime_enabled_check = DummyCheck(False)
        self.regime_engine = None

        self.live_mode = False

        # Confluence placeholders
        self._confluence_line_map = {}

    # required stub
    def _active_strategy_priorities(self):
        return [], {}


# ─────────────────────────────────────────────
# Engine Test
# ─────────────────────────────────────────────

def test_simulator_stacker_engine_accounting():

    sim = DummySimulator()

    x_arr = np.arange(len(sim.all_price_data))

    # Create simple long entry at index 0 only
    long_mask = np.array([True, False, False, False, False])
    short_mask = np.zeros_like(long_mask)

    results = sim._run_trade_simulation(
        x_arr=x_arr,
        short_mask=short_mask,
        long_mask=long_mask,
        strategy_masks=None,
    )

    # We expect:
    # 1 stack unwind at 115
    # 1 profitable stack exit at 130

    assert results["stacked_positions"] >= 1
    assert results["unwind_losses"] >= 1

    # Most important: no double counting
    total_points = results["total_points"]

    # Anchor: +30
    # Stack1: +20
    # Stack2 unwind: -5
    # Expected = 45

    assert round(total_points, 2) == 45.0