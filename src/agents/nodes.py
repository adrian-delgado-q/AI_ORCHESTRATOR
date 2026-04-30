"""LangGraph agent nodes — Stage 4.

Each node receives SDLCState, updates it, and returns it.
Stage 2: dev_node and qa_node write real files via src.io.workspace.
Stage 3: review_node calls real subprocess tools; release_engineer_node
         blocks completion when required gates fail.
Stage 4: All nodes use real LLM calls via src.core.llm.load_llm().
         review_node calls DiagnosticUtility on failures to populate
         ToolEvidence.diagnosis for use in the next dev loop.
"""
from __future__ import annotations

import json
import logging
import re

from src.agents.diagnostic import DiagnosticUtility
from src.core.llm import BaseLLM, load_llm
from src.io.workspace import write_file
from src.state.schema import SDLCRequirement, SDLCState, ToolEvidence
from src.tools.runners import (
    run_bandit,
    run_complexity_check,
    run_mypy,
    run_pip_audit,
    run_pytest,
    run_ruff,
)

logger = logging.getLogger(__name__)

# Required gates — failure triggers a dev loop.
_REQUIRED_TOOLS = {"ruff", "pytest"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> str:
    """Return the first complete JSON value (object or array) found in *text*.

    Handles three cases:
    1. Markdown-fenced blocks (triple backtick json).
    2. A raw JSON value that may be surrounded by prose.
    3. Text that IS valid JSON already.
    """
    import json as _json

    # 1. Direct parse (text is already clean JSON)
    try:
        _json.loads(text)
        return text
    except _json.JSONDecodeError:
        pass

    # 2. Markdown fenced block
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            _json.loads(candidate)
            return candidate
        except _json.JSONDecodeError:
            pass

    # 3. Find first { or [ and use raw_decode to get exactly the JSON span
    decoder = _json.JSONDecoder()
    for start_char in ["{", "["]:
        idx = text.find(start_char)
        if idx == -1:
            continue
        try:
            _, end = decoder.raw_decode(text, idx)
            return text[idx:end]
        except _json.JSONDecodeError:
            continue

    return text


def _diagnosis_context(gate_evidence: list[ToolEvidence]) -> str:
    """Build a human-readable summary of prior loop diagnoses for dev prompts."""
    failed = [e for e in gate_evidence if not e.passed and e.diagnosis]
    if not failed:
        return ""
    lines = ["Prior review loop failures (fix these):\n"]
    for e in failed:
        lines.append(f"  [{e.tool_name}] {e.diagnosis}\n")
    return "".join(lines)


# ---------------------------------------------------------------------------
# 1. TechLead — planning
# ---------------------------------------------------------------------------

def tech_lead_node(state: SDLCState, llm: BaseLLM | None = None) -> SDLCState:
    logger.info("[TechLead] Generating requirements and architecture...")
    state.current_phase = "planning"

    _llm = llm or load_llm("main")

    lessons_block = ""
    if state.lessons_learned:
        lessons_block = "\nLessons learned from prior runs:\n" + "\n".join(
            f"- {l}" for l in state.lessons_learned
        )

    system_prompt = (
        "You are a principal engineer performing technical planning for a software project.\n"
        "You will be given a project objective and context. Your output MUST be valid JSON.\n"
        "Return a JSON object with two keys:\n"
        '  "requirements": a list of objects, each with "id" (string, format REQ-NNN),\n'
        '    "description" (string), and "acceptance_criteria" (list of strings).\n'
        '  "architecture_doc": a markdown string describing the high-level architecture.\n'
        "Keep requirements focused and concrete. Minimum 1, maximum 10."
        + lessons_block
    )
    technical_req_block = ""
    if state.technical_requirements:
        technical_req_block = "\nTechnical requirements:\n" + "\n".join(
            f"- {r}" for r in state.technical_requirements
        )
    success_block = ""
    if state.success_criteria:
        success_block = "\nSuccess criteria:\n" + "\n".join(
            f"- {c}" for c in state.success_criteria
        )

    user_content = (
        f"Project objective: {state.objective}\n"
        f"Context: {state.context or 'None provided'}"
        + technical_req_block
        + success_block
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    reply = _llm.chat(messages)

    try:
        parsed = json.loads(_extract_json(reply))
        raw_reqs = parsed.get("requirements", [])
        architecture_doc = parsed.get("architecture_doc", "")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("[TechLead] JSON parse failed (%s); falling back to single stub req.", exc)
        raw_reqs = []
        architecture_doc = reply

    requirements: list[SDLCRequirement] = []
    for i, r in enumerate(raw_reqs):
        try:
            requirements.append(SDLCRequirement.model_validate(r))
        except Exception as exc:
            logger.warning("[TechLead] Skipping malformed requirement %d: %s", i, exc)

    if not requirements:
        requirements = [
            SDLCRequirement(
                id="REQ-001",
                description=f"Implement: {state.objective}",
                acceptance_criteria=["System meets the stated objective"],
            )
        ]

    state.requirements = requirements
    state.architecture_doc = architecture_doc or (
        f"# Architecture\n\nObjective: {state.objective}\n\n"
        f"Requirements identified: {len(requirements)}"
    )

    high_risk_keywords = {"security", "auth", "payment", "critical", "production"}
    text_pool = (state.objective + " " + (state.context or "")).lower()
    if any(kw in text_pool for kw in high_risk_keywords):
        state.risk_level = "high"
    elif len(requirements) > 5:
        state.risk_level = "medium"
    else:
        state.risk_level = "low"

    state.current_phase = "implementation"
    logger.info("[TechLead] Done. %d requirements, risk=%s.", len(requirements), state.risk_level)
    return state


# ---------------------------------------------------------------------------
# 2. Dev — implementation
# ---------------------------------------------------------------------------

def dev_node(state: SDLCState, llm: BaseLLM | None = None) -> SDLCState:
    logger.info("[Dev] Writing implementation files...")
    state.current_phase = "implementation"

    _llm = llm or load_llm("main")
    arch_context = f"\nArchitecture context:\n{state.architecture_doc}\n" if state.architecture_doc else ""
    diag_context = _diagnosis_context(state.gate_evidence)

    changes = []
    for req in state.requirements:
        rel_path = f"src/{req.id.lower().replace('-', '_')}_impl.py"

        system_prompt = (
            "You are an expert Python developer. Write complete, production-ready Python code.\n"
            "Rules:\n"
            "- The FIRST line of your output must be exactly: # Requirement: {req_id}\n"
            "- Output ONLY the Python source file. No markdown. No explanation.\n"
            "- Use type hints throughout.\n"
            "- Functions must have docstrings.\n"
            "- Do not use external libraries unless explicitly required.\n"
        ).format(req_id=req.id)

        user_content = (
            f"Requirement ID: {req.id}\n"
            f"Description: {req.description}\n"
            f"Acceptance criteria:\n"
            + "\n".join(f"  - {c}" for c in req.acceptance_criteria)
            + arch_context
            + (f"\n{diag_context}" if diag_context else "")
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        code = _llm.chat(messages).strip()
        tag = f"# Requirement: {req.id}"
        if not code.startswith(tag):
            code = f"{tag}\n{code}"

        fc = write_file(
            run_id=state.run_id,
            path=rel_path,
            content=code,
            requirement_id=req.id,
            rationale=f"LLM-generated implementation for {req.id}: {req.description}",
        )
        changes.append(fc)
        logger.info("[Dev] Wrote %s (%d chars).", rel_path, len(code))

    state.files_changed = changes
    state.current_phase = "testing"
    logger.info("[Dev] Done. %d files written.", len(state.files_changed))
    return state


# ---------------------------------------------------------------------------
# 3. QA — test generation
# ---------------------------------------------------------------------------

def qa_node(state: SDLCState, llm: BaseLLM | None = None) -> SDLCState:
    logger.info("[QA] Writing test files...")
    state.current_phase = "testing"

    _llm = llm or load_llm("main")

    tests = []
    for req in state.requirements:
        rel_path = f"tests/test_{req.id.lower().replace('-', '_')}.py"
        impl_module = req.id.lower().replace("-", "_") + "_impl"
        impl_path = f"src/{impl_module}.py"

        system_prompt = (
            "You are a senior QA engineer writing pytest test files.\n"
            "Rules:\n"
            "- Output ONLY the Python test file. No markdown. No explanation.\n"
            "- The FIRST line must be exactly: # Requirement: {req_id}\n"
            "- Import the implementation module using sys.path manipulation:\n"
            "    import sys\n"
            "    from pathlib import Path\n"
            "    sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))\n"
            "- Each test function must start with 'def test_'.\n"
            "- Cover every acceptance criterion with at least one test.\n"
            "- Tests must be self-contained (no fixtures requiring external services).\n"
            "- Use type hints.\n"
        ).format(req_id=req.id)

        user_content = (
            f"Requirement ID: {req.id}\n"
            f"Description: {req.description}\n"
            f"Acceptance criteria:\n"
            + "\n".join(f"  - {c}" for c in req.acceptance_criteria)
            + f"\nImplementation module to test: {impl_module} (file: {impl_path})"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        code = _llm.chat(messages).strip()
        tag = f"# Requirement: {req.id}"
        if not code.startswith(tag):
            code = f"{tag}\n{code}"

        fc = write_file(
            run_id=state.run_id,
            path=rel_path,
            content=code,
            requirement_id=req.id,
            rationale=f"LLM-generated tests for {req.id}: {req.description}",
        )
        tests.append(fc)
        logger.info("[QA] Wrote %s (%d chars).", rel_path, len(code))

    state.tests_written = tests
    state.current_phase = "review"
    logger.info("[QA] Done. %d test files written.", len(state.tests_written))
    return state


# ---------------------------------------------------------------------------
# 4. Review — deterministic gates + LLM diagnosis
# ---------------------------------------------------------------------------

def review_node(state: SDLCState, llm: BaseLLM | None = None) -> SDLCState:
    logger.info("[Review] Running quality gates...")
    state.current_phase = "review"

    _llm = llm or load_llm("diagnostic")
    diagnostic = DiagnosticUtility(_llm)

    qt = state.quality_thresholds
    evidence: list[ToolEvidence] = []

    # --- Required ---
    evidence.append(run_ruff(state.run_id))
    evidence.append(run_pytest(state.run_id, min_coverage=qt.min_test_coverage))

    # --- Optional ---
    evidence.append(run_mypy(state.run_id, enforce=qt.enforce_type_hints))
    evidence.append(run_bandit(state.run_id))
    evidence.append(run_pip_audit(state.run_id))
    evidence.append(run_complexity_check(state.run_id, max_complexity=qt.max_cyclomatic_complexity))

    # Populate diagnosis on failures
    for ev in evidence:
        if not ev.passed:
            try:
                ev.diagnosis = diagnostic.diagnose(ev)
            except Exception as exc:
                logger.warning("[Review] Diagnosis failed for %s: %s", ev.tool_name, exc)
                ev.diagnosis = f"Diagnosis unavailable: {exc}"

    state.gate_evidence = evidence

    required_failed = [
        e for e in evidence if e.tool_name in _REQUIRED_TOOLS and not e.passed
    ]

    if not required_failed:
        state.current_phase = "release"
        logger.info("[Review] All required gates passed.")
    else:
        state.loop_count += 1
        state.current_phase = "implementation"
        failed_names = [e.tool_name for e in required_failed]
        logger.warning("[Review] Required gates failed: %s. Loop %d.", failed_names, state.loop_count)

    return state


# ---------------------------------------------------------------------------
# 5. ReleaseEngineer — traceability + LLM release notes
# ---------------------------------------------------------------------------

def release_engineer_node(state: SDLCState, llm: BaseLLM | None = None) -> SDLCState:
    logger.info("[ReleaseEngineer] Validating traceability and generating release notes...")
    state.current_phase = "release"

    # Safety net: block done if any required gate evidence is a failure.
    # (review_node normally catches this, but RE is the hard stop.)
    required_failures = [
        e for e in state.gate_evidence
        if e.tool_name in _REQUIRED_TOOLS and not e.passed
    ]
    if required_failures:
        failed_names = [e.tool_name for e in required_failures]
        logger.error(
            "[ReleaseEngineer] Blocking release — required gates still failing: %s",
            failed_names,
        )
        # Do NOT increment loop_count here — review_node already counted this loop.
        state.current_phase = "implementation"
        return state

    # Traceability check
    req_ids = {r.id for r in state.requirements}
    covered_by_code = {f.requirement_id for f in state.files_changed}
    covered_by_tests = {f.requirement_id for f in state.tests_written}
    missing_code = req_ids - covered_by_code
    missing_tests = req_ids - covered_by_tests
    if missing_code:
        logger.warning("[ReleaseEngineer] Missing code for: %s", missing_code)
    if missing_tests:
        logger.warning("[ReleaseEngineer] Missing tests for: %s", missing_tests)

    # Generate release notes with LLM
    _llm = llm or load_llm("main")
    gate_summary = "\n".join(
        f"  {e.tool_name}: {'PASS' if e.passed else 'FAIL'}" for e in state.gate_evidence
    )
    req_summary = "\n".join(f"  - {r.id}: {r.description}" for r in state.requirements)
    files_summary = "\n".join(f"  - {f.path}" for f in state.files_changed)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a release engineer writing concise release notes for a software delivery.\n"
                "Output plain markdown. Be factual and brief."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Run ID: {state.run_id}\n"
                f"Objective: {state.objective}\n\n"
                f"Requirements delivered:\n{req_summary}\n\n"
                f"Files changed:\n{files_summary}\n\n"
                f"Quality gate results:\n{gate_summary}\n\n"
                "Write release notes."
            ),
        },
    ]

    try:
        state.release_notes = _llm.chat(messages).strip()
    except Exception as exc:
        logger.warning("[ReleaseEngineer] LLM release notes failed (%s); using fallback.", exc)
        state.release_notes = (
            f"# Release Notes — {state.run_id}\n\n"
            f"**Objective:** {state.objective}\n\n"
            f"**Requirements:** {len(state.requirements)}\n"
            f"**Files changed:** {len(state.files_changed)}\n"
            f"**Tests written:** {len(state.tests_written)}\n"
            f"**Gate evidence:** {len(state.gate_evidence)}\n"
        )

    state.current_phase = "done"
    logger.info("[ReleaseEngineer] Done.")
    return state


# ---------------------------------------------------------------------------
# 6. Supervisor — routing + HITL escalation
# ---------------------------------------------------------------------------

def supervisor_node(state: SDLCState, llm: BaseLLM | None = None) -> SDLCState:
    logger.info("[Supervisor] Evaluating state. Phase=%s loop=%d", state.current_phase, state.loop_count)

    escalate = state.loop_count >= 3 or state.risk_level == "critical"

    # Generate a supervisor summary with LLM
    _llm = llm or load_llm("main")
    gate_summary = "\n".join(
        f"  {e.tool_name}: {'PASS' if e.passed else 'FAIL'}"
        + (f" — {e.diagnosis}" if e.diagnosis else "")
        for e in state.gate_evidence
    ) or "  No gate evidence collected."

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior engineering manager reviewing an automated SDLC run.\n"
                "Summarise the run outcome concisely (max 100 words).\n"
                "If escalating to human review, explain why."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Run ID: {state.run_id}\n"
                f"Phase: {state.current_phase}\n"
                f"Loop count: {state.loop_count}\n"
                f"Risk level: {state.risk_level}\n"
                f"Gate results:\n{gate_summary}\n"
                f"Escalating to human review: {escalate}"
            ),
        },
    ]

    try:
        state.supervisor_notes = _llm.chat(messages).strip()
        logger.info("[Supervisor] Notes: %s", state.supervisor_notes[:120])
    except Exception as exc:
        logger.warning("[Supervisor] LLM summary failed (%s).", exc)
        state.supervisor_notes = f"Supervisor LLM unavailable: {exc}"

    if state.loop_count >= 3:
        logger.warning("[Supervisor] Loop cap reached. Escalating to human_review.")
        state.current_phase = "human_review"
        return state

    if state.risk_level == "critical":
        logger.warning("[Supervisor] Critical risk. Escalating to human_review.")
        state.current_phase = "human_review"
        return state

    logger.info("[Supervisor] Run complete.")
    return state
