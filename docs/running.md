# Running the Orchestrator

## CLI reference

```
python main.py --goal <path> [options]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--goal` | path | *(required)* | Path to the goal YAML file |
| `--mode` | `local` \| `temporal` | `local` | Execution mode (`temporal` available in Stage 7) |
| `--no-sandbox` | flag | off | Disable Docker sandboxing |
| `--resume` | flag | off | Resume from the last saved checkpoint |

---

## `make` targets

### Environment

| Target | Description |
|--------|-------------|
| `make setup` | Create `.venv` and install all dev deps (one-shot) |
| `make install` | Re-install in editable mode with `dev` extras |
| `make install-all` | Install all optional extras (stage3–stage5 + dev) |

### Code quality

| Target | Description |
|--------|-------------|
| `make lint` | `ruff check src/ tests/` |
| `make lint-fix` | `ruff check --fix src/ tests/` |
| `make format` | `ruff format src/ tests/` |
| `make format-check` | Check formatting without writing |
| `make typecheck` | `mypy src/` |
| `make check` | Full quality pass: lint + format-check + typecheck |

### Tests

| Target | Description |
|--------|-------------|
| `make test` | Full test suite with verbose output |
| `make test-cov` | Tests with coverage report |
| `make test-stage STAGE=3` | Run tests for a specific stage |
| `make test-fast` | Stop on first failure |

### Docker

| Target | Description |
|--------|-------------|
| `make docker-build` | Build the `omega-python-runner` sandbox image |
| `make docker-clean` | Remove the sandbox image |
| `make docker-check` | Check whether the image exists locally |

### Orchestrator runs

| Target | Description |
|--------|-------------|
| `make run` | Run with sandbox, using `GOAL` variable |
| `make run-no-sandbox` | Run without sandbox |
| `make resume` | Resume from last checkpoint |
| `make resume-no-sandbox` | Resume without sandbox |
| `make run-example` | Run the built-in example goal |
| `make run-auth` | Run the `implement_auth` goal |

Override the goal path:

```bash
make run GOAL=config/goals/my_goal.yaml
```

### Cleanup

| Target | Description |
|--------|-------------|
| `make clean` | Remove all generated artefacts |
| `make clean-runs` | Delete everything under `runs/` |
| `make clean-volumes` | Delete everything under `volumes/` |
| `make clean-pycache` | Remove all `__pycache__` directories |

---

## Resume a run

If a run is interrupted mid-way, resume from the last saved checkpoint:

```bash
python main.py --goal config/goals/example_goal.yaml --mode local --resume
```

The state is loaded from `runs/<goal_id>/state.json`. The graph continues from the last completed phase.

!!! note
    `--resume` only works if `runs/<goal_id>/state.json` exists. If it does not, the run starts fresh with a warning.

---

## Run output locations

| Path | Contents |
|------|----------|
| `runs/<goal_id>/state.json` | Full SDLCState snapshot |
| `runs/<goal_id>/timings.json` | Per-node timing data |
| `volumes/<goal_id>/src/` | Generated implementation files |
| `volumes/<goal_id>/tests/` | Generated test files |
| `volumes/<goal_id>/requirements.txt` | Generated Python dependencies |

---

## Example session

```bash
# Fresh run with sandbox
make run-example

# Inspect generated code
ls volumes/example-goal-001/src/

# Inspect final state
cat runs/example-goal-001/state.json | python -m json.tool | head -40

# Resume after interruption
make resume GOAL=config/goals/example_goal.yaml
```
