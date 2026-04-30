# Stage 4 Report — Real LLM Agents and Diagnostic Utility

**Status:** Complete | **Date:** 2026-04-30

---

## What Was Built

### New files

| File | Purpose |
|---|---|
| `config/llm.yaml` | LLM configuration: `main` section (code generation) and `diagnostic` section (failure analysis), both targeting `deepseek/deepseek-chat` |
| `src/core/llm.py` | `BaseLLM` protocol, `LiteLLMBackend` (real LiteLLM calls), `StubLLM` (deterministic fallback), `load_llm(section)` factory |
| `src/agents/diagnostic.py` | `DiagnosticUtility` — converts raw tool findings into structured fix-it instructions; called only on failing `ToolEvidence` entries |
| `tests/test_stage4.py` | 45 new tests covering all new components |

### Modified files

| File | Change |
|---|---|
| `src/agents/nodes.py` | All 6 nodes rewritten with real LLM calls; added `_extract_json` and `_diagnosis_context` helpers |
| `src/state/schema.py` | Added `supervisor_notes: Optional[str] = None` field to `SDLCState` |
| `pyproject.toml` | `litellm>=1.0` promoted from commented-out to active core dependency |
| `tests/conftest.py` | Added autouse `stub_load_llm` fixture that intercepts `load_llm` for all tests |

---

## Node-by-node changes

### `tech_lead_node`
- Sends objective, context, technical requirements, success criteria, and `lessons_learned` to the LLM.
- Expects a JSON response with a `requirements` list (`id`, `description`, `acceptance_criteria`) and an `architecture_doc` markdown string.
- Falls back to a single stub `REQ-001` if the LLM returns unparseable JSON.
- Sets `risk_level` based on keywords (`security`, `auth`, `payment`, `critical`, `production`) or requirement count (>5 → `medium`).

### `dev_node`
- Per-requirement LLM prompt includes: requirement text, acceptance criteria, architecture doc, and any prior `ToolEvidence.diagnosis` values from `gate_evidence` (repair loop context).
- Enforces `# Requirement: {id}` as the first line; prepends it automatically if the LLM omits it.
- Writes via `write_file` — interface unchanged from Stage 2.

### `qa_node`
- Per-requirement LLM prompt includes acceptance criteria and the implementation module name.
- Enforces `# Requirement: {id}` first line.
- System prompt instructs the LLM to use `sys.path.insert` to locate the source module — same structural requirement as the Stage 3 stub template.

### `review_node`
- All 6 subprocess tool calls are **unchanged** from Stage 3 (ruff, pytest, mypy, bandit, pip-audit, complexity).
- After tool execution, iterates over failing `ToolEvidence` entries and calls `DiagnosticUtility.diagnose(ev)` to populate `ev.diagnosis`.
- Diagnosis exceptions are caught; a fallback message is stored so the graph never crashes.
- Routing via `current_phase` is **unchanged** from Stage 3.

### `release_engineer_node`
- Safety block on required gate failures is **unchanged** from Stage 3.
- LLM generates `release_notes` from run ID, objective, requirements, files changed, and gate results.
- Falls back to a formatted plaintext summary if the LLM call fails.

### `supervisor_node`
- LLM generates a ≤100-word summary of the run, including gate results and diagnosis snippets, stored in `state.supervisor_notes`.
- Escalation logic (`loop_count >= 3` or `risk_level == "critical"`) is **unchanged** from Stage 3.
- LLM exception caught; fallback stored so graph never crashes.

---

## `StubLLM` — backward-compatibility strategy

When `DEEPSEEK_API_KEY` is absent or empty, `load_llm()` returns `StubLLM` instead of a real backend. `StubLLM` inspects the system prompt to identify which node called it and returns deterministic output that is structurally valid for that node:

| Caller (detected by system prompt) | Output |
|---|---|
| `tech_lead_node` | Valid JSON with one `REQ-001` requirement and an arch doc |
| `dev_node` | Python source with `# Requirement: {id}` tag and a stub function |
| `qa_node` | pytest file with `sys.path` setup and a `def test_` function |
| `release_engineer_node` | Short markdown release notes |
| `supervisor_node` | One-line summary string |
| `DiagnosticUtility` | Generic fix-it instruction |

This means Stages 1–3 tests run identically whether or not a real API key is set.

Additionally, `tests/conftest.py` gained an autouse pytest fixture `stub_load_llm` that monkeypatches `load_llm` in `src.agents.nodes` for every test. Individual Stage 4 tests that need controlled LLM output pass a `MockLLM(reply=...)` directly to the node function — which bypasses `load_llm` entirely.

---

## Tests

**Total passing: 94/94** (49 prior Stages 1–3 + 45 new Stage 4)

| Class | Tests | What is covered |
|---|---|---|
| `TestLLMModule` | 9 | Protocol conformance, `load_llm` factory (stub vs real vs missing section), `StubLLM` output for tech-lead / dev / qa roles |
| `TestDiagnosticUtility` | 3 | Failing evidence produces diagnosis; passing evidence is skipped; prompt content validation |
| `TestHelpers` | 7 | `_extract_json` (fenced, unfenced object, unfenced array, no-json fallback); `_diagnosis_context` (empty on pass, empty without diagnosis, with diagnosis) |
| `TestTechLeadNode` | 5 | Valid JSON parse, invalid JSON fallback, risk `high` on keyword, lessons in prompt, malformed requirements skipped |
| `TestDevNode` | 4 | Requirement tag present, tag prepended if missing, diagnosis injected in prompt, phase set to `testing` |
| `TestQaNode` | 2 | Test file written with tag, phase set to `review` |
| `TestReviewNodeStage4` | 3 | Diagnosis called only on failures, not called on all-pass, exception produces fallback message |
| `TestReleaseEngineerNodeStage4` | 3 | LLM release notes generated, fallback on LLM failure, block when required gate fails |
| `TestSupervisorNodeStage4` | 5 | Notes set, escalate on loop cap, escalate on critical risk, no escalation on clean run, fallback on LLM failure |
| `TestSchemaStage4` | 2 | `supervisor_notes` field present and serialises |
| `TestStage4EndToEnd` | 2 | Full graph all-gates-pass → `done`; always-failing gates → `human_review` after 3 loops |

---

## Key design decisions

- **Sync LLM calls:** `load_llm()` returns `LiteLLMBackend.chat()` which calls `litellm.completion()` (synchronous). No `asyncio.run()` wrapper needed because the LangGraph node wrapper in `graph.py` is already sync. Streaming deferred to Stage 8.
- **`diagnostic` LLM section:** `review_node` uses `load_llm("diagnostic")` for diagnosis calls, allowing a lighter/cheaper model to be substituted via config without touching code.
- **`supervisor_notes` vs `architecture_doc`:** A dedicated field was added rather than overwriting `architecture_doc` to keep concerns separate and avoid breaking Stage 1 state assertions.
- **`_extract_json` uses `JSONDecoder.raw_decode`:** Correctly extracts the outermost JSON object or array from prose — preventing the previous bug where an inner `[` array was found before the outer `{` object.
- **No code in state:** `dev_node` and `qa_node` still write only `FileChange` (path + hash) to state. Generated code lives exclusively in `volumes/{run_id}/`.

---

## Known limitations (deferred to later stages)

- Tools still run on host via subprocess (Docker sandboxing in Stage 5).
- No persistent cross-run memory (Stage 6).
- No Temporal crash-safety (Stage 7).
- LLM-generated code quality is not guaranteed to pass all gates on first attempt; the repair loop (up to 3 iterations) handles this.
