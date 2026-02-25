import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.observability import (
    AnomalyDetector,
    ExecutionJournal,
    IncidentResponder,
    TCAReporter,
    TelemetryDashboard,
    TraceContext,
    _utc_now_iso,  # FIX #2: import unified UTC helper so every timestamp is consistent
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
        if request.urgency == "high" or request.execution_algo == "IMMEDIATE":
            return {"route": "primary", "order_type": request.order_type, "queue_priority": "join", "limit_price": request.limit_price}
        return {"route": "primary", "order_type": request.order_type, "queue_priority": "join", "limit_price": request.limit_price}


class ExecutionAlgoPlanner:
    """Breaks a parent order into child slices based on algo type."""

    def plan(self, request: ExecutionRequest) -> List[int]:
        qty = request.quantity
        if request.execution_algo == "IMMEDIATE" or request.max_child_orders <= 1:
            return [qty]

        n = min(request.max_child_orders, 5)
        base = qty // n
        remainder = qty % n
        slices = [base] * n
        if remainder:
            slices[-1] += remainder
        if request.randomize_slices:
            random.shuffle(slices)
        return [s for s in slices if s > 0]


class SlippageModel:
    """Lightweight pre-trade slippage estimator."""

    def estimate(self, request: ExecutionRequest, child_qty: int) -> Dict[str, float]:
        spread = abs(request.ask - request.bid) if request.bid and request.ask else request.ltp * 0.001
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
    """
    Core execution engine.

    Fixes applied:
    - FIX #2: All timestamps now use _utc_now_iso() (UTC) instead of
               datetime.now().isoformat() (local time). This makes the entire
               journal consistently UTC-stamped.
    - FIX #4: signal_id passed to AnomalyDetector.on_signal() now includes
               tradingsymbol, quantity, and source as fallback context so
               duplicate detection actually works when signal_id is blank.
    - FIX #5: execute() no longer calls anomaly_detector.heartbeat() inline.
               Heartbeat must be driven by an external periodic timer
               (see start_heartbeat_timer / stop_heartbeat_timer helpers).
    - FIX #3: record_fill() and record_exit() are the correct hooks to call
               when paper order completion events arrive. A new helper
               record_paper_fill() makes this easy to wire from PaperTradingManager.
    """

    def __init__(
        self,
        trading_mode: str,
        base_dir: Path,
        remediation_hooks: Optional[Dict[str, Callable[..., None]]] = None,
    ):
        self.trading_mode = trading_mode
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

        # FIX #5: heartbeat timer — created here but must be started by the
        # caller once the event loop is running (e.g. in QTimer or threading.Timer).
        self._heartbeat_interval_seconds = 30
        self._heartbeat_thread: Optional[threading.Timer] = None

    # ------------------------------------------------------------------
    # FIX #5: External heartbeat timer management
    # ------------------------------------------------------------------

    def start_heartbeat_timer(self):
        """
        Start a background repeating timer that drives anomaly detection
        independently of whether execute() is being called.
        Call this once after the execution stack is ready.
        """
        self._schedule_next_heartbeat()

    def stop_heartbeat_timer(self):
        """Cancel the heartbeat timer (e.g. on app shutdown)."""
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.cancel()
            self._heartbeat_thread = None

    def _schedule_next_heartbeat(self):
        import threading as _threading
        self._heartbeat_thread = _threading.Timer(
            self._heartbeat_interval_seconds, self._run_heartbeat
        )
        self._heartbeat_thread.daemon = True
        self._heartbeat_thread.start()

    def _run_heartbeat(self):
        try:
            self.anomaly_detector.heartbeat()
            self.tca_reporter.generate(self.journal.path)
        except Exception as exc:
            logger.error("ExecutionStack heartbeat error: %s", exc)
        finally:
            # Re-schedule — keep going forever until stop_heartbeat_timer() is called
            self._schedule_next_heartbeat()

    # ------------------------------------------------------------------
    # Core execute path
    # ------------------------------------------------------------------

    def execute(
        self,
        request: ExecutionRequest,
        place_order_fn: Callable[..., str],
        base_order_args: Dict[str, Any],
    ) -> List[str]:
        trace = TraceContext.new(
            tags={
                "tradingsymbol": request.tradingsymbol,
                "execution_algo": request.execution_algo,
                "source": request.metadata.get("source", "unknown"),
            }
        )

        # FIX #4: build a meaningful signal_id for duplicate detection even when
        # the caller doesn't provide one explicitly.
        raw_signal_id = str(
            request.metadata.get("signal_id")
            or request.metadata.get("auto_token")
            or ""
        )
        self.anomaly_detector.on_signal(
            raw_signal_id,
            tradingsymbol=request.tradingsymbol,
            quantity=request.quantity,
            source=request.metadata.get("source", ""),
        )

        route = self.router.choose_route(request)
        slices = self.planner.plan(request)
        order_ids: List[str] = []

        signal_event = trace.next_span(
            "signal_received",
            {
                "signal_id": raw_signal_id or None,
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
                        # FIX #2: use UTC, not datetime.now()
                        "timestamp": _utc_now_iso(),
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
                        "timestamp": _utc_now_iso(),  # FIX #2
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

        # FIX #5: do NOT call anomaly_detector.heartbeat() here anymore.
        # Heartbeat now runs on its own independent timer started via
        # start_heartbeat_timer().  Calling it here caused it to only run
        # when new orders arrived, missing stuck orders during quiet periods.
        return order_ids

    # ------------------------------------------------------------------
    # Fill / exit recording
    # ------------------------------------------------------------------

    def record_fill(self, order_id: str, filled_price: float, filled_qty: int):
        """
        Call this whenever a fill confirmation arrives (live or paper).
        FIX #3: this is the correct place to close out active_orders so the
        AnomalyDetector stops watching the order.
        """
        self.anomaly_detector.on_order_closed(order_id)
        payload = {
            "order_id": order_id,
            "filled_price": filled_price,
            "filled_qty": filled_qty,
            "status": "filled",
        }
        self.journal.append("order_fill", payload)
        self.dashboard.observe("order_fill", payload)

    def record_paper_fill(self, order_data: Dict[str, Any]):
        """
        Convenience wrapper for PaperTradingManager.order_update callbacks.
        Wire PaperTradingManager.order_update signal to this method so paper
        fills close out the anomaly detector correctly.

        Usage (in main window / execution facade):
            paper_trader.order_update.connect(
                lambda od: execution_stack.record_paper_fill(od)
            )
        """
        if order_data.get("status") != "COMPLETE":
            return
        order_id = order_data.get("order_id", "")
        filled_price = float(order_data.get("average_price") or order_data.get("price") or 0.0)
        filled_qty = int(order_data.get("filled_quantity") or order_data.get("quantity") or 0)
        if order_id and filled_qty > 0:
            self.record_fill(order_id, filled_price, filled_qty)

    def record_cancelled(self, order_id: str):
        """
        Call when an order is cancelled/rejected so it's removed from the
        stuck-order watchlist immediately.
        """
        self.anomaly_detector.on_order_closed(order_id)
        self.journal.append("order_cancelled", {"order_id": order_id, "status": "cancelled"})

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
        """
        Public heartbeat entrypoint — safe to call from Qt QTimer as well.
        Delegates to the anomaly detector and refreshes the TCA report.
        """
        self.anomaly_detector.heartbeat()
        self.tca_reporter.generate(self.journal.path)


# Avoid circular import — import threading at module level is fine
import threading