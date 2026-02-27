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

Unwind Cooldown (BUG FIX — 2026-02-27):
  After a LIFO unwind, the stacker must NOT immediately re-stack at the same
  price level. The live tick loop was causing a buy→unwind→buy oscillation loop
  when price hovered at a stack boundary.

  Fix: mark_unwind() raises the minimum favorable-move required before the
  next stack is allowed. Price must travel a full additional step beyond the
  unwind point before re-stacking is permitted.
  This mirrors the simulator's _did_unwind guard but works at tick granularity.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class StackEntry:
    """A single stacked position added after the anchor."""
    entry_price: float
    entry_bar_idx: int
    stack_number: int          # 1-based: 1 = first stack after anchor
    layer_tag: str = ""       # e.g. STACK_1, STACK_2
    tradingsymbols: list[str] = field(default_factory=list)
    qty_per_symbol: int = 0


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
    anchor_tradingsymbols: list[str] = field(default_factory=list)
    anchor_qty_per_symbol: int = 0
    profit_harvest_threshold: float = 0.0
    profit_harvest_enabled: bool = False
    _harvest_floor: float = field(default=0.0, repr=False)

    # ── Unwind cooldown (live tick loop guard) ─────────────────────────────
    # After a LIFO unwind, price must travel a full extra step beyond the
    # unwind level before a new stack is allowed. Prevents buy→unwind→buy
    # oscillation when price hovers at a stack boundary between ticks.
    _unwind_cooldown_min_points: float = field(default=0.0, repr=False)

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
        move = self.favorable_move(current_price)
        # ── Unwind cooldown guard ──────────────────────────────────────────
        # After an unwind, require price to move a full extra step beyond the
        # cooldown floor before re-stacking is allowed.
        if move < self._unwind_cooldown_min_points:
            return False
        return move >= self.next_trigger_points

    def add_stack(
        self,
        entry_price: float,
        bar_idx: int,
        tradingsymbols: list[str] | None = None,
        qty_per_symbol: int = 0,
    ):
        """Record a new stack entry and advance the trigger threshold."""
        # Dedup guard: avoid double-firing on jittery duplicate ticks near
        # the same trigger level. CRITICAL: we MUST still advance next_trigger_points
        # even when skipping, otherwise the caller's `while should_add_stack()`
        # loop never terminates (infinite loop / app freeze).
        if self.stack_entries and abs(self.stack_entries[-1].entry_price - entry_price) < 0.5:
            self.next_trigger_points += self.step_points  # advance trigger so while-loop exits
            return

        self.stack_entries.append(StackEntry(
            entry_price=entry_price,
            entry_bar_idx=bar_idx,
            stack_number=len(self.stack_entries) + 1,
            layer_tag=f"STACK_{len(self.stack_entries) + 1}",
            tradingsymbols=list(tradingsymbols or []),
            qty_per_symbol=int(qty_per_symbol or 0),
        ))
        self.next_trigger_points += self.step_points   # advance to next level

        # Stacking successfully — clear any lingering cooldown floor now that
        # price has proven it moved far enough.
        self._unwind_cooldown_min_points = 0.0

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
        ids_to_remove = {id(e) for e in entries}
        self.stack_entries = [s for s in self.stack_entries if id(s) not in ids_to_remove]

        # Recalibrate next trigger: resume from the level above the highest
        # remaining stack, measured from the anchor.
        if self.stack_entries:
            # next trigger = one step above the last surviving stack entry
            last_surviving_move = self.favorable_move(self.stack_entries[-1].entry_price)
            self.next_trigger_points = last_surviving_move + self.step_points
        else:
            # All stacks gone — fresh start from anchor
            self.next_trigger_points = self.step_points

        # Keep harvest floor realistic after LIFO unwinds.
        # Conservative reset: only fully reset when pyramid is empty.
        if self.profit_harvest_enabled and self._harvest_floor > 0:
            if not self.stack_entries:
                self._harvest_floor = 0.0

    def mark_unwind(self) -> None:
        """
        Call this immediately after remove_stacks() in the live tick path.

        Raises the minimum favorable-move threshold required before the next
        stack is allowed. Price must travel one full extra step beyond the
        current next_trigger_points before should_add_stack() returns True.

        This stops the buy→unwind→buy oscillation loop that occurs when price
        hovers at a stack boundary and tick callbacks fire faster than QTimer
        signals are processed by the coordinator.

        The simulator uses a _did_unwind boolean per bar — this is the
        equivalent protection for the continuous live tick path.
        """
        # next_trigger_points is already recalibrated by remove_stacks().
        # We require price to go one full step BEYOND that before re-stacking.
        self._unwind_cooldown_min_points = self.next_trigger_points + self.step_points

    def is_in_unwind_cooldown(self, current_price: float) -> bool:
        """
        True if price has NOT yet traveled far enough from the unwind level
        to justify a new stack. Returns False if no cooldown is active.
        """
        if self._unwind_cooldown_min_points <= 0.0:
            return False
        return self.favorable_move(current_price) < self._unwind_cooldown_min_points

    def compute_partial_pnl(
        self,
        entries: list[StackEntry],
        exit_price: float,
        slippage_points: float = 0.0,
    ) -> float:
        """P&L for a specific set of stack entries being exited early."""
        if self.signal_side == "long":
            effective_exit = exit_price - slippage_points
            return sum(effective_exit - e.entry_price for e in entries)
        effective_exit = exit_price + slippage_points
        return sum(e.entry_price - effective_exit for e in entries)

    # ── FIFO Profit Harvest ────────────────────────────────────────────────

    def setup_harvest(self, threshold_rupees: float) -> None:
        """
        Enable profit harvesting. Called once when anchor trade is opened.
        threshold_rupees: user-set value e.g. 10000
        """
        self.profit_harvest_threshold = threshold_rupees
        self.profit_harvest_enabled = threshold_rupees > 0
        self._harvest_floor = 0.0

    def should_harvest_profit(self, total_pnl_rupees: float) -> bool:
        """
        FIFO harvest trigger: fires when live PnL crosses the next floor.
        Uses rupees directly from position_manager — no manual calculation.
        """
        if not self.profit_harvest_enabled:
            return False
        if not self.stack_entries:
            return False
        return total_pnl_rupees >= (self._harvest_floor + self.profit_harvest_threshold)

    def harvest_oldest_stack(self) -> StackEntry | None:
        """
        FIFO: pop STACK_1 (oldest/lowest entry = most locked profit).
        Advances the floor so next harvest needs another full threshold gain.
        Recalibrates stacking trigger from the new top of pyramid.
        """
        if not self.stack_entries:
            return None

        oldest = self.stack_entries.pop(0)
        self._harvest_floor += self.profit_harvest_threshold

        if self.stack_entries:
            last_move = self.favorable_move(self.stack_entries[-1].entry_price)
            self.next_trigger_points = last_move + self.step_points
        else:
            self.next_trigger_points = self.step_points

        return oldest