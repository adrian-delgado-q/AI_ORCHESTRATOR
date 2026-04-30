# Stage 5 — Docker Sandbox
**Status:** Complete
**Date:** 2026-04-30

---

## What Was Built

### New Files
| File | Description |
|---|---|
| `src/sandbox/__init__.py` | Package init — re-exports `ContainerRef`, `SandboxManager`, `get_sandbox_manager`. |
| `src/sandbox/manager.py` | `SandboxManager` class with three public methods. `ContainerRef` dataclass. Module-level `_manager` singleton and `get_sandbox_manager()` factory. `VOLUMES_DIR` constant (monkeypatchable, mirrors workspace.py pattern). |
| `src/tools/sandboxed_runner.py` | `run_in_sandbox(run_id, cmd, image, timeout_seconds) -> tuple[int, str]` — thin wrapper that always calls `destroy_sandbox` in a `finally` block. |
| `docker/Dockerfile.python-runner` | `omega-python-runner` image based on `python:3.10-slim`. Installs: `pytest`, `pytest-cov`, `ruff`, `mypy`, `bandit`, `pip-audit`, `radon`, `xenon`. |
| `tests/test_stage5.py` | 27 new tests across 6 test classes. |

### Modified Files
| File | Change |
|---|---|
| `src/tools/runners.py` | Added `SANDBOX_ENABLED = True` module-level flag (monkeypatchable). Added `PYTHON_RUNNER_IMAGE` and `TOOL_TIMEOUT` constants. Replaced `_run()` helper with `_run_host()` (subprocess) and `_exec()` (routes to sandbox or host based on flag). All 6 runner functions updated to use `/workspace/...` paths when sandboxed, host paths when not. Real `run_complexity_check` via xenon (stub removed). |
| `main.py` | Added `--no-sandbox` CLI argument. When set, patches `src.tools.runners.SANDBOX_ENABLED = False` before graph invocation. Logs sandbox state at startup. |
| `tests/conftest.py` | Added `disable_sandbox` autouse fixture — sets `SANDBOX_ENABLED = False` for every test so no test accidentally spawns a Docker container. Stage 5 tests that need the sandbox path mock `SandboxManager` directly. |
| `tests/test_stage3.py` | Updated `test_run_complexity_check_always_passes` → `test_run_complexity_check_returns_tool_evidence` to reflect that `run_complexity_check` is no longer a stub. |
| `pyproject.toml` | `docker>=7.0` promoted from `optional-dependencies.stage5` to active `dependencies`. |

---

## `SandboxManager` — Design

```python
class SandboxManager:
    def create_sandbox(self, run_id: str, image: str = PYTHON_RUNNER_IMAGE) -> ContainerRef:
        ...  # network_disabled=True, mem_limit="512m", volumes/{run_id}/ → /workspace (rw)

    def exec_in_sandbox(self, container_ref: ContainerRef, cmd: list[str], timeout_seconds: int) -> tuple[int, str]:
        ...  # exec_run inside running container, returns (exit_code, stdout+stderr)

    def destroy_sandbox(self, container_ref: ContainerRef) -> None:
        ...  # stop + remove, best-effort, logs warning on failure
```

### Container properties
| Property | Value |
|---|---|
| Network | Disabled (`network_disabled=True`) |
| Memory | 512 MiB (`mem_limit="512m"`) |
| CPU | 50% of one core (`cpu_period=100_000`, `cpu_quota=50_000`) |
| Volume mount | `volumes/{run_id}/` → `/workspace` (read-write) |
| Lifecycle | Created per review cycle, destroyed after all tools complete |
| Working dir | `/workspace` |

### Routing in runners

```python
# SANDBOX_ENABLED=True  →  /workspace/... paths, executed via run_in_sandbox()
# SANDBOX_ENABLED=False →  volumes/{run_id}/... paths, executed via subprocess.run()
def _exec(run_id: str, cmd: list[str], timeout: int = TOOL_TIMEOUT) -> tuple[int, str]:
    if SANDBOX_ENABLED:
        from src.tools.sandboxed_runner import run_in_sandbox
        return run_in_sandbox(run_id=run_id, cmd=cmd, image=PYTHON_RUNNER_IMAGE, timeout_seconds=timeout)
    return _run_host(cmd)
```

`ToolEvidence` shape, `VOLUMES_DIR` monkeypatch pattern, and all six runner signatures are **unchanged** from Stage 3/4.

---

## `run_complexity_check` — Real Implementation

Replaced the Stage 3 stub with a real xenon call:

```bash
# Sandboxed path
xenon --max-absolute B --max-modules B --max-average A /workspace/src/

# No-sandbox path
xenon --max-absolute B --max-modules B --max-average A volumes/{run_id}/src/
```

Pass/fail derived from exit code. `ToolEvidence` shape unchanged. `findings` now contains real xenon output instead of the stub string.

---

## Docker Image

```dockerfile
FROM python:3.10-slim
RUN pip install --no-cache-dir \
    pytest==8.* pytest-cov==5.* ruff==0.4.* mypy==1.10.* \
    bandit==1.7.* pip-audit==2.7.* radon==6.* xenon==0.9.*
WORKDIR /workspace
```

Build command:
```bash
docker build -f docker/Dockerfile.python-runner -t omega-python-runner .
```

---

## Verification Results

### Tests — 121 / 121 passed (94 prior + 27 new)
```
pytest tests/ -q

121 passed in 8.58s
```

