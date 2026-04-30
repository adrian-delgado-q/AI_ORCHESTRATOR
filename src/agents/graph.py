"""LangGraph state machine — Stage 1.

Topology:
    dispatcher → tech_lead → dev → qa → review → release_engineer → supervisor → END
                                ↑_______________________↓ (on gate failure, loop_count < 3)

    dispatcher: entry-point node that routes to the right phase on a
    fresh run (planning) or a resumed run (any other phase).
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

# Maps current_phase → the graph node that should run next.
_PHASE_TO_NODE: dict[str, str] = {
    "planning":       "tech_lead",
    "implementation": "dev",
    "testing":        "qa",
    "review":         "review",
    "release":        "release_engineer",
    "done":           "__end__",
    "human_review":   "__end__",
}


def _dispatch(state: SDLCState) -> str:
    """Return the node name to jump to based on current_phase."""
    phase = state.current_phase
    target = _PHASE_TO_NODE.get(phase, "tech_lead")
    if target == "__end__":
        logger.info("[Dispatcher] Phase=%s — nothing to do, ending.", phase)
    else:
        logger.info("[Dispatcher] Phase=%s → routing to '%s'.", phase, target)
    return target


def _route_after_review(state: SDLCState) -> str:
    """Route based on the phase review_node set.

    review_node is authoritative:
      - 'review'         → dep-fix short-circuit: re-run gates after reinstall
      - 'implementation' → code quality failure: send back to dev
      - 'release'        → all required gates green: proceed to release
    """
    if state.current_phase == "review":
        logger.info("[Router] Dep-fix loop — re-running review gates.")
        return "review"
    if state.current_phase == "implementation" and state.loop_count < 3:
        logger.info("[Router] Review failed (loop %d). Routing back to dev.", state.loop_count)
        return "dev"
    return "release_engineer"


def _route_after_supervisor(state: SDLCState) -> str:
    if state.current_phase == "human_review":
        return END  # type: ignore[return-value]
    return END  # type: ignore[return-value]


def build_graph() -> StateGraph:
    """Build and compile the Omega LangGraph state machine."""
    from src.core.timing import timed
    from src.state.persistence import save_state

    graph = StateGraph(dict)

    # Wrap each node: deserialise → run → checkpoint → serialise.
    # Checkpointing after every node allows --resume to pick up at any boundary.
    def _wrap(fn):
        def _node(state_dict: dict) -> dict:
            s = SDLCState.model_validate(state_dict)
            with timed(s.run_id, "node", fn.__name__, {"phase": s.current_phase}):
                updated = fn(s)
            save_state(updated)
            return updated.model_dump()
        _node.__name__ = fn.__name__
        return _node

    # Dispatcher is a passthrough; routing is in the conditional edge.
    graph.add_node("dispatcher", lambda d: d)
    graph.add_node("tech_lead", _wrap(tech_lead_node))
    graph.add_node("dev", _wrap(dev_node))
    graph.add_node("qa", _wrap(qa_node))
    graph.add_node("review", _wrap(review_node))
    graph.add_node("release_engineer", _wrap(release_engineer_node))
    graph.add_node("supervisor", _wrap(supervisor_node))

    # Entry point is always the dispatcher
    graph.set_entry_point("dispatcher")
    graph.add_conditional_edges(
        "dispatcher",
        lambda d: _dispatch(SDLCState.model_validate(d)),
        {
            "tech_lead":        "tech_lead",
            "dev":              "dev",
            "qa":               "qa",
            "review":           "review",
            "release_engineer": "release_engineer",
            "__end__":          END,
        },
    )

    # Fixed edges
    graph.add_edge("tech_lead", "dev")
    graph.add_edge("dev", "qa")
    graph.add_edge("qa", "review")

    # Conditional edge after review
    graph.add_conditional_edges(
        "review",
        lambda d: _route_after_review(SDLCState.model_validate(d)),
        {"review": "review", "dev": "dev", "release_engineer": "release_engineer"},
    )

    graph.add_edge("release_engineer", "supervisor")
    graph.add_edge("supervisor", END)

    return graph.compile()


# Module-level compiled graph (reused across runs)
omega_graph = build_graph()
