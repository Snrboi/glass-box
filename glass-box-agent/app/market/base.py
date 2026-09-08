"""Market data source interface.

A snapshot records WHAT THE AGENT SAW: timestamp, prices, bid/ask, spread
and source (PRD §5, DATA_SNAPSHOT). The `note` string is deliberately
UNTRUSTED free text — it is the prompt-injection attack surface and must be
treated as data, never as instructions (PRD §7, §11).
"""
from __future__ import annotations

from typing import Protocol


def compute_spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2
    return round(((ask - bid) / mid) * 10_000, 4)


class MarketSource(Protocol):
    name: str

    def snapshot(self, symbols: list[str]) -> dict:
        """Return {ts, source, scenario, note, symbols: {SYM: {bid, ask,
        mid, spread_bps}}}."""
        ...


def build_snapshot(ts: str, source: str, scenario: str, note: str,
                   books: dict[str, tuple[float, float]]) -> dict:
    symbols = {}
    for sym, (bid, ask) in books.items():
        mid = round((bid + ask) / 2, 8)
        symbols[sym] = {
            "bid": bid, "ask": ask, "mid": mid,
            "spread_bps": compute_spread_bps(bid, ask),
        }
    return {"ts": ts, "source": source, "scenario": scenario,
            "note": note, "symbols": symbols}
