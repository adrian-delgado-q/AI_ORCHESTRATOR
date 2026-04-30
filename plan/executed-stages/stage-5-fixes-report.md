# Post-Stage 5 — Quality Gate & Resilience Fixes
**Status:** Complete
**Date:** 2026-04-30
**Context:** Hotfixes applied after Stage 5 end-to-end testing across multiple
full runs. Initial failures (ruff/pytest/bandit gate failures, missing sandbox
deps, no resume) led to deeper rounds that uncovered silent failure context,
stale files on loops, a tool-name-coupled failure protocol, and a broken
self-healing loop where missing deps were never detected and fixed at runtime.
17 distinct root causes resolved across 6 source files.

---

## Problems Identified

Running `python main.py --goal config/goals/example_goal.yaml --mode local`
produced the following failures across multiple end-to-end test attempts:

| # | Area | Symptom | Root Cause |
|---|---|---|---|
| 1 | `ruff` gate | E402 / F401 failures | LLM placed imports after class definitions; ruff also scanned `.deps/` |
| 2 | `pytest` gate | `ModuleNotFoundError` | No runtime deps in Docker image; no install mechanism |
| 3 | `pytest` gate | `AttributeError` on test_client | qa_node prompt used Flask API against FastAPI app |
| 4 | `qa_node` | `KeyError: 'title'` crash | `{'title': 'x'}` in `.format()` template treated as placeholder |
| 5 | `bandit` gate | Noisy LOW-severity failures | No `-ll -ii` severity filter |
| 6 | Resilience | Full re-run after any crash | No per-node checkpointing; no `--resume` flag |
| 7 | `pip install` | Silently fails in QA containers | `network_disabled=True` prevents internet access during install |
| 8 | Logs | Failures invisible / truncated | Output capped at 120 chars; `bandit -q`; `pytest -q` |
| 9 | Loops | Orphaned files from previous runs | `req_*_impl.py` / `test_req_*.py` not cleaned up before regeneration |
| 10 | LLM prompts | LLM regenerates identical broken files | `_diagnosis_context` passed 72-char summary not raw tool output |
| 11 | `qa_node` loops | LLM ignores pytest failures | No pytest failure context injected into qa_node prompt |
| 12 | `requirements.txt` | Test deps (httpx) missing | Only `src/` files scanned; test imports never analysed |
| 13 | Failure protocol | Coupled to exact tool names | Gate context filtered by `e.tool == "pytest"` not by role |
| 14 | `dev_node` / `qa_node` | E999 SyntaxError on every loop 1 | LLM wraps output in ` ```python ... ``` ` fences; written raw to disk |
| 15 | `qa_node` loops | F841/F401 in test files never fixed | `qa_node` only saw `test` role failures, not `linter` failures in test files |
| 16 | Self-healing | Missing deps loop forever | `review_node` never parsed `ModuleNotFoundError` to patch `requirements.txt` |
| 17 | Routing | Dep-fix forced full code regen | No `"review" → "review"` edge; dep failures incorrectly routed back to `dev` |

---

## Fixes Applied

### 1. Dynamic runtime dependency installation (two-phase sandbox approach)

**Problem:** The `omega-python-runner` image contained only QA tools. Generated
code importing `fastapi`, `pydantic`, `uvicorn`, etc. caused `ModuleNotFoundError`
inside the sandbox, making pytest collect 0 tests and report 0% coverage.

**Rejected approach — hardcoding deps in Dockerfile:** Breaks for any non-FastAPI
project.

**First attempt — bash-wrap inside the QA container:** `_exec()` was changed to
wrap every sandboxed command as `bash -c "pip install -q -r /workspace/requirements.txt && <cmd>"`. This failed silently because `SandboxManager.create_sandbox()` sets `network_disabled=True` — the QA containers have no internet access by design. The `pip install` retry warnings were being truncated in the log to 120 chars, which obscured the root cause.

**Final solution — two-phase dep handling:**

