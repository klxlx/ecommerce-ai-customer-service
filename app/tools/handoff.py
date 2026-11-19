"""Handoff payload construction."""

from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


def build_handoff_payload(state: AgentState, reason: str) -> dict[str, Any]:
    """Build the persisted handoff ticket payload from graph state."""
    return {
        "reason": reason,
        "summary": state.get("user_message", ""),
        "slots": state.get("slots", {}),
        "evidence": {
            "sources": state.get("sources", []),
            "tool": state.get("tool_evidence", {}),
        },
    }
