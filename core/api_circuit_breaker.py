import logging
from typing import Optional  # Added Optional for APICircuitBreaker type hint
from datetime import datetime, timedelta  # Added time for APICircuitBreaker

logger = logging.getLogger(__name__)

# API Health Logger (moved to top with other loggers)
api_logger = logging.getLogger("api_health")
api_handler = logging.FileHandler("logs/api_health.log")
api_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
api_handler.setFormatter(api_formatter)
api_logger.addHandler(api_handler)
api_logger.setLevel(logging.INFO)


# Moved APICircuitBreaker class definition to be before its first use or in a common area
class APICircuitBreaker:
    """
    Circuit breaker for API calls to prevent overwhelming failed endpoints

    States: CLOSED (normal) -> OPEN (failing) -> HALF_OPEN (testing)
    """

    def __init__(self, failure_threshold: int = 5, timeout_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None  # Type hinted Optional[datetime]
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def can_execute(self) -> bool:
        """Check if API call should be allowed"""
        if self.state == "CLOSED":
            return True
        elif self.state == "OPEN":
            if self._should_attempt_reset():
                self.state = "HALF_OPEN"
                return True
            return False
        elif self.state == "HALF_OPEN":
            return True
        return False

    def record_success(self):
        """Record successful API call"""
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self):
        """Record failed API call"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            logger.warning(f"Circuit breaker OPEN after {self.failure_count} failures")

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        if not self.last_failure_time:
            return True
        return datetime.now() - self.last_failure_time >= timedelta(seconds=self.timeout_seconds)