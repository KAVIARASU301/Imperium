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

            # ── FIX 1: Register the futures token with the CVD engine so it
            #           actually emits cvd_updated for this token. Without this
            #           call the engine silently ignores ticks for the token and
            #           _latest_cvd stays None forever.
            if self.cvd_engine is not None:
                if hasattr(self.cvd_engine, "register_token"):
                    self.cvd_engine.register_token(self.instrument_token)
                    logger.debug(
                        "[PriceCVDChart] Registered cvd_token=%s with CVD engine",
                        self.instrument_token,
                    )
                self.cvd_engine.cvd_updated.connect(self._on_cvd_updated)

            if parent is not None and hasattr(parent, "market_data_worker"):
                try:
                    parent.market_data_worker.data_received.connect(self._on_market_ticks)
                except Exception:
                    logger.debug(
                        "[PriceCVDChart] Could not connect to market tick stream",
                        exc_info=True,
                    )

            # ── FIX 2: Ensure the spot/index price token is subscribed on the
            #           websocket.  main_window only adds cvd_token (futures) to
            #           active_cvd_tokens; the spot token is never included, so
            #           _on_market_ticks never receives price ticks and
            #           _latest_price stays None.
            if (
                self.price_instrument_token != self.instrument_token
                and parent is not None
            ):
                self._subscribe_price_token_on_parent(parent)

        except Exception:
            logger.exception("[PriceCVDChart] Failed to initialise web view")
            err = QLabel("Unable to initialize embedded chart view.")
            err.setStyleSheet("color:#EF5350; padding: 12px;")
            root.addWidget(err)

    # ── Spot-token subscription helpers ─────────────────────────────────────

    def _subscribe_price_token_on_parent(self, parent) -> None:
        """Ask the main window to subscribe the spot/index price token."""
        try:
            if hasattr(parent, "active_cvd_tokens"):
                parent.active_cvd_tokens.add(int(self.price_instrument_token))
            # Trigger a subscription delta so the websocket picks it up.
            if hasattr(parent, "_update_market_subscriptions"):
                parent._update_market_subscriptions()
            elif hasattr(parent, "subscription_policy"):
                parent.subscription_policy.update_market_subscriptions()
            logger.debug(
                "[PriceCVDChart] Requested subscription for price_token=%s",
                self.price_instrument_token,
            )
        except Exception:
            logger.warning(
                "[PriceCVDChart] Could not subscribe price_instrument_token=%s",
                self.price_instrument_token,
                exc_info=True,
            )

    def _unsubscribe_price_token_on_parent(self) -> None:
        """Release the spot/index token subscription when dialog closes."""
        if self.price_instrument_token == self.instrument_token:
            return  # Nothing extra was subscribed
        parent = self.parent()
        if parent is None:
            return
        try:
            # Only discard if no other open PriceCVD dialog still needs it.
            other_dialogs_need_token = any(
                getattr(d, "price_instrument_token", None) == self.price_instrument_token
                and d is not self
                and not d.isHidden()
                for d in getattr(parent, "_price_cvd_chart_dialogs", [])
            )
            if not other_dialogs_need_token and hasattr(parent, "active_cvd_tokens"):
                parent.active_cvd_tokens.discard(int(self.price_instrument_token))
            if hasattr(parent, "_update_market_subscriptions"):
                parent._update_market_subscriptions()
            elif hasattr(parent, "subscription_policy"):
                parent.subscription_policy.update_market_subscriptions()
        except Exception:
            logger.debug(
                "[PriceCVDChart] Could not clean up price_instrument_token=%s subscription",
                self.price_instrument_token,
                exc_info=True,
            )

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
        self._unsubscribe_price_token_on_parent()
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
            logger.debug(
                "[PriceCVDChart] Failed to blank web view for %s", self.symbol, exc_info=True
            )
        try:
            web_view.deleteLater()
        finally:
            self._web_view = None

    def _disconnect_live_feeds(self) -> None:
        if self.cvd_engine is not None:
            # ── FIX 1 (cleanup): Unregister the token so the CVD engine stops
            #           accumulating state for it after the dialog is gone.
            if hasattr(self.cvd_engine, "unregister_token"):
                try:
                    self.cvd_engine.unregister_token(self.instrument_token)
                except Exception:
                    pass
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
            logger.exception(
                "[PriceCVDChart] Historical fetch failed for token=%s", token
            )
        return []

    @staticmethod
    def _normalize_rows(rows: list[dict]) -> list[dict]:
        normalized = []
        for r in rows:
            dt = r.get("date")
            if not dt:
                continue
            try:
                ts = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
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

    def _build_cvd_candles(self, raw_rows: list[dict]) -> list[dict]:
        """Pre-compute CVD OHLC from raw OHLCV using CVDHistoricalBuilder.

        Returns rows where o/h/l/c are CVD values (not price), so the HTML
        can read them directly instead of recomputing from direction × volume.
        Returns empty list on any failure so caller can fall back.
        """
        if not raw_rows:
            return []
        try:
            import pandas as pd
            from core.cvd.cvd_historical import CVDHistoricalBuilder

            df = pd.DataFrame(raw_rows)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            for col in ("open", "high", "low", "close", "volume"):
                if col not in df.columns:
                    return []

            cvd_df = CVDHistoricalBuilder.build_cvd_ohlc(df)
            result = []
            for ts, row in cvd_df.iterrows():
                result.append({
                    "date": ts.isoformat(),
                    "o": float(row["open"]),
                    "h": float(row["high"]),
                    "l": float(row["low"]),
                    "c": float(row["close"]),
                    "v": 0.0,   # not needed for pre-computed CVD
                })
            return result
        except Exception:
            logger.exception("[PriceCVDChart] CVD candle pre-build failed for %s", self.symbol)
            return []

    def _reseed_from_history(self, payload: dict) -> None:
        """Re-seed engine and latest values after every historical refresh.

        Called on every fetch (not just the first) so the per-minute reload
        keeps the engine baseline aligned with the freshly rebuilt chart.
        """
        from datetime import date as _date

        price_rows = payload.get("price", [])
        cvd_rows   = payload.get("cvd", [])

        # ── Price ──────────────────────────────────────────────────────────
        if price_rows:
            last_close = float(price_rows[-1].get("c", 0.0))
            if last_close:
                self._latest_price = last_close

        # ── CVD ────────────────────────────────────────────────────────────
        if not cvd_rows:
            return

        last_cvd_row = cvd_rows[-1]
        last_cvd_value = float(last_cvd_row.get("c", 0.0))

        # Always update — not just when None — so minute-refresh stays aligned.
        self._latest_cvd = last_cvd_value

        # Seed CVD engine so live ticks continue from the historical baseline.
        # seed_from_historical sets last_volume=None so the engine's first-tick
        # handler establishes the correct session-volume baseline on next tick.
        if self.cvd_engine is not None and hasattr(self.cvd_engine, "seed_from_historical"):
            last_price = self._latest_price or 0.0
            try:
                ts_str = last_cvd_row.get("date", "")
                session_day = datetime.fromisoformat(ts_str).date() if ts_str else _date.today()
            except Exception:
                session_day = _date.today()
            try:
                self.cvd_engine.seed_from_historical(
                    token=self.instrument_token,
                    cvd_value=last_cvd_value,
                    last_price=last_price,
                    cumulative_volume=0,    # ignored; engine uses last_volume=None
                    session_day=session_day,
                )
                logger.debug(
                    "[PriceCVDChart] Engine seeded cvd=%.0f for %s",
                    last_cvd_value,
                    self.symbol,
                )
            except Exception:
                logger.debug(
                    "[PriceCVDChart] Engine seed failed for %s",
                    self.symbol,
                    exc_info=True,
                )

    def _on_web_view_loaded(self, ok: bool):
        if not ok:
            logger.warning(
                "[PriceCVDChart] Web view failed to load for %s", self.symbol
            )
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
        next_minute = now.replace(second=0, microsecond=0) + timedelta(
            minutes=1, milliseconds=150
        )
        interval_ms = max(250, int((next_minute - now).total_seconds() * 1000))
        self._historical_refresh_timer.start(interval_ms)

    def _refresh_historical_chart_data(self) -> None:
        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=8)

            price_rows = self._fetch_historical(self.price_instrument_token, from_dt, to_dt)
            cvd_raw_rows = self._fetch_historical(self.instrument_token, from_dt, to_dt)

            # Pre-compute CVD OHLC so the HTML reads CVD values directly.
            # If the builder fails, fall back to raw rows (HTML recomputes).
            cvd_candle_rows = self._build_cvd_candles(cvd_raw_rows) or self._normalize_rows(cvd_raw_rows)

            payload = {
                "price": self._normalize_rows(price_rows),
                "cvd": cvd_candle_rows,
            }
            self._inject_payload(payload)
            # Always re-seed so live ticks continue from the correct CVD baseline.
            self._reseed_from_history(payload)
            logger.info(
                "[PriceCVDChart] Injected real data for %s (price=%d, cvd=%d)",
                self.symbol,
                len(payload["price"]),
                len(payload["cvd"]),
            )
        except Exception:
            logger.exception(
                "[PriceCVDChart] Failed to inject real chart data for %s", self.symbol
            )

    def _inject_payload(self, payload: dict) -> None:
        js = (
            f"window.__PRICE_CVD_REAL_DATA__ = {json.dumps(payload)};"
            "if (typeof window.__reloadPriceCvdData === 'function') window.__reloadPriceCvdData();"
        )
        self._web_view.page().runJavaScript(js)

    # ── Live tick handlers ────────────────────────────────────────────────

    def _on_cvd_updated(self, instrument_token: int, cvd_value: float, last_price: float) -> None:
        if instrument_token != self.instrument_token:
            return

        self._latest_cvd = float(cvd_value)

        if self.price_instrument_token == self.instrument_token:
            # Same token: price comes directly from CVD engine.
            self._latest_price = float(last_price)
        elif self._latest_price is None:
            # ── FIX 3b: When price token differs (spot vs futures) and the
            #            spot tick hasn't arrived yet, bootstrap from the
            #            futures last_price so we don't stay stuck at None.
            self._latest_price = float(last_price)
            logger.debug(
                "[PriceCVDChart] Bootstrapped _latest_price=%.4f from futures "
                "tick while waiting for spot token=%s",
                self._latest_price,
                self.price_instrument_token,
            )

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

                # ── FIX 3c: Pull current CVD from engine inline if the
                #            cvd_updated signal hasn't fired yet (e.g. engine
                #            batches updates or token wasn't registered in time).
                if self._latest_cvd is None and self.cvd_engine is not None:
                    current_cvd = None
                    if hasattr(self.cvd_engine, "get_current_cvd"):
                        current_cvd = self.cvd_engine.get_current_cvd(token)
                    elif hasattr(self.cvd_engine, "_cvd_state"):
                        # Fallback: read internal state dict directly
                        state = self.cvd_engine._cvd_state.get(token)
                        if state is not None:
                            current_cvd = float(state) if not hasattr(state, "get") else float(state.get("cvd", 0.0))
                    if current_cvd is not None:
                        self._latest_cvd = float(current_cvd)
                        logger.debug(
                            "[PriceCVDChart] Pulled CVD=%.4f from engine for token=%s",
                            self._latest_cvd,
                            token,
                        )

        if saw_relevant:
            self._schedule_live_flush()

    def _schedule_live_flush(self) -> None:
        if not self._web_ready:
            return
        if not self._live_flush_timer.isActive():
            self._live_flush_timer.start()

    def _flush_live_tick(self) -> None:
        if not self._web_ready:
            return

        # ── FIX 3d: Don't require both values to be non-None. If we have at
        #            least a price, push with CVD=0 as a fallback so the chart
        #            isn't frozen. Log a warning so the issue stays visible.
        if self._latest_price is None:
            logger.debug(
                "[PriceCVDChart] Skipping flush — _latest_price still None for %s",
                self.symbol,
            )
            return

        if self._latest_cvd is None:
            logger.warning(
                "[PriceCVDChart] _latest_cvd is None for %s — "
                "CVD engine may not be emitting cvd_updated for token=%s. "
                "Flushing with cvd=0.0 as fallback.",
                self.symbol,
                self.instrument_token,
            )

        payload = {
            "price": self._latest_price,
            "cvd": self._latest_cvd if self._latest_cvd is not None else 0.0,
            "timestamp": self._latest_tick_ts.isoformat(),
        }
        js = (
            f"if (typeof window.__applyPriceCvdLiveTick === 'function') "
            f"window.__applyPriceCvdLiveTick({json.dumps(payload)});"
        )
        try:
            self._web_view.page().runJavaScript(js)
        except Exception:
            logger.debug(
                "[PriceCVDChart] Failed to push live tick for %s",
                self.symbol,
                exc_info=True,
            )