1. `dev_node` (see §2) generates `requirements.txt` after writing impl files.
2. Before the first QA tool runs per review cycle, `_ensure_deps_installed()` in
   `runners.py` detects `requirements.txt` and spins up a **separate
   network-enabled container** that installs deps with
   `pip install -r /workspace/requirements.txt --target /workspace/.deps`.
   This container is destroyed immediately after.
3. All QA containers (still `network_disabled=True`) receive
   `PYTHONPATH=/workspace/.deps` via `exec_run`'s `environment` parameter,
   allowing them to import installed packages without needing internet access.
4. `_ensure_deps_installed()` is idempotent: it skips if `.deps/` is already
   populated (cross-session resume) and uses a `.deps-stale` sentinel file
   to force reinstall when `dev_node` regenerates `requirements.txt` during a
   loop but cannot delete a root-owned `.deps/` directory.

**Files changed:**

| File | Change |
|---|---|
| `docker/Dockerfile.python-runner` | QA-tools-only; comment explains the dynamic install pattern. |
| `src/sandbox/manager.py` | Added `install_deps(run_id)` method — network-enabled container, `pip install --target /workspace/.deps`. Added `env: dict` param to `exec_in_sandbox()`. |
| `src/tools/sandboxed_runner.py` | Threaded `env` param through to `exec_in_sandbox()`. |
| `src/tools/runners.py` | Removed bash-wrap. Added `_deps_installed: set` session cache. Added `_ensure_deps_installed()`. `_exec()` calls it before sandbox dispatch and injects `PYTHONPATH=/workspace/.deps`. |
| `src/agents/nodes.py` | After regenerating `requirements.txt` in `dev_node`, removes `.deps/` (or writes `.deps-stale` if root-owned) and calls `_runners._deps_installed.discard(run_id)` to force reinstall on the next review cycle. |
| `main.py` | Fresh run clears `.deps/` (or writes `.deps-stale` if root-owned). |

---

### 2. Automatic `requirements.txt` generation in `dev_node` (`src/agents/nodes.py`)

**Problem:** No mechanism existed to declare what third-party packages the
generated code needed. The sandbox had no way to install them.

**Solution:** After writing all impl files, `dev_node` concatenates the
generated Python source (capped at 8 000 chars to stay within context limits)
and makes one LLM call with the prompt:

> *"Given Python source files, output ONLY a valid pip requirements.txt with
> one package per line. Include only third-party packages."*

The response is stripped of any markdown fencing and written to
`volumes/{run_id}/requirements.txt`. The `_exec()` wrapper in runners then
installs it automatically at review time.

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | Added `VOLUMES_DIR` to imports. After the impl-file loop in `dev_node`, added an LLM call to generate `requirements.txt` and a `write_file()` call to persist it. |

---

### 3. `dev_node` system prompt — enforce top-of-file imports (`src/agents/nodes.py`)

**Problem:** The LLM generated impl files that placed some imports (notably
`from fastapi import APIRouter`) after class and storage definitions. ruff
flagged these as E402 (module-level import not at top of file), causing the
required `ruff` gate to fail and triggering a dev loop.

**Solution:** Added explicit import-ordering rules to the `dev_node` system
prompt:

> *"IMPORT ORDER IS CRITICAL: ALL import and from-import statements MUST appear
> at the very top of the file, before ANY class or function definitions. Never
> add an import after a class, function, or any executable statement."*

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | `dev_node` system prompt extended with import-ordering rules and a specific callout for `APIRouter`. |

---

### 4. `qa_node` system prompt — FastAPI test client + import order (`src/agents/nodes.py`)

**Problem:** Two issues in generated test files:
1. Tests used Flask's `app.test_client()` / `response.get_json()` API against
   FastAPI apps, causing `AttributeError` on every test.
2. The `sys.path.insert()` block was sometimes placed after other imports,
   causing further E402 violations.

**Bug during fix:** The qa_node prompt template used `.format()` for
`{req_id}` and `{impl_module}` substitution but contained literal dict
examples like `{'title': 'x'}`. Python's `.format()` treated `{title}` as a
placeholder and raised `KeyError: "'title'"`. Fixed by doubling the braces:
`{{'title': 'x'}}`.

