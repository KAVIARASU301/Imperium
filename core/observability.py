import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


@dataclass
class TraceContext:
    trace_id: str
    parent_span_id: Optional[str] = None
    tags: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new(tags: Optional[Dict[str, Any]] = None) -> "TraceContext":
        return TraceContext(trace_id=uuid.uuid4().hex, tags=tags or {})

    def next_span(self, operation: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        span_id = uuid.uuid4().hex[:16]
        payload = {
            "trace_id": self.trace_id,
            "span_id": span_id,
            "parent_span_id": self.parent_span_id,
            "operation": operation,
            "timestamp": _utc_now_iso(),
        }
        if self.tags:
            payload["tags"] = dict(self.tags)
        if extra:
            payload.update(extra)
        self.parent_span_id = span_id
        return payload


class ExecutionJournal:
    """Append-only event journal to support incident forensics and attribution."""

    def __init__(self, trading_mode: str, base_dir: Path):
        self.path = base_dir / f"execution_journal_{trading_mode}.jsonl"
        self._lock = threading.Lock()

    def append(self, event_type: str, payload: Dict[str, Any]):
        entry = {
            "event_type": event_type,
            "timestamp": _utc_now_iso(),
            **payload,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, default=str))
                fp.write("\n")
        except Exception as exc:
            logger.error("Failed to append execution journal event: %s", exc)


class TelemetryDashboard:
    """In-memory rolling dashboard + periodic persisted snapshot for operations."""

    def __init__(self, trading_mode: str, base_dir: Path, window_size: int = 500):
        self.path = base_dir / f"telemetry_dashboard_{trading_mode}.json"
        self.latency_ms: Deque[float] = deque(maxlen=window_size)
        self.slippage: Deque[float] = deque(maxlen=window_size)
        self.rejects: Deque[int] = deque(maxlen=window_size)
        self.hit_markers: Deque[int] = deque(maxlen=window_size)
        self.utilization: Deque[float] = deque(maxlen=window_size)
        self.events_by_type: Dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, event_type: str, payload: Dict[str, Any]):
        with self._lock:
            self.events_by_type[event_type] += 1
            latency = payload.get("latency_ms")
            if isinstance(latency, (int, float)):
                self.latency_ms.append(float(latency))
            expected_slippage = payload.get("expected_slippage")
            if isinstance(expected_slippage, (int, float)):
                self.slippage.append(float(expected_slippage))
            status = str(payload.get("status") or "").lower()
            self.rejects.append(1 if status in {"error", "rejected"} else 0)
            outcome = str(payload.get("outcome") or "").lower()
            if outcome:
                self.hit_markers.append(1 if outcome in {"win", "hit", "target_hit"} else 0)
            used = payload.get("risk_used")
            total = payload.get("risk_total")
            if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0:
                self.utilization.append(float(used) / float(total))
            self._persist_snapshot_locked()

    def _persist_snapshot_locked(self):
        snapshot = self.snapshot()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as fp:
                json.dump(snapshot, fp, indent=2)
        except Exception as exc:
            logger.error("Failed to write telemetry dashboard snapshot: %s", exc)

    def snapshot(self) -> Dict[str, Any]:
        def _avg(items: Deque[float]) -> float:
            return round(sum(items) / len(items), 4) if items else 0.0

        reject_rate = round((sum(self.rejects) / len(self.rejects)) * 100, 2) if self.rejects else 0.0
        hit_ratio = round((sum(self.hit_markers) / len(self.hit_markers)) * 100, 2) if self.hit_markers else 0.0
        risk_util = round(_avg(self.utilization) * 100, 2) if self.utilization else 0.0
        return {
            "timestamp": _utc_now_iso(),
            "latency_avg_ms": _avg(self.latency_ms),
            "slippage_avg": _avg(self.slippage),
            "reject_rate_pct": reject_rate,
            "hit_ratio_pct": hit_ratio,
            "risk_utilization_pct": risk_util,
            "event_counts": dict(self.events_by_type),
        }


