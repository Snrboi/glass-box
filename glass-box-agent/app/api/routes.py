"""REST + SSE API contract (PRD §8).

  GET  /healthz                        Liveness
  GET  /                               Dashboard
  GET  /api/stream                     SSE event feed
  GET  /api/decisions?limit=N          Recent decisions
  GET  /api/decisions/{id}             Full decision cycle
  GET  /api/chain/verify               Verify chain integrity
  POST /api/agent/tick                 Run one dry-run cycle
  POST /api/agent/control              pause / resume / halt_clear
  GET  /api/export/decisions.jsonl     Export complete chain
"""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..agent.cycle import (EVENT_CYCLE_STARTED, EVENT_DECISION,
                           AgentHalted, AgentPaused, TickConflict)
from ..audit.verify import verify_chain
from .sse import stream_from_queue

router = APIRouter()


class ControlBody(BaseModel):
    action: str  # pause | resume | halt_clear


# --------------------------------------------------------------------- util
def _services(request: Request):
    return request.app.state.services


def _event_client_view(event: dict) -> dict:
    e = dict(event)
    e["payload"] = json.loads(e["payload"]) if isinstance(e["payload"], str) \
        else e["payload"]
    return e


def _decision_summary(store, decision_event: dict) -> dict:
    decision_id = decision_event["decision_id"]
    events = store.events_for_decision(decision_id)
    by_type = {e["event_type"]: e for e in events}
    analysis = json.loads(by_type["ANALYSIS_AND_POLICY"]["payload"]) \
        if "ANALYSIS_AND_POLICY" in by_type else {}
    snap = json.loads(by_type["DATA_SNAPSHOT"]["payload"]) \
        if "DATA_SNAPSHOT" in by_type else {}
    decision = json.loads(decision_event["payload"])
    proposal = analysis.get("proposal", {})
    return {
        "decision_id": decision_id,
        "seq": decision_event["seq"],
        "ts": decision_event["ts"],
        "run_id": decision_event["run_id"],
        "scenario": snap.get("scenario"),
        "symbol": decision.get("symbol") or proposal.get("symbol"),
        "proposal_action": decision.get("proposal_action"),
        "outcome": decision.get("action"),
        "reason": decision.get("reason"),
        "winning_rules": decision.get("winning_rules", []),
        "confidence": analysis.get("confidence"),
        "quote_notional_usdt": proposal.get("quote_notional_usdt"),
        "executed": "EXECUTION_RESULT" in by_type,
    }


def _summary_counters(store) -> dict:
    counters = {"cycles": store.count_of_type(EVENT_CYCLE_STARTED),
                "execute": 0, "block": 0, "escalate": 0, "halt": 0, "hold": 0}
    for e in store.events_of_type(EVENT_DECISION):
        outcome = json.loads(e["payload"]).get("action", "")
        key = outcome.lower()
        if key in counters:
            counters[key] += 1
    return counters


# ------------------------------------------------------------------- routes
@router.get("/healthz")
def healthz(request: Request):
    s = _services(request)
    return {
        "status": "ok",
        "mode": "dry-run",
        "agent_state": s.agent_state,
        "run_id": s.run_id,
        "events": s.store.count(),
        "market_source": s.market.name,
        "llm_source": s.llm.name,
    }


@router.get("/api/stream")
def stream(request: Request):
    s = _services(request)
    q = s.broker.subscribe()

    def gen():
        try:
            yield from stream_from_queue(q, threading.Event())
        finally:
            s.broker.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/decisions")
def decisions(request: Request, limit: int = Query(50, ge=1, le=500)):
    s = _services(request)
    decision_events = s.store.events_of_type(EVENT_DECISION)[-limit:]
    return {
        "decisions": [_decision_summary(s.store, e)
                      for e in reversed(decision_events)],
        "summary": _summary_counters(s.store),
        "agent_state": s.agent_state,
        "events": s.store.count(),
    }


@router.get("/api/decisions/{decision_id}")
def decision_detail(request: Request, decision_id: str):
    s = _services(request)
    events = s.store.events_for_decision(decision_id)
    if not events:
        return JSONResponse({"error": "decision_not_found"}, status_code=404)
    return {
        "decision_id": decision_id,
        "events": [_event_client_view(e) for e in events],
    }


@router.get("/api/chain/verify")
def chain_verify(request: Request):
    s = _services(request)
    result = verify_chain(s.store.all_events())
    result["total"] = s.store.count()
    return result


@router.post("/api/agent/tick")
def agent_tick(request: Request):
    s = _services(request)
    try:
        result = s.cycle.run()
    except TickConflict:
        return JSONResponse(
            {"error": "tick_in_progress",
             "detail": "a decision cycle is already running; concurrent "
                       "ticks are rejected to protect chain integrity"},
            status_code=409)
    except AgentHalted:
        return JSONResponse(
            {"error": "agent_halted",
             "detail": "P-05 DRAWDOWN_BREAKER fired; clear the halt via "
                       "POST /api/agent/control {action: halt_clear}"},
            status_code=409)
    except AgentPaused:
        return JSONResponse({"error": "agent_paused"}, status_code=409)
    return {"status": "ok", "agent_state": s.agent_state, **result}


@router.post("/api/agent/control")
def agent_control(request: Request, body: ControlBody):
    s = _services(request)
    action = body.action
    if action == "pause":
        if s.agent_state == "HALTED":
            return JSONResponse({"error": "agent_halted"}, status_code=409)
        s.agent_state = "PAUSED"
    elif action == "resume":
        if s.agent_state == "HALTED":
            return JSONResponse({"error": "agent_halted"}, status_code=409)
        s.agent_state = "RUNNING"
    elif action == "halt_clear":
        if s.agent_state != "HALTED":
            return JSONResponse({"error": "not_halted"}, status_code=409)
        s.agent_state = "RUNNING"
    else:
        return JSONResponse(
            {"error": "unknown_action",
             "detail": "expected pause | resume | halt_clear"},
            status_code=400)
    s.on_state_change()
    return {"status": "ok", "agent_state": s.agent_state}


@router.get("/api/export/decisions.jsonl")
def export_jsonl(request: Request):
    s = _services(request)
    if not s.jsonl_path.exists():
        return JSONResponse({"error": "no_chain_yet"}, status_code=404)
    return FileResponse(str(s.jsonl_path),
                        media_type="application/x-ndjson",
                        filename="decisions.jsonl")
