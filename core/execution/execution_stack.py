import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.observability import (
    AnomalyDetector,
    ExecutionJournal,
    IncidentResponder,
    TCAReporter,
    TelemetryDashboard,
    TraceContext,
)

logger = logging.getLogger(__name__)


@dataclass
class ExecutionRequest:
    tradingsymbol: str
    transaction_type: str
    quantity: int
    order_type: str
    product: str
    ltp: float
    bid: float = 0.0
    ask: float = 0.0
    limit_price: Optional[float] = None
    urgency: str = "normal"
    participation_rate: float = 0.15
    execution_algo: str = "IMMEDIATE"
    max_child_orders: int = 1
    randomize_slices: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class SmartOrderRouter:
    """Single-broker SOR abstraction for future multi-venue routing."""

    def choose_route(self, request: ExecutionRequest) -> Dict[str, Any]:
        spread = max(0.0, request.ask - request.bid) if request.ask > request.bid > 0 else 0.0
        spread_bps = (spread / request.ltp * 10_000) if request.ltp > 0 and spread > 0 else 0.0

        route = {
            "route": "primary",
            "queue_priority": "neutral",
            "order_type": request.order_type,
            "limit_price": request.limit_price,
        }

        if request.urgency == "high":
            route["queue_priority"] = "take"
            if request.order_type != "MARKET" and request.ask > 0:
                route["limit_price"] = max(request.limit_price or 0.0, request.ask)
        elif spread_bps > 12:
            route["queue_priority"] = "join"
            if request.bid > 0:
                route["order_type"] = "LIMIT"
                route["limit_price"] = request.bid

        return route


class ExecutionAlgoPlanner:
    def plan(self, request: ExecutionRequest) -> List[int]:
        qty = max(1, int(request.quantity))
        algo = (request.execution_algo or "IMMEDIATE").upper()

        if algo in {"IMMEDIATE", "IS"}:
            return [qty]

        slices = max(1, min(int(request.max_child_orders or 1), qty))
        base = qty // slices
        rem = qty % slices
        plan = [base + (1 if i < rem else 0) for i in range(slices)]

        if algo in {"TWAP", "VWAP", "POV"} and request.randomize_slices and slices > 1:
            jittered = []
            budget = qty
            for i, child in enumerate(plan):
                if i == slices - 1:
                    jittered.append(budget)
                    break
                jitter = int(max(1, child) * random.uniform(-0.15, 0.15))
                value = max(1, child + jitter)
                value = min(value, budget - (slices - i - 1))
                jittered.append(value)
                budget -= value
            plan = jittered
        return plan


class SlippageModel:
    def estimate(self, request: ExecutionRequest, child_qty: int) -> Dict[str, float]:
        spread = max(0.0, request.ask - request.bid) if request.ask > request.bid > 0 else 0.0
        spread_cost = spread / 2.0
        participation = max(0.01, min(1.0, child_qty / max(1, request.quantity)))
        impact = request.ltp * 0.0004 * (participation ** 0.6)
        return {
            "expected_slippage": round(spread_cost + impact, 4),
            "impact_estimate": round(impact, 4),
        }


class RetryPolicy:
    def classify(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if any(k in msg for k in ["timeout", "temporarily", "connection", "network"]):
            return "transient"
        if "rate" in msg and "limit" in msg:
            return "throttle"
        if any(k in msg for k in ["margin", "rms", "insufficient"]):
            return "risk"
        return "fatal"

    def max_attempts(self, bucket: str) -> int:
        return {"transient": 3, "throttle": 4, "risk": 1, "fatal": 1}.get(bucket, 1)

    def sleep_seconds(self, bucket: str, attempt: int) -> float:
        if bucket == "throttle":
            return min(1.5, 0.4 * (attempt + 1))
        if bucket == "transient":
            return min(1.0, 0.2 * (2 ** attempt))
        return 0.0


class FillQualityTracker:
    def __init__(self, trading_mode: str, base_dir: Path):
        self.path = base_dir / f"execution_quality_{trading_mode}.jsonl"

    def append(self, record: Dict[str, Any]):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record))
                fp.write("\n")
        except Exception as exc:
            logger.error("Failed to write execution quality record: %s", exc)


