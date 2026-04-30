# Lean Omega — Incremental SDLC Orchestrator, Language-Agnostic Edition

**Document name:** `AI-REVIEWED-FULL-PLAN-Agnostic.md`
**Version:** v5
**Status:** Stages 1–4 are completed and verified. Stages 5–9 are the forward plan.
**Primary architectural change:** keep the orchestrator Python-based, but make generated projects language-aware through typed contracts, target-stack resolution, test-first planning, deterministic evaluator gates, and Docker sandboxing before any multi-runtime execution.

---

## TL;DR

The final destination is an incremental SDLC orchestrator with:

- LangGraph-style agent topology
- deterministic review gates
- Docker-isolated tool execution
- typed implementation contracts
- language-aware generated projects
- stack-specific validation
- memory-assisted learning
- Temporal durability
- production-grade auditability

The key sequencing decision is deliberate:

1. **Stages 1–4 are preserved as completed Python-only local stages.**
2. **Stage 5 is dedicated entirely to Docker sandboxing.**
3. **Stage 6 introduces the typed contract workflow and language-agnostic target stack.**
4. **Memory, Temporal, and production hardening shift to Stages 7–9.**

This avoids the critical security mistake of running `npm`, `go test`, `tsc`, or other third-party toolchains directly on the host.

---

## Core Principle

The system must evolve without breaking verified stages.

Every stage must produce a working, testable system. No stage should require tearing up the stage before it. Interfaces must stabilise early and implementations must swap behind those interfaces.

---

## Current Verified Baseline

Stages 1–4 are built and verified. They are Python-only and local. They must remain compatible with all existing behaviour and tests.

**Non-regression target: 94 tests must remain green through all future stages.**

The current verified graph is:

```text
tech_lead → dev → qa → review → release_engineer → supervisor
```

The new future graph becomes active only after Stage 6:

```text
tech_lead → stack_resolver → implementation_planner → qa → dev → review → release_engineer → supervisor
```

On failed required gates after Stage 6:

```text
review → implementation_planner → qa → dev → review
```

### Routing Mechanism — Preserved Invariant

`review_node` sets `state.current_phase` authoritatively on pass or failure. The graph router (`_route_after_review`) reads `state.current_phase` — it does **not** re-scan `gate_evidence`. This prevents optional-tool failures (e.g. mypy) from incorrectly triggering a dev loop. This mechanism must be preserved through the Stage 6 graph topology change.

### Escalation Conditions — Preserved Invariant

`supervisor_node` escalates to `human_review` on either:

- `loop_count >= 3` (loop cap reached), **or**
- `risk_level == "critical"` (set by `tech_lead_node` on keyword detection)

Both conditions must survive the Stage 6 refactor.

---

## Existing Verified Interfaces

These signatures are frozen. Stage 5+ changes must be additive or routed behind these interfaces, not restructured.

```python
# src/io/workspace.py
write_file(run_id: str, path: str, content: str, requirement_id: str, rationale: str) -> FileChange
read_file(run_id: str, path: str) -> str

# src/state/schema.py
class FileChange(BaseModel):
    path: str
    requirement_id: str
    rationale: str
    hash: str

class ToolEvidence(BaseModel):
    tool: str
    passed: bool
    required: bool
    findings: list[str]
    diagnosis: Optional[str] = None
```

`VOLUMES_DIR` is a module-level constant exposed in both `src/io/workspace.py` and `src/tools/runners.py`. It is monkeypatched in tests. Any sandbox refactor must preserve this pattern.

---

## Updated Project Structure

```text
src/
  core/
    goal.py                   ← OmegaGoal schema and YAML loader
    llm.py                    ← BaseLLM protocol, LiteLLMBackend, StubLLM, load_llm()
    config.py                 ← config loading helpers

  agents/
    graph.py                  ← graph topology and routing (_route_after_review)
    nodes.py                  ← agent node implementations, _REQUIRED_TOOLS, StubLLM dispatching
    diagnostic.py             ← DiagnosticUtility
    stack_resolver.py         ← Stage 6: target-stack inference and normalisation
    implementation_planner.py ← Stage 6: typed implementation contract generation

  state/
    schema.py                 ← SDLCState, ToolEvidence, FileChange, TargetStack, ImplementationTarget
    persistence.py            ← save_state / load_state

  workflows/
    omega_workflow.py         ← Temporal workflow and activities (Stage 8 only)

  memory/
    local_store.py            ← lessons_learned.json backend (Stage 7)
    zep_store.py              ← Zep / Graphiti backend (Stage 7+)
    memory_manager.py         ← unified memory interface

  tools/
    runners.py                ← existing ToolEvidence-returning wrappers (Stages 3–4)
    python_runners.py         ← ruff, pytest, mypy, bandit, pip-audit, radon/xenon (Stage 5+)
    node_runners.py           ← npm test, npm lint, tsc, npm audit (Stage 6)
    go_runners.py             ← go test, gofmt check, go vet (Stage 6)
    dispatcher.py             ← language-specific review gate selection (Stage 6)
    sandboxed_runner.py       ← Stage 5 Docker-backed execution wrapper

  sandbox/
    manager.py                ← SandboxManager, container lifecycle, isolated exec

  io/
    workspace.py              ← write_file, read_file, VOLUMES_DIR, hash tracking
    paths.py                  ← run and volume path helpers

config/
  llm.yaml                    ← main + diagnostic sections
  temporal.yaml               ← Stage 8
  memory.yaml                 ← Stage 7
  goals/
    example_goal.yaml
    implement_auth.yaml
    example_node_goal.yaml    ← Stage 6+
    example_go_goal.yaml      ← Stage 6+

tests/
  conftest.py                 ← stub_load_llm autouse fixture (must be extended for Stage 6 nodes)
  test_stage1.py
  test_stage2.py
  test_stage3.py
  test_stage4.py
  test_stage5.py              ← Stage 5
  test_stage6.py              ← Stage 6

volumes/
  {run_id}/                   ← generated project workspace

runs/
  {run_id}/state.json         ← persisted run state snapshots
  lessons_learned.json        ← local memory backend (Stage 7 fallback)

main.py                       ← CLI entrypoint: --goal, --mode local|temporal, --no-sandbox
```

