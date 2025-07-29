# dialogs/market_monitor_dialog.py
import logging
from datetime import datetime, timedelta
from typing import Dict, List
import pandas as pd
from PySide6.QtCore import Qt, QByteArray, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QWidget,
                               QLabel, QLineEdit, QPushButton, QMessageBox, QComboBox,
                               QSizePolicy, QFrame, QSpacerItem)
from kiteconnect import KiteConnect

from widgets.market_monitor_widget import MarketChartWidget
from utils.config_manager import ConfigManager
from utils.cpr_calculator import CPRCalculator
from core.market_data_worker import MarketDataWorker

logger = logging.getLogger(__name__)


class MarketMonitorDialog(QDialog):
    def __init__(self, real_kite_client: KiteConnect, api_key: str, access_token: str,
                 config_manager: ConfigManager, parent=None):

        super().__init__(parent)
        self.kite = real_kite_client
        self.config_manager = config_manager

        self.market_data_worker = MarketDataWorker(api_key, access_token)
        self.market_data_worker.data_received.connect(self._on_ticks_received)
        self.market_data_worker.start()

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

        self.symbols_entry.setText("NIFTY 50, NIFTY BANK, FINNIFTY, SENSEX")
        QTimer.singleShot(100, self._load_charts_data)

    def _setup_window(self):
        self.setWindowTitle("Market Monitor")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(1200, 700)
        self.setMinimumSize(960, 600)
        self.setObjectName("MarketMonitorDialog")  # Set object name for specific styling

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
        if self.market_data_worker:
            self.market_data_worker.data_received.connect(self._on_ticks_received)

    def _apply_styles(self):
        """Applies a custom dark theme stylesheet to the dialog."""
        STYLE_SHEET = """
        QDialog#MarketMonitorDialog {
            background-color: #2C2C2C;
        }

        QLabel {
            color: #E0E0E0;
            font-size: 12px;
        }

        /* Style for Buttons */
        QPushButton {
            background-color: #424242;
            color: #FFFFFF;
            border: 1px solid #555555;
            padding: 6px 14px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #555555;
            border: 1px solid #666666;
        }
        QPushButton:pressed {
            background-color: #383838;
        }
        QPushButton:disabled {
            background-color: #3A3A3A;
            color: #888888;
            border-color: #444444;
        }

        /* Style for Dropdown Menus */
        QComboBox {
            background-color: #424242;
            color: #E0E0E0;
            border: 1px solid #555555;
            padding: 6px;
            padding-left: 12px;
            border-radius: 4px;
            font-size: 12px;
        }
        QComboBox:hover {
            border: 1px solid #666666;
        }
        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 22px;
            border-left-width: 1px;
            border-left-color: #555555;
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }
        QComboBox QAbstractItemView {
            background-color: #3A3A3A;
            color: #E0E0E0;
            border: 1px solid #555555;
            selection-background-color: #007ACC;
            outline: 0px;
        }

        /* Style for Text Input */
        QLineEdit {
            background-color: #3A3A3A;
            color: #E0E0E0;
            border: 1px solid #555555;
            border-radius: 4px;
            padding: 7px;
            font-size: 12px;
        }
        QLineEdit:focus {
            border: 1px solid #007ACC;
        }

        /* Style for the Separator Line */
        QFrame[frameShape="4"] { /* QFrame.HLine */
            border: none;
            height: 1px;
            background-color: #4A4A4A;
        }
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
        self._unsubscribe_all()
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

    def _on_ticks_received(self, ticks: List[Dict]):
        for tick in ticks:
            logger.debug(f"MarketMonitor Tick: {tick}")
            if chart := self.token_to_chart_map.get(tick.get('instrument_token')):
                chart.add_tick(tick)

    def _subscribe_to(self, tokens: set):
        if self.market_data_worker:
            # Force-set instruments instead of union (fix for frozen charts)
            self.market_data_worker.set_instruments(tokens)
            logger.info(f"Subscribed to tokens: {tokens}")

    def _unsubscribe_all(self):
        if self.market_data_worker and self.token_to_chart_map: self.market_data_worker.set_instruments(
            self.market_data_worker.subscribed_tokens - set(self.token_to_chart_map.keys()))

    def _load_and_populate_sets(self):
        self.symbol_sets = self.config_manager.load_market_monitor_sets()
        self.set_selector_combo.clear()
        self.set_selector_combo.addItem("Select a Symbol Set")
        for s in self.symbol_sets: self.set_selector_combo.addItem(s.get("name"))

    def _on_set_selected(self, index: int):
        if index > 0 and (index - 1) < len(self.symbol_sets):
            self.symbols_entry.setText(self.symbol_sets[index - 1].get("symbols", ""))
            QTimer.singleShot(50, self._load_charts_data)
        elif index == 0:
            self.symbols_entry.clear()

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
        self._unsubscribe_all()
        super().closeEvent(event)