## Plan: Lean Omega — Incremental SDLC Orchestrator (v4)

**TL;DR** — The final architecture (LangGraph + Temporal + Zep + Docker + DeepSeek) is unchanged as the destination. The strategy changes: build a fully runnable local-only system first, then layer in infrastructure one stage at a time. Each stage produces a working, testable system. No stage breaks the stage before it. Interfaces are defined once and implementations are swapped, not rewritten.

---

**Project Structure (stable across all stages)**
```
src/
  core/           ← OmegaGoal schema, config loader, BaseLLM (added Stage 4)
  agents/         ← 6 LangGraph node functions, diagnostic utility (Stage 4)
  state/          ← SDLCState + sub-schemas
  workflows/      ← Temporal workflow + activities (Stage 7 only)
  memory/         ← lessons_learned.json (Stage 6), Zep adapter (Stage 6+)
  tools/          ← subprocess wrappers (Stage 3), Docker wrappers (Stage 5)
  sandbox/        ← SandboxManager (Stage 5)
  io/             ← write_file, read_file, FileChange (Stage 2)
config/
  llm.yaml        ← (Stage 4)
  temporal.yaml   ← (Stage 7)
  memory.yaml     ← (Stage 6)
  goals/
    example_goal.yaml
    implement_auth.yaml
volumes/          ← per-run generated code: volumes/{run_id}/
runs/             ← per-run state snapshots: runs/{run_id}/state.json
main.py           ← CLI entrypoint: --goal, --mode local|temporal
```

---

### Stage 1 — Local SDLC Loop Skeleton

**Goal:** A runnable end-to-end mock that proves the graph topology and state schema are correct before touching real LLMs or infrastructure.

**Build:**
- `OmegaGoal` Pydantic schema and YAML loader (`src/core/goal.py`)
- `SDLCState` full Pydantic schema (`src/state/schema.py`) — includes all fields needed by all future stages, but populated with stubs for now
- 6 stubbed LangGraph node functions in `src/agents/` — each logs its phase, appends a fake `SDLCRequirement` or `ToolEvidence`, and advances `current_phase`
- `StateGraph` connecting nodes with fixed edges: `tech_lead → dev → qa → review → release_engineer → supervisor → END`. Conditional edge after `review`: if any `gate_evidence` fails AND `loop_count < 3`, route back to `dev`
- State persistence: serialize `SDLCState` to `runs/{run_id}/state.json` after every node transition
- `volumes/{run_id}/` directory provisioned on run start
- CLI: `python main.py --goal config/goals/example_goal.yaml --mode local`

**Success criteria:**
- Valid YAML loads and validates as `OmegaGoal`
- Graph runs `planning → done` without errors
- `runs/{run_id}/state.json` exists and contains populated state after the run
- Mock run completes with `current_phase == "done"`

**Known limitations:** All agent behavior is hardcoded stubs. No real files written. No LLM calls.

---

### Stage 2 — Real Local Workspace and File Tracking

**Goal:** Replace fake file behavior with real disk I/O. Prove that state stays lean (paths/hashes only, no code blobs).

**Build:**
- `src/io/workspace.py`: `write_file(run_id, path, content) -> FileChange`, `read_file(run_id, path) -> str`
- `FileChange` Pydantic model: `path`, `requirement_id`, `rationale`, `hash` (sha256 of content)
- Update `SDLCState.files_changed` and `tests_written` to `list[FileChange]`
- `dev_node` stub writes one real source file (hardcoded Python snippet) to `volumes/{run_id}/`
- `qa_node` stub writes one real test file (hardcoded pytest snippet) to `volumes/{run_id}/`
- Assert `state.json` contains `FileChange` records with paths and hashes — no raw code strings

**Success criteria:**
- `volumes/{run_id}/` contains real files after the run
- `state.json` has populated `files_changed` and `tests_written` with `FileChange` objects
- `state.json` remains small (no code embedded)

**Known limitations:** File content is still hardcoded stubs. Tools don't run yet.

---

### Stage 3 — Deterministic Review Gates (Local subprocess)

**Goal:** Make `review_node` produce real `ToolEvidence` by running actual tools via `subprocess`. Prove the pass/fail routing logic works before adding LLMs or Docker.

