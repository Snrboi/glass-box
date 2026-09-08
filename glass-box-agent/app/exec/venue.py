"""Dry-run simulated venue (PRD §1, §5).

Simulates fills and portfolio effects WITHOUT placing real orders. No live
execution path exists anywhere in this codebase — dry-run only.

Portfolio integrity (PRD §5): the mutable portfolio_state table is only a
materialized current-state cache. The audit-critical post-execution
`portfolio_after` is embedded inside the EXECUTION_RESULT event itself, so
the evidence needed to explain a fill is itself chained. On every cycle the
cache is rebuilt from the last EXECUTION_RESULT so a tampered cache cannot
feed the next governor decision.
"""
from __future__ import annotations

import json

BASE_ASSETS = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}

EVENT_EXECUTION_RESULT = "EXECUTION_RESULT"


def _empty_positions() -> dict:
    return {"BTC": {"qty": 0.0, "avg_cost": 0.0},
            "ETH": {"qty": 0.0, "avg_cost": 0.0}}


class SimulatedVenue:
    name = "simulated"

    def __init__(self, store, fee_bps: float = 10.0):
        self._store = store
        self.fee_rate = fee_bps / 10_000

    # --------------------------------------------------------------
    def init_portfolio(self, initial_cash_usdt: float) -> dict:
        state = self._store.portfolio_get()
        if state is None:
            state = {
                "cash_usdt": float(initial_cash_usdt),
                "positions": _empty_positions(),
                "day_realized_pnl_usdt": 0.0,
            }
            self._store.portfolio_set(state)
        return state

    def current(self) -> dict:
        return self._store.portfolio_get()

    def rebuild_from_chain(self, initial_cash_usdt: float) -> dict:
        """Materialize portfolio from the last chained fill.

        If the chain has no EXECUTION_RESULT yet, keep the cache (or seed
        it from initial cash). Tampering the cache after a fill is healed
        on the next cycle.
        """
        events = self._store.events_of_type(EVENT_EXECUTION_RESULT)
        if not events:
            state = self._store.portfolio_get()
            if state is None:
                return self.init_portfolio(initial_cash_usdt)
            return state
        payload = events[-1]["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        after = payload.get("portfolio_after") or {}
        if "positions" in after and isinstance(after["positions"], dict):
            positions = {}
            for asset in ("BTC", "ETH"):
                src = after["positions"].get(asset) or {}
                positions[asset] = {
                    "qty": float(src.get("qty", 0.0) or 0.0),
                    "avg_cost": float(src.get("avg_cost", 0.0) or 0.0),
                }
        else:
            positions = {
                "BTC": {"qty": float(after.get("BTC", 0.0) or 0.0),
                        "avg_cost": 0.0},
                "ETH": {"qty": float(after.get("ETH", 0.0) or 0.0),
                        "avg_cost": 0.0},
            }
        state = {
            "cash_usdt": float(after.get("cash_usdt", initial_cash_usdt)),
            "positions": positions,
            "day_realized_pnl_usdt": float(
                after.get("day_realized_pnl_usdt", 0.0) or 0.0),
        }
        self._store.portfolio_set(state)
        return state

    def preflight(self, proposal: dict) -> tuple[bool, str | None]:
        """Refuse fills the five policy rules do not cover: no cash, no inventory."""
        action = proposal.get("action")
        if action not in ("BUY", "SELL"):
            return True, None
        state = self.current()
        if state is None:
            return False, "BLOCK_NO_PORTFOLIO"
        notional = round(float(proposal["quote_notional_usdt"]), 2)
        if action == "BUY":
            fee_usdt = round(notional * self.fee_rate, 8)
            cost = notional + fee_usdt
            if cost > float(state["cash_usdt"]) + 1e-9:
                return False, "BLOCK_INSUFFICIENT_CASH"
            return True, None
        asset = BASE_ASSETS[proposal["symbol"]]
        held = float(state["positions"][asset]["qty"])
        if held <= 0:
            return False, "BLOCK_NO_POSITION"
        return True, None

    # --------------------------------------------------------------
    def fill(self, proposal: dict, book: dict) -> dict:
        """Simulate a fill: BUY lifts the ask, SELL hits the bid.

        Returns the EXECUTION_RESULT evidence: fill, fee, slippage and the
        post-trade portfolio snapshot (portfolio_after).
        """
        state = self.current()
        side = proposal["action"]
        symbol = proposal["symbol"]
        asset = BASE_ASSETS[symbol]
        notional = round(float(proposal["quote_notional_usdt"]), 2)

        bid, ask, mid = float(book["bid"]), float(book["ask"]), float(book["mid"])
        fill_price = ask if side == "BUY" else bid
        slippage_bps = round(((fill_price - mid) / mid) * 10_000, 4)
        if side == "SELL":
            qty = round(notional / fill_price, 8)
            held = float(state["positions"][asset]["qty"])
            if qty > held:  # never sell more than the simulated book holds
                qty = held
                notional = round(qty * fill_price, 2)
        else:
            qty = round(notional / fill_price, 8)

        fee_usdt = round(notional * self.fee_rate, 8)
        pos = dict(state["positions"][asset])

        if side == "BUY":
            cost = notional + fee_usdt
            new_qty = round(pos["qty"] + qty, 8)
            pos["avg_cost"] = round(
                (pos["qty"] * pos["avg_cost"] + cost) / new_qty, 8) if new_qty else 0.0
            pos["qty"] = new_qty
            cash_after = round(state["cash_usdt"] - cost, 8)
            realized_delta = 0.0
        else:  # SELL
            proceeds = round(notional - fee_usdt, 8)
            realized_delta = round((fill_price - pos["avg_cost"]) * qty - fee_usdt, 8)
            pos["qty"] = round(pos["qty"] - qty, 8)
            if pos["qty"] <= 0:
                pos["qty"], pos["avg_cost"] = 0.0, 0.0
            cash_after = round(state["cash_usdt"] + proceeds, 8)

        positions = dict(state["positions"])
        positions[asset] = pos
        day_pnl_after = round(state["day_realized_pnl_usdt"] + realized_delta, 8)

        new_state = {"cash_usdt": cash_after, "positions": positions,
                     "day_realized_pnl_usdt": day_pnl_after}
        self._store.portfolio_set(new_state)

        return {
            "status": "FILLED",
            "side": side,
            "symbol": symbol,
            "fill_price": fill_price,
            "filled_qty": qty,
            "filled_notional": notional,
            "fee_usdt": fee_usdt,
            "slippage_bps": slippage_bps,
            "realized_pnl_delta_usdt": realized_delta,
            "portfolio_after": {
                "cash_usdt": cash_after,
                "BTC": positions["BTC"]["qty"],
                "ETH": positions["ETH"]["qty"],
                "positions": {
                    "BTC": dict(positions["BTC"]),
                    "ETH": dict(positions["ETH"]),
                },
                "day_realized_pnl_usdt": day_pnl_after,
            },
        }

    # --------------------------------------------------------------
    def marks_from_snapshot(self, snapshot: dict) -> dict:
        """Mid marks per symbol, used for exposure math (P-03)."""
        return {sym: book["mid"] for sym, book in snapshot["symbols"].items()}

    def governor_portfolio_view(self, snapshot: dict) -> dict:
        """Flattened portfolio view consumed by the governor."""
        state = self.current()
        return {
            "cash_usdt": state["cash_usdt"],
            "positions": {a: p["qty"] for a, p in state["positions"].items()},
            "day_realized_pnl_usdt": state["day_realized_pnl_usdt"],
            "marks": self.marks_from_snapshot(snapshot),
        }
