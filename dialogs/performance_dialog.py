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

    KEY IMPROVEMENTS (Updated for Separate DBs):
    - Works with mode-specific TradeLedger instances (trades_paper.db / trades_live.db)
    - No longer needs trading_mode filter (each DB only has its own mode's data)
    - Uses TradeLedger's public API methods instead of direct SQL
    - Validates data at every step
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

    def __init__(self, trade_ledger, parent=None):
        """
        Initialize PerformanceDialog with a mode-specific TradeLedger instance.

        Args:
            trade_ledger: TradeLedger instance (already mode-specific)
            parent: Parent widget
        """
        super().__init__(parent)

        self.trade_ledger = trade_ledger
        self._drag_pos: QPoint | None = None

        # Validate trade_ledger
        if not self.trade_ledger:
            logger.error("PerformanceDialog initialized without trade_ledger!")
            raise ValueError("trade_ledger is required")

        # Get mode from the ledger instance itself
        self.mode = self.trade_ledger.mode.upper()

        self.setWindowTitle(f"Performance Dashboard - {self.mode}")
        self.setMinimumSize(1000, 720)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._setup_ui()
        self._connect_signals()
        self._apply_styles()

        # Initial data load
        self.refresh()

        logger.debug(f"PerformanceDialog initialized for mode: {self.mode}")

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

        # Display mode badge
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
    # DATA - Using TradeLedger Public API
    # ------------------------------------------------------------------

    def _get_all_trades_from_ledger(self) -> list[dict]:
        """
        Fetch ALL trades from the ledger (no mode filter needed - DB is mode-specific).
        Uses TradeLedger's public API instead of direct SQL.
        """
        try:
            # Get all unique session dates first
            cur = self.trade_ledger.conn.cursor()
            cur.execute("SELECT DISTINCT session_date FROM trades ORDER BY session_date ASC")
            dates = [row[0] for row in cur.fetchall()]

            # Fetch trades for all dates
            all_trades = []
            for session_date in dates:
                trades = self.trade_ledger.get_trades_for_date(session_date)
                all_trades.extend(trades)

            logger.debug(f"[{self.mode}] Fetched {len(all_trades)} trades from ledger")

            # Convert to dict format for compatibility
            return [
                {
                    "symbol": dict(t)["tradingsymbol"],
                    "pnl": dict(t)["net_pnl"],
                    "qty": dict(t)["quantity"],
                    "date": dict(t)["session_date"],
                    "exit_time": dict(t)["exit_time"],
                }
                for t in all_trades
            ]
        except Exception as e:
            logger.error(f"[{self.mode}] Error fetching trades from ledger: {e}", exc_info=True)
            return []

    def _get_pnl_by_day_from_ledger(self) -> dict:
        """
        Get daily PnL aggregated by session_date.
        Uses direct SQL (no mode filter needed - DB is mode-specific).
        """
        try:
            cur = self.trade_ledger.conn.cursor()
            cur.execute("""
                SELECT session_date, COALESCE(SUM(net_pnl), 0)
                FROM trades
                GROUP BY session_date
                ORDER BY session_date ASC
            """)

            rows = cur.fetchall()

            if not rows:
                logger.debug(f"[{self.mode}] No trading data found")

            return {row[0]: row[1] for row in rows}

        except Exception as e:
            logger.error(f"[{self.mode}] Error fetching PnL by day: {e}", exc_info=True)
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
        No mode filtering needed - DB is already mode-specific.
        """
        pnl_by_day = self._get_pnl_by_day_from_ledger()

        if not pnl_by_day:
            logger.debug(f"[{self.mode}] No trading data found")
            self._clear_metrics()
            self.chart.clear()
            self._show_empty_chart_message()
            return

        self._update_metrics(pnl_by_day)
        self._plot_equity(pnl_by_day)

    def _show_empty_chart_message(self):
        """Display a message when there's no data to plot"""
        self.chart.clear()
        text_item = pg.TextItem(
            "No trading data available yet.\nComplete some trades to see your performance!",
            anchor=(0.5, 0.5),
            color='#A9B1C3'
        )
        # Position text in center
        self.chart.addItem(text_item)
        view_range = self.chart.viewRange()
        x_center = sum(view_range[0]) / 2
        y_center = sum(view_range[1]) / 2
        text_item.setPos(x_center, y_center)

    def _clear_metrics(self):
        """Reset all metrics to default values"""
        for key, label in self.labels.items():
            label.setText("—")
            label.setStyleSheet("color: #E0E0E0;")

    def _update_metrics(self, pnl_by_day: dict):
        """
        Calculate and display all performance metrics.
        Uses trade-level data (all trades in this mode-specific DB).
        """
        trades = self._get_all_trades_from_ledger()

        if not trades:
            logger.warning(f"[{self.mode}] No trades found")
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

    def _plot_equity(self, pnl_by_day: dict):
        """
        Plots cumulative PnL using UNIX timestamps for DateAxisItem.
        Handles single-day and multi-day data safely.
        """
        self.chart.clear()

        if not pnl_by_day:
            return

        # Sort dates
        sorted_items = sorted(pnl_by_day.items(), key=lambda x: x[0])

        x_vals = []
        y_vals = []
        cumulative_pnl = 0.0

        for session_date, day_pnl in sorted_items:
            try:
                # Handle different date formats
                if isinstance(session_date, str):
                    # String date: YYYY-MM-DD
                    if len(session_date) == 10 and session_date.count('-') == 2:
                        dt = datetime.strptime(session_date, "%Y-%m-%d")
                    else:
                        logger.warning(f"Unexpected date format: {session_date}")
                        continue
                elif isinstance(session_date, datetime):
                    # Already a datetime object
                    dt = session_date
                elif hasattr(session_date, 'year') and hasattr(session_date, 'month'):
                    # datetime.date object (from SQLite with PARSE_DECLTYPES)
                    from datetime import date as date_type
                    if isinstance(session_date, date_type):
                        dt = datetime.combine(session_date, datetime.min.time())
                    else:
                        logger.warning(f"Unknown date-like object: {type(session_date)} - {session_date}")
                        continue
                else:
                    logger.warning(f"Unknown date type: {type(session_date)} - {session_date}")
                    continue

                ts = dt.timestamp()
                cumulative_pnl += day_pnl
                x_vals.append(ts)
                y_vals.append(cumulative_pnl)

            except Exception as e:
                logger.error(f"Error processing date {session_date}: {e}", exc_info=True)
                continue

        if not x_vals:
            logger.warning(f"[{self.mode}] No valid data points to plot")
            return

        # Plot equity curve
        pen = pg.mkPen("#29C7C9", width=2)
        self.chart.plot(x_vals, y_vals, pen=pen, symbol="o", symbolSize=6)

        # Handle single-day vs multi-day display
        if len(x_vals) == 1:
            # Expand ±12 hours to create visible range
            half_day = 12 * 60 * 60
            self.chart.setXRange(
                x_vals[0] - half_day,
                x_vals[0] + half_day,
                padding=0
            )
        else:
            self.chart.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)

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