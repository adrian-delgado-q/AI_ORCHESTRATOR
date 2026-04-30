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
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.agents.diagnostic import DiagnosticUtility
from src.core.llm import BaseLLM, load_llm
from src.core.timing import timed
from src.io.workspace import VOLUMES_DIR, write_file
from src.state.schema import FileChange, SDLCRequirement, SDLCState, ToolEvidence
from src.tools.runners import (
    run_bandit,
    run_complexity_check,
    run_mypy,
    run_pip_audit,
    run_pytest,
    run_ruff,
    run_ruff_fix,
    shared_review_sandbox,
)

logger = logging.getLogger(__name__)

# Required gates — failure triggers a dev loop.
_REQUIRED_TOOLS = {"ruff", "pytest"}

_IMPORT_PACKAGE_MAP = {
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "jwt": "PyJWT",
    "jose": "python-jose",
    "numpy": "numpy",
    "pandas": "pandas",
    "passlib": "passlib",
    "pydantic": "pydantic",
    "pytest": "pytest",
    "requests": "requests",
    "sqlalchemy": "sqlalchemy",
    "starlette": "starlette",
    "uvicorn": "uvicorn",
    "yaml": "PyYAML",
}

_COMMON_LOCAL_MODULE_PREFIXES = {"req_"}


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


def _llm_concurrency() -> int:
    raw = os.environ.get("OMEGA_LLM_CONCURRENCY", "4")
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("[config] Invalid OMEGA_LLM_CONCURRENCY=%r; using 4.", raw)
        return 4


def _full_review_on_required_failure() -> bool:
    return os.environ.get("OMEGA_REVIEW_FULL_ON_REQUIRED_FAILURE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _chat(run_id: str, llm: BaseLLM, node: str, messages: list[dict]) -> str:
    with timed(run_id, "llm", node, {"messages": len(messages)}):
        return llm.chat(messages)


def _infer_requirements_from_imports(run_id: str, files: list[FileChange]) -> str:
    """Build requirements.txt content from Python imports in generated files."""
    import ast

    volume = VOLUMES_DIR / run_id
    modules: set[str] = set()
    for fc in files:
        if not fc.path.endswith(".py"):
            continue
        path = volume / fc.path
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            logger.warning("[Deps] Skipping import scan for syntax-invalid file: %s", fc.path)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module.split(".", 1)[0])

    stdlib = getattr(sys, "stdlib_module_names", set())
    local_modules = {
        Path(fc.path).stem
        for fc in files
        if fc.path.endswith(".py")
    }
    packages: set[str] = set()
    for module in modules:
        if module in stdlib or module in local_modules:
            continue
        if any(module.startswith(prefix) for prefix in _COMMON_LOCAL_MODULE_PREFIXES):
            continue
        package = _IMPORT_PACKAGE_MAP.get(module)
        if package:
            packages.add(package)

    # Starlette/FastAPI TestClient imports httpx at runtime.
    if {"fastapi", "starlette"} & packages:
        packages.add("httpx")

    return "\n".join(sorted(packages)) + ("\n" if packages else "")


def gate_failure_context(
    gate_evidence: list[ToolEvidence],
    roles: set[str] | None = None,
) -> str:
    """Generic failure context block for any node prompt.

    Filters failures by *roles* (e.g. {'linter'}, {'test'}) — or returns all
    failures when *roles* is None.  Includes the full raw tool output so the
    LLM sees actual file:line errors, not a truncated paraphrase.

    Language/tool agnostic: callers request by role, never by tool name, so
    swapping ruff→eslint or pytest→jest requires no node changes.
    """
    failed = [
        e for e in gate_evidence
        if not e.passed and (roles is None or not e.role or e.role in roles)
    ]
    if not failed:
        return ""
    lines = ["PRIOR REVIEW LOOP FAILURES — you MUST fix all of these:\n"]
    for e in failed:
        lines.append(f"\n=== [{e.tool_name} / role={e.role}] ===")
        if e.diagnosis:
            lines.append(f"Diagnosis: {e.diagnosis}")
        lines.append(f"Full output:\n{e.findings}")
    return "\n".join(lines)


