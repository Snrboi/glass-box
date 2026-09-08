"""Optional auto-loop around the manual tick (PRD §3 layout).

The demo is driven by manual RUN TICK presses; this loop exists so the
agent can also free-run on an interval when configured
(GLASSBOX_LOOP_INTERVAL_S>0). It is DISABLED by default and never required
for the demo. pause/resume/halt semantics are enforced by AgentCycle.
"""
from __future__ import annotations

import threading

from .cycle import AgentCycle, AgentHalted, AgentPaused, TickConflict


class AgentLoop:
    def __init__(self, cycle: AgentCycle, interval_s: float = 0.0):
        self._cycle = cycle
        self._interval = float(interval_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="glassbox-agent-loop")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._cycle.run()
            except (TickConflict, AgentHalted, AgentPaused):
                continue

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
