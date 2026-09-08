"""Glass Box Agent — application wiring (PRD §3).

Build posture: simulator-first, dry-run only, $0 infrastructure.
Core demo runs entirely on SimulatorMarket + MockLLM; the real Claude
adapter is selected only via GLASSBOX_LLM=claude and never gates the demo.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .agent.cycle import AgentCycle
from .agent.loop import AgentLoop
from .api.routes import router
from .api.sse import Broker
from .audit.chain import AuditStore
from .audit.jsonl import JsonlMirror
from .exec.venue import SimulatedVenue
from .llm.client import ClaudeLLM, LLMUnavailable
from .llm.mock import MockLLM
from .market.simulator import SimulatorMarket
from .policy.engine import Governor, load_policy

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "data"
POLICY_PATH = ROOT / "policy.yaml"
FRONTEND_DIR = ROOT / "frontend"


class Services:
    def __init__(self, data_dir: str | Path | None = None,
                 phase_delay_ms: int | None = None):
        self.data_dir = Path(
            data_dir or os.environ.get("GLASSBOX_DATA_DIR", DEFAULT_DATA_DIR))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.data_dir / "decisions.jsonl"

        self.broker = Broker()
        self.mirror = JsonlMirror(self.jsonl_path)
        self.store = AuditStore(self.data_dir / "glassbox.db",
                                mirror=self.mirror)

        self.policy = load_policy(str(POLICY_PATH))
        self.governor = Governor(self.policy)
        self.watchlist = list(self.policy["watchlist"])

        self.market = SimulatorMarket()

        llm_choice = os.environ.get("GLASSBOX_LLM", "mock").lower()
        if llm_choice == "claude":
            try:
                self.llm = ClaudeLLM(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            except LLMUnavailable:
                # Hard rule (PRD §13): real adapters never gate the demo.
                self.llm = MockLLM()
        else:
            self.llm = MockLLM()

        self.venue = SimulatedVenue(
            self.store,
            fee_bps=float(self.policy["portfolio"]["fee_bps"]))
        self.venue.init_portfolio(self.policy["portfolio"]["initial_cash_usdt"])

        self._agent_state = "RUNNING"
        self.run_id = f"run-{uuid.uuid4().hex[:8]}"

        if phase_delay_ms is None:
            phase_delay_ms = int(os.environ.get(
                "GLASSBOX_TICK_PHASE_DELAY_MS", "350"))
        self.cycle = AgentCycle(
            store=self.store, broker=self.broker, market=self.market,
            llm=self.llm, governor=self.governor, venue=self.venue,
            watchlist=self.watchlist, run_id=self.run_id,
            phase_delay_ms=phase_delay_ms,
            get_state=lambda: self._agent_state,
            on_halt=self._on_halt,
        )
        self.loop = AgentLoop(
            self.cycle,
            interval_s=float(os.environ.get("GLASSBOX_LOOP_INTERVAL_S", "0")))

    # --------------------------------------------------------------
    @property
    def agent_state(self) -> str:
        return self._agent_state

    @agent_state.setter
    def agent_state(self, value: str) -> None:
        self._agent_state = value

    def _on_halt(self) -> None:
        self._agent_state = "HALTED"
        self.on_state_change()

    def on_state_change(self) -> None:
        self.broker.publish_message(
            {"type": "agent_state", "state": self._agent_state})


def create_app(data_dir: str | Path | None = None,
               phase_delay_ms: int | None = None) -> FastAPI:
    app = FastAPI(title="Glass Box Agent — Agent Audit Console",
                  version="3.1.0")
    app.state.services = Services(data_dir=data_dir,
                                  phase_delay_ms=phase_delay_ms)
    app.include_router(router)

    @app.on_event("startup")
    def _startup() -> None:
        app.state.services.loop.start()

    # Dashboard + static assets (vanilla JS, no build step), mounted last so
    # /api/* routes take precedence.
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True),
              name="frontend")
    return app


app = create_app()
