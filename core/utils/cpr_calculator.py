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
        Calculates CPR levels from one complete previous-session dataframe.
        """
        if data.empty:
            logger.warning("CPR calculation failed: Input DataFrame is empty.")
            return None

        required_cols = {'high', 'low', 'close'}
        if not required_cols.issubset(data.columns):
            logger.warning("CPR calculation failed: DataFrame missing required columns %s", required_cols)
            return None

        try:
            day_data = data.sort_index()
            day_high = pd.to_numeric(day_data['high'], errors='coerce').max()
            day_low = pd.to_numeric(day_data['low'], errors='coerce').min()
            close_series = pd.to_numeric(day_data['close'], errors='coerce').dropna()

            if pd.isna(day_high) or pd.isna(day_low) or close_series.empty:
                logger.warning("CPR calculation failed: Invalid HLC values after numeric normalization.")
                return None

            day_close = float(close_series.iloc[-1])
            return CPRCalculator.calculate_cpr_levels(float(day_high), float(day_low), day_close)

        except (IndexError, KeyError, ValueError, TypeError) as e:
            logger.error(f"Could not calculate CPR due to a data issue: {e}")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred during CPR calculation: {e}", exc_info=True)
            return None
