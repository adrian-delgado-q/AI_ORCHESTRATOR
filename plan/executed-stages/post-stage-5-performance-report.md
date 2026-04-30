# Post-Stage 5 — Local Execution Performance Improvements
**Status:** Complete
**Date:** 2026-04-30
**Context:** Performance pass applied after Stage 5 sandboxing and resilience
fixes. The orchestrator was functionally correct but still slow because local
mode executed independent LLM calls serially, created one Docker container per
review tool, reinstalled dependencies without a content hash guard, and ran
optional gates even when required gates had already failed.

---

## Problems Identified

| Area | Symptom | Root Cause |
|---|---|---|
| LLM dispatch | Runs waited on each requirement one at a time | `dev_node` and `qa_node` called the LLM serially per requirement |
| Dependency inference | Extra LLM call during implementation | `dev_node` asked the LLM to infer `requirements.txt` from generated code |
| Review gates | Docker startup overhead repeated for every tool | Each tool used `run_in_sandbox()`, creating and destroying its own container |
| Dependency install | Resume/review could reinstall unchanged deps | `.deps` cache was tracked only by run ID/session, not by `requirements.txt` content |
| Failed repair loops | Optional gates added latency after required failures | `review_node` ran mypy, bandit, pip-audit, and complexity even when ruff/pytest had already failed |
| Observability | Slow phases were hard to pinpoint | No timing trace existed for graph nodes, LLM calls, installs, or tools |
| Sandbox CPU | Local review tools were CPU-throttled | Sandbox CPU quota defaulted to 50% of one CPU |

---

## Fixes Applied

### 1. Lightweight timing traces

Added `src/core/timing.py` with a best-effort `timed()` context manager. Timing
events are appended to:

```text
runs/{run_id}/timings.json
```

Instrumented:
- graph node execution in `src/agents/graph.py`
- LLM calls in `src/agents/nodes.py`
- dependency installs in `src/tools/runners.py`
- individual tool executions in `src/tools/runners.py`

Timing write failures are debug-only and never block orchestration.

### 2. Concurrent per-requirement LLM generation

Updated `dev_node` and `qa_node` to use a bounded `ThreadPoolExecutor` for
independent per-requirement LLM calls.

New environment knob:

```text
OMEGA_LLM_CONCURRENCY=4
```

Ordering is deterministic: results are written in the same order as
`state.requirements`, even when faster later requirements return first.

### 3. Deterministic dependency inference

Replaced the extra LLM dependency-analysis call with an AST-based Python import
scanner. The scanner:
- reads generated source and test files
- ignores stdlib and generated local modules
- maps common import names to PyPI package names
- includes known test-runtime dependencies such as `httpx` for FastAPI/Starlette

`qa_node` refreshes `requirements.txt` after writing tests so test-only imports
are included before review starts.

### 4. Shared review sandbox

Added `shared_review_sandbox(run_id)` in `src/tools/runners.py`. During
`review_node`, required and optional gates reuse a single network-disabled
sandbox container instead of creating one container per tool.

The old single-command path still exists for direct runner calls and tests.
Cleanup remains best-effort in a `finally` block.

### 5. Dependency cache keyed by requirements hash

Added `.deps-requirements.sha256` alongside `.deps/`. `_ensure_deps_installed()`
now skips install only when:
- `.deps/` exists and is populated
- `.deps-stale` is absent
- the stored hash matches current `requirements.txt`

If `requirements.txt` changes, dependencies reinstall and the hash marker is
updated after successful install.

### 6. Required-gate short-circuit

`review_node` now runs required gates first (`ruff`, `pytest`). If either fails,
optional gates are skipped by default so repair loops return to implementation
faster.

New environment knob:

```text
OMEGA_REVIEW_FULL_ON_REQUIRED_FAILURE=true
```

When set, optional gates still run even after required gate failure.

### 7. Sandbox CPU quota configuration

Raised the default local sandbox CPU quota from 50% of one CPU to one full CPU.

New environment knob:

```text
OMEGA_SANDBOX_CPU_QUOTA=100000
```

---

## Files Changed

| File | Change |
|---|---|
| `src/core/timing.py` | New timing trace helper. |
| `src/agents/graph.py` | Records per-node timings. |
| `src/agents/nodes.py` | Concurrent dev/QA LLM calls, deterministic dependency scanning, review short-circuit. |
| `src/tools/runners.py` | Shared review sandbox, dependency hash cache, tool/install timings, host `.venv/bin` fallback. |
| `src/sandbox/manager.py` | Configurable CPU quota with one-CPU default. |
| `tests/test_stage3.py` | Required-failure optional-gate skip coverage. |
| `tests/test_stage4.py` | Dev/QA concurrent-order preservation coverage. |
| `tests/test_stage5.py` | Dependency hash cache and shared sandbox lifecycle coverage. |

---

## Validation

Maintained test suite:

```bash
.venv/bin/pytest tests
```

Result:

```text
127 passed
```

Repository-wide `pytest` still collects generated tests under
`volumes/example-goal-001/`; those are generated project tests and require the
generated app dependencies on the host. The maintained orchestrator suite is
under `tests/`.

---

## Operational Notes

- Default local mode remains sandboxed.
- `--no-sandbox` and `--resume` behavior are unchanged.
- Required gates remain authoritative for graph routing.
- Optional gates are still available on successful required gates, or on failed
  required gates when `OMEGA_REVIEW_FULL_ON_REQUIRED_FAILURE=true`.
- Timing traces are run-local diagnostics only; they are not part of
  `SDLCState` and do not affect graph decisions.
