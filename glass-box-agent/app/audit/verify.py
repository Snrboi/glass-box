"""Independent chain verification (PRD §4 + Appendix A).

Recomputes every hash from the beginning of the chain and checks:
  1. sequence continuity          (seq modified or removed -> fail)
  2. previous-hash continuity     (prev_hash modified -> fail)
  3. record hash over the complete envelope
       (payload / event_type / ts / run_id / decision_id tampered -> fail)

Response shape: {"valid": false, "checked": 183, "first_break_seq": 183}
plus a `first_break` diagnostic {seq, kind, expected, actual} so the UI can
show Expected vs Actual hash fragments (PRD §9).
"""
from __future__ import annotations

import json

from .chain import ZERO, compute_hash


def verify_chain(events: list[dict]) -> dict:
    """Events are dicts straight from the store: payload is JSON *text*."""
    prev = ZERO
    checked = 0
    for i, event in enumerate(events, start=1):
        checked = i
        if event["seq"] != i:
            return {
                "valid": False, "checked": checked, "first_break_seq": event["seq"],
                "first_break": {"seq": event["seq"], "kind": "sequence",
                                "expected": str(i), "actual": str(event["seq"])},
            }
        if event["prev_hash"] != prev:
            return {
                "valid": False, "checked": checked, "first_break_seq": event["seq"],
                "first_break": {"seq": event["seq"], "kind": "prev_hash",
                                "expected": prev, "actual": event["prev_hash"]},
            }
        payload = event["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        record = {
            "seq": event["seq"],
            "ts": event["ts"],
            "run_id": event["run_id"],
            "decision_id": event["decision_id"],
            "event_type": event["event_type"],
            "payload": payload,
            "prev_hash": event["prev_hash"],
        }
        computed = compute_hash(record)
        if computed != event["record_hash"]:
            return {
                "valid": False, "checked": checked, "first_break_seq": event["seq"],
                "first_break": {"seq": event["seq"], "kind": "record_hash",
                                "expected": computed, "actual": event["record_hash"]},
            }
        prev = event["record_hash"]
    return {"valid": True, "checked": checked, "first_break_seq": None,
            "first_break": None}
