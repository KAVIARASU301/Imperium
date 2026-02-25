from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass
class GovernanceDecision:
    confidence: float
    regime: str
    enabled: bool
    reasons: list[str]
    deploy_mode: str
    can_trade_live: bool
    drift_score: float
    health_score: float


class SignalGovernance:
    """Lightweight institutional-style guardrails for strategy signals."""

    STRATEGIES = ("atr_reversal", "atr_divergence", "ema_cross", "range_breakout", "cvd_range_breakout", "open_drive")

    def __init__(self):
        self.deploy_mode = "canary"  # shadow | canary | live
        self.canary_live_ratio = 0.25
        self.min_confidence_for_live = 0.55
        self.health_alert_threshold = 0.40
        self.feature_window = 120
        self.feature_baseline_window = 480
        self._feature_history: deque[np.ndarray] = deque(maxlen=self.feature_baseline_window)
        self._health_by_strategy = {name: deque(maxlen=80) for name in self.STRATEGIES}
        self._bar_counter = 0

        self._base_strategy_weights = {
            "atr_reversal": 0.24,
            "atr_divergence": 0.20,
            "ema_cross": 0.26,
            "range_breakout": 0.20,
            "cvd_range_breakout": 0.06,
            "open_drive": 0.04,
        }
        self.strategy_weights = dict(self._base_strategy_weights)
        self.strategy_weight_window_days = 5
        self.strategy_weight_decay_lambda = 0.9
        self.strategy_weight_bars_per_day = 390
        self._strategy_weight_floor = 0.05
        self._strategy_edge_history = {
            name: deque(maxlen=self.strategy_weight_window_days * self.strategy_weight_bars_per_day)
            for name in self.STRATEGIES
        }
        self._last_edge_idx_by_strategy = {name: -1 for name in self.STRATEGIES}
        self._regime_snapshot = None  # MarketRegime from RegimeEngine, set each redraw
        self.regime_strategy_matrix = {
            "trend": {"atr_reversal": True, "atr_divergence": True, "ema_cross": True, "range_breakout": True, "cvd_range_breakout": True, "open_drive": True},
            "chop": {"atr_reversal": True, "atr_divergence": False, "ema_cross": False, "range_breakout": False, "cvd_range_breakout": True, "open_drive": True},
            "high_vol": {"atr_reversal": True, "atr_divergence": True, "ema_cross": True, "range_breakout": False, "cvd_range_breakout": False, "open_drive": True},
        }

    @staticmethod
    def _safe_mean(values: Iterable[float], fallback: float = 0.0) -> float:
        arr = np.asarray(list(values), dtype=float)
        if arr.size == 0:
            return fallback
        return float(np.nanmean(arr))

    def classify_regime(self, price: np.ndarray, ema10: np.ndarray, ema51: np.ndarray, atr: np.ndarray) -> str:
        if len(price) < 20:
            return "trend"

        idx = len(price) - 1
        slope_10 = ema10[idx] - ema10[max(0, idx - 5)]
        slope_51 = ema51[idx] - ema51[max(0, idx - 8)]
        spread = abs(ema10[idx] - ema51[idx]) / max(abs(ema51[idx]), 1e-9)

        recent_atr = self._safe_mean(atr[max(0, idx - 10): idx + 1], fallback=0.0)
        rolling_atr = self._safe_mean(atr[max(0, idx - 60): idx + 1], fallback=recent_atr)
        atr_ratio = recent_atr / max(rolling_atr, 1e-9)

        if atr_ratio > 1.35:
            return "high_vol"
        if abs(slope_10) < 0.08 and abs(slope_51) < 0.06 and spread < 0.0025:
            return "chop"
        return "trend"

    def _walk_forward_stability(self, realized_edge: dict[str, np.ndarray]) -> dict[str, float]:
        stability: dict[str, float] = {}
        for strategy, edges in realized_edge.items():
            arr = np.asarray(edges, dtype=float)
            arr = arr[~np.isnan(arr)]
            if arr.size < 40:
                stability[strategy] = 0.5
                continue

            fold_size = max(12, arr.size // 5)
            fold_scores = []
            for start in range(0, arr.size - fold_size + 1, fold_size):
                fold = arr[start:start + fold_size]
                fold_scores.append(float(np.nanmean(fold)))

            if not fold_scores:
                stability[strategy] = 0.5
                continue

            mean_edge = abs(float(np.nanmean(fold_scores)))
            std_edge = float(np.nanstd(fold_scores))
            stability[strategy] = float(np.clip(mean_edge / max(std_edge + 1e-6, 0.05), 0.0, 1.0))
        return stability

    def _compute_feature_drift(self, feature_vector: np.ndarray) -> float:
        vec = np.asarray(feature_vector, dtype=float)
        self._feature_history.append(vec)

        if len(self._feature_history) < max(50, self.feature_window):
            return 0.0

        hist = np.asarray(self._feature_history)
        cur = hist[-self.feature_window:]
        base_end = max(1, len(hist) - self.feature_window)
        base = hist[max(0, base_end - self.feature_window):base_end]

        if base.size == 0:
            return 0.0

        cur_mean = np.nanmean(cur, axis=0)
        base_mean = np.nanmean(base, axis=0)
        base_std = np.nanstd(base, axis=0) + 1e-6
        z = np.abs(cur_mean - base_mean) / base_std
        return float(np.clip(np.nanmean(z) / 3.0, 0.0, 1.0))

    def _update_strategy_health(self, strategy: str, signed_return: float):
        if strategy not in self._health_by_strategy:
            return
        self._health_by_strategy[strategy].append(float(signed_return))

    def _update_online_strategy_weights(self, realized_edge: dict[str, np.ndarray], closed_idx: int) -> None:
        win_scores: dict[str, float] = {}
        for strategy in self.STRATEGIES:
            strategy_edge = realized_edge.get(strategy)
            if strategy_edge is not None and closed_idx < len(strategy_edge) and closed_idx > self._last_edge_idx_by_strategy[strategy]:
                edge = float(strategy_edge[closed_idx])
                if not np.isnan(edge):
                    self._strategy_edge_history[strategy].append(edge)
                    self._last_edge_idx_by_strategy[strategy] = closed_idx

            history = self._strategy_edge_history[strategy]
            if not history:
                win_scores[strategy] = 0.5
                continue

            outcomes = (np.asarray(history, dtype=float) > 0.0).astype(float)
            age = np.arange(len(outcomes) - 1, -1, -1, dtype=float)
            decay_weights = np.power(self.strategy_weight_decay_lambda, age)
            weighted_win_rate = float(np.dot(outcomes, decay_weights) / max(np.sum(decay_weights), 1e-9))
            win_scores[strategy] = max(self._strategy_weight_floor, weighted_win_rate)

        raw_sum = sum(win_scores.values())
        if raw_sum <= 0.0:
            return

        self.strategy_weights = {
            strategy: win_scores[strategy] / raw_sum
            for strategy in self.STRATEGIES
        }

    def _health_score(self, strategy: str) -> float:
        history = self._health_by_strategy.get(strategy)
        if not history:
            return 0.5
        arr = np.asarray(history, dtype=float)
        if arr.size < 8:
            return 0.55
        win_rate = float(np.mean(arr > 0))
        edge = float(np.mean(arr))
        score = 0.65 * win_rate + 0.35 * np.clip((edge + 0.003) / 0.006, 0.0, 1.0)
        return float(np.clip(score, 0.0, 1.0))

    def set_current_regime(self, regime) -> None:
        """Called from the dialog on every chart redraw with the latest MarketRegime snapshot."""
        self._regime_snapshot = regime

    def fuse_signal(
        self,
        strategy_type: str,
        side: str,
        strategy_masks: dict,
        closed_idx: int,
        price_close: np.ndarray,
        ema10: np.ndarray,
        ema51: np.ndarray,
        atr: np.ndarray,
        cvd_close: np.ndarray,
        cvd_ema10: np.ndarray,
    ) -> GovernanceDecision:
        self._bar_counter += 1
        reasons: list[str] = []

        # ── Regime engine gate (highest priority, evaluated first) ────────────
        regime_confidence_mult = 1.0
        if self._regime_snapshot is not None:
            allowed = self._regime_snapshot.allowed_strategies
            if not allowed.get(strategy_type, True):
                enabled_flag = False
                reasons.append(f"regime_blocked:{strategy_type}")
            else:
                enabled_flag = None  # will be set by legacy matrix below
            regime_confidence_mult = getattr(self._regime_snapshot, "confidence_multiplier", 1.0)
        else:
            enabled_flag = None

        regime = self.classify_regime(price_close, ema10, ema51, atr)
        legacy_enabled = self.regime_strategy_matrix.get(regime, {}).get(strategy_type, True)
        enabled = enabled_flag if enabled_flag is not None else legacy_enabled
        if not legacy_enabled and enabled_flag is None:
            reasons.append(f"regime_block:{regime}")

        realized_edge = self._build_realized_edge_series(strategy_masks, price_close)
        self._update_online_strategy_weights(realized_edge, closed_idx)

        agreement_score = 0.0
        total_weight = 0.0
        for name, weight in self.strategy_weights.items():
            mask = strategy_masks.get(side, {}).get(name)
            if mask is None or closed_idx >= len(mask):
                continue
            total_weight += weight
            if bool(mask[closed_idx]):
                agreement_score += weight
        agreement_score = agreement_score / max(total_weight, 1e-9)

        feature_vector = np.array([
            (price_close[closed_idx] - ema10[closed_idx]) / max(abs(ema10[closed_idx]), 1e-9),
            (price_close[closed_idx] - ema51[closed_idx]) / max(abs(ema51[closed_idx]), 1e-9),
            (cvd_close[closed_idx] - cvd_ema10[closed_idx]) / max(abs(cvd_ema10[closed_idx]), 1.0),
            atr[closed_idx] / max(abs(price_close[closed_idx]), 1e-9),
        ], dtype=float)

        drift_score = self._compute_feature_drift(feature_vector)
        if drift_score > 0.7:
            reasons.append("feature_drift_high")

        stability = self._walk_forward_stability(realized_edge).get(strategy_type, 0.5)
        if stability < 0.35:
            reasons.append("parameter_stability_low")

        signed_return = self._latest_signed_return(side, price_close, closed_idx)
        self._update_strategy_health(strategy_type, signed_return)
        health_score = self._health_score(strategy_type)
        if health_score < self.health_alert_threshold:
            reasons.append("strategy_health_degraded")

        confidence = (
            0.36 * agreement_score
            + 0.22 * stability
            + 0.22 * health_score
            + 0.20 * (1.0 - drift_score)
        )
        confidence = float(np.clip(confidence * regime_confidence_mult, 0.0, 1.0))

        if confidence < self.min_confidence_for_live:
            reasons.append("low_confidence")

        can_trade_live = enabled and confidence >= self.min_confidence_for_live

        if self.deploy_mode == "shadow":
            can_trade_live = False
            reasons.append("shadow_mode")
        elif self.deploy_mode == "canary":
            bucket = (self._bar_counter % 100) / 100.0
            if bucket > self.canary_live_ratio:
                can_trade_live = False
                reasons.append("canary_holdout")

        return GovernanceDecision(
            confidence=confidence,
            regime=regime,
            enabled=enabled,
            reasons=reasons,
            deploy_mode=self.deploy_mode,
            can_trade_live=can_trade_live,
            drift_score=drift_score,
            health_score=health_score,
        )

    @staticmethod
    def _latest_signed_return(side: str, price: np.ndarray, idx: int) -> float:
        if idx <= 0 or idx >= len(price):
            return 0.0
        delta = (price[idx] - price[idx - 1]) / max(abs(price[idx - 1]), 1e-9)
        return float(delta if side == "long" else -delta)

    @staticmethod
    def _build_realized_edge_series(strategy_masks: dict, price_close: np.ndarray) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        n = len(price_close)
        one_bar_returns = np.zeros(n, dtype=float)
        if n > 1:
            one_bar_returns[1:] = (price_close[1:] - price_close[:-1]) / np.maximum(np.abs(price_close[:-1]), 1e-9)

        for strategy in SignalGovernance.STRATEGIES:
            short_mask = strategy_masks.get("short", {}).get(strategy)
            long_mask = strategy_masks.get("long", {}).get(strategy)
            if short_mask is None or long_mask is None:
                out[strategy] = np.array([], dtype=float)
                continue

            short_arr = np.asarray(short_mask, dtype=bool)
            long_arr = np.asarray(long_mask, dtype=bool)
            min_len = min(len(short_arr), len(long_arr), n)
            if min_len == 0:
                out[strategy] = np.array([], dtype=float)
                continue

            dir_arr = np.zeros(min_len, dtype=float)
            dir_arr[long_arr[:min_len]] = 1.0
            dir_arr[short_arr[:min_len]] = -1.0
            out[strategy] = dir_arr * one_bar_returns[:min_len]
        return out
