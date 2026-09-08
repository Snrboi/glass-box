"""API contract tests (PRD §8, §12): decisions, verify, export, dashboard
smoke (SSE event appears + selected decision renders), tick concurrency 409
at the HTTP layer."""
import json
import threading

from app.audit.verify import verify_chain


def test_healthz(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["mode"] == "dry-run"
    assert body["market_source"] == "simulator"


def test_tick_then_decision_detail_renders(client):
    """Dashboard 'selected decision renders' smoke: one full cycle is
    retrievable with its five lifecycle events in order."""
    res = client.post("/api/agent/tick")
    assert res.status_code == 200
    decision_id = res.json()["decision_id"]

    res = client.get(f"/api/decisions/{decision_id}")
    assert res.status_code == 200
    detail = res.json()
    types = [e["event_type"] for e in detail["events"]]
    assert types == ["CYCLE_STARTED", "DATA_SNAPSHOT",
                     "ANALYSIS_AND_POLICY", "DECISION", "EXECUTION_RESULT"]
    # payloads arrive parsed and complete-enough for the drill-down
    analysis = detail["events"][2]["payload"]
    assert "proposal" in analysis and "verdicts" in analysis
    assert len(analysis["verdicts"]) == 5


def test_decision_detail_404(client):
    assert client.get("/api/decisions/dec-nope").status_code == 404


def test_decisions_list_and_summary(client):
    client.post("/api/agent/tick")
    client.post("/api/agent/tick")
    res = client.get("/api/decisions?limit=10")
    assert res.status_code == 200
    body = res.json()
    assert len(body["decisions"]) == 2
    assert body["summary"]["cycles"] == 2
    assert body["summary"]["execute"] + body["summary"]["block"] == 2


def test_chain_verify_endpoint_valid(client):
    client.post("/api/agent/tick")
    res = client.get("/api/chain/verify")
    body = res.json()
    assert body["valid"] is True
    assert body["checked"] == 5


def test_concurrent_tick_returns_409_over_http(client):
    """Second overlapping tick returns 409 and the chain stays consistent."""
    services = client.app.state.services
    services.cycle._tick_lock.acquire()   # simulate a tick in progress
    try:
        res = client.post("/api/agent/tick")
    finally:
        services.cycle._tick_lock.release()
    assert res.status_code == 409
    assert res.json()["error"] == "tick_in_progress"
    assert services.store.count_of_type("CYCLE_STARTED") == 0


def test_agent_control_pause_resume(client):
    res = client.post("/api/agent/control", json={"action": "pause"})
    assert res.json()["agent_state"] == "PAUSED"
    res = client.post("/api/agent/tick")
    assert res.status_code == 409
    assert res.json()["error"] == "agent_paused"
    res = client.post("/api/agent/control", json={"action": "resume"})
    assert res.json()["agent_state"] == "RUNNING"
    res = client.post("/api/agent/control", json={"action": "bogus"})
    assert res.status_code == 400


def test_jsonl_export_preserves_order_and_content(client):
    services = client.app.state.services
    client.post("/api/agent/tick")
    client.post("/api/agent/tick")
    res = client.get("/api/export/decisions.jsonl")
    assert res.status_code == 200
    lines = [json.loads(x) for x in res.text.strip().split("\n")]
    db_events = services.store.all_events()
    assert len(lines) == len(db_events) == 9
    assert [e["seq"] for e in lines] == [e["seq"] for e in db_events]
    assert [e["record_hash"] for e in lines] == \
        [e["record_hash"] for e in db_events]
    # the export itself is independently verifiable (PRD §4: external
    # inspection) — feed it straight back into the verifier
    for e in lines:
        e["payload"] = json.dumps(e["payload"], sort_keys=True,
                                  separators=(",", ":"), ensure_ascii=False)
    assert verify_chain(lines)["valid"] is True


def test_dashboard_smoke(client):
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "GLASS BOX" in html
    assert "RUN TICK" in html
    assert "VERIFY CHAIN" in html
    assert "SIMULATED DATA" in html and "DRY RUN" in html
    # no frontend framework / build pipeline (PRD §2 non-goals)
    assert "cdn" not in html.lower()


def test_sse_event_appears(tmp_path):
    """Dashboard smoke: an SSE event appears on /api/stream when a tick runs.
    Uses a real uvicorn server so the infinite stream cannot wedge the
    TestClient portal; every wait is time-bounded."""
    import time

    import httpx
    import uvicorn

    from app.main import create_app

    app = create_app(data_dir=tmp_path / "sse", phase_delay_ms=0)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=18377, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):  # bounded startup wait
            if server.started:
                break
            time.sleep(0.05)
        assert server.started

        received = []
        with httpx.Client(base_url="http://127.0.0.1:18377",
                          timeout=10) as http:
            with http.stream("GET", "/api/stream") as res:
                assert res.status_code == 200
                assert res.headers["content-type"].startswith(
                    "text/event-stream")
                http.post("/api/agent/tick")
                deadline = time.time() + 10
                for line in res.iter_lines():
                    assert time.time() < deadline, "timed out awaiting SSE"
                    if line.startswith("data: "):
                        received.append(json.loads(line.split("data: ", 1)[1]))
                    if any(m.get("type") == "audit_event" for m in received):
                        break
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    types = [m.get("type") for m in received]
    assert "ready" in types
    assert "audit_event" in types
    audit = next(m for m in received if m.get("type") == "audit_event")
    assert audit["event"]["event_type"] == "CYCLE_STARTED"