**Build:**
- `src/tools/` — one wrapper per tool, each returns `ToolEvidence`:
  - `run_ruff(run_id) -> ToolEvidence` (required)
  - `run_pytest(run_id, min_coverage) -> ToolEvidence` (required)
  - `run_mypy(run_id, enforce: bool) -> ToolEvidence` (optional, skips if disabled in thresholds)
  - `run_bandit(run_id) -> ToolEvidence` (optional)
  - `run_pip_audit(run_id) -> ToolEvidence` (optional, skips if no requirements.txt)
  - `run_complexity_check(run_id, max_complexity) -> ToolEvidence` (interface only, stub impl)
- All tools invoked via `subprocess.run`, output parsed into `ToolEvidence.findings`, pass/fail set from return code
- `review_node` calls all enabled tools, populates `gate_evidence`
- `release_engineer_node` blocks `done` if any required `ToolEvidence.passed == False`
- `QualityThresholds` from `OmegaGoal` forwarded as CLI flags to each tool

**Success criteria:**
- `review_node` produces real `ToolEvidence` records from actual tool output
- A run with intentionally broken code fails at review and routes back to `dev_node`
- `loop_count` increments on each failed cycle
- A run with valid stub code passes all gates and reaches `done`

**Known limitations:** `dev_node` still writes hardcoded stubs. Tools run on host (no sandbox yet). No LLM diagnosis of failures.

---

### Stage 4 — Real LLM Agents and Diagnostic Utility

**Goal:** Replace all stubs with real LiteLLM/DeepSeek calls. Add the Diagnostic Utility so `dev_node` receives structured fix-it instructions instead of raw stack traces.

**Build:**
- `src/core/llm.py`: `BaseLLM` protocol, `async achat()`, `async aembed()`, LiteLLM backend
- `config/llm.yaml` with `main` and `diagnostic` sections both pointing to `deepseek/deepseek-chat` + `DEEPSEEK_API_KEY`
- Real implementations for all 6 nodes:
  - `tech_lead_node`: generates `list[SDLCRequirement]` + `architecture_doc` from goal + context
  - `dev_node`: generates code files for each requirement, writes via `write_file`, inserts `# Requirement: {id}` comment tags
  - `qa_node`: generates pytest files per requirement, writes via `write_file`
  - `review_node`: runs tools, then calls Diagnostic Utility on any failure to populate `ToolEvidence.diagnosis`
  - `release_engineer_node`: generates `release_notes`
  - `supervisor_node`: evaluates loop state, escalates to `human_review` after 3 failed loops
- `src/agents/diagnostic.py`: `DiagnosticUtility` — uses `diagnostic_llm` to summarize raw `findings` into `diagnosis` (structured fix-it instructions). Called only on failure, not on every review pass.
- `dev_node` receives `ToolEvidence.diagnosis` fields from prior loop iterations in its prompt context

**Success criteria:**
- `tech_lead_node` generates real `SDLCRequirement` objects from `example_goal.yaml`
- `dev_node` writes real code files with requirement tags
- `qa_node` writes real test files
- `review_node` diagnosis is readable and actionable (not a raw stack trace)
- After 3 failed review loops, `current_phase == "human_review"` and the run stops cleanly

**Known limitations:** Tools still run on host via subprocess. No memory layer. No Temporal.

---

### Stage 5 — Docker Sandbox

**Goal:** Eliminate RCE risk. All generated code execution moves into ephemeral, network-isolated Docker containers.

**Build:**
- `src/sandbox/manager.py`: `SandboxManager` using Python Docker SDK
  - `create_sandbox(run_id) -> ContainerRef` — network-disabled, `volumes/{run_id}/` mounted read-only
  - `exec_in_sandbox(container_ref, cmd) -> tuple[int, str]`
  - `destroy_sandbox(container_ref)`
- All tool wrappers in `src/tools/` updated to route through `SandboxManager` instead of direct `subprocess`
- `write_file` still writes to host volume; the sandbox mounts it read-only for tool execution
- `local` mode without Docker remains available via a `--no-sandbox` flag for development

**Success criteria:**
- `pytest`, `ruff`, and other tools execute inside Docker containers
- Containers are destroyed after each tool run
- Host machine does not execute generated code
- `ToolEvidence` interface is unchanged — Stage 3/4 tests still pass

