"""Deterministic policy engine (PRD §6).

Same inputs -> same verdicts and outcome. All five rules ALWAYS evaluate
(so the UI can show the complete verdict table); the final outcome is then
resolved by fixed precedence:

    HALT/ESCALATE wins -> first BLOCK in fixed order -> HOLD -> EXECUTE
"""
from __future__ import annotations

import yaml

from .rules import EVALUATION_ORDER, PRECEDENCE, RULES


def load_policy(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class Governor:
    def __init__(self, policy: dict):
        self.policy = policy
        self._cfg = {r["id"]: r for r in policy["rules"]}

    # ------------------------------------------------------------------
    def _context(self, proposal: dict, market: dict, portfolio: dict) -> dict:
        bid = float(market["bid"])
        ask = float(market["ask"])
        marks = portfolio["marks"]
        positions = portfolio["positions"]
        exposure = sum(
            float(qty) * float(marks.get(f"{asset}USDT", 0.0))
            for asset, qty in positions.items()
        )
        notional = float(proposal["quote_notional_usdt"])
        if proposal["action"] == "BUY":
            projected = exposure + notional
        elif proposal["action"] == "SELL":
            projected = max(0.0, exposure - notional)
        else:
            projected = exposure
        return {
            "confidence": float(proposal["confidence"]),
            "quote_notional_usdt": notional,
            "day_realized_pnl_usdt": float(portfolio["day_realized_pnl_usdt"]),
            "projected_exposure_usdt": projected,
            "bid": bid, "ask": ask,
        }

    # ------------------------------------------------------------------
    def evaluate(self, proposal: dict, market: dict, portfolio: dict) -> dict:
        """Run all five rules in fixed order. Returns verdicts only —
        use ``decide`` for the final outcome."""
        ctx = self._context(proposal, market, portfolio)
        verdicts = [RULES[rid](ctx, self._cfg[rid]) for rid in EVALUATION_ORDER]
        return verdicts

    # ------------------------------------------------------------------
    def decide(self, proposal: dict, verdicts: list[dict],
               llm_malformed: bool = False) -> dict:
        """Apply outcome precedence to a proposal + its five verdicts."""
        by_id = {v["id"]: v for v in verdicts}
        halts = [v for v in verdicts if v["verdict"] == "HALT"]
        rule_escalates = [v for v in verdicts if v["verdict"] == "ESCALATE"]
        blocks = [v for v in verdicts if v["verdict"] == "BLOCK"]
        model_escalated = proposal["action"] == "ESCALATE" or llm_malformed

        # 1. HALT wins above everything (P-05 emits ESCALATE + HALT).
        if halts:
            winner = halts[0]
            return {"outcome": "HALT", "reason": winner["reason"],
                    "winning_rules": [winner["id"]], "precedence": PRECEDENCE}

        # 2. ESCALATE tier — model-side signals first, then the rule breach.
        if llm_malformed:
            return {"outcome": "ESCALATE", "reason": "ESCALATE_LLM_UNRELIABLE",
                    "winning_rules": [v["id"] for v in rule_escalates] or ["MODEL"],
                    "precedence": PRECEDENCE}
        if model_escalated:
            return {"outcome": "ESCALATE", "reason": "ESCALATE_MODEL_FLAGGED",
                    "winning_rules": [v["id"] for v in rule_escalates] or ["MODEL"],
                    "precedence": PRECEDENCE}
        if rule_escalates:
            winner = rule_escalates[0]
            return {"outcome": "ESCALATE", "reason": winner["reason"],
                    "winning_rules": [winner["id"]], "precedence": PRECEDENCE}

        # 3. First BLOCK in fixed order.
        if blocks:
            winner = blocks[0]
            return {"outcome": "BLOCK", "reason": winner["reason"],
                    "winning_rules": [winner["id"]], "precedence": PRECEDENCE}

        # 4. HOLD, then 5. EXECUTE.
        if proposal["action"] in ("HOLD",):
            return {"outcome": "HOLD", "reason": "MODEL_HOLD",
                    "winning_rules": [], "precedence": PRECEDENCE}
        return {"outcome": "EXECUTE", "reason": "ALL_RULES_PASS",
                "winning_rules": [], "precedence": PRECEDENCE}
