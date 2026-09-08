#!/usr/bin/env python3
"""One command to verify the chain (PRD §4, §14).

    python tools/verify_chain.py                 # verify the SQLite chain
    python tools/verify_chain.py --jsonl         # verify the JSONL export instead

Recomputes every hash from the beginning of the chain and checks sequence
continuity and previous-hash continuity. Exits 0 when valid, 1 when broken.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit.chain import AuditStore            # noqa: E402
from app.audit.jsonl import JsonlMirror           # noqa: E402
from app.audit.verify import verify_chain         # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Glass Box audit chain")
    parser.add_argument("--jsonl", action="store_true",
                        help="verify data/decisions.jsonl instead of the SQLite chain")
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if args.jsonl:
        events = JsonlMirror(data_dir / "decisions.jsonl").read_all()
        source = str(data_dir / "decisions.jsonl")
        result = verify_chain(events)
    else:
        store = AuditStore(data_dir / "glassbox.db")  # opens read/write, never edits
        source = str(data_dir / "glassbox.db")
        result = verify_store(store)
        store.close()
    print(json.dumps(result, indent=2))
    if result["valid"]:
        print(f"✓ CHAIN VERIFIED — {result['checked']} events ({source})")
        return 0
    print(f"✕ CHAIN BROKEN — first break at seq {result['first_break_seq']} "
          f"({source})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