def _diagnosis_context(gate_evidence: list[ToolEvidence]) -> str:
    """Backward-compatible Stage 4 helper name."""
    failed = [e for e in gate_evidence if not e.passed and e.diagnosis]
    if not failed:
        return ""
    lines = ["Prior review loop failures (fix these):\n"]
    for e in failed:
        lines.append(f"  [{e.tool_name}] {e.diagnosis}\n")
    return "".join(lines)


def _inspect_module_exports(file_path) -> str:
    """Return a human-readable summary of top-level public names in a Python file.

    Uses ast.parse so it works before the module can be imported.  Safe to call
    even if the file does not exist or has a syntax error — returns a fallback
    message in those cases so callers never need to handle exceptions.

    Examples of output lines:
      router: APIRouter()         ← module-level assignment
      app: FastAPI()              ← module-level assignment
      todos: list                 ← module-level assignment (bare list literal)
      Todo: class(BaseModel)      ← class definition
      create_todo: function       ← function definition
    """
    import ast as _ast
    from pathlib import Path as _Path

    path = _Path(file_path)
    if not path.exists():
        return "(impl file not found — cannot inspect exports)"
    try:
        tree = _ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return f"(syntax error in impl file — cannot inspect exports: {exc})"

    exports: list[str] = []
    for node in tree.body:
        if isinstance(node, _ast.Assign):
            # e.g. router = APIRouter()  /  app = FastAPI()  /  todos = []
            for target in node.targets:
                if not isinstance(target, _ast.Name) or target.id.startswith("_"):
                    continue
                name = target.id
                val = node.value
                if isinstance(val, _ast.Call):
                    func_name = ""
                    if isinstance(val.func, _ast.Name):
                        func_name = val.func.id
                    elif isinstance(val.func, _ast.Attribute):
                        func_name = val.func.attr
                    exports.append(f"{name}: {func_name}()" if func_name else f"{name}: (assigned)")
                elif isinstance(val, _ast.List):
                    exports.append(f"{name}: list")
                elif isinstance(val, _ast.Dict):
                    exports.append(f"{name}: dict")
                else:
                    exports.append(f"{name}: (assigned)")
        elif isinstance(node, _ast.AnnAssign):
            # e.g. todos: list[Todo] = []
            if isinstance(node.target, _ast.Name) and not node.target.id.startswith("_"):
                exports.append(f"{node.target.id}: (annotated assignment)")
        elif isinstance(node, _ast.ClassDef) and not node.name.startswith("_"):
            bases = ", ".join(
                (b.id if isinstance(b, _ast.Name) else b.attr if isinstance(b, _ast.Attribute) else "?")
                for b in node.bases
            )
            exports.append(f"{node.name}: class({bases})" if bases else f"{node.name}: class")
        elif isinstance(node, _ast.FunctionDef) and not node.name.startswith("_"):
            exports.append(f"{node.name}: function")
        elif isinstance(node, (_ast.AsyncFunctionDef,)) and not node.name.startswith("_"):
            exports.append(f"{node.name}: async function")

    if not exports:
        return "(no public top-level names found in impl file)"
    return "\n  ".join(exports)


