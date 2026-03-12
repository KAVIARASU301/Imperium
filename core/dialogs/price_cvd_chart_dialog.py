"""Price & CVD chart dialog rendered from the bundled HTML file."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
import json

from PySide6.QtCore import Qt, QUrl, QSettings, QByteArray, QTimer
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
        self._web_ready = False
        self._latest_price: float | None = None
        self._latest_cvd: float | None = None
        self._latest_tick_ts = datetime.now()
        self._live_flush_timer = QTimer(self)
        self._live_flush_timer.setSingleShot(True)
        self._live_flush_timer.setInterval(120)
        self._live_flush_timer.timeout.connect(self._flush_live_tick)
        self._historical_refresh_timer = QTimer(self)
        self._historical_refresh_timer.setSingleShot(True)
        self._historical_refresh_timer.timeout.connect(self._refresh_historical_from_timer)

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

            if self.cvd_engine is not None:
                self.cvd_engine.cvd_updated.connect(self._on_cvd_updated)

            if parent is not None and hasattr(parent, "market_data_worker"):
                try:
                    parent.market_data_worker.data_received.connect(self._on_market_ticks)
                except Exception:
                    logger.debug("[PriceCVDChart] Could not connect to market tick stream", exc_info=True)
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
        self._live_flush_timer.stop()
        self._historical_refresh_timer.stop()
        self._disconnect_live_feeds()
        self._teardown_web_view()
        self._save_geometry()
        super().closeEvent(event)

    def _teardown_web_view(self) -> None:
        web_view = getattr(self, "_web_view", None)
        if web_view is None:
            return

        try:
            web_view.loadFinished.disconnect(self._on_web_view_loaded)
        except Exception:
            pass

        try:
            web_view.setUrl(QUrl("about:blank"))
        except Exception:
            logger.debug("[PriceCVDChart] Failed to blank web view for %s", self.symbol, exc_info=True)

        try:
            web_view.deleteLater()
        finally:
            self._web_view = None

    def _disconnect_live_feeds(self) -> None:
        if self.cvd_engine is not None:
            try:
                self.cvd_engine.cvd_updated.disconnect(self._on_cvd_updated)
            except Exception:
                pass

        parent = self.parent()
        if parent is not None and hasattr(parent, "market_data_worker"):
            try:
                parent.market_data_worker.data_received.disconnect(self._on_market_ticks)
            except Exception:
                pass

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

        self._web_ready = True
        self._refresh_historical_chart_data()
        self._schedule_next_historical_refresh()

    def _refresh_historical_from_timer(self) -> None:
        if not self._web_ready:
            return
        self._refresh_historical_chart_data()
        self._schedule_next_historical_refresh()

    def _schedule_next_historical_refresh(self) -> None:
        now = datetime.now()
        next_minute = (now.replace(second=0, microsecond=0) + timedelta(minutes=1, milliseconds=150))
        interval_ms = max(250, int((next_minute - now).total_seconds() * 1000))
        self._historical_refresh_timer.start(interval_ms)

    def _refresh_historical_chart_data(self) -> None:
        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=8)

            price_rows = self._fetch_historical(self.price_instrument_token, from_dt, to_dt)
            cvd_rows = self._fetch_historical(self.instrument_token, from_dt, to_dt)

            payload = {
                "price": self._normalize_rows(price_rows),
                "cvd": self._normalize_rows(cvd_rows),
            }
            self._inject_payload(payload)
            logger.info(
                "[PriceCVDChart] Injected real data for %s (price=%d, cvd=%d)",
                self.symbol,
                len(payload["price"]),
                len(payload["cvd"]),
            )
        except Exception:
            logger.exception("[PriceCVDChart] Failed to inject real chart data for %s", self.symbol)

    def _inject_payload(self, payload: dict) -> None:
        js = (
            f"window.__PRICE_CVD_REAL_DATA__ = {json.dumps(payload)};"
            "if (typeof window.__reloadPriceCvdData === 'function') window.__reloadPriceCvdData();"
        )
        self._web_view.page().runJavaScript(js)

    def _on_cvd_updated(self, instrument_token: int, cvd_value: float, last_price: float) -> None:
        if instrument_token != self.instrument_token:
            return

        self._latest_cvd = float(cvd_value)
        if self.price_instrument_token == self.instrument_token:
            self._latest_price = float(last_price)
        self._latest_tick_ts = datetime.now()
        self._schedule_live_flush()

    def _on_market_ticks(self, ticks: list[dict]) -> None:
        saw_relevant = False
        for tick in ticks:
            token = tick.get("instrument_token")
            if token == self.price_instrument_token:
                price = tick.get("last_price")
                if price is not None:
                    self._latest_price = float(price)
                    saw_relevant = True

            if token == self.instrument_token:
                ts = tick.get("exchange_timestamp") or tick.get("last_trade_time")
                if hasattr(ts, "replace"):
                    self._latest_tick_ts = ts

        if saw_relevant:
            self._schedule_live_flush()

    def _schedule_live_flush(self) -> None:
        if not self._web_ready:
            return
        if not self._live_flush_timer.isActive():
            self._live_flush_timer.start()

    def _flush_live_tick(self) -> None:
        if not self._web_ready or self._latest_price is None or self._latest_cvd is None:
            return

        payload = {
            "price": self._latest_price,
            "cvd": self._latest_cvd,
            "timestamp": self._latest_tick_ts.isoformat(),
        }
        js = (
            f"if (typeof window.__applyPriceCvdLiveTick === 'function') "
            f"window.__applyPriceCvdLiveTick({json.dumps(payload)});"
        )
        try:
            self._web_view.page().runJavaScript(js)
        except Exception:
            logger.debug("[PriceCVDChart] Failed to push live tick for %s", self.symbol, exc_info=True)
