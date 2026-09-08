"""Behavioral tests (PRD §12):
- full event lifecycle on a manual tick
- malformed LLM output -> one retry -> ESCALATE / LLM_UNRELIABLE
- prompt injection in market data is inert (stored as data, never obeyed)
- EXECUTION_RESULT contains portfolio_after
- concurrent tick protection (409-or-serialized contract)
"""
import json
import threading

from app.agent.cycle import (EVENT_DECISION, EVENT_EXECUTION_RESULT,
                             LIFECYCLE, TickConflict)
from app.audit.verify import verify_chain
from app.llm.mock import MockLLM


def _events(services, decision_id=None):
    if decision_id:
        return services.store.events_for_decision(decision_id)
    return services.store.all_events()


def test_manual_tick_produces_complete_lifecycle(services):
    """One manual tick produces the complete event lifecycle (clean scenario)."""
    result = services.cycle.run()
    assert result["outcome"] == "EXECUTE"
    events = _events(services, result["decision_id"])
    assert [e["event_type"] for e in events] == LIFECYCLE
    # decision lifecycle events share one decision id and one run id
    assert {e["decision_id"] for e in events} == {result["decision_id"]}
    assert {e["run_id"] for e in events} == {services.run_id}
    payload = json.loads(events[1]["payload"])
    assert payload["source"] == "simulator"      # honest simulation labeling
    assert set(payload["symbols"]) == {"BTCUSDT", "ETHUSDT"}


def test_blocked_decision_has_no_execution(services):
    services.cycle.run()                    # clean -> EXECUTE
    result = services.cycle.run()           # low_confidence -> BLOCK
    assert result["outcome"] == "BLOCK"
    assert result["reason"] == "BLOCK_CONF_LOW"
    events = _events(services, result["decision_id"])
    types = [e["event_type"] for e in events]
    assert EVENT_EXECUTION_RESULT not in types
    assert types[-1] == EVENT_DECISION


def test_execution_result_contains_portfolio_after(services):
    result = services.cycle.run()
    events = _events(services, result["decision_id"])
    execution = json.loads(events[-1]["payload"])
    assert execution["status"] == "FILLED"
    after = execution["portfolio_after"]
    for field in ("cash_usdt", "BTC", "ETH", "day_realized_pnl_usdt"):
        assert field in after
    # $50.00 at 0.10% -> $0.05 fee; cash reduced by notional + fee
    assert execution["fee_usdt"] == 0.05
    assert after["cash_usdt"] == 1000.00 - 50.00 - 0.05
    assert after["BTC"] > 0


def test_malformed_llm_retries_once_then_escalates(services):
    """Malformed output -> retry once; second malformed output -> ESCALATE
    with LLM_UNRELIABLE (PRD §7, §11)."""
    class StubLLM:
        name = "stub-malformed"

        def __init__(self):
            self.calls = 0

        def propose(self, snapshot, focus_symbol):
            self.calls += 1
            return MockLLM().malformed_response()  # pinned malformed fixture

    stub = StubLLM()
    services.cycle.llm = stub
    result = services.cycle.run()
    assert stub.calls == 2                     # exactly one retry
    assert result["outcome"] == "ESCALATE"
    assert result["reason"] == "ESCALATE_LLM_UNRELIABLE"
    events = _events(services, result["decision_id"])
    analysis = json.loads(events[2]["payload"])
    assert analysis["llm"]["malformed"] is True
    assert analysis["llm"]["attempts"] == 2
    assert "LLM_UNRELIABLE" in analysis["flags"]
    assert EVENT_EXECUTION_RESULT not in [e["event_type"] for e in events]


def test_prompt_injection_in_market_data_is_inert(services):
    """Malicious text inside market data -> treated as untrusted data ->
    ESCALATE / no order. The text is preserved verbatim as DATA."""
    for _ in range(3):                          # clean, low_conf, injection
        result = services.cycle.run()
    assert result["scenario"] == "prompt_injection"
    assert result["outcome"] == "ESCALATE"
    assert result["reason"] == "ESCALATE_PROMPT_INJECTION"
    events = _events(services, result["decision_id"])
    assert EVENT_EXECUTION_RESULT not in [e["event_type"] for e in events]

    snapshot = json.loads(events[1]["payload"])
    # the attack text survives as inert data in the audit record
    assert "ignore all previous instructions" in snapshot["note"].lower()

    analysis = json.loads(events[2]["payload"])
    proposal = analysis["proposal"]
    assert proposal["action"] == "ESCALATE"
    assert "prompt_injection_detected" in proposal["flags"]


