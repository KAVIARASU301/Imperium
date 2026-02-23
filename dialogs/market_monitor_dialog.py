# dialogs/market_monitor_dialog.py
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Set
import pandas as pd
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QWidget,
                               QLabel, QLineEdit, QPushButton, QMessageBox, QComboBox,
                               QSizePolicy, QFrame, QSpacerItem)
from PySide6.QtCore import Qt, QByteArray, QTimer, Signal, QEvent
from PySide6.QtGui import QFont
from kiteconnect import KiteConnect

from utils.config_manager import ConfigManager
from utils.cpr_calculator import CPRCalculator
from core.market_data.market_data_worker import MarketDataWorker

logger = logging.getLogger(__name__)


class DateNavigator(QWidget):
    """Date navigation control for historical data viewing"""
    date_changed = Signal(datetime, datetime)  # current_date, previous_date

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.btn_back = QPushButton("◀")
        self.btn_back.setFixedSize(40, 32)
        self.btn_back.setToolTip("Previous trading day")
        self.btn_back.clicked.connect(self._go_backward)

        self.lbl_dates = QLabel()
        self.lbl_dates.setAlignment(Qt.AlignCenter)
        self.lbl_dates.setMinimumWidth(500)
        self.lbl_dates.setStyleSheet("""
            QLabel {
                color: #E0E0E0;
                font-size: 13px;
                font-weight: 600;
            }
        """)

        self.btn_forward = QPushButton("▶")
        self.btn_forward.setFixedSize(40, 32)
        self.btn_forward.setToolTip("Next trading day")
        self.btn_forward.clicked.connect(self._go_forward)

        layout.addStretch()
        layout.addWidget(self.btn_back)
        layout.addWidget(self.lbl_dates)
        layout.addWidget(self.btn_forward)
        layout.addStretch()

    def _get_previous_trading_day(self, date: datetime) -> datetime:
        prev = date - timedelta(days=1)
        while prev.weekday() >= 5:  # Skip weekends
            prev -= timedelta(days=1)
        return prev

    def _get_next_trading_day(self, date: datetime) -> datetime:
        nxt = date + timedelta(days=1)
        while nxt.weekday() >= 5:  # Skip weekends
            nxt += timedelta(days=1)
        return nxt

    def _update_display(self):
        prev = self._get_previous_trading_day(self._current_date)
        cur_str = self._current_date.strftime("%A, %b %d, %Y")
        prev_str = prev.strftime("%A, %b %d, %Y")

        self.lbl_dates.setText(
            f"<span style='color:#5B9BD5;'>Previous: {prev_str}</span>"
            f"  |  "
            f"<span style='color:#26A69A;'>Current: {cur_str}</span>"
        )

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.btn_forward.setEnabled(self._current_date < today)

    def _go_backward(self):
        self._current_date = self._get_previous_trading_day(self._current_date)
        self._update_display()
        self.date_changed.emit(
            self._current_date,
            self._get_previous_trading_day(self._current_date)
        )

    def _go_forward(self):
        self._current_date = self._get_next_trading_day(self._current_date)
        self._update_display()
        self.date_changed.emit(
            self._current_date,
            self._get_previous_trading_day(self._current_date)
        )

    def get_dates(self):
        return (
            self._current_date,
            self._get_previous_trading_day(self._current_date)
        )