**Solution:** Replaced the Flask-centric prompt with a FastAPI-aware one:

- Instructs the LLM to use `from starlette.testclient import TestClient` and
  `TestClient(app)`.
- Provides a fallback pattern for when the impl exposes an `APIRouter` rather
  than a bare `FastAPI()` instance.
- Clarifies that `sys.path.insert()` must come before all other imports.
- Uses `response.json()` instead of `response.get_json()`.

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | `qa_node` system prompt rewritten for FastAPI. Literal `{` / `}` in example code escaped as `{{` / `}}` to avoid `KeyError` from `.format()`. |

---

### 5. `bandit` severity filter (`src/tools/runners.py`)

**Problem:** `bandit` exits with code 1 on any finding at any severity level.
Generated code routinely triggers LOW-severity items (e.g. B101 `assert` usage,
B311 random number generator). `run_bandit` treated any non-zero exit code as
failure.

**Note:** `bandit` is an optional gate (not in `_REQUIRED_TOOLS`) so failures
do not trigger a dev loop, but they generate noisy diagnostics and waste the
diagnostic LLM call.

**Solution:** Added `-ll -ii` flags to suppress LOW severity / LOW confidence
findings. Only MEDIUM+ severity and MEDIUM+ confidence issues are reported.

**Files changed:**

| File | Change |
|---|---|
| `src/tools/runners.py` | `run_bandit()` command extended with `-ll -ii` in both sandbox and host paths. |

---

### 6. Run resumability — dispatcher node + per-node checkpointing (`src/agents/graph.py`, `main.py`)

**Problem:** Every invocation of `main.py` started a fresh run from
`tech_lead`, discarding all LLM work from previous runs. Crashes mid-flow
(e.g. during `qa_node`) meant re-running all preceding nodes from scratch.
State was only saved at run start and run end.

**Solution:** Three coordinated changes:

**a) Dispatcher node in the graph (`src/agents/graph.py`)**

A new `dispatcher` node is set as the graph entry point. It is a no-op
passthrough whose sole purpose is to trigger a conditional edge that reads
`current_phase` from state and routes to the correct node:

```python
_PHASE_TO_NODE = {
    "planning":       "tech_lead",
    "implementation": "dev",
    "testing":        "qa",
    "review":         "review",
    "release":        "release_engineer",
    "done":           "__end__",
    "human_review":   "__end__",
}
```

This means a state loaded with `current_phase="testing"` will jump directly
to `qa`, skipping `tech_lead` and `dev` entirely.

**b) Per-node checkpointing (`src/agents/graph.py`)**

The `_wrap()` helper now calls `save_state(updated)` after every node
completes, before returning to LangGraph. `runs/{run_id}/state.json` always
reflects the last fully completed node.

**c) `--resume` flag and `load_state` (`main.py`)**

`run_local()` accepts a `resume: bool` parameter. When `True`:
- It calls `load_state(goal.goal_id)` to restore the last checkpoint.
- Logs the resumed phase and loop count.
- Falls back to a fresh start if no state file is found.

A `--resume` CLI argument is wired through `argparse` to `run_local`.

**Resume behaviour by failure point:**

| Crashed after node | Saved `current_phase` | Resumes at |
|---|---|---|
| `tech_lead` | `implementation` | `dev` |
| `dev` | `testing` | `qa` |
| `qa` | `review` | `review` |
| `review` (gate fail loop) | `implementation` | `dev` |

**Files changed:**

| File | Change |
|---|---|
| `src/agents/graph.py` | Added `_PHASE_TO_NODE` map, `_dispatch()` function, `dispatcher` node, conditional edges from dispatcher to all other nodes. `_wrap()` now calls `save_state()`. `set_entry_point` changed from `tech_lead` to `dispatcher`. |
| `main.py` | `run_local()` gains `resume: bool` param and `load_state` import. `--resume` argparse argument added. `run_local(args.goal, resume=args.resume)` call updated. |

---

### 7. ruff scanning `.deps/` installed packages (`src/tools/runners.py`)

