"""Price & CVD chart dialog rendered from the bundled HTML file."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

logger = logging.getLogger(__name__)


class PriceCVDChartDialog(QDialog):
    """Dialog that hosts the Price/CVD chart HTML in a web view."""

    def __init__(
        self,
        kite,
        instrument_token,
        symbol,
        cvd_engine=None,
        price_instrument_token=None,
        parent=None,
    ):
        super().__init__(parent)
        self.kite = kite
        self.instrument_token = instrument_token
        self.symbol = symbol
        self.cvd_engine = cvd_engine
        self.price_instrument_token = price_instrument_token or instrument_token

        self.setWindowTitle(f"Price & CVD Chart — {symbol}")
        self.resize(1320, 780)
        self.setMinimumSize(900, 560)
        self.setStyleSheet("background:#0B0F1A;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        html_path = Path(__file__).with_name("price_cvd_chart.html")
        if not html_path.exists():
            logger.error("[PriceCVDChart] Missing HTML file: %s", html_path)
            missing = QLabel(f"Missing chart file:\n{html_path}")
            missing.setStyleSheet("color:#D8E0F0; padding: 12px;")
            root.addWidget(missing)
            return

        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView

            web_view = QWebEngineView(self)
            web_view.setContextMenuPolicy(Qt.NoContextMenu)
            web_view.setUrl(QUrl.fromLocalFile(str(html_path.resolve())))
            root.addWidget(web_view)
            self._web_view = web_view
            logger.info("[PriceCVDChart] Loaded HTML chart from %s", html_path)
        except Exception:
            logger.exception("[PriceCVDChart] Failed to initialise web view")
            err = QLabel("Unable to initialize embedded chart view.")
            err.setStyleSheet("color:#EF5350; padding: 12px;")
            root.addWidget(err)
