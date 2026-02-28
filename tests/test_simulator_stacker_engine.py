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


class DummyCombo:
    def __init__(self, data):
        self._data = data

    def currentData(self):
        return self._data


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
        self.giveback_promotion_points_input = DummyInput(150)
        self.exit_mode_combo = DummyCombo("giveback")

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


def test_giveback_qualification_promotes_to_trend_mode():
    sim = DummySimulator()
    sim.all_price_data = np.array([100, 300, 260, 250], dtype=float)
    sim.all_price_high_data = sim.all_price_data
    sim.all_price_low_data = sim.all_price_data
    sim.all_cvd_data = sim.all_price_data

    base_time = datetime(2024, 1, 1, 9, 15)
    sim.all_timestamps = [base_time + timedelta(minutes=i) for i in range(len(sim.all_price_data))]

    sim.max_profit_giveback_input = DummyInput(40)
    sim.exit_mode_combo = DummyCombo("trend")
    sim._selected_max_giveback_strategies = lambda: {"ema_cross"}

    def _resolve_signal(**kwargs):
        idx = kwargs["idx"]
        return ("long", "ema_cross") if idx == 0 else (None, None)

    sim._resolve_signal_side_and_strategy = _resolve_signal

    x_arr = np.arange(len(sim.all_price_data))
    long_mask = np.array([True, False, False, False])
    short_mask = np.array([False, False, False, False])

    results = sim._run_trade_simulation(x_arr=x_arr, short_mask=short_mask, long_mask=long_mask, strategy_masks=None)

    assert round(results["total_points"], 2) == 150.0


def test_giveback_qualification_keeps_giveback_before_threshold():
    sim = DummySimulator()
    sim.all_price_data = np.array([100, 220, 170, 160], dtype=float)
    sim.all_price_high_data = sim.all_price_data
    sim.all_price_low_data = sim.all_price_data
    sim.all_cvd_data = sim.all_price_data

    base_time = datetime(2024, 1, 1, 9, 15)
    sim.all_timestamps = [base_time + timedelta(minutes=i) for i in range(len(sim.all_price_data))]

    sim.max_profit_giveback_input = DummyInput(40)
    sim.exit_mode_combo = DummyCombo("trend")
    sim.giveback_promotion_points_input = DummyInput(150)
    sim._selected_max_giveback_strategies = lambda: {"ema_cross"}

    def _resolve_signal(**kwargs):
        idx = kwargs["idx"]
        return ("long", "ema_cross") if idx == 0 else (None, None)

    sim._resolve_signal_side_and_strategy = _resolve_signal

    x_arr = np.arange(len(sim.all_price_data))
    long_mask = np.array([True, False, False, False])
    short_mask = np.array([False, False, False, False])

    results = sim._run_trade_simulation(x_arr=x_arr, short_mask=short_mask, long_mask=long_mask, strategy_masks=None)

    assert round(results["total_points"], 2) == 70.0


def test_simulator_allows_entry_exactly_at_cutoff_time():
    sim = DummySimulator()
    sim.all_price_data = np.array([100, 101, 102], dtype=float)
    sim.all_price_high_data = sim.all_price_data
    sim.all_price_low_data = sim.all_price_data
    sim.all_cvd_data = sim.all_price_data

    base_time = datetime(2024, 1, 1, 15, 14)
    sim.all_timestamps = [base_time + timedelta(minutes=i) for i in range(len(sim.all_price_data))]

    def _resolve_signal(**kwargs):
        idx = kwargs["idx"]
        return ("long", "atr_reversal") if idx == 1 else (None, None)

    sim._resolve_signal_side_and_strategy = _resolve_signal

    x_arr = np.arange(len(sim.all_price_data))
    long_mask = np.array([False, True, False])
    short_mask = np.array([False, False, False])

    results = sim._run_trade_simulation(x_arr=x_arr, short_mask=short_mask, long_mask=long_mask, strategy_masks=None)

    assert results["trades"] == 1
    assert results["taken_long_x"] == [1.0]


def test_simulator_rejects_entry_after_cutoff_time():
    sim = DummySimulator()
    sim.all_price_data = np.array([100, 101, 102], dtype=float)
    sim.all_price_high_data = sim.all_price_data
    sim.all_price_low_data = sim.all_price_data
    sim.all_cvd_data = sim.all_price_data

    base_time = datetime(2024, 1, 1, 15, 14)
    sim.all_timestamps = [base_time + timedelta(minutes=i) for i in range(len(sim.all_price_data))]

    def _resolve_signal(**kwargs):
        idx = kwargs["idx"]
        return ("long", "atr_reversal") if idx == 2 else (None, None)

    sim._resolve_signal_side_and_strategy = _resolve_signal

    x_arr = np.arange(len(sim.all_price_data))
    long_mask = np.array([False, False, True])
    short_mask = np.array([False, False, False])

    results = sim._run_trade_simulation(x_arr=x_arr, short_mask=short_mask, long_mask=long_mask, strategy_masks=None)

    assert results["trades"] == 0
    assert results["taken_long_x"] == []