class MarketChartWidget(QWidget):
    """Optimized chart with line mode only"""

    MAX_CHART_POINTS = 1500

    def __init__(self, parent=None, timeframe_combo=None):
        super().__init__(parent)
        self.timeframe_combo = timeframe_combo
        self.symbol = ""
        self.chart_data = pd.DataFrame()
        self.day_separator_pos = None
        self.cpr_levels = None
        self._line_plot_today = None
        self._line_plot_prev = None
        self._live_dot = None  # Small dot at the end

        # Optimized update system
        self._data_is_dirty = False
        self._pending_ticks = []  # Batch tick updates

        # Reusable chart resources (avoid allocations in redraw loop)
        self._pen_prev = pg.mkPen('#666666', width=1.2)
        self._pen_today = pg.mkPen('#00BCD4', width=1.5)
        self._sep_pen = pg.mkPen('#555555', width=1, style=Qt.DashLine)
        self._dot_brush = pg.mkBrush('#00E676')

        # Throttle to 250ms for smoother updates
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._throttled_update)
        self.update_timer.start(250)

        self._setup_ui()
        self._setup_chart()
        self.show_message("EMPTY", "Awaiting symbol selection")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(5, 2, 5, 2)
        self.symbol_label = QLabel("NO SYMBOL")
        self.symbol_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #E0E0E0;")
        header_layout.addWidget(self.symbol_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        self.plot_widget = pg.PlotWidget()
        layout.addWidget(self.plot_widget)

    def _setup_chart(self):
        self.plot_widget.setBackground('#1A1A1A')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.setClipToView(True)
        self.plot_widget.setDownsampling(auto=True, mode='peak')
        self.plot_widget.setMouseEnabled(x=True, y=True)
        self.plot_widget.enableAutoRange(axis='y', enable=True)
        axis_pen = pg.mkPen(color='#B0B0B0', width=1)
        font = QFont("Segoe UI", 8)
        self.plot_widget.showAxis('right', True)
        for axis in [self.plot_widget.getAxis('left'), self.plot_widget.getAxis('right'),
                     self.plot_widget.getAxis('bottom')]:
            axis.setPen(axis_pen)
            axis.setTickFont(font)
        self.plot_widget.getAxis('bottom').setStyle(showValues=False)

    def _throttled_update(self):
        """Batched update - process all pending ticks at once"""
        if not self.isVisible():
            return
        window = self.window()
        if window is None or not window.isActiveWindow():
            return
        if self._data_is_dirty:
            for tick in self._pending_ticks:
                ltp = tick.get("last_price")
                if ltp is None or self.chart_data.empty:
                    continue
                self.chart_data.iat[-1, self.chart_data.columns.get_loc("close")] = ltp
                current_high = self.chart_data.iat[-1, self.chart_data.columns.get_loc("high")]
                current_low = self.chart_data.iat[-1, self.chart_data.columns.get_loc("low")]
                self.chart_data.iat[-1, self.chart_data.columns.get_loc("high")] = max(current_high, ltp)
                self.chart_data.iat[-1, self.chart_data.columns.get_loc("low")] = min(current_low, ltp)

            self._prune_chart_data()
            self._plot_chart_data(full_redraw=False)
            self._data_is_dirty = False
            self._pending_ticks.clear()

    def set_updates_enabled(self, enabled: bool):
        if enabled:
            if not self.update_timer.isActive():
                self.update_timer.start(250)
        else:
            self.update_timer.stop()

    def set_data(self, symbol: str, data: pd.DataFrame, day_separator_pos: int | None = None,
                 cpr_levels: Dict | None = None):
        if data.empty:
            self.show_message(f"[{symbol}]", "No historical data available.")
            return
        self.symbol = symbol
        # Avoid copy - just reference it (assuming no external mutation)
        self.chart_data = data
        self._prune_chart_data()
        self.day_separator_pos = day_separator_pos
        self.cpr_levels = cpr_levels
        self.symbol_label.setText(self.symbol)
        self._plot_chart_data(full_redraw=True)
        self.set_visible_range("Auto")

    def _prune_chart_data(self):
        """Keep only recent bars for long-running monitor sessions."""
        if self.chart_data.empty:
            return
        if len(self.chart_data) <= self.MAX_CHART_POINTS:
            return
        self.chart_data = self.chart_data.tail(self.MAX_CHART_POINTS).copy()

    def add_tick(self, tick: dict):
        """Queue tick for batched processing"""
        self._pending_ticks.append(tick)
        self._data_is_dirty = True

    def _draw_cpr(self):
        """Draw CPR band + levels so they remain visible on dense charts."""
        if not self.cpr_levels:
            return

        pivot = self.cpr_levels.get("pivot")
        tc = self.cpr_levels.get("tc")
        bc = self.cpr_levels.get("bc")
        if pivot is None:
            return

        if tc is not None and bc is not None:
            cpr_region = pg.LinearRegionItem(
                values=[bc, tc],
                orientation='horizontal',
                brush=pg.mkBrush(240, 118, 55, 24),
                pen=pg.mkPen(240, 118, 55, 0),
                movable=False,
            )
            self.plot_widget.addItem(cpr_region)

            self.plot_widget.addItem(
                pg.InfiniteLine(
                    pos=tc,
                    angle=0,
                    pen=pg.mkPen(color='#F07637', width=1),
                )
            )
            self.plot_widget.addItem(
                pg.InfiniteLine(
                    pos=bc,
                    angle=0,
                    pen=pg.mkPen(color='#F07637', width=1),
                )
            )

        inf_line = pg.InfiniteLine(
            pos=pivot,
            angle=0,
            pen=pg.mkPen(color='#F39C12', width=1.5, style=Qt.DotLine),
        )
        self.plot_widget.addItem(inf_line)

    def _plot_chart_data(self, full_redraw=True):
        """Optimized line chart plotting only"""
        if self.chart_data.empty:
            return

        plot_item = self.plot_widget.getPlotItem()

        if full_redraw:
            plot_item.clear()
            self._line_plot_today = None
            self._line_plot_prev = None
            self._live_dot = None

        df = self.chart_data
        x = np.arange(len(df))
        closes = df['close'].values

        # Line chart mode
        if self.day_separator_pos is not None:
            sep = self.day_separator_pos
            if self._line_plot_prev is None or full_redraw:
                self._line_plot_prev = plot_item.plot(
                    x[:sep], closes[:sep],
                    pen=self._pen_prev
                )
            else:
                self._line_plot_prev.setData(x[:sep], closes[:sep])

            if self._line_plot_today is None or full_redraw:
                self._line_plot_today = plot_item.plot(
                    x[sep:], closes[sep:],
                    pen=self._pen_today
                )
            else:
                self._line_plot_today.setData(x[sep:], closes[sep:])
        else:
            if self._line_plot_today is None or full_redraw:
                self._line_plot_today = plot_item.plot(
                    x, closes,
                    pen=self._pen_today
                )
            else:
                self._line_plot_today.setData(x, closes)

        # Draw CPR and separator only on full redraw
        if full_redraw:
            self._draw_cpr()
            if self.day_separator_pos:
                sep_line = pg.InfiniteLine(
                    pos=self.day_separator_pos - 0.5,
                    angle=90,
                    pen=self._sep_pen
                )
                plot_item.addItem(sep_line)

        # Add small dot at the end to show live movement
        if len(df) > 0:
            last_x = x[-1]
            last_price = closes[-1]

            if self._live_dot is None or full_redraw:
                self._live_dot = plot_item.plot(
                    [last_x], [last_price],
                    pen=None,
                    symbol='o',
                    symbolSize=6,
                    symbolBrush=self._dot_brush,
                    symbolPen=None
                )
            else:
                self._live_dot.setData([last_x], [last_price])

    def set_visible_range(self, count_text: str):
        """Optimized range setting"""
        if self.chart_data.empty:
            return

        total = len(self.chart_data)

        if count_text == "Auto":
            self.plot_widget.enableAutoRange()
            return

        try:
            count = int(count_text)
            start_idx = max(0, total - count)
            self.plot_widget.setXRange(start_idx, total, padding=0.02)
        except ValueError:
            pass

    def show_message(self, title: str, msg: str):
        """Lightweight message display"""
        self.plot_widget.clear()
        text_item = pg.TextItem(f"{title}\n{msg}", color='#888888', anchor=(0.5, 0.5))
        text_item.setPos(0.5, 0.5)
        self.plot_widget.addItem(text_item)


class MarketMonitorDialog(QDialog):
    """Main dialog - inherits optimizations from chart widgets"""

    def __init__(self, real_kite_client: KiteConnect = None,
                 market_data_worker: MarketDataWorker = None,
                 config_manager: ConfigManager = None, parent=None,
                 # Legacy params for backward compatibility
                 kite: KiteConnect = None, instruments_df: pd.DataFrame = None):
        super().__init__(parent)

        # Support both calling conventions
        self.kite = real_kite_client or kite
        self.config_manager = config_manager
        self.market_data_worker = market_data_worker

        # Get instruments from parent's instrument_data (dict format)
        # Convert to DataFrame for token mapping
        self.instruments_df = self._get_instruments_from_parent(parent, instruments_df)

        # Pre-build token map once (don't rebuild every lookup)
        self.symbol_to_token_map = self._build_token_map()

        self.token_to_chart_map: Dict[int, MarketChartWidget] = {}
        self.symbol_sets = []

        # Track current dates for historical browsing
        self.current_date = None
        self.previous_date = None
        self.live_mode = True

        self._setup_ui()
        self._connect_signals()
        self._apply_styles()
        self._load_and_populate_sets()
        self._restore_state()

        # Connect to worker with optimized callback
        self.market_data_worker.data_received.connect(
            self._on_ticks_received,
            Qt.QueuedConnection,
        )

        # Initialize with today's date
        self.current_date, self.previous_date = self.navigator.get_dates()

    def _get_instruments_from_parent(self, parent, instruments_df):
        """Extract instruments from kite.instruments() or parent"""
        if instruments_df is not None:
            return instruments_df

        # The original approach: fetch directly from kite
        if self.kite:
            try:
                instrument_list = self.kite.instruments()
                # Convert list of dicts to DataFrame
                return pd.DataFrame(instrument_list)
            except Exception as e:
                logger.error(f"Failed to fetch instruments from kite: {e}")

        # Fallback: Try parent's instrument_data (but it's in different format)
        if parent and hasattr(parent, 'instrument_data'):
            # parent.instrument_data is dict[symbol -> list of instruments]
            # We need to flatten it
            rows = []
            instrument_data = parent.instrument_data
            for symbol, instruments in instrument_data.items():
                if isinstance(instruments, list):
                    for instr in instruments:
                        if isinstance(instr, dict):
                            rows.append(instr)
                        elif isinstance(instr, str):
                            # Skip string entries
                            continue

            if rows:
                return pd.DataFrame(rows)

        # Last resort: empty DataFrame
        return pd.DataFrame(columns=['tradingsymbol', 'instrument_token', 'exchange'])

    def _build_token_map(self) -> Dict[str, int]:
        """Build symbol-to-token map once at init"""
        token_map = {}

        if self.instruments_df.empty:
            logger.warning("MarketMonitor: instruments_df is empty, token map will be empty")
            return token_map

        # Filter for equity and indices only
        try:
            filtered = self.instruments_df[
                self.instruments_df['instrument_type'].isin(['EQ', 'INDICES'])
            ]

            for _, row in filtered.iterrows():
                token_map[row['tradingsymbol']] = row['instrument_token']
        except KeyError:
            # Fallback: just use NSE exchange if instrument_type column doesn't exist
            logger.warning("MarketMonitor: instrument_type column not found, using NSE filter")
            try:
                nse_df = self.instruments_df[self.instruments_df['exchange'] == 'NSE']
                for _, row in nse_df.iterrows():
                    token_map[row['tradingsymbol']] = row['instrument_token']
            except Exception as e:
                logger.error(f"Failed to build token map: {e}")

        # Add common aliases
        if 'NIFTY 50' in token_map:
            token_map['NIFTY'] = token_map['NIFTY 50']
        if 'NIFTY BANK' in token_map:
            token_map['BANKNIFTY'] = token_map['NIFTY BANK']
        if 'NIFTY FIN SERVICE' in token_map:
            token_map['FINNIFTY'] = token_map['NIFTY FIN SERVICE']

        logger.info(f"MarketMonitor: Built token map with {len(token_map)} symbols")
        return token_map

    def _setup_ui(self):
        """UI setup with maximize and resize enabled"""
        self.setObjectName("MarketMonitorDialog")
        self.setWindowTitle("Market Monitor - Live Multi-Chart View")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(1400, 900)
        self.setMinimumSize(800, 600)  # Set minimum size for usability

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Control panel
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(8)

        lbl_symbols = QLabel("Symbols (comma-separated):")
        controls_layout.addWidget(lbl_symbols)

        self.symbols_entry = QLineEdit()
        self.symbols_entry.setPlaceholderText("e.g., NIFTY, BANKNIFTY, RELIANCE")
        controls_layout.addWidget(self.symbols_entry, 3)

        self.timeframe_combo = QComboBox()
        self.timeframe_combo.addItems(["minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute"])
        self.timeframe_combo.setCurrentText("5minute")
        controls_layout.addWidget(QLabel("Timeframe:"))
        controls_layout.addWidget(self.timeframe_combo, 1)

        self.candle_count_combo = QComboBox()
        self.candle_count_combo.addItems(["30", "60", "90", "120", "150", "Auto"])
        self.candle_count_combo.setCurrentText("Auto")
        controls_layout.addWidget(QLabel("Visible Candles:"))
        controls_layout.addWidget(self.candle_count_combo, 1)

        self.load_button = QPushButton("Load Charts")
        controls_layout.addWidget(self.load_button)

        main_layout.addLayout(controls_layout)

        # Symbol set management + Date Navigator (combined row)
        set_nav_layout = QHBoxLayout()
        set_nav_layout.setSpacing(8)

        set_nav_layout.addWidget(QLabel("Symbol Set:"))

        self.set_selector_combo = QComboBox()
        self.set_selector_combo.setMinimumWidth(180)
        set_nav_layout.addWidget(self.set_selector_combo)

        # Date Navigator in the middle
        self.navigator = DateNavigator(self)
        set_nav_layout.addWidget(self.navigator, 1)  # Takes remaining space, centered

        self.save_set_button = QPushButton("Save Current Set")
        set_nav_layout.addWidget(self.save_set_button)

        main_layout.addLayout(set_nav_layout)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        main_layout.addWidget(sep1)

        # Chart grid (2x3)
        chart_grid = QGridLayout()
        chart_grid.setSpacing(6)

        self.charts = []
        for row in range(2):
            for col in range(3):
                chart = MarketChartWidget(self, self.timeframe_combo)
                chart_grid.addWidget(chart, row, col)
                self.charts.append(chart)

        main_layout.addLayout(chart_grid, 1)

    def _fetch_and_plot_initial(self, chart: MarketChartWidget, symbol: str, token: int):
        """Optimized initial data fetch with historical date support"""
        if not self.kite:
            chart.show_message(f"[{symbol}] ERROR", "Kite client not available")
            logger.error("MarketMonitor: Kite client is None")
            return

        try:
            tf = self.timeframe_combo.currentText()

            # Always include previous trading day + current day in one request,
            # so CPR can be calculated from previous day even in live mode.
            if self.live_mode:
                current_trading_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                previous_trading_day = self._get_previous_trading_day(current_trading_day)

                # Give small forward buffer to include the latest intraday bars.
                from_date = previous_trading_day
                to_date = datetime.now() + timedelta(minutes=1)
            else:
                # Historical mode - use navigator dates
                to_date = self.current_date + timedelta(days=1)
                from_date = self.previous_date

            records = self.kite.historical_data(
                token, from_date, to_date, tf, continuous=False, oi=False
            )

            if not records:
                chart.show_message(f"[{symbol}] NO DATA", "No historical data available")
                return

            df = pd.DataFrame(records)
            df.dropna(inplace=True)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            unique_dates = sorted(pd.Series(df.index.date).unique())
            cpr_levels, day_separator_pos = None, None

            if len(unique_dates) < 2:
                logger.warning(
                    "MarketMonitor: CPR unavailable for %s - expected 2 sessions, got %s (%s)",
                    symbol,
                    len(unique_dates),
                    unique_dates,
                )
                chart.set_data(symbol, df)
                return

            today_date, prev_day_date = unique_dates[-1], unique_dates[-2]
            today_df = df[df.index.date == today_date]
            prev_day_df = df[df.index.date == prev_day_date]
            cpr_levels = CPRCalculator.get_previous_day_cpr(prev_day_df)
            day_separator_pos = len(prev_day_df)
            two_day_df = pd.concat([prev_day_df, today_df])
            chart.set_data(symbol, two_day_df, day_separator_pos, cpr_levels)

        except Exception as e:
            logger.error(f"Failed to fetch/plot data for {symbol}: {e}", exc_info=True)
            chart.show_message(f"[{symbol}] DATA ERROR", "Could not load data.")

    @staticmethod
    def _get_previous_trading_day(date: datetime) -> datetime:
        prev = date - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev

    def _connect_signals(self):
        self.load_button.clicked.connect(self._load_charts_data)
        self.save_set_button.clicked.connect(self._save_current_set)
        self.set_selector_combo.currentIndexChanged.connect(self._on_set_selected)
        self.candle_count_combo.currentTextChanged.connect(self._on_candle_count_changed)
        self.timeframe_combo.currentTextChanged.connect(self._load_charts_data)
        self.navigator.date_changed.connect(self._on_date_changed)

    def _apply_styles(self):
        """Stylesheet (unchanged)"""
        STYLE_SHEET = """
        QDialog#MarketMonitorDialog { background-color: #2C2C2C; }
        QLabel { color: #E0E0E0; font-size: 12px; }
        QPushButton {
            background-color: #424242; color: #FFFFFF; border: 1px solid #555555;
            padding: 6px 14px; border-radius: 4px; font-size: 12px; font-weight: bold;
        }
        QPushButton:hover { background-color: #555555; border: 1px solid #666666; }
        QPushButton:pressed { background-color: #383838; }
        QPushButton:disabled { background-color: #3A3A3A; color: #888888; border-color: #444444; }
        QComboBox {
            background-color: #424242; color: #E0E0E0; border: 1px solid #555555;
            padding: 6px; padding-left: 12px; border-radius: 4px; font-size: 12px;
        }
        QComboBox:hover { border: 1px solid #666666; }
        QComboBox::drop-down {
            subcontrol-origin: padding; subcontrol-position: top right; width: 22px;
            border-left-width: 1px; border-left-color: #555555; border-left-style: solid;
            border-top-right-radius: 3px; border-bottom-right-radius: 3px;
        }
        QComboBox QAbstractItemView {
            background-color: #3A3A3A; color: #E0E0E0; border: 1px solid #555555;
            selection-background-color: #007ACC; outline: 0px;
        }
        QLineEdit {
            background-color: #3A3A3A; color: #E0E0E0; border: 1px solid #555555;
            border-radius: 4px; padding: 7px; font-size: 12px;
        }
        QLineEdit:focus { border: 1px solid #007ACC; }
        QFrame[frameShape="4"] { border: none; height: 1px; background-color: #4A4A4A; }
        """
        self.setStyleSheet(STYLE_SHEET)

    def _get_instrument_token(self, symbol: str) -> int | None:
        """Fast token lookup"""
        upper_symbol = symbol.strip().upper()
        alias_map = {'NIFTY': 'NIFTY 50', 'BANKNIFTY': 'NIFTY BANK', 'FINNIFTY': 'NIFTY FIN SERVICE'}
        return self.symbol_to_token_map.get(alias_map.get(upper_symbol, upper_symbol))

    def _on_candle_count_changed(self, text: str):
        for chart in self.charts:
            chart.set_visible_range(text)

    def _on_date_changed(self, current_date: datetime, previous_date: datetime):
        """Handle date navigation - reload charts with historical data"""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self.current_date = current_date
        self.previous_date = previous_date

        # Check if we're in live mode or historical mode
        if current_date >= today:
            self.live_mode = True
            logger.info("MarketMonitor: Switched to LIVE mode")
        else:
            self.live_mode = False
            logger.info(f"MarketMonitor: Viewing historical data for {current_date.date()}")

        # Reload all charts with the new date range
        self._load_charts_data()

    def _load_charts_data(self):
        """Load charts (unchanged logic)"""
        self.unsubscribe_all()
        self.token_to_chart_map.clear()
        symbols = [s.strip() for s in self.symbols_entry.text().strip().split(',') if s.strip()]
        if not symbols:
            return

        self.load_button.setEnabled(False)
        self.load_button.setText("Loading...")
        tokens_to_subscribe = set()

        for i, chart in enumerate(self.charts):
            if i < len(symbols):
                symbol, token = symbols[i], self._get_instrument_token(symbols[i])
                if token:
                    self.token_to_chart_map[token] = chart
                    tokens_to_subscribe.add(token)
                    self._fetch_and_plot_initial(chart, symbol, token)
                else:
                    chart.show_message(f"INVALID: {symbol}", "Symbol not found")
            else:
                chart.show_message("EMPTY", "Awaiting symbol")

        self._on_candle_count_changed(self.candle_count_combo.currentText())
        if tokens_to_subscribe:
            self._subscribe_to(tokens_to_subscribe)

        self.load_button.setEnabled(True)
        self.load_button.setText("Load Charts")

    def _on_ticks_received(self, ticks: list[dict]):
        """Optimized tick routing - direct dispatch without logging"""
        for tick in ticks:
            if (token := tick.get("instrument_token")) in self.token_to_chart_map:
                self.token_to_chart_map[token].add_tick(tick)

    def _subscribe_to(self, tokens: Set[int]):
        if not tokens:
            return
        self.market_data_worker.set_instruments(tokens, append=True)
        logger.info(f"Market Monitor subscribed to tokens: {tokens}")

    def unsubscribe_all(self):
        if self.market_data_worker and self.token_to_chart_map:
            tokens_to_remove = set(self.token_to_chart_map.keys())
            current_subs = self.market_data_worker.subscribed_tokens
            self.market_data_worker.set_instruments(current_subs - tokens_to_remove)
            logger.info(f"Market Monitor unsubscribed from tokens: {tokens_to_remove}")
            self.token_to_chart_map.clear()

    def _load_and_populate_sets(self):
        self.symbol_sets = self.config_manager.load_market_monitor_sets()
        self.set_selector_combo.blockSignals(True)
        self.set_selector_combo.clear()
        self.set_selector_combo.addItem("Select a Symbol Set")

        for s in self.symbol_sets:
            self.set_selector_combo.addItem(s.get("name"))

        self.set_selector_combo.insertSeparator(self.set_selector_combo.count())
        self.set_selector_combo.addItem("➕ Add New Set…")
        self.set_selector_combo.addItem("🗑️ Manage Sets…")
        self.set_selector_combo.blockSignals(False)

    def _on_set_selected(self, index: int):
        if index == 0:
            self.symbols_entry.clear()
            return

        text = self.set_selector_combo.itemText(index)

        if text.startswith("➕"):
            from dialogs.add_symbol_set_dialog import AddSymbolSetDialog
            dlg = AddSymbolSetDialog(self)
            if dlg.exec() == QDialog.Accepted:
                data = dlg.get_data()
                if data["name"] and data["symbols"]:
                    self.symbol_sets.append(data)
                    self.config_manager.save_market_monitor_sets(self.symbol_sets)
                    self._load_and_populate_sets()
            return

        if text.startswith("🗑️"):
            from dialogs.manage_symbol_sets_dialog import ManageSymbolSetsDialog
            dlg = ManageSymbolSetsDialog(self.symbol_sets, self)
            dlg.exec()
            self.config_manager.save_market_monitor_sets(self.symbol_sets)
            self._load_and_populate_sets()
            return

        set_idx = index - 1
        if 0 <= set_idx < len(self.symbol_sets):
            self.symbols_entry.setText(self.symbol_sets[set_idx]["symbols"])
            QTimer.singleShot(50, self._load_charts_data)

    def _save_current_set(self):
        if (idx := self.set_selector_combo.currentIndex()) <= 0:
            return
        if not (symbols := self.symbols_entry.text().strip()):
            return
        self.symbol_sets[idx - 1]["symbols"] = symbols
        self.config_manager.save_market_monitor_sets(self.symbol_sets)

    def _restore_state(self):
        try:
            if state := self.config_manager.load_dialog_state('market_monitor'):
                self.restoreGeometry(QByteArray.fromBase64(state.encode('utf-8')))
        except Exception as e:
            logger.error(f"Could not restore dialog state: {e}")

    def closeEvent(self, event):
        try:
            self.config_manager.save_dialog_state(
                'market_monitor',
                self.saveGeometry().toBase64().data().decode('utf-8')
            )
        except Exception as e:
            logger.error(f"Failed to save dialog state: {e}")

        self.market_data_worker.data_received.disconnect(self._on_ticks_received)
        super().closeEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            is_active = self.isActiveWindow()
            for chart in self.charts:
                chart.set_updates_enabled(is_active)
        super().changeEvent(event)
