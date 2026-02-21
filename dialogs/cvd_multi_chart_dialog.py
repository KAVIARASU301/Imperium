import logging
from datetime import datetime, timedelta
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QWidget
)
from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QFont

from widgets.cvd_chart_widget import CVDChartWidget

logger = logging.getLogger(__name__)


class DateNavigator(QWidget):
    """Professional date navigator with forward/backward controls."""

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

        # Backward button
        self.btn_back = QPushButton("◀")
        self.btn_back.setFixedSize(40, 32)
        self.btn_back.setToolTip("Previous trading day")
        self.btn_back.clicked.connect(self._go_backward)

        # Date display
        self.lbl_dates = QLabel()
        self.lbl_dates.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        self.lbl_dates.setFont(font)
        self.lbl_dates.setMinimumWidth(500)

        # Forward button
        self.btn_forward = QPushButton("▶")
        self.btn_forward.setFixedSize(40, 32)
        self.btn_forward.setToolTip("Next trading day")
        self.btn_forward.clicked.connect(self._go_forward)

        # Spacer
        layout.addStretch()
        layout.addWidget(self.btn_back)
        layout.addWidget(self.lbl_dates)
        layout.addWidget(self.btn_forward)
        layout.addStretch()

    def _get_previous_trading_day(self, date: datetime) -> datetime:
        """Get previous trading day (skip weekends)."""
        prev = date - timedelta(days=1)
        # Skip weekends
        while prev.weekday() >= 5:  # Saturday=5, Sunday=6
            prev -= timedelta(days=1)
        return prev

    def _get_next_trading_day(self, date: datetime) -> datetime:
        """Get next trading day (skip weekends)."""
        next_day = date + timedelta(days=1)
        # Skip weekends
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)
        return next_day

    def _update_display(self):
        """Update date labels."""
        previous = self._get_previous_trading_day(self._current_date)

        current_str = self._current_date.strftime("%A, %b %d, %Y")
        previous_str = previous.strftime("%A, %b %d, %Y")

        text = (
            f"<span style='color: #0088ff;'>Previous: {previous_str}</span>  |  "
            f"<span style='color: #26A69A;'>Current: {current_str}</span>"
        )

        self.lbl_dates.setText(text)

        # Disable forward if at today
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.btn_forward.setEnabled(self._current_date < today)

    def _go_backward(self):
        """Navigate to previous trading day."""
        self._current_date = self._get_previous_trading_day(self._current_date)
        self._update_display()
        previous = self._get_previous_trading_day(self._current_date)
        self.date_changed.emit(self._current_date, previous)

    def _go_forward(self):
        """Navigate to next trading day."""
        self._current_date = self._get_next_trading_day(self._current_date)
        self._update_display()
        previous = self._get_previous_trading_day(self._current_date)
        self.date_changed.emit(self._current_date, previous)

    def get_dates(self) -> tuple[datetime, datetime]:
        """Get current and previous dates."""
        previous = self._get_previous_trading_day(self._current_date)
        return self._current_date, previous


