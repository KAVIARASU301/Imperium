# core/market_data_worker.py - COMPLETE FIXED VERSION

import logging
from typing import Set, Optional
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from kiteconnect import KiteTicker
from datetime import datetime, timedelta, time
import socket
import time as pytime
import requests

logger = logging.getLogger(__name__)


class MarketDataWorker(QObject):
    """
    Manages the KiteTicker WebSocket connection from the main thread.
    The KiteTicker itself runs in a background thread.
    """
    data_received = Signal(list)
    connection_closed = Signal()
    connection_error = Signal(str)
    connection_status_changed = Signal(str)

    # Internal signals: KiteTicker callbacks arrive from a non-Qt thread.
    # We fan-in through queued Qt signals so QTimer operations happen on this
    # QObject's thread only.
    _ticks_received = Signal(list)
    _ws_connected = Signal(object)
    _ws_closed = Signal(int, str)
    _ws_error = Signal(int, str)

    def __init__(self, api_key: str, access_token: str):
        super().__init__()
        self.api_key = api_key
        self.access_token = access_token
        self.kws: Optional[KiteTicker] = None
        self.is_running = False
        self.subscribed_tokens: Set[int] = set()
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10  # Prevent infinite reconnection
        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.timeout.connect(self.reconnect)
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.timeout.connect(self._check_heartbeat)
        self.last_tick_time: Optional[datetime] = None
        self.is_intentional_stop = False  # Track if user manually stopped
        self._heartbeat_stale_reported = False
        self.network_check_failed_count = 0  # Track consecutive network failures
        self._http_probe_cooldown_sec = 60
        self._last_http_probe_monotonic = 0.0
        self._kite_ticker_logger = logging.getLogger("kiteconnect.ticker")
        self._kite_ticker_log_level_before_stop: Optional[int] = None

        # Ensure websocket callback handling runs on the worker's thread.
        self._ticks_received.connect(self._handle_ticks, Qt.QueuedConnection)
        self._ws_connected.connect(self._handle_connect, Qt.QueuedConnection)
        self._ws_closed.connect(self._handle_close, Qt.QueuedConnection)
        self._ws_error.connect(self._handle_error, Qt.QueuedConnection)

    def _check_network_connectivity(self, force_http_probe: bool = False) -> tuple[bool, str]:
        """
        Fast connectivity check for reconnect/start path.
        Uses DNS + TCP socket probe as primary signal.
        HTTP probe is debounced and non-blocking.
        Returns: (is_connected, error_message)
        """
        host = 'api.kite.trade'

        # Quick DNS check (cheap)
        try:
            resolved_ip = socket.gethostbyname(host)
            logger.debug(f"DNS resolution successful for {host}: {resolved_ip}")
        except socket.gaierror as e:
            return False, f"DNS resolution failed: {e}"
        except Exception as e:
            return False, f"DNS check error: {e}"

        # Fast TCP reachability check (avoid repeated full HTTP requests)
        try:
            with socket.create_connection((host, 443), timeout=2):
                pass
            logger.debug(f"TCP reachability successful for {host}:443")
        except socket.timeout:
            return False, "Socket connect timeout: Network slow/unstable"
        except OSError as e:
            return False, f"Socket connectivity failed: {e}"
        except Exception as e:
            return False, f"Socket check error: {e}"

        # Optional deep probe with cooldown to avoid repeated external calls.
        now = pytime.monotonic()
        http_probe_due = (now - self._last_http_probe_monotonic) >= self._http_probe_cooldown_sec
        should_probe_http = force_http_probe or http_probe_due

        if not should_probe_http:
            return True, "Network OK (DNS + socket)"

        self._last_http_probe_monotonic = now
        try:
            response = requests.get(f'https://{host}', timeout=3)
            logger.debug(f"HTTP probe successful: {response.status_code}")
            return True, "Network OK"
        except requests.exceptions.RequestException as e:
            # Non-fatal: DNS + socket checks already passed.
            logger.warning(f"HTTP probe failed but fast checks passed: {e}")
            return True, "Network OK (HTTP probe skipped/failing)"

    def _is_market_hours(self) -> bool:
        """
        Check if current time is within Indian market hours (9:15 AM - 3:30 PM IST).
        Also checks if it's a weekday (Mon-Fri).
        Returns: True if within market hours, False otherwise
        """
        now = datetime.now()

        # Check if it's a weekend
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False

        # Market hours: 9:15 AM to 3:30 PM
        market_open = time(9, 15)
        market_close = time(15, 30)
        current_time = now.time()

        return market_open <= current_time <= market_close

    def _get_market_status(self) -> str:
        """
        Get human-readable market status.
        Returns: Status string like "Market Open", "Market Closed", "Weekend"
        """
        now = datetime.now()

        # Check weekend
        if now.weekday() >= 5:
            return "Weekend"

        # Check market hours
        market_open = time(9, 15)
        market_close = time(15, 30)
        current_time = now.time()

        if current_time < market_open:
            return "Pre-Market"
        elif current_time > market_close:
            return "Post-Market"
        else:
            return "Market Open"

    def start(self):
        """Initializes and connects the KiteTicker WebSocket client."""
        if self.is_running:
            logger.warning("MarketDataWorker is already running.")
            return

        logger.info("MarketDataWorker starting...")
        self.is_intentional_stop = False  # Reset flag

        if self._kite_ticker_log_level_before_stop is not None:
            self._kite_ticker_logger.setLevel(self._kite_ticker_log_level_before_stop)
            self._kite_ticker_log_level_before_stop = None

        # 🔥 NETWORK CHECK: Verify connectivity before attempting connection
        is_connected, error_msg = self._check_network_connectivity(force_http_probe=True)
        if not is_connected:
            logger.error(f"Network connectivity check failed: {error_msg}")
            self.connection_status_changed.emit(f"Network Error: {error_msg}")
            self.network_check_failed_count += 1

            # Don't attempt connection if network is down
            if self.network_check_failed_count < 3:
                logger.info(f"Will retry network check in 10s (attempt {self.network_check_failed_count}/3)")
                QTimer.singleShot(10000, self.start)
            else:
                logger.error("Network persistently unavailable. Please check your internet connection.")
                self.connection_status_changed.emit("Network Unavailable - Check Connection")
                self.network_check_failed_count = 0  # Reset for next manual retry
            return

        # Reset network failure counter on successful check
        self.network_check_failed_count = 0
        self.connection_status_changed.emit("Connecting")

        if not self.kws:
            # Keep a single KiteTicker instance for process lifetime.
            # Internally it uses Twisted reactor, which is not restartable
            # once stopped.
            self.kws = KiteTicker(self.api_key, self.access_token)

            # Assign callbacks once.
            self.kws.on_ticks = self._on_ticks
            self.kws.on_connect = self._on_connect
            self.kws.on_close = self._on_close
            self.kws.on_error = self._on_error

        # The connect call is non-blocking and runs in its own thread
        try:
            self.kws.connect(threaded=True)
            self.is_running = True
            logger.info("KiteTicker connection initiated")
        except Exception as e:
            logger.error(f"Failed to start KiteTicker: {e}")
            self.is_running = False

            # 🔥 Better error messaging
            if "Name or service not known" in str(e) or "Failed to resolve" in str(e):
                self.connection_status_changed.emit("DNS Error - Check Network")
            elif "Connection refused" in str(e):
                self.connection_status_changed.emit("Connection Refused - Kite API Down?")
            else:
                self.connection_status_changed.emit("Connection Failed")

            # Trigger reconnect with network check
            if not self.is_intentional_stop:
                QTimer.singleShot(5000, self.start)

    def _check_heartbeat(self):
        """Check if ticks are being received (only during market hours)"""
        if not self.is_running:
            return

        # 🔥 Skip heartbeat checks outside market hours
        if not self._is_market_hours():
            # Silently continue - no need to warn outside trading hours
            return

        if self.last_tick_time:
            time_since_last_tick = datetime.now() - self.last_tick_time
            if time_since_last_tick > timedelta(seconds=30):
                if not self._heartbeat_stale_reported:
                    logger.warning("Heartbeat: No ticks received in the last 30 seconds (during market hours).")
                    self.connection_status_changed.emit("Connected (No Recent Ticks)")
                    self._heartbeat_stale_reported = True

    def _on_ticks(self, _, ticks):
        """Callback for receiving ticks."""
        self._ticks_received.emit(ticks)

    def _on_connect(self, _, response):
        self._ws_connected.emit(response)

    def _on_close(self, _, code, reason):
        self._ws_closed.emit(code, str(reason))

    def _on_error(self, _, code, reason):
        self._ws_error.emit(int(code), str(reason))

    def _handle_ticks(self, ticks):
        """Qt-thread handler for receiving ticks."""
        self.last_tick_time = datetime.now()
        self._heartbeat_stale_reported = False
        self.data_received.emit(ticks)

    def _handle_connect(self, response):
        """Callback on successful connection."""
        logger.info("WebSocket connected. Subscribing to existing tokens.")

        # 🔥 Smart status based on market hours
        market_status = self._get_market_status()
        if market_status == "Market Open":
            self.connection_status_changed.emit("Connected")
        else:
            self.connection_status_changed.emit(f"Connected ({market_status})")

        self.reconnect_attempts = 0  # Reset counter on success
        self.last_tick_time = datetime.now()
        self._heartbeat_stale_reported = False

        # 🔥 FIX: Stop reconnect timer on successful connection
        if self.reconnect_timer.isActive():
            self.reconnect_timer.stop()
            logger.info("Stopped reconnect timer - connection successful")

        # Start heartbeat monitoring
        if not self.heartbeat_timer.isActive():
            self.heartbeat_timer.start(15000)

        # Subscriptions handled by main window after connection
        logger.debug("Connection established. Waiting for subscription instructions.")

    def _handle_close(self, code, reason):
        """Callback on connection close."""
        if self.is_intentional_stop:
            logger.info(f"WebSocket closed during intentional shutdown. Code: {code}, Reason: {reason}")
            self.is_running = False
            self.heartbeat_timer.stop()
            return

        logger.warning(f"WebSocket connection closed. Code: {code}, Reason: {reason}")
        self.is_running = False
        self.heartbeat_timer.stop()
        self.connection_status_changed.emit("Disconnected")
        self.connection_closed.emit()

        # If KiteTicker internal retry is enabled, don't force our own restart.
        # Starting a fresh instance after reactor stop triggers
        # twisted.internet.error.ReactorNotRestartable.
        if not self.is_intentional_stop and self.reconnect_attempts < self.max_reconnect_attempts:
            if not self.reconnect_timer.isActive():
                delay = min(5000 * (2 ** min(self.reconnect_attempts, 5)), 60000)  # Exponential backoff, max 60s
                logger.info(f"Scheduling reconnection in {delay / 1000}s...")
                self.reconnect_timer.start(delay)
        elif self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached. Giving up.")
            self.connection_status_changed.emit("Connection Failed - Max Retries Reached")

    def _handle_error(self, code, reason):
        """Callback for WebSocket errors."""
        if self.is_intentional_stop:
            logger.info(f"Ignoring WebSocket error during intentional shutdown. Code: {code}, Reason: {reason}")
            return

        logger.error(f"WebSocket error. Code: {code}, Reason: {reason}")
        self.connection_status_changed.emit(f"Error: {reason}")
        self.connection_error.emit(str(reason))

    def reconnect(self):
        """Attempt to reconnect to the WebSocket."""
        # 🔥 FIX: Don't check is_running here - it will always be False after disconnect
        if self.is_intentional_stop:
            logger.info("Reconnect aborted - intentional stop")
            self.reconnect_timer.stop()
            return

        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"Max reconnection attempts reached. Stopping reconnection.")
            self.reconnect_timer.stop()
            self.connection_status_changed.emit("Max Retries - Check Network & Restart")
            return

        # 🔥 NETWORK CHECK: Verify connectivity before reconnecting
        is_connected, error_msg = self._check_network_connectivity()
        if not is_connected:
            logger.warning(f"Network check failed during reconnect: {error_msg}")
            self.connection_status_changed.emit(
                f"Waiting for Network... ({self.reconnect_attempts}/{self.max_reconnect_attempts})")

            # Don't increment reconnect_attempts for network issues
            # Just wait longer before retrying
            if not self.reconnect_timer.isActive():
                self.reconnect_timer.start(15000)  # Wait 15s for network to recover
            return

        self.reconnect_attempts += 1
        logger.info(f"Attempting to reconnect... (Attempt #{self.reconnect_attempts}/{self.max_reconnect_attempts})")
        self.connection_status_changed.emit(
            f"Reconnecting ({self.reconnect_attempts}/{self.max_reconnect_attempts})...")

        # Stop the timer before calling start to avoid overlap
        self.reconnect_timer.stop()

        # Reconnect using the same KiteTicker instance to avoid Twisted
        # reactor restart errors.
        if not self.kws:
            self.start()
            return

        try:
            self.connection_status_changed.emit("Connecting")
            self.kws.connect(threaded=True)
            self.is_running = True
            logger.info("KiteTicker reconnect initiated")
        except Exception as e:
            self.is_running = False
            logger.error(f"Reconnect failed: {e}")

            # 🔥 Better error classification
            if "ReactorNotRestartable" in str(e):
                self.connection_status_changed.emit("Connection Failed - Restart App")
            elif "Name or service not known" in str(e) or "Failed to resolve" in str(e):
                self.connection_status_changed.emit("DNS Error - Check Network Connection")
                # Don't count DNS failures against max_reconnect_attempts
                self.reconnect_attempts -= 1
            elif "Connection refused" in str(e):
                self.connection_status_changed.emit("Connection Refused - API Down?")

            if not self.is_intentional_stop and not self.reconnect_timer.isActive():
                # Exponential backoff for retries
                delay = min(5000 * (2 ** min(self.reconnect_attempts, 4)), 60000)
                logger.info(f"Will retry in {delay / 1000}s")
                self.reconnect_timer.start(delay)

    def set_instruments(self, instrument_tokens: Set[int], append: bool = False):
        """
        🔥 FIXED: Updates or appends instrument tokens for subscription.
        """
        # Convert to set
        instrument_tokens_set = set(instrument_tokens)

        logger.debug(f"[set_instruments] Called with {len(instrument_tokens_set)} tokens, append={append}")

        if append:
            instrument_tokens_set |= self.subscribed_tokens

        # 🔥 CRITICAL: Check WebSocket connection state
        if not self.kws:
            logger.warning("[set_instruments] KiteTicker not initialized. Storing tokens.")
            self.subscribed_tokens = instrument_tokens_set
            return

        if not self.kws.is_connected():
            logger.warning("[set_instruments] WebSocket not connected. Storing tokens for later.")
            self.subscribed_tokens = instrument_tokens_set
            return

        # Calculate changes
        new_tokens = instrument_tokens_set
        old_tokens = self.subscribed_tokens

        tokens_to_add = list(new_tokens - old_tokens)
        tokens_to_remove = list(old_tokens - new_tokens) if not append else []

        # 🔥 FIX: Subscribe to new tokens
        if tokens_to_add:
            try:
                self.kws.subscribe(tokens_to_add)
                self.kws.set_mode(self.kws.MODE_FULL, tokens_to_add)
                logger.info(f"Subscribed to {len(tokens_to_add)} new tokens.")
            except Exception as e:
                logger.error(f"Failed to subscribe to new tokens: {e}")
                # Don't add to subscribed_tokens if subscription failed
                return

        # 🔥 FIX: Unsubscribe from removed tokens
        if tokens_to_remove:
            try:
                self.kws.unsubscribe(tokens_to_remove)
                logger.info(f"Unsubscribed from {len(tokens_to_remove)} tokens.")
            except Exception as e:
                logger.error(f"Failed to unsubscribe tokens: {e}")

        # 🔥 CRITICAL: Update internal state AFTER successful operations
        self.subscribed_tokens = new_tokens

        logger.debug(f"[set_instruments] Now tracking {len(self.subscribed_tokens)} tokens")

    def stop(self):
        """Stops the worker and closes the WebSocket connection."""
        logger.info("Stopping MarketDataWorker...")
        self.is_intentional_stop = True  # 🔥 Mark as intentional stop
        self.reconnect_timer.stop()
        self.heartbeat_timer.stop()

        if self.kws:
            try:
                if self.is_running:
                    if self._kite_ticker_log_level_before_stop is None:
                        self._kite_ticker_log_level_before_stop = self._kite_ticker_logger.level
                    self._kite_ticker_logger.setLevel(logging.CRITICAL)
                    self.kws.stop()
                    logger.info("KiteTicker stopped successfully")
            except Exception as e:
                logger.warning(f"Error while stopping KiteTicker: {e}")

        self.is_running = False
        self.connection_status_changed.emit("Stopped")

    def manual_reconnect(self):
        """Manually trigger a reconnection (e.g., from UI button)."""
        logger.info("Manual reconnection triggered by user")
        self.is_intentional_stop = False
        self.reconnect_attempts = 0  # Reset counter for manual reconnect
        self.network_check_failed_count = 0  # Reset network failure counter
        self.reconnect_timer.stop()

        self.is_running = False

        # Use start() instead of reconnect() to trigger network check
        self.start()
