"""
core/auto_trader/signal_quality_scorer.py

Per-signal quality score — evaluates how GOOD a specific signal bar is
RIGHT NOW, not just whether the strategy has worked historically.

This is the "is this signal worth trading?" layer.
The existing SignalGovernance handles "is this strategy healthy overall?"
These two scores multiply together inside fuse_signal() for final confidence.

5 Factors (institutional grade):
  1. ATR Extension Strength   — how extended is price vs EMA, normalized to recent history
  2. CVD Alignment Strength   — how strongly CVD confirms the direction
  3. Trend Strength (ADX)     — ADX as a 0-1 quality multiplier
  4. CVD Slope Momentum       — rate-of-change of CVD in signal direction
  5. Higher-TF Trend Match    — does the 5m parent trend agree?

Usage:
    from core.auto_trader.signal_quality_scorer import SignalQualityScorer

    scorer = SignalQualityScorer()
    score, breakdown = scorer.score(
        idx=closed_idx,
        side="long",         # "long" | "short"
        strategy_type="atr_reversal",
        price_close=price_data,
        ema51=ema51_data,
        atr=atr_values,
        adx=adx_values,
        cvd_close=cvd_data,
        cvd_ema10=cvd_ema10_data,
        cvd_ema51=cvd_ema51_data,
        parent_long_mask=parent_long_mask,   # optional, None = ignored
        parent_short_mask=parent_short_mask, # optional, None = ignored
    )

    if score >= 0.65:
        # fire the signal
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np


# ---------------------------------------------------------------------------
# Factor weights — tune these per your instrument.
# They must sum to 1.0.
# ---------------------------------------------------------------------------
_WEIGHTS = {
    "atr_extension":    0.28,   # how stretched price is from EMA
    "cvd_alignment":    0.27,   # CVD position + direction vs EMAs
    "adx_strength":     0.20,   # trend strength (ADX)
    "cvd_slope":        0.15,   # CVD momentum slope
    "higher_tf_match":  0.10,   # 5m parent timeframe agrees
}

# Minimum score to recommend trading this signal
DEFAULT_MIN_SCORE = 0.65

# Per-strategy score floors/ceilings (some strategies inherently score lower)
_STRATEGY_FLOOR = {
    "atr_reversal":      0.0,
    "atr_divergence":    0.0,
    "ema_cross":         0.0,
    "range_breakout":    0.10,   # breakout gets a slight boost floor
    "open_drive":        0.10,
    "cvd_range_breakout":0.05,
}


@dataclass
class SignalQualityBreakdown:
    """Human-readable breakdown of each factor's contribution."""
    total_score: float
    atr_extension_score: float
    cvd_alignment_score: float
    adx_strength_score: float
    cvd_slope_score: float
    higher_tf_score: float
    meets_threshold: bool
    threshold_used: float
    notes: list[str]

    def __str__(self) -> str:
        lines = [
            f"Signal Quality: {self.total_score:.3f} ({'✅ PASS' if self.meets_threshold else '❌ FAIL'} @ {self.threshold_used:.2f})",
            f"  ATR Extension : {self.atr_extension_score:.3f} (w={_WEIGHTS['atr_extension']:.2f})",
            f"  CVD Alignment : {self.cvd_alignment_score:.3f} (w={_WEIGHTS['cvd_alignment']:.2f})",
            f"  ADX Strength  : {self.adx_strength_score:.3f} (w={_WEIGHTS['adx_strength']:.2f})",
            f"  CVD Slope     : {self.cvd_slope_score:.3f} (w={_WEIGHTS['cvd_slope']:.2f})",
            f"  Higher TF     : {self.higher_tf_score:.3f} (w={_WEIGHTS['higher_tf_match']:.2f})",
        ]
        if self.notes:
            lines.append("  Notes: " + " | ".join(self.notes))
        return "\n".join(lines)


