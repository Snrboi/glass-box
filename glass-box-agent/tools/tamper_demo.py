#!/usr/bin/env python3
"""Tamper demonstration (PRD §10 money shot, §15 'tamper states reproducible').

Simulates an attacker with raw database file access: it bypasses the
application, drops the immutability triggers the way a real attacker must,
edits ONE protected field of one event, and restores the triggers. The next
VERIFY CHAIN must report valid:false with that event's seq as first_break.

    python tools/tamper_demo.py                  # tamper latest EXECUTION_RESULT payload
    python tools/tamper_demo.py --seq 7          # tamper a specific event's payload
    python tools/tamper_demo.py --field event_type --seq 4

This only touches local dry-run demo data. Re-seed afterwards with
tools/demo_setup.py.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIELDS = ("payload", "event_type", "ts", "run_id", "decision_id", "seq")


def drop_triggers(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS events_no_update")
    conn.execute("DROP TRIGGER IF EXISTS events_no_delete")


def restore_triggers(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TRIGGER IF NOT EXISTS events_no_update
    BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'events are immutable: UPDATE rejected'); END;
    CREATE TRIGGER IF NOT EXISTS events_no_delete
    BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'events are immutable: DELETE rejected'); END;
    """)


def tamper_payload(raw: str) -> str:
    payload = json.loads(raw)
    if "filled_notional" in payload:
        payload["filled_notional"] = round(payload["filled_notional"] * 10, 2)
    elif "confidence" in payload:
        payload["confidence"] = 0.99
    else:
        payload["tampered_by"] = "tamper_demo"
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def tamper_value(field: str, raw: str) -> str:
    if field == "payload":
        return tamper_payload(raw)
    if field == "event_type":
        return raw.lower()  # retyping event_type must break verification
    if field == "seq":
        return raw  # handled numerically by caller
    return raw + "_tampered"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simulate a database-level tamper attack on the audit chain")
    parser.add_argument("--seq", type=int, default=None,
                        help="event seq to tamper (default: latest EXECUTION_RESULT)")
    parser.add_argument("--field", default="payload", choices=FIELDS,
                        help="protected field to modify (default: payload)")
    parser.add_argument("--db", default=str(
        Path(__file__).resolve().parent.parent / "data" / "glassbox.db"))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    seq = args.seq
    if seq is None:
        row = conn.execute(
            "SELECT seq FROM events WHERE event_type = 'EXECUTION_RESULT'"
            " ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            row = conn.execute(
                "SELECT seq FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            print("no events in chain — run tools/demo_setup.py first")
            return 1
        seq = row["seq"]

    row = conn.execute("SELECT * FROM events WHERE seq = ?", (seq,)).fetchone()
    if row is None:
        print(f"no event with seq {seq}")
        return 1

    drop_triggers(conn)
    if args.field == "seq":
        conn.execute("UPDATE events SET seq = ? WHERE record_hash = ?",
                     (seq + 1000, row["record_hash"]))
    else:
        conn.execute(
            f"UPDATE events SET {args.field} = ? WHERE record_hash = ?",
            (tamper_value(args.field, row[args.field]), row["record_hash"]))
    conn.commit()
    restore_triggers(conn)
    conn.commit()
    conn.close()

    print(f"✕ attacker edited {args.field} of event seq {seq} "
          "(immutability triggers bypassed at the file level, then restored)")
    print("→ now press VERIFY CHAIN in the console, or run:")
    print("  python tools/verify_chain.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
