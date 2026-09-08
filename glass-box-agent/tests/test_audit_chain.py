"""Chain & integrity tests (PRD §12):
- Hash correctness for known records
- Canonical JSON stability
- 1,000-event chain verification remains fast
- Sequence-break detection
- Previous-hash break detection
- Payload tamper detection
- Metadata tamper detection (event_type / ts / run_id / decision_id / seq)
- Immutability triggers reject UPDATE and DELETE (PRD §4)
"""
import json
import sqlite3
import time

import pytest

from app.audit.chain import ZERO, AuditStore, canonical, compute_hash
from app.audit.verify import verify_chain

KNOWN_RECORD = {
    "seq": 1,
    "ts": "2026-01-01T00:00:00.000Z",
    "run_id": "run-test",
    "decision_id": "dec-00001",
    "event_type": "CYCLE_STARTED",
    "payload": {"symbols": ["BTCUSDT", "ETHUSDT"], "mode": "dry-run"},
    "prev_hash": ZERO,
}
# Pinned golden vector — recompute only if the canonical record changes.
KNOWN_HASH = "f8e235afcdbcd0f7689611d6e6a4a986b36a32d15346dcc9c226677738603ab4"
KNOWN_CANONICAL = (
    '{"decision_id":"dec-00001","event_type":"CYCLE_STARTED",'
    '"payload":{"mode":"dry-run","symbols":["BTCUSDT","ETHUSDT"]},'
    '"prev_hash":"' + ZERO + '","run_id":"run-test","seq":1,'
    '"ts":"2026-01-01T00:00:00.000Z"}'
)


def test_hash_correctness_known_record():
    assert compute_hash(KNOWN_RECORD) == KNOWN_HASH


def test_canonical_json_stability():
    assert canonical(KNOWN_RECORD) == KNOWN_CANONICAL
    # key order in the source dict must not matter
    shuffled = dict(reversed(list(KNOWN_RECORD.items())))
    assert canonical(shuffled) == KNOWN_CANONICAL
    # ensure_ascii=False: unicode survives unescaped
    rec = dict(KNOWN_RECORD)
    rec["payload"] = {"note": "mômentum — 中文字符"}
    assert "mômentum — 中文字符" in canonical(rec)
    # no whitespace separators anywhere
    assert '", "' not in canonical(KNOWN_RECORD)


def _build_chain(store: AuditStore, n: int):
    for i in range(1, n + 1):
        store.append("run-t", "dec-t", "DECISION", {"i": i})
    return store.all_events()


def test_verify_valid_chain(services):
    events = _build_chain(services.store, 10)
    result = verify_chain(events)
    assert result["valid"] is True
    assert result["checked"] == 10
    assert result["first_break_seq"] is None


def test_payload_tamper_detection(services):
    events = _build_chain(services.store, 5)
    e = dict(events[1])
    payload = json.loads(e["payload"])
    payload["i"] = 999
    e["payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    events[1] = e
    result = verify_chain(events)
    assert result["valid"] is False
    assert result["first_break_seq"] == 2
    assert result["first_break"]["kind"] == "record_hash"


@pytest.mark.parametrize("field", ["event_type", "ts", "run_id", "decision_id"])
def test_metadata_tamper_detection(services, field):
    """Changing event_type, ts, run_id, decision_id or seq fails verification."""
    events = _build_chain(services.store, 5)
    e = dict(events[2])
    e[field] = str(e[field]) + "_tampered"
    events[2] = e
    result = verify_chain(events)
    assert result["valid"] is False
    assert result["first_break_seq"] == 3
    assert result["first_break"]["kind"] == "record_hash"


def test_sequence_break_detection_modified(services):
    events = _build_chain(services.store, 5)
    e = dict(events[2])
    e["seq"] = 99
    events[2] = e
    result = verify_chain(events)
    assert result["valid"] is False
    assert result["first_break_seq"] == 99
    assert result["first_break"]["kind"] == "sequence"


def test_sequence_break_detection_removed_event(services):
    events = _build_chain(services.store, 5)
    removed_seq = events[1]["seq"]
    del events[1]  # attacker deletes an event
    result = verify_chain(events)
    assert result["valid"] is False
    assert result["first_break"]["kind"] in ("sequence", "prev_hash")
    assert result["first_break_seq"] > removed_seq - 1


def test_prev_hash_break_detection(services):
    events = _build_chain(services.store, 5)
    e = dict(events[3])
    e["prev_hash"] = "f" * 64
    events[3] = e
    result = verify_chain(events)
    assert result["valid"] is False
    assert result["first_break_seq"] == 4
    assert result["first_break"]["kind"] == "prev_hash"


def test_1000_event_chain_verification_is_fast(services):
    events = _build_chain(services.store, 1000)
    started = time.perf_counter()
    result = verify_chain(events)
    elapsed = time.perf_counter() - started
    assert result["valid"] is True
    assert result["checked"] == 1000
    assert elapsed < 5.0


def test_triggers_reject_update_and_delete(services):
    _build_chain(services.store, 2)
    conn = sqlite3.connect(services.store.db_path)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE events SET payload = '{}' WHERE seq = 1")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM events WHERE seq = 1")
    conn.close()


def test_tamper_roundtrip_through_database(services, tmp_path):
    """DB-level attack (triggers bypassed as a real attacker must) is
    detected with the correct first_break_seq."""
    _build_chain(services.store, 6)
    victim = services.store.all_events()[2]
    conn = sqlite3.connect(services.store.db_path)
    conn.execute("DROP TRIGGER events_no_update")
    conn.execute("UPDATE events SET event_type = 'decision' WHERE seq = ?",
                 (victim["seq"],))
    conn.commit()
    conn.execute(
        "CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN"
        " SELECT RAISE(ABORT, 'events are immutable: UPDATE rejected'); END;")
    conn.commit()
    conn.close()
    services.store.close()
    store = AuditStore(services.store.db_path)
    result = verify_chain(store.all_events())
    store.close()
    assert result["valid"] is False
    assert result["first_break_seq"] == victim["seq"]