**Problem:** After the two-phase dep install landed, ruff was pointed at
`/workspace/` (the entire volume root). This included `/workspace/.deps/`
where pip had installed third-party packages. Many of those packages contain
Python that ruff can't parse under its default settings, producing errors like:
```
error: Failed to parse src/req_003_impl.py:2:1: Got unexpected token
.deps/anyio/pytest_plugin.py:109:17: E721 ...
```
The first error was actually a parse failure inside `.deps/`, not in generated
code at all. This made ruff always fail regardless of generated code quality.

**Solution:** Changed `run_ruff()` to target `src/` and `tests/` explicitly
instead of the workspace root:
```python
cmd = ["ruff", "check", "/workspace/src/", "/workspace/tests/"]
```
All other tools (`bandit`, `mypy`, `xenon`) already targeted `/workspace/src/`
specifically and were unaffected.

**Files changed:**

| File | Change |
|---|---|
| `src/tools/runners.py` | `run_ruff()` targets `src/` and `tests/` instead of workspace root. |

---

### 8. Silent / truncated tool failure output (`src/tools/runners.py`)

**Problem:** All tool failures were logged as a single line truncated to 120
chars (`f"FAIL -- {findings[:120]}"`). This made it impossible to diagnose
failures from the terminal — the actual ruff violations, pytest tracebacks,
and bandit findings were invisible. Additionally:
- `bandit` was run with `-q` (quiet), suppressing its findings report entirely.
- `pytest` was run with `-q` (quiet/minimal), hiding individual test names and
  tracebacks.

**Solution:** Added a shared `_log_result()` helper that prints a one-line
`PASS` or a full untruncated multi-line `FAIL` block:
```python
def _log_result(tool, passed, findings, extra=""):
    if passed:
        logger.info("[%s] PASS%s", tool, ...)
    else:
        logger.info("%s\n%s", label, findings)  # complete output
```
All six runner functions now call `_log_result()` instead of individual
`logger.info(...)` calls. Also:
- Removed `-q` from `bandit` to restore findings output.
- Switched `pytest` from `-q` to `-v` for full test name and traceback output.

**Files changed:**

| File | Change |
|---|---|
| `src/tools/runners.py` | Added `_log_result()` helper. All runner functions use it. `bandit` `-q` removed. `pytest` switched to `-v`. |

---

```bash
# Normal fresh run
python main.py --goal config/goals/example_goal.yaml --mode local

# Resume after crash or fix
python main.py --goal config/goals/example_goal.yaml --mode local --resume
```

---

### 9. Stale files from previous runs persisting into loops (`src/agents/nodes.py`)

**Problem:** When a run with 4 requirements looped back to `dev_node` for a
3-requirement goal (or vice versa), orphaned files such as `req_004_impl.py`
and `test_req_004.py` remained on disk. ruff and pytest discovered and ran them,
causing spurious failures unrelated to the current generation cycle.

**Solution:** Both `dev_node` and `qa_node` now glob-delete their respective
output files before generating new ones:

```python
# dev_node — before writing impl files
src_dir = VOLUMES_DIR / state.run_id / "src"
if src_dir.exists():
    for old_file in src_dir.glob("req_*_impl.py"):
        old_file.unlink(missing_ok=True)

# qa_node — before writing test files
tests_dir = VOLUMES_DIR / state.run_id / "tests"
if tests_dir.exists():
    for old_file in tests_dir.glob("test_req_*.py"):
        old_file.unlink(missing_ok=True)
```

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | Glob-delete `req_*_impl.py` at the top of `dev_node`. Glob-delete `test_req_*.py` at the top of `qa_node`. |

---

### 10. Failure context hidden from LLM — `_diagnosis_context` passed summaries, not findings (`src/agents/nodes.py`)

**Problem:** The old `_diagnosis_context()` helper passed only the 72-character
LLM diagnostic summary to subsequent nodes. The raw tool output (ruff line
numbers, pytest tracebacks, bandit locations) was never included in the prompt.
The LLM therefore received vague summaries like *"linting issues found"* with
no actionable detail, leading to identical regenerated files and infinite
retry loops.