---

# Stage 1 — Local SDLC Loop Skeleton

**Status:** Completed / Verified
**Test count:** 10 / 10 passed

## What Was Built

- `OmegaGoal` Pydantic schema and YAML loader (`src/core/goal.py`)
- `SDLCState` full Pydantic schema (`src/state/schema.py`) — all fields defined at this stage, populated incrementally
  - `SDLCState.from_goal(goal)` factory method
  - includes `FileChange`, `ToolEvidence`, `QualityThresholds`, `SDLCRequirement`
- `src/state/persistence.py` — `save_state(state)` and `load_state(run_id)` to `runs/{run_id}/state.json`
- six stubbed LangGraph node functions in `src/agents/nodes.py`
  - each logs its phase, appends a fake `SDLCRequirement` or `ToolEvidence`, advances `current_phase`
  - `_REQUIRED_TOOLS` set at module level
- `StateGraph` in `src/agents/graph.py` with fixed edges:

```text
tech_lead → dev → qa → review → release_engineer → supervisor → END
```

- `_route_after_review` routes on `state.current_phase` (not raw evidence re-scan)
- conditional escalation in `supervisor_node`: `loop_count >= 3` or `risk_level == "critical"`
- state persistence after every node transition
- `volumes/{run_id}/` provisioned on run start
- `config/goals/example_goal.yaml` and `config/goals/implement_auth.yaml`
- CLI: `python main.py --goal config/goals/example_goal.yaml --mode local`

## Success Criteria

- valid YAML loads and validates as `OmegaGoal`
- graph runs `planning → done` without errors
- `runs/{run_id}/state.json` exists and contains populated state
- mock run completes with `current_phase == "done"`

## Known Limitations

- all agent behaviour is hardcoded stubs
- no real files written
- no LLM calls

---

# Stage 2 — Real Local Workspace and File Tracking

**Status:** Completed / Verified
**Test count:** 22 / 22 passed (10 prior + 12 new)

## What Was Built

- `src/io/workspace.py`
  - `VOLUMES_DIR = Path("volumes")` — module-level, monkeypatchable
  - `write_file(run_id, path, content, requirement_id, rationale) -> FileChange`
  - `read_file(run_id, path) -> str`
  - creates parent dirs, computes `sha256` hash of UTF-8 content
- `FileChange` Pydantic model: `path`, `requirement_id`, `rationale`, `hash`
- `SDLCState.files_changed` and `tests_written` updated to `list[FileChange]`
- `dev_node` writes one real `.py` source file per requirement to `volumes/{run_id}/src/`
- `qa_node` writes one real `test_*.py` per requirement to `volumes/{run_id}/tests/`
- `state.json` stores paths and hashes only — no raw code

## Success Criteria

- `volumes/{run_id}/` contains real files after the run
- `state.json` has populated `files_changed` and `tests_written` with `FileChange` objects
- `state.json` remains small — no `content` key, no code blobs

## Known Limitations

- file content is still hardcoded stubs
- tools do not run yet
- generated files are Python-only

---

# Stage 3 — Deterministic Review Gates, Local Subprocess

**Status:** Completed / Verified
**Test count:** 49 / 49 passed (22 prior + 27 new)

## What Was Built

- `src/tools/__init__.py` — re-exports all six runner functions
- `src/tools/runners.py`
  - `VOLUMES_DIR` module-level constant, monkeypatchable
  - `run_ruff(run_id) -> ToolEvidence` — required
  - `run_pytest(run_id, min_coverage) -> ToolEvidence` — required; `min_coverage` forwarded from `QualityThresholds`
  - `run_mypy(run_id, enforce: bool) -> ToolEvidence` — skipped when `enforce=False`
  - `run_bandit(run_id) -> ToolEvidence` — optional
  - `run_pip_audit(run_id) -> ToolEvidence` — optional; skipped when no `requirements.txt`
  - `run_complexity_check(run_id, max_complexity) -> ToolEvidence` — **stub only; always passes; real radon/xenon deferred to Stage 5**
- all wrappers use `subprocess.run`, parse output into `ToolEvidence.findings`, derive pass/fail from return code
- `review_node` calls all enabled tools, sets `state.current_phase` authoritatively, populates `gate_evidence`
- `release_engineer_node` blocks `done` if required gate still failing (safety net — does **not** increment `loop_count`)
- `_route_after_review` reads `state.current_phase`, not raw evidence

## Design Decisions

| Decision | Rationale |
|---|---|
| `_route_after_review` routes on `current_phase` | Centralises required-vs-optional gate logic in `review_node`; prevents optional failures from incorrectly triggering dev loop |
| `release_engineer_node` does not increment `loop_count` | RE is a safety net; `review_node` already incremented for the cycle; double-increment breaks the loop-cap-equals-3 invariant |
| `qa_node` stub imports and calls source module | Achieves real coverage on stub files without changing YAML thresholds |

## Success Criteria

- `review_node` produces real `ToolEvidence` from actual tool output
- intentionally broken code fails at review and routes back to `dev_node`
- `loop_count` increments on each failed required-gate cycle
- valid stub code passes required gates and reaches `done`

## Known Limitations

