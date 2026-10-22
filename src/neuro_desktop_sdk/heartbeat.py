"""One stoppable, non-overlapping heartbeat worker per running integration."""

from __future__ import annotations

import threading
from types import TracebackType
from typing import Any

from .client import IntegrationClient, SDKError, State
from .manifest import clean_text


class Heartbeat:
    """Send every 15 seconds; Neurcro expires readiness after 45 seconds.

    ``start`` sends once synchronously so a missing grant is visible immediately.
    Transient errors are retained as ``last_error`` and retried next interval.
    No process, game, input device or official Neuro websocket is started here.
    """

    def __init__(self, client: IntegrationClient, *, state: State = "ready", message: str | None = None, interval: float = 15):
        if not 1 <= interval <= 20:
            raise ValueError("heartbeat interval must be between 1 and 20 seconds")
        if state not in {"ready", "busy"}:
            raise ValueError("running heartbeat state must be ready or busy")
        self.client = client
        self.interval = interval
        self._state = state
        self._message = None if message is None else clean_text(message, "message", 500)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_error: SDKError | None = None
        self.last_response: dict[str, Any] = {}

    def _send(self) -> None:
        with self._lock:
            state, message = self._state, self._message
        self.last_response = self.client.heartbeat(state, message)

    def start(self) -> Heartbeat:
        if self._thread is not None or self._stop.is_set():
            raise RuntimeError("A heartbeat helper can only be started once")
        self._send()
        self._thread = threading.Thread(target=self._run, name="neuro-desktop-heartbeat", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._send()
                self.last_error = None
            except SDKError as exc:
                self.last_error = exc

    def update(self, state: State, message: str | None = None) -> None:
        if state not in {"ready", "busy"}:
            raise ValueError("running heartbeat state must be ready or busy")
        message = None if message is None else clean_text(message, "message", 500)
        with self._lock:
            self._state, self._message = state, message

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._thread is None:
            return
        # Requests are bounded by the client's timeout; join before offline so a
        # late ready response cannot revive an integration after shutdown.
        self._thread.join(self.client.timeout + 1)
        if self._thread.is_alive():
            self.last_error = SDKError("Heartbeat shutdown is waiting for an outstanding request; readiness will expire")
            return
        try:
            self.client.hide()
        except SDKError as exc:
            self.last_error = exc
        try:
            self.client.heartbeat("offline")
        except SDKError as exc:
            self.last_error = exc

    def __enter__(self) -> Heartbeat:
        return self.start()

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()
