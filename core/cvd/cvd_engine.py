# core/cvd/cvd_engine.py

import logging
from datetime import datetime
from typing import Dict, Iterable, Optional

from PySide6.QtCore import QObject, Signal
from core.cvd.cvd_state import CVDState
from datetime import date
from core.cvd.cvd_mode import CVDMode

logger = logging.getLogger(__name__)


class CVDEngine(QObject):
    """
    Tick-driven CVD engine.
    Emits signal whenever CVD changes.
    """

    cvd_updated = Signal(int, float)  # instrument_token, cvd_value

    def __init__(self):
        super().__init__()
        self._states: Dict[int, CVDState] = {}
        self._last_log_time: Dict[int, float] = {}
        self.mode: CVDMode = CVDMode.NORMAL

    def set_mode(self, mode: CVDMode):
        if self.mode == mode:
            return

        self.mode = mode
        logger.info(f"[CVD] Mode changed → {mode.value}")

        if mode == CVDMode.SINGLE_DAY:
            self._force_reset_all()

    def _force_reset_all(self):
        today = date.today()
        for state in self._states.values():
            state.reset_session(today)

    def register_token(self, token: int):
        """Explicitly register a token for CVD tracking."""
        if token not in self._states:
            self._states[token] = CVDState(instrument_token=token)
            logger.info(f"[CVD] Registered token {token}")

    def process_ticks(self, ticks: Iterable[dict]):
        """Process multiple ticks."""
        for tick in ticks:
            self._process_single_tick(tick)

    def _process_single_tick(self, tick: dict):
        """Process a single tick and update CVD."""
        token = tick.get("instrument_token")
        price = tick.get("last_price")
        volume = tick.get("volume")
        last_qty = tick.get("last_quantity") or tick.get("last_traded_quantity")

        if token is None or price is None:
            return

        if volume is None and last_qty is None:
            return

        # Get or create state
        state = self._states.get(token)
        if not state:
            return  # Only process registered tokens

        # Session management
        today = datetime.now().date()

        # NORMAL → reset only on date change
        if self.mode == CVDMode.NORMAL:
            if state.session_date != today:
                state.reset_session(today)

        # SINGLE DAY → always enforce today-only session
        else:
            if state.session_date != today:
                state.reset_session(today)

        # Initialize on first tick
        if state.last_volume is None:
            state.last_price = price
            state.last_volume = volume if volume is not None else 0
            self.cvd_updated.emit(token, state.cvd)
            return

        # Calculate volume delta.
        # Prefer exchange cumulative volume when it advances; otherwise fall back
        # to per-tick traded quantity for smoother intrabar updates.
        volume_delta = 0
        if volume is not None:
            volume_delta = volume - state.last_volume
            # Handle rare feed/session resets where cumulative volume drops.
            if volume_delta < 0:
                volume_delta = int(last_qty or 0)

        if volume_delta <= 0 and last_qty is not None:
            volume_delta = int(last_qty)

        # Update CVD if volume increased
        if volume_delta > 0:
            if price >= state.last_price:
                state.cvd += volume_delta
            else:
                state.cvd -= volume_delta

            # Emit signal
            self.cvd_updated.emit(token, state.cvd)

            # Throttled logging (every 2 seconds per token)
            current_time = datetime.now().timestamp()
            last_log = self._last_log_time.get(token, 0)
            if current_time - last_log >= 2.0:
                logger.debug(
                    f"[CVD] token={token} cvd={state.cvd:,.0f} "
                    f"delta={volume_delta:+,d} price={price:.2f}"
                )
                self._last_log_time[token] = current_time

        # Update state
        state.last_price = price
        if volume is not None:
            state.last_volume = volume

    def get_cvd(self, token: int) -> Optional[float]:
        """Get current CVD value for a token."""
        state = self._states.get(token)
        return state.cvd if state else None

    def snapshot(self) -> Dict[int, float]:
        """Get snapshot of all CVD values."""
        return {
            token: state.cvd
            for token, state in self._states.items()
        }

    def subscribe_instruments(self, tokens: Iterable[int]) -> bool:
        """Compatibility helper used by some UI flows.

        CVDEngine itself does not own websocket subscriptions; main window handles
        market data subscriptions. Here we just ensure token states exist.
        """
        try:
            for token in tokens:
                self.register_token(int(token))
            return True
        except Exception:
            logger.exception("[CVD] Failed to register instruments")
            return False

    def clear_token(self, token: int):
        """Remove a token from tracking."""
        if token in self._states:
            del self._states[token]
            logger.info(f"[CVD] Cleared token {token}")