- tools run on host via subprocess — Docker isolation in Stage 5
- generated code execution is not sandboxed
- no LLM diagnosis of failures
- `run_complexity_check` is a stub; real radon/xenon in Stage 5

---

# Stage 4 — Real LLM Agents and Diagnostic Utility

**Status:** Completed / Verified
**Test count:** 94 / 94 passed (49 prior + 45 new)

## What Was Built

- `src/core/llm.py`
  - `BaseLLM` protocol
  - `LiteLLMBackend` — synchronous `litellm.completion()` (streaming deferred to Stage 8)
  - `StubLLM` — deterministic fallback when `DEEPSEEK_API_KEY` is absent; inspects system prompt to identify caller and returns structurally valid output per node
  - `load_llm(section)` factory — returns `StubLLM` when key is absent
- `config/llm.yaml` — `main` and `diagnostic` sections, both targeting `deepseek/deepseek-chat`
- `tests/conftest.py` — `stub_load_llm` autouse fixture monkeypatches `load_llm` in `src.agents.nodes` for all tests

### Real Node Responsibilities

**`tech_lead_node`**
- generates `list[SDLCRequirement]` with `id`, `description`, `acceptance_criteria`
- generates `architecture_doc` markdown
- sets `risk_level`: keywords (`security`, `auth`, `payment`, `critical`, `production`) or requirement count > 5 → `medium`; falls back to stub `REQ-001` on unparseable JSON
- reads `lessons_learned` from state and injects into prompt

**`dev_node`**
- per-requirement prompt includes requirement text, acceptance criteria, architecture doc, and prior `ToolEvidence.diagnosis` values
- enforces `# Requirement: {id}` as first line (prepends if LLM omits it)
- writes via `write_file` — interface unchanged from Stage 2

**`qa_node`**
- per-requirement prompt includes acceptance criteria and implementation module name
- enforces `# Requirement: {id}` first line
- uses `sys.path.insert` shim so generated tests can locate source modules

**`review_node`**
- all six subprocess tool calls unchanged from Stage 3
- calls `DiagnosticUtility.diagnose(ev)` on each failing `ToolEvidence` entry
- exceptions caught with fallback message — graph never crashes

**`release_engineer_node`**
- generates `release_notes` from run ID, objective, requirements, files changed, gate results
- fallback to formatted plaintext summary on LLM failure

**`supervisor_node`**
- generates `supervisor_notes` (≤100 words) stored in `SDLCState.supervisor_notes`
- escalation logic unchanged from Stage 3

### Diagnostic Utility

`src/agents/diagnostic.py`

```python
class DiagnosticUtility:
    def diagnose(self, tool_evidence: ToolEvidence) -> str:
        ...
```

Called only on failing evidence. Review node uses `load_llm("diagnostic")` — allows a lighter/cheaper model via config without touching code.

Diagnosis shape:

```text
failing_tool:
failing_file_or_symbol:
root_cause:
minimal_repair_instruction:
supporting_finding:
```

### StubLLM Caller Map

| Caller | Output |
|---|---|
| `tech_lead_node` | Valid JSON with one REQ-001 requirement and arch doc |
| `dev_node` | Python source with `# Requirement: {id}` tag |
| `qa_node` | pytest file with `sys.path` setup and `def test_` |
| `release_engineer_node` | Short markdown release notes |
| `supervisor_node` | One-line summary string |
| `DiagnosticUtility` | Generic fix-it instruction |

## Success Criteria

- `tech_lead_node` generates real `SDLCRequirement` objects
- `dev_node` writes real code files with requirement tags
- `qa_node` writes real pytest files
- `review_node` diagnosis is readable and actionable
- failed review loops provide diagnosis to the next generation pass
- after three failed review loops: `current_phase == "human_review"`

## Known Limitations

- tools still run on host via subprocess
- generated project remains Python-only
- no memory layer
- no Temporal
- no Docker sandbox yet

---

# Stage 5 — Docker Sandbox

**Status:** Forward Plan
**Priority:** Security prerequisite
**Critical rule:** Stage 5 must land before Stage 6.

## Goal

Eliminate host remote-code-execution risk before introducing multi-language support.

Stage 6 will add Node/TypeScript and Go support. Running the following on the host is not acceptable:

```bash
npm install && npm test && npm run lint
npx tsc --noEmit && npm audit
go test ./... && go vet ./...
```

Docker sandboxing must come first.

## Build

### `src/sandbox/manager.py`

```python
class SandboxManager:
    def create_sandbox(self, run_id: str, target_stack: "TargetStack | None" = None) -> ContainerRef:
        ...

    def exec_in_sandbox(self, container_ref: ContainerRef, cmd: list[str], timeout_seconds: int) -> tuple[int, str]:
        ...

    def destroy_sandbox(self, container_ref: ContainerRef) -> None:
        ...
```

### Sandbox Requirements

Containers must be:

- ephemeral — destroyed after tool execution
- network-disabled by default
- mounted only to the run workspace (`volumes/{run_id}/` → `/workspace`)
- mount mode: read-write to workspace only (required for tool-generated cache and coverage reports inside the container; no other host paths mounted)
- isolated from the host filesystem
- resource-constrained where possible
- timeout-protected

### Tool Runner Refactor

Update all existing Stage 3/4 tool wrappers to route through the sandbox by default.

Before:

```python
subprocess.run(...)
```

After:

```python
sandbox.exec_in_sandbox(container_ref, cmd, timeout_seconds)
```

`ToolEvidence` interface is unchanged. `VOLUMES_DIR` module-level constant is preserved in `src/tools/runners.py` for test monkeypatching.

### `run_complexity_check` — Real Implementation

Stage 5 is where `run_complexity_check` becomes real. Replace the stub with radon/xenon:

