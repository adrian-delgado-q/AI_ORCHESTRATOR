# Contributing

## Development workflow

```bash
# 1. Set up environment
bash scripts/setup_env.sh
make install-all

# 2. Build the sandbox image
make docker-build

# 3. Make your changes in src/ or tests/

# 4. Run the full quality pass
make check

# 5. Run the full test suite
make test

# 6. Submit a pull request targeting main
```

---

## Running tests

```bash
# All tests
make test

# With coverage
make test-cov

# Single stage
make test-stage STAGE=3

# Stop on first failure
make test-fast
```

Tests use `StubLLM` and do not require an API key or Docker. The sandbox is monkeypatched in `tests/conftest.py`.

### Test conventions

| Convention | Details |
|------------|---------|
| One file per stage | `tests/test_stage1.py`, `tests/test_stage2.py`, … |
| No real LLM calls | `StubLLM` must be used in all unit tests |
| No real Docker calls | Sandbox is monkeypatched via `conftest.py` |
| `VOLUMES_DIR` isolation | Tests monkeypatch `src.io.workspace.VOLUMES_DIR` and `src.tools.runners.VOLUMES_DIR` to a `tmp_path` fixture |
| 94+ tests must stay green | All prior-stage tests must pass when a new stage lands |

---

## Code style

All code is linted and formatted with [ruff](https://docs.astral.sh/ruff/):

```bash
make lint        # check
make lint-fix    # auto-fix
make format      # format
```

Type checking:

```bash
make typecheck   # mypy src/
```

Configuration lives in `pyproject.toml` under `[tool.ruff]` and `[tool.mypy]`.

---

## Adding a new agent node

1. Add the node function to `src/agents/nodes.py`.
2. Register it in `src/agents/graph.py` and wire up routing.
3. Add a test in `tests/test_stageN.py` that exercises the node with `StubLLM`.
4. Ensure existing tests still pass: `make test`.

!!! warning "Routing invariant"
    Only `review_node` may set `state.current_phase`. The graph router reads this field exclusively. Do not set `current_phase` in any other node.

---

## Adding a new tool runner

1. Add a function in `src/tools/runners.py` that returns `ToolEvidence`.
2. Register it in `src/agents/nodes.py` within the `qa_node` tool list.
3. Add a corresponding sandboxed path in `src/tools/sandboxed_runner.py`.
4. Add tests that monkeypatch the runner and assert `ToolEvidence` shape.

---

## Interfaces that must not change

These are frozen contracts. New functionality must be additive or hidden behind new interfaces:

```python
# src/io/workspace.py
write_file(run_id, path, content, requirement_id, rationale) -> FileChange
read_file(run_id, path) -> str
VOLUMES_DIR: Path  # monkeypatched in tests

# src/state/schema.py
class FileChange(BaseModel): ...
class ToolEvidence(BaseModel): ...

# src/agents/graph.py
# _route_after_review reads state.current_phase — do not replace with
# gate_evidence scanning
```

---

## CI gates

Every pull request and push to `main` runs:

| Job | Tool | Must pass |
|-----|------|-----------|
| Lint & Format | ruff | Yes |
| Type Check | mypy | Yes |
| Tests | pytest + cov | Yes |
| Security | bandit + pip-audit | Yes |
| Build Docker image | docker build | Yes |

The `publish-image` job only runs on `main` push after all gates pass.  
The `docs` job builds and publishes MkDocs to GitHub Pages on `main` push.
