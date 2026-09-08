"""Server-Sent Events broker (PRD §8: GET /api/stream).

The console streams the decision trace live: every audit event is published
to all connected subscribers as it is appended. Thread-safe and
framework-light — publishing happens from the (threadpool) tick context.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Iterator

log = logging.getLogger("glassbox.sse")


def format_sse(payload: dict, event: str | None = None) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


class Broker:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict) -> None:
        msg = {"type": "audit_event", "event": event}
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                log.warning("sse subscriber queue full; dropping audit event")

    def publish_message(self, msg: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                log.warning("sse subscriber queue full; dropping message")


def stream_from_queue(q: queue.Queue, stop: threading.Event) -> Iterator[str]:
    """Sync generator consumed by StreamingResponse."""
    yield format_sse({"type": "ready"})
    while not stop.is_set():
        try:
            msg = q.get(timeout=15)
            yield format_sse(msg)
        except queue.Empty:
            yield ": heartbeat\n\n"