```bash
xenon --max-absolute B --max-modules B --max-average A volumes/{run_id}/src/
```

Pass/fail derived from exit code. `ToolEvidence` shape is unchanged.

### Local Development Escape Hatch

```bash
python main.py --goal config/goals/example_goal.yaml --mode local --no-sandbox
```

Default behaviour after Stage 5: sandbox enabled.

### Docker Images

Stage 5 bootstrap image (`omega-python-runner`):

- Python 3.10+
- pytest + pytest-cov
- ruff
- mypy
- bandit
- pip-audit
- radon / xenon (for real complexity check)

Stage 6 will add `omega-node-runner` and `omega-go-runner` (or a unified image).

## Success Criteria

- `ruff`, `pytest`, `mypy`, `bandit`, `pip-audit`, complexity check execute inside Docker
- `run_complexity_check` is no longer a stub
- containers are destroyed after each tool run
- host no longer executes generated code by default
- all 94 prior tests remain green
- `ToolEvidence` shape is unchanged
- `VOLUMES_DIR` monkeypatch pattern is preserved
- `--no-sandbox` works for trusted development only
- failed review routing behaviour is identical to Stage 3/4

## Known Limitations

- still Python-only
- no target stack resolution yet
- no typed implementation planner yet
- no memory
- no Temporal durability

---

# Stage 6 — Typed Contracts and Language-Agnostic Target Stack

**Status:** Forward Plan
**Priority:** Major architecture upgrade
**Depends on:** Stage 5

## Goal

Keep the orchestrator in Python, but allow generated projects to target Python, TypeScript, JavaScript, or Go.

Stage 6 combines two upgrades:

1. **Typed Contract + Evaluator Workflow**
2. **Language-Agnostic Target Stack**

The system moves from vague requirement-to-code generation into a stricter workflow:

```text
requirements → target stack → typed implementation contract → tests → implementation → deterministic gates
```

## New Graph Topology

```text
tech_lead
  → stack_resolver
  → implementation_planner
  → qa
  → dev
  → review
  → release_engineer
  → supervisor
  → END
```

Failed required gates route back to the planner (not to `dev` directly):

```text
review → implementation_planner → qa → dev → review
```

`_route_after_review` continues to read `state.current_phase` — the routing mechanism is preserved; only the target phase name changes from `"implementation"` to `"implementation_planning"`.

## Why `qa` Comes Before `dev`

`qa_node` uses the typed implementation contract to generate tests before the implementation exists. This constrains `dev_node` and prevents it from inventing APIs that tests do not verify.

---

## Stage 6 Schema Updates

All new fields are additive. Existing `SDLCState` fields are unchanged.

### `TargetStackInput`

Optional user input in `OmegaGoal`:

```python
class TargetStackInput(BaseModel):
    language: Optional[Literal["python", "typescript", "javascript", "go"]] = None
    runtime: Optional[str] = None
    package_manager: Optional[str] = None
```

### `TargetStack`

Normalised, resolved stack stored in `SDLCState`:

```python
class TargetStack(BaseModel):
    language: Literal["python", "typescript", "javascript", "go"]
    runtime: str
    package_manager: Optional[str] = None

    source_dir: str
    test_dir: str

    test_command: str
    lint_command: Optional[str] = None
    typecheck_command: Optional[str] = None
    security_command: Optional[str] = None

    required_tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)
```

### `ImplementationTarget`

Precise file and symbol-level contract generated by `implementation_planner_node`:

```python
class ImplementationTarget(BaseModel):
    requirement_id: str

    path: str
    test_path: str

    public_symbols: list[str]
    type_contract: list[str]
    test_cases: list[str]

    error_behavior: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
```

### `SDLCState` Additions

```python
class SDLCState(BaseModel):
    # ... all existing fields unchanged ...

    target_stack: Optional[TargetStack] = None
    implementation_plan: Optional[str] = None
    implementation_targets: list[ImplementationTarget] = Field(default_factory=list)

    current_phase: Literal[
        "planning",
        "stack_resolution",
        "implementation_planning",
        "qa",
        "development",
        "review",
        "release",
        "supervision",
        "human_review",
        "done",
        "failed",
    ]
```

### `OmegaGoal` Addition

```python
class OmegaGoal(BaseModel):
    # ... all existing fields unchanged ...
    target_stack: Optional[TargetStackInput] = None
```

Example TypeScript goal:

```yaml
id: implement-auth-api
title: Implement Auth API
description: Build a small Express authentication API with request validation.
target_stack:
  language: "typescript"
  runtime: "node"
  package_manager: "npm"
```

Example Go goal:

```yaml
id: implement-health-service
title: Implement Health Service
description: Build a small HTTP health-check service using Go net/http.
target_stack:
  language: "go"
  runtime: "go"
```

---

## New Node: `stack_resolver_node`

### Purpose

Determine the generated project's target language, runtime, package manager, directories, and default verification commands.

### Resolution Rules

Explicit user input wins:

```text
goal.target_stack.language exists → use it
goal.target_stack.runtime exists → use it
goal.target_stack.package_manager exists → use it
```

Inference from goal text when explicit input is missing:

| Signals | Resolved Stack |
|---|---|
| FastAPI, Pydantic, pytest, Django, Flask | Python |
| Express, Node, npm, TypeScript, NestJS | TypeScript |
| plain Node without TypeScript | JavaScript |
| Golang, Go, Gin, net/http | Go |

Deterministic fallback — in code, not only in prompt:

```python
if resolver_confidence < threshold or not resolved_language:
    language = "python"
```

### Default Stack Values

Python:

