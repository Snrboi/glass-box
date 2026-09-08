"""One dry-run decision cycle (PRD §1, §5, §7).

Lifecycle — five persisted event types forming one readable decision
lifecycle:

    CYCLE_STARTED -> DATA_SNAPSHOT -> ANALYSIS_AND_POLICY -> DECISION
                  -> EXECUTION_RESULT   (only when the outcome is EXECUTE)

Concurrency (PRD §8): manual ticks are serialized by a lock; an
overlapping tick is REJECTED so two simultaneous ticks can never corrupt
cycle state or append an inconsistent sequence.
"""
from __future__ import annotations

import json
import threading
import time

from ..audit.chain import utc_now_iso
from ..llm.client import LLMUnavailable
from ..llm.prompts import validate_proposal
from ..policy.injection import INJECTION_FLAGS, looks_injected

EVENT_CYCLE_STARTED = "CYCLE_STARTED"
EVENT_DATA_SNAPSHOT = "DATA_SNAPSHOT"
EVENT_ANALYSIS_AND_POLICY = "ANALYSIS_AND_POLICY"
EVENT_DECISION = "DECISION"
EVENT_EXECUTION_RESULT = "EXECUTION_RESULT"

LIFECYCLE = [EVENT_CYCLE_STARTED, EVENT_DATA_SNAPSHOT,
             EVENT_ANALYSIS_AND_POLICY, EVENT_DECISION,
             EVENT_EXECUTION_RESULT]


class TickConflict(RuntimeError):
    """A tick is already in progress -> HTTP 409."""


class AgentHalted(RuntimeError):
    """Drawdown breaker fired; ticks refused until halt_clear -> HTTP 409."""


class AgentPaused(RuntimeError):
    """Agent paused via control API -> HTTP 409."""


