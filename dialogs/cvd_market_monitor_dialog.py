from PySide6.QtWidgets import QDialog, QGridLayout
from PySide6.QtCore import Qt
import logging

from widgets.cvd_chart_widget import CVDChartWidget

logger = logging.getLogger(__name__)


class CVDMarketMonitorDialog(QDialog):
    """
    Market-Monitor-style dialog for CVD charts (2x2 grid),
    implemented using CVDChartWidget (NOT dialogs).
    """

    def __init__(
        self,
        kite,
        cvd_engine,
        symbol_to_token: dict,
        parent=None
    ):
        super().__init__(parent)

        self.kite = kite
        self.cvd_engine = cvd_engine
        self.symbol_to_token = symbol_to_token

        self.setWindowTitle("CVD Market Monitor")
        self.setMinimumSize(1200, 700)
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )

        self._setup_ui()

    def _setup_ui(self):
        layout = QGridLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        symbols = list(self.symbol_to_token.keys())

        for idx, symbol in enumerate(symbols):
            token = self.symbol_to_token.get(symbol)
            if not token:
                continue

            widget = CVDChartWidget(
                kite=self.kite,
                instrument_token=token,
                cvd_engine=self.cvd_engine,
                symbol=f"{symbol} FUT",
                parent=self
            )

            row = idx // 2
            col = idx % 2
            layout.addWidget(widget, row, col)