```python
TargetStack(
    language="python", runtime="python", package_manager="pip",
    source_dir="src", test_dir="tests",
    test_command="pytest",
    lint_command="ruff check .",
    typecheck_command="mypy src",
    security_command="bandit -r src",
    required_tools=["ruff", "pytest"],
    optional_tools=["mypy", "bandit", "pip-audit"],
)
```

TypeScript:

```python
TargetStack(
    language="typescript", runtime="node", package_manager="npm",
    source_dir="src", test_dir="test",
    test_command="npm test",
    lint_command="npm run lint",
    typecheck_command="npx tsc --noEmit",
    security_command="npm audit",
    required_tools=["npm_test", "tsc"],
    optional_tools=["npm_lint", "npm_audit"],
)
```

JavaScript:

```python
TargetStack(
    language="javascript", runtime="node", package_manager="npm",
    source_dir="src", test_dir="test",
    test_command="npm test",
    lint_command="npm run lint",
    typecheck_command=None,
    security_command="npm audit",
    required_tools=["npm_test"],
    optional_tools=["npm_lint", "npm_audit"],
)
```

Go:

```python
TargetStack(
    language="go", runtime="go", package_manager=None,
    source_dir=".", test_dir=".",
    test_command="go test ./...",
    lint_command="gofmt -l .",
    typecheck_command="go vet ./...",
    security_command=None,
    required_tools=["go_test", "gofmt_check", "go_vet"],
    optional_tools=[],
)
```

---

## New Node: `implementation_planner_node`

### Purpose

Convert structured requirements into a typed implementation contract.

This node is the boundary between vague product intent and concrete code generation.

It decides: exact files, exact test files, public symbols, function signatures, return types, error behaviour, dependencies, test cases, verification commands.

It does **not** write implementation code.

### Inputs

- `SDLCRequirement` list
- `TargetStack`
- architecture notes
- prior `ToolEvidence.diagnosis` (repair loop context)
- previous failed files if any
- quality thresholds

### Output

- `implementation_plan` string
- `implementation_targets` list of `ImplementationTarget`

### Contract Requirements by Language

**Python:**

```python
ImplementationTarget(
    requirement_id="REQ-001",
    path="src/auth/token_service.py",
    test_path="tests/test_token_service.py",
    public_symbols=["create_token", "verify_token"],
    type_contract=[
        "create_token(user_id: str, ttl_seconds: int) -> str",
        "verify_token(token: str) -> dict[str, str]",
    ],
    test_cases=[
        "creates a token for a valid user id",
        "rejects expired tokens",
        "rejects malformed tokens",
    ],
)
```

**TypeScript:**

```python
ImplementationTarget(
    requirement_id="REQ-001",
    path="src/auth/tokenService.ts",
    test_path="test/tokenService.test.ts",
    public_symbols=["createToken", "verifyToken"],
    type_contract=[
        "createToken(userId: string, ttlSeconds: number): string",
        "verifyToken(token: string): Record<string, string>",
    ],
    test_cases=[
        "creates a token for a valid user id",
        "rejects expired tokens",
        "rejects malformed tokens",
    ],
)
```

**Go:**

```python
ImplementationTarget(
    requirement_id="REQ-001",
    path="auth/token_service.go",
    test_path="auth/token_service_test.go",
    public_symbols=["CreateToken", "VerifyToken"],
    type_contract=[
        "func CreateToken(userID string, ttlSeconds int) (string, error)",
        "func VerifyToken(token string) (map[string]string, error)",
    ],
    test_cases=[
        "creates a token for a valid user id",
        "returns an error for expired tokens",
        "returns an error for malformed tokens",
    ],
)
```

---

## Updated Node Responsibilities After Stage 6

### `tech_lead_node`

Unchanged goal. Produces: requirements, architecture notes, constraints, success criteria, risk notes. Does **not** decide file paths, function signatures, or test commands.

### `stack_resolver_node`

New. Produces a normalised `TargetStack`. Enforces deterministic Python fallback in code.

### `implementation_planner_node`

New. Produces typed `ImplementationTarget` list. Includes prior diagnostic feedback during retry loops. Output validated by Pydantic.

### `qa_node`

Reads `implementation_targets`. Generates language-appropriate tests **before** implementation exists.

| Target | Test Style |
|---|---|
| Python | pytest |
| TypeScript | Vitest or Jest |
| JavaScript | Vitest or Jest |
| Go | standard `testing` package |

### `dev_node`

Writes only files listed in `implementation_targets`. Preserves requirement tags. Does not invent unplanned public APIs.

| Target | Rules |
|---|---|
| Python | explicit type hints, no untyped public functions |
| TypeScript | strict types, no implicit `any`, exports match contract |
| JavaScript | idiomatic JS, JSDoc only if requested |
| Go | idiomatic package structure, explicit errors, gofmt-compatible |

### `review_node`

Dispatches deterministic gates by `target_stack.language`. Executes tools only inside Docker. Calls Diagnostic Utility on failures.

### `release_engineer_node`

Blocks release if required gates failed or requirements lack code/test coverage. Includes target stack in release notes.

### `supervisor_node`

Routes failed review to `implementation_planner` (not `dev`). Increments loop count. Escalates on loop cap or `risk_level == "critical"`.

---

## Language-Specific Review Gate Dispatch

`src/tools/dispatcher.py`

```python
class RunnerSpec(BaseModel):
    name: str
    required: bool
    command: list[str]
    timeout_seconds: int

def select_runners(target_stack: TargetStack, thresholds: QualityThresholds) -> list[RunnerSpec]:
    ...
```

This replaces the module-level `_REQUIRED_TOOLS` set in `nodes.py` for Stage 6+. The migration must be explicit: existing `_REQUIRED_TOOLS` logic moves into the Python dispatch path of `dispatcher.py`.

### Python

