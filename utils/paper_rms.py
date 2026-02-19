# utils/paper_rms.py

import logging

logger = logging.getLogger(__name__)


class PaperRMS:
    """
    Simple Risk & Margin System for PAPER trading.
    Acts as a gatekeeper before order placement.
    """

    def __init__(self, starting_balance: float = 1_000_000.0):
        self.starting_balance = starting_balance
        self.used_margin = 0.0

    # -----------------------------
    # Margin Model (simple & safe)
    # -----------------------------
    def calculate_required_margin(self, price: float, quantity: int) -> float:
        """
        Very conservative margin model:
        premium * quantity * safety_factor
        """
        SAFETY_FACTOR = 1.1
        if price is None or quantity <= 0:
            raise ValueError("Invalid price or quantity for margin calculation")
        return price * quantity * SAFETY_FACTOR

    # -----------------------------
    # Checks
    # -----------------------------
    def can_place_order(self, price: float, quantity: int):
        try:
            required = self.calculate_required_margin(price, quantity)
        except Exception as e:
            return False, str(e)

        if self.available_margin < required:
            return False, (
                f"Insufficient margin. "
                f"Required: ₹{required:,.2f}, "
                f"Available: ₹{self.available_margin:,.2f}"
            )

        return True, ""

    # -----------------------------
    # Bookkeeping
    # -----------------------------
    def reserve_margin(self, price: float, quantity: int):
        margin = self.calculate_required_margin(price, quantity)
        self.used_margin += margin
        logger.info(f"RMS reserved margin: {margin:.2f}")

    def release_margin(self, price: float, quantity: int):
        margin = self.calculate_required_margin(price, quantity)
        self.used_margin = max(0.0, self.used_margin - margin)
        logger.info(f"RMS released margin: {margin:.2f}")

    # -----------------------------
    # Properties
    # -----------------------------
    @property
    def available_margin(self) -> float:
        return self.starting_balance - self.used_margin

    def snapshot(self) -> dict:
        return {
            "used": self.used_margin,
            "available": self.available_margin,
            "total": self.starting_balance,
        }
