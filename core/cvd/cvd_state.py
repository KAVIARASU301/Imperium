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

    instrument_token: int
    cvd: float = 0.0
    last_price: float | None = None
    last_volume: int | None = None
    session_date: date | None = None

    def reset_session(self, new_date: date):
        """Reset CVD at the start of a new session."""
        self.cvd = 0.0
        self.session_date = new_date
        self.last_price = None
        self.last_volume = None