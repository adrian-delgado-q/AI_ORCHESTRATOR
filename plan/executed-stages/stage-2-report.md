# Stage 2 — Real Local Workspace and File Tracking
**Status:** Complete  
**Date:** 2026-04-30  

---

## What Was Built

### New Files
| File | Description |
|---|---|
| `src/io/__init__.py` | Package init (empty) |
| `src/io/workspace.py` | `write_file(run_id, path, content, requirement_id, rationale) -> FileChange` and `read_file(run_id, path) -> str`. Writes to `volumes/{run_id}/{path}`, creates parent dirs, computes `sha256` hash of UTF-8 content. Exposes `VOLUMES_DIR = Path("volumes")` constant for test patching. |
| `tests/test_stage2.py` | 12 new tests — see breakdown below. |

### Modified Files
| File | Change |
|---|---|
| `src/agents/nodes.py` | `dev_node` replaced empty-hash `FileChange` stubs with real `write_file()` calls — writes one `.py` source file per requirement to `volumes/{run_id}/src/`. `qa_node` same pattern — writes one `test_*.py` per requirement to `volumes/{run_id}/tests/`. Module docstring updated to Stage 2. Top-level import of `write_file` replaces the inline `from src.state.schema import FileChange` inside each node. |

---

## Verification Results

### Tests — 22 / 22 passed
```
pytest tests/test_stage1.py tests/test_stage2.py -v

tests/test_stage1.py::TestOmegaGoal::test_valid_goal                                        PASSED
tests/test_stage1.py::TestOmegaGoal::test_goal_id_no_spaces                                 PASSED
tests/test_stage1.py::TestOmegaGoal::test_empty_objective                                   PASSED
tests/test_stage1.py::TestOmegaGoal::test_load_goal_from_yaml                               PASSED
tests/test_stage1.py::TestOmegaGoal::test_load_goal_full_yaml                               PASSED
tests/test_stage1.py::TestSDLCState::test_from_goal                                         PASSED
tests/test_stage1.py::TestSDLCState::test_state_serialization_round_trip                    PASSED
tests/test_stage1.py::TestOmegaGraph::test_mock_run_reaches_done                            PASSED
tests/test_stage1.py::TestOmegaGraph::test_loop_cap_escalates_to_human_review               PASSED
tests/test_stage1.py::TestPersistence::test_save_and_load                                   PASSED
tests/test_stage2.py::TestWorkspaceIO::test_write_file_creates_file                         PASSED
tests/test_stage2.py::TestWorkspaceIO::test_write_file_returns_file_change_with_hash        PASSED
tests/test_stage2.py::TestWorkspaceIO::test_write_file_hash_is_correct_sha256               PASSED
tests/test_stage2.py::TestWorkspaceIO::test_read_file_round_trips_content                   PASSED
tests/test_stage2.py::TestWorkspaceIO::test_read_file_missing_raises                        PASSED
tests/test_stage2.py::TestWorkspaceIO::test_write_file_creates_parent_dirs                  PASSED
tests/test_stage2.py::TestNodeFileWrites::test_dev_node_populates_files_changed_with_hashes PASSED
tests/test_stage2.py::TestNodeFileWrites::test_dev_node_writes_files_to_volume              PASSED
tests/test_stage2.py::TestNodeFileWrites::test_qa_node_populates_tests_written_with_hashes  PASSED
tests/test_stage2.py::TestNodeFileWrites::test_qa_node_writes_test_files_to_volume          PASSED
tests/test_stage2.py::TestStage2EndToEnd::test_graph_run_writes_real_files                  PASSED
tests/test_stage2.py::TestStage2EndToEnd::test_state_json_has_no_code_blobs                 PASSED

22 passed in 0.43s
```

### End-to-end run
```
python main.py --goal config/goals/example_goal.yaml --mode local

12:03:32  INFO      Goal validated: example-goal-001 — Build a simple REST API for a todo list.
12:03:32  INFO      [TechLead] Done. 3 requirements created.
12:03:32  INFO      [Dev]      Done. 3 files written.
12:03:32  INFO      [QA]       Done. 3 test files written.
12:03:32  INFO      [Review]   All gates passed.
12:03:32  INFO      [ReleaseEngineer] Done.
12:03:32  INFO      [Supervisor] Run complete.

Phase     : done
Loop count: 0
State     : runs/example-goal-001/state.json
```

### Volume output
```
ls volumes/example-goal-001/src/
req_001_impl.py  req_002_impl.py  req_003_impl.py

ls volumes/example-goal-001/tests/
test_req_001.py  test_req_002.py  test_req_003.py
```

### State lean check (no code blobs)
```
python -c "
import json
s = json.load(open('runs/example-goal-001/state.json'))
for f in s['files_changed']:
    print(f['path'], f['hash'][:16] + '…')
"

src/req_001_impl.py  22bfcfbf8a972fb1…
src/req_002_impl.py  97a8436d5b02d405…
src/req_003_impl.py  20e627e91928ed50…
```

`files_changed` and `tests_written` entries contain only `path`, `requirement_id`, `rationale`, and `hash` — no `content` key, no code blobs in state.

---

## Runtime Environment
| Item | Value |
|---|---|
| Python | 3.10.12 |
| New deps | None — `hashlib` and `pathlib` are stdlib |
| `VOLUMES_DIR` | `Path("volumes")` — monkeypatchable in tests |

---

## Known Limitations (to be resolved in later stages)
| Limitation | Resolved in |
|---|---|
| File content is hardcoded stubs — not generated from goal context | Stage 4 |
| `review_node` runs no actual tools — all gate evidence is still hardcoded | Stage 3 |
| No LLM calls — all agent logic is hardcoded | Stage 4 |
| Generated code runs on host if tools were real — no sandboxing | Stage 5 |
| No cross-run memory — `lessons_learned` is always empty | Stage 6 |
| No crash recovery | Stage 7 |

---

## How to Reproduce
```bash
cd AI_ORCHESTRATOR

# Run all tests (Stage 1 + Stage 2)
pytest tests/test_stage1.py tests/test_stage2.py -v

# Run a full SDLC loop
python main.py --goal config/goals/example_goal.yaml --mode local

# Inspect generated files on volume
ls volumes/example-goal-001/src/
ls volumes/example-goal-001/tests/

# Confirm state carries only paths + hashes
cat runs/example-goal-001/state.json | python -m json.tool | grep -A4 '"files_changed"'
```
