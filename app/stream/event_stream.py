"""Transport-agnostic event stream manager built on WorkforceRuntime.subscribe_events()."""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from core.events.model import Event

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime

logger = logging.getLogger("AutonomOS.EventStream")


@dataclass
class EventStreamEnvelope:
    """Wire-format envelope for streaming events to clients."""
    type: str  # 'event', 'heartbeat', 'resync'
    sequence_number: Optional[int] = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "sequence_number": self.sequence_number,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventStreamEnvelope:
        return cls(
            type=data.get("type", "event"),
            sequence_number=data.get("sequence_number"),
            data=dict(data.get("data", {})),
        )

    @classmethod
    def from_event(cls, event: Event) -> EventStreamEnvelope:
        return cls(
            type="event",
            sequence_number=event.sequence_number,
            data=event.to_dict(),
        )

    @classmethod
    def heartbeat(cls) -> EventStreamEnvelope:
        return cls(type="heartbeat")

    @classmethod
    def resync(cls, last_sequence: int) -> EventStreamEnvelope:
        return cls(type="resync", sequence_number=last_sequence, data={"action": "resync_required"})


class EventStreamConnection:
    """Per-client event stream connection with dedup and ordering."""

    def __init__(self, client_id: str, since_sequence: int = 0):
        self.client_id = client_id
        self.last_sequence = since_sequence
        self._queue: queue.Queue[EventStreamEnvelope] = queue.Queue(maxsize=1000)
        self._seen_event_ids: set[str] = set()
        self._max_seen_cache = 5000
        self._connected = True

    @property
    def is_connected(self) -> bool:
        return self._connected

    def deliver(self, event: Event) -> None:
        """Deliver an event to this connection, with dedup."""
        if not self._connected:
            return

        # Dedup by event_id
        if event.event_id in self._seen_event_ids:
            return
        self._seen_event_ids.add(event.event_id)
        if len(self._seen_event_ids) > self._max_seen_cache:
            # Trim oldest (approximation — set doesn't preserve order, but prevents unbounded growth)
            self._seen_event_ids = set(list(self._seen_event_ids)[-2500:])

        # Track sequence
        if event.sequence_number is not None:
            self.last_sequence = max(self.last_sequence, event.sequence_number)

        envelope = EventStreamEnvelope.from_event(event)
        try:
            self._queue.put_nowait(envelope)
        except queue.Full:
            logger.warning(f"Event stream queue full for client {self.client_id}, dropping oldest")
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(envelope)
            except queue.Empty:
                pass

    def get_next_event(self, timeout: float = 1.0) -> Optional[EventStreamEnvelope]:
        """Block until next event or timeout. Returns None on timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def disconnect(self) -> None:
        self._connected = False


class EventStreamManager:
    """Manages client subscriptions for real-time event delivery."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime
        self._connections: dict[str, EventStreamConnection] = {}
        self._lock = threading.RLock()
        self._subscribed = False

    def _ensure_subscribed(self) -> None:
        """Lazily subscribe to runtime events on first connection."""
        if not self._subscribed:
            self._runtime.subscribe_events(self._on_event)
            self._subscribed = True

    def _on_event(self, event: Event) -> None:
        """Runtime event callback — fan out to all connected clients."""
        with self._lock:
            connections = list(self._connections.values())
        for conn in connections:
            try:
                conn.deliver(event)
            except Exception as e:
                logger.warning(f"Error delivering event to {conn.client_id}: {e}")

    def connect(self, client_id: str, since_sequence: int = 0) -> EventStreamConnection:
        """Create a new event stream connection for a client.
        
        Delivers any missed events since `since_sequence` from the store,
        then begins live streaming from the runtime subscriber.
        """
        self._ensure_subscribed()

        conn = EventStreamConnection(client_id, since_sequence)

        # Catch up on missed events from persistent store
        if since_sequence > 0:
            missed = self._runtime.store.list_events(since_sequence=since_sequence, limit=500)
            for event in missed:
                conn.deliver(event)

        with self._lock:
            # Disconnect existing connection for same client
            if client_id in self._connections:
                self._connections[client_id].disconnect()
            self._connections[client_id] = conn

        logger.info(f"Event stream connected: {client_id} (since_seq={since_sequence})")
        return conn

    def disconnect(self, client_id: str) -> None:
        """Disconnect a client from the event stream."""
        with self._lock:
            conn = self._connections.pop(client_id, None)
            if conn:
                conn.disconnect()
                logger.info(f"Event stream disconnected: {client_id}")

    def get_connection(self, client_id: str) -> Optional[EventStreamConnection]:
        """Get an existing connection by client ID."""
        with self._lock:
            return self._connections.get(client_id)

    @property
    def connection_count(self) -> int:
        with self._lock:
            return len(self._connections)
