"""Deterministic untrusted-input detector — independent of the LLM.

The model is advisory. Instruction-like text inside market data must be
flagged and escalated even if the model proposes BUY/SELL. The MockLLM
path already reacts; this module is the *control*, used by the cycle.
"""
from __future__ import annotations

INJECTION_MARKERS = (
    "ignore all previous",
    "ignore previous instructions",
    "system override",
)

INJECTION_FLAGS = [
    "prompt_injection_detected",
    "instruction_like_text_inside_market_data",
]


def looks_injected(note: str | None) -> bool:
    if not note:
        return False
    lower = note.lower()
    return any(marker in lower for marker in INJECTION_MARKERS)
