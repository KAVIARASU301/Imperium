# core/cvd/cvd_engine.py

import logging
from datetime import datetime
from typing import Dict, Iterable

from core.cvd.cvd_state import CVDState

logger = logging.getLogger(__name__)


class CVDEngine:
    """
    Computes Cumulative Volume Delta (CVD) from live ticks.

    Logic:
    - Uses volume delta between ticks
    - Uses price change to infer buy/sell aggression
    - Resets daily (session-based)
    """

    def __init__(self):
        self._states: Dict[str, CVDState] = {}

    def get_state(self, symbol: str) -> CVDState:
        if symbol not in self._states:
            self._states[symbol] = CVDState(symbol=symbol)
        return self._states[symbol]

    def process_ticks(self, ticks: Iterable[dict]):
        """
        Process a batch of ticks from MarketDataWorker.
        """
        for tick in ticks:
            self._process_single_tick(tick)

    def _process_single_tick(self, tick: dict):
        symbol = tick.get("tradingsymbol")
        price = tick.get("last_price")
        volume = tick.get("volume_traded")

        if not symbol or price is None or volume is None:
            return

        state = self.get_state(symbol)

        today = datetime.now().date()

        # Session reset (like anchor = 1D in TradingView)
        if state.session_date != today:
            state.reset_session(today)

        # First tick bootstrap
        if state.last_price is None or state.last_volume is None:
            state.last_price = price
            state.last_volume = volume
            return

        volume_delta = volume - state.last_volume
        price_delta = price - state.last_price

        # Ignore invalid or zero volume changes
        if volume_delta <= 0:
            state.last_price = price
            state.last_volume = volume
            return

        # Aggression inference (same idea as requestVolumeDelta)
        if price_delta > 0:
            state.cvd += volume_delta
        elif price_delta < 0:
            state.cvd -= volume_delta
        # else: flat price → ignore

        state.last_price = price
        state.last_volume = volume

    def snapshot(self) -> Dict[str, float]:
        """
        Returns a lightweight snapshot for UI.
        """
        return {symbol: state.cvd for symbol, state in self._states.items()}

    def ensure_symbol(self, symbol: str):
        self.get_state(symbol)

    def get_cvd(self, symbol: str) -> float | None:
        state = self._states.get(symbol)
        if not state:
            return None
        return state.cvd