def _extract_import_errors(evidence: list[ToolEvidence]) -> list[tuple[str, str, str]]:
    """Parse pytest findings for ImportError: cannot import name 'X' from 'Y'.

    Returns a list of (test_file, imported_name, impl_module) tuples.
    """
    import re as _re
    pattern = _re.compile(
        r"(tests/test_\S+\.py).*?ImportError.*?cannot import name '(\w+)' from '(\w+)'",
        _re.DOTALL,
    )
    results = []
    for ev in evidence:
        if ev.findings:
            for m in pattern.finditer(ev.findings):
                results.append((m.group(1), m.group(2), m.group(3)))
    return results


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

    reply = _chat(state.run_id, _llm, "tech_lead_node", messages)

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
    diag_context = gate_failure_context(state.gate_evidence, roles={"linter", "local_module"})

    # Remove stale implementation files from previous runs/loops so ruff never
    # sees orphaned files that no longer correspond to current requirements.
    src_dir = VOLUMES_DIR / state.run_id / "src"
    if src_dir.exists():
        for old_file in src_dir.glob("req_*_impl.py"):
            try:
                old_file.unlink()
            except OSError:
                pass

    def _generate_impl(req: SDLCRequirement) -> tuple[SDLCRequirement, str, str]:
        rel_path = f"src/{req.id.lower().replace('-', '_')}_impl.py"

        system_prompt = (
            "You are an expert Python developer. Write complete, production-ready Python code.\n"
            "Rules:\n"
            "- The FIRST line of your output must be exactly: # Requirement: {req_id}\n"
            "- Output ONLY the Python source file. No markdown. No explanation.\n"
            "- Use type hints throughout.\n"
            "- Functions must have docstrings.\n"
            "- Do not use external libraries unless explicitly required.\n"
            "- IMPORT ORDER IS CRITICAL: ALL import and from-import statements MUST appear\n"
            "  at the very top of the file, immediately after the first comment line,\n"
            "  before ANY class or function definitions. Never add an import after a class,\n"
            "  function, or any executable statement. ruff will fail with E402 otherwise.\n"
            "- If you need fastapi.APIRouter, import it in the top-level imports block,\n"
            "  not after your model or storage classes.\n"
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

        code = _chat(state.run_id, _llm, "dev_node", messages).strip()
        # Strip markdown fences the LLM sometimes wraps around code blocks.
        # Leaving them in causes E999 SyntaxError in ruff on every loop 1.
        code = re.sub(r"^```[^\n]*\n", "", code)
        code = re.sub(r"\n```$", "", code).strip()
        tag = f"# Requirement: {req.id}"
        if not code.startswith(tag):
            code = f"{tag}\n{code}"
        return req, rel_path, code

    concurrency = min(_llm_concurrency(), max(1, len(state.requirements)))
    if concurrency > 1 and len(state.requirements) > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            generated = list(pool.map(_generate_impl, state.requirements))
    else:
        generated = [_generate_impl(req) for req in state.requirements]

    changes = []
    for req, rel_path, code in generated:
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

    # ---- Generate requirements.txt from src/ AND existing tests/ ----
    # Scanning both dirs ensures test-only deps (e.g. httpx for TestClient,
    # requests-mock, etc.) are included alongside runtime deps.
    all_files: list[FileChange] = list(changes) + list(state.tests_written)
    req_content = _infer_requirements_from_imports(state.run_id, all_files).strip()
    write_file(
        run_id=state.run_id,
        path="requirements.txt",
        content=(req_content + "\n") if req_content else "",
        requirement_id="DEPS",
        rationale="Import-scanned runtime/test dependencies for the generated project.",
    )
    logger.info("[Dev] Wrote requirements.txt.")

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
    # Include linter, import_mismatch, and test failures.
    # import_mismatch carries the real impl exports so the LLM stops guessing.
    test_ctx = gate_failure_context(state.gate_evidence, roles={"test", "linter", "import_mismatch"})

    # Remove stale test files from previous runs/loops so ruff/pytest never
    # see orphaned tests that no longer correspond to current requirements.
    tests_dir = VOLUMES_DIR / state.run_id / "tests"
    if tests_dir.exists():
        for old_file in tests_dir.glob("test_req_*.py"):
            try:
                old_file.unlink()
            except OSError:
                pass

    def _generate_test(req: SDLCRequirement) -> tuple[SDLCRequirement, str, str]:
        rel_path = f"tests/test_{req.id.lower().replace('-', '_')}.py"
        impl_module = req.id.lower().replace("-", "_") + "_impl"
        impl_path = f"src/{impl_module}.py"

        system_prompt = (
            "You are a senior QA engineer writing pytest test files for FastAPI projects.\n"
            "Rules:\n"
            "- Output ONLY the Python test file. No markdown. No explanation.\n"
            "- The FIRST line must be exactly: # Requirement: {req_id}\n"
            "- IMPORT ORDER: The sys.path block MUST come immediately after the first comment\n"
            "  line and BEFORE all other imports, like this (copy exactly):\n"
            "    import sys\n"
            "    from pathlib import Path\n"
            "    sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))\n"
            "  Then all other imports follow (no # noqa comment needed).\n"
            "- Each test function must start with 'def test_'.\n"
            "- Cover every acceptance criterion with at least one test.\n"
            "- Tests must be self-contained (no fixtures requiring external services).\n"
            "- Use type hints.\n"
            "- FASTAPI TESTING: Use starlette.testclient.TestClient, NOT Flask test_client().\n"
            "  Correct pattern:\n"
            "    from starlette.testclient import TestClient\n"
            "    client = TestClient(app)   # app is a FastAPI() instance\n"
            "    response = client.post('/todos', json={{'title': 'x'}})\n"
            "    assert response.status_code == 201\n"
            "    data = response.json()     # NOT response.get_json()\n"
            "- NEVER use app.test_client() or response.get_json() -- those are Flask APIs.\n"
            "- If the implementation module exposes a router (APIRouter) rather than a\n"
            "  bare FastAPI app, create a minimal test app:\n"
            "    from fastapi import FastAPI\n"
            "    from {impl_module} import router\n"
            "    app = FastAPI(); app.include_router(router)\n"
            "    client = TestClient(app)\n"
        ).format(req_id=req.id, impl_module=impl_module)

        # Read the actual impl file to get ground-truth public names.
        # This prevents the LLM from guessing 'router' when the file
        # exposes 'app', or vice versa.
        impl_abs = VOLUMES_DIR / state.run_id / impl_path
        impl_exports = _inspect_module_exports(impl_abs)

        user_content = (
            f"Requirement ID: {req.id}\n"
            f"Description: {req.description}\n"
            f"Acceptance criteria:\n"
            + "\n".join(f"  - {c}" for c in req.acceptance_criteria)
            + f"\nImplementation module to test: {impl_module} (file: {impl_path})"
            + f"\n\nACTUAL public names exported by {impl_module} (MUST use only these):\n  {impl_exports}"
            + (f"\n\n{test_ctx}" if test_ctx else "")
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        code = _chat(state.run_id, _llm, "qa_node", messages).strip()
        # Strip markdown fences — same issue as dev_node, kills E999 on loop 1.
        code = re.sub(r"^```[^\n]*\n", "", code)
        code = re.sub(r"\n```$", "", code).strip()
        tag = f"# Requirement: {req.id}"
        if not code.startswith(tag):
            code = f"{tag}\n{code}"
        return req, rel_path, code

    concurrency = min(_llm_concurrency(), max(1, len(state.requirements)))
    if concurrency > 1 and len(state.requirements) > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            generated_tests = list(pool.map(_generate_test, state.requirements))
    else:
        generated_tests = [_generate_test(req) for req in state.requirements]

    tests = []
    for req, rel_path, code in generated_tests:
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

    # Regenerate requirements after tests are written so test-only imports are
    # available before the review node installs dependencies.
    all_files: list[FileChange] = list(state.files_changed) + list(tests)
    req_content = _infer_requirements_from_imports(state.run_id, all_files).strip()
    write_file(
        run_id=state.run_id,
        path="requirements.txt",
        content=(req_content + "\n") if req_content else "",
        requirement_id="DEPS",
        rationale="Import-scanned runtime/test dependencies for the generated project.",
    )
    logger.info("[QA] Refreshed requirements.txt.")

    state.current_phase = "review"
    logger.info("[QA] Done. %d test files written.", len(state.tests_written))
    return state


# ---------------------------------------------------------------------------
# 4. Review — deterministic gates + LLM diagnosis
# ---------------------------------------------------------------------------

def _extract_missing_modules(evidence: list[ToolEvidence]) -> set[str]:
    """Parse all gate findings for 'ModuleNotFoundError: No module named X'.

    Returns a set of bare package names that need to be added to
    requirements.txt.  Works for any language toolchain that surfaces the
    standard Python import error message — no hardcoded package names.
    """
    import re as _re
    missing: set[str] = set()
    pattern = _re.compile(r"No module named '([\w]+)'")  # 'httpx', 'pydantic', …
    for ev in evidence:
        if ev.findings:
            for match in pattern.finditer(ev.findings):
                missing.add(match.group(1))
    return missing


def review_node(state: SDLCState, llm: BaseLLM | None = None) -> SDLCState:
    logger.info("[Review] Running quality gates...")
    state.current_phase = "review"

    _llm = llm or load_llm("diagnostic")
    diagnostic = DiagnosticUtility(_llm)

    qt = state.quality_thresholds
    evidence: list[ToolEvidence] = []

    with shared_review_sandbox(state.run_id):
        # --- Required ---
        evidence.append(run_ruff(state.run_id))
        evidence.append(run_pytest(state.run_id, min_coverage=qt.min_test_coverage))

        required_failed_now = [
            e for e in evidence if e.tool_name in _REQUIRED_TOOLS and not e.passed
        ]
        if required_failed_now and not _full_review_on_required_failure():
            logger.info("[Review] Required gates failed; skipping optional gates for faster repair loop.")
        else:
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

    # ── Import-mismatch evidence ──────────────────────────────────────────────
    # Parse pytest output for "cannot import name 'X' from 'Y'" errors.
    # For each mismatch, read the impl file with AST and build a ToolEvidence
    # that carries the ACTUAL exports — so qa_node stops guessing wrong names.
    import_errors = _extract_import_errors(evidence)
    if import_errors:
        mismatch_parts: list[str] = []
        seen_modules: set[str] = set()
        for (test_file, imported_name, impl_module) in import_errors:
            impl_abs = VOLUMES_DIR / state.run_id / f"src/{impl_module}.py"
            exports = _inspect_module_exports(impl_abs)
            mismatch_parts.append(
                f"{test_file}: tried to import '{imported_name}' from '{impl_module}'.\n"
                f"  Actual exports in {impl_module}:\n    {exports}"
            )
            seen_modules.add(impl_module)
        mismatch_findings = (
            "IMPORT CONTRACT MISMATCH — tests import names that don't exist in the impl.\n"
            "You MUST rewrite the tests to use only the names listed under 'Actual exports'.\n\n"
            + "\n\n".join(mismatch_parts)
        )
        mismatch_ev = ToolEvidence(
            tool_name="import_contract",
            passed=False,
            role="import_mismatch",
            findings=mismatch_findings,
            diagnosis="Test imports names not present in impl. Use actual exports only.",
        )
        evidence = list(evidence) + [mismatch_ev]
        state.gate_evidence = evidence
        logger.warning(
            "[Review] Import contract mismatches detected (%d). Injected import_mismatch evidence.",
            len(import_errors),
        )
    # ─────────────────────────────────────────────────────────────────────────

    state.gate_evidence = evidence

    # ── Ruff auto-fix short-circuit ───────────────────────────────────────────
    # Always run FIRST — it is purely local file surgery, no network, no LLM.
    # If all ruff errors carry the [*] fixable marker, apply --fix in-place and
    # loop back to review.  loop_count NOT incremented.
    # If mixed fixable+non-fixable, apply --fix to reduce noise then fall
    # through to the LLM loop for the remainder.
    ruff_ev = next((e for e in evidence if e.tool_name == "ruff" and not e.passed), None)
    if ruff_ev is not None:
        findings_lines = (ruff_ev.findings or "").splitlines()
        error_lines = [l for l in findings_lines if ".py:" in l and ": " in l and not l.startswith("Found")]
        fixable = [l for l in error_lines if "[*]" in l]
        non_fixable = [l for l in error_lines if "[*]" not in l]
        if fixable and not non_fixable:
            logger.info(
                "[Review] Ruff: %d auto-fixable error(s), 0 non-fixable — applying ruff --fix.",
                len(fixable),
            )
            rc, fix_out = run_ruff_fix(state.run_id)
            logger.info("[Review] ruff --fix exit=%d: %s", rc, fix_out[:200] if fix_out else "(no output)")
            state.current_phase = "review"
            logger.info("[Review] Ruff auto-fix applied — re-running gates.")
            return state
        elif fixable and non_fixable:
            logger.info(
                "[Review] Ruff: %d auto-fixable + %d non-fixable — applying --fix, routing to dev for remainder.",
                len(fixable), len(non_fixable),
            )
            run_ruff_fix(state.run_id)
    # ─────────────────────────────────────────────────────────────────────────

    # ── Dynamic dep resolution ────────────────────────────────────────────────
    # If any gate printed "No module named 'X'", try to install X from PyPI.
    # Call install_deps eagerly right here so we know immediately whether pip
    # can satisfy the module or not — rather than blindly returning and hoping.
    #
    # Success (rc==0) → loop_count unchanged, loop back to review.
    # Failure (rc!=0) → X is a hallucinated local file, not a PyPI package.
    #   Strip the bad entry from requirements.txt, add a ToolEvidence with
    #   role='local_module' so dev_node sees the exact error, and route to dev.
    missing_mods = _extract_missing_modules(evidence)
    if missing_mods:
        req_path = VOLUMES_DIR / state.run_id / "requirements.txt"
        existing_reqs: set[str] = set()
        if req_path.exists():
            existing_reqs = {line.strip().lower() for line in req_path.read_text().splitlines() if line.strip()}
        new_mods = {m for m in missing_mods if m.lower() not in existing_reqs}
        if new_mods:
            logger.warning(
                "[Review] Missing modules detected: %s — patching requirements.txt.",
                sorted(new_mods),
            )
            with req_path.open("a") as fh:
                for mod in sorted(new_mods):
                    fh.write(f"{mod}\n")
        else:
            logger.warning(
                "[Review] Missing modules %s already in requirements.txt — forcing reinstall.",
                sorted(missing_mods),
            )

        # Eager install — we need the return code NOW.
        import src.tools.runners as _runners
        _runners._deps_installed.discard(state.run_id)
        stale = VOLUMES_DIR / state.run_id / ".deps-stale"
        if stale.exists():
            stale.unlink(missing_ok=True)
        from src.sandbox.manager import get_sandbox_manager
        install_rc, install_out = get_sandbox_manager().install_deps(state.run_id)

        if install_rc == 0:
            _runners._deps_installed.add(state.run_id)
            if req_path.exists():
                _runners._write_deps_hash(state.run_id, _runners._requirements_hash(req_path))
            # Real PyPI packages — reinstall succeeded, re-run gates.
            logger.info("[Review] Dep install succeeded — re-running gates.")
            state.current_phase = "review"
            return state
        else:
            # pip cannot install the module → it is a fabricated local import.
            # Strip the bad entries from requirements.txt so they don't keep
            # poisoning future installs.
            bad_mods = {m.lower() for m in missing_mods}
            if req_path.exists():
                clean_lines = [
                    line for line in req_path.read_text().splitlines()
                    if line.strip().lower() not in bad_mods
                ]
                req_path.write_text("\n".join(clean_lines) + "\n")
                logger.warning(
                    "[Review] Removed non-installable entries from requirements.txt: %s",
                    sorted(bad_mods),
                )
            # Inject a synthetic ToolEvidence so dev_node gets the full context.
            local_mod_evidence = ToolEvidence(
                tool_name="local_module_import",
                passed=False,
                role="local_module",
                findings=(
                    f"pip install failed for: {sorted(missing_mods)}\n"
                    f"pip output:\n{install_out[:600]}\n\n"
                    "These are NOT PyPI packages — the implementation file contains a "
                    "'from <name> import ...' that references a non-existent local module.\n"
                    "Fix: inline the class/function directly in the impl file instead of "
                    "importing from a separate module that does not exist."
                ),
                diagnosis="LLM generated an import from a local file that was never created.",
            )
            state.gate_evidence = list(evidence) + [local_mod_evidence]
            state.loop_count += 1
            state.current_phase = "implementation"
            logger.warning(
                "[Review] Non-installable local imports %s — routing to dev. Loop %d.",
                sorted(missing_mods), state.loop_count,
            )
            return state
    # ─────────────────────────────────────────────────────────────────────────

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
