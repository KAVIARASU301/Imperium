import logging
from typing import Optional, Callable, Any
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# -----------------------------
# API Health Logger (Production Safe)
# -----------------------------
import os
from pathlib import Path

# Runtime data directory (outside project)
RUNTIME_DIR = Path.home() / ".imperium_desk"
LOG_DIR = RUNTIME_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "api_health.log"

api_logger = logging.getLogger("api_health")
api_logger.setLevel(logging.INFO)

if not api_logger.handlers:
    api_handler = logging.FileHandler(LOG_FILE)
    api_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    )
    api_handler.setFormatter(api_formatter)
    api_logger.addHandler(api_handler)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, blocking calls
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitMetrics:
    """Track circuit breaker metrics"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0  # Calls blocked by circuit
    last_success_time: Optional[datetime] = None
    last_failure_time: Optional[datetime] = None
    state_changes: list = field(default_factory=list)

    def success_rate(self) -> float:
        """Calculate success rate"""
        if self.total_calls == 0:
            return 0.0
        return (self.successful_calls / self.total_calls) * 100

    def log_summary(self):
        """Log metrics summary"""
        api_logger.info(
            f"Circuit Metrics | Total: {self.total_calls} | "
            f"Success: {self.successful_calls} | Failed: {self.failed_calls} | "
            f"Rejected: {self.rejected_calls} | Success Rate: {self.success_rate():.1f}%"
        )


class APICircuitBreaker:
    """
    Enhanced circuit breaker for API calls with exponential backoff and metrics

    States:
    - CLOSED: Normal operation, all calls allowed
    - OPEN: Circuit is open (failures exceeded threshold), calls blocked
    - HALF_OPEN: Testing recovery, limited calls allowed

    Features:
    - Exponential backoff on failures
    - Automatic state recovery
    - Detailed metrics tracking
    - Configurable thresholds
    """

    def __init__(
            self,
            failure_threshold: int = 5,
            timeout_seconds: int = 60,
            half_open_max_calls: int = 3,
            success_threshold: int = 2,  # Successes needed in HALF_OPEN to close
            max_timeout_seconds: int = 300,  # Max backoff time (5 mins)
    ):
        self.failure_threshold = failure_threshold
        self.base_timeout = timeout_seconds
        self.timeout_seconds = timeout_seconds
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        self.max_timeout_seconds = max_timeout_seconds

        self.failure_count = 0
        self.half_open_attempts = 0
        self.half_open_successes = 0
        self.consecutive_failures = 0  # Track consecutive failures for backoff

        self.state = CircuitState.CLOSED
        self.last_failure_time: Optional[datetime] = None
        self.last_state_change: Optional[datetime] = None

        self.metrics = CircuitMetrics()

        logger.info(
            f"Circuit breaker initialized | Threshold: {failure_threshold} | "
            f"Timeout: {timeout_seconds}s | Half-open calls: {half_open_max_calls}"
        )

    def can_execute(self) -> bool:
        """
        Check if API call should be allowed

        Returns:
            bool: True if call is allowed, False if blocked
        """
        self.metrics.total_calls += 1

        if self.state == CircuitState.CLOSED:
            return True

        elif self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self._transition_to_half_open()
                return True
            else:
                self.metrics.rejected_calls += 1
                return False

        elif self.state == CircuitState.HALF_OPEN:
            if self.half_open_attempts < self.half_open_max_calls:
                self.half_open_attempts += 1
                return True
            else:
                self.metrics.rejected_calls += 1
                return False

        return False

    def record_success(self):
        """Record successful API call"""
        self.metrics.successful_calls += 1
        self.metrics.last_success_time = datetime.now()
        self.consecutive_failures = 0  # Reset consecutive failures

        if self.state == CircuitState.HALF_OPEN:
            self.half_open_successes += 1
            logger.info(
                f"Circuit HALF_OPEN success {self.half_open_successes}/{self.success_threshold}"
            )

            if self.half_open_successes >= self.success_threshold:
                self._transition_to_closed()

        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success in closed state
            if self.failure_count > 0:
                logger.info(f"Circuit recovered, resetting failure count from {self.failure_count}")
                self.failure_count = 0

    def record_failure(self):
        """Record failed API call with exponential backoff"""
        self.metrics.failed_calls += 1
        self.failure_count += 1
        self.consecutive_failures += 1
        self.last_failure_time = datetime.now()
        self.metrics.last_failure_time = self.last_failure_time

        if self.state == CircuitState.HALF_OPEN:
            # Failure in HALF_OPEN immediately opens circuit
            logger.warning(
                f"Circuit HALF_OPEN test failed after {self.half_open_attempts} attempts"
            )
            self._transition_to_open()

        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self._transition_to_open()

    def _transition_to_open(self):
        """Transition to OPEN state with exponential backoff"""
        self.state = CircuitState.OPEN
        self.last_state_change = datetime.now()

        # Exponential backoff: double timeout each time, up to max
        self.timeout_seconds = min(
            self.base_timeout * (2 ** (self.consecutive_failures - 1)),
            self.max_timeout_seconds
        )

        self.metrics.state_changes.append({
            "state": "OPEN",
            "time": self.last_state_change,
            "failures": self.failure_count,
            "timeout": self.timeout_seconds
        })

        logger.warning(
            f"Circuit breaker OPEN | Failures: {self.failure_count} | "
            f"Timeout: {self.timeout_seconds}s | Backoff level: {self.consecutive_failures}"
        )
        api_logger.warning(
            f"CIRCUIT_OPEN failures={self.failure_count} timeout={self.timeout_seconds}s"
        )

    def _transition_to_half_open(self):
        """Transition to HALF_OPEN state"""
        self.state = CircuitState.HALF_OPEN
        self.last_state_change = datetime.now()
        self.half_open_attempts = 0
        self.half_open_successes = 0

        self.metrics.state_changes.append({
            "state": "HALF_OPEN",
            "time": self.last_state_change
        })

        logger.info(
            f"Circuit breaker HALF_OPEN | Testing recovery with {self.half_open_max_calls} attempts"
        )
        api_logger.info("CIRCUIT_HALF_OPEN testing_recovery")

    def _transition_to_closed(self):
        """Transition to CLOSED state"""
        self.state = CircuitState.CLOSED
        self.last_state_change = datetime.now()
        self.failure_count = 0
        self.half_open_attempts = 0
        self.half_open_successes = 0
        self.timeout_seconds = self.base_timeout  # Reset timeout

        self.metrics.state_changes.append({
            "state": "CLOSED",
            "time": self.last_state_change
        })

        logger.info("Circuit breaker CLOSED | Recovery successful")
        api_logger.info("CIRCUIT_CLOSED recovery_complete")

        # Log metrics summary on recovery
        self.metrics.log_summary()

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        if not self.last_failure_time:
            return True

        elapsed = datetime.now() - self.last_failure_time
        return elapsed >= timedelta(seconds=self.timeout_seconds)

    def get_state(self) -> str:
        """Get current circuit state"""
        return self.state.value

    def get_metrics(self) -> dict:
        """Get circuit metrics"""
        return {
            "state": self.state.value,
            "total_calls": self.metrics.total_calls,
            "successful_calls": self.metrics.successful_calls,
            "failed_calls": self.metrics.failed_calls,
            "rejected_calls": self.metrics.rejected_calls,
            "success_rate": self.metrics.success_rate(),
            "failure_count": self.failure_count,
            "timeout_seconds": self.timeout_seconds,
            "last_state_change": self.last_state_change.isoformat() if self.last_state_change else None
        }

    def reset(self):
        """Manually reset circuit breaker"""
        logger.info("Circuit breaker manually reset")
        self._transition_to_closed()


def circuit_breaker_wrapper(
        circuit: APICircuitBreaker,
        fallback_value: Any = None,
        raise_on_open: bool = False
):
    """
    Decorator for wrapping API calls with circuit breaker

    Usage:
        @circuit_breaker_wrapper(my_circuit, fallback_value=0)
        def get_price():
            return api.get_ltp()

    Args:
        circuit: APICircuitBreaker instance
        fallback_value: Value to return when circuit is open
        raise_on_open: If True, raise exception when circuit is open instead of returning fallback
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            if not circuit.can_execute():
                api_logger.warning(f"Circuit OPEN: Blocked call to {func.__name__}")
                if raise_on_open:
                    raise Exception(f"Circuit breaker is {circuit.get_state()}, call blocked")
                return fallback_value

            try:
                result = func(*args, **kwargs)
                circuit.record_success()
                return result
            except Exception as e:
                circuit.record_failure()
                logger.error(f"Circuit breaker caught error in {func.__name__}: {e}")
                raise

        return wrapper

    return decorator