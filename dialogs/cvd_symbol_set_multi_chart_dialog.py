# dialogs/cvd_symbol_set_multi_chart_dialog.py

import logging
from datetime import datetime
from typing import List

import pandas as pd
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox
)
from PySide6.QtCore import Qt, QTimer

from core.cvd.cvd_symbol_sets import CVDSymbolSetManager
from core.cvd.cvd_chart_widget import CVDChartWidget
from dialogs.cvd_multi_chart_dialog import DateNavigator
from dialogs.cvd_symbol_sets_dialog import ManageCVDSymbolSetsDialog

logger = logging.getLogger(__name__)


class CVDSetMultiChartDialog(QDialog):
    """
    CVD Multi Chart for user-defined SYMBOL SETS (stocks).

    - Same layout as existing CVDMultiChartDialog
    - Independent token lifecycle
    - Uses nearest FUT for each symbol
    - No impact on other CVD dialogs
    """

    MAX_CHARTS = 4

    def __init__(
        self,
        kite,
        symbol_set_manager: CVDSymbolSetManager,
        resolve_fut_token_fn,
        register_token_fn,
        unregister_tokens_fn,
        parent=None,
    ):
        super().__init__(parent)

        self.kite = kite
        self.symbol_set_manager = symbol_set_manager
        self.current_date = None
        self.previous_date = None

        # Callbacks into main window (keeps this dialog clean)
        self._resolve_fut_token = resolve_fut_token_fn
        self._register_token = register_token_fn
        self._unregister_tokens = unregister_tokens_fn

        self.chart_widgets: List[CVDChartWidget] = []
        self.active_tokens: set[int] = set()

        self.setWindowTitle("CVD Symbol Set Monitor")
        self.setMinimumSize(1300, 720)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )

        self._setup_ui()
        self._load_sets()
        self._pending_chart_reload = 0

    # ------------------------------------------------------------------

    def _setup_ui(self):
        # =========================
        # Root layout
        # =========================
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(8, 8, 8, 8)

        # =========================
        # Header
        # =========================
        header = QHBoxLayout()
        header.setSpacing(8)

        # ---- Symbol Set Label ----
        label = QLabel("Symbol Set")
        label.setStyleSheet("color:#A9B1C3; font-size:11px;")
        header.addWidget(label)

        # ---- Symbol Set Combo ----
        self.set_combo = QComboBox()
        self.set_combo.currentIndexChanged.connect(self._on_set_changed)
        self.set_combo.setMinimumWidth(220)
        self.set_combo.setStyleSheet("""
            QComboBox {
                background-color: #1E2230;
                border: 1px solid #3A4458;
                border-radius: 6px;
                padding: 4px 8px;
                color: #E0E6F1;
                font-size: 11px;
            }
            QComboBox::drop-down {
                border: none;
            }
        """)
        header.addWidget(self.set_combo)

        # ---- Manage Sets (secondary action) ----
        manage_btn = QPushButton("Manage")
        manage_btn.clicked.connect(self._open_manage_sets_dialog)
        manage_btn.setToolTip("Create / Edit Symbol Sets")
        manage_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 1px solid #3A4458;
                border-radius: 6px;
                padding: 4px 10px;
                color: #A9B1C3;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2A2F44;
                color: #FFFFFF;
            }
        """)
        header.addWidget(manage_btn)

        # ---- Reload (utility action) ----
        reload_btn = QPushButton("↻")
        reload_btn.clicked.connect(self._reload_current_set)
        reload_btn.setToolTip("Reload current symbol set")
        reload_btn.setFixedWidth(28)
        reload_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 1px solid #3A4458;
                border-radius: 6px;
                color: #A9B1C3;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #2A2F44;
                color: #FFFFFF;
            }
        """)
        header.addWidget(reload_btn)

        header.addStretch()

        # ---- Aggregate toggle (view mode) ----
        self.aggregate_toggle = QPushButton("Aggregate CVD")
        self.aggregate_toggle.setCheckable(True)
        self.aggregate_toggle.setToolTip("Toggle aggregate CVD view")
        self.aggregate_toggle.setStyleSheet("""
            QPushButton {
                background-color: #1E2230;
                border: 1px solid #3A4458;
                border-radius: 14px;
                padding: 5px 14px;
                color: #A9B1C3;
                font-size: 11px;
            }
            QPushButton:checked {
                background-color: #2A3B5C;
                border-color: #4C6FFF;
                color: #FFFFFF;
            }
            QPushButton:hover {
                border-color: #4C6FFF;
            }
        """)
        self.aggregate_toggle.toggled.connect(self._toggle_aggregate_mode)
        header.addWidget(self.aggregate_toggle)

        root.addLayout(header)

        # =========================
        # Date navigator
        # =========================
        self.navigator = DateNavigator(self)
        self.navigator.date_changed.connect(self._on_date_changed)
        root.addWidget(self.navigator)

        # =========================
        # Charts grid
        # =========================
        self.grid = QGridLayout()
        self.grid.setSpacing(8)

        # ---- Individual CVD charts (passive on creation) ----
        self.chart_widgets = []

        for i in range(self.MAX_CHARTS):
            widget = CVDChartWidget(
                kite=self.kite,
                instrument_token=None,
                symbol="",
                parent=self
            )
            widget.crosshair_moved.connect(self._on_crosshair_sync)
            widget.stop_updates()  # ensure no timers
            widget.hide()

            self.chart_widgets.append(widget)
            self.grid.addWidget(widget, i // 2, i % 2)

        # ---- Aggregate CVD chart (render-only) ----
        self.aggregate_chart = CVDChartWidget(
            kite=self.kite,
            instrument_token=None,
            symbol="AGGREGATE CVD",
            parent=self
        )
        self.aggregate_chart.stop_updates()  # CRITICAL: never run timers
        self.aggregate_chart.hide()

        # Span across entire grid (2x2)
        self.grid.addWidget(self.aggregate_chart, 0, 0, 2, 2)

        # Attach grid AFTER all widgets are added
        root.addLayout(self.grid)

        # =========================
        # Status bar
        # =========================
        self.status = QLabel("Ready")
        self.status.setStyleSheet("color:#888; font-size:10px;")
        root.addWidget(self.status)

    def _on_crosshair_sync(self, x_index: int, timestamp: datetime):
        """
        Synchronize crosshair across all visible charts.
        """
        for w in self.chart_widgets:
            if w.isVisible():
                w.update_crosshair(x_index, timestamp)

        # Aggregate chart should follow, never lead
        if self.aggregate_chart.isVisible():
            self.aggregate_chart.update_crosshair(x_index, timestamp)

    # ------------------------------------------------------------------
    def _open_manage_sets_dialog(self):

        dlg = ManageCVDSymbolSetsDialog(
            symbol_set_manager=self.symbol_set_manager,
            parent=self
        )

        dlg.symbol_sets_updated.connect(self._on_symbol_sets_updated)

        dlg.exec()

    def _load_sets(self):
        self.set_combo.blockSignals(True)
        self.set_combo.clear()

        self.set_combo.addItem("Select Symbol Set")
        self.symbol_sets = self.symbol_set_manager.load_sets()

        for s in self.symbol_sets:
            self.set_combo.addItem(s.get("name", "Unnamed"))

        self.set_combo.blockSignals(False)

        # Auto-select first set AND trigger load
        if self.symbol_sets:
            self.set_combo.setCurrentIndex(1)
            self._on_set_changed(1)

    def _on_symbol_sets_updated(self):
        """
        Reload symbol sets after save/delete without reopening dialog.
        """
        current_text = self.set_combo.currentText()

        self._load_sets()

        # Try to restore previous selection
        index = self.set_combo.findText(current_text)
        if index >= 0:
            self.set_combo.setCurrentIndex(index)

    # ------------------------------------------------------------------
    def _toggle_aggregate_mode(self, enabled: bool):
        if enabled:
            for w in self.chart_widgets:
                w.hide()

            self._refresh_aggregate_chart()
            self.aggregate_chart.show()
        else:
            self.aggregate_chart.hide()

            for w in self.chart_widgets:
                if w.instrument_token:
                    w.show()

    def _show_aggregate_chart(self):
        # Hide individual charts
        for w in self.chart_widgets:
            w.hide()

        # Build aggregate CVD
        agg_df = self._build_aggregate_cvd()
        if agg_df is None or agg_df.empty:
            return

        # Inject data into aggregate widget
        self.aggregate_chart.stop_updates()
        self.aggregate_chart.cvd_df = agg_df
        self.aggregate_chart.prev_day_close_cvd = 0.0
        self.aggregate_chart.rebased_mode = True
        self.aggregate_chart.symbol = "AGGREGATE CVD"
        self.aggregate_chart.title_label.setText("AGGREGATE CVD (Rebased)")
        self.aggregate_chart._plot()

        self.aggregate_chart.show()

    def _show_separate_charts(self):
        self.aggregate_chart.hide()

        for w in self.chart_widgets:
            if w.instrument_token:
                w.show()

    def _build_aggregate_cvd(self):
        """
        Build aggregate CVD using the SAME sessions and dates
        as the individual charts.
        """
        dfs = []

        for w in self.chart_widgets:
            if w.cvd_df is not None and not w.cvd_df.empty:
                dfs.append(w.cvd_df)

        if not dfs:
            return None

        # --- Find common timestamps across all charts ---
        common_index = dfs[0].index
        for df in dfs[1:]:
            common_index = common_index.intersection(df.index)

        if common_index.empty:
            return None

        # --- Sum CLOSE values ---
        agg_close = None
        for df in dfs:
            series = df.loc[common_index, "close"]
            agg_close = series if agg_close is None else agg_close + series

        # --- Build aggregate dataframe ---
        agg_df = pd.DataFrame(index=common_index)
        agg_df["close"] = agg_close
        agg_df["open"] = agg_close
        agg_df["high"] = agg_close
        agg_df["low"] = agg_close
        agg_df["session"] = agg_df.index.date

        return agg_df

    def _refresh_aggregate_chart(self):
        agg_df = self._build_aggregate_cvd()
        if agg_df is None or agg_df.empty:
            return

        self.aggregate_chart.stop_updates()
        self.aggregate_chart.cvd_df = agg_df
        self.aggregate_chart.prev_day_close_cvd = 0.0
        self.aggregate_chart.rebased_mode = True

        self.aggregate_chart.symbol = "AGGREGATE CVD"
        self.aggregate_chart.title_label.setText("AGGREGATE CVD (Rebased)")

        #  Force aggregate to follow same mode as children
        self.aggregate_chart.live_mode = False
        self.aggregate_chart.current_date = self.current_date
        self.aggregate_chart.previous_date = self.previous_date

        self.aggregate_chart._plot()

    def _on_set_changed(self, index: int):
        if index <= 0:
            self._clear_charts()
            return

        set_data = self.symbol_sets[index - 1]
        symbols = set_data.get("symbols", [])[:self.MAX_CHARTS]
        logger.info(f"[CVD-SET] Loading set: {set_data.get('name')} -> {symbols}")

        self._activate_symbols(symbols)

        cur, prev = self.navigator.get_dates()
        self._load_charts_for_dates(cur, prev)

    # ------------------------------------------------------------------

    def _activate_symbols(self, symbols: List[str]):
        self._clear_charts()

        for i, symbol in enumerate(symbols):
            token = self._resolve_fut_token(symbol)
            if not token:
                logger.warning(f"[CVD-SET] No FUT for {symbol}")
                continue

            self._register_token(token)
            self.active_tokens.add(token)
            logger.info(
                f"[CVD-SET] Activating {symbol} → token {token}"
            )

            widget = self.chart_widgets[i]
            widget.set_instrument(token, f"{symbol}")
            widget.show()

        self.status.setText(f"Loaded {len(self.active_tokens)} symbols")

    # ------------------------------------------------------------------

    def _load_charts_for_dates(self, current_date, previous_date):
        # Count how many charts will reload
        visible_charts = [
            w for w in self.chart_widgets
            if w.isVisible() and hasattr(w, "load_historical_dates")
        ]

        self._pending_chart_reload = len(visible_charts)

        if self._pending_chart_reload == 0:
            return

        for widget in visible_charts:
            # Wrap reload completion
            self._reload_chart_with_callback(
                widget, current_date, previous_date
            )

    def _reload_chart_with_callback(self, widget, current_date, previous_date):
        """
        Reload a chart and notify dialog when done.
        """

        # Call chart reload
        widget.load_historical_dates(current_date, previous_date)

        # Poll until data is ready (lightweight & safe)
        def check_ready():
            if widget.cvd_df is not None and not widget.cvd_df.empty:
                self._pending_chart_reload -= 1

                # When ALL charts finished → rebuild aggregate
                if self._pending_chart_reload == 0:
                    if self.aggregate_toggle.isChecked():
                        self._refresh_aggregate_chart()
            else:
                QTimer.singleShot(30, check_ready)

        QTimer.singleShot(30, check_ready)

    # ------------------------------------------------------------------

    def _reload_current_set(self):
        idx = self.set_combo.currentIndex()
        if idx > 0:
            self._on_set_changed(idx)

    # ------------------------------------------------------------------

    def _on_date_changed(self, current_date, previous_date):
        # Store canonical dates
        self.current_date = current_date
        self.previous_date = previous_date

        self.status.setText(
            f"Loading {previous_date.date()} → {current_date.date()}"
        )

        # Update individual charts
        QTimer.singleShot(
            10,
            lambda: self._load_charts_for_dates(current_date, previous_date)
        )


    # ------------------------------------------------------------------

    def _clear_charts(self):
        if self.active_tokens:
            self._unregister_tokens(self.active_tokens)
            self.active_tokens.clear()

        for widget in self.chart_widgets:
            widget.stop_updates()
            widget.hide()

    # ------------------------------------------------------------------

    def closeEvent(self, event):
        logger.info("[CVD-SET] Closing dialog")
        self._clear_charts()
        super().closeEvent(event)
