"""The five deterministic policy rules (PRD §6).

Independent of the LLM. Evaluation order is FIXED and visible here, in the
code, and in the UI: P-05 -> P-01 -> P-02 -> P-03 -> P-04.

Each rule returns a verdict dict with the actual observed value and the
threshold — the UI shows both, not just PASS/BLOCK (PRD §9).
"""
from __future__ import annotations

PRECEDENCE = "HALT/ESCALATE > BLOCK > HOLD > EXECUTE"


def spread_bps(bid: float, ask: float) -> float:
    """Basis points, to avoid float-equality problems right at the 1.00%
    boundary (PRD §6). PASS if spread_bps <= 100."""
    mid = (bid + ask) / 2
    return ((ask - bid) / mid) * 10_000


def rule_p05_drawdown_breaker(ctx: dict, cfg: dict) -> dict:
    """PASS while day_realized_loss < limit; breach -> ESCALATE + HALT."""
    limit = float(cfg["day_realized_loss_limit_usdt"])
    pnl = float(ctx["day_realized_pnl_usdt"])
    loss = round(-pnl, 2) if pnl < 0 else 0.0
    ok = loss < limit
    return {
        "id": "P-05", "name": "DRAWDOWN_BREAKER",
        "verdict": "PASS" if ok else "HALT",
        "actual_value": loss, "actual": f"${loss:.2f} day loss",
        "threshold": f"< ${limit:.2f}", "passed": ok,
        "reason": None if ok else "HALT_DRAWDOWN_BREAKER",
    }


def rule_p01_conf_min(ctx: dict, cfg: dict) -> dict:
    """PASS if confidence >= min_confidence; breach -> BLOCK."""
    floor = float(cfg["min_confidence"])
    conf = float(ctx["confidence"])
    ok = conf >= floor
    return {
        "id": "P-01", "name": "CONF_MIN",
        "verdict": "PASS" if ok else "BLOCK",
        "actual_value": conf, "actual": f"{conf:.2f}",
        "threshold": f">= {floor:.2f}", "passed": ok,
        "reason": None if ok else "BLOCK_CONF_LOW",
    }


def rule_p02_notional_cap(ctx: dict, cfg: dict) -> dict:
    """PASS if quote_notional_usdt <= max_notional; breach -> BLOCK."""
    cap = float(cfg["max_notional_usdt"])
    notional = float(ctx["quote_notional_usdt"])
    ok = notional <= cap
    return {
        "id": "P-02", "name": "NOTIONAL_CAP",
        "verdict": "PASS" if ok else "BLOCK",
        "actual_value": notional, "actual": f"${notional:.2f}",
        "threshold": f"<= ${cap:.2f}", "passed": ok,
        "reason": None if ok else "BLOCK_NOTIONAL_CAP",
    }


def rule_p03_exposure_cap(ctx: dict, cfg: dict) -> dict:
    """PASS if projected post-trade exposure <= cap; breach -> BLOCK."""
    cap = float(cfg["max_projected_exposure_usdt"])
    projected = float(ctx["projected_exposure_usdt"])
    ok = projected <= cap
    return {
        "id": "P-03", "name": "EXPOSURE_CAP",
        "verdict": "PASS" if ok else "BLOCK",
        "actual_value": round(projected, 2),
        "actual": f"${projected:.2f} projected",
        "threshold": f"<= ${cap:.2f}", "passed": ok,
        "reason": None if ok else "BLOCK_EXPOSURE_CAP",
    }


def rule_p04_spread_sanity(ctx: dict, cfg: dict) -> dict:
    """PASS if spread_bps <= max_spread_bps; breach -> ESCALATE."""
    max_bps = float(cfg["max_spread_bps"])
    bps = round(spread_bps(ctx["bid"], ctx["ask"]), 4)
    ok = bps <= max_bps
    return {
        "id": "P-04", "name": "SPREAD_SANITY",
        "verdict": "PASS" if ok else "ESCALATE",
        "actual_value": bps, "actual": f"{bps:.2f} bps",
        "threshold": f"<= {max_bps:.0f} bps", "passed": ok,
        "reason": None if ok else "ESCALATE_SPREAD_SANITY",
    }


# Fixed evaluation order — must match the UI rendering order exactly.
RULES = {
    "P-05": rule_p05_drawdown_breaker,
    "P-01": rule_p01_conf_min,
    "P-02": rule_p02_notional_cap,
    "P-03": rule_p03_exposure_cap,
    "P-04": rule_p04_spread_sanity,
}
EVALUATION_ORDER = ["P-05", "P-01", "P-02", "P-03", "P-04"]
BLOCK_ORDER = ["P-01", "P-02", "P-03"]
