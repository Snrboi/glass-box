"""JSONL mirror of the full event chain (PRD §4: export & external inspection).

One line per event, in chain order, containing the complete stored envelope
(payload as parsed JSON) plus the record_hash — the same shape the
verification routine consumes, so `tools/verify_chain.py` can run fully
offline against this file.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path


class JsonlMirror:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: dict) -> None:
        line = json.dumps(event, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_all(self) -> list[dict]:
        events: list[dict] = []
        if not self.path.exists():
            return events
        with self._lock, open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    e = json.loads(line)
                    # verify_chain expects payload as JSON text or dict; both ok.
                    e["payload"] = json.dumps(e["payload"], sort_keys=True,
                                              separators=(",", ":"),
                                              ensure_ascii=False)
                    events.append(e)
        return events
