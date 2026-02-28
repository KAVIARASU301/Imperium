"""
hybrid_exit_engine.py
=====================
Institutional-grade hybrid exit engine for intraday options scalping (1m).

CONCEPT — 3-Phase State Machine
--------------------------------
Traditional exits watch for reversals → exits too early when premium is still
convex (expanding non-linearly with momentum).

This engine instead tracks MOMENTUM DECELERATION, not reversal:

    EARLY        → noise zone, only hard stop active
    EXPANSION    → impulse confirmed, ride the premium spike, no trailing
    DISTRIBUTION → deceleration detected, tight dynamic giveback kicks in

Each trade carries its own per-bar state.  All math is pure numpy / scalar
so it integrates cleanly with the existing SimulatorMixin loop.

Key Concepts Used (Institutional)
----------------------------------
1. ATR-normalized velocity   — measures impulse strength, not raw price delta
2. First derivative of ATR ratio (vol acceleration) — detects when vol
   expansion is losing steam before price actually reverses
3. State machine per trade   — avoids early exit in trending markets
4. Convex giveback formula   — giveback widens with profit, protecting gains
   proportionally rather than using a fixed point offset
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ─────────────────────────────────────────────────────────────────
# PHASE CONSTANTS
# ─────────────────────────────────────────────────────────────────
PHASE_EARLY = 0        # Noise zone — only hard stop active
PHASE_EXPANSION = 1    # Impulse confirmed — ride the premium, no giveback
PHASE_DISTRIBUTION = 2  # Deceleration — tight dynamic trailing kicks in

PhaseType = Literal[0, 1, 2]


# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────
@dataclass
class HybridExitConfig:
    # ── Expansion unlock thresholds ──────────────────────────────
    adx_unlock_threshold: float = 28.0        # ADX must exceed this
    atr_ratio_unlock_threshold: float = 1.15  # ATR / rolling_ATR must exceed this
    adx_rising_bars: int = 2                  # ADX must have been rising for N bars
    min_profit_unlock: float = 0.0            # Optional: min profit before unlocking (0 = disabled)

    # ── Rolling ATR window for normalization ─────────────────────
    rolling_atr_window: int = 20              # used to normalize ATR into a ratio

    # ── Momentum velocity detection ──────────────────────────────
    velocity_window: int = 3                  # bars for price delta calculation
    velocity_threshold: float = 1.5           # min velocity to classify as "strong impulse"
    velocity_collapse_ratio: float = 0.5      # if velocity drops below prev * this → collapse

    # ── Vol acceleration collapse ─────────────────────────────────
    vol_lookback_positive: int = 3            # N prior positive slopes before collapse check

    # ── Distribution / full breakdown ───────────────────────────
    adx_breakdown_lookback: int = 10          # ADX must fall below rolling min of this period
    atr_breakdown_ratio: float = 0.90         # ATR < peak_ATR * this → structural breakdown
    ema_breakdown_crosses: bool = True        # price crosses EMA51 opposite side → hard exit

    # ── Extreme extension exit (mean reversion risk) ─────────────
    extreme_extension_atr_multiple: float = 3.0   # abs(close - ema51) / ATR > this → dist

    # ── Dynamic giveback formula ─────────────────────────────────
    # giveback = max(base_giveback_pct * entry_price, profit_ratio * peak_profit, atr_multiple * ATR)
    base_giveback_pct: float = 0.003          # 0.3% of entry price as floor giveback
    profit_giveback_ratio: float = 0.30       # 30% of peak profit
    atr_giveback_multiple: float = 1.2        # 1.2x ATR as alternative floor


# ─────────────────────────────────────────────────────────────────
# PER-TRADE STATE  (attach to active_trade dict in simulator)
# ─────────────────────────────────────────────────────────────────
@dataclass
class HybridExitState:
    phase: PhaseType = PHASE_EARLY
    peak_profit: float = 0.0
    peak_atr: float = 0.0

    # rolling history (last N values stored as small deques)
    adx_history: list = field(default_factory=list)        # last ~12 ADX values
    atr_ratio_history: list = field(default_factory=list)  # last ~5 ATR ratio values
    velocity_history: list = field(default_factory=list)   # last ~3 velocity values

    def to_dict(self) -> dict:
        return {
            "hybrid_phase": self.phase,
            "hybrid_peak_profit": self.peak_profit,
            "hybrid_peak_atr": self.peak_atr,
            "hybrid_adx_history": self.adx_history,
            "hybrid_atr_ratio_history": self.atr_ratio_history,
            "hybrid_velocity_history": self.velocity_history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HybridExitState":
        s = cls()
        s.phase = d.get("hybrid_phase", PHASE_EARLY)
        s.peak_profit = d.get("hybrid_peak_profit", 0.0)
        s.peak_atr = d.get("hybrid_peak_atr", 0.0)
        s.adx_history = d.get("hybrid_adx_history", [])
        s.atr_ratio_history = d.get("hybrid_atr_ratio_history", [])
        s.velocity_history = d.get("hybrid_velocity_history", [])
        return s


# ─────────────────────────────────────────────────────────────────
# SCALAR HELPERS  (operate on single bar values, not full arrays)
# These are called inside the simulator's per-bar loop.
# ─────────────────────────────────────────────────────────────────

def _compute_atr_ratio(atr: float, rolling_atr: float) -> float:
    """V_t = ATR_t / rolling_ATR_t  — vol expansion factor."""
    return atr / max(rolling_atr, 1e-9)


def _adx_is_rising(adx_history: list, n: int) -> bool:
    """True if the last n ADX values were strictly increasing."""
    if len(adx_history) < n + 1:
        return False
    recent = adx_history[-(n + 1):]
    return all(recent[i] < recent[i + 1] for i in range(n))


def _vol_acceleration_collapse(atr_ratio_history: list, lookback_positive: int) -> bool:
    """
    Detect when vol was accelerating (positive slope for N bars) but just turned
    negative.  This is the earliest signal that the premium spike is topping.
    """
    if len(atr_ratio_history) < lookback_positive + 2:
        return False
    # slopes = first differences
    vals = atr_ratio_history[-(lookback_positive + 2):]
    slopes = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    prev_slopes = slopes[:-1]
    curr_slope = slopes[-1]
    return all(s > 0 for s in prev_slopes) and curr_slope < 0


def _velocity_collapse(velocity_history: list, threshold: float, collapse_ratio: float) -> bool:
    """
    Velocity = (close_t - close_{t-n}) / ATR_t
    Collapse = prev velocity was strong impulse, current dropped sharply.
    Catches impulse death before price actually reverses.
    """
    if len(velocity_history) < 2:
        return False
    v_prev = velocity_history[-2]
    v_now = velocity_history[-1]
    return abs(v_prev) > threshold and abs(v_now) < abs(v_prev) * collapse_ratio


def _extreme_extension(close: float, ema51: float, atr: float, multiple: float) -> bool:
    """Price stretched too far from mean — mean reversion probability spikes."""
    if atr <= 0:
        return False
    return abs(close - ema51) / atr > multiple


def _structural_breakdown(
    adx_history: list,
    atr: float,
    peak_atr: float,
    adx_breakdown_lookback: int,
    atr_breakdown_ratio: float,
) -> bool:
    """
    Late-stage collapse: ADX fell below its recent low AND ATR contracted.
    Institutional signal: trend exhaustion, not just deceleration.
    """
    if len(adx_history) < adx_breakdown_lookback:
        return False
    adx_now = adx_history[-1]
    adx_min_lookback = min(adx_history[-adx_breakdown_lookback:])
    atr_collapsed = atr < peak_atr * atr_breakdown_ratio
    adx_below_range = adx_now < adx_min_lookback * 1.02  # slight tolerance
    return adx_below_range and atr_collapsed


def _compute_dynamic_giveback(
    config: HybridExitConfig,
    peak_profit: float,
    entry_price: float,
    atr: float,
) -> float:
    """
    Convex giveback formula — three-way max:
      1. Base floor: prevents exiting on microvolatility
      2. Profit ratio: scales with gains (protects convex premium captures)
      3. ATR floor: market-adaptive minimum
    """
    base = config.base_giveback_pct * entry_price
    profit_based = config.profit_giveback_ratio * peak_profit
    atr_based = config.atr_giveback_multiple * atr
    return max(base, profit_based, atr_based)


# ─────────────────────────────────────────────────────────────────
# MAIN ENGINE — called once per bar inside the simulator loop
# ─────────────────────────────────────────────────────────────────

class HybridExitEngine:
    """
    Drop-in replacement / supplement to the flat giveback exit in simulator.py.

    Usage inside per-bar loop
    -------------------------
        engine = HybridExitEngine(config)          # created once outside loop
        # on trade open:
        active_trade["hybrid_state"] = HybridExitState()
        # each bar:
        decision = engine.evaluate(
            state    = HybridExitState.from_dict(active_trade),
            ...bar values...
        )
        active_trade.update(decision.updated_state.to_dict())
        if decision.exit_now:
            _close_trade(idx, reason=decision.exit_reason)
    """

    def __init__(self, config: HybridExitConfig | None = None):
        self.config = config or HybridExitConfig()

    # ── Rolling ATR window (simple, maintained externally per-trade) ──
    # We compute rolling_atr inline from the per-trade ATR history.

    @staticmethod
    def _rolling_mean(history: list, window: int) -> float:
        if not history:
            return 1e-9
        window_vals = history[-window:]
        return sum(window_vals) / len(window_vals)

    def evaluate(
        self,
        state: HybridExitState,
        favorable_move: float,     # current profit in underlying points (positive = in profit)
        entry_price: float,
        close: float,
        ema51: float,
        atr: float,
        adx: float,
        signal_side: str,          # "long" or "short"
        price_close_prev: float | None = None,
        velocity_window_close: list | None = None,  # last N close prices for velocity calc
    ) -> "HybridExitDecision":
        """
        Evaluate exit decision for one bar.
        Returns HybridExitDecision with exit_now, exit_reason, updated_state.
        """
        cfg = self.config
        s = state  # alias

        # ── Update rolling ADX history ────────────────────────────────────
        s.adx_history.append(adx)
        max_adx_hist = max(cfg.adx_rising_bars + 3, cfg.adx_breakdown_lookback + 2)
        if len(s.adx_history) > max_adx_hist:
            s.adx_history.pop(0)

        # ── Update raw ATR history (used to compute rolling ATR and ratios) ─
        # atr_ratio_history stores RAW ATR values (not ratios — the name is legacy).
        s.atr_ratio_history.append(atr)
        if len(s.atr_ratio_history) > cfg.rolling_atr_window + 8:
            s.atr_ratio_history.pop(0)

        # Rolling ATR baseline and current ratio
        rolling_atr_val = self._rolling_mean(s.atr_ratio_history, cfg.rolling_atr_window)
        atr_ratio = _compute_atr_ratio(atr, rolling_atr_val)

        # ── Build ATR-ratio series for vol acceleration detection ────────
        # Compute a ratio value for each raw ATR in history using a rolling
        # baseline.  Done purely from stored history — no out-of-bounds risk.
        raw_atrs = s.atr_ratio_history
        ratio_series = []
        for i in range(len(raw_atrs)):
            window_slice = raw_atrs[max(0, i - cfg.rolling_atr_window + 1): i + 1]
            rm = sum(window_slice) / len(window_slice) if window_slice else 1e-9
            ratio_series.append(raw_atrs[i] / max(rm, 1e-9))

        # ── Velocity (ATR-normalised signed impulse) ─────────────────────
        if velocity_window_close and len(velocity_window_close) >= cfg.velocity_window:
            price_delta = velocity_window_close[-1] - velocity_window_close[-cfg.velocity_window]
            velocity = price_delta / max(atr, 1e-9)
            if signal_side == "short":
                velocity = -velocity
        else:
            velocity = 0.0
        s.velocity_history.append(velocity)
        if len(s.velocity_history) > 8:
            s.velocity_history.pop(0)

        # ── Update peak profit & peak ATR ─────────────────────────────────
        s.peak_profit = max(s.peak_profit, favorable_move)
        if atr > s.peak_atr:
            s.peak_atr = atr

        # ── Phase transitions ──────────────────────────────────────────────
        exit_now = False
        exit_reason = "none"

        if s.phase == PHASE_EARLY:
            adx_rising = _adx_is_rising(s.adx_history, cfg.adx_rising_bars)
            profit_ok = favorable_move >= cfg.min_profit_unlock
            if (
                adx > cfg.adx_unlock_threshold
                and atr_ratio > cfg.atr_ratio_unlock_threshold
                and adx_rising
                and profit_ok
            ):
                s.phase = PHASE_EXPANSION

        elif s.phase == PHASE_EXPANSION:
            # Trigger 1: ADX slope turns negative for N bars
            adx_slope_negative = (
                len(s.adx_history) >= cfg.adx_rising_bars + 1
                and all(
                    s.adx_history[-(j + 1)] < s.adx_history[-(j + 2)]
                    for j in range(cfg.adx_rising_bars)
                )
            )
            # Trigger 2: Vol acceleration collapse (first derivative of ratio turns negative)
            vol_collapse = _vol_acceleration_collapse(
                ratio_series,
                min(cfg.vol_lookback_positive, max(0, len(ratio_series) - 2))
            )
            # Trigger 3: Velocity impulse death
            vel_collapse = _velocity_collapse(
                s.velocity_history, cfg.velocity_threshold, cfg.velocity_collapse_ratio
            )
            # Trigger 4: Extreme extension — mean reversion risk
            extreme_ext = _extreme_extension(close, ema51, atr, cfg.extreme_extension_atr_multiple)

            if adx_slope_negative or vol_collapse or vel_collapse or extreme_ext:
                s.phase = PHASE_DISTRIBUTION

        elif s.phase == PHASE_DISTRIBUTION:
            # Full structural breakdown → immediate exit, no giveback wait
            structural = _structural_breakdown(
                s.adx_history, atr, s.peak_atr,
                cfg.adx_breakdown_lookback, cfg.atr_breakdown_ratio
            )
            ema_cross_collapse = (
                cfg.ema_breakdown_crosses
                and ema51 > 0
                and (
                    (signal_side == "long" and close < ema51)
                    or (signal_side == "short" and close > ema51)
                )
            )
            if structural or ema_cross_collapse:
                exit_now = True
                exit_reason = "hybrid_structural_breakdown"
            else:
                giveback_threshold = _compute_dynamic_giveback(
                    cfg, s.peak_profit, entry_price, atr
                )
                pullback = s.peak_profit - favorable_move
                if pullback >= giveback_threshold:
                    exit_now = True
                    exit_reason = "hybrid_dynamic_giveback"

        return HybridExitDecision(
            exit_now=exit_now,
            exit_reason=exit_reason,
            updated_state=s,
            phase=s.phase,
            peak_profit=s.peak_profit,
        )


@dataclass
class HybridExitDecision:
    exit_now: bool
    exit_reason: str
    updated_state: HybridExitState
    phase: PhaseType
    peak_profit: float

    @property
    def phase_name(self) -> str:
        return {PHASE_EARLY: "EARLY", PHASE_EXPANSION: "EXPANSION", PHASE_DISTRIBUTION: "DISTRIBUTION"}.get(self.phase, "?")


# ─────────────────────────────────────────────────────────────────
# CONVENIENCE: get default engine instance
# ─────────────────────────────────────────────────────────────────
def create_default_engine() -> HybridExitEngine:
    """Returns engine tuned for Nifty/BankNifty 1m options scalping."""
    return HybridExitEngine(HybridExitConfig(
        adx_unlock_threshold=28.0,
        atr_ratio_unlock_threshold=1.15,
        adx_rising_bars=2,
        rolling_atr_window=20,
        velocity_window=3,
        velocity_threshold=1.5,
        velocity_collapse_ratio=0.5,
        vol_lookback_positive=3,
        extreme_extension_atr_multiple=3.0,
        adx_breakdown_lookback=10,
        atr_breakdown_ratio=0.90,
        base_giveback_pct=0.003,
        profit_giveback_ratio=0.30,
        atr_giveback_multiple=1.2,
    ))