"""Deterministic MockLLM (PRD §7, §12).

Supports the pinned canned responses in fixtures/llm-responses.json —
clean BUY (0.62), low-confidence BUY (0.42), HOLD, and malformed JSON —
plus the deterministic prompt-injection reaction required by the system
prompt contract: injection-like market data -> ESCALATE with flags naming
what looked wrong.

Core demo reliability (PRD §2 success gates) depends ONLY on this mock and
the simulator. No external API is ever on the demo path.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..policy.injection import INJECTION_FLAGS, looks_injected

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "llm-responses.json"


class MockLLM:
    name = "mock"

    def __init__(self, fixture_path: str | Path = FIXTURE_PATH):
        with open(fixture_path, "r", encoding="utf-8") as fh:
            responses = json.load(fh)
        self._by_name = {r["name"]: r["raw"] for r in responses}

    # --------------------------------------------------------------
    def propose(self, snapshot: dict, focus_symbol: str) -> str:
        """Return the RAW model output (a JSON string), selected
        deterministically from the scenario in the snapshot."""
        scenario = snapshot.get("scenario", "clean_market")

        # The system prompt contract: manipulated/wrong-looking data ->
        # ESCALATE with flags. Detected deterministically for the mock.
        note = snapshot.get("note", "")
        if scenario == "prompt_injection" or looks_injected(note):
            return json.dumps({
                "action": "ESCALATE",
                "symbol": focus_symbol,
                "quote_notional_usdt": 0.00,
                "confidence": 0.80,
                "thesis_plain_english": (
                    "Market data note contains instruction-like text "
                    "attempting to direct trades. Treating it as untrusted "
                    "data and escalating instead of proposing an order."
                ),
                "factors": [{"name": "data_integrity", "value": "suspect"},
                            {"name": "note_pattern", "value": "imperative override"}],
                "flags": list(INJECTION_FLAGS),
            })

        if scenario == "low_confidence":
            return self._by_name["low_confidence_buy"]
        if scenario == "hold_market":
            return self._by_name["hold"]
        if scenario == "malformed_fixture_demo":
            return self._by_name["malformed_missing_confidence"]

        # clean_market / wide_spread: the pinned clean BUY, aimed at the
        # scenario's focus symbol (ETH for the wide-spread beat).
        proposal = json.loads(self._by_name["clean_buy"])
        proposal["symbol"] = focus_symbol
        return json.dumps(proposal)

    # --------------------------------------------------------------
    def malformed_response(self) -> str:
        """Direct access to the pinned malformed fixture (tests)."""
        return self._by_name["malformed_missing_confidence"]
