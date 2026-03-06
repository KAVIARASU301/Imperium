"""
core/cvd/live_refresh_controller.py
====================================
Minute-boundary-aligned polling controller for CVD charts.

Architecture:
  WebSocket (CVDEngine) → animates every tick inside the current bar (real-time)
  MinuteAlignedPoller  → fires ONCE at :00 of each minute to commit the
                         closed bar to history and seed the next bar's open

This eliminates 19/20 timer wakes vs a flat 3-second QTimer while making
the chart feel MORE alive — WebSocket was already doing the intra-bar work.

Timeline example — started at 09:17:34:
  Alignment fire → 09:18:00   (one-shot, 26 s from now)
  Steady repeats → every 60 000 ms exactly
"""

from __future__ import annotations
from datetime import datetime
from PySide6.QtCore import QObject, QTimer


class MinuteAlignedPoller(QObject):
    """
    Drop-in replacement for a flat-interval QTimer driving live historical polls.

    Usage:
        self._poller = MinuteAlignedPoller(callback=self._on_minute_close, parent=self)
        self._poller.start()
        ...
        self._poller.stop()
    """

    def __init__(
        self,
        callback,
        interval_minutes: int = 1,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._callback    = callback
        self._interval_ms = interval_minutes * 60_000

        # One-shot: lands at the next clean :00 boundary
        self._align_timer = QTimer(self)
        self._align_timer.setSingleShot(True)
        self._align_timer.timeout.connect(self._on_aligned)

        # Steady: every interval_ms after first alignment
        self._steady_timer = QTimer(self)
        self._steady_timer.setSingleShot(False)
        self._steady_timer.timeout.connect(self._callback)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        self._align_timer.stop()
        self._steady_timer.stop()
        self._align_timer.start(self._ms_to_next_boundary())

    def stop(self):
        self._align_timer.stop()
        self._steady_timer.stop()

    def is_active(self) -> bool:
        return self._align_timer.isActive() or self._steady_timer.isActive()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_aligned(self):
        self._callback()
        self._steady_timer.start(self._interval_ms)

    def _ms_to_next_boundary(self) -> int:
        now        = datetime.now()
        elapsed_ms = now.second * 1000 + now.microsecond // 1000
        ms_left    = self._interval_ms - (elapsed_ms % self._interval_ms)
        return max(ms_left, 500)