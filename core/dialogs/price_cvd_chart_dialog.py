"""Price & CVD chart dialog rendered from the bundled HTML file."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
import json

from PySide6.QtCore import Qt, QUrl, QSettings, QByteArray
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

logger = logging.getLogger(__name__)




class PriceCVDChartDialog(QDialog):
    """Dialog that hosts the Price/CVD chart HTML in a web view."""

    # Compact default — user can maximise via in-chart controls or OS chrome
    _DEFAULT_W = 860
    _DEFAULT_H = 500

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

        # Promote to a full top-level window so the OS decorates it with
        # Minimize / Maximize / Close buttons in the native title bar.
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )

        self.setWindowTitle(f"Price & CVD — {symbol}")
        self.resize(self._DEFAULT_W, self._DEFAULT_H)
        self.setMinimumSize(620, 380)
        self.setStyleSheet("background:#0B0F1A;")

        # ── Restore previous window geometry ──
        self._settings_key = f"PriceCVDChart/{symbol}"
        self._restore_geometry()

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
            web_view.loadFinished.connect(self._on_web_view_loaded)
            root.addWidget(web_view)
            self._web_view = web_view
            logger.info("[PriceCVDChart] Loaded HTML chart from %s", html_path)
        except Exception:
            logger.exception("[PriceCVDChart] Failed to initialise web view")
            err = QLabel("Unable to initialize embedded chart view.")
            err.setStyleSheet("color:#EF5350; padding: 12px;")
            root.addWidget(err)

    # ── Window geometry persistence ────────────────────────────────────────

    def _qsettings(self) -> QSettings:
        return QSettings("TradingTerminal", "PriceCVDChart")

    def _restore_geometry(self) -> None:
        qs = self._qsettings()
        geom: QByteArray = qs.value(f"{self._settings_key}/geometry")  # type: ignore[assignment]
        if geom and not geom.isEmpty():
            self.restoreGeometry(geom)
            logger.debug("[PriceCVDChart] Geometry restored for %s", self.symbol)

    def _save_geometry(self) -> None:
        qs = self._qsettings()
        qs.setValue(f"{self._settings_key}/geometry", self.saveGeometry())
        qs.sync()
        logger.debug("[PriceCVDChart] Geometry saved for %s", self.symbol)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_geometry()
        super().closeEvent(event)

    # ── Historical data ──────────────────────────────────────────────────
    def _fetch_historical(self, token: int, from_dt: datetime, to_dt: datetime) -> list[dict]:
        if not self.kite or not token:
            return []
        try:
            data = self.kite.historical_data(token, from_dt, to_dt, interval="minute")
            if isinstance(data, list):
                return data
        except Exception:
            logger.exception("[PriceCVDChart] Historical fetch failed for token=%s", token)
        return []

    @staticmethod
    def _normalize_rows(rows: list[dict]) -> list[dict]:
        normalized = []
        for r in rows:
            dt = r.get("date")
            if not dt:
                continue
            try:
                if hasattr(dt, "isoformat"):
                    ts = dt.isoformat()
                else:
                    ts = str(dt)
            except Exception:
                continue
            normalized.append({
                "date": ts,
                "o": float(r.get("open", 0.0)),
                "h": float(r.get("high", 0.0)),
                "l": float(r.get("low", 0.0)),
                "c": float(r.get("close", 0.0)),
                "v": float(r.get("volume", 0.0)),
            })
        return normalized

    def _on_web_view_loaded(self, ok: bool):
        if not ok:
            logger.warning("[PriceCVDChart] Web view failed to load for %s", self.symbol)
            return

        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=8)

            price_rows = self._fetch_historical(self.price_instrument_token, from_dt, to_dt)
            cvd_rows = self._fetch_historical(self.instrument_token, from_dt, to_dt)

            payload = {
                "price": self._normalize_rows(price_rows),
                "cvd": self._normalize_rows(cvd_rows),
            }
            js = (
                f"window.__PRICE_CVD_REAL_DATA__ = {json.dumps(payload)};"
                "if (typeof window.__reloadPriceCvdData === 'function') window.__reloadPriceCvdData();"
            )
            self._web_view.page().runJavaScript(js)
            logger.info(
                "[PriceCVDChart] Injected real data for %s (price=%d, cvd=%d)",
                self.symbol,
                len(payload["price"]),
                len(payload["cvd"]),
            )
        except Exception:
            logger.exception("[PriceCVDChart] Failed to inject real chart data for %s", self.symbol)