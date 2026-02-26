"""
core/auto_trader/stacker.py

Pyramid position stacker — adds a new position every N favorable points
from the anchor entry.

Architecture:
  - StackerState tracks the anchor + all stack entries for ONE instrument token
  - AutoTraderDialog holds one StackerState per active trade (reset on exit)
  - On each bar: check if favorable move has crossed the next stack trigger
  - On exit: fire exit signal for anchor + all N stacked entries

Defensive LIFO Unwind (anti-loss logic):
  When the market reverses against stacked positions *before* the anchor's
  exit signal fires, the stacker unwinds stacks one by one in LIFO order
  as price crosses back through each stack's entry price.

  Flow:
    1. Market goes against pyramid → stacks_to_unwind() returns breached entries
    2. Each breached stack is exited immediately (near breakeven)
    3. remove_stacks() trims the list and resets the trigger threshold
    4. When market turns favorable again, stacking resumes from current level
    5. Anchor exits only on its own exit signal → then ALL remaining stacks exit

  The anchor is NEVER part of the unwind — it holds until its own signal fires.

Key design decision: stacking threshold is measured from the ANCHOR entry
price, not from each stack entry. This keeps the pyramid consistent and
avoids phantom triggers when later entries move the reference point.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class StackEntry:
    """A single stacked position added after the anchor."""
    entry_price: float
    entry_bar_idx: int
    stack_number: int          # 1-based: 1 = first stack after anchor


@dataclass
class StackerState:
    """
    Live stacker state for one active anchor trade.
    Reset to None when the anchor trade exits.
    """
    anchor_entry_price: float
    anchor_bar_idx: int
    signal_side: str                         # "long" | "short"
    step_points: float                       # user-set stack interval
    max_stacks: int                          # user-set cap (1-5)
    stack_entries: list[StackEntry] = field(default_factory=list)
    next_trigger_points: float = 0.0        # next threshold from anchor

    def __post_init__(self):
        self.next_trigger_points = self.step_points  # first trigger = 1x step

    @property
    def total_positions(self) -> int:
        """Anchor (1) + all stacks."""
        return 1 + len(self.stack_entries)

    @property
    def can_stack_more(self) -> bool:
        return len(self.stack_entries) < self.max_stacks

    def favorable_move(self, current_price: float) -> float:
        """Points gained from anchor entry in signal direction."""
        if self.signal_side == "long":
            return current_price - self.anchor_entry_price
        return self.anchor_entry_price - current_price

    def should_add_stack(self, current_price: float) -> bool:
        """True if price has crossed the next stacking threshold."""
        if not self.can_stack_more:
            return False
        return self.favorable_move(current_price) >= self.next_trigger_points

    def add_stack(self, entry_price: float, bar_idx: int):
        """Record a new stack entry and advance the trigger threshold."""
        # Dedup guard: avoid double-firing on jittery duplicate ticks near
        # the same trigger level.
        if self.stack_entries and abs(self.stack_entries[-1].entry_price - entry_price) < 0.5:
            return

        self.stack_entries.append(StackEntry(
            entry_price=entry_price,
            entry_bar_idx=bar_idx,
            stack_number=len(self.stack_entries) + 1,
        ))
        self.next_trigger_points += self.step_points   # advance to next level

    def compute_total_pnl(self, exit_price: float) -> float:
        """
        Sum P&L across anchor + all stacks at the given exit price.
        Used by simulator for accurate reporting.
        """
        if self.signal_side == "long":
            anchor_pnl = exit_price - self.anchor_entry_price
            stack_pnl = sum(exit_price - s.entry_price for s in self.stack_entries)
        else:
            anchor_pnl = self.anchor_entry_price - exit_price
            stack_pnl = sum(s.entry_price - exit_price for s in self.stack_entries)
        return anchor_pnl + stack_pnl

    # ── Defensive LIFO unwind ──────────────────────────────────────────────

    def stacks_to_unwind(self, current_price: float) -> list[StackEntry]:
        """
        Returns stack entries that have been crossed adversely (LIFO order).

        When market reverses against stacked positions, exit each stack whose
        entry price has been breached — from the top (most recent) downward.
        Stops at the first stack that has NOT been breached, so we never
        skip over a still-profitable stack to exit a deeper one.

        The anchor is NEVER included — it exits only on its own exit signal.
        """
        to_exit: list[StackEntry] = []
        for entry in reversed(self.stack_entries):
            if self.signal_side == "long":
                breached = current_price <= entry.entry_price
            else:
                breached = current_price >= entry.entry_price
            if breached:
                to_exit.append(entry)
            else:
                break  # stop — everything below this is still in profit
        return to_exit

    def remove_stacks(self, entries: list[StackEntry]) -> None:
        """
        Remove the given stack entries (result of stacks_to_unwind) and
        reset the stacking threshold so new stacks can be added again from
        the current top of the remaining pyramid.
        """
        for e in entries:
            if e in self.stack_entries:
                self.stack_entries.remove(e)

        # Recalibrate next trigger: resume from the level above the highest
        # remaining stack, measured from the anchor.
        if self.stack_entries:
            # next trigger = one step above the last surviving stack entry
            last_surviving_move = self.favorable_move(self.stack_entries[-1].entry_price)
            self.next_trigger_points = last_surviving_move + self.step_points
        else:
            # All stacks gone — fresh start from anchor
            self.next_trigger_points = self.step_points

    def compute_partial_pnl(self, entries: list[StackEntry], exit_price: float) -> float:
        """P&L for a specific set of stack entries being exited early."""
        if self.signal_side == "long":
            return sum(exit_price - e.entry_price for e in entries)
        return sum(e.entry_price - exit_price for e in entries)
