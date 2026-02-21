import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)


class CvdAutomationService(QObject):
    """Background coordinator for CVD automation events/state.

    Dialogs push raw automation events here. The service journals inbound events,
    restores/persists active trade ownership, and forwards events on its own timer
    so order-routing logic is no longer directly coupled to UI signal timing.
    """

    automation_signal_received = Signal(dict)
    market_state_received = Signal(dict)

    def __init__(self, trading_mode: str, base_dir: Path, parent=None):
        super().__init__(parent)
        self.trading_mode = trading_mode
        self.state_file = base_dir / f"cvd_automation_state_{trading_mode}.json"
        self.event_journal_file = base_dir / f"cvd_automation_events_{trading_mode}.jsonl"

        self.positions: dict[int, dict] = {}
        self.market_state: dict[int, dict] = {}
        self._event_queue: Deque[tuple[str, dict]] = deque()

        self._queue_timer = QTimer(self)
        self._queue_timer.setInterval(100)
        self._queue_timer.timeout.connect(self._drain_event_queue)
        self._queue_timer.start()

        self.load_state()

    def submit_automation_signal(self, payload: dict):
        self._enqueue_event("automation_signal", payload)

    def submit_market_state(self, payload: dict):
        self._enqueue_event("market_state", payload)

    def _enqueue_event(self, event_type: str, payload: dict):
        if not isinstance(payload, dict):
            return
        token = payload.get("instrument_token")
        if token is None:
            return

        self._event_queue.append((event_type, payload))
        self._append_event_journal(event_type, payload)

    def _drain_event_queue(self):
        while self._event_queue:
            event_type, payload = self._event_queue.popleft()
            if event_type == "automation_signal":
                self.automation_signal_received.emit(payload)
            elif event_type == "market_state":
                self.market_state_received.emit(payload)

    def persist_state(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": datetime.now().isoformat(),
                "trading_mode": self.trading_mode,
                "positions": {
                    str(token): trade
                    for token, trade in self.positions.items()
                    if isinstance(trade, dict) and trade.get("tradingsymbol")
                },
            }
            tmp_file = self.state_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_file.replace(self.state_file)
        except Exception as exc:
            logger.error("[AUTO-SVC] Failed to persist CVD automation state: %s", exc, exc_info=True)

    def load_state(self):
        if not self.state_file.exists():
            return

        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            persisted_positions = payload.get("positions", {})
            restored = 0

            if isinstance(persisted_positions, dict):
                for token_raw, trade in persisted_positions.items():
                    if not isinstance(trade, dict):
                        continue
                    tradingsymbol = str(trade.get("tradingsymbol") or "").strip()
                    if not tradingsymbol:
                        continue
                    try:
                        token = int(token_raw)
                    except (TypeError, ValueError):
                        continue
                    self.positions[token] = trade
                    restored += 1

            if restored:
                logger.info("[AUTO-SVC] Restored %s persisted CVD automation position(s).", restored)
        except Exception as exc:
            logger.error("[AUTO-SVC] Failed to load CVD automation state: %s", exc, exc_info=True)

    def _append_event_journal(self, event_type: str, payload: dict):
        try:
            self.event_journal_file.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "timestamp": datetime.now().isoformat(),
                "event_type": event_type,
                "payload": payload,
            }
            with self.event_journal_file.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(row))
                fp.write("\n")
        except Exception as exc:
            logger.error("[AUTO-SVC] Failed to append event journal: %s", exc, exc_info=True)