class AgentCycle:
    def __init__(self, *, store, broker, market, llm, governor, venue,
                 watchlist: list[str], run_id: str,
                 phase_delay_ms: int = 0, get_state=None, on_halt=None,
                 policy_meta: dict | None = None,
                 initial_cash_usdt: float = 1000.0):
        self.store = store
        self.broker = broker
        self.market = market
        self.llm = llm
        self.governor = governor
        self.venue = venue
        self.watchlist = list(watchlist)
        self.run_id = run_id
        self.phase_delay_ms = phase_delay_ms
        self.policy_meta = policy_meta or {}
        self.initial_cash_usdt = float(initial_cash_usdt)
        self._tick_lock = threading.Lock()
        self._get_state = get_state or (lambda: "RUNNING")
        self._on_halt = on_halt or (lambda: None)

    # --------------------------------------------------------------
    def _pace(self) -> None:
        """Demo pacing so the trace appears step-by-step live (0 in tests)."""
        if self.phase_delay_ms:
            time.sleep(self.phase_delay_ms / 1000)

    def _emit(self, decision_id: str, event_type: str, payload: dict) -> dict:
        event = self.store.append(self.run_id, decision_id, event_type, payload)
        self.broker.publish(event)
        return event

    # --------------------------------------------------------------
    def run(self) -> dict:
        if not self._tick_lock.acquire(blocking=False):
            raise TickConflict("a decision cycle is already in progress")
        try:
            state = self._get_state()
            if state == "HALTED":
                raise AgentHalted("agent is halted (P-05 DRAWDOWN_BREAKER)")
            if state == "PAUSED":
                raise AgentPaused("agent is paused")
            return self._run_locked()
        finally:
            self._tick_lock.release()

    # --------------------------------------------------------------
    def _run_locked(self) -> dict:
        # Cache is a materialization of the chain, not a second source of truth.
        self.venue.rebuild_from_chain(self.initial_cash_usdt)

        cycle_index = self.store.count_of_type(EVENT_CYCLE_STARTED) + 1
        decision_id = f"dec-{cycle_index:05d}"

        forecast = self.market.peek_scenario()
        focus_symbol = forecast["symbol"]

        started = {
            "run_id": self.run_id,
            "cycle_index": cycle_index,
            "symbols": self.watchlist,
            "mode": "dry-run",
            "market_source": self.market.name,
            "llm_source": self.llm.name,
            "started_ts": utc_now_iso(),
        }
        if self.policy_meta:
            started["policy"] = self.policy_meta
        self._emit(decision_id, EVENT_CYCLE_STARTED, started)

        self._pace()
        snapshot = self.market.snapshot(self.watchlist)
        self._emit(decision_id, EVENT_DATA_SNAPSHOT, snapshot)

        self._pace()
        injection = looks_injected(snapshot.get("note"))
        proposal, llm_meta = self._obtain_proposal(snapshot, focus_symbol)
        llm_meta["injection_detected"] = injection
        if injection:
            flags = list(proposal.get("flags") or [])
            for flag in INJECTION_FLAGS:
                if flag not in flags:
                    flags.append(flag)
            proposal["flags"] = flags

        book = snapshot["symbols"][proposal["symbol"]]
        portfolio_view = self.venue.governor_portfolio_view(snapshot)
        verdicts = self.governor.evaluate(proposal, book, portfolio_view)

        self._emit(decision_id, EVENT_ANALYSIS_AND_POLICY, {
            "proposal": proposal,
            "flags": proposal["flags"],
            "confidence": proposal["confidence"],
            "llm": llm_meta,
            "verdicts": verdicts,
        })

        self._pace()
        outcome = self.governor.decide(
            proposal, verdicts, llm_malformed=llm_meta["malformed"],
            injection_detected=injection)
        if outcome["outcome"] == "EXECUTE":
            ok, block_reason = self.venue.preflight(proposal)
            if not ok:
                outcome = {
                    "outcome": "BLOCK",
                    "reason": block_reason,
                    "winning_rules": [],
                    "precedence": outcome["precedence"],
                }
        if outcome["outcome"] == "HALT":
            # P-05 DRAWDOWN_BREAKER: escalate + halt. Further ticks are
            # refused until halt_clear (PRD §6, §8).
            self._on_halt()
        self._emit(decision_id, EVENT_DECISION, {
            "action": outcome["outcome"],
            "proposal_action": proposal["action"],
            "symbol": proposal["symbol"],
            "reason": outcome["reason"],
            "winning_rules": outcome["winning_rules"],
            "rule_precedence": outcome["precedence"],
        })

        execution = None
        if outcome["outcome"] == "EXECUTE":
            self._pace()
            execution = self.venue.fill(proposal, book)
            self._emit(decision_id, EVENT_EXECUTION_RESULT, execution)

        return {
            "decision_id": decision_id,
            "cycle_index": cycle_index,
            "scenario": snapshot["scenario"],
            "outcome": outcome["outcome"],
            "reason": outcome["reason"],
            "executed": execution is not None,
        }

    # --------------------------------------------------------------
    def _obtain_proposal(self, snapshot: dict, focus_symbol: str):
        """Model proposes; we validate. Malformed -> retry once -> ESCALATE
        with LLM_UNRELIABLE (PRD §7, §11)."""
        attempts = 0
        errors: list[str] = []
        malformed = False
        raw = None
        for attempts in (1, 2):
            try:
                raw = self.llm.propose(snapshot, focus_symbol)
            except LLMUnavailable as exc:
                errors = [f"llm_unavailable: {exc}"]
                malformed = True
                break
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                errors = ["output is not valid JSON"]
                continue
            proposal, errors = validate_proposal(data)
            if proposal is not None:
                return proposal, {"source": self.llm.name, "attempts": attempts,
                                  "malformed": False, "errors": []}
        else:
            malformed = True
        if not malformed and errors:
            malformed = True

        # Second malformed output -> synthesized ESCALATE (LLM_UNRELIABLE).
        return {
            "action": "ESCALATE",
            "symbol": focus_symbol,
            "quote_notional_usdt": 0.0,
            "confidence": 0.0,
            "thesis_plain_english": (
                "Model output was malformed twice; escalating per the "
                "LLM_UNRELIABLE safety path."),
            "factors": [{"name": "validation_errors", "value": "; ".join(errors)[:120]}],
            "flags": ["LLM_UNRELIABLE"],
        }, {"source": self.llm.name, "attempts": attempts, "malformed": True,
            "errors": errors}
