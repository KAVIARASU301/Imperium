"""Shared API circuit breaker implementation.

This module re-exports the canonical circuit breaker from
`core.market_data.api_circuit_breaker` so every app subsystem uses the same
implementation and behavior.
"""

from core.market_data.api_circuit_breaker import (  # noqa: F401
    APICircuitBreaker,
    CircuitMetrics,
    CircuitState,
    circuit_breaker_wrapper,
)

__all__ = [
    "APICircuitBreaker",
    "CircuitMetrics",
    "CircuitState",
    "circuit_breaker_wrapper",
]
