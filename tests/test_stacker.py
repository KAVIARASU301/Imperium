import pytest
from core.auto_trader.stacker import StackerState, StackEntry


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def long_stacker():
    return StackerState(
        anchor_entry_price=100.0,
        anchor_bar_idx=0,
        signal_side="long",
        step_points=10.0,
        max_stacks=3,
    )


@pytest.fixture
def short_stacker():
    return StackerState(
        anchor_entry_price=100.0,
        anchor_bar_idx=0,
        signal_side="short",
        step_points=10.0,
        max_stacks=3,
    )


# ─────────────────────────────────────────────────────────────
# Basic properties
# ─────────────────────────────────────────────────────────────

def test_initial_state(long_stacker):
    assert long_stacker.total_positions == 1
    assert long_stacker.can_stack_more is True
    assert long_stacker.next_trigger_points == 10.0


# ─────────────────────────────────────────────────────────────
# Favorable move logic
# ─────────────────────────────────────────────────────────────

def test_favorable_move_long(long_stacker):
    assert long_stacker.favorable_move(110.0) == 10.0


def test_favorable_move_short(short_stacker):
    assert short_stacker.favorable_move(90.0) == 10.0


# ─────────────────────────────────────────────────────────────
# Stack trigger logic
# ─────────────────────────────────────────────────────────────

def test_should_add_stack(long_stacker):
    assert long_stacker.should_add_stack(109.0) is False
    assert long_stacker.should_add_stack(110.0) is True


def test_add_stack_increases_threshold(long_stacker):
    long_stacker.add_stack(entry_price=110.0, bar_idx=1)
    assert len(long_stacker.stack_entries) == 1
    assert long_stacker.next_trigger_points == 20.0


def test_max_stack_limit(long_stacker):
    long_stacker.add_stack(110.0, 1)
    long_stacker.add_stack(120.0, 2)
    long_stacker.add_stack(130.0, 3)

    assert long_stacker.can_stack_more is False
    assert long_stacker.should_add_stack(140.0) is False


# ─────────────────────────────────────────────────────────────
# Dedup guard
# ─────────────────────────────────────────────────────────────

def test_dedup_guard_advances_trigger(long_stacker):
    long_stacker.add_stack(110.0, 1)
    previous_trigger = long_stacker.next_trigger_points

    # Add near-identical price
    long_stacker.add_stack(110.1, 2)

    # No new stack added
    assert len(long_stacker.stack_entries) == 1

    # But trigger must advance (infinite loop protection)
    assert long_stacker.next_trigger_points == previous_trigger + 10.0


# ─────────────────────────────────────────────────────────────
# LIFO unwind
# ─────────────────────────────────────────────────────────────

def test_lifo_unwind_long(long_stacker):
    long_stacker.add_stack(110.0, 1)
    long_stacker.add_stack(120.0, 2)

    # Price drops to 119 → should unwind only top stack (120)
    to_exit = long_stacker.stacks_to_unwind(119.0)
    assert len(to_exit) == 1
    assert to_exit[0].entry_price == 120.0


def test_remove_stacks_recalibrates_trigger(long_stacker):
    long_stacker.add_stack(110.0, 1)
    long_stacker.add_stack(120.0, 2)

    to_exit = long_stacker.stacks_to_unwind(119.0)
    long_stacker.remove_stacks(to_exit)

    assert len(long_stacker.stack_entries) == 1
    # Next trigger should be one step above remaining stack
    assert long_stacker.next_trigger_points == 20.0


# ─────────────────────────────────────────────────────────────
# PnL calculations
# ─────────────────────────────────────────────────────────────

def test_compute_total_pnl_long(long_stacker):
    long_stacker.add_stack(110.0, 1)
    long_stacker.add_stack(120.0, 2)

    pnl = long_stacker.compute_total_pnl(exit_price=130.0)

    # Anchor: 30
    # Stack1: 20
    # Stack2: 10
    assert pnl == 60.0


def test_compute_partial_pnl_long(long_stacker):
    long_stacker.add_stack(110.0, 1)
    stack = long_stacker.stack_entries[0]

    pnl = long_stacker.compute_partial_pnl(
        entries=[stack],
        exit_price=109.0,
        slippage_points=1.0,
    )

    # effective exit = 108
    # 108 - 110 = -2
    assert pnl == -2.0


# ─────────────────────────────────────────────────────────────
# Profit harvest
# ─────────────────────────────────────────────────────────────

def test_setup_harvest(long_stacker):
    long_stacker.setup_harvest(10000)
    assert long_stacker.profit_harvest_enabled is True
    assert long_stacker.profit_harvest_threshold == 10000