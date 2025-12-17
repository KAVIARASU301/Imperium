# dialogs/market_monitor_dialog.py
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Set
import pandas as pd
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QWidget,
                               QLabel, QLineEdit, QPushButton, QMessageBox, QComboBox,
                               QSizePolicy, QFrame, QSpacerItem, QGraphicsObject)
from PySide6.QtCore import Qt, QByteArray, QTimer, QRectF
from PySide6.QtGui import QFont, QPicture, QPainter
from kiteconnect import KiteConnect

from utils.config_manager import ConfigManager
from utils.cpr_calculator import CPRCalculator
from core.market_data_worker import MarketDataWorker

logger = logging.getLogger(__name__)


# --- MERGED FROM market_monitor_widget.py ---
class CandlestickItem(QGraphicsObject):
    def __init__(self, data=None):
        super().__init__()
        self.data = data or []
        # The QPicture caching is removed for efficiency

    def updateData(self, data):
        """
        This method is called to update the data for the chart.
        It signals that the geometry is about to change, updates the data,
        and then schedules a repaint.
        """
        # Inform the graphics scene that the item's geometry will change.
        # This is crucial for the scene to manage updates correctly.
        self.prepareGeometryChange()
        self.data = data
        # Schedule a repaint of the item.
        self.update()

    # The generatePicture method is no longer needed and has been removed.

    def paint(self, painter, option, widget=None):
        """
        The paint method is called by the graphics system to draw the item.
        The drawing logic is now here, which is more efficient than the
        previous QPicture caching approach for dynamic data.
        """
        painter.setRenderHint(QPainter.Antialiasing)

        BULL_COLOR = '#26A69A'
        BEAR_COLOR = '#EF5350'
        w = 0.3  # Width of the candlestick body

        # The visible range can be optimized here, but for now, we draw all data.
        for x, open_, high, low, close in self.data:
            bullish = close >= open_
            pen_color = BULL_COLOR if bullish else BEAR_COLOR
            pen = pg.mkPen(color=pen_color, width=1.5)
            painter.setPen(pen)

            # Draw the high-low wick
            painter.drawLine(x, low, x, high)

            brush_color = BULL_COLOR if bullish else BEAR_COLOR
            painter.setBrush(pg.mkBrush(brush_color))

            top = max(open_, close)
            bottom = min(open_, close)
            height = top - bottom

            # Draw the open-close body
            painter.drawRect(QRectF(x - w, bottom, w * 2, height))

    def boundingRect(self):
        """
        This method must return the outer bounds of the item. It is essential
        for the graphics scene to know the item's area.
        """
        if not self.data:
            return QRectF()

        # Unpack all data points to find the min/max coordinates
        xs, opens, highs, lows, closes = zip(*self.data)
        pen_width_offset = 1  # Add a small buffer for the pen width

        return QRectF(
            min(xs) - pen_width_offset,
            min(lows),
            (max(xs) - min(xs)) + (2 * pen_width_offset),
            max(highs) - min(lows)
        )


