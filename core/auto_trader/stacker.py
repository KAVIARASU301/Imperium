"""
core/auto_trader/stacker.py

Pyramid position stacker — adds a new position every N favorable points
from the anchor entry. All stacked positions exit together when the
anchor's exit condition fires.

Architecture:
  - StackerState tracks the anchor + all stack entries for ONE instrument token
  - AutoTraderDialog holds one StackerState per active trade (reset on exit)
  - On each bar: check if favorable move has crossed the next stack trigger
  - On exit: fire exit signal for anchor + all N stacked entries

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