import logging
from typing import Callable

from core.execution.paper_trading_manager import PaperTradingManager
from utils.network_utils import NetworkError, with_timeout

logger = logging.getLogger(__name__)
api_logger = logging.getLogger("api_health")


class AccountHealthService:
    """Handles account/profile polling with circuit-breaker-aware fallbacks."""

    def __init__(
        self,
        *,
        trader,
        real_kite_client,
        profile_circuit_breaker,
        margin_circuit_breaker,
        network_monitor,
        publish_status: Callable[[str, int, str], None],
        update_header_account_info: Callable[[str, float], None],
    ):
        self.trader = trader
        self.real_kite_client = real_kite_client
        self.profile_circuit_breaker = profile_circuit_breaker
        self.margin_circuit_breaker = margin_circuit_breaker
        self.network_monitor = network_monitor
        self.publish_status = publish_status
        self.update_header_account_info = update_header_account_info

        self.last_successful_balance: float = 0.0
        self.last_successful_user_id: str = "Unknown"
        self.last_successful_margins: dict = {}
        self.rms_failures: int = 0

    @with_timeout(timeout_seconds=5)
    def _periodic_api_health_check(self):
        logger.debug("Performing periodic API health check.")
        if self.profile_circuit_breaker.can_execute() or self.margin_circuit_breaker.can_execute():
            self._update_account_info()
        else:
            logger.debug("API health check skipped - circuit breakers are OPEN.")

    @with_timeout(timeout_seconds=5)
    def _fetch_profile_safe(self):
        """Helper method to fetch profile with timeout."""
        return self.real_kite_client.profile()

    @with_timeout(timeout_seconds=5)
    def _fetch_margins_safe(self):
        """Helper method to fetch margins with timeout."""
        return self.real_kite_client.margins()

    def _update_account_info(self):
        if isinstance(self.trader, PaperTradingManager):
            try:
                profile = self.trader.profile()
                margins_data = self.trader.margins()
                user_id = profile.get("user_id", "PAPER")
                balance = margins_data.get("equity", {}).get("net", 0.0)
                self.last_successful_margins = margins_data
                self.last_successful_user_id = user_id
                self.last_successful_balance = balance
                self.update_header_account_info(user_id, balance)
                logger.debug(f"Paper account info updated. Balance: {balance}")
            except Exception as e:
                logger.error(f"Failed to get paper account info: {e}")
            return

        if not self.real_kite_client or not hasattr(self.real_kite_client, 'access_token') or not self.real_kite_client.access_token:
            logger.debug("Skipping live account info update: Not a valid Kite client.")
            return

        if self.profile_circuit_breaker.can_execute():
            try:
                profile = self._fetch_profile_safe()
                if profile and isinstance(profile, dict):
                    self.last_successful_user_id = profile.get("user_id", "Unknown")
                    self.profile_circuit_breaker.record_success()
                    self.network_monitor.record_success()
                    api_logger.info("Profile fetch successful.")
                else:
                    logger.warning(f"Profile fetch returned unexpected data type: {type(profile)}")
                    self.profile_circuit_breaker.record_failure()
                    api_logger.warning(f"Profile fetch: Unexpected data type {type(profile)}")
            except NetworkError as e:
                logger.warning(f"Profile fetch network error: {e}")
                self.profile_circuit_breaker.record_failure()
                self.network_monitor.record_failure()
                api_logger.warning(f"Profile fetch network error: {e}")
            except Exception as e:
                logger.warning(f"Profile fetch API call failed: {e}")
                self.profile_circuit_breaker.record_failure()
                api_logger.warning(f"Profile fetch failed: {e}")

        current_balance_to_display = self.last_successful_balance
        if self.margin_circuit_breaker.can_execute():
            try:
                margins_data = self._fetch_margins_safe()
                if margins_data and isinstance(margins_data, dict):
                    calculated_balance = 0
                    if 'equity' in margins_data and margins_data['equity'] is not None:
                        calculated_balance += margins_data['equity'].get('net', 0)
                    if 'commodity' in margins_data and margins_data['commodity'] is not None:
                        calculated_balance += margins_data['commodity'].get('net', 0)
                    self.last_successful_balance = calculated_balance
                    self.last_successful_margins = margins_data
                    current_balance_to_display = self.last_successful_balance
                    self.margin_circuit_breaker.record_success()
                    self.network_monitor.record_success()
                    api_logger.info(f"Margins fetch successful. Balance: {current_balance_to_display}")
                    self.rms_failures = 0
                else:
                    logger.warning(f"Margins fetch returned unexpected data type: {type(margins_data)}")
                    self.margin_circuit_breaker.record_failure()
                    api_logger.warning(f"Margins fetch: Unexpected data type {type(margins_data)}")
            except NetworkError as e:
                logger.error(f"Margins fetch network error: {e}")
                self.margin_circuit_breaker.record_failure()
                self.network_monitor.record_failure()
                api_logger.error(f"Margins fetch network error: {e}")
                api_logger.error(f"Margins fetch failed: {e}")
                if self.margin_circuit_breaker.state == "OPEN":
                    self.publish_status("API issues (margins) - using cached data.", 5000, "warning")

        self.update_header_account_info(self.last_successful_user_id, current_balance_to_display)

    def _get_account_balance_safe(self) -> float:
        return self.last_successful_balance