**Solution:** Replaced `_diagnosis_context` with a new generic helper
`gate_failure_context(gate_evidence, roles=None)` that:

1. Filters `state.gate_evidence` to entries where `passed=False`, optionally
   restricted to a set of `roles` (e.g. `{"linter"}`, `{"test"}`).
2. Returns a formatted block containing the **full raw `findings` output** for
   every failing tool, plus the LLM diagnostic summary when available.

```python
def gate_failure_context(gate_evidence, roles=None):
    failed = [
        e for e in gate_evidence
        if not e.passed and (roles is None or e.role in roles)
    ]
    if not failed:
        return ""
    parts = []
    for e in failed:
        header = f"=== {e.tool} ({e.role}) FAILED ==="
        body = e.findings or "(no output captured)"
        diag = f"Diagnosis: {e.diagnosis}" if e.diagnosis else ""
        parts.append("\n".join(filter(None, [header, body, diag])))
    return "Previous gate failures — fix these issues:\n\n" + "\n\n".join(parts)
```

**`dev_node`** calls `gate_failure_context(state.gate_evidence, roles={"linter"})`
and prepends the result to its user-content prompt so the LLM sees exact ruff
line numbers and messages.

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | Added `gate_failure_context()`. Removed `_diagnosis_context()`. `dev_node` calls it with `roles={"linter"}`. |

---

### 11. `qa_node` oblivious to pytest failures (`src/agents/nodes.py`)

**Problem:** `qa_node` regenerated test files without any knowledge of what
pytest had actually reported on the previous loop. AttributeError tracebacks,
import failures, and assertion mismatches were invisible to the LLM, so it
kept producing the same broken tests.

**Solution:** `qa_node` now calls
`gate_failure_context(state.gate_evidence, roles={"test"})` and appends the
result to every per-requirement `user_content` prompt:

```python
test_ctx = gate_failure_context(state.gate_evidence, roles={"test"})
...
user_content = (
    f"Requirement ID: {req.id}\n..."
    + (f"\n\n{test_ctx}" if test_ctx else "")
)
```

On loop 1 `test_ctx` is empty (no prior evidence). On loop 2+ the LLM receives
the full pytest traceback and can fix the specific failure.

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | `qa_node` computes `test_ctx` via `gate_failure_context(roles={"test"})` and appends it to every requirement's user-content message. |

---

### 12. Test-only dependencies missing from `requirements.txt` (`src/agents/nodes.py`)

**Problem:** The LLM-inferred `requirements.txt` was generated from `src/`
impl files only. Test files import additional packages (`httpx`, `requests`,
`pytest-mock`, etc.) that are not imported in the application code. These were
never installed, causing `ModuleNotFoundError` on the first `pytest` run.

`httpx` is a particularly common omission because it is an optional dependency
of `starlette.testclient` — the LLM rarely includes it unprompted.

**Solution:**

1. `dev_node` now concatenates **both** `state.changes` (impl files) **and**
   `state.tests_written` (test files from the previous qa cycle) when building
   the code snippet sent to the LLM for requirements inference:

   ```python
   all_files: list[FileChange] = list(changes) + list(state.tests_written)
   ```

2. The requirements-generation system prompt was updated to say:
   *"Include ALL third-party packages needed at both runtime AND test time."*

3. `httpx` is unconditionally appended to the `pip install` command in
   `install_deps()` as a safety net for the first loop (when `tests_written`
   is still empty):
   ```
   pip install -r /workspace/requirements.txt httpx --target /workspace/.deps
   ```

**Limitation:** On a fully fresh run `state.tests_written` is still empty
when `dev_node` runs, so test-only deps appear from loop 2 onward. The `httpx`
hard-pin covers the most common first-loop miss.

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | `all_files` combines `changes` + `state.tests_written`. System prompt updated to include test-time deps. |
| `src/sandbox/manager.py` | `install_deps()` always appends `httpx` to the `pip install` command. |

---

### 13. Failure protocol coupled to tool names — role-based `ToolEvidence` (`src/state/schema.py`, `src/tools/runners.py`)

