from core.auto_trader.stacker import StackerState


def test_autotrader_stacker_deep_reversal_wipeout():

    stacker = StackerState(
        anchor_entry_price=100.0,
        anchor_bar_idx=0,
        signal_side="long",
        step_points=10.0,
        max_stacks=3,
    )

    total_unwind_pnl = 0.0

    # Build full pyramid
    for price in [110.0, 120.0, 130.0]:
        while stacker.should_add_stack(price):
            stacker.add_stack(price, 1)

    assert len(stacker.stack_entries) == 3

    # Deep reversal to 90
    crash_price = 90.0

    to_unwind = stacker.stacks_to_unwind(crash_price)

    # All stacks should unwind
    assert len(to_unwind) == 3

    unwind_pnl = stacker.compute_partial_pnl(to_unwind, crash_price)
    total_unwind_pnl += unwind_pnl

    stacker.remove_stacks(to_unwind)

    # Ensure no stacks remain
    assert len(stacker.stack_entries) == 0

    # Anchor exit at 90
    anchor_exit = stacker.compute_total_pnl(exit_price=crash_price)

    final_total = anchor_exit + total_unwind_pnl

    # Expected:
    # Stack losses: -90
    # Anchor loss: -10
    # TOTAL = -100

    assert final_total == -100.0