import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FIX #2: Unified UTC timestamp helper — used everywhere so the journal
#          never has mixed local-time vs UTC entries.
# ---------------------------------------------------------------------------
def _utc_now_iso() -> str:
    """Always returns a UTC ISO-8601 string with 'Z' suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
            "timestamp": _utc_now_iso(),  # FIX #2: always UTC
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

    _WINDOW = 3600  # seconds to retain events in rolling window

    def __init__(self, trading_mode: str, base_dir: Path, max_events: int = 2000):
        self.path = base_dir / f"telemetry_snapshot_{trading_mode}.json"
        self._lock = threading.Lock()
        self._events: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._counters: Dict[str, int] = defaultdict(int)

    def observe(self, event_type: str, payload: Dict[str, Any]):
        with self._lock:
            self._events.append({"event_type": event_type, "ts": time.time(), **payload})
            self._counters[event_type] += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "generated_at": _utc_now_iso(),
                "counters": dict(self._counters),
                "recent_events": list(self._events)[-50:],
            }

    def persist(self):
        snap = self.snapshot()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as fp:
                json.dump(snap, fp, indent=2, default=str)
        except Exception as exc:
            logger.error("Failed to persist telemetry snapshot: %s", exc)


class IncidentResponder:
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


# ---------------------------------------------------------------------------
# Stuck-order alert state — tracks when we last fired an alert per order so
# we can apply a cooldown and avoid spamming the journal.
# ---------------------------------------------------------------------------
_STUCK_ORDER_ALERT_COOLDOWN_SECONDS = 300   # re-alert at most once every 5 min per order
_STUCK_ORDER_MAX_AGE_SECONDS = 600          # auto-evict from active_orders after 10 min


class AnomalyDetector:
    """
    Detects runtime anomalies: stale ticks, stuck orders, duplicate signals,
    and runaway event loops.

    Key fixes vs. original:
    - FIX #1: stuck orders are now REMOVED from active_orders after
      _STUCK_ORDER_MAX_AGE_SECONDS so they cannot fire alerts forever.
    - FIX #1: per-order alert cooldown (_stuck_alerted_at) prevents the same
      order from producing a new incident every ~20 s heartbeat tick.
    - FIX #5: heartbeat() is designed to be called from an external periodic
      timer, not only from execute(). Call it from a QTimer / threading.Timer
      at a fixed interval (e.g. every 30 s) so it works even when no new
      orders arrive.
    - FIX #4: on_signal() now uses a stable signal_id derived from
      (tradingsymbol, quantity, source) when an explicit signal_id is absent,
      so duplicate detection always has something meaningful to compare.
    """

    def __init__(
        self,
        responder: IncidentResponder,
        stale_tick_seconds: int = 10,
        loop_threshold: int = 80,
    ):
        self.responder = responder
        self.stale_tick_seconds = stale_tick_seconds
        self.loop_threshold = loop_threshold

        self.last_tick_ts: Dict[str, float] = {}
        # order_id -> created_at (unix time, set once and NEVER reset)
        self.active_orders: Dict[str, float] = {}
        # FIX #1: track last alert time per order to apply cooldown
        self._stuck_alerted_at: Dict[str, float] = {}

        self.signal_seen: Dict[str, float] = {}
        self.loop_window: Deque[float] = deque(maxlen=200)

    def on_tick(self, symbol: str, tick_ts: Optional[float] = None):
        now = tick_ts or time.time()
        self.last_tick_ts[symbol] = now
        self.loop_window.append(now)
        self._detect_runaway_loop(now)

    # FIX #4: derive a fallback signal_id so detection always fires
    def on_signal(self, signal_id: str, *, tradingsymbol: str = "", quantity: int = 0, source: str = ""):
        now = time.time()
        # If the caller passes a blank/None signal_id, build one from context
        effective_id = signal_id or f"{tradingsymbol}:{quantity}:{source}"
        if not effective_id:
            return
        if effective_id in self.signal_seen and now - self.signal_seen[effective_id] < 30:
            self.responder.trigger("duplicate_signal", "medium", {"signal_id": effective_id})
        self.signal_seen[effective_id] = now

    def on_order_submitted(self, order_id: str):
        self.active_orders[order_id] = time.time()

    def on_order_closed(self, order_id: str):
        """Call this when a fill/cancel/rejection is confirmed."""
        self.active_orders.pop(order_id, None)
        self._stuck_alerted_at.pop(order_id, None)

    def heartbeat(self):
        """
        Should be called on a fixed external timer (e.g. every 30 s).
        Do NOT rely on execute() calling this — execute() may not be called
        when the market is quiet.
        """
        now = time.time()

        # --- stale tick check ---
        for symbol, ts in list(self.last_tick_ts.items()):
            if now - ts > self.stale_tick_seconds:
                self.responder.trigger(
                    "stale_tick",
                    "high",
                    {"symbol": symbol, "seconds_since_tick": round(now - ts, 2)},
                )
                self.last_tick_ts[symbol] = now  # snooze per-symbol

        # --- stuck order check (FIX #1) ---
        for order_id, created in list(self.active_orders.items()):
            age = now - created

            # FIX #1a: auto-evict very old orders — they will never get a fill
            # callback (especially in paper mode) so keeping them just pollutes
            # the journal.  Log once at eviction time.
            if age > _STUCK_ORDER_MAX_AGE_SECONDS:
                logger.warning(
                    "AnomalyDetector: auto-evicting order %s after %.0f s without fill callback",
                    order_id,
                    age,
                )
                self.journal_eviction(order_id, age)
                self.active_orders.pop(order_id, None)
                self._stuck_alerted_at.pop(order_id, None)
                continue

            # FIX #1b: only fire an alert if we haven't alerted recently
            if age > 20:
                last_alert = self._stuck_alerted_at.get(order_id, 0.0)
                if now - last_alert >= _STUCK_ORDER_ALERT_COOLDOWN_SECONDS:
                    self.responder.trigger(
                        "stuck_order",
                        "critical",
                        {"order_id": order_id, "open_for_seconds": round(age, 2)},
                    )
                    self._stuck_alerted_at[order_id] = now
                # NOTE: we do NOT reset active_orders[order_id] = now anymore.
                # The age must keep growing so we can evict correctly above.

    def journal_eviction(self, order_id: str, age: float):
        """Helper so the responder can log the eviction event."""
        self.responder.journal.append(
            "order_evicted",
            {
                "order_id": order_id,
                "open_for_seconds": round(age, 2),
                "reason": "no_fill_callback_within_max_age",
            },
        )

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
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rows.append(json.loads(stripped))
                except json.JSONDecodeError:
                    continue

        placements = [r for r in rows if r.get("event_type") == "order_placed"]
        rejects = [r for r in rows if r.get("event_type") == "order_error"]
        fills = [r for r in rows if r.get("event_type") == "order_fill"]
        exits = [r for r in rows if r.get("event_type") == "position_exit"]
        incidents = [r for r in rows if r.get("event_type") == "incident"]
        stuck = [r for r in incidents if r.get("incident") == "stuck_order"]

        avg_latency = round(
            sum(float(p.get("latency_ms", 0.0)) for p in placements) / max(1, len(placements)), 4
        )
        avg_slippage = round(
            sum(float(p.get("expected_slippage", 0.0)) for p in placements) / max(1, len(placements)), 4
        )
        hit_ratio = round(
            (len([e for e in exits if str(e.get("outcome", "")).lower() == "win"]) / max(1, len(exits))) * 100,
            2,
        )
        fill_rate = round(len(fills) / max(1, len(placements)) * 100, 2)

        report = {
            "generated_at": _utc_now_iso(),
            "orders_placed": len(placements),
            "orders_filled": len(fills),
            "fill_rate_pct": fill_rate,
            "orders_rejected": len(rejects),
            "reject_rate_pct": round((len(rejects) / max(1, len(placements) + len(rejects))) * 100, 2),
            "avg_latency_ms": avg_latency,
            "avg_expected_slippage": avg_slippage,
            "hit_ratio_pct": hit_ratio,
            "total_incidents": len(incidents),
            "stuck_order_incidents": len(stuck),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fp:
            json.dump(report, fp, indent=2)