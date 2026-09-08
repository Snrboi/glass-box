#!/usr/bin/env python3
"""Scripted demo setup (PRD §13 Day 3 AM + Appendix B).

Resets the database, then runs the deterministic scenario rotation so the
console opens with a seeded decision trace containing at least one EXECUTE,
one BLOCK, and ESCALATE cases — entirely via simulator + MockLLM.

    python tools/demo_setup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.cycle import AgentHalted, AgentPaused, TickConflict   # noqa: E402
from app.audit.verify import verify_chain                            # noqa: E402
from app.main import Services                                        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

ROTATION_TICKS = 5  # one full scenario rotation:
                    # EXECUTE -> BLOCK -> ESCALATE(injection)
                    # -> ESCALATE(wide spread) -> HOLD


def reset_data() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("glassbox.db", "glassbox.db-wal", "glassbox.db-shm",
                 "decisions.jsonl", "chain.key"):
        path = DATA_DIR / name
        if path.exists():
            path.unlink()
    print(f"• reset {DATA_DIR}")


def main() -> int:
    reset_data()
    services = Services(data_dir=DATA_DIR, phase_delay_ms=0)
    print(f"• run_id {services.run_id} (simulator + {services.llm.name})")

    for i in range(ROTATION_TICKS):
        try:
            result = services.cycle.run()
        except (TickConflict, AgentHalted, AgentPaused) as exc:
            print(f"  tick {i + 1}: rejected: {exc}")
            continue
        print(f"  tick {i + 1}: {result['decision_id']} "
              f"[{result['scenario']}] -> {result['outcome']} "
              f"({result['reason']})")

    events = services.store.all_events()
    result = verify_chain(events)
    print(f"• chain: {result['checked']} events, valid={result['valid']}")

    summary = {
        "cycles": services.store.count_of_type("CYCLE_STARTED"),
        "decisions": services.store.count_of_type("DECISION"),
        "chain_valid": result["valid"],
        "events": services.store.count(),
    }
    print(f"• ready: {summary}")
    print("→ start the console:  python -m uvicorn app.main:app "
          "--host 0.0.0.0 --port 8000")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