class MarketChartWidget(QWidget):
    def __init__(self, parent=None, timeframe_combo=None):
        super().__init__(parent)
        self.timeframe_combo = timeframe_combo
        self.symbol = ""
        self.chart_data = pd.DataFrame()
        self.chart_mode = 'line'
        self.day_separator_pos = None
        self.cpr_levels = None
        self._candlestick_item = None
        self._line_plot_today = None
        self._line_plot_prev = None
        self._live_indicator = None
        # FIX: Add a dirty flag and an update timer for throttling
        self._data_is_dirty = False
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._throttled_update)
        self.update_timer.start(500)  # Update chart at most every 500ms

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
        button_style = ("QPushButton { font-size: 11px; background-color: #424242;"
                        " border-radius: 4px; color: white; padding: 4px 8px; }"
                        " QPushButton:hover { background-color: #555555; }")
        self.mode_btn = QPushButton("Candle")
        self.mode_btn.setFixedWidth(60)
        self.mode_btn.setToolTip("Toggle Line / Candlestick View")
        self.mode_btn.setStyleSheet(button_style)
        self.mode_btn.clicked.connect(self.toggle_chart_mode)
        header_layout.addWidget(self.mode_btn)
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
        """Only redraws the chart if new data has arrived."""
        if self._data_is_dirty:
            self._plot_chart_data(full_redraw=False)
            self._data_is_dirty = False

    def set_data(self, symbol: str, data: pd.DataFrame, day_separator_pos: int | None = None,
                 cpr_levels: Dict | None = None):
        if data.empty:
            self.show_message(f"[{symbol}]", "No historical data available.")
            return
        self.symbol = symbol
        self.chart_data = data.copy()
        self.day_separator_pos = day_separator_pos
        self.cpr_levels = cpr_levels
        self.symbol_label.setText(self.symbol)
        self._plot_chart_data(full_redraw=True)
        self.set_visible_range("Auto")

    def _draw_cpr(self):
        """
        Draw ONLY the CPR center (Pivot) line.
        No background shading, no BC/TC region.
        """
        if not self.cpr_levels:
            return

        pivot = self.cpr_levels.get("pivot")
        if pivot is None:
            return

        pivot_pen = pg.mkPen(
            color="#F39C12",  # same amber tone you already use
            width=1,
            style=Qt.DashLine
        )

        pivot_line = pg.InfiniteLine(
            pos=pivot,
            angle=0,
            movable=False,
            pen=pivot_pen
        )

        self.plot_widget.addItem(pivot_line)

    def _plot_chart_data(self, full_redraw=False):
        if full_redraw:
            self.plot_widget.clear()
            self._candlestick_item = None
            self._line_plot_today = None
            self._line_plot_prev = None
            self._live_indicator = None

            # Draw CPR (only pivot, as already fixed)
            self._draw_cpr()

            if self.day_separator_pos is not None:
                sep = pg.InfiniteLine(
                    pos=self.day_separator_pos - 0.5,
                    angle=90,
                    pen=pg.mkPen(color='#3A4458', style=Qt.DashLine, width=1.5)
                )
                self.plot_widget.addItem(sep)

        x = np.arange(len(self.chart_data))

        # ------------------------------------------------------------------
        # LINE MODE
        # ------------------------------------------------------------------
        if self.chart_mode == 'line':

            # Remove candle item if switching modes
            if self._candlestick_item:
                self.plot_widget.removeItem(self._candlestick_item)
                self._candlestick_item = None

            # Create plots if needed
            if self._line_plot_today is None:
                fill_brush = pg.mkBrush(color=(41, 199, 201, 20))

                self._line_plot_today = self.plot_widget.plot(
                    [], [],
                    pen=pg.mkPen(color='#29C7C9', width=2),
                    fillLevel=0,
                    fillBrush=fill_brush
                )

                self._line_plot_prev = self.plot_widget.plot(
                    [], [],
                    pen=pg.mkPen(color='#E0E0E0', width=1.5)
                )

                self._live_indicator = pg.ScatterPlotItem(
                    [], [],
                    size=8,
                    pen=pg.mkPen(color='white', width=1),
                    brush=pg.mkBrush(color='#29C7C9')
                )
                self.plot_widget.addItem(self._live_indicator)

            # ---------------- FIX: build Y series correctly ----------------
            closes = self.chart_data['close'].values
            opens = self.chart_data['open'].values

            if len(closes) > 0:
                # First point = OPEN of first candle
                full_y = np.concatenate(([opens[0]], closes[1:]))
            else:
                full_y = closes

            # ---------------- Handle prev / today split ----------------
            if self.day_separator_pos is not None and self.day_separator_pos > 0:
                prev_x = x[:self.day_separator_pos]
                prev_y = full_y[:self.day_separator_pos]

                today_x = x[self.day_separator_pos:]
                today_y = full_y[self.day_separator_pos:]

                self._line_plot_prev.setData(prev_x, prev_y)
                self._line_plot_today.setData(today_x, today_y)

                if len(today_x) > 0:
                    self._live_indicator.setData(
                        [today_x[-1]],
                        [today_y[-1]]
                    )
            else:
                self._line_plot_prev.clear()
                self._line_plot_today.setData(x, full_y)

                if len(x) > 0:
                    self._live_indicator.setData(
                        [x[-1]],
                        [full_y[-1]]
                    )

        # ------------------------------------------------------------------
        # CANDLESTICK MODE
        # ------------------------------------------------------------------
        else:
            if self._line_plot_today:
                self.plot_widget.removeItem(self._line_plot_today)
                self.plot_widget.removeItem(self._line_plot_prev)
                self.plot_widget.removeItem(self._live_indicator)

                self._line_plot_today = None
                self._line_plot_prev = None
                self._live_indicator = None

            cs_data = [
                (i, *self.chart_data.iloc[i][['open', 'high', 'low', 'close']].values)
                for i in range(len(self.chart_data))
            ]

            if self._candlestick_item is None:
                self._candlestick_item = CandlestickItem(cs_data)
                self.plot_widget.addItem(self._candlestick_item)
            else:
                self._candlestick_item.updateData(cs_data)

    def add_tick(self, tick: dict):
        ltp = tick.get('last_price')
        if self.chart_data.empty or ltp is None: return
        now = datetime.now().replace(second=0, microsecond=0)
        tf_str = self.timeframe_combo.currentText() if self.timeframe_combo else "1min"
        tf_minutes = int(tf_str.replace("min", ""))
        rounded = now - timedelta(minutes=now.minute % tf_minutes)
        if rounded in self.chart_data.index:
            row = self.chart_data.loc[rounded]
            self.chart_data.at[rounded, 'close'] = ltp
            self.chart_data.at[rounded, 'high'] = max(row['high'], ltp)
            self.chart_data.at[rounded, 'low'] = min(row['low'], ltp)
        else:
            last_close = self.chart_data.iloc[-1]['close']
            new_row = pd.DataFrame([{'open': last_close, 'high': ltp, 'low': ltp, 'close': ltp}], index=[rounded])
            self.chart_data = pd.concat([self.chart_data, new_row])
            # No need to sort index if new rows are always appended
        self._data_is_dirty = True

    def set_visible_range(self, count_str: str):
        if self.chart_data.empty: return
        vb = self.plot_widget.getViewBox()

        data_view = self.chart_data

        if count_str.lower() != 'auto':
            try:
                count = int(count_str)
                total = len(self.chart_data)
                start = max(0, total - count)
                vb.setXRange(start, total, padding=0.02)
                data_view = self.chart_data.iloc[start:]
            except (ValueError, TypeError):
                vb.enableAutoRange()
                return
        else:
            vb.enableAutoRange(axis=pg.ViewBox.XAxis)

        if not data_view.empty:
            y_min = data_view['low'].min()
            y_max = data_view['high'].max()
            y_range = y_max - y_min

            margin = y_range * 0.1  # 10% margin
            if margin == 0:  # If price is flat
                margin = y_max * 0.01  # 1% of the price as margin
            if margin == 0:  # If price is also 0
                margin = 1  # a small default margin

            vb.setYRange(y_min - margin, y_max + margin, padding=0)
        else:
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)

    def toggle_chart_mode(self):
        self.chart_mode = 'line' if self.chart_mode == 'candlestick' else 'candlestick'
        self.mode_btn.setText("Candle" if self.chart_mode == 'line' else "Line")
        self._plot_chart_data(full_redraw=True)

    def show_message(self, title: str, message: str = ""):
        self.plot_widget.clear()
        self.symbol_label.setText(title)
        if message:
            text = pg.TextItem(message, color='#888888', anchor=(0.5, 0.5))
            text.setFont(QFont("Segoe UI", 10))
            self.plot_widget.addItem(text, ignoreBounds=True)

class AddSymbolSetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Symbol Set")
        self.setFixedSize(400, 220)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Group Name"))
        self.name_edit = QLineEdit()
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("Symbols (comma separated)"))
        self.symbols_edit = QLineEdit()
        layout.addWidget(self.symbols_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")

        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    def get_data(self):
        return {
            "name": self.name_edit.text().strip(),
            "symbols": self.symbols_edit.text().strip()
        }
class ManageSymbolSetsDialog(QDialog):
    def __init__(self, symbol_sets: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Symbol Sets")
        self.setFixedSize(350, 300)
        self.symbol_sets = symbol_sets

        layout = QVBoxLayout(self)

        self.list_widget = QComboBox()
        for s in symbol_sets:
            self.list_widget.addItem(s["name"])
        layout.addWidget(self.list_widget)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_selected)
        layout.addWidget(delete_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _delete_selected(self):
        idx = self.list_widget.currentIndex()
        if idx >= 0:
            self.symbol_sets.pop(idx)
            self.list_widget.removeItem(idx)

class MarketMonitorDialog(QDialog):
    def __init__(self, real_kite_client: KiteConnect, market_data_worker: MarketDataWorker,
                 config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.kite = real_kite_client
        self.config_manager = config_manager
        self.market_data_worker = market_data_worker
        self.market_data_worker.data_received.connect(self._on_ticks_received)

        self.charts: List[MarketChartWidget] = []
        self.token_to_chart_map: Dict[int, MarketChartWidget] = {}
        self.symbol_sets: List[Dict] = []
        self.symbol_to_token_map: Dict[str, int] = {}

        self.timeframe_map = {
            "1min": "minute", "3min": "3minute", "5min": "5minute",
            "10min": "10minute", "15min": "15minute", "30min": "30minute"
        }

        self._fetch_and_build_symbol_map()
        self._setup_window()
        self._setup_ui()
        self._apply_styles()
        self._connect_signals()
        self._load_and_populate_sets()
        self._restore_state()

        self.symbols_entry.setText("NIFTY 50, NIFTY BANK, SENSEX, FINNIFTY")
        print("[MarketMonitor] Init complete")
        QTimer.singleShot(100, self._load_charts_data)

    def _setup_window(self):
        self.setWindowTitle("Market Monitor")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(1200, 700)
        self.setMinimumSize(960, 600)
        self.setObjectName("MarketMonitorDialog")

    def _fetch_and_build_symbol_map(self):
        try:
            self.instrument_data = self.kite.instruments()
            self.symbol_to_token_map = {inst['tradingsymbol']: inst['instrument_token'] for inst in self.instrument_data
                                        if inst.get('instrument_type') in ['EQ', 'INDICES']}
        except Exception as e:
            logger.error(f"Failed to fetch instruments: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", "Could not load required instrument data.")

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        main_layout.addWidget(self._create_control_panel())
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(separator)
        main_layout.addLayout(self._create_chart_grid(), 1)

    def _create_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Symbol Sets:"))
        self.set_selector_combo = QComboBox()
        self.set_selector_combo.setToolTip("Select a pre-saved set of symbols")
        layout.addWidget(self.set_selector_combo, 1)

        self.symbols_entry = QLineEdit()
        self.symbols_entry.setPlaceholderText("Enter comma-separated symbols")
        layout.addWidget(self.symbols_entry, 3)

        self.save_set_button = QPushButton("Save Set")
        layout.addWidget(self.save_set_button)

        layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        layout.addWidget(QLabel("Candles:"))
        self.candle_count_combo = QComboBox()
        self.candle_count_combo.addItems(["Auto", "800", "600", "500", "400", "300", "200", "100", "75", "50"])
        self.candle_count_combo.setToolTip("Select number of recent candles to show (zoom)")
        layout.addWidget(self.candle_count_combo)

        layout.addWidget(QLabel("Timeframe:"))
        self.timeframe_combo = QComboBox()
        self.timeframe_combo.addItems(self.timeframe_map.keys())
        self.timeframe_combo.setToolTip("Select chart interval")
        layout.addWidget(self.timeframe_combo)

        self.load_button = QPushButton("Load Charts")
        layout.addWidget(self.load_button)
        return panel

    def _create_chart_grid(self) -> QGridLayout:
        grid_layout = QGridLayout()
        grid_layout.setSpacing(8)
        for i in range(2):
            for j in range(2):
                chart_widget = MarketChartWidget(self, timeframe_combo=self.timeframe_combo)
                grid_layout.addWidget(chart_widget, i, j)
                self.charts.append(chart_widget)
        return grid_layout

    def _fetch_and_plot_initial(self, chart: MarketChartWidget, symbol: str, token: int):
        try:
            selected_tf = self.timeframe_combo.currentText()
            api_interval = self.timeframe_map.get(selected_tf, "minute")
            to_date, from_date = datetime.now().date(), datetime.now().date() - timedelta(days=15)
            hist_data = self.kite.historical_data(token, from_date, to_date, api_interval)
            if not hist_data: raise ValueError("No historical data from API.")
            df = pd.DataFrame(hist_data)
            df.dropna(inplace=True)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            if df.index.tz is not None: df.index = df.index.tz_localize(None)
            unique_dates = sorted(pd.Series(df.index.date).unique())
            cpr_levels, day_separator_pos = None, None
            if len(unique_dates) < 2:
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

    def _connect_signals(self):
        self.load_button.clicked.connect(self._load_charts_data)
        self.save_set_button.clicked.connect(self._save_current_set)
        self.set_selector_combo.currentIndexChanged.connect(self._on_set_selected)
        self.candle_count_combo.currentTextChanged.connect(self._on_candle_count_changed)
        self.timeframe_combo.currentTextChanged.connect(self._load_charts_data)

    def _apply_styles(self):
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
        upper_symbol = symbol.strip().upper()
        alias_map = {'NIFTY': 'NIFTY 50', 'BANKNIFTY': 'NIFTY BANK', 'FINNIFTY': 'NIFTY FIN SERVICE'}
        return self.symbol_to_token_map.get(alias_map.get(upper_symbol, upper_symbol))

    def _on_candle_count_changed(self, text: str):
        for chart in self.charts:
            chart.set_visible_range(text)

    def _load_charts_data(self):
        self.unsubscribe_all()
        self.token_to_chart_map.clear()
        symbols = [s.strip() for s in self.symbols_entry.text().strip().split(',') if s.strip()]
        if not symbols: return
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
        if tokens_to_subscribe: self._subscribe_to(tokens_to_subscribe)
        self.load_button.setEnabled(True)
        self.load_button.setText("Load Charts")

    def _on_ticks_received(self, ticks: list[dict]):
        for tick in ticks:
            token = tick.get("instrument_token")
            if token in self.token_to_chart_map:
                chart = self.token_to_chart_map[token]
                chart.add_tick(tick)
                # print(f"Tick routed to {chart.symbol}: LTP = {tick.get('last_price')}")

    def _subscribe_to(self, tokens: Set[int]):
        if not tokens:
            return
        self.market_data_worker.set_instruments(tokens, append=True)  # ✅ FIXED
        logger.info(f"Market Monitor subscribed to tokens: {tokens}")

    def unsubscribe_all(self):
        if self.market_data_worker and self.token_to_chart_map:
            print("[MarketMonitor] unsubscribe_all called")
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
            dlg = AddSymbolSetDialog(self)
            if dlg.exec() == QDialog.Accepted:
                data = dlg.get_data()
                if data["name"] and data["symbols"]:
                    self.symbol_sets.append(data)
                    self.config_manager.save_market_monitor_sets(self.symbol_sets)
                    self._load_and_populate_sets()
            return

        if text.startswith("🗑️"):
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
        if (idx := self.set_selector_combo.currentIndex()) <= 0: return
        if not (symbols := self.symbols_entry.text().strip()): return
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
            self.config_manager.save_dialog_state('market_monitor',
                                                  self.saveGeometry().toBase64().data().decode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to save dialog state: {e}")
        print("[MarketMonitor] Dialog closed")
        self.market_data_worker.data_received.disconnect(self._on_ticks_received)
        super().closeEvent(event)