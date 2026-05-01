# Goal YAML Reference

Goal files live in `config/goals/`. Pass any goal file to the CLI via `--goal`.

---

## Full annotated example

```yaml
# config/goals/example_goal.yaml

# Unique identifier for this run. Used as the run directory name under
# runs/ and volumes/. Must be a valid directory name.
goal_id: "example-goal-001"

# Plain-English description of what to build.
objective: "Build a simple REST API for a todo list."

# Optional background that helps the tech_lead agent understand constraints.
context: "Standalone FastAPI app, no database required for this example."

# Explicit technical constraints. Each item becomes a discrete requirement
# that the dev agent implements and the QA agent validates separately.
technical_requirements:
  - "Use FastAPI with Pydantic models."
  - "Endpoints must be async."

# Quality gate thresholds enforced by the review node.
quality_thresholds:
  max_cyclomatic_complexity: 10   # radon / xenon threshold (Stage 5+)
  min_test_coverage: 80           # pytest-cov minimum %
  enforce_type_hints: false       # whether mypy failures block the pass gate

# Natural-language success criteria. The tech_lead uses these as acceptance
# conditions when decomposing requirements.
success_criteria:
  - "User can create a new todo item."
  - "User can list all todo items."
  - "User can delete a todo item by ID."
```

---

## Field reference

### `goal_id` *(required)*

**Type:** string  
**Rules:** Must be unique across active runs. Becomes the directory name under `runs/` and `volumes/`. Use lowercase letters, digits, and hyphens.

---

### `objective` *(required)*

**Type:** string  
Plain-English description of what to build. This is the primary prompt for the `tech_lead` agent.

---

### `context` *(optional)*

**Type:** string  
Additional background passed to the `tech_lead`. Use this for architectural constraints, stack preferences, or scope boundaries.

---

### `technical_requirements` *(optional)*

**Type:** list[string]  
Explicit requirements that must be implemented. If omitted, the `tech_lead` agent infers requirements from `objective` and `success_criteria`. When provided, these are injected directly and the agent supplements rather than replaces them.

---

### `quality_thresholds` *(optional)*

**Type:** object

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_cyclomatic_complexity` | int | 10 | Maximum cyclomatic complexity per function (radon/xenon, Stage 5+) |
| `min_test_coverage` | int | 80 | Minimum pytest-cov line coverage (%) |
| `enforce_type_hints` | bool | false | Whether mypy failures are required (blocking) gates |

---

### `success_criteria` *(optional)*

**Type:** list[string]  
Acceptance conditions framed as user-visible behaviours. The `tech_lead` uses these to validate that generated requirements are complete.

---

## Creating a new goal

```bash
cp config/goals/example_goal.yaml config/goals/my_goal.yaml
# edit my_goal.yaml
python main.py --goal config/goals/my_goal.yaml --mode local
```

!!! tip "Unique `goal_id` is important"
    If you reuse a `goal_id`, `--resume` will load the previous run's state. Use a fresh ID for an unrelated goal.
