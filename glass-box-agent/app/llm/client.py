"""Real Anthropic Claude adapter (PRD §7) — OPTIONAL.

"Real Anthropic integration is optional and must never become a demo
dependency." It is selected only when explicitly configured:

    GLASSBOX_LLM=claude  ANTHROPIC_API_KEY=sk-...

If the SDK or key is missing, or the call fails, the cycle escalates
LLM_UNRELIABLE — it never falls through to placing an order.
"""
from __future__ import annotations

from .prompts import (DECISION_PROPOSAL_SCHEMA, SYSTEM_PROMPT,
                      build_user_prompt)

DEFAULT_MODEL = "claude-sonnet-4-5"


class LLMUnavailable(RuntimeError):
    pass


class ClaudeLLM:
    name = "claude"

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        if not api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMUnavailable(
                "anthropic SDK not installed (pip install anthropic)") from exc
        self._api_key = api_key
        self.model = model

    def propose(self, snapshot: dict, focus_symbol: str) -> str:
        """Call Claude with tool-use so the structured DecisionProposal is
        enforced by the API, and return the tool input as raw JSON text."""
        try:
            import anthropic
            import json as _json

            client = anthropic.Anthropic(api_key=self._api_key)
            tool = {
                "name": "propose_decision",
                "description": "Propose exactly ONE trading action as a "
                               "DecisionProposal for the deterministic "
                               "risk governor to review.",
                "input_schema": DECISION_PROPOSAL_SCHEMA,
            }
            resp = client.messages.create(
                model=self.model,
                max_tokens=800,
                system=SYSTEM_PROMPT,
                tools=[tool],
                tool_choice={"type": "tool", "name": "propose_decision"},
                messages=[{
                    "role": "user",
                    "content": build_user_prompt(snapshot, focus_symbol),
                }],
            )
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" \
                        and block.name == "propose_decision":
                    return _json.dumps(block.input)
            raise LLMUnavailable("Claude returned no propose_decision tool call")
        except LLMUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - network dependent
            raise LLMUnavailable(f"Claude adapter failed: {exc}") from exc