Required: `ruff`, `pytest` (+ `mypy` when `enforce_type_hints=True`)
Optional: `bandit`, `pip-audit`, `complexity`

### TypeScript

Required: `npm_test`, `tsc`
Optional: `npm_lint`, `npm_audit`

Generated project must include `package.json` and `tsconfig.json` with strict mode:

```json
{ "compilerOptions": { "strict": true } }
```

### JavaScript

Required: `npm_test`
Optional: `npm_lint`, `npm_audit`

### Go

Required: `go_test`, `gofmt_check`, `go_vet`

`gofmt` runs in check mode only. Review gate must not silently rewrite files during validation.

---

## `StubLLM` Extension for Stage 6

`StubLLM` must gain stub outputs for the two new nodes:

| Caller | Output |
|---|---|
| `stack_resolver_node` | Valid `TargetStack` JSON defaulting to Python |
| `implementation_planner_node` | Valid `ImplementationTarget` list with one entry per requirement |

`tests/conftest.py` `stub_load_llm` fixture must cover both new nodes.

---

## Stage 6 Test Plan

### Stack Resolver Tests

- FastAPI/Pydantic/Django/Flask → Python
- Express/TypeScript/NestJS → TypeScript
- plain Node (no TypeScript) → JavaScript
- Go/Golang/Gin/net/http → Go
- explicit `target_stack` overrides inference
- ambiguous goal defaults to Python deterministically

### Schema Tests

- `TargetStackInput` validates partial input
- `TargetStack` validates normalised values
- `ImplementationTarget` validates required field contracts
- `SDLCState` accepts new optional fields
- invalid language is rejected

### Graph Tests

- graph order: `tech_lead → stack_resolver → implementation_planner → qa → dev → review → release_engineer → supervisor`
- failed review routes to `implementation_planner → qa → dev`
- Python default path remains green
- loop cap still escalates to `human_review`

### Planner Tests

- Python plans `src/*.py` / `tests/test_*.py`
- TypeScript plans `src/*.ts` / `test/*.test.ts`
- JavaScript plans `src/*.js` / `test/*.test.js`
- Go plans `*.go` / `*_test.go`
- planner includes public symbols, type contracts, test cases
- planner includes prior diagnoses during repair loops

### Review Dispatch Tests

- Python runs Python gates
- TypeScript runs npm and tsc gates
- JavaScript runs npm gates
- Go runs go test, gofmt check, go vet
- all outputs become `ToolEvidence`
- failed required evidence blocks release
- failed optional evidence does not block release

### Security Tests

- Node commands run inside Docker
- Go commands run inside Docker
- Python commands run inside Docker
- host subprocess execution disabled by default
- `--no-sandbox` is explicit

## Success Criteria

- target stack resolved for every run
- explicit target stack overrides inference
- ambiguous goals default to Python
- planner produces valid `ImplementationTarget` records
- QA runs before dev
- dev implements only the typed contract
- review dispatches stack-specific gates
- no Node or Go toolchain executes on the host
- all 94 prior tests remain green
- new language-specific tests pass

## Known Limitations

- supported generated languages: Python, TypeScript, JavaScript, Go
- orchestrator remains Python
- package installation policy must be strict and audited
- Docker image management may become its own operational concern
- memory not active until Stage 7
- Temporal durability not active until Stage 8

---

# Stage 7 — Memory Layer

**Status:** Forward Plan
**Original Stage:** 6 (renumbered to accommodate Stage 6 typed contracts)
**Depends on:** Stage 6 (`TargetStack` required for stack-aware lesson filtering)

## Goal

Add cross-run learning so the system avoids repeating past mistakes, filtered by target stack.

A Python lesson must not automatically influence a Go run. A TypeScript package issue must not pollute Python planning.

## Build

### `src/memory/local_store.py`

```python
class LessonEntry(BaseModel):
    run_id: str
    goal_id: str
    language: Literal["python", "typescript", "javascript", "go", "agnostic"]
    runtime: Optional[str] = None
    package_manager: Optional[str] = None
    positive: list[str]
    negative: list[str]
    failed_tools: list[str] = Field(default_factory=list)
    successful_patterns: list[str] = Field(default_factory=list)
    created_at: str
```

Persists to `runs/lessons_learned.json`.

### Memory Interface

```python
class MemoryManager(Protocol):
    def remember_run_summary(self, state: SDLCState) -> None:
        ...

    def recall_past_decisions(
        self,
        query: str,
        target_stack: TargetStack,
        limit: int = 5,
    ) -> list[LessonEntry]:
        ...
```

### `src/memory/zep_store.py`

Same interface as JSON store. JSON store remains the fallback.

### `config/memory.yaml`

Backend selection config.

### Node Integration

| Node | Memory Use |
|---|---|
| `tech_lead_node` | Stack-agnostic lessons only |
| `implementation_planner_node` | Stack-specific: `language == current target_stack.language` or `language == "agnostic"` |
| `review_node` | Annotates repeated tool failures |
| `release_engineer_node` | Writes lesson entry after every completed or failed run |

## Success Criteria

- completed runs write lesson entries
- failed runs write useful negative lessons
- retrieval filters by language/runtime
- stack-agnostic lessons available across languages
- Python failures are not injected into Go or TypeScript prompts
- memory data appears in `SDLCState.lessons_learned`

## Known Limitations

- no Temporal durability yet
- lesson quality depends on summary quality
- Zep retrieval quality depends on embedding/index behaviour

---

# Stage 8 — Temporal Orchestration

**Status:** Forward Plan
**Original Stage:** 7 (renumbered)
**Depends on:** Stage 6 (stack-aware activities) and Stage 5 (Docker lifecycle as activity)

## Goal