class CVDMultiChartDialog(QDialog):
    """
    Professional-grade CVD Market Monitor with synchronized crosshairs
    and date navigation.

    Features:
    - 2x2 grid of CVD charts
    - Synchronized crosshair across all charts
    - Date navigation (forward/backward)
    - Weekend-aware trading day calculation
    - Optimized chart updates
    """

    def __init__(self, kite, symbol_to_token: dict, parent=None):
        super().__init__(parent)

        self.kite = kite
        self.symbol_to_token = symbol_to_token or {}
        self.chart_widgets = []

        self.setWindowTitle("CVD Multi Chart Monitor")
        self.setMinimumSize(1300, 700)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )

        self._setup_ui()
        self._connect_crosshairs()
        self._setup_refresh_timer()

        # Initialize with current dates
        current_date, previous_date = self.navigator.get_dates()

        # Ensure live mode on open
        for widget in self.chart_widgets:
            widget.live_mode = True

        self._load_all_charts(current_date, previous_date)

    def _setup_ui(self):
        """Setup the UI layout."""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # Header with navigation
        self.navigator = DateNavigator(self)
        self.navigator.date_changed.connect(self._on_date_changed)
        main_layout.addWidget(self.navigator)

        # Charts grid
        grid_layout = QGridLayout()
        grid_layout.setSpacing(8)
        grid_layout.setContentsMargins(0, 8, 0, 0)

        if not self.symbol_to_token:
            logger.warning("CVD Multi Chart opened with empty symbol list")
            return

        symbols = list(self.symbol_to_token.keys())[:4]  # Max 4 charts

        for idx, symbol in enumerate(symbols):
            instrument_token = self.symbol_to_token.get(symbol)

            if not instrument_token:
                logger.warning(f"Missing instrument token for symbol: {symbol}")
                continue

            try:
                widget = CVDChartWidget(
                    kite=self.kite,
                    instrument_token=instrument_token,
                    symbol=f"{symbol} FUT",
                    parent=self,
                    auto_refresh=False,
                )

                row = idx // 2
                col = idx % 2
                grid_layout.addWidget(widget, row, col)
                self.chart_widgets.append(widget)

            except Exception:
                logger.exception(f"Failed to create CVD widget for {symbol}")

        main_layout.addLayout(grid_layout)

        # Status bar
        self._setup_status_bar(main_layout)

    def _setup_status_bar(self, parent_layout):
        """Add status bar with info."""
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(4, 4, 4, 4)

        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet("color: #888; font-size: 10px;")

        status_layout.addWidget(self.lbl_status)
        status_layout.addStretch()

        parent_layout.addLayout(status_layout)

    def _setup_refresh_timer(self):
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_live_charts)
        self.refresh_timer.start(CVDChartWidget.REFRESH_INTERVAL_MS)

    def _refresh_live_charts(self):
        if not self.isVisible() or not self.isActiveWindow():
            return
        for widget in self.chart_widgets:
            widget.refresh_if_live(force=True)

    def _connect_crosshairs(self):
        """Connect crosshair signals between all charts."""
        if len(self.chart_widgets) < 2:
            return

        # Connect each chart's crosshair to all others
        for source_widget in self.chart_widgets:
            # Assuming CVDChartWidget has a crosshair_moved signal
            # Signal should emit (x_value, y_value) or (timestamp, price)
            if hasattr(source_widget, 'crosshair_moved'):
                for target_widget in self.chart_widgets:
                    if source_widget != target_widget:
                        source_widget.crosshair_moved.connect(
                            target_widget.update_crosshair
                        )

        logger.info(f"Synchronized crosshairs across {len(self.chart_widgets)} charts")

    def _on_date_changed(self, current_date: datetime, previous_date: datetime):
        """Handle date navigation."""
        self.lbl_status.setText(f"Loading data for {current_date.strftime('%Y-%m-%d')}...")

        # Use QTimer to update UI before loading
        QTimer.singleShot(10, lambda: self._load_all_charts(current_date, previous_date))

    def _load_all_charts(self, current_date: datetime, previous_date: datetime):
        """Load historical data for all charts."""
        for widget in self.chart_widgets:
            try:
                if hasattr(widget, 'load_historical_dates'):
                    widget.load_historical_dates(current_date, previous_date)
            except Exception:
                logger.exception(f"Failed to load data for widget")

        self.lbl_status.setText(
            f"Loaded: {previous_date.strftime('%Y-%m-%d')} → {current_date.strftime('%Y-%m-%d')}"
        )

    def closeEvent(self, event):
        """Cleanup on close."""
        logger.info("Closing CVD Market Monitor")

        # Stop any timers in chart widgets
        for widget in self.chart_widgets:
            if hasattr(widget, 'stop_updates'):
                widget.stop_updates()
        if hasattr(self, "refresh_timer"):
            self.refresh_timer.stop()

        super().closeEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                if not self.refresh_timer.isActive():
                    self.refresh_timer.start(CVDChartWidget.REFRESH_INTERVAL_MS)
            else:
                self.refresh_timer.stop()
        super().changeEvent(event)
