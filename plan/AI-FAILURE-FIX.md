# Speed Up Local Orchestrator Execution

## Summary

Improve current local-mode performance before Temporal by reducing serial waits, Docker startup churn, and unnecessary LLM calls while preserving sandboxed execution and required gate correctness.

Primary targets:
- Parallelize independent per-requirement LLM calls.
- Reuse one sandbox container per review cycle.
- Cache dependency installs by `requirements.txt` hash.
- Avoid slow optional work when required gates already failed.
- Add timing traces so future regressions are visible.

## Key Changes

- Add lightweight run timing instrumentation around graph nodes, LLM calls, dependency install, and each review gate. Persist timing data under `runs/{run_id}/timings.json` without changing generated project files.
- Update `dev_node` and `qa_node` to dispatch per-requirement LLM calls concurrently using a bounded worker pool, default `OMEGA_LLM_CONCURRENCY=4`. Preserve deterministic output ordering by requirement ID.
- Replace the LLM-based `requirements.txt` inference with a deterministic Python import scanner plus a small package-name mapping for common third-party imports. Keep an override path for future stack-specific planners.
- Change review execution to create one network-disabled sandbox per review cycle and run all enabled gates inside it, instead of creating one container per tool.
- Store a hash marker for `requirements.txt`; only reinstall `.deps` when the hash changes or `.deps-stale` exists.
- Run required gates first. If `ruff` or `pytest` fails, skip optional gates by default for retry loops and diagnose only required failures. Add a config flag to run full optional review when desired.
- Raise local sandbox CPU quota from 50% to one full CPU by default, with a config/env override for stricter isolation.

## Public Interfaces

- Add optional config/env knobs:
  - `OMEGA_LLM_CONCURRENCY`, default `4`
  - `OMEGA_REVIEW_FULL_ON_REQUIRED_FAILURE`, default `false`
  - `OMEGA_SANDBOX_CPU_QUOTA`, default one CPU
- No CLI behavior changes required.
- Existing `--no-sandbox` and `--resume` behavior remains unchanged.

## Test Plan

- Add tests proving `dev_node` and `qa_node` preserve requirement order under concurrent fake LLM responses.
- Add dependency-cache tests: unchanged `requirements.txt` skips install; changed hash reinstalls.
- Add review-runner tests proving one sandbox lifecycle can execute multiple gates and still cleans up on exceptions.
- Add routing tests proving required gate failures still send the graph back to implementation.
- Run the existing stage suite to confirm current behavior remains compatible.

## Assumptions

- Optimize local mode now rather than waiting for Stage 8 Temporal.
- Keep Docker sandboxing enabled by default.
- Required gates remain authoritative; optional gates may be deferred during failing retry loops for speed.
