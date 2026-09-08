"""Deterministic risk governor tests (PRD §6, §12).

All 5 pinned golden cases in fixtures/policy-cases.json must produce their
pinned outcome; the confidence boundary 0.55 passes; spread is computed in
basis points to avoid float-equality problems at the 1.00% boundary.
"""
import pytest

from pathlib import Path

from app.policy.engine import Governor, load_policy
from app.policy.rules import rule_p04_spread_sanity, spread_bps

ROOT = Path(__file__).resolve().parent.parent
GOVERNOR = Governor(load_policy(str(ROOT / "policy.yaml")))


@pytest.mark.parametrize("case", range(6),
                         ids=lambda i: f"golden_{i + 1}")
def test_policy_golden_cases(policy_cases, case):
    pinned = policy_cases[case]
    inp = pinned["input"]
    proposal = {
        "action": inp["action"],
        "symbol": inp["symbol"],
        "quote_notional_usdt": inp["quote_notional_usdt"],
        "confidence": inp["confidence"],
    }
    verdicts = GOVERNOR.evaluate(proposal, inp["market"], inp["portfolio"])
    assert len(verdicts) == 5  # all five rules always evaluate
    outcome = GOVERNOR.decide(proposal, verdicts)
    expected = pinned["expected"]
    assert outcome["outcome"] == expected["outcome"], pinned["name"]
    assert outcome["reason"] == expected["reason"], pinned["name"]
    assert outcome["winning_rules"] == expected["winning_rules"], pinned["name"]


def test_confidence_boundary_exactly_passes(policy_cases):
    """confidence exactly 0.55 -> P-01 PASSES (>=), so the trade executes."""
    boundary = policy_cases[2]
    assert boundary["input"]["confidence"] == 0.55
    assert boundary["expected"]["outcome"] == "EXECUTE"


def test_spread_bps_boundary_math():
    # exactly 100 bps -> PASS; anything above -> ESCALATE
    assert spread_bps(99.5, 100.5) == pytest.approx(100.0)
    v = rule_p04_spread_sanity({"bid": 99.5, "ask": 100.5},
                               {"max_spread_bps": 100})
    assert v["verdict"] == "PASS"
    v = rule_p04_spread_sanity({"bid": 99.4, "ask": 100.6},
                               {"max_spread_bps": 100})
    assert v["verdict"] == "ESCALATE"


def test_precedence_escalate_beats_block():
    proposal = {"action": "BUY", "symbol": "BTCUSDT",
                "quote_notional_usdt": 75.0, "confidence": 0.42}
    market = {"bid": 3400.0, "ask": 3468.0}  # wide spread -> P-04 ESCALATE
    portfolio = {"cash_usdt": 1000.0, "positions": {"BTC": 0.0, "ETH": 0.0},
                 "day_realized_pnl_usdt": 0.0,
                 "marks": {"BTCUSDT": 100.0, "ETHUSDT": 100.0}}
    verdicts = GOVERNOR.evaluate(proposal, market, portfolio)
    verdict_map = {v["id"]: v["verdict"] for v in verdicts}
    assert verdict_map["P-01"] == "BLOCK"   # low confidence
    assert verdict_map["P-02"] == "BLOCK"   # notional cap
    assert verdict_map["P-04"] == "ESCALATE"
    outcome = GOVERNOR.decide(proposal, verdicts)
    # HALT/ESCALATE wins over BLOCK per precedence.
    assert outcome["outcome"] == "ESCALATE"
    assert outcome["winning_rules"] == ["P-04"]


def test_precedence_halt_beats_everything():
    proposal = {"action": "BUY", "symbol": "BTCUSDT",
                "quote_notional_usdt": 75.0, "confidence": 0.42}
    market = {"bid": 99.5, "ask": 100.5}
    portfolio = {"cash_usdt": 1000.0, "positions": {"BTC": 0.0, "ETH": 0.0},
                 "day_realized_pnl_usdt": -30.0,
                 "marks": {"BTCUSDT": 100.0, "ETHUSDT": 100.0}}
    verdicts = GOVERNOR.evaluate(proposal, market, portfolio)
    outcome = GOVERNOR.decide(proposal, verdicts)
    assert outcome["outcome"] == "HALT"
    assert outcome["winning_rules"] == ["P-05"]