class IncidentResponder:
    """Runs simple playbooks and optional auto-remediation hooks."""

    def __init__(self, journal: ExecutionJournal, remediation_hooks: Optional[Dict[str, Callable[..., None]]] = None):
        self.journal = journal
        self.remediation_hooks = remediation_hooks or {}

    def trigger(self, incident: str, severity: str, details: Dict[str, Any]):
        payload = {
            "incident": incident,
            "severity": severity,
            "details": details,
            "playbook": self._playbook_for(incident),
        }
        self.journal.append("incident", payload)
        self._auto_remediate(incident, payload)

    def _playbook_for(self, incident: str) -> List[str]:
        playbooks = {
            "stuck_order": ["pause_strategy", "unwind_risk"],
            "stale_tick": ["pause_strategy", "reroute_data_feed"],
            "duplicate_signal": ["pause_strategy"],
            "runaway_loop": ["pause_strategy", "unwind_risk", "reroute_execution"],
        }
        return playbooks.get(incident, ["pause_strategy"])

    def _auto_remediate(self, incident: str, payload: Dict[str, Any]):
        action_alias = {
            "pause_strategy": "pause_strategy",
            "unwind_risk": "unwind_risk",
            "reroute_data_feed": "reroute",
            "reroute_execution": "reroute",
        }
        for action in payload.get("playbook", []):
            hook_name = action_alias.get(action)
            hook = self.remediation_hooks.get(hook_name)
            if not hook:
                continue
            try:
                hook(incident=incident, payload=payload)
                self.journal.append("incident_action", {"incident": incident, "action": action, "status": "executed"})
            except Exception as exc:
                self.journal.append(
                    "incident_action",
                    {"incident": incident, "action": action, "status": "failed", "error": str(exc)},
                )


class AnomalyDetector:
    def __init__(self, responder: IncidentResponder, stale_tick_seconds: int = 10, loop_threshold: int = 80):
        self.responder = responder
        self.stale_tick_seconds = stale_tick_seconds
        self.loop_threshold = loop_threshold
        self.last_tick_ts: Dict[str, float] = {}
        self.active_orders: Dict[str, float] = {}
        self.signal_seen: Dict[str, float] = {}
        self.loop_window: Deque[float] = deque(maxlen=200)

    def on_tick(self, symbol: str, tick_ts: Optional[float] = None):
        now = tick_ts or time.time()
        self.last_tick_ts[symbol] = now
        self.loop_window.append(now)
        self._detect_runaway_loop(now)

    def on_signal(self, signal_id: str):
        now = time.time()
        if signal_id in self.signal_seen and now - self.signal_seen[signal_id] < 30:
            self.responder.trigger("duplicate_signal", "medium", {"signal_id": signal_id})
        self.signal_seen[signal_id] = now

    def on_order_submitted(self, order_id: str):
        self.active_orders[order_id] = time.time()

    def on_order_closed(self, order_id: str):
        self.active_orders.pop(order_id, None)

    def heartbeat(self):
        now = time.time()
        for symbol, ts in list(self.last_tick_ts.items()):
            if now - ts > self.stale_tick_seconds:
                self.responder.trigger("stale_tick", "high", {"symbol": symbol, "seconds_since_tick": round(now - ts, 2)})
                self.last_tick_ts[symbol] = now

        for order_id, created in list(self.active_orders.items()):
            age = now - created
            if age > 20:
                self.responder.trigger("stuck_order", "critical", {"order_id": order_id, "open_for_seconds": round(age, 2)})
                self.active_orders[order_id] = now

    def _detect_runaway_loop(self, now: float):
        latest = [t for t in self.loop_window if now - t <= 1.0]
        if len(latest) >= self.loop_threshold:
            self.responder.trigger("runaway_loop", "critical", {"events_per_second": len(latest)})
            self.loop_window.clear()


class TCAReporter:
    """Creates periodic post-trade execution attribution reports from journal records."""

    def __init__(self, trading_mode: str, base_dir: Path):
        self.path = base_dir / f"tca_report_{trading_mode}.json"

    def generate(self, journal_path: Path):
        if not journal_path.exists():
            return
        rows: List[Dict[str, Any]] = []
        with journal_path.open("r", encoding="utf-8") as fp:
            for line in fp:
                with_context = line.strip()
                if not with_context:
                    continue
                try:
                    rows.append(json.loads(with_context))
                except json.JSONDecodeError:
                    continue

        placements = [r for r in rows if r.get("event_type") == "order_placed"]
        rejects = [r for r in rows if r.get("event_type") == "order_error"]
        exits = [r for r in rows if r.get("event_type") == "position_exit"]

        avg_latency = round(sum(float(p.get("latency_ms", 0.0)) for p in placements) / max(1, len(placements)), 4)
        avg_slippage = round(sum(float(p.get("expected_slippage", 0.0)) for p in placements) / max(1, len(placements)), 4)
        hit_ratio = round((len([e for e in exits if str(e.get("outcome", "")).lower() == "win"]) / max(1, len(exits))) * 100, 2)

        report = {
            "generated_at": _utc_now_iso(),
            "orders_placed": len(placements),
            "orders_rejected": len(rejects),
            "reject_rate_pct": round((len(rejects) / max(1, len(placements) + len(rejects))) * 100, 2),
            "avg_latency_ms": avg_latency,
            "avg_expected_slippage": avg_slippage,
            "hit_ratio_pct": hit_ratio,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fp:
            json.dump(report, fp, indent=2)