Add crash safety, durable execution, and human-in-the-loop handling without changing agent logic.

Local mode must continue to work.

## Build

### `src/workflows/omega_workflow.py`

```python
@workflow.defn
class OmegaWorkflow:
    @workflow.run
    async def run(self, goal_id: str) -> SDLCState:
        ...
```

Each node runs as a Temporal activity:

```python
@activity.defn
async def tech_lead_activity(state: SDLCState) -> SDLCState: ...

@activity.defn
async def stack_resolver_activity(state: SDLCState) -> SDLCState: ...

@activity.defn
async def implementation_planner_activity(state: SDLCState) -> SDLCState: ...

@activity.defn
async def qa_activity(state: SDLCState) -> SDLCState: ...

@activity.defn
async def dev_activity(state: SDLCState) -> SDLCState: ...

@activity.defn
async def review_activity(state: SDLCState) -> SDLCState: ...
```

### Side Effects Become Activities

- file writes and reads
- Docker sandbox execution
- tool runs
- memory writes
- LLM calls
- release summary generation

### HITL Handling

`human_review` emits a Temporal signal and blocks:

```python
workflow.wait_condition(...)
```

Do not rely on LangGraph interrupt for durable human review.

### Retry Policies

| Activity Type | Retries |
|---|---:|
| LLM calls | 2 with backoff |
| Docker tool runs | 3 for infrastructure failures only |
| deterministic test failures | 0 logical retries |
| file I/O | 3 |
| memory write | 3 |
| Temporal signal wait | indefinite |

Critical distinction:

```text
tool infrastructure failure ≠ generated code test failure
```

A failed `pytest`, `npm test`, or `go test` routes through the evaluator loop. It must not be retried as if infrastructure failed.

### Workflow ID

```text
{goal_id}-{run_id}
```

### `config/temporal.yaml`

Worker address, namespace, and retry policy config.

### CLI

```bash
python main.py --goal config/goals/example_goal.yaml --mode local
python main.py --goal config/goals/example_goal.yaml --mode temporal
```

## Success Criteria

- same goal runs in both `--mode local` and `--mode temporal`
- Temporal mode supports Python, TypeScript, JavaScript, and Go projects
- killing a worker during a tool activity resumes from the last completed activity
- `human_review` blocks until a signal is sent
- Docker sandbox lifecycle is activity-managed
- agent logic is not rewritten for Temporal
- local mode works without Temporal, Redis, or Zep

## Known Limitations

- Redis not yet required
- Temporal event history is the durable checkpoint
- large artifacts remain in filesystem/object storage, not state history

---

# Stage 9 — Production Hardening

**Status:** Forward Plan
**Original Stage:** 8 (renumbered)
**Depends on:** Stages 5–8

## Goal

Make the system auditable, reproducible, secure, and operationally complete across all supported target languages.

## Build

### Structured Audit Logging

Every node transition logs: timestamp, run ID, goal ID, target language, phase, loop count, files changed, tests written, tool evidence summary, failed required gates, current decision.

### Requirement Traceability

```python
grep_requirement_tags(run_id, target_stack) -> ToolEvidence
```

Scans code and test files for language-specific requirement tags:

| Language | Tag format |
|---|---|
| Python | `# Requirement: REQ-001` |
| TypeScript / JavaScript | `// Requirement: REQ-001` |
| Go | `// Requirement: REQ-001` |

Release blocks if:
- any requirement lacks code coverage by tag
- any requirement lacks test coverage by tag
- any required gate failed
- any success criterion lacks supporting evidence

### Success Criteria Cross-Check

```python
class SuccessCriteriaEvidence(BaseModel):
    success_criterion: str
    evidence_tools: list[str]
    supporting_files: list[str]
    passed: bool
```

Every item in `OmegaGoal.success_criteria` must map to at least one passing `ToolEvidence` record or an explicit human-reviewed exception.

### Stack-Aware Reproducibility

Record: target language, runtime version, package manager version, Docker image digest, commands run, dependency files, lockfiles, file hashes, tool versions.

| Language | Captured files |
|---|---|
| Python | `requirements.txt`, `pyproject.toml`, `uv.lock` / `poetry.lock` |
| TypeScript / JavaScript | `package.json`, `package-lock.json`, `tsconfig.json` |
| Go | `go.mod`, `go.sum` |

### Error Records

```python
class ActivityErrorRecord(BaseModel):
    node: str
    activity: str
    language: Optional[str]
    error_type: str
    message: str
    retryable: bool
    timestamp: str
```

### CLI Commands

```bash
python main.py inspect    --run {run_id}
python main.py resume     --run {run_id}
python main.py clean      --run {run_id}
python main.py evidence   --run {run_id}
python main.py reproduce  --run {run_id}
```

### Optional Redis Layer

Introduced only if needed for fast lookup in larger deployments. Caches status and progress summaries only — not the source of truth. Durable state remains in Temporal history, filesystem, `state.json`, and audit logs.

## Success Criteria

- completed run fully auditable from `runs/{run_id}/state.json`
- failed run clearly explains: which tool, which requirement, which node, infrastructure vs generated-code failure
- traceability check blocks release if requirement tags are missing
- success criteria cross-check blocks unsupported claims
- stack-specific dependency files captured
- Docker image digests captured
- local and Temporal modes pass all prior stage tests
- generated project reproducible from recorded state and workspace artifacts

## Known Limitations

- full supply-chain security is broader than this stage
- package installation policy may need dedicated controls
- complex monorepo generation may require additional project templates

---

# Cross-Cutting Rules

These rules apply across all stages.

## 1. Never Store Generated Code in State

`SDLCState` stores file paths, hashes, requirement IDs, rationale, tool evidence, and summaries. It must not store raw generated source code.

