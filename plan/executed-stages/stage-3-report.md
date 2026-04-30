# Stage 3 — Deterministic Review Gates (Local subprocess)
**Status:** Complete  
**Date:** 2026-04-30  

---

## What Was Built

### New Files
| File | Description |
|---|---|
| `src/tools/__init__.py` | Package init — re-exports all six runner functions. |
| `src/tools/runners.py` | Six tool wrappers, each returning `ToolEvidence`. Module-level `VOLUMES_DIR` constant monkeypatchable in tests (mirrors `src/io/workspace.py` pattern). See table below. |
| `tests/test_stage3.py` | 27 new tests across five test classes — see breakdown below. |

### Modified Files
| File | Change |
|---|---|
| `src/agents/nodes.py` | Module docstring updated to Stage 3. Added imports for all six runner functions. `review_node` replaced stub evidence with real tool calls; routing is now driven by required-gate failures only (`ruff`, `pytest`). `release_engineer_node` gained a safety-net block: if required gate evidence is still failing when RE is reached it sets `current_phase = "implementation"` (without incrementing `loop_count`; `review_node` owns that) and returns, allowing `supervisor_node` to escalate. `qa_node` stub test template updated to import and call the source stub (ensures ≥ 80 % coverage on real runs). `_REQUIRED_TOOLS` set defined at module level. |
| `src/agents/graph.py` | `_route_after_review` rewritten to route on `state.current_phase` (set authoritatively by `review_node`) instead of re-scanning all gate evidence. This keeps the required-vs-optional gate distinction in a single place and avoids optional-tool failures incorrectly triggering a dev loop. |
| `tests/test_stage2.py` | Two Stage 2 E2E tests updated to also monkeypatch `src.tools.runners.VOLUMES_DIR` — required now that `review_node` calls real tools that read from `VOLUMES_DIR`. |

---

### Tool Wrappers (`src/tools/runners.py`)
| Function | Tool | Required | Skip condition | Implementation |
|---|---|---|---|---|
| `run_ruff(run_id)` | ruff | ✓ | — | `ruff check volumes/{run_id}/` |
| `run_pytest(run_id, min_coverage)` | pytest + pytest-cov | ✓ | — | `pytest volumes/{run_id}/tests/ --cov=… --cov-fail-under={n}` |
| `run_mypy(run_id, enforce)` | mypy | — | `enforce=False` | `mypy volumes/{run_id}/src/ --ignore-missing-imports` |
| `run_bandit(run_id)` | bandit | — | — | `bandit -r volumes/{run_id}/src/ -q` |
| `run_pip_audit(run_id)` | pip-audit | — | no `requirements.txt` | `pip-audit -r volumes/{run_id}/requirements.txt` |
| `run_complexity_check(run_id, max_complexity)` | (stub) | — | — | Always returns passing ToolEvidence; real radon/xenon in Stage 5 |

---

## Verification Results

### Tests — 49 / 49 passed

```
pytest tests/test_stage1.py tests/test_stage2.py tests/test_stage3.py -v

tests/test_stage1.py  — 10 / 10 PASSED
tests/test_stage2.py  — 12 / 12 PASSED
tests/test_stage3.py  — 27 / 27 PASSED

49 passed in 6.72s
```

#### Stage 3 test breakdown
| Class | Count | What is tested |
|---|---|---|
| `TestToolRunnersUnit` | 12 | Each runner returns `ToolEvidence`; pass/fail paths driven by mocked `subprocess.run`; skip conditions for `mypy` (`enforce=False`) and `pip-audit` (no requirements.txt). |
| `TestReviewNodeGating` | 7 | `review_node` calls tools and populates `gate_evidence`; required-gate failure routes to `implementation`; optional failures do not block release; `loop_count` increments only on required failures; successive failures accumulate correctly. |
| `TestReleaseEngineerBlock` | 3 | RE blocks completion when required gate still failing; proceeds to `done` when all required pass; passes cleanly with empty evidence. |
| `TestGraphLoopRouting` | 3 | Full graph with mocked tools: all-pass → `done` in loop 0; always-fail → `human_review` after cap; `loop_count == 3` exactly on cap. |
| `TestStage3EndToEndReal` | 2 | Full graph with **real subprocess** tools on real stub files; reaches `done` in loop 0; gate evidence contains real tool output strings (not the old stub strings). |