class ExecutionStack:
    def __init__(self, trading_mode: str, base_dir: Path, remediation_hooks: Optional[Dict[str, Callable[..., None]]] = None):
        self.router = SmartOrderRouter()
        self.planner = ExecutionAlgoPlanner()
        self.slippage = SlippageModel()
        self.retry = RetryPolicy()
        self.fill_quality = FillQualityTracker(trading_mode=trading_mode, base_dir=base_dir)
        self.journal = ExecutionJournal(trading_mode=trading_mode, base_dir=base_dir)
        self.dashboard = TelemetryDashboard(trading_mode=trading_mode, base_dir=base_dir)
        self.incident_responder = IncidentResponder(journal=self.journal, remediation_hooks=remediation_hooks)
        self.anomaly_detector = AnomalyDetector(responder=self.incident_responder)
        self.tca_reporter = TCAReporter(trading_mode=trading_mode, base_dir=base_dir)

    def execute(self, request: ExecutionRequest, place_order_fn: Callable[..., str], base_order_args: Dict[str, Any]) -> List[str]:
        trace = TraceContext.new(tags={
            "tradingsymbol": request.tradingsymbol,
            "execution_algo": request.execution_algo,
            "source": request.metadata.get("source", "unknown"),
        })
        signal_id = str(request.metadata.get("signal_id") or request.metadata.get("auto_token") or "")
        if signal_id:
            self.anomaly_detector.on_signal(signal_id)

        route = self.router.choose_route(request)
        slices = self.planner.plan(request)
        order_ids: List[str] = []

        signal_event = trace.next_span(
            "signal_received",
            {
                "signal_id": signal_id or None,
                "tradingsymbol": request.tradingsymbol,
                "quantity": request.quantity,
                "metadata": request.metadata,
            },
        )
        self.journal.append("signal", signal_event)
        self.dashboard.observe("signal", signal_event)

        for idx, child_qty in enumerate(slices, start=1):
            metrics = self.slippage.estimate(request, child_qty)
            order_args = dict(base_order_args)
            order_args["quantity"] = child_qty
            order_args["order_type"] = route.get("order_type") or request.order_type
            limit_price = route.get("limit_price")
            if order_args["order_type"] == "LIMIT" and limit_price is not None:
                order_args["price"] = limit_price
            else:
                order_args.pop("price", None)

            attempts = 0
            while True:
                started_at = time.time()
                try:
                    order_id = place_order_fn(**order_args)
                    self.anomaly_detector.on_order_submitted(order_id)
                    order_ids.append(order_id)
                    placed_record = {
                        "timestamp": datetime.now().isoformat(),
                        "trace_id": trace.trace_id,
                        "tradingsymbol": request.tradingsymbol,
                        "child_index": idx,
                        "children": len(slices),
                        "quantity": child_qty,
                        "order_id": order_id,
                        "arrival_price": request.ltp,
                        "limit_price": order_args.get("price"),
                        "expected_slippage": metrics["expected_slippage"],
                        "impact_estimate": metrics["impact_estimate"],
                        "latency_ms": round((time.time() - started_at) * 1000, 2),
                        "execution_algo": request.execution_algo,
                        "route": route.get("route"),
                        "queue_priority": route.get("queue_priority"),
                        "status": "placed",
                        "risk_used": request.metadata.get("risk_used"),
                        "risk_total": request.metadata.get("risk_total"),
                    }
                    self.fill_quality.append(placed_record)
                    journal_event = trace.next_span("order_placed", placed_record)
                    self.journal.append("order_placed", journal_event)
                    self.dashboard.observe("order_placed", placed_record)
                    break
                except Exception as exc:
                    bucket = self.retry.classify(exc)
                    max_attempts = self.retry.max_attempts(bucket)
                    attempts += 1
                    error_record = {
                        "timestamp": datetime.now().isoformat(),
                        "trace_id": trace.trace_id,
                        "tradingsymbol": request.tradingsymbol,
                        "child_index": idx,
                        "children": len(slices),
                        "quantity": child_qty,
                        "arrival_price": request.ltp,
                        "expected_slippage": metrics["expected_slippage"],
                        "impact_estimate": metrics["impact_estimate"],
                        "execution_algo": request.execution_algo,
                        "route": route.get("route"),
                        "queue_priority": route.get("queue_priority"),
                        "status": "error",
                        "error_bucket": bucket,
                        "error": str(exc),
                        "attempt": attempts,
                        "risk_used": request.metadata.get("risk_used"),
                        "risk_total": request.metadata.get("risk_total"),
                    }
                    self.fill_quality.append(error_record)
                    self.journal.append("order_error", trace.next_span("order_error", error_record))
                    self.dashboard.observe("order_error", error_record)
                    if attempts >= max_attempts:
                        raise
                    time.sleep(self.retry.sleep_seconds(bucket, attempts))

        self.anomaly_detector.heartbeat()
        self.tca_reporter.generate(self.journal.path)
        return order_ids

    def record_fill(self, order_id: str, filled_price: float, filled_qty: int):
        self.anomaly_detector.on_order_closed(order_id)
        payload = {
            "order_id": order_id,
            "filled_price": filled_price,
            "filled_qty": filled_qty,
            "status": "filled",
        }
        self.journal.append("order_fill", payload)
        self.dashboard.observe("order_fill", payload)

    def record_exit(self, tradingsymbol: str, outcome: str, pnl: float):
        payload = {
            "tradingsymbol": tradingsymbol,
            "outcome": outcome,
            "pnl": pnl,
            "status": "closed",
        }
        self.journal.append("position_exit", payload)
        self.dashboard.observe("position_exit", payload)

    def ingest_tick(self, symbol: str, tick_ts: Optional[float] = None):
        self.anomaly_detector.on_tick(symbol, tick_ts=tick_ts)

    def heartbeat(self):
        self.anomaly_detector.heartbeat()
        self.tca_reporter.generate(self.journal.path)
