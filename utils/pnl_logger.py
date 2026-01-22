import sqlite3
import logging
import os
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class PnlLogger:
    """Handles logging realized P&L to a persistent SQLite database."""

    def __init__(self, mode: str = 'live', db_path: Optional[str] = None):
        """
        Initializes the logger for a specific mode ('live' or 'paper').
        """
        if db_path is None:
            home = os.path.expanduser("~")
            db_dir = os.path.join(home, ".options_badger")
            os.makedirs(db_dir, exist_ok=True)
            # Create a mode-specific database file
            db_filename = f"pnl_history_{mode}.db"
            self.db_path = os.path.join(db_dir, db_filename)
        else:
            self.db_path = db_path

        logger.info(f"P&L history database for '{mode}' mode at: {self.db_path}")
        self._create_table()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _create_table(self):
        """Creates the 'realized_pnl' table if it doesn't exist."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS realized_pnl (
                        date TEXT PRIMARY KEY,
                        pnl REAL NOT NULL
                    )
                """)
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database error while creating P&L table: {e}")

    def log_pnl(self, pnl_date: datetime, pnl_value: float):
        """
        Logs P&L for a specific date. If an entry for the date
        exists, it adds the new P&L value to the existing one.
        """
        date_key = pnl_date.strftime("%Y-%m-%d")
        query = """
            INSERT INTO realized_pnl (date, pnl)
            VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET
            pnl = pnl + excluded.pnl;
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (date_key, pnl_value))
                conn.commit()
                logger.info(f"Logged P&L of {pnl_value:.2f} for date {date_key}")
        except sqlite3.Error as e:
            logger.error(f"Failed to log P&L for date {date_key}: {e}")

    def get_pnl_for_date(self, pnl_date: datetime) -> float:
        """
        Retrieves the total realized P&L for a specific date.
        Returns 0.0 if no entry is found for that date.
        """
        date_key = pnl_date.strftime("%Y-%m-%d")
        query = "SELECT pnl FROM realized_pnl WHERE date = ?"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (date_key,))
                result = cursor.fetchone()
                if result:
                    return result[0]
                return 0.0
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch P&L for date {date_key}: {e}")
            return 0.0

    def get_all_pnl(self) -> Dict[str, float]:
        """Retrieves all P&L data from the database."""
        query = "SELECT date, pnl FROM realized_pnl"
        pnl_data = {}
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                for row in rows:
                    pnl_data[row['date']] = row['pnl']
            return pnl_data
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch P&L data from database: {e}")
            return {}