# utils/data_models.py

"""Data models for Options Scalper application"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import date
from typing import Optional


class OptionType(Enum):
    """Option type enumeration"""
    CALL = "CE"
    PUT = "PE"


@dataclass
class Contract:
    """Represents an option contract"""
    symbol: str
    strike: float
    option_type: str
    expiry: date
    tradingsymbol: str
    instrument_token: int
    lot_size: int
    ltp: float = 0.0
    volume: int = 0
    oi: int = 0
    oi_change: int = 0
    bid: float = 0.0
    ask: float = 0.0
    iv: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0


@dataclass
class Position:
    """Represents an open position"""
    symbol: str
    tradingsymbol: str
    quantity: int
    average_price: float
    ltp: float
    pnl: float
    contract: Contract
    order_id: Optional[str]
    exchange: str = "NFO"
    product: str = "MIS"
    is_exiting: bool = False

    # --- New fields for Stop-Loss and Target ---
    stop_loss_price: Optional[float] = field(default=None)
    target_price: Optional[float] = field(default=None)
    trailing_stop_loss: Optional[float] = field(default=None)
    stop_loss_order_id: Optional[str] = field(default=None)
    target_order_id: Optional[str] = field(default=None)

    def update_pnl(self, new_ltp: float):
        """Recalculate P&L based on updated LTP"""
        self.ltp = new_ltp
        self.pnl = (new_ltp - self.average_price) * self.quantity