class SignalQualityScorer:
    """
    Computes a 0.0–1.0 quality score for a signal at a specific bar index.

    Designed to be called from SignalGovernance.fuse_signal() just before
    the final can_trade_live decision.

    Integration in signal_governance.py fuse_signal():

        from core.auto_trader.signal_quality_scorer import SignalQualityScorer
        # (add as instance var in __init__)
        self.quality_scorer = SignalQualityScorer()

        # Inside fuse_signal(), before computing final confidence:
        quality_score, quality_breakdown = self.quality_scorer.score(
            idx=closed_idx,
            side=side,
            strategy_type=strategy_type,
            price_close=price_close,
            ema51=ema51,
            atr=atr,
            adx=adx,           # <-- pass this in (add to fuse_signal signature)
            cvd_close=cvd_close,
            cvd_ema10=cvd_ema10,
            cvd_ema51=cvd_ema51,  # <-- pass this in (add to fuse_signal signature)
        )

        # Blend with existing confidence (quality gates final score)
        confidence = confidence * (0.5 + 0.5 * quality_score)
        # ↑ This means: even perfect historical health is halved if current signal quality is 0
        #   and a perfect quality signal (1.0) leaves confidence unchanged.
    """

    # Rolling window for percentile-normalizing ATR extension distance
    ATR_PERCENTILE_WINDOW = 200
    ATR_PERCENTILE_RANK = 90        # we compare current distance to the 90th pct of history

    # CVD slope lookback in bars
    CVD_SLOPE_LOOKBACK = 10

    # ADX thresholds for scoring
    ADX_STRONG_TREND = 35.0         # full score
    ADX_MIN_TREND = 20.0            # score starts here

    def __init__(self, min_score: float = DEFAULT_MIN_SCORE):
        self.min_score = min_score
        # Rolling buffer for ATR extension distances (for percentile normalization)
        self._atr_ext_history: list[float] = []

    def score(
        self,
        idx: int,
        side: str,                        # "long" | "short"
        strategy_type: str,
        price_close: np.ndarray,
        ema51: np.ndarray,
        atr: np.ndarray,
        cvd_close: np.ndarray,
        cvd_ema10: np.ndarray,
        cvd_ema51: np.ndarray,
        adx: np.ndarray | None = None,
        parent_long_mask: np.ndarray | None = None,
        parent_short_mask: np.ndarray | None = None,
        min_score_override: float | None = None,
    ) -> tuple[float, SignalQualityBreakdown]:
        """
        Score the signal at `idx`. Returns (total_score, breakdown).

        total_score is 0.0–1.0. Higher = better quality signal.
        """
        notes: list[str] = []
        n = len(price_close)

        if idx <= 0 or idx >= n:
            bd = SignalQualityBreakdown(
                total_score=0.0,
                atr_extension_score=0.0,
                cvd_alignment_score=0.0,
                adx_strength_score=0.5,
                cvd_slope_score=0.5,
                higher_tf_score=0.5,
                meets_threshold=False,
                threshold_used=min_score_override or self.min_score,
                notes=["invalid_index"],
            )
            return 0.0, bd

        # ── Factor 1: ATR Extension Strength ────────────────────────────────
        # Measures how stretched price is from EMA, normalized to recent history.
        # Fixes the "fixed 3.01x ATR threshold breaks in regime changes" problem.
        atr_ext_score = self._score_atr_extension(idx, price_close, ema51, atr, side, notes)

        # ── Factor 2: CVD Alignment Strength ────────────────────────────────
        # CVD must be on the right side of its EMAs AND the gap should be meaningful.
        cvd_align_score = self._score_cvd_alignment(idx, side, cvd_close, cvd_ema10, cvd_ema51, notes)

        # ── Factor 3: ADX Trend Strength ────────────────────────────────────
        # Higher ADX = stronger trend context = better signal quality.
        adx_score = self._score_adx(idx, adx, strategy_type, notes)

        # ── Factor 4: CVD Slope Momentum ────────────────────────────────────
        # Is CVD accelerating in the signal direction?
        cvd_slope_score = self._score_cvd_slope(idx, side, cvd_close, notes)

        # ── Factor 5: Higher-TF Match ────────────────────────────────────────
        # Does the 5m parent trend agree with this signal direction?
        htf_score = self._score_higher_tf(idx, side, parent_long_mask, parent_short_mask, notes)

        # ── Weighted total ───────────────────────────────────────────────────
        raw_score = (
            _WEIGHTS["atr_extension"]   * atr_ext_score +
            _WEIGHTS["cvd_alignment"]   * cvd_align_score +
            _WEIGHTS["adx_strength"]    * adx_score +
            _WEIGHTS["cvd_slope"]       * cvd_slope_score +
            _WEIGHTS["higher_tf_match"] * htf_score
        )

        # Apply per-strategy floor
        floor = _STRATEGY_FLOOR.get(strategy_type, 0.0)
        total_score = float(np.clip(raw_score + floor, 0.0, 1.0))

        threshold = min_score_override if min_score_override is not None else self.min_score

        breakdown = SignalQualityBreakdown(
            total_score=total_score,
            atr_extension_score=atr_ext_score,
            cvd_alignment_score=cvd_align_score,
            adx_strength_score=adx_score,
            cvd_slope_score=cvd_slope_score,
            higher_tf_score=htf_score,
            meets_threshold=total_score >= threshold,
            threshold_used=threshold,
            notes=notes,
        )
        return total_score, breakdown

    # =========================================================================
    # Private factor scorers
    # =========================================================================

    def _score_atr_extension(
        self,
        idx: int,
        price_close: np.ndarray,
        ema51: np.ndarray,
        atr: np.ndarray,
        side: str,
        notes: list[str],
    ) -> float:
        """
        Percentile-normalized ATR distance.

        Instead of asking "is price > 3x ATR from EMA?" (fixed threshold)
        we ask "is this extension in the top 20% of recent history?"

        This self-calibrates to the current volatility regime.
        """
        atr_val = float(atr[idx]) if idx < len(atr) else 0.0
        if atr_val <= 0 or not np.isfinite(atr_val):
            notes.append("atr_invalid")
            return 0.5  # neutral, not penalize

        ema_val = float(ema51[idx]) if idx < len(ema51) else float(price_close[idx])
        price_val = float(price_close[idx])

        if not np.isfinite(ema_val) or ema_val <= 0:
            notes.append("ema_invalid")
            return 0.5

        # Current distance in ATR multiples
        current_dist = abs(price_val - ema_val) / atr_val

        # Update rolling history
        self._atr_ext_history.append(current_dist)
        if len(self._atr_ext_history) > self.ATR_PERCENTILE_WINDOW:
            self._atr_ext_history.pop(0)

        if len(self._atr_ext_history) < 30:
            # Not enough history yet — fall back to absolute threshold
            score = float(np.clip((current_dist - 1.5) / 3.0, 0.0, 1.0))
            notes.append(f"atr_warmup(dist={current_dist:.2f})")
            return score

        # Compute percentile rank of current distance
        hist = np.array(self._atr_ext_history[:-1])  # exclude current bar
        p90 = np.percentile(hist, self.ATR_PERCENTILE_RANK)

        if p90 <= 0:
            return 0.5

        # Score: 1.0 if at/above 90th percentile, 0.0 if below 50th
        p50 = np.percentile(hist, 50)
        score = float(np.clip((current_dist - p50) / max(p90 - p50, 1e-6), 0.0, 1.0))

        notes.append(f"atr_ext={current_dist:.2f} p90={p90:.2f}")
        return score

    def _score_cvd_alignment(
        self,
        idx: int,
        side: str,
        cvd_close: np.ndarray,
        cvd_ema10: np.ndarray,
        cvd_ema51: np.ndarray,
        notes: list[str],
    ) -> float:
        """
        CVD alignment score. Checks:
          1. Is CVD on the right side of both EMAs? (0.5 points)
          2. How large is the gap between CVD and its EMA51? (0.5 points, normalized)
        """
        if idx >= len(cvd_close) or idx >= len(cvd_ema10) or idx >= len(cvd_ema51):
            notes.append("cvd_oob")
            return 0.5

        cvd_val  = float(cvd_close[idx])
        ema10_val = float(cvd_ema10[idx])
        ema51_val = float(cvd_ema51[idx])

        if not (np.isfinite(cvd_val) and np.isfinite(ema10_val) and np.isfinite(ema51_val)):
            notes.append("cvd_nan")
            return 0.5

        # Direction check: both EMAs on right side
        if side == "long":
            ema_side_score = 0.5 if (cvd_val > ema10_val and cvd_val > ema51_val) else (
                0.25 if (cvd_val > ema51_val) else 0.0
            )
        else:  # short
            ema_side_score = 0.5 if (cvd_val < ema10_val and cvd_val < ema51_val) else (
                0.25 if (cvd_val < ema51_val) else 0.0
            )

        # Gap magnitude: normalize gap to rolling std of CVD values up to idx
        lookback = min(idx, 100)
        if lookback < 10:
            gap_score = 0.25  # not enough history
        else:
            cvd_slice = cvd_close[max(0, idx - lookback): idx]
            cvd_std = float(np.std(cvd_slice))
            if cvd_std <= 0:
                gap_score = 0.25
            else:
                raw_gap = abs(cvd_val - ema51_val)
                # 1 std = 0.25, 2 std = 0.5 (capped)
                gap_score = float(np.clip((raw_gap / cvd_std) * 0.25, 0.0, 0.5))

        total = ema_side_score + gap_score
        notes.append(f"cvd_align={total:.2f}(side={ema_side_score:.2f},gap={gap_score:.2f})")
        return float(np.clip(total, 0.0, 1.0))

    def _score_adx(
        self,
        idx: int,
        adx: np.ndarray | None,
        strategy_type: str,
        notes: list[str],
    ) -> float:
        """
        ADX-based trend strength score.
        - Range-bound strategies (atr_reversal) prefer LOWER ADX (weak trend = good mean reversion)
        - Trend strategies (ema_cross, breakout) prefer HIGHER ADX

        This inverts the score for mean-reversion strategies.
        """
        if adx is None or idx >= len(adx):
            notes.append("adx_missing")
            return 0.5  # neutral

        adx_val = float(adx[idx])
        if not np.isfinite(adx_val) or adx_val < 0:
            return 0.5

        # Normalize ADX: 0 at ADX=0, 1.0 at ADX=35+
        raw_score = float(np.clip(
            (adx_val - self.ADX_MIN_TREND) / (self.ADX_STRONG_TREND - self.ADX_MIN_TREND),
            0.0, 1.0
        ))

        # Mean-reversion strategies work best in weak trends (low ADX)
        is_reversal = strategy_type in ("atr_reversal",)
        score = (1.0 - raw_score) if is_reversal else raw_score

        notes.append(f"adx={adx_val:.1f} score={score:.2f}")
        return score

    def _score_cvd_slope(
        self,
        idx: int,
        side: str,
        cvd_close: np.ndarray,
        notes: list[str],
    ) -> float:
        """
        CVD momentum: rate of change over the last N bars.
        Stronger CVD slope in signal direction = higher score.
        """
        lookback = self.CVD_SLOPE_LOOKBACK
        start = max(0, idx - lookback)

        if idx - start < 3:
            notes.append("cvd_slope_warmup")
            return 0.5

        cvd_start = float(cvd_close[start])
        cvd_now   = float(cvd_close[idx])

        if not (np.isfinite(cvd_start) and np.isfinite(cvd_now)):
            return 0.5

        # Normalize by rolling std to get a z-score-like momentum
        cvd_slice = cvd_close[start: idx + 1]
        cvd_std = float(np.std(cvd_slice))

        if cvd_std <= 0:
            return 0.5

        slope = (cvd_now - cvd_start) / cvd_std  # z-score units

        if side == "long":
            # Positive slope = good for long
            score = float(np.clip((slope + 1.0) / 3.0, 0.0, 1.0))
        else:
            # Negative slope = good for short
            score = float(np.clip((-slope + 1.0) / 3.0, 0.0, 1.0))

        notes.append(f"cvd_slope_z={slope:.2f} score={score:.2f}")
        return score

    def _score_higher_tf(
        self,
        idx: int,
        side: str,
        parent_long_mask: np.ndarray | None,
        parent_short_mask: np.ndarray | None,
        notes: list[str],
    ) -> float:
        """
        Higher-timeframe agreement.
        - Full score (1.0): parent TF matches signal direction
        - Half score (0.5): no parent mask provided (no penalty, no bonus)
        - Zero score (0.0): parent TF opposes signal direction
        """
        if parent_long_mask is None or parent_short_mask is None:
            notes.append("htf_unavailable")
            return 0.5  # neutral — don't penalize if not available

        long_ok  = idx < len(parent_long_mask)  and bool(parent_long_mask[idx])
        short_ok = idx < len(parent_short_mask) and bool(parent_short_mask[idx])

        if side == "long":
            if long_ok:
                notes.append("htf=✅_long")
                return 1.0
            elif short_ok:
                notes.append("htf=❌_long_vs_short")
                return 0.0
            else:
                notes.append("htf=neutral")
                return 0.4

        else:  # short
            if short_ok:
                notes.append("htf=✅_short")
                return 1.0
            elif long_ok:
                notes.append("htf=❌_short_vs_long")
                return 0.0
            else:
                notes.append("htf=neutral")
                return 0.4