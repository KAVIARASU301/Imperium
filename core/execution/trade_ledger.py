# core/trade_ledger.py

import sqlite3
import logging
from pathlib import Path
from datetime import date, datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class TradeLedger:
    """
    Single Source of Truth for ALL completed trades.

    Maintains separate databases for paper and live trading.

    Rules:
    - A trade is recorded ONLY after exit order is COMPLETE
    - Realized PnL is FINAL and never recalculated
    - All performance metrics must read from here
    """

    def __init__(self, mode: str = "paper"):
        """
        Initialize TradeLedger with separate DB for each trading mode.

        Args:
            mode: "paper" or "live"
        """
        if mode.lower() not in ["paper", "live"]:
            raise ValueError(f"Invalid mode '{mode}'. Must be 'paper' or 'live'")

        self.mode = mode.lower()

        # Separate DB files: trades_paper.db and trades_live.db
        db_name = f"trades_{self.mode}.db"
        self.db_path = Path.home() / ".imperium_desk" / db_name
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self.conn = self._conn

        self._create_tables()

        logger.info(f"TradeLedger initialized in {self.mode.upper()} mode at {self.db_path}")

    # ------------------------------------------------------------------
    # DB Setup
    # ------------------------------------------------------------------

    def _create_tables(self):
        cursor = self._conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id            TEXT PRIMARY KEY,
                order_id_entry      TEXT,
                order_id_exit       TEXT UNIQUE,

                symbol              TEXT,
                tradingsymbol       TEXT,
                instrument_token    INTEGER,
                option_type         TEXT,
                expiry              DATE,
                strike              REAL,

                side                TEXT,
                quantity            INTEGER,

                entry_price         REAL,
                exit_price          REAL,

                entry_time          TEXT,
                exit_time           TEXT,

                realized_pnl        REAL,
                charges             REAL DEFAULT 0,
                net_pnl             REAL,

                exit_reason         TEXT,
                strategy_tag        TEXT,
                trade_status        TEXT DEFAULT 'MANUAL',
                strategy_name       TEXT DEFAULT 'N/A',

                trading_mode        TEXT,
                session_date        DATE,

                created_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_session_date
            ON trades(session_date);
        """)

        self._ensure_column(cursor, "trades", "trade_status", "TEXT DEFAULT 'MANUAL'")
        self._ensure_column(cursor, "trades", "strategy_name", "TEXT DEFAULT 'N/A'")

        self._conn.commit()

    def _ensure_column(self, cursor, table: str, column: str, definition: str) -> None:
        rows = cursor.execute(f"PRAGMA table_info({table})").fetchall()
        existing_columns = {row[1] for row in rows}
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def record_trade(self, trade: Dict) -> None:
        """
        Insert a finalized trade into the ledger.
        Must be called ONLY after exit order is COMPLETE.
        """

        try:
            # 🔑 CRITICAL: Convert date objects to ISO strings for SQLite
            expiry = trade.get("expiry")
            if expiry and isinstance(expiry, (date, datetime)):
                expiry = expiry.isoformat()

            session_date = trade.get("session_date")
            if not session_date:
                session_date = date.today().isoformat()
            elif isinstance(session_date, (date, datetime)):
                session_date = session_date.isoformat()

            cursor = self._conn.cursor()

            # 🔑 CRITICAL: Use INSERT instead of INSERT OR IGNORE to catch errors
            cursor.execute("""
                INSERT INTO trades (
                    trade_id,
                    order_id_entry,
                    order_id_exit,
                    symbol,
                    tradingsymbol,
                    instrument_token,
                    option_type,
                    expiry,
                    strike,
                    side,
                    quantity,
                    entry_price,
                    exit_price,
                    entry_time,
                    exit_time,
                    realized_pnl,
                    charges,
                    net_pnl,
                    exit_reason,
                    strategy_tag,
                    trade_status,
                    strategy_name,
                    trading_mode,
                    session_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade["trade_id"],
                trade.get("order_id_entry"),
                trade.get("order_id_exit"),
                trade.get("symbol"),
                trade.get("tradingsymbol"),
                trade.get("instrument_token"),
                trade.get("option_type"),
                expiry,  # Already converted to ISO string
                trade.get("strike"),
                trade.get("side"),
                trade.get("quantity"),
                trade.get("entry_price"),
                trade.get("exit_price"),
                trade.get("entry_time"),
                trade.get("exit_time"),
                trade.get("realized_pnl"),
                trade.get("charges", 0.0),
                trade.get("net_pnl"),
                trade.get("exit_reason"),
                trade.get("strategy_tag"),
                str(trade.get("trade_status") or "MANUAL").upper(),
                trade.get("strategy_name") or trade.get("strategy_tag") or "N/A",
                self.mode.upper(),  # Force consistency with ledger mode
                session_date  # Already converted to ISO string
            ))

            self._conn.commit()

            logger.info(
                f"[{self.mode.upper()}] ✅ Trade recorded | {trade.get('tradingsymbol')} | "
                f"PnL: {trade.get('net_pnl'):.2f}"
            )

        except sqlite3.IntegrityError as e:
            # Handle duplicate order_id_exit (UNIQUE constraint violation)
            if "UNIQUE constraint failed" in str(e):
                logger.warning(
                    f"[{self.mode.upper()}] Duplicate trade ignored for order_id_exit={trade.get('order_id_exit')}"
                )
            else:
                logger.error(f"[{self.mode.upper()}] DB integrity error: {e}", exc_info=True)
                raise
        except Exception as e:
            logger.error(
                f"[{self.mode.upper()}] ❌ Failed to record trade | "
                f"tradingsymbol={trade.get('tradingsymbol')} | "
                f"order_id_exit={trade.get('order_id_exit')} | "
                f"Error: {e}",
                exc_info=True
            )
            raise

    # ------------------------------------------------------------------
    # Read APIs (used by widgets later)
    # ------------------------------------------------------------------

    def get_trades_for_day(self, session_date: Optional[str] = None) -> List[Dict]:
        session_date = session_date or date.today().isoformat()

        cursor = self._conn.cursor()
        rows = cursor.execute(
            "SELECT * FROM trades WHERE session_date = ? ORDER BY exit_time",
            (session_date,)
        ).fetchall()

        return [dict(row) for row in rows]

    def get_day_summary(self, session_date: Optional[str] = None) -> Dict:
        session_date = session_date or date.today().isoformat()

        cursor = self._conn.cursor()

        row = cursor.execute("""
            SELECT
                COUNT(*)                            AS total_trades,
                SUM(net_pnl)                        AS total_pnl,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) AS losses,
                MAX(net_pnl)                        AS best_trade,
                AVG(CASE WHEN net_pnl > 0 THEN net_pnl END) AS avg_win,
                AVG(CASE WHEN net_pnl < 0 THEN net_pnl END) AS avg_loss
            FROM trades
            WHERE session_date = ?
        """, (session_date,)).fetchone()

        if not row or row["total_trades"] == 0:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "best_trade": 0.0
            }

        total = row["total_trades"]
        wins = row["wins"]

        return {
            "total_trades": total,
            "wins": wins,
            "losses": row["losses"],
            "total_pnl": row["total_pnl"] or 0.0,
            "win_rate": round((wins / total) * 100, 2),
            "avg_win": row["avg_win"] or 0.0,
            "avg_loss": row["avg_loss"] or 0.0,
            "best_trade": row["best_trade"] or 0.0
        }

    def get_trades_for_date(self, session_date: str):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM trades WHERE session_date = ? ORDER BY exit_time ASC",
            (session_date,)
        )
        return cur.fetchall()

    def get_realized_pnl_for_date(self, session_date: str) -> float:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM trades WHERE session_date = ?",
            (session_date,)
        )
        return cur.fetchone()[0]

    def get_daily_trade_stats(self, trading_day: str, mode: str = None):
        """
        Get trade stats for a specific day.

        Args:
            trading_day: Date string in YYYY-MM-DD format
            mode: Deprecated - kept for backward compatibility but ignored
        """
        cur = self.conn.cursor()

        cur.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(net_pnl), 0)
            FROM trades
            WHERE session_date = ?
        """, (trading_day,))

        total, wins, losses, total_pnl = cur.fetchone()
        win_rate = (wins / total * 100) if total else 0.0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl
        }

    def get_trade_stats_for_date(self, session_date: str) -> dict:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT 
                COUNT(*)                                  AS total_trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(net_pnl), 0)                 AS total_pnl
            FROM trades
            WHERE session_date = ?
        """, (session_date,))
        row = cur.fetchone()

        total = row[0] or 0
        wins = row[1] or 0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": row[2] or 0,
            "win_rate": (wins / total * 100) if total else 0.0,
            "total_pnl": row[3] or 0.0
        }

    # ------------------------------------------------------------------
    # Debug / Utility
    # ------------------------------------------------------------------

    def get_row_count(self) -> int:
        """Get total number of trades in this ledger."""
        cur = self.conn.cursor()
        return cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    def get_last_n_trades(self, n: int = 10) -> List[Dict]:
        """Get the last N trades for debugging."""
        cur = self.conn.cursor()
        rows = cur.execute(
            "SELECT * FROM trades ORDER BY exit_time DESC LIMIT ?",
            (n,)
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def close(self):
        try:
            self._conn.close()
            logger.info(f"[{self.mode.upper()}] TradeLedger closed")
        except Exception:
            pass