**Known limitations:** No persistent memory. No Temporal durability.

---

### Stage 6 — Memory Layer

**Goal:** Add cross-run learning so `tech_lead_node` avoids repeating past mistakes.

**Build:**
- **Simple first:** `src/memory/local_store.py` — reads/writes `runs/lessons_learned.json`. Two lists per entry: `positive` (patterns that worked) and `negative` (failure patterns tagged by tech stack).
- `release_engineer_node` writes a lesson entry on every completed run (pass or fail)
- `tech_lead_node` reads matching lessons at planning time, injects into `SDLCState.lessons_learned`
- **Then add Zep:** `src/memory/zep_store.py` — Graphiti-backed graph store replacing the JSON file. Same interface (`remember_run_summary()`, `recall_past_decisions(query)`). JSON store remains as fallback.
- `memory_manager.py`: unified interface with backend configurable via `config/memory.yaml`

**Success criteria:**
- `release_engineer_node` writes lessons after a run
- `tech_lead_node` retrieves and injects relevant lessons on the next run of a similar goal
- `lessons_learned` appears in `SDLCState` and in agent system prompts
- System avoids a known failure pattern when `lessons_learned` includes it

**Known limitations:** No Temporal durability. No Redis. Lesson retrieval quality depends on Zep embedding.

---

### Stage 7 — Temporal Orchestration

**Goal:** Add crash-safety and HITL handling. Local mode must continue to work unchanged.

**Build:**
- `src/workflows/omega_workflow.py`: `@workflow.defn class OmegaWorkflow` — wraps each node call in a `@activity.defn`. Workflow ID = `goal_id`.
- All side effects (file I/O, tool runs, memory writes) become individually retriable Temporal Activities
- HITL: `human_review` phase emits a Temporal Signal; workflow blocks via `workflow.wait_condition` — not LangGraph interrupt
- `--mode temporal` flag in CLI submits to Temporal instead of running the graph directly
- `--mode local` continues to call node functions directly, bypassing Temporal
- Activity retry policies defined per activity type (tool runs: 3 retries, LLM calls: 2 retries with backoff)

**Success criteria:**
- Same `example_goal.yaml` runs in both `--mode local` and `--mode temporal`
- `SIGKILL` during a tool activity → restart → Temporal replays from last completed activity
- `human_review` phase blocks indefinitely until a Temporal Signal is sent
- No agent logic is rewritten — only wrapped in activity definitions

**Known limitations:** No Redis yet. State checkpoint is Temporal event history.

---

### Stage 8 — Production Hardening

**Goal:** Make the system auditable, reproducible, and operationally complete.

**Build:**
- **Redis** (only if needed): fast state lookup for large deployments, replacing disk `state.json` reads in hot paths
- Structured audit logging — every node transition logged with timestamp, phase, loop count, and evidence summary
- `release_engineer_node`: `grep_requirement_tags` activity scans all code/test files for `# Requirement: {id}` comments; blocks `done` if any requirement is untagged
- `success_criteria` cross-check: every item in `OmegaGoal.success_criteria` must map to at least one passing `ToolEvidence`
- Quality thresholds enforced as CLI flags forwarded to each tool (already scaffolded in Stage 3, fully wired here)
- CLI commands: `python main.py inspect --run {run_id}`, `python main.py resume --run {run_id}`, `python main.py clean --run {run_id}`
- Strong error handling: every activity failure emits a structured error record into `SDLCState`

**Success criteria:**
- A completed run is fully auditable from `runs/{run_id}/state.json` alone
- A failed run explains why it failed (which tool, which requirement, which node)
- Traceability check blocks release if any `Requirement.id` is missing from code or tests
- Local and Temporal modes both pass all prior stage tests

---

**Cross-Cutting Rules (enforced throughout)**

1. Never store generated code in `SDLCState` — paths and hashes only
2. Never execute generated code on the host once Stage 5 is complete
3. Every stage ships with a verification command (documented in the stage)
4. Interfaces (`BaseLLM`, `ToolEvidence`, `FileChange`, `MemoryManager`) are defined in Stage 1–3 and never changed structurally — only implementations swap
5. `--mode local` must always work without any external service running
6. Temporal is never a prerequisite for running the system — it is an enhancement