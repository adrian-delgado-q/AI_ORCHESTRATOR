"""Six stubbed LangGraph agent nodes — Stage 1.

Each node receives SDLCState, updates it, and returns it.
Real LLM calls are added in Stage 4.
"""
from __future__ import annotations

import logging

from src.state.schema import SDLCRequirement, SDLCState, ToolEvidence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. TechLead — planning
# ---------------------------------------------------------------------------

def tech_lead_node(state: SDLCState) -> SDLCState:
    logger.info("[TechLead] Generating requirements and architecture...")
    state.current_phase = "planning"

    # Stage 1: stub requirements derived from success_criteria
    stub_requirements = [
        SDLCRequirement(
            id=f"REQ-{i+1:03d}",
            description=criterion,
            acceptance_criteria=[criterion],
        )
        for i, criterion in enumerate(state.success_criteria)
    ] or [
        SDLCRequirement(
            id="REQ-001",
            description=f"Implement: {state.objective}",
            acceptance_criteria=["System meets the stated objective"],
        )
    ]

    state.requirements = stub_requirements
    state.architecture_doc = (
        f"# Architecture\n\nObjective: {state.objective}\n\n"
        f"Requirements identified: {len(stub_requirements)}\n\n"
        "*(Stub — real architecture generated in Stage 4)*"
    )
    state.risk_level = "low"
    state.current_phase = "implementation"

    logger.info("[TechLead] Done. %d requirements created.", len(stub_requirements))
    return state


# ---------------------------------------------------------------------------
# 2. Dev — implementation
# ---------------------------------------------------------------------------

def dev_node(state: SDLCState) -> SDLCState:
    logger.info("[Dev] Writing implementation files...")
    state.current_phase = "implementation"

    # Stage 1: no real files written — Stage 2 adds write_file
    # files_changed is populated with stubs to exercise the schema
    from src.state.schema import FileChange

    state.files_changed = [
        FileChange(
            path=f"src/{req.id.lower().replace('-', '_')}_impl.py",
            requirement_id=req.id,
            rationale=f"Stub implementation for {req.id}: {req.description}",
            hash="",  # real hash added in Stage 2
        )
        for req in state.requirements
    ]

    state.current_phase = "testing"
    logger.info("[Dev] Done. %d files stubbed.", len(state.files_changed))
    return state


# ---------------------------------------------------------------------------
# 3. QA — test generation
# ---------------------------------------------------------------------------

def qa_node(state: SDLCState) -> SDLCState:
    logger.info("[QA] Writing test files...")
    state.current_phase = "testing"

    from src.state.schema import FileChange

    state.tests_written = [
        FileChange(
            path=f"tests/test_{req.id.lower().replace('-', '_')}.py",
            requirement_id=req.id,
            rationale=f"Stub tests for {req.id}: {req.description}",
            hash="",
        )
        for req in state.requirements
    ]

    state.current_phase = "review"
    logger.info("[QA] Done. %d test files stubbed.", len(state.tests_written))
    return state


# ---------------------------------------------------------------------------
# 4. Review — deterministic gates
# ---------------------------------------------------------------------------

def review_node(state: SDLCState) -> SDLCState:
    logger.info("[Review] Running quality gates...")
    state.current_phase = "review"

    # Stage 1: all gates pass (real tool calls added in Stage 3)
    state.gate_evidence = [
        ToolEvidence(tool_name="ruff", passed=True, findings="No issues (stub)"),
        ToolEvidence(tool_name="pytest", passed=True, findings="All tests passed (stub)"),
    ]

    all_passed = all(e.passed for e in state.gate_evidence)

    if all_passed:
        state.current_phase = "release"
        logger.info("[Review] All gates passed.")
    else:
        state.loop_count += 1
        state.current_phase = "implementation"
        logger.warning("[Review] Gates failed. Loop %d.", state.loop_count)

    return state


# ---------------------------------------------------------------------------
# 5. ReleaseEngineer — traceability + release notes
# ---------------------------------------------------------------------------

def release_engineer_node(state: SDLCState) -> SDLCState:
    logger.info("[ReleaseEngineer] Validating traceability and generating release notes...")
    state.current_phase = "release"

    # Check every requirement has at least one file and one test
    req_ids = {r.id for r in state.requirements}
    covered_by_code = {f.requirement_id for f in state.files_changed}
    covered_by_tests = {f.requirement_id for f in state.tests_written}
    missing_code = req_ids - covered_by_code
    missing_tests = req_ids - covered_by_tests

    if missing_code:
        logger.warning("[ReleaseEngineer] Missing code for: %s", missing_code)
    if missing_tests:
        logger.warning("[ReleaseEngineer] Missing tests for: %s", missing_tests)

    # Stage 1: generate stub release notes
    state.release_notes = (
        f"# Release Notes — {state.run_id}\n\n"
        f"**Objective:** {state.objective}\n\n"
        f"**Requirements:** {len(state.requirements)}\n"
        f"**Files changed:** {len(state.files_changed)}\n"
        f"**Tests written:** {len(state.tests_written)}\n"
        f"**Gate evidence:** {len(state.gate_evidence)}\n\n"
        "*(Stub — real release notes generated in Stage 4)*"
    )

    state.current_phase = "done"
    logger.info("[ReleaseEngineer] Done.")
    return state


# ---------------------------------------------------------------------------
# 6. Supervisor — routing + HITL escalation
# ---------------------------------------------------------------------------

def supervisor_node(state: SDLCState) -> SDLCState:
    logger.info("[Supervisor] Evaluating state. Phase=%s loop=%d", state.current_phase, state.loop_count)

    if state.loop_count >= 3:
        logger.warning("[Supervisor] Loop cap reached. Escalating to human_review.")
        state.current_phase = "human_review"
        return state

    if state.risk_level == "critical":
        logger.warning("[Supervisor] Critical risk. Escalating to human_review.")
        state.current_phase = "human_review"
        return state

    # current_phase == "done" — nothing to do
    logger.info("[Supervisor] Run complete.")
    return state
