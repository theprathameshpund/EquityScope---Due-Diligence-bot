"""Market agent: yfinance snapshot + peer multiples + optional FRED macro.

Pure tool calls except a one-paragraph neutral summary (fast model) and
peer ticker suggestion. Any failure degrades to an unavailable snapshot —
it never fails the run.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from src.llm.router import LLMRouter, load_prompt
from src.logging_setup import get_logger
from src.state import AgentState, MarketSnapshot
from src.tools.market_data import get_macro_notes, get_peer_multiples, get_snapshot

log = get_logger(__name__)


class _Peers(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=5)


def _suggest_peers(router: LLMRouter, state: AgentState) -> list[str]:
    system = load_prompt("peer_suggest").format(
        company=state.company_name, ticker=state.ticker
    )
    try:
        result = router.complete_json("fast", system, "List the 3 peer tickers.", _Peers)
    except ValueError:
        return []
    cleaned = [t.strip().upper() for t in result.tickers if t.strip()]
    return [t for t in cleaned if t != state.ticker.upper()][:3]


def market_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: build the market snapshot; degrade gracefully."""
    router = LLMRouter(state.run_id)
    try:
        snapshot = get_snapshot(state.ticker)
        snapshot.peers = get_peer_multiples(_suggest_peers(router, state))
        snapshot.macro_notes = get_macro_notes()

        try:
            data = snapshot.model_dump(mode="json", exclude={"summary", "available", "error"})
            response = router.complete(
                "fast",
                load_prompt("market_summary"),
                json.dumps(data, default=str),
                max_tokens=200,
            )
            snapshot.summary = response.text.strip()
        except Exception as exc:
            log.warning("market_summary_failed", error=str(exc))

        return {"market": snapshot}
    except Exception as exc:
        log.warning("market_data_unavailable", error=str(exc))
        return {
            "market": MarketSnapshot(available=False, error=str(exc), ticker=state.ticker),
            "data_gaps": [f"Market data unavailable: {exc}"],
        }
