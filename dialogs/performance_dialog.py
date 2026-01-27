import logging
from datetime import datetime

import pyqtgraph as pg
from pyqtgraph import DateAxisItem
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout
)

logger = logging.getLogger(__name__)


class PerformanceDialog(QDialog):
    """
    Professional Performance Dashboard
    Single source of truth: TradeLedger

    KEY IMPROVEMENTS:
    - Properly filters by trading_mode (LIVE vs PAPER)
    - Uses uppercase mode comparison for consistency
    - Validates data at every step
    - Clear separation between live and paper trading metrics
    """

    refresh_requested = Signal()

    # ------------------------------------------------------------------
    # Tooltips (KEYED BY METRIC KEY — NOT UI TEXT)
    # ------------------------------------------------------------------
    METRIC_TOOLTIPS = {
        "total_pnl": "Total profit or loss accumulated across all trading days.",
        "expectancy": "Average profit per trade.\nPositive expectancy means the system has an edge.",
        "win_rate": "Percentage of trades that ended in profit.",
        "profit_factor": "Total profit divided by total loss.\nAbove 1.5 is considered healthy.",

        "avg_win": "Average profit from winning trades.",
        "avg_loss": "Average loss from losing trades.",
        "rr_ratio": "Risk—Reward Ratio.\nHow much you gain compared to how much you lose.",
        "rr_quality": "Human-friendly evaluation of Risk—Reward quality.",

        "total_trades": "Total number of completed trades.",
        "consistency": "Percentage of days that ended in profit.\nMeasures stability, not accuracy.",
        "best_day": "Highest profit achieved in a single trading day.",
        "worst_day": "Largest loss incurred in a single trading day."
    }

    # ------------------------------------------------------------------

    def __init__(self, trade_ledger, mode="live", parent=None):
        super().__init__(parent)

        # ✅ CRITICAL: Store mode in uppercase for DB consistency
        self.mode = mode.upper()
        self._drag_pos: QPoint | None = None
        self.trade_ledger = trade_ledger

        # Validate trade_ledger
        if not self.trade_ledger:
            logger.error("PerformanceDialog initialized without trade_ledger!")
            raise ValueError("trade_ledger is required")

        self.setWindowTitle(f"Performance Dashboard - {self.mode}")
        self.setMinimumSize(1000, 720)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._setup_ui()
        self._connect_signals()
        self._apply_styles()

        # Initial data load
        self.refresh()

        logger.info(f"PerformanceDialog initialized for mode: {self.mode}")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        self.container = QWidget(self)
        self.container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(22, 14, 22, 22)
        layout.setSpacing(18)

        layout.addLayout(self._create_header())
        layout.addLayout(self._create_metrics_grid())
        layout.addWidget(self._create_chart(), 1)

    def _create_header(self):
        layout = QHBoxLayout()

        title = QLabel("PERFORMANCE DASHBOARD")
        title.setObjectName("dialogTitle")

        # Display mode badge (lowercase for display, uppercase for logic)
        mode_badge = QLabel(self.mode)
        mode_badge.setObjectName("modeBadge")

        self.refresh_btn = QPushButton("REFRESH")
        self.refresh_btn.setObjectName("navButton")

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")

        layout.addWidget(title)
        layout.addWidget(mode_badge)
        layout.addStretch()
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.close_btn)

        return layout

    def _create_metrics_grid(self):
        grid = QGridLayout()
        grid.setSpacing(14)

        self.labels = {}

        metrics = [
            ("Total P&L", "total_pnl"),
            ("Expectancy", "expectancy"),
            ("Win Rate", "win_rate"),
            ("Profit Factor", "profit_factor"),

            ("Avg Win", "avg_win"),
            ("Avg Loss", "avg_loss"),
            ("Risk—Reward", "rr_ratio"),
            ("RR Quality", "rr_quality"),

            ("Total Trades", "total_trades"),
            ("Consistency", "consistency"),
            ("Best Day", "best_day"),
            ("Worst Day", "worst_day"),
        ]

        for i, (title, key) in enumerate(metrics):
            row, col = divmod(i, 4)
            self.labels[key] = self._metric_card(grid, title, key, row, col)

        return grid

    def _metric_card(self, layout, title, metric_key, row, col):
        card = QWidget()
        card.setObjectName("metricCard")

        # ✅ TOOLTIP FIX — USE METRIC KEY DIRECTLY
        tooltip = self.METRIC_TOOLTIPS.get(metric_key)
        if tooltip:
            card.setToolTip(tooltip)

        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("metricTitle")

        value_lbl = QLabel("—")
        value_lbl.setObjectName("metricValue")
        value_lbl.setAlignment(Qt.AlignCenter)
        value_lbl.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))

        v.addWidget(title_lbl)
        v.addWidget(value_lbl)

        layout.addWidget(card, row, col)
        return value_lbl

    # ------------------------------------------------------------------
    # CHART
    # ------------------------------------------------------------------

    def _create_chart(self):
        # Custom axis formatter for Y-axis (PnL)
        class CurrencyAxisItem(pg.AxisItem):
            def tickStrings(self, values, scale, spacing):
                """Format tick values as currency without scientific notation"""
                strings = []
                for v in values:
                    if abs(v) >= 10_000_000:  # 1 Cr+
                        strings.append(f'₹{v / 10_000_000:.1f}Cr')
                    elif abs(v) >= 100_000:  # 1 Lakh+
                        strings.append(f'₹{v / 100_000:.1f}L')
                    elif abs(v) >= 1_000:  # 1K+
                        strings.append(f'₹{v / 1_000:.0f}K')
                    else:
                        strings.append(f'₹{v:.0f}')
                return strings

        # Create axes with custom formatters
        date_axis = DateAxisItem(orientation="bottom")
        currency_axis = CurrencyAxisItem(orientation="left")

        chart = pg.PlotWidget(axisItems={"bottom": date_axis, "left": currency_axis})
        chart.setBackground("#161A25")
        chart.showGrid(x=True, y=True, alpha=0.25)
        chart.setLabel("left", "Cumulative P&L")
        chart.setLabel("bottom", "Date")

        # Set tick spacing for date axis
        chart.getAxis("bottom").setTickSpacing(
            major=86400 * 7,  # 7 days
            minor=86400  # 1 day
        )

        # Style the axes
        for axis_name in ['left', 'bottom']:
            axis = chart.getAxis(axis_name)
            axis.setPen(pg.mkPen('#3A4458', width=1))
            axis.setTextPen(pg.mkPen('#A9B1C3'))

        self.chart = chart
        return chart

    # ------------------------------------------------------------------
    # DATA - PROPERLY FILTERED BY MODE
    # ------------------------------------------------------------------

    def _get_trades_from_ledger(self) -> list[dict]:
        """
        Fetch trades for the current mode from TradeLedger.
        ✅ CRITICAL: Uses uppercase mode for DB query
        """
        try:
            cur = self.trade_ledger.conn.cursor()
            cur.execute("""
                SELECT
                    tradingsymbol,
                    net_pnl,
                    quantity,
                    session_date,
                    exit_time
                FROM trades
                WHERE trading_mode = ?
                ORDER BY session_date ASC, exit_time ASC
            """, (self.mode,))  # Already uppercase from __init__

            rows = cur.fetchall()

            logger.debug(f"Fetched {len(rows)} trades for mode: {self.mode}")

            return [
                {
                    "symbol": r[0],
                    "pnl": r[1],
                    "qty": r[2],
                    "date": r[3],
                    "exit_time": r[4],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Error fetching trades from ledger: {e}", exc_info=True)
            return []

    def _get_pnl_by_day_from_ledger(self) -> dict:
        """
        Get daily PnL aggregated by session_date.
        ✅ CRITICAL: Filters by trading_mode
        """
        try:
            cur = self.trade_ledger.conn.cursor()
            cur.execute("""
                SELECT session_date, COALESCE(SUM(net_pnl), 0)
                FROM trades
                WHERE trading_mode = ?
                GROUP BY session_date
                ORDER BY session_date ASC
            """, (self.mode,))

            rows = cur.fetchall()

            logger.debug(f"Fetched {len(rows)} trading days for mode: {self.mode}")

            return {row[0]: row[1] for row in rows}
        except Exception as e:
            logger.error(f"Error fetching PnL by day: {e}", exc_info=True)
            return {}

    def update_metrics(self, _metrics: dict | None = None):
        """
        Public API — ignores external metrics.
        Always reads from TradeLedger.
        """
        self.refresh()

    def refresh(self):
        """
        Refresh performance metrics directly from TradeLedger.
        ✅ All data is filtered by self.mode
        """
        logger.info(f"Refreshing performance dialog for mode: {self.mode}")

        pnl_by_day = self._get_pnl_by_day_from_ledger()

        if not pnl_by_day:
            logger.info(f"No trading data found for mode: {self.mode}")
            self._clear_metrics()
            self.chart.clear()
            return

        self._update_metrics(pnl_by_day)
        self._plot_equity(pnl_by_day)

    def _clear_metrics(self):
        """Reset all metrics to default values"""
        for key, label in self.labels.items():
            label.setText("—")
            label.setStyleSheet("color: #E0E0E0;")

    def _update_metrics(self, pnl_by_day: dict):
        """
        Calculate and display all performance metrics.
        ✅ Uses trade-level data filtered by mode
        """
        trades = self._get_trades_from_ledger()

        if not trades:
            logger.warning(f"No trades found for mode: {self.mode}")
            self._clear_metrics()
            return

        trade_pnls = [t["pnl"] for t in trades]

        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p < 0]

        total_trades = len(trade_pnls)
        total_pnl = sum(trade_pnls)

        # Trade-level metrics
        win_rate = (len(wins) / total_trades) * 100 if total_trades else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0

        expectancy = (
                (win_rate / 100) * avg_win -
                ((100 - win_rate) / 100) * avg_loss
        )

        profit_factor = sum(wins) / abs(sum(losses)) if losses else float("inf")
        rr_ratio = avg_win / avg_loss if avg_loss else 0

        rr_quality = (
            "Poor" if rr_ratio < 1 else
            "Not Bad" if rr_ratio < 1.5 else
            "Good" if rr_ratio < 2 else
            "Very Good"
        )

        # Day-based metrics
        daily_values = list(pnl_by_day.values())
        green_days = [v for v in daily_values if v > 0]

        consistency = (
            (len(green_days) / len(daily_values)) * 100
            if daily_values else 0
        )

        best_day = max(daily_values) if daily_values else 0
        worst_day = min(daily_values) if daily_values else 0

        # UI update helper
        def setv(key, text, color):
            lbl = self.labels[key]
            lbl.setText(text)
            lbl.setStyleSheet(f"color:{color};")

        # Update all metrics
        setv("total_pnl", f"₹{total_pnl:,.0f}", "#29C7C9" if total_pnl >= 0 else "#F85149")
        setv("expectancy", f"₹{expectancy:,.0f}", "#00D1B2" if expectancy >= 0 else "#F85149")
        setv("win_rate", f"{win_rate:.1f}%", "#4CAF50" if win_rate >= 50 else "#F39C12")
        setv("profit_factor", f"{profit_factor:.2f}", "#4CAF50" if profit_factor >= 1.5 else "#F39C12")

        setv("avg_win", f"₹{avg_win:,.0f}", "#4CAF50")
        setv("avg_loss", f"₹{avg_loss:,.0f}", "#F85149")
        setv("rr_ratio", f"{rr_ratio:.2f}", "#29C7C9")
        setv("rr_quality", rr_quality,
             "#00D1B2" if rr_ratio >= 2 else "#29C7C9" if rr_ratio >= 1.5 else "#F39C12")

        setv("total_trades", str(total_trades), "#E0E0E0")
        setv("consistency", f"{consistency:.1f}%", "#4CAF50" if consistency >= 50 else "#F39C12")
        setv("best_day", f"₹{best_day:,.0f}", "#4CAF50")
        setv("worst_day", f"₹{worst_day:,.0f}", "#F85149")

        logger.info(f"Metrics updated: {total_trades} trades, Total PnL: ₹{total_pnl:,.0f}")

    def _plot_equity(self, pnl: dict):
        """
        Plot cumulative equity curve from daily PnL data.
        ✅ Data is already filtered by mode
        """
        self.chart.clear()

        xs, ys = [], []
        running = 0.0

        for d, p in sorted(pnl.items()):
            running += p
            if isinstance(d, str):
                dt = datetime.strptime(d, "%Y-%m-%d")
            else:
                dt = datetime.combine(d, datetime.min.time())

            xs.append(dt.timestamp())
            ys.append(running)

        if not xs:
            logger.debug("No data points to plot")
            return

        # Zero line
        self.chart.addItem(
            pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen("#3A4458", style=Qt.DashLine))
        )

        # Main soft equity line
        self.chart.plot(
            xs, ys,
            pen=pg.mkPen("#9ADFE0", width=1.6),
            antialias=True
        )

        # Area fills for visual clarity
        self.chart.plot(xs, [y if y > 0 else 0 for y in ys],
                        pen=None, fillLevel=0,
                        fillBrush=pg.mkBrush(41, 199, 201, 55))

        self.chart.plot(xs, [y if y < 0 else 0 for y in ys],
                        pen=None, fillLevel=0,
                        fillBrush=pg.mkBrush(248, 81, 73, 55))

        logger.debug(f"Equity curve plotted with {len(xs)} data points")

    # ------------------------------------------------------------------
    # DRAG SUPPORT
    # ------------------------------------------------------------------

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e):
        if self._drag_pos:
            delta = e.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = e.globalPosition().toPoint()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    # ------------------------------------------------------------------
    # SIGNALS & STYLE
    # ------------------------------------------------------------------

    def _connect_signals(self):
        self.refresh_btn.clicked.connect(self.refresh)
        self.close_btn.clicked.connect(self.close)

    def _apply_styles(self):
        self.setStyleSheet("""
            QToolTip {
                background-color: #212635;
                color: #E0E0E0;
                border: 1px solid #3A4458;
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 11px;
            }
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 14px;
            }
            #dialogTitle {
                color: #FFFFFF;
                font-size: 18px;
                font-weight: 600;
            }
            #modeBadge {
                background-color: #212635;
                border: 1px solid #3A4458;
                border-radius: 6px;
                padding: 4px 10px;
                color: #29C7C9;
                font-size: 11px;
                font-weight: bold;
            }
            #metricCard {
                background-color: #212635;
                border: 1px solid #3A4458;
                border-radius: 10px;
            }
            #metricTitle {
                color: #A9B1C3;
                font-size: 11px;
            }
            #metricValue {
                color: #FFFFFF;
            }
            #closeButton {
                background: transparent;
                border: none;
                color: #8A9BA8;
                font-size: 16px;
            }
            #closeButton:hover {
                color: #FFFFFF;
            }
            QPushButton#navButton {
                background-color: #212635;
                border: 1px solid #3A4458;
                border-radius: 6px;
                padding: 6px 14px;
                color: #E0E0E0;
            }
            QPushButton#navButton:hover {
                background-color: #29C7C9;
                color: #161A25;
            }
        """)