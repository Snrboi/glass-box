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

import hmac

from .chain import ZERO, compute_hash
from .sign import head_mac


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


def verify_head(events: list[dict], head: dict | None, key: bytes) -> dict:
    """Check the HMAC over the current chain head (seq + record_hash)."""
    if not events:
        return {"head_valid": True, "reason": None}
    last = events[-1]
    if not head or not head.get("mac") or head.get("seq") is None:
        return {"head_valid": False, "reason": "missing_head_mac"}
    try:
        head_seq = int(head["seq"])
    except (TypeError, ValueError):
        return {"head_valid": False, "reason": "head_mismatch"}
    if head_seq != last["seq"] or head.get("hash") != last["record_hash"]:
        return {"head_valid": False, "reason": "head_mismatch"}
    expected = head_mac(key, last["seq"], last["record_hash"])
    if not hmac.compare_digest(str(head["mac"]), expected):
        return {"head_valid": False, "reason": "head_mac_invalid"}
    return {"head_valid": True, "reason": None}


def verify_store(store) -> dict:
    """Hash-chain verification plus the HMAC over the current head."""
    events = store.all_events()
    result = verify_chain(events)
    head = store.head_state()
    signed = verify_head(events, head, store.chain_key())
    result["head_valid"] = signed["head_valid"]
    result["head_reason"] = signed["reason"]
    result["key_id"] = head.get("key_id")
    result["total"] = len(events)
    if result["valid"] and not signed["head_valid"]:
        result["valid"] = False
        last_seq = events[-1]["seq"] if events else None
        result["first_break_seq"] = last_seq
        result["first_break"] = {
            "seq": last_seq,
            "kind": "head_mac",
            "expected": "valid HMAC over seq:record_hash",
            "actual": signed["reason"] or "invalid",
        }
    return result
