A few quick questions before I lock this in: 

Asked 3 questions (Sandbox, Diag Model, Code Storage)

## Plan: Lean Omega — SDLC Multi-Agent Orchestrator (v3)

**TL;DR** — A production-grade SDLC orchestrator where **LangGraph** is the cognitive state machine (6 agent personas), **Temporal.io** is the durable execution engine owning all side effects and HITL waits, **Zep (Graphiti)** stores cross-run summaries and "Lesson Learned" negative constraints, and **Redis** holds per-run `SDLCState`. **LiteLLM + DeepSeek** power all agents including a cheap Diagnostic Utility that translates raw tool failures into actionable fix-it instructions for `dev_node`. Generated code lives on mounted volumes (paths + hashes in state, not code blobs). All test/review activities run in **ephemeral Docker containers** to eliminate RCE risk.

---

**Project Structure**
```
src/
  core/           ← BaseLLM, OmegaGoal schema, config loader
  agents/         ← 6 LangGraph node functions + diagnostic utility
  state/          ← SDLCState + sub-schemas (Pydantic)
  workflows/      ← Temporal workflow + activity definitions
  memory/         ← ZepStore (cross-run), RedisStore (per-run)
  tools/          ← Docker activity wrappers: ruff, pytest, bandit, mypy, pip-audit
  sandbox/        ← Docker container lifecycle management
config/
  llm.yaml        ← provider, model, api_key_env (DEEPSEEK_API_KEY)
  temporal.yaml   ← host, namespace, task_queue
  memory.yaml     ← zep + redis connection settings
  goals/
    implement_auth.yaml
    example_goal.yaml
volumes/          ← ephemeral per-run workspaces (run_id subdirectories)
main.py
```

---

**Steps**

1. **Project scaffold** — `pyproject.toml` with deps: `langgraph`, `temporalio`, `zep-python`, `graphiti-core`, `redis`, `litellm`, `pydantic`, `docker` (Python SDK). Python 3.11+.

2. **Goal Definition File** (`config/goals/*.yaml`) — Primary interface. Each file is a self-contained mission document validated against `OmegaGoal` before any agent runs:

   ```yaml
   goal_id: "auth-system-001"
   objective: "Implement a secure JWT-based authentication system for a FastAPI app."
   context: "The app uses PostgreSQL with SQLAlchemy. We need login and signup endpoints."
   technical_requirements:
     - "Use Passlib with bcrypt for password hashing."
     - "All endpoints must be asynchronous."
     - "JWT secret must be loaded from environment variables."
   quality_thresholds:
     max_cyclomatic_complexity: 8
     min_test_coverage: 90
     enforce_type_hints: true
   success_criteria:
     - "User can register with email and password."
     - "User can exchange credentials for a token."
     - "Protected routes return 401 without a valid token."
   ```

   `OmegaGoal` Pydantic schema (`src/core/goal.py`) validates all fields on load. `goal_id` doubles as both the Temporal workflow ID and the `run_id`, guaranteeing idempotency.

3. **Refined State Schema** (`src/state/schema.py`) — `files_changed` is now a list of `FileChange` objects (paths + hashes only — no code blobs in state). Code lives on the mounted volume at `volumes/{run_id}/`:

   ```python
   class FileChange(BaseModel):
       path: str           # relative path on volume
       requirement_id: str # links code to a requirement
       rationale: str      # why the agent changed this file
       hash: str           # sha256 of content for integrity

   class QualityThresholds(BaseModel):
       max_cyclomatic_complexity: int = 10
       min_test_coverage: int = 80
       enforce_type_hints: bool = True

   class SDLCRequirement(BaseModel):
       id: str
       description: str
       acceptance_criteria: list[str]

   class ToolEvidence(BaseModel):
       tool_name: str
       passed: bool
       findings: str           # raw output
       diagnosis: Optional[str] = None  # filled by Diagnostic Utility on failure

   class SDLCState(BaseModel):
       run_id: str
       objective: str
       context: Optional[str] = None
       technical_requirements: list[str] = Field(default_factory=list)
       quality_thresholds: QualityThresholds = Field(default_factory=QualityThresholds)
       success_criteria: list[str] = Field(default_factory=list)
       risk_level: Literal["low","medium","high","critical"] = "low"
       requirements: list[SDLCRequirement] = Field(default_factory=list)
       architecture_doc: Optional[str] = None
       files_changed: list[FileChange] = Field(default_factory=list)
       tests_written: list[FileChange] = Field(default_factory=list)
       gate_evidence: list[ToolEvidence] = Field(default_factory=list)
       lessons_learned: list[str] = Field(default_factory=list)  # injected from Zep
       release_notes: Optional[str] = None
       current_phase: Literal["planning","implementation","testing","review","release","done","human_review"] = "planning"
       loop_count: int = 0
   ```

