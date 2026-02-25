"""
Regime Engine — Market & Volatility Regime Classifier
======================================================
Classifies the current market into trend + volatility + session regimes
and returns a MarketRegime snapshot that SignalGovernance and the exit
manager can consume.

Design principles
-----------------
- Zero new data dependencies: reuses ADX / ATR arrays already computed
  by StrategySignalDetector.
- Stateless per-call classification (classify()) plus a light stateful
  confirmation buffer (_trend_confirm / _vol_confirm) so a single spike
  bar cannot flip the regime.
- All thresholds are user-configurable via RegimeConfig; defaults are
  safe starting values tuned for Nifty / BankNifty 1-min data.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from typing import Optional

import numpy as np


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RegimeConfig:
    """All user-tunable thresholds for the regime engine."""

    # ── Trend thresholds ──────────────────────────────────────────────────────
    adx_strong_trend: float = 28.0      # ADX above this → STRONG_TREND
    adx_weak_trend: float = 20.0        # ADX 20-28 → WEAK_TREND
    # below adx_weak_trend                              → CHOP
    adx_confirmation_bars: int = 3      # consecutive bars required to confirm regime change

    # ── Volatility thresholds ─────────────────────────────────────────────────
    atr_rolling_window: int = 30        # bars for rolling ATR baseline (session bars)
    atr_high_vol_ratio: float = 1.5     # current/rolling > this → HIGH_VOL
    atr_low_vol_ratio: float = 0.70     # current/rolling < this → LOW_VOL
    # between low and high                              → NORMAL_VOL
    vol_confirmation_bars: int = 2      # consecutive bars to confirm vol change

    # ── Session phase times (IST, 24h) ───────────────────────────────────────
    open_drive_end: dtime = field(default_factory=lambda: dtime(9, 30))
    morning_end: dtime = field(default_factory=lambda: dtime(11, 30))
    midday_end: dtime = field(default_factory=lambda: dtime(13, 30))
    afternoon_end: dtime = field(default_factory=lambda: dtime(15, 0))
    pre_close_end: dtime = field(default_factory=lambda: dtime(15, 30))

    # ── Strategy enable matrix (regime → strategy → bool) ────────────────────
    # Keyed by (trend_regime, vol_regime); strategies not listed default True.
    strategy_matrix: dict = field(default_factory=lambda: {
        # STRONG trend: disable reversal (fighting trend), keep everything else
        ("STRONG_TREND", "NORMAL_VOL"): {
            "atr_reversal": False, "atr_divergence": True,
            "ema_cross": True,     "range_breakout": True,
            "cvd_range_breakout": True, "open_drive": True,
        },
        ("STRONG_TREND", "HIGH_VOL"): {
            "atr_reversal": False, "atr_divergence": True,
            "ema_cross": True,     "range_breakout": False,
            "cvd_range_breakout": False, "open_drive": True,
        },
        ("STRONG_TREND", "LOW_VOL"): {
            "atr_reversal": False, "atr_divergence": True,
            "ema_cross": True,     "range_breakout": True,
            "cvd_range_breakout": False, "open_drive": True,
        },
        # WEAK trend: all on but reduced confidence
        ("WEAK_TREND", "NORMAL_VOL"): {
            "atr_reversal": True, "atr_divergence": True,
            "ema_cross": True,    "range_breakout": True,
            "cvd_range_breakout": True, "open_drive": True,
        },
        ("WEAK_TREND", "HIGH_VOL"): {
            "atr_reversal": True,  "atr_divergence": True,
            "ema_cross": True,     "range_breakout": False,
            "cvd_range_breakout": False, "open_drive": True,
        },
        ("WEAK_TREND", "LOW_VOL"): {
            "atr_reversal": True,  "atr_divergence": False,
            "ema_cross": False,    "range_breakout": False,
            "cvd_range_breakout": False, "open_drive": True,
        },
        # CHOP: reversal only, breakout/cross disabled
        ("CHOP", "NORMAL_VOL"): {
            "atr_reversal": True,  "atr_divergence": False,
            "ema_cross": False,    "range_breakout": False,
            "cvd_range_breakout": True, "open_drive": True,
        },
        ("CHOP", "HIGH_VOL"): {
            "atr_reversal": True,  "atr_divergence": False,
            "ema_cross": False,    "range_breakout": False,
            "cvd_range_breakout": False, "open_drive": True,
        },
        ("CHOP", "LOW_VOL"): {
            # Low vol chop → nothing fires, too thin
            "atr_reversal": False, "atr_divergence": False,
            "ema_cross": False,    "range_breakout": False,
            "cvd_range_breakout": False, "open_drive": False,
        },
    })


@dataclass
class MarketRegime:
    """Snapshot of the current market regime at one point in time."""
    trend: str          # STRONG_TREND | WEAK_TREND | CHOP
    volatility: str     # HIGH_VOL | NORMAL_VOL | LOW_VOL
    session: str        # OPEN_DRIVE | MORNING | MIDDAY | AFTERNOON | PRE_CLOSE
    adx_value: float
    atr_ratio: float    # current_atr / rolling_atr
    allowed_strategies: dict[str, bool]
    confidence_multiplier: float   # 1.0 = no change, < 1.0 = reduce confidence

    @property
    def label(self) -> str:
        """Short human-readable label for UI display."""
        trend_icons = {
            "STRONG_TREND": "▲▲",
            "WEAK_TREND":   "▲",
            "CHOP":         "↔",
        }
        vol_icons = {
            "HIGH_VOL":    "🔥",
            "NORMAL_VOL":  "●",
            "LOW_VOL":     "❄",
        }
        return f"{trend_icons.get(self.trend, '?')} {self.trend.replace('_', ' ')}  {vol_icons.get(self.volatility, '')} {self.volatility.replace('_', ' ')}  |  {self.session.replace('_', ' ')}"

    @property
    def trend_color(self) -> str:
        return {
            "STRONG_TREND": "#00E676",
            "WEAK_TREND":   "#FFB300",
            "CHOP":         "#FF4D4D",
        }.get(self.trend, "#8A99B3")

    @property
    def vol_color(self) -> str:
        return {
            "HIGH_VOL":   "#FF4D4D",
            "NORMAL_VOL": "#4D9FFF",
            "LOW_VOL":    "#8A99B3",
        }.get(self.volatility, "#8A99B3")


# ══════════════════════════════════════════════════════════════════════════════
# ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class RegimeEngine:
    """
    Classifies market regime from pre-computed indicator arrays.

    Usage (called from StrategySignalDetector / SignalGovernance):
    ---------------------------------------------------------------
        engine = RegimeEngine(config)
        regime = engine.classify(
            adx=adx_array,
            atr=atr_array,
            bar_time=current_bar_time,   # datetime.time or None
        )
        if regime.allowed_strategies.get("ema_cross", True):
            ...
    """

    def __init__(self, config: Optional[RegimeConfig] = None):
        self.config = config or RegimeConfig()

        # Confirmation buffers — prevent flip-flopping on single bars
        self._trend_buffer: deque[str] = deque(maxlen=self.config.adx_confirmation_bars + 2)
        self._vol_buffer:   deque[str] = deque(maxlen=self.config.vol_confirmation_bars + 2)

        # Confirmed (sticky) states
        self._confirmed_trend: str = "WEAK_TREND"
        self._confirmed_vol:   str = "NORMAL_VOL"

        # ATR rolling baseline (session-scoped)
        self._atr_baseline: deque[float] = deque(maxlen=self.config.atr_rolling_window)
        self._last_classified_bar_key = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def classify(
        self,
        adx: np.ndarray,
        atr: np.ndarray,
        bar_time=None,
    ) -> MarketRegime:
        """
        Classify regime at the last bar of the supplied arrays.

        Parameters
        ----------
        adx       : computed ADX array (same length as price)
        atr       : computed ATR array
        bar_time  : datetime.time of the current bar (for session phase)
        """
        if len(adx) == 0 or len(atr) == 0:
            return self._default_regime(bar_time)

        idx = len(adx) - 1
        adx_val = float(adx[idx])
        atr_val = float(atr[idx])

        bar_key = None
        if isinstance(bar_time, datetime):
            bar_key = bar_time.isoformat()
        elif bar_time is not None:
            bar_key = str(bar_time)

        is_new_bar = bar_key is None or bar_key != self._last_classified_bar_key

        # Update rolling ATR baseline only once per closed bar
        if is_new_bar:
            self._atr_baseline.append(atr_val)
        rolling_atr = float(np.mean(self._atr_baseline)) if self._atr_baseline else atr_val
        atr_ratio = atr_val / max(rolling_atr, 1e-9)

        # Raw regime from current bar
        raw_trend = self._classify_trend(adx_val, adx, idx)
        raw_vol   = self._classify_vol(atr_ratio)

        if is_new_bar:
            # Push into confirmation buffers only once per closed bar.
            self._trend_buffer.append(raw_trend)
            self._vol_buffer.append(raw_vol)

            # Confirm only when N consecutive bars agree
            self._confirmed_trend = self._confirm(
                self._trend_buffer,
                self._confirmed_trend,
                self.config.adx_confirmation_bars,
            )
            self._confirmed_vol = self._confirm(
                self._vol_buffer,
                self._confirmed_vol,
                self.config.vol_confirmation_bars,
            )
            self._last_classified_bar_key = bar_key

        session = self._classify_session(bar_time)
        allowed = self._resolve_strategy_matrix(self._confirmed_trend, self._confirmed_vol, session)
        confidence_mult = self._confidence_multiplier(self._confirmed_trend, self._confirmed_vol, session)

        return MarketRegime(
            trend=self._confirmed_trend,
            volatility=self._confirmed_vol,
            session=session,
            adx_value=adx_val,
            atr_ratio=atr_ratio,
            allowed_strategies=allowed,
            confidence_multiplier=confidence_mult,
        )

    def reset_session(self):
        """Call at session start (9:15) to clear ATR baseline and buffers."""
        self._atr_baseline.clear()
        self._trend_buffer.clear()
        self._vol_buffer.clear()
        self._confirmed_trend = "WEAK_TREND"
        self._confirmed_vol   = "NORMAL_VOL"
        self._last_classified_bar_key = None

    def update_config(self, config: RegimeConfig):
        """Hot-swap config from UI settings change."""
        self.config = config
        self._trend_buffer = deque(self._trend_buffer, maxlen=config.adx_confirmation_bars + 2)
        self._vol_buffer   = deque(self._vol_buffer,   maxlen=config.vol_confirmation_bars + 2)
        self._atr_baseline = deque(self._atr_baseline, maxlen=config.atr_rolling_window)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _classify_trend(self, adx_val: float, adx: np.ndarray, idx: int) -> str:
        c = self.config
        if adx_val >= c.adx_strong_trend:
            # Also check slope — ADX must be rising or stable
            adx_slope = adx_val - float(adx[max(0, idx - 3)])
            if adx_slope >= -1.0:   # allow minor slope dip but reject sharp collapse
                return "STRONG_TREND"
            return "WEAK_TREND"
        if adx_val >= c.adx_weak_trend:
            return "WEAK_TREND"
        return "CHOP"

    def _classify_vol(self, atr_ratio: float) -> str:
        c = self.config
        if atr_ratio >= c.atr_high_vol_ratio:
            return "HIGH_VOL"
        if atr_ratio <= c.atr_low_vol_ratio:
            return "LOW_VOL"
        return "NORMAL_VOL"

    def _classify_session(self, bar_time) -> str:
        if bar_time is None:
            return "MORNING"
        c = self.config
        if isinstance(bar_time, dtime):
            t = bar_time
        elif isinstance(bar_time, datetime):
            t = bar_time.time()
        elif hasattr(bar_time, "time"):
            # pandas.Timestamp and similar datetime-like objects
            t = bar_time.time()
        else:
            return "MORNING"

        if t < c.open_drive_end:
            return "OPEN_DRIVE"
        if t < c.morning_end:
            return "MORNING"
        if t < c.midday_end:
            return "MIDDAY"
        if t < c.afternoon_end:
            return "AFTERNOON"
        return "PRE_CLOSE"

    def _resolve_strategy_matrix(
        self,
        trend: str,
        vol: str,
        session: str,
    ) -> dict[str, bool]:
        """
        Resolve which strategies are allowed.
        Session overrides take priority over trend/vol matrix.
        """
        # Session hard overrides
        if session == "OPEN_DRIVE":
            # Only open_drive fires; everything else locked
            return {
                "atr_reversal": False,
                "atr_divergence": False,
                "ema_cross": False,
                "range_breakout": False,
                "open_drive": True,
            }
        if session == "PRE_CLOSE":
            # Reduce to only high-conviction signals
            return {
                "atr_reversal": False,
                "atr_divergence": True,
                "ema_cross": False,
                "range_breakout": False,
                "open_drive": False,
            }

        # Normal session — look up trend/vol matrix
        base = self.config.strategy_matrix.get((trend, vol))
        if base is None:
            # Default: all on for unknown combination
            base = {s: True for s in ("atr_reversal", "atr_divergence", "ema_cross", "range_breakout")}

        result = dict(base)
        result["open_drive"] = False  # never fires outside OPEN_DRIVE session
        return result

    def _confidence_multiplier(self, trend: str, vol: str, session: str) -> float:
        """Return a 0.5–1.0 multiplier applied to governance confidence."""
        mult = 1.0
        if session in ("OPEN_DRIVE", "PRE_CLOSE"):
            mult *= 0.6     # noisiest phases → lower confidence
        if session == "MIDDAY" and vol == "LOW_VOL":
            mult *= 0.75    # thin midday chop → reduce confidence
        if trend == "CHOP" and vol == "HIGH_VOL":
            mult *= 0.8     # volatile chop is dangerous
        return max(0.5, mult)

    @staticmethod
    def _confirm(buf: deque, current: str, n: int) -> str:
        """Return new confirmed state only if last n bars all agree."""
        if len(buf) < n:
            return current
        recent = list(buf)[-n:]
        if len(set(recent)) == 1:
            return recent[0]
        return current

    def _default_regime(self, bar_time) -> MarketRegime:
        session = self._classify_session(bar_time)
        return MarketRegime(
            trend="WEAK_TREND",
            volatility="NORMAL_VOL",
            session=session,
            adx_value=0.0,
            atr_ratio=1.0,
            allowed_strategies={s: True for s in (
                "atr_reversal", "atr_divergence", "ema_cross", "range_breakout", "open_drive"
            )},
            confidence_multiplier=1.0,
        )
