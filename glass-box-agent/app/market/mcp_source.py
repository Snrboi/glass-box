"""OPTIONAL and NON-CRITICAL (PRD §3 repository layout, marked with *).

A live Binance market-data adapter via the AgentOS MCP path may be added
here on Day 2 PM *only if it is stable* (PRD §13 hard rule). It must be a
drop-in implementing ``app.market.base.MarketSource`` and must never gate
the core demo, which runs entirely on the deterministic simulator.

Intentionally shipped as a stub: the winning demo is simulator-first.
"""
from __future__ import annotations

from .base import MarketSource


class MCPMarketSource:
    """Placeholder for the optional live MCP market adapter."""

    name = "mcp"

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError(
            "MCP market source is an optional adapter and is not part of the "
            "core demo. Use app.market.simulator.SimulatorMarket instead."
        )


def _type_check(_: type[MarketSource] = MCPMarketSource) -> None:  # pragma: no cover
    pass