def test_concurrent_ticks_are_rejected_not_corrupted(services):
    """Two simultaneous ticks: exactly one wins; the other is rejected.
    Cycle state and chain sequence stay consistent (PRD §8, §11)."""
    services.cycle.phase_delay_ms = 150        # widen the overlap window
    barrier = threading.Barrier(2)
    results = {}

    def worker(name):
        barrier.wait()
        try:
            results[name] = ("ok", services.cycle.run())
        except TickConflict as exc:
            results[name] = ("conflict", str(exc))

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()

    kinds = sorted(v[0] for v in results.values())
    assert kinds == ["conflict", "ok"]          # one serialized, one rejected
    check = verify_chain(services.store.all_events())
    assert check["valid"] is True
    # exactly one cycle started — no half-written second cycle
    from app.agent.cycle import EVENT_CYCLE_STARTED
    assert services.store.count_of_type(EVENT_CYCLE_STARTED) == 1


def _proposal(action, symbol="BTCUSDT", notional=50.0, confidence=0.62):
    return json.dumps({
        "action": action,
        "symbol": symbol,
        "quote_notional_usdt": notional,
        "confidence": confidence,
        "thesis_plain_english": "Stub proposal for a unit test.",
        "factors": [{"name": "test", "value": "yes"}],
        "flags": [],
    })


class _StubLLM:
    name = "stub"

    def __init__(self, raw: str):
        self.raw = raw
        self.calls = 0

    def propose(self, snapshot, focus_symbol):
        self.calls += 1
        return self.raw


def test_cycle_started_records_policy_hash(services):
    result = services.cycle.run()
    events = _events(services, result["decision_id"])
    payload = json.loads(events[0]["payload"])
    assert events[0]["event_type"] == "CYCLE_STARTED"
    assert "policy" in payload
    assert len(payload["policy"]["hash"]) == 64
    assert [r["id"] for r in payload["policy"]["rules"]] == [
        "P-05", "P-01", "P-02", "P-03", "P-04"]


def test_prompt_injection_escalates_even_if_model_buys(services):
    """Independent detector, not the LLM, is the control."""
    services.market._idx = 2  # prompt_injection scenario
    services.cycle.llm = _StubLLM(_proposal("BUY"))
    result = services.cycle.run()
    assert result["scenario"] == "prompt_injection"
    assert result["outcome"] == "ESCALATE"
    assert result["reason"] == "ESCALATE_PROMPT_INJECTION"
    events = _events(services, result["decision_id"])
    analysis = json.loads(events[2]["payload"])
    assert analysis["llm"]["injection_detected"] is True
    assert "prompt_injection_detected" in analysis["proposal"]["flags"]
    assert EVENT_EXECUTION_RESULT not in [e["event_type"] for e in events]


def test_buy_without_cash_is_blocked(services):
    services.store.portfolio_set({
        "cash_usdt": 1.0,
        "positions": {"BTC": {"qty": 0.0, "avg_cost": 0.0},
                      "ETH": {"qty": 0.0, "avg_cost": 0.0}},
        "day_realized_pnl_usdt": 0.0,
    })
    result = services.cycle.run()
    assert result["outcome"] == "BLOCK"
    assert result["reason"] == "BLOCK_INSUFFICIENT_CASH"
    assert result["executed"] is False


def test_sell_without_position_is_blocked(services):
    services.cycle.llm = _StubLLM(_proposal("SELL"))
    result = services.cycle.run()
    assert result["outcome"] == "BLOCK"
    assert result["reason"] == "BLOCK_NO_POSITION"


def test_sell_fill_after_buy(services):
    bought = services.cycle.run()
    assert bought["outcome"] == "EXECUTE"
    services.cycle.llm = _StubLLM(_proposal("SELL", notional=50.0))
    result = services.cycle.run()
    assert result["outcome"] == "EXECUTE"
    events = _events(services, result["decision_id"])
    execution = json.loads(events[-1]["payload"])
    assert execution["side"] == "SELL"
    assert execution["status"] == "FILLED"
    after = execution["portfolio_after"]
    assert after["positions"]["BTC"]["qty"] == 0.0
    assert after["cash_usdt"] > 900


def test_portfolio_cache_tamper_is_rebuilt_from_chain(services):
    first = services.cycle.run()
    assert first["executed"] is True
    real = services.venue.current()
    services.store.portfolio_set({
        "cash_usdt": 999999.0,
        "positions": real["positions"],
        "day_realized_pnl_usdt": 0.0,
    })
    services.cycle.run()  # rebuilds at start; second scenario BLOCKs
    restored = services.venue.current()
    assert restored["cash_usdt"] == real["cash_usdt"]
