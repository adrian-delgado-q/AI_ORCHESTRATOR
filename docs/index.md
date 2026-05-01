# Lean Omega — AI SDLC Orchestrator

**Lean Omega** is a multi-agent orchestrator that takes a plain-English goal and autonomously drives the entire software development lifecycle:

1. A **Tech Lead** agent decomposes the goal into structured requirements.
2. A **Developer** agent writes Python code for each requirement.
3. A **QA** agent runs a suite of quality gates (ruff, pytest, mypy, bandit, pip-audit).
4. A **Review** agent decides to pass or loop back to development.
5. A **Release Engineer** stages the artefacts.
6. A **Supervisor** enforces the loop cap and escalates to human review when needed.

All tool execution happens inside an **isolated Docker sandbox** — no third-party code ever touches the host Python environment.

---

## At a glance

```
┌─────────────┐    goal YAML     ┌──────────────────────────────────────────┐
│  You        │ ───────────────► │          Lean Omega graph                │
└─────────────┘                  │                                          │
                                 │  tech_lead → dev → qa → review           │
                                 │       ▲                   │              │
                                 │       └──────── loop ◄────┘              │
                                 │                   │                      │
                                 │         release_engineer → supervisor    │
                                 └──────────────────────────────────────────┘
                                              │
                                   volumes/<run_id>/
                                   ├── src/           ← generated code
                                   ├── tests/         ← generated tests
                                   └── requirements.txt
```

---

## Key design decisions

| Decision | Rationale |
|----------|-----------|
| **LangGraph** topology | Explicit, inspectable state machine rather than an opaque chain |
| **Deterministic review gates** | Pass/fail is decided by tool exit codes, not LLM opinion |
| **Docker sandbox** | `npm`, `go`, `tsc`, `pip install` never run on the host |
| **Typed contracts** (Stage 6+) | `ImplementationTarget` schema drives language-agnostic generation |
| **Staged rollout** | Each stage is independently testable; no stage breaks the one before |

---

## Navigation

- [**Setup**](setup.md) — prerequisites, installation, first run
- [**Architecture**](architecture.md) — deep-dive into the agent graph and state machine
- [**Goal YAML reference**](goal-yaml.md) — every field documented and annotated
- [**Configuration**](configuration.md) — LLM config, environment variables, sandbox flags
- [**Running**](running.md) — CLI flags, `make` targets, resume, sandbox toggle
- [**Stages roadmap**](stages.md) — what is built today and what is coming
- [**Contributing**](contributing.md) — dev workflow, test conventions, PR guidelines