## 2. Never Execute Generated Code on the Host After Stage 5

All generated code validation must happen inside Docker after Stage 5 unless the operator explicitly uses `--no-sandbox`.

## 3. Every Stage Must Have Verification Commands

```bash
pytest
python main.py --goal config/goals/example_goal.yaml --mode local
python main.py inspect --run {run_id}
```

## 4. Interfaces Must Remain Stable

These interfaces must not change casually:

- `BaseLLM`
- `ToolEvidence`
- `FileChange` (including full `write_file` signature: `run_id, path, content, requirement_id, rationale`)
- `TargetStack`
- `ImplementationTarget`
- `MemoryManager`
- `VOLUMES_DIR` monkeypatch pattern in `src/io/workspace.py` and `src/tools/runners.py`

## 5. Local Mode Must Always Work

```bash
python main.py --goal config/goals/example_goal.yaml --mode local
```

Must work without Temporal, Redis, or Zep. Docker is required by default after Stage 5 unless `--no-sandbox` is passed.

## 6. Review Gates Must Be Deterministic

LLMs generate code and diagnosis. They do not decide whether a gate passed. Gate pass/fail comes from exit codes, threshold checks, structured tool output, and explicit policy.

## 7. Required vs Optional Gates Must Be Clear

A failed required gate blocks release. A failed optional gate creates evidence and diagnosis but does not block release unless policy says otherwise.

## 8. Diagnostics Must Be Repair-Oriented

```text
failing_tool:
failing_file_or_symbol:
root_cause:
minimal_repair_instruction:
supporting_finding:
```

Do not dump raw logs into the next agent prompt without summarisation.

## 9. Python Is the Default, Not the Only Target

If target stack is ambiguous, default to Python in code. If the goal explicitly asks for Node, TypeScript, JavaScript, Go, or a named framework, the resolver must honour that.

## 10. Memory Must Be Stack-Aware

Lessons must be filtered by language, runtime, package manager, or stack-agnostic relevance. Do not inject Python-specific lessons into Go or TypeScript runs.

---

# Migration Notes from Current Verified System

## What Must Not Break

- all 94 existing passing tests
- current Python default workflow
- current `OmegaGoal` YAML files without `target_stack`
- current `ToolEvidence` consumers
- current `FileChange` behaviour and `write_file` signature
- current `runs/{run_id}/state.json` persistence
- current `volumes/{run_id}/` workspace model
- `_route_after_review` routing-on-phase mechanism
- `supervisor_node` dual escalation: loop cap **and** critical risk

## Compatibility Strategy

Add new fields as optional first:

```python
target_stack: Optional[TargetStack] = None
implementation_targets: list[ImplementationTarget] = Field(default_factory=list)
```

Default to Python when `target_stack is None`.

Introduce new test fixtures without replacing old ones:

```python
make_state()                         # existing
make_state_for_stack(language)       # new
```

Keep all Stage 1–4 tests validating the Python path. Add new tests for stack-specific behaviour.

Extend `StubLLM` and `conftest.py` stub fixture for the two new Stage 6 nodes before writing their tests.

---

# Recommended Implementation Order

## Step 1 — Stage 5: Docker Sandbox

- build `SandboxManager`
- implement real `run_complexity_check` (radon/xenon)
- route all existing Python tools through Docker
- preserve `ToolEvidence`, `VOLUMES_DIR` pattern, `--no-sandbox`
- verify all 94 tests remain green

## Step 2 — Stage 6: Add Schemas

- `TargetStackInput`, `TargetStack`, `ImplementationTarget`
- `SDLCState.target_stack`, `SDLCState.implementation_targets`
- `OmegaGoal.target_stack`

## Step 3 — Stage 6: `stack_resolver_node`

- explicit override, deterministic inference, Python fallback
- graph insertion

## Step 4 — Stage 6: `implementation_planner_node`

- typed contracts, file paths, public symbols, test cases
- prior diagnosis awareness in repair loops

## Step 5 — Stage 6: Move QA Before Dev

- QA generates tests from contract
- dev implements contract
- failed review loops back to planner
- `_route_after_review` updated to target `"implementation_planning"`

## Step 6 — Stage 6: Stack-Specific Tool Dispatch

- `dispatcher.py` replaces module-level `_REQUIRED_TOOLS`
- Python, Node/TypeScript, and Go runners
- Docker-only execution

## Step 7 — Stage 6: Test Coverage

- resolver, planner, graph, dispatch, Docker safety tests

## Step 8 — Stage 7: Memory Layer

- local lessons, stack-aware retrieval, optional Zep backend

## Step 9 — Stage 8: Temporal

- activities, durable HITL, retry policies

## Step 10 — Stage 9: Production Hardening

- audit logs, reproducibility metadata, traceability, success criteria evidence checks

---

# Final Architecture Summary

```text
 1. Load OmegaGoal
 2. Resolve target stack (Python fallback if ambiguous)
 3. Generate structured requirements (tech_lead)
 4. Produce typed implementation contract (implementation_planner)
 5. Generate tests from contract (qa — before dev)
 6. Generate implementation from contract (dev)
 7. Run stack-specific deterministic gates in Docker (review)
 8. Diagnose failures into repair instructions (diagnostic utility)
 9. Loop: implementation_planner → qa → dev → review (until gates pass or loop cap)
10. Produce release notes grounded only in evidence (release_engineer)
11. Write stack-aware lessons (memory — Stage 7)
12. Optionally run durably through Temporal (Stage 8)
13. Preserve a full audit trail (Stage 9)
```

The strongest architectural sequencing decision:

```text
Docker first (Stage 5), multi-language second (Stage 6).
```

This prevents expanding the attack surface before the containment layer is in place.
