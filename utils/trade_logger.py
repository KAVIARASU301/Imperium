# core/utils/trade_logger.py
import sqlite3
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class TradeLogger:
    """
    Handles logging trades to a persistent SQLite database.
    """

    def __init__(self, mode: str = 'live', db_path: Optional[str] = None):
        """
        Initializes the logger for a specific mode ('live' or 'paper').
        """
        if db_path is None:
            home = os.path.expanduser("~")
            db_dir = os.path.join(home, ".imperium_desk")
            os.makedirs(db_dir, exist_ok=True)
            db_filename = f"trade_history_{mode}.db"
            self.db_path = os.path.join(db_dir, db_filename)
        else:
            self.db_path = db_path

        logger.info(f"Trade history database for '{mode}' mode at: {self.db_path}")
        self._create_table()

    def _get_connection(self):
        """Creates and returns a database connection."""
        return sqlite3.connect(self.db_path)

    def _create_table(self):
        """Creates the 'orders' table if it doesn't already exist."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # ADDED the 'pnl' column to store profit and loss
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id TEXT UNIQUE,
                        timestamp TEXT NOT NULL,
                        tradingsymbol TEXT NOT NULL,
                        transaction_type TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        average_price REAL NOT NULL,
                        status TEXT NOT NULL,
                        product TEXT,
                        pnl REAL DEFAULT 0.0
                    )
                """)
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database error while creating table: {e}")

    def log_trade(self, order_data: Dict):
        """Logs a single completed trade to the database."""
        if not order_data.get('order_id'):
            logger.warning("Attempted to log a trade without an order_id. Skipping.")
            return

        query = """
            INSERT OR REPLACE INTO orders
            (order_id, timestamp, tradingsymbol, transaction_type, quantity, average_price, status, product, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            order_data.get('order_id'),
            order_data.get('order_timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            order_data.get('tradingsymbol'),
            order_data.get('transaction_type'),
            order_data.get('filled_quantity', order_data.get('quantity')),
            order_data.get('average_price'),
            order_data.get('status'),
            order_data.get('product'),
            order_data.get('pnl', 0.0)
        )

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                logger.info(f"Logged/Replaced trade for order ID: {params[0]} with PNL: {params[8]}")
        except sqlite3.Error as e:
            logger.error(f"Failed to log trade for order ID {params[0]}: {e}")

    def get_trades_for_date(self, trade_date: datetime) -> List[Dict]:
        """Retrieves all completed trades for a specific date."""
        date_str = trade_date.strftime('%Y-%m-%d')
        query = "SELECT * FROM orders WHERE date(timestamp) = ? ORDER BY timestamp DESC"
        trades = []
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, (date_str,))
                rows = cursor.fetchall()
                for row in rows:
                    trades.append(dict(row))
            return trades
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch trades for date {date_str}: {e}")
            return []

    def get_all_trades(self) -> List[Dict]:
        """Retrieves all completed trades from the database, most recent first."""
        query = "SELECT * FROM orders ORDER BY timestamp DESC"
        trades = []
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                for row in rows:
                    trades.append(dict(row))
            return trades
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch trades from database: {e}")
            return []