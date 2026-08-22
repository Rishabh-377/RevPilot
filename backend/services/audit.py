"""
Audit Service
=============

Immutable, append-only audit log for every stage in the recovery pipeline.
Records schema validation, diagnosis, context creation, strategy, guardrail,
execution, outcome, and statistical learning with full trace context.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path

from backend.models.schemas import AuditEvent


class AuditService:
    """Append-only audit trail for the RevPilot recovery pipeline."""

    def __init__(self, persistence_path: Path | str | None = None) -> None:
        self.persistence_path = Path(persistence_path) if persistence_path else None
        self._events: list[AuditEvent] = []
        self._event_index: dict[str, list[AuditEvent]] = defaultdict(list)
        self._idempotency_keys: set[str] = set()
        self._lock = threading.Lock()

    def log(self, event: AuditEvent) -> None:
        """Append an audit event to the log (idempotent)."""
        with self._lock:
            if event.idempotency_key in self._idempotency_keys:
                return
            self._idempotency_keys.add(event.idempotency_key)
            self._events.append(event)
            self._event_index[event.event_id].append(event)

        if self.persistence_path:
            self._persist_event(event)

    def get_trail(self, event_id: str) -> list[AuditEvent]:
        """Retrieve the full audit trail for a payment failure event in chronological order."""
        return list(self._event_index.get(event_id, []))

    def get_all(self) -> list[AuditEvent]:
        """Retrieve all recorded audit events."""
        return list(self._events)

    def count(self) -> int:
        """Return total count of audit events."""
        return len(self._events)

    def clear(self) -> None:
        """Clear all in-memory audit events."""
        self._events.clear()
        self._event_index.clear()
        self._idempotency_keys.clear()

    def _persist_event(self, event: AuditEvent) -> None:
        """Append single audit record to disk."""
        if not self.persistence_path:
            return
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.persistence_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(mode="json"), default=str) + "\n")