4. **LLM abstraction** (`src/core/llm.py`) — LiteLLM wrapper with `BaseLLM` protocol (`async achat()`, `async aembed()`). Config via `config/llm.yaml`:

   ```yaml
   main:
     provider: deepseek
     model: deepseek/deepseek-chat
     api_key_env: DEEPSEEK_API_KEY
   diagnostic:
     provider: deepseek
     model: deepseek/deepseek-chat   # same key, fast/cheap calls
     api_key_env: DEEPSEEK_API_KEY
   ```

   Two named instances are instantiated — `main_llm` for the 6 agents and `diagnostic_llm` for the Diagnostic Utility. Swapping providers requires only YAML changes.

5. **Docker Sandbox** (`src/sandbox/`) — `SandboxManager` uses the Python Docker SDK to manage ephemeral containers for all code execution activities:
   - `create_sandbox(run_id) -> ContainerRef` — spins up a network-isolated container (no internet access, no host mounts except the read-only `volumes/{run_id}/` workspace)
   - `exec_in_sandbox(container_ref, cmd) -> tuple[int, str]` — runs a command, captures stdout/stderr
   - `destroy_sandbox(container_ref)` — removes the container after the activity completes
   - Every tool activity (`run_pytest`, `run_ruff`, etc.) calls `create_sandbox` → `exec_in_sandbox` → `destroy_sandbox`. No tool ever executes directly on the host.

6. **Temporal Activities** (`src/workflows/activities.py`) — All `@activity.defn` with retry policies. Tool activities are sandboxed via `SandboxManager`:
   - `write_file(run_id, path, content)` — writes to `volumes/{run_id}/`, returns `FileChange` with hash
   - `read_file(run_id, path) -> str`
   - `run_ruff(run_id, thresholds) -> ToolEvidence`
   - `run_mypy(run_id, enforce_type_hints: bool) -> ToolEvidence`
   - `run_bandit(run_id) -> ToolEvidence`
   - `run_pytest(run_id, min_coverage: int) -> ToolEvidence` — passes `--fail-under={min_coverage}`
   - `run_pip_audit(run_id) -> ToolEvidence`
   - `run_complexity_check(run_id, max_complexity: int) -> ToolEvidence`
   - `grep_requirement_tags(run_id) -> dict[str, list[str]]` — scans code for `# Requirement: {id}` comments, returns mapping for traceability (deterministic, no LLM)
   - `send_hitl_signal(run_id, reason)` — emits a Temporal Signal; workflow blocks via `workflow.wait_condition` until a human approves
   - `save_to_redis(run_id, state_json)`
   - `save_to_zep(summary, lessons_learned)`
   - `provision_volume(run_id)` / `teardown_volume(run_id)`

7. **Diagnostic Utility** (`src/agents/diagnostic.py`) — Not a full agent node; a shared helper called inside agent nodes when `ToolEvidence.passed == False`. Uses `diagnostic_llm` to summarize the raw `findings` string into a structured `diagnosis` string (e.g., `"pytest: 3 failures in test_auth.py lines 45, 67, 89. Root cause: missing AsyncSession fixture. Fix: add @pytest.fixture async def session in conftest.py"`). The diagnosis is written back into `ToolEvidence.diagnosis` and injected into `dev_node`'s prompt on the next loop iteration. This prevents `dev_node` from receiving raw 50-line stack traces.

8. **6 LangGraph Agent Nodes** (`src/agents/`) — Each receives `SDLCState`, returns updated `SDLCState`. Code is written to volume via `write_file` activity and tracked as `FileChange` — never stored in state as raw text:
   - `supervisor_node` — evaluates state; triggers `send_hitl_signal` if `risk_level == "critical"` OR `loop_count >= 3`; blocks via `workflow.wait_condition` (owned by Temporal, not LangGraph); sets `current_phase = "done"` when all gates pass
   - `tech_lead_node` — queries Zep for past decisions AND `lessons_learned` (negative constraints from past failed runs) on similar objectives; uses `objective + context + technical_requirements + lessons_learned` to produce `list[SDLCRequirement]` and `architecture_doc`; sets `risk_level`
   - `dev_node` — reads requirements + architecture + any `ToolEvidence.diagnosis` from prior loop; generates code to volume via `write_file`; inserts `# Requirement: {id}` comment tags into every generated file for deterministic traceability
   - `qa_node` — reads requirements + `files_changed` paths + `success_criteria`; generates pytest files to volume; tags every test with `# Requirement: {id}`
   - `review_node` — invokes all sandboxed tool activities parameterized by `quality_thresholds`; on any failure, calls Diagnostic Utility to populate `ToolEvidence.diagnosis`; makes **zero direct LLM calls for evaluation** — LLM only summarizes failure text
   - `release_engineer_node` — runs `grep_requirement_tags` activity; validates that every `Requirement.id` appears in code files, test files, and has a passing `ToolEvidence`; cross-checks `success_criteria`; generates `release_notes`; calls `save_to_zep` with run summary + new lessons learned