**Problem:** `gate_failure_context` (and its predecessor `_diagnosis_context`)
identified failing tools by matching exact tool names (`"pytest"`, `"ruff"`).
Adding a new linter or switching pytest for a different test runner would
require hunting down every name-check across nodes.

**Solution:** Added a `role` field to `ToolEvidence`:

```python
class ToolEvidence(BaseModel):
    ...
    role: str = ""   # linter | test | security | audit | complexity
```

Every runner sets the appropriate role on its returned `ToolEvidence`:

| Runner | `role` |
|---|---|
| `run_ruff`, `run_mypy` | `linter` |
| `run_pytest` | `test` |
| `run_bandit` | `security` |
| `run_pip_audit` | `audit` |
| `run_complexity_check` | `complexity` |

`gate_failure_context()` filters by `e.role in roles`, never by `e.tool`. The
failure feedback loop is now fully language-agnostic and tool-name-agnostic —
replacing ruff with flake8 or pytest with unittest requires only updating the
runner's `role=` value.

**Files changed:**

| File | Change |
|---|---|
| `src/state/schema.py` | `ToolEvidence` gains `role: str = ""` field. |
| `src/tools/runners.py` | All six `run_*` functions set `role=` on the returned `ToolEvidence`. |
| `src/agents/nodes.py` | `gate_failure_context()` filters by `e.role`, not `e.tool`. |

---

### 14. LLM wraps generated code in markdown fences — E999 SyntaxError (`src/agents/nodes.py`)

**Problem:** Both `dev_node` and `qa_node` called `_llm.chat(...).strip()` and
wrote the result directly to disk. The LLM frequently wraps its response in a
markdown code fence:

````
```python
# Requirement: REQ-001
...
```
````

The backtick characters are not valid Python. ruff reported
`E999 SyntaxError: Got unexpected token \`` on every loop 1, no matter how
many times `dev` regenerated the file, because the stripping happened nowhere.

**Solution:** Immediately after the LLM call in both nodes, strip any fenced
block before writing to disk:

```python
code = re.sub(r"^```[^\n]*\n", "", code)
code = re.sub(r"\n```$", "", code).strip()
```

This is deterministic — no LLM prompt change needed — and handles both
` ```python ` and bare ` ``` ` variants.

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | Markdown fence strip applied to `code` in `dev_node` after LLM call. |
| `src/agents/nodes.py` | Markdown fence strip applied to `code` in `qa_node` after LLM call. |

---

### 15. `qa_node` did not see linter failures in test files (`src/agents/nodes.py`)

**Problem:** `qa_node` called
`gate_failure_context(state.gate_evidence, roles={"test"})`. ruff runs against
`tests/` as well as `src/`, so F841 (`local variable assigned but never used`)
and F401 (`imported but unused`) violations in test files landed in the
`linter` role evidence, not `test`. `qa_node` never saw them and regenerated
identical broken test files on every loop.

**Solution:** Expand `qa_node`'s failure context to include both roles:

```python
test_ctx = gate_failure_context(state.gate_evidence, roles={"test", "linter"})
```

On a loop, the LLM now receives the exact ruff line/column violations in test
files alongside any pytest tracebacks.

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | `qa_node` computes `test_ctx` with `roles={"test", "linter"}`. |

---

### 16. Self-healing loop broken — missing deps never detected at runtime (`src/agents/nodes.py`)

**Problem:** The `requirements.txt` generation LLM call consistently omitted
test-only transitive deps (most commonly `httpx`, required by
`starlette.testclient`). Every review cycle produced the same
`RuntimeError: The starlette.testclient module requires the httpx package`
error. The orchestrator detected this as a *pytest* failure and routed back to
`dev_node`, which regenerated functionally identical code — never touching the
missing dep. The loop repeated indefinitely without converging.

**Root cause of the deeper issue:** There was no mechanism that read the gate
output, identified `ModuleNotFoundError: No module named 'X'`, and acted on it.

**Solution:** Added `_extract_missing_modules(evidence)` and a dep-fix
short-circuit at the top of the routing logic in `review_node`:

```python
def _extract_missing_modules(evidence: list[ToolEvidence]) -> set[str]:
    pattern = re.compile(r"No module named '([\w]+)'")
    missing: set[str] = set()
    for ev in evidence:
        if ev.findings:
            for match in pattern.finditer(ev.findings):
                missing.add(match.group(1))
    return missing
