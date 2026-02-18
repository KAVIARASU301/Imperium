# utils/network_utils.py
"""
Network utilities for handling timeouts and connection errors gracefully
"""
import logging
from functools import wraps
import requests
from typing import Callable, Any
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class NetworkError(Exception):
    """Raised when network operations fail"""
    pass


def with_timeout(timeout_seconds: int = 5):
    """
    Decorator to add timeout to KiteConnect API calls

    Usage:
        @with_timeout(timeout_seconds=5)
        def get_profile(self):
            return self.trader.profile()
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                # Set session timeout for requests
                if hasattr(args[0], 'trader') and hasattr(args[0].trader, 'session'):
                    # Configure session with timeout
                    session = args[0].trader.session
                    if not hasattr(session, '_original_request'):
                        session._original_request = session.request

                    def request_with_timeout(*req_args, **req_kwargs):
                        req_kwargs.setdefault('timeout', timeout_seconds)
                        return session._original_request(*req_args, **req_kwargs)

                    session.request = request_with_timeout

                return func(*args, **kwargs)

            except requests.exceptions.Timeout:
                logger.error(f"{func.__name__} timed out after {timeout_seconds}s")
                raise NetworkError(f"Request timeout - check your internet connection")
            except requests.exceptions.ConnectionError:
                logger.error(f"{func.__name__} connection failed")
                raise NetworkError("Connection failed - check your internet connection")
            except requests.exceptions.RequestException as e:
                logger.error(f"{func.__name__} network error: {e}")
                raise NetworkError(f"Network error: {str(e)}")
            except Exception as e:
                # Let other exceptions pass through
                raise

        return wrapper

    return decorator


class NetworkMonitor(QObject):
    """
    Monitor network connectivity and emit signals
    """
    connection_lost = Signal()
    connection_restored = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_online = True
        self._consecutive_failures = 0
        self._failure_threshold = 2  # Consider offline after 2 failures

    def record_success(self):
        """Call this after successful network operation"""
        was_offline = not self._is_online
        self._consecutive_failures = 0
        self._is_online = True

        if was_offline:
            self.connection_restored.emit()
            logger.info("Network connection restored")

    def record_failure(self):
        """Call this after failed network operation"""
        self._consecutive_failures += 1

        if self._consecutive_failures >= self._failure_threshold and self._is_online:
            self._is_online = False
            self.connection_lost.emit()
            logger.warning("Network connection lost")

    @property
    def is_online(self) -> bool:
        return self._is_online