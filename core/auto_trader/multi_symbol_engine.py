"""
multi_symbol_engine.py
======================
Institutional Actor-Model orchestrator for multi-symbol ATR reversal scanning.

Each symbol gets its own isolated QThread worker.
The engine is a thin coordinator: it spins up workers, collects signals,
and emits them to the UI. Zero UI logic lives here.

Architecture concept: "Shared-Nothing Workers"
  - Each SymbolWorker owns its own DataWorker, StrategySignalDetector,
    SignalGovernance, and ChopFilter instances.
  - They NEVER share state. The orchestrator is only a signal bus.
  - This is how prop desks run strategy pods: each pod is independent,
    the desk just aggregates P&L (or in our case, signals).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from core.auto_trader.chop_filter import ChopFilter
from core.auto_trader.constants import TRADING_START, TRADING_END
from core.auto_trader.indicators import (
    calculate_atr,
    calculate_ema,
    calculate_vwap,
    compute_adx,
    calculate_cvd_zscore,
    is_chop_regime,
)
from core.auto_trader.signal_governance import SignalGovernance
from core.auto_trader.strategy_signal_detector import StrategySignalDetector
from core.auto_trader.data_worker import _DataFetchWorker, build_price_cvd_from_ticks

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Signal payload — everything the UI needs to display one alert
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AtrSignalEvent:
    """Fired when ATR reversal confirms on a symbol."""
    symbol: str
    instrument_token: int
    side: str                        # "long" | "short"
    price: float
    atr: float
    adx: float
    confidence: float                # 0.0–1.0 from SignalGovernance
    quality_score: float
    chop_filtered: bool
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def direction_label(self) -> str:
        return "▲ LONG" if self.side == "long" else "▼ SHORT"

    @property
    def confidence_pct(self) -> str:
        return f"{self.confidence * 100:.0f}%"


# ─────────────────────────────────────────────────────────────────────────────
# Per-symbol worker — runs in its own QThread
# ─────────────────────────────────────────────────────────────────────────────

class SymbolWorker(QObject):
    """
    Isolated per-symbol scanner.

    Fetches OHLCV + CVD data, recomputes indicators on every refresh tick,
    runs ATR reversal detection, and emits signal_fired when a new signal
    appears that wasn't present last bar.

    Institutional pattern: each worker is stateless between runs except for
    _last_signal_bar to prevent re-firing the same bar's signal repeatedly.
    """

    signal_fired = Signal(object)   # AtrSignalEvent
    status_update = Signal(str, str)  # symbol, status message
    error_occurred = Signal(str, str)  # symbol, error message

    # How many minutes of data to fetch
    TIMEFRAME_MINUTES = 1
    REFRESH_INTERVAL_MS = 60_000    # re-run every 60s (one bar)

    def __init__(
        self,
        kite,
        instrument_token: int,
        symbol: str,
        timeframe_minutes: int = 1,
        atr_base_ema: int = 21,
        atr_distance_threshold: float = 1.5,
        cvd_zscore_threshold: float = 1.5,
        atr_extension_min: float = 1.10,
        parent=None,
    ):
        super().__init__(parent)
        self.kite = kite
        self.instrument_token = instrument_token
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes

        # Strategy params (tunable per symbol later)
        self.atr_base_ema = atr_base_ema
        self.atr_distance_threshold = atr_distance_threshold
        self.cvd_zscore_threshold = cvd_zscore_threshold
        self.atr_extension_min = atr_extension_min

        # Strategy components — owned exclusively by this worker
        self.detector = StrategySignalDetector(timeframe_minutes=timeframe_minutes)
        self.governance = SignalGovernance()
        self.chop = ChopFilter(period=14, threshold=61.8)

        # State
        self._last_signal_bar: dict[str, int] = {"long": -1, "short": -1}
        self._active = False
        self._refresh_timer: Optional[QTimer] = None
        self._fetch_thread: Optional[QThread] = None
        self._fetch_worker: Optional[_DataFetchWorker] = None

    def start(self):
        """Start periodic scanning."""
        self._active = True
        self._refresh_timer = QTimer()
        self._refresh_timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._run_scan)
        self._refresh_timer.start()
        # Run immediately on start
        QTimer.singleShot(0, self._run_scan)

    def stop(self):
        """Cleanly stop this worker."""
        self._active = False
        if self._refresh_timer:
            self._refresh_timer.stop()
        if self._fetch_worker:
            self._fetch_worker.cancel()

    def _run_scan(self):
        if not self._active:
            return

        from datetime import datetime, timedelta
        now = datetime.now()
        from_dt = now - timedelta(days=5)

        self.status_update.emit(self.symbol, "fetching")

        # Spin up a data fetch worker in its own thread
        self._fetch_worker = _DataFetchWorker(
            kite=self.kite,
            instrument_token=self.instrument_token,
            from_dt=from_dt,
            to_dt=now,
            timeframe_minutes=self.timeframe_minutes,
            focus_mode=True,
        )
        self._fetch_thread = QThread()
        self._fetch_worker.moveToThread(self._fetch_thread)
        self._fetch_worker.result_ready.connect(self._on_data_ready)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_worker.finished.connect(self._fetch_thread.quit)
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_thread.start()

    def _on_fetch_error(self, msg: str):
        self.error_occurred.emit(self.symbol, f"fetch_error:{msg}")
        self.status_update.emit(self.symbol, "error")

    def _on_data_ready(self, cvd_df, price_df, prev_close, previous_day_cpr):
        """
        Core signal computation — called in worker thread after data fetch.
        This is where the institutional magic happens:
          1. Compute indicators
          2. Check chop regime (don't trade sideways markets)
          3. Run ATR reversal detector
          4. Gate through SignalGovernance (confidence scoring)
          5. Emit only NEW signals (dedup by bar index)
        """
        try:
            if price_df is None or price_df.empty:
                self.status_update.emit(self.symbol, "no_data")
                return

            # ── Arrays ──────────────────────────────────────────────────
            price_close = price_df["close"].to_numpy(dtype=float)
            price_high  = price_df["high"].to_numpy(dtype=float)
            price_low   = price_df["low"].to_numpy(dtype=float)
            price_open  = price_df["open"].to_numpy(dtype=float)
            volume      = price_df.get("volume", np.ones(len(price_close))).to_numpy(dtype=float)

            if len(price_close) < 30:
                self.status_update.emit(self.symbol, "warming_up")
                return

            # ── Indicators ──────────────────────────────────────────────
            atr         = calculate_atr(price_high, price_low, price_close, period=14)
            ema10       = calculate_ema(price_close, 10)
            ema51       = calculate_ema(price_close, self.atr_base_ema)
            adx         = compute_adx(price_high, price_low, price_close, period=14)

            # Session-aware VWAP
            session_keys = price_df.index.date if hasattr(price_df.index, "date") else None
            vwap = calculate_vwap(price_close, volume, session_keys=session_keys)

            # CVD indicators
            cvd_close = cvd_df["close"].to_numpy(dtype=float) if (cvd_df is not None and not cvd_df.empty) else np.zeros_like(price_close)
            cvd_ema10 = calculate_ema(cvd_close, 10)
            cvd_ema51 = calculate_ema(cvd_close, 51)
            cvd_zscore, _ = calculate_cvd_zscore(cvd_close, ema_period=51, zscore_window=50)

            # ── Chop filter at current bar ───────────────────────────────
            idx = len(price_close) - 1
            in_chop = is_chop_regime(
                idx=idx,
                strategy_type="atr_reversal",
                price=price_close,
                ema_slow=ema51,
                atr=atr,
                adx=adx,
                price_high=price_high,
                price_low=price_low,
            )

            # ── ATR reversal masks ───────────────────────────────────────
            safe_atr = np.where(atr <= 0, np.nan, atr)
            distance = np.abs(price_close - ema51) / safe_atr

            price_atr_above = (distance >= self.atr_distance_threshold) & (price_close > ema51)
            price_atr_below = (distance >= self.atr_distance_threshold) & (price_close < ema51)

            # CVD z-score distance masks
            cvd_atr_above = cvd_zscore >= self.cvd_zscore_threshold
            cvd_atr_below = cvd_zscore <= -self.cvd_zscore_threshold

            timestamps = list(price_df.index) if hasattr(price_df.index, "__iter__") else None

            # ── Detect signals ───────────────────────────────────────────
            (
                short_confirmed,
                long_confirmed,
                _short_raw,
                _long_raw,
            ) = self.detector.detect_atr_reversal_strategy(
                price_atr_above=price_atr_above,
                price_atr_below=price_atr_below,
                cvd_atr_above=cvd_atr_above,
                cvd_atr_below=cvd_atr_below,
                atr_values=atr,
                timestamps=timestamps,
                price_close=price_close,
                price_open=price_open,
                price_ema51=ema51,
                price_vwap=vwap,
                cvd_data=cvd_close,
                vwap_min_distance_atr_mult=0.3,
                exhaustion_min_score=2,
            )

            # ── Check current bar for new signal ─────────────────────────
            for side, mask in [("long", long_confirmed), ("short", short_confirmed)]:
                if not mask[idx]:
                    continue
                if self._last_signal_bar[side] == idx:
                    continue  # already emitted this bar

                self._last_signal_bar[side] = idx

                # Gate through governance
                strategy_masks = {
                    "long":  {"atr_reversal": long_confirmed},
                    "short": {"atr_reversal": short_confirmed},
                }
                decision = self.governance.fuse_signal(
                    strategy_type="atr_reversal",
                    side=side,
                    strategy_masks=strategy_masks,
                    closed_idx=idx,
                    price_close=price_close,
                    ema10=ema10,
                    ema51=ema51,
                    atr=atr,
                    cvd_close=cvd_close,
                    cvd_ema10=cvd_ema10,
                    cvd_ema51=cvd_ema51,
                    adx=adx,
                )

                event = AtrSignalEvent(
                    symbol=self.symbol,
                    instrument_token=self.instrument_token,
                    side=side,
                    price=float(price_close[idx]),
                    atr=float(atr[idx]),
                    adx=float(adx[idx]),
                    confidence=decision.confidence,
                    quality_score=decision.signal_quality_score,
                    chop_filtered=in_chop,
                )

                logger.info(
                    "[SCANNER] %s %s @ %.2f  conf=%.2f  chop=%s",
                    self.symbol, side.upper(), event.price,
                    event.confidence, in_chop,
                )

                self.signal_fired.emit(event)

            self.status_update.emit(self.symbol, "watching")

        except Exception as exc:
            logger.error("[SCANNER] %s compute error: %s", self.symbol, exc, exc_info=True)
            self.error_occurred.emit(self.symbol, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator — the "desk" that manages all symbol pods
# ─────────────────────────────────────────────────────────────────────────────

class MultiSymbolEngine(QObject):
    """
    Manages N SymbolWorkers, each in their own QThread.

    The UI talks only to this class:
      - add_symbol()     → spin up a new worker
      - remove_symbol()  → cleanly stop and destroy
      - signal_fired     → connect to UI slot

    Institutional concept: "Portfolio-level signal bus"
    The engine doesn't trade — it just detects and emits.
    Order routing happens downstream (existing execution stack).
    """

    signal_fired    = Signal(object)    # AtrSignalEvent → forward to UI
    symbol_status   = Signal(str, str)  # symbol, status
    symbol_error    = Signal(str, str)  # symbol, error

    def __init__(self, kite, parent=None):
        super().__init__(parent)
        self.kite = kite
        self._workers: dict[str, SymbolWorker] = {}
        self._threads: dict[str, QThread] = {}

        # Default strategy params (can be overridden per symbol)
        self.default_params = {
            "timeframe_minutes": 1,
            "atr_base_ema": 21,
            "atr_distance_threshold": 1.5,
            "cvd_zscore_threshold": 1.5,
            "atr_extension_min": 1.10,
        }

    @property
    def watched_symbols(self) -> list[str]:
        return list(self._workers.keys())

    def add_symbol(self, symbol: str, instrument_token: int, params: dict | None = None):
        """Add a symbol to the watchlist and start scanning."""
        if symbol in self._workers:
            logger.warning("[ENGINE] %s already being watched", symbol)
            return

        p = {**self.default_params, **(params or {})}

        worker = SymbolWorker(
            kite=self.kite,
            instrument_token=instrument_token,
            symbol=symbol,
            **p,
        )
        thread = QThread()
        worker.moveToThread(thread)

        worker.signal_fired.connect(self.signal_fired)
        worker.status_update.connect(self.symbol_status)
        worker.error_occurred.connect(self.symbol_error)

        thread.started.connect(worker.start)
        thread.start()

        self._workers[symbol] = worker
        self._threads[symbol] = thread

        logger.info("[ENGINE] Started watching %s (token=%d)", symbol, instrument_token)
        self.symbol_status.emit(symbol, "started")

    def remove_symbol(self, symbol: str):
        """Stop and remove a symbol from the watchlist."""
        worker = self._workers.pop(symbol, None)
        thread = self._threads.pop(symbol, None)

        if worker:
            worker.stop()
        if thread:
            thread.quit()
            thread.wait(2000)

        logger.info("[ENGINE] Stopped watching %s", symbol)

    def remove_all(self):
        for symbol in list(self._workers.keys()):
            self.remove_symbol(symbol)

    def update_params(self, symbol: str, params: dict):
        """Hot-update strategy parameters for a running worker."""
        worker = self._workers.get(symbol)
        if not worker:
            return
        for k, v in params.items():
            if hasattr(worker, k):
                setattr(worker, k, v)