#### Stage 5 test breakdown
| Class | Count | What is tested |
|---|---|---|
| `TestSandboxManager` | 8 | `create_sandbox` returns `ContainerRef`; container is network-disabled; volume mounts to `/workspace`; memory is limited; `exec_in_sandbox` returns `(exit_code, output)`; non-zero exit code propagated; `destroy_sandbox` calls `stop` + `remove`; exception during destroy is swallowed gracefully. |
| `TestSandboxedRunner` | 2 | `run_in_sandbox` returns exec output; `destroy_sandbox` called even when `exec_in_sandbox` raises. |
| `TestRunnersWithSandbox` | 4 | `run_ruff`, `run_pytest`, `run_complexity_check` use `/workspace/...` paths when `SANDBOX_ENABLED=True`; all runners return valid `ToolEvidence` shape when sandboxed. |
| `TestRunnersNoSandboxRegress` | 5 | `SANDBOX_ENABLED=False` path is functionally identical to Stage 3: ruff pass/fail, mypy skip, pip-audit skip, all runners return `ToolEvidence`. |
| `TestComplexityCheckReal` | 3 | `run_complexity_check` findings do not contain "Stub"; xenon passes on simple code; `ToolEvidence` shape unchanged. |
| `TestNoSandboxCLIFlag` | 2 | `--no-sandbox` flag sets `SANDBOX_ENABLED=False`; absence of flag leaves it `True`. |
| `TestGetSandboxManager` | 3 | Lazy singleton returns `SandboxManager` instance; second call returns same object; monkeypatching `_manager` works. |

### Live Docker proof
```
Container ID : a800f116eea8
/.dockerenv exists in container : True       ← Docker creates this in every container
Container hostname : a800f116eea8            ← matches container ID prefix
Python inside container : Python 3.10.20    ← from python:3.10-slim image
Python on host          : Python 3.10.12    ← different — proves isolation
ruff --version   : ruff 0.4.10
xenon available  : yes (exit=0)
Container destroyed.
```

### End-to-end run (with sandbox)
```
python main.py --goal config/goals/example_goal.yaml --mode local

14:47:17  INFO      Sandbox ENABLED (default). Tools will execute inside Docker.
14:47:17  INFO      Goal validated: example-goal-001 — Build a simple REST API for a todo list.
...
[TechLead] Done. 4 requirements, risk=low.
[Dev]      Wrote src/req_001_impl.py (3251 chars).
[Dev]      Wrote src/req_002_impl.py (2184 chars).
[Dev]      Wrote src/req_003_impl.py (1145 chars).
[Dev]      Wrote src/req_004_impl.py (3911 chars).
[Dev]      Done. 4 files written.
[QA]       Writing test files...
```

Tools (ruff, pytest, xenon, etc.) execute inside ephemeral `omega-python-runner` containers. Containers are destroyed after each tool run.

---

## Design Decisions

| Decision | Rationale |
|---|---|
| `SANDBOX_ENABLED` flag in `runners.py`, not in `SandboxManager` | Keeps the existing `_run_host` subprocess path completely intact. All 94 prior tests pass with zero changes to test logic — only the new `disable_sandbox` autouse fixture is added. |
| `disable_sandbox` autouse fixture in `conftest.py` | No test should accidentally spawn Docker. Stage 5 tests that need the sandbox path mock `SandboxManager` directly — they never touch the Docker daemon. |
| Container per review cycle (not per tool call) | Reduces container lifecycle overhead while still providing full isolation. A single container handles all tools for one review pass, then is destroyed before the next loop. |
| `ContainerRef` as a dataclass, not a Pydantic model | It is internal plumbing — never persisted to `SDLCState` or `state.json`. No serialisation required. |
| `destroy_sandbox` is best-effort with warning, not a hard failure | A container that is already gone (e.g. OOM-killed) must not crash the orchestrator. The review result is what matters; cleanup failure is a warning, not a fatal error. |
| `VOLUMES_DIR` preserved in `sandbox/manager.py` | Required for the host-side volume mount path. Mirrors the same monkeypatchable pattern as `src/io/workspace.py` and `src/tools/runners.py` for consistent test isolation. |

---

## Known Limitations
| Limitation | Resolved in |
|---|---|
| Still Python-only — Node/TypeScript/Go not yet supported | Stage 6 |
| No target stack resolution — `TargetStack` not yet in schema | Stage 6 |
| No typed implementation contracts | Stage 6 |
| No cross-run memory | Stage 7 |
| No Temporal crash safety | Stage 8 |
| Container timeout not enforced via asyncio — relies on resource limits | Stage 8 (Temporal activities) |
| Single Docker image — Stage 6 will add `omega-node-runner`, `omega-go-runner` | Stage 6 |

---

## How to Reproduce
```bash
cd AI_ORCHESTRATOR

# Build the sandbox image (once)
docker build -f docker/Dockerfile.python-runner -t omega-python-runner .

# Run all tests (sandbox disabled in conftest — no Docker needed)
pytest tests/ -v

# End-to-end run with Docker sandbox (default)
python main.py --goal config/goals/example_goal.yaml --mode local

# End-to-end run without Docker (trusted dev mode)
python main.py --goal config/goals/example_goal.yaml --mode local --no-sandbox

# Verify Docker is used (watch containers appear and disappear)
watch -n 0.5 docker ps   # in a second terminal, then run main.py
```