```

After collecting all gate evidence, before routing:

1. Call `_extract_missing_modules(evidence)`.
2. If any missing modules are found, append the new ones to `requirements.txt`
   (only packages not already listed, checked case-insensitively).
3. Write `.deps-stale` marker and call
   `_runners._deps_installed.discard(run_id)` to force reinstall.
4. Set `state.current_phase = "review"` and return early.
5. `loop_count` is **not** incremented — this is a dep problem, not a code
   quality problem.

This is fully language/package agnostic: no hardcoded package names. Any
`ModuleNotFoundError` from any tool (pytest, mypy, etc.) is caught and
resolved automatically.

**Files changed:**

| File | Change |
|---|---|
| `src/agents/nodes.py` | Added `_extract_missing_modules()`. `review_node` calls it after gate collection; patches `requirements.txt`, marks stale, and returns with `current_phase="review"` if missing deps found. |

---

### 17. No routing path for dep-fix loop — missing `"review" → "review"` edge (`src/agents/graph.py`)

**Problem:** `_route_after_review()` only had two return values: `"dev"` and
`"release_engineer"`. Even after fix §16 set `current_phase="review"`, the
router fell through to `"release_engineer"` (or `"dev"`) because `"review"`
was not a recognised case. The LangGraph conditional edges map only registered
`{"dev": "dev", "release_engineer": "release_engineer"}`, so a `"review"`
return value would have caused a KeyError.

**Solution:** Added the `"review"` case to the router and the edges map:

```python
def _route_after_review(state: SDLCState) -> str:
    if state.current_phase == "review":          # dep-fix loop
        return "review"
    if state.current_phase == "implementation" and state.loop_count < 3:
        return "dev"
    return "release_engineer"
```

```python
graph.add_conditional_edges(
    "review",
    lambda d: _route_after_review(SDLCState.model_validate(d)),
    {"review": "review", "dev": "dev", "release_engineer": "release_engineer"},
)
```

The dep-fix loop now completes cleanly: `review → (reinstall) → review → gates
pass → release_engineer`.

**Files changed:**

| File | Change |
|---|---|
| `src/agents/graph.py` | `_route_after_review()` handles `"review"` phase. Conditional edges map extended with `"review": "review"`. |

---

## Files Changed Summary

| File | Reason |
|---|---|
| `docker/Dockerfile.python-runner` | QA-tools-only; comment explains the two-phase dep install pattern |
| `src/sandbox/manager.py` | `install_deps()` method (network-enabled container, `--target .deps`, always includes `httpx`); `env` param on `exec_in_sandbox()` |
| `src/tools/sandboxed_runner.py` | Threaded `env` param through to `exec_in_sandbox()` |
| `src/state/schema.py` | `ToolEvidence.role` field added |
| `src/tools/runners.py` | `_ensure_deps_installed()` + `PYTHONPATH` injection; `_log_result()` helper; ruff targets `src/`+`tests/`; bandit `-q` removed; pytest `-q`→`-v`; all runners set `role=` |
| `src/agents/nodes.py` | `dev_node`: import-order rules + requirements.txt from src+tests + `.deps` cache invalidation + stale-file cleanup + `gate_failure_context(roles={"linter"})` + markdown fence strip; `qa_node`: FastAPI TestClient + brace-escaping + stale-file cleanup + `gate_failure_context(roles={"test","linter"})` + markdown fence strip; `gate_failure_context()` replaces `_diagnosis_context()`; `_extract_missing_modules()` + dep-fix short-circuit in `review_node` |
| `src/agents/graph.py` | Dispatcher node; per-node `save_state`; phase-based routing; `"review"→"review"` dep-fix edge |
| `main.py` | `--resume` flag; `load_state` integration; `.deps` cleanup on fresh run |
