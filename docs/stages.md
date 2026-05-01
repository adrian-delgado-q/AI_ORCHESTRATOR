# Stages Roadmap

Lean Omega is built in incremental, independently-testable stages. Each stage adds capability without breaking the verified baseline of all previous stages.

**Non-regression target:** 94+ tests must remain green through all stage transitions.

---

## Completed stages

### Stage 1 — Core state machine ✅

LangGraph graph with all six agent nodes (`tech_lead → dev → qa → review → release_engineer → supervisor`). Pydantic `SDLCState`, `FileChange`, `ToolEvidence` schemas. Goal YAML loader. `StubLLM` for deterministic tests.

### Stage 2 — File I/O and workspace ✅

`workspace.write_file` / `read_file` with SHA-256 hash tracking. `VOLUMES_DIR` monkeypatching pattern for test isolation. State persistence (`save_state` / `load_state`).

### Stage 3 — Tool runners ✅

`ToolEvidence`-returning wrappers for `ruff`, `pytest`, `mypy`, `bandit`, `pip-audit`. Subprocess isolation. Tool output parsing and structured evidence collection.

### Stage 4 — Real LLM integration ✅

`LiteLLMBackend` wired into all agent nodes. `DiagnosticUtility` for structured failure analysis. `load_llm()` factory with `config/llm.yaml`. DeepSeek as the default provider.

### Stage 5 — Docker sandboxing ✅

`SandboxManager` (`src/sandbox/manager.py`) wraps the Docker Python SDK. All tool execution migrated to Docker containers. `omega-python-runner` image (`docker/Dockerfile.python-runner`). Stale `.deps` cache detection and recovery.

---

## Upcoming stages

### Stage 6 — Typed contracts + language-agnostic generation 🔜

- `TargetStack` schema (language, package manager, test framework, lint tool)
- `stack_resolver` node — infers the target stack from the goal
- `implementation_planner` node — generates `ImplementationTarget` contracts before `dev`
- Language-specific tool dispatchers (`node_runners.py`, `go_runners.py`)
- New graph topology: `tech_lead → stack_resolver → implementation_planner → qa → dev → review → ...`

```
# Stage 6 graph
tech_lead → stack_resolver → implementation_planner → qa → dev → review
                                        ▲                              │
                                        └──────────── loop ◄───────────┘
```

### Stage 7 — Memory and learning 🔜

- `lessons_learned.json` local memory store
- Zep / Graphiti integration for cross-run memory
- `memory_manager.py` unified interface
- Agents query memory before planning to avoid repeating known failure patterns

### Stage 8 — Temporal durability 🔜

- Temporal workflow and activities wrapping the LangGraph graph
- Long-running runs survive process restarts
- Temporal visibility UI for run inspection
- `--mode temporal` CLI flag becomes active

### Stage 9 — Production hardening 🔜

- Redis-backed state for multi-instance deployments
- Structured logging (JSON) for log aggregation
- OpenTelemetry tracing
- Horizontal scaling of agent workers

---

## Stage contract

Each stage must:

1. Produce a working, testable system on its own.
2. Pass all tests from all prior stages.
3. Add new interfaces behind stable facades — no breaking changes to `workspace.py`, `schema.py`, or the routing invariants.
