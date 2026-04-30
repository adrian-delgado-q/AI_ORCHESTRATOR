"""LangGraph state machine — Stage 1.

Topology:
    tech_lead → dev → qa → review → release_engineer → supervisor → END
                  ↑_______________________↓ (on gate failure, loop_count < 3)
"""
from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from src.agents.nodes import (
    dev_node,
    qa_node,
    release_engineer_node,
    review_node,
    supervisor_node,
    tech_lead_node,
)
from src.state.schema import SDLCState

logger = logging.getLogger(__name__)


def _route_after_review(state: SDLCState) -> str:
    """Route back to dev on gate failure (up to 3 loops), else forward."""
    failed = any(not e.passed for e in state.gate_evidence)
    if failed and state.loop_count < 3:
        logger.info("[Router] Review failed (loop %d). Routing back to dev.", state.loop_count)
        return "dev"
    return "release_engineer"


def _route_after_supervisor(state: SDLCState) -> str:
    if state.current_phase == "human_review":
        return END  # type: ignore[return-value]
    return END  # type: ignore[return-value]


def build_graph() -> StateGraph:
    """Build and compile the Omega LangGraph state machine."""
    # LangGraph requires a dict-based state or a TypedDict.
    # We use a thin dict wrapper and convert to/from SDLCState.
    graph = StateGraph(dict)

    # Wrap each node so it works with the dict state LangGraph expects
    def _wrap(fn):
        def _node(state_dict: dict) -> dict:
            s = SDLCState.model_validate(state_dict)
            updated = fn(s)
            return updated.model_dump()
        _node.__name__ = fn.__name__
        return _node

    graph.add_node("tech_lead", _wrap(tech_lead_node))
    graph.add_node("dev", _wrap(dev_node))
    graph.add_node("qa", _wrap(qa_node))
    graph.add_node("review", _wrap(review_node))
    graph.add_node("release_engineer", _wrap(release_engineer_node))
    graph.add_node("supervisor", _wrap(supervisor_node))

    # Fixed edges
    graph.set_entry_point("tech_lead")
    graph.add_edge("tech_lead", "dev")
    graph.add_edge("dev", "qa")
    graph.add_edge("qa", "review")

    # Conditional edge after review
    graph.add_conditional_edges(
        "review",
        lambda d: _route_after_review(SDLCState.model_validate(d)),
        {"dev": "dev", "release_engineer": "release_engineer"},
    )

    graph.add_edge("release_engineer", "supervisor")
    graph.add_edge("supervisor", END)

    return graph.compile()


# Module-level compiled graph (reused across runs)
omega_graph = build_graph()
