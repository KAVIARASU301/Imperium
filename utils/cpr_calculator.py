# core/utils/cpr_calculator.py
"""
Utility for calculating Central Pivot Range (CPR) levels.
"""

import logging
from typing import Dict, Optional
import pandas as pd

logger = logging.getLogger(__name__)


class CPRCalculator:
    """Optimized CPR calculation with robust error handling."""

    @staticmethod
    def calculate_cpr_levels(high: float, low: float, close: float) -> Dict[str, float]:
        """Calculates Pivot, BC, and TC from HLC values."""
        pivot = (high + low + close) / 3
        bc = (high + low) / 2
        tc = (pivot - bc) + pivot

        # Ensure tc is always above bc
        if tc < bc:
            tc, bc = bc, tc

        return {
            'pivot': round(pivot, 2),
            'tc': round(tc, 2),
            'bc': round(bc, 2),
            'range_width': round(abs(tc - bc), 2)
        }

    @staticmethod
    def get_previous_day_cpr(data: pd.DataFrame) -> Optional[Dict[str, float]]:
        """
        Calculates CPR levels from the provided day's data.
        It assumes the input DataFrame contains the data for the single day
        (i.e., the previous trading day) needed for the calculation.
        """
        if data.empty:
            logger.warning("CPR calculation failed: Input DataFrame is empty.")
            return None

        # Check for required columns
        if not all(col in data.columns for col in ['high', 'low', 'close']):
            logger.warning("CPR calculation failed: DataFrame missing 'high', 'low', or 'close' columns.")
            return None

        try:
            # Calculate HLC from the entire provided dataframe
            day_high = data['high'].max()
            day_low = data['low'].min()
            day_close = data['close'].iloc[-1]

            return CPRCalculator.calculate_cpr_levels(day_high, day_low, day_close)

        except (IndexError, KeyError) as e:
            logger.error(f"Could not calculate CPR due to a data issue: {e}")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred during CPR calculation: {e}", exc_info=True)
            return None