---

### End-to-end mock run

```
python main.py --goal config/goals/example_goal.yaml --mode local

12:21:33  INFO      Loading goal from: config/goals/example_goal.yaml
12:21:33  INFO      Goal validated: example-goal-001 — Build a simple REST API for a todo list.
12:21:33  INFO      Volume directory provisioned: volumes/example-goal-001
12:21:33  INFO      ────────────────────────────────────────────────────────────
12:21:33  INFO      Starting Omega graph for run_id=example-goal-001
12:21:33  INFO      ────────────────────────────────────────────────────────────
12:21:33  INFO      [TechLead] Done. 3 requirements created.
12:21:33  INFO      [Dev]      Done. 3 files written.
12:21:33  INFO      [QA]       Done. 3 test files written.
12:21:33  INFO      [Review]   Running quality gates...
12:21:33  INFO      [ruff]     PASS
12:21:34  INFO      [pytest]   PASS (coverage >= 80%)
12:21:34  INFO      [mypy]     Skipped — enforce_type_hints=false.
12:21:34  INFO      [bandit]   PASS
12:21:34  INFO      [pip-audit] Skipped — no requirements.txt present.
12:21:34  INFO      [complexity] Stub — always passes in Stage 3 (threshold=10).
12:21:34  INFO      [Review]   All required gates passed.
12:21:34  INFO      [ReleaseEngineer] Done.
12:21:34  INFO      [Supervisor] Run complete.

Phase     : done
Loop count: 0
State     : runs/example-goal-001/state.json
```

### Gate evidence in state.json (6 records)
| tool_name | passed | findings (truncated) |
|---|---|---|
| ruff | true | `No lint issues found.` |
| pytest | true | `3 passed, …  TOTAL   … 100%  …` |
| mypy | true | `Skipped — enforce_type_hints=false.` |
| bandit | true | `No issues identified.` |
| pip-audit | true | `Skipped — no requirements.txt present.` |
| complexity | true | `Stub — real check in Stage 5 (max_complexity=10).` |

---

## Design decisions made / deviations from plan

| Decision | Rationale |
|---|---|
| `_route_after_review` routed on `current_phase` not raw evidence | The plan distinguishes required vs optional tools. Routing on all evidence would have caused optional-tool failures (e.g. mypy) to trigger a dev loop. Centralising the pass/fail decision in `review_node` and having the router trust it keeps the logic in one place. |
| `release_engineer_node` does NOT increment `loop_count` | RE is a safety net. `review_node` already incremented the counter for the cycle that failed. If RE double-incremented, the final count would be 4 instead of 3 and tests relying on the cap being exactly 3 would fail. |
| `qa_node` stub test template updated to import and call source | Stub tests that only contain `pass` give 0 % coverage. The updated template adds a `sys.path.insert` shim and calls the stub function, achieving 100 % coverage on stub source files without changing the YAML thresholds. |

---

## Runtime Environment
| Item | Value |
|---|---|
| Python | 3.10.12 |
| Venv | `.venv/` — project-local |
| ruff | installed via `.[stage3]` |
| pytest + pytest-cov | installed via `.[stage3]` |
| mypy | installed via `.[stage3]` |
| bandit | installed via `.[stage3]` |
| pip-audit | installed via `.[stage3]` |

---

## Known limitations (as planned)
- `dev_node` still writes hardcoded stubs — real LLM-generated code in Stage 4.
- Tools run on host via subprocess — Docker isolation in Stage 5.
- No LLM diagnosis of failures — `ToolEvidence.diagnosis` populated in Stage 4.
- `run_complexity_check` is a stub — real radon/xenon integration in Stage 5.
