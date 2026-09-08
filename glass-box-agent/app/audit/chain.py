"""Hash-chained, append-only audit store.

Every persisted event is immutable and cryptographically linked to the
previous event. The hash protects the *complete event envelope* — seq, ts,
run_id, decision_id, event_type, payload and prev_hash — not merely the
payload (PRD §4 / Appendix A).

Storage:
  - SQLite ``events`` table, INSERT-only by contract.
  - SQLite triggers REJECT UPDATE and DELETE on events.
  - The application never exposes an event-edit API.
  - A JSONL mirror of the full chain is maintained for export/inspection.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .sign import head_mac, key_fingerprint, load_or_create_key

ZERO = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    decision_id  TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    record_hash  TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are immutable: UPDATE rejected');
END;
CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are immutable: DELETE rejected');
END;
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_state (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(record: dict) -> str:
    """Canonical JSON — must stay byte-stable across runs and machines."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(record: dict) -> str:
    return hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()


class AuditStore:
    """Thread-safe append-only store. A single lock serializes appends so
    two concurrent writers can never fork the sequence."""

    def __init__(self, db_path: str | Path, mirror=None,
                 key_path: str | Path | None = None):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()
        self.mirror = mirror  # app.audit.jsonl.JsonlMirror | None
        self.key_path = Path(
            key_path if key_path is not None
            else Path(self.db_path).parent / "chain.key")
        self._key = load_or_create_key(self.key_path)

    # ------------------------------------------------------------------ write
    def append(self, run_id: str, decision_id: str, event_type: str,
               payload: dict, ts: str | None = None) -> dict:
        """Append one immutable event. Returns the stored event (with hash)."""
        ts = ts or utc_now_iso()
        with self._lock:
            cur = self._conn.execute(
                "SELECT seq, record_hash FROM events ORDER BY seq DESC LIMIT 1")
            row = cur.fetchone()
            seq = 1 if row is None else row["seq"] + 1
            prev_hash = ZERO if row is None else row["record_hash"]

            record = {
                "seq": seq,
                "ts": ts,
                "run_id": run_id,
                "decision_id": decision_id,
                "event_type": event_type,
                "payload": payload,
                "prev_hash": prev_hash,
            }
            record_hash = compute_hash(record)

            payload_text = (canonical(payload) if isinstance(payload, dict)
                            else json.dumps(payload))
            self._conn.execute(
                "INSERT INTO events (seq, ts, run_id, decision_id, event_type,"
                "                    payload, prev_hash, record_hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, ts, run_id, decision_id, event_type,
                 payload_text, prev_hash, record_hash),
            )
            mac = head_mac(self._key, seq, record_hash)
            for meta_key, meta_value in (
                ("head_seq", str(seq)),
                ("head_hash", record_hash),
                ("head_mac", mac),
            ):
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (meta_key, meta_value),
                )
            self._conn.commit()

        event = dict(record)
        event["record_hash"] = record_hash
        if self.mirror is not None:
            self.mirror.append(event)
        return event

    # ------------------------------------------------------------------- read
    def all_events(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY seq ASC").fetchall()
        return [self._row_to_event(r) for r in rows]

    def events_for_decision(self, decision_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE decision_id = ? ORDER BY seq ASC",
                (decision_id,)).fetchall()
        return [self._row_to_event(r) for r in rows]

    def events_of_type(self, event_type: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE event_type = ? ORDER BY seq ASC",
                (event_type,)).fetchall()
        return [self._row_to_event(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]

    def count_of_type(self, event_type: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) c FROM events WHERE event_type = ?",
                (event_type,)).fetchone()["c"]

    # ------------------------------------------------------------------- meta
    def meta_get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))
            self._conn.commit()

    # ------------------------------------------------------------- portfolio
    def portfolio_get(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM portfolio_state WHERE id = 1").fetchone()
        return json.loads(row["state"]) if row else None

    def portfolio_set(self, state: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO portfolio_state (id, state) VALUES (1, ?)"
                " ON CONFLICT(id) DO UPDATE SET state = excluded.state",
                (json.dumps(state, sort_keys=True),))
            self._conn.commit()

    # ------------------------------------------------------------------ misc
    def raw_connection_for_tamper_demo(self) -> sqlite3.Connection:
        """Demo/attack tooling only — see tools/tamper_demo.py."""
        return self._conn

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict:
        return {
            "seq": row["seq"],
            "ts": row["ts"],
            "run_id": row["run_id"],
            "decision_id": row["decision_id"],
            "event_type": row["event_type"],
            "payload": row["payload"],          # JSON text (canonical form)
            "prev_hash": row["prev_hash"],
            "record_hash": row["record_hash"],
        }

    def chain_key(self) -> bytes:
        return self._key

    def key_id(self) -> str:
        return key_fingerprint(self._key)

    def head_state(self) -> dict:
        return {
            "seq": self.meta_get("head_seq"),
            "hash": self.meta_get("head_hash"),
            "mac": self.meta_get("head_mac"),
            "key_id": self.key_id(),
        }

    def close(self) -> None:
        self._conn.close()
