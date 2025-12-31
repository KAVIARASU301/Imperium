# core/cvd/cvd_state.py

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class CVDState:
    """
    Holds per-symbol CVD state.
    This is intentionally UI-agnostic.
    """

    symbol: str
    cvd: float = 0.0

    last_price: Optional[float] = None
    last_volume: Optional[int] = None

    session_date: Optional[date] = None

    def reset_session(self, new_date: date):
        """Reset CVD at the start of a new session."""
        self.cvd = 0.0
        self.session_date = new_date
        self.last_price = None
        self.last_volume = None