9. **Zep Negative Constraints** (`src/memory/zep_store.py`) — On run completion, `release_engineer_node` writes two types of graph nodes:
   - **Positive:** architecture decisions, patterns that worked
   - **Negative (Lessons Learned):** failure patterns tagged by technology stack (e.g., `"sqlalchemy 2.x + pydantic v1: incompatible — 3 loops failed"`) — these are recalled by `tech_lead_node` and injected into `SDLCState.lessons_learned` as a "What NOT to do" list at planning time

10. **Temporal Workflow** (`src/workflows/omega_workflow.py`) — `@workflow.defn class OmegaWorkflow`. Temporal is the **single source of truth** for state persistence. LangGraph is treated as a pure routing function. HITL wait is handled exclusively via `workflow.wait_condition` on a Temporal Signal — not LangGraph's interrupt mechanism. On crash + restart, Temporal replays from the last completed activity.

11. **Entrypoint** (`main.py`) — `python main.py --goal config/goals/implement_auth.yaml`. Loads and validates `OmegaGoal`, provisions `volumes/{goal_id}/`, submits to Temporal using `goal_id` as workflow ID (idempotent re-submit).

---

**Phased Build Order (Security First)**

- **Phase 1 (Skeleton):** `OmegaGoal` + `SDLCState` schemas, YAML loader, stubbed 6-node LangGraph, dummy activities, end-to-end mock run to `done`
- **Phase 2 (Hardened Sandbox):** Docker `SandboxManager`, all tool activities run in network-isolated ephemeral containers, `provision_volume` / `teardown_volume` activities
- **Phase 3 (Memory & Logic):** Redis state persistence, Zep cross-run summaries + negative constraints, Zep recall in `tech_lead_node`, `lessons_learned` injection
- **Phase 4 (Cognition + Diagnostics):** Real LiteLLM/DeepSeek calls in all 6 nodes, Diagnostic Utility wired into `review_node`, `ToolEvidence.diagnosis` injected into `dev_node` prompts
- **Phase 5 (Release Gate):** `grep_requirement_tags` traceability enforcement, `success_criteria` cross-check, HITL Signal + `workflow.wait_condition`, loop guard escalation, Zep lesson learned writes

---

**Verification**

- **Schema:** Load `implement_auth.yaml`, assert `OmegaGoal` validates and correctly initializes `SDLCState` with zero code blobs
- **Sandbox isolation:** Run `run_pytest` activity, assert it spawns a new Docker container with no network access and destroys it on completion
- **State bloat:** Assert `SDLCState` JSON never exceeds 100KB regardless of codebase size (code lives on volume)
- **Diagnostic path:** Inject a failing `ToolEvidence` with a raw stack trace, assert `ToolEvidence.diagnosis` is populated and forwarded to the next `dev_node` prompt
- **Traceability:** Assert `grep_requirement_tags` finds `# Requirement: {id}` tags in all generated files; assert `release_engineer_node` blocks release if any requirement is untagged
- **Chaos:** `SIGKILL` during `run_pytest`, restart, verify Temporal replays without re-running passed activities
- **Provider swap:** Change `config/llm.yaml` to a different provider, re-run integration test, assert zero code changes

---

**Decisions**

- **Docker sandbox in Phase 2 (not Phase 5):** RCE risk from LLM-generated code executing on the host is a critical security concern, not a polish item. Sandboxing is the second thing built, before any real LLM calls.
- **DeepSeek for both main and diagnostic:** Single API key (`DEEPSEEK_API_KEY`), single provider config. Fast enough for diagnostic summarization; no second provider to manage.
- **Volume mount for code, not state/Git:** Avoids state bloat and the 2MB Redis/Temporal payload limit. Simpler than Git branches for now — `FileChange.hash` provides integrity. Git integration deferred.
- **`FileChange` with rationale + hash:** Gives `release_engineer_node` and future auditors a full lineage of why each file changed without storing code in state. Hash enables integrity checks.
- **`# Requirement: {id}` tags in code:** Enables `grep_requirement_tags` to verify traceability deterministically with zero LLM calls. The final release gate is fully auditable by a human reading the comments.
- **Temporal owns HITL, not LangGraph:** `workflow.wait_condition` on a Temporal Signal handles indefinite human waits gracefully. LangGraph's interrupt is not designed for waits measured in minutes/hours.
- **Negative constraints in Zep:** Failure patterns stored as first-class graph nodes prevent the system from repeating the same dependency conflicts or design mistakes across runs on similar objectives.
- **Hard loop cap at 3 → HITL escalation:** Prevents infinite dev/review cycles regardless of risk level. After 3 failed loops the system admits it needs human judgment.