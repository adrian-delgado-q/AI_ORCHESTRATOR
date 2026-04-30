# Stage 1 — Local SDLC Loop Skeleton
**Status:** Complete  
**Date:** 2026-04-30  

---

## What Was Built

### Environment
| File | Description |
|---|---|
| `.envrc` | direnv config — auto-activates `.venv` on `cd`, sets `PYTHONPATH`, overrides Nexus PyPI mirror to public PyPI |
| `scripts/setup_env.sh` | One-shot bootstrap script — creates `.venv` with Python 3.10, installs all deps, runs `direnv allow` |
| `.env.local` | Local secrets template (not committed) — pre-wired with stub keys for Stages 4–8 |
| `.gitignore` | Updated to exclude `.venv/`, `.direnv/`, `.env.local`, `runs/`, `volumes/` |
| `pyproject.toml` | Project manifest with hatchling build, deps declared per stage as optional extras |

### Schemas
| File | Description |
|---|---|
| `src/core/goal.py` | `OmegaGoal` Pydantic model — validates `goal_id`, `objective`, `context`, `technical_requirements`, `quality_thresholds`, `success_criteria`. Includes `load_goal(path)` YAML loader. |
| `src/state/schema.py` | `SDLCState` full Pydantic model — all fields for all 8 stages defined now, populated incrementally. Includes `FileChange`, `ToolEvidence`, `QualityThresholds`, `SDLCRequirement`. `SDLCState.from_goal(goal)` factory method. |
| `src/state/persistence.py` | `save_state(state)` and `load_state(run_id)` — serialize/deserialize `SDLCState` to `runs/{run_id}/state.json`. |

### Agents (stubs)
| File | Node | Behaviour in Stage 1 |
|---|---|---|
| `src/agents/nodes.py` | `tech_lead_node` | Derives stub `SDLCRequirement` list from `success_criteria`. Sets `architecture_doc` and `risk_level = "low"`. |
| | `dev_node` | Creates `FileChange` stubs (one per requirement) with empty hashes. No files written to disk yet. |
| | `qa_node` | Creates `FileChange` stubs for test files (one per requirement). |
| | `review_node` | Returns two passing `ToolEvidence` stubs (`ruff`, `pytest`). No subprocess calls yet. |
| | `release_engineer_node` | Validates traceability (req IDs covered by code and tests). Generates stub release notes. |
| | `supervisor_node` | Escalates to `human_review` if `loop_count >= 3` or `risk_level == "critical"`. Otherwise marks run complete. |

### Graph
| File | Description |
|---|---|
| `src/agents/graph.py` | `StateGraph` with fixed edges: `tech_lead → dev → qa → review → release_engineer → supervisor → END`. Conditional edge after `review`: failed gates + `loop_count < 3` → back to `dev`. State is converted `SDLCState ↔ dict` at each node boundary. |

### CLI
| File | Description |
|---|---|
| `main.py` | `python main.py --goal <path> --mode local`. Loads goal, provisions `volumes/{run_id}/`, initialises `SDLCState`, invokes graph, saves final state, exits `0` on `done` or `1` otherwise. `--mode temporal` stub returns error until Stage 7. |

### Goals
| File | Description |
|---|---|
| `config/goals/example_goal.yaml` | Simple todo-list REST API goal — used for all Stage 1 verification. |
| `config/goals/implement_auth.yaml` | JWT auth system for FastAPI — the primary production goal for later stages. |

---

## Verification Results

### Tests — 10 / 10 passed
```
pytest tests/test_stage1.py -v

tests/test_stage1.py::TestOmegaGoal::test_valid_goal                          PASSED
tests/test_stage1.py::TestOmegaGoal::test_goal_id_no_spaces                   PASSED
tests/test_stage1.py::TestOmegaGoal::test_empty_objective                     PASSED
tests/test_stage1.py::TestOmegaGoal::test_load_goal_from_yaml                 PASSED
tests/test_stage1.py::TestOmegaGoal::test_load_goal_full_yaml                 PASSED
tests/test_stage1.py::TestSDLCState::test_from_goal                           PASSED
tests/test_stage1.py::TestSDLCState::test_state_serialization_round_trip      PASSED
tests/test_stage1.py::TestOmegaGraph::test_mock_run_reaches_done              PASSED
tests/test_stage1.py::TestOmegaGraph::test_loop_cap_escalates_to_human_review PASSED
tests/test_stage1.py::TestPersistence::test_save_and_load                     PASSED

10 passed in 0.35s
```

### End-to-end mock run
```
python main.py --goal config/goals/example_goal.yaml --mode local

Goal validated: example-goal-001 — Build a simple REST API for a todo list.
[TechLead] Done. 3 requirements created.
[Dev]      Done. 3 files stubbed.
[QA]       Done. 3 test files stubbed.
[Review]   All gates passed.
[ReleaseEngineer] Done.
[Supervisor] Run complete.

Phase     : done
Loop count: 0
State     : runs/example-goal-001/state.json
```

---

## Runtime Environment
| Item | Value |
|---|---|
| Python | 3.10.12 (system — `/opt/miniforge/bin/bin/python3`) |
| Venv | `.venv/` — project-local, not committed |
| direnv | 2.37.1 — auto-activates on `cd` via `eval "$(direnv hook zsh)"` in `~/.zshrc` |
| PyPI override | `PIP_INDEX_URL=https://pypi.org/simple/` — bypasses corporate Nexus mirror |
| Key deps installed | `langgraph 0.2.x`, `pydantic 2.x`, `pyyaml 6.x`, `pytest`, `pytest-cov`, `ruff` |

---

## Known Limitations (to be resolved in later stages)
| Limitation | Resolved in |
|---|---|
| No real files written to `volumes/{run_id}/` — `hash` fields are empty strings | Stage 2 |
| `review_node` runs no actual tools — all evidence is hardcoded stubs | Stage 3 |
| No LLM calls — all agent logic is hardcoded | Stage 4 |
| Generated code runs on host if tools were real — no sandboxing | Stage 5 |
| No cross-run memory — `lessons_learned` is always empty | Stage 6 |
| No crash recovery — process kill loses in-flight state | Stage 7 |
| No audit logging, traceability comments, or `# Requirement: {id}` tagging | Stage 8 |

---

## How to Reproduce
```bash
# Clone and bootstrap (once)
cd AI_ORCHESTRATOR
./scripts/setup_env.sh

# Every subsequent cd auto-activates the venv via direnv
cd ../ && cd AI_ORCHESTRATOR

# Run tests
pytest tests/ -v

# Run a mock SDLC loop
python main.py --goal config/goals/example_goal.yaml --mode local

# Inspect output
cat runs/example-goal-001/state.json
```
