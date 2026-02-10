"""
swing_hunter.py
───────────────
Stop-Loss Hunt Zone Detector

Concepts
────────
• Swing High / Low  : Local pivot (price peak or trough) in a leg
• Virgin Zone       : 0–50% retrace of a leg  →  retail still trapped, market WILL revisit
• Hunted Zone       : >50% retrace            →  weak hands flushed, stop-loss hunt COMPLETE

Algorithm
─────────
1. Smooth price with a rolling median (noise filter)
2. Walk through the series identifying alternating swings (zig-zag):
   - A new High is confirmed when price drops N bars after the peak
   - A new Low  is confirmed when price rises N bars after the trough
3. For every consecutive swing pair (leg), compute the retracement.
4. Mark zones on both the price chart and CVD chart.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import numpy as np


# ──────────────────────────────────────────────
# Data Types
# ──────────────────────────────────────────────

ZoneType = Literal["virgin", "hunted"]


@dataclass
class Swing:
    idx: int            # bar index in the series
    price: float
    kind: Literal["high", "low"]
    timestamp: object   # datetime


@dataclass
class SwingZone:
    leg_start: Swing
    leg_end: Swing
    retrace_swing: Swing     # the pivot that defines how deep the retrace went
    zone_top: float
    zone_bot: float
    zone_type: ZoneType      # "virgin" | "hunted"
    retrace_pct: float       # 0–100
    x_start: int             # chart x coord of zone left edge
    x_end: int               # chart x coord of zone right edge (current bar)


# ──────────────────────────────────────────────
# Core Engine
# ──────────────────────────────────────────────

class SwingHunter:
    """
    Parameters
    ──────────
    pivot_lookback  : bars each side to confirm a swing pivot  (default 3)
    retrace_thresh  : % above which a zone becomes "hunted"   (default 50.0)
    max_swings      : how many recent swing pairs to annotate  (default 5)
    smooth_window   : rolling-median window for noise filter   (default 3)
    """

    def __init__(
        self,
        pivot_lookback: int = 3,
        retrace_thresh: float = 50.0,
        max_swings: int = 6,
        smooth_window: int = 3,
    ):
        self.pivot_lookback = pivot_lookback
        self.retrace_thresh = retrace_thresh
        self.max_swings = max_swings
        self.smooth_window = smooth_window

    # ── public ───────────────────────────────

    def find_zones(
        self,
        prices: np.ndarray,
        timestamps: list,
        x_indices: list[int],
    ) -> tuple[list[Swing], list[SwingZone]]:
        """
        Returns (swings, zones) for the given price series.
        x_indices maps bar position → chart x coordinate.
        """
        if len(prices) < self.pivot_lookback * 2 + 1:
            return [], []

        smoothed = self._smooth(prices)
        swings = self._detect_swings(smoothed, timestamps, x_indices)
        zones = self._compute_zones(swings, x_indices)
        return swings, zones

    # ── private ──────────────────────────────

    def _smooth(self, prices: np.ndarray) -> np.ndarray:
        if self.smooth_window <= 1:
            return prices
        w = self.smooth_window
        out = np.empty_like(prices)
        for i in range(len(prices)):
            lo = max(0, i - w // 2)
            hi = min(len(prices), i + w // 2 + 1)
            out[i] = np.median(prices[lo:hi])
        return out

    def _detect_swings(
        self,
        prices: np.ndarray,
        timestamps: list,
        x_indices: list[int],
    ) -> list[Swing]:
        """
        ZigZag-style alternating swing detection.
        Each swing must be strictly higher/lower than the previous one of same kind.
        """
        n = len(prices)
        lb = self.pivot_lookback
        pivots: list[tuple[int, float, str]] = []  # (idx, price, kind)

        # Step 1: collect raw pivot candidates
        for i in range(lb, n - lb):
            window = prices[i - lb: i + lb + 1]
            is_high = prices[i] == window.max() and prices[i] > prices[i - 1] and prices[i] > prices[i + 1]
            is_low  = prices[i] == window.min() and prices[i] < prices[i - 1] and prices[i] < prices[i + 1]
            if is_high:
                pivots.append((i, prices[i], "high"))
            elif is_low:
                pivots.append((i, prices[i], "low"))

        if not pivots:
            return []

        # Step 2: enforce strict alternation (merge consecutive same-kind)
        alternating: list[tuple[int, float, str]] = [pivots[0]]
        for idx, price, kind in pivots[1:]:
            last_kind = alternating[-1][2]
            if kind == last_kind:
                # Keep the more extreme one
                if (kind == "high" and price > alternating[-1][1]) or \
                   (kind == "low"  and price < alternating[-1][1]):
                    alternating[-1] = (idx, price, kind)
            else:
                alternating.append((idx, price, kind))

        # Step 3: build Swing objects, keep last N
        swings = [
            Swing(
                idx=i,
                price=p,
                kind=k,
                timestamp=timestamps[i] if i < len(timestamps) else None,
            )
            for i, p, k in alternating
        ]

        # Return most recent swings (max_swings * 2 to cover pairs)
        return swings[-(self.max_swings * 2):]

    def _compute_zones(
        self,
        swings: list[Swing],
        x_indices: list[int],
    ) -> list[SwingZone]:
        """
        For each leg (consecutive swing pair), look at the NEXT swing
        to determine how deep the retrace was.

        Leg:  SwingA → SwingB   (e.g. Low → High = bullish leg)
        Retrace: SwingB → SwingC

        retracement % = |SwingC.price - SwingB.price| / |SwingB.price - SwingA.price| * 100
        """
        if len(swings) < 3:
            return []

        zones: list[SwingZone] = []
        n_bars = len(x_indices)

        for i in range(len(swings) - 2):
            A = swings[i]
            B = swings[i + 1]
            C = swings[i + 2]

            leg_size = abs(B.price - A.price)
            if leg_size < 1e-9:
                continue

            retrace_size = abs(C.price - B.price)
            retrace_pct = (retrace_size / leg_size) * 100.0

            # Zone sits between A and B level (the leg itself)
            if B.kind == "high":
                # Bullish leg: A=low, B=high
                zone_top = B.price
                zone_bot = A.price
            else:
                # Bearish leg: A=high, B=low
                zone_top = A.price
                zone_bot = B.price

            # Virgin: retrace < 50% → weak hands NOT yet flushed
            # Hunted: retrace >= 50% → stop hunt complete
            zone_type: ZoneType = "hunted" if retrace_pct >= self.retrace_thresh else "virgin"

            # x coords: from leg start to current last bar
            x_start_idx = min(A.idx, n_bars - 1)
            x_end_idx   = min(n_bars - 1, n_bars - 1)

            x_start = x_indices[x_start_idx]
            x_end   = x_indices[x_end_idx]

            zones.append(SwingZone(
                leg_start=A,
                leg_end=B,
                retrace_swing=C,
                zone_top=zone_top,
                zone_bot=zone_bot,
                zone_type=zone_type,
                retrace_pct=round(retrace_pct, 1),
                x_start=x_start,
                x_end=x_end,
            ))

        return zones