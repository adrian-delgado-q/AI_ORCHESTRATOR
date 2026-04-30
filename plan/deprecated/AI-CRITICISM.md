I want you to implement this project using an incremental development strategy.

The goal is to build an SDLC multi-agent orchestrator, but I do not want the first version to include the full production stack. The original architecture includes LangGraph, Temporal, Redis, Zep/Graphiti, Docker sandboxing, LiteLLM/DeepSeek, per-run workspaces, diagnostic agents, and release gates. That is the final direction, not the first implementation target.

Your job is to build this in clear stages that are meaningful, testable, and not too granular. Each stage should produce a working system that can be run locally and validated before moving to the next stage.

Use this development strategy:

Stage 1: Local SDLC Loop Skeleton

Build the minimum local version of the system.

Include:
- OmegaGoal YAML schema and loader
- SDLCState Pydantic schema
- Six agent node stubs:
  - supervisor_node
  - tech_lead_node
  - dev_node
  - qa_node
  - review_node
  - release_engineer_node
- A simple LangGraph flow connecting the nodes
- A local run command:
  python main.py --goal config/goals/example_goal.yaml --mode local
- State persisted to:
  runs/{run_id}/state.json
- Generated files written to:
  volumes/{run_id}/

Do not add Temporal, Redis, Zep, Graphiti, or Docker yet.

Success criteria:
- A goal YAML file can be loaded and validated.
- The graph runs from planning to done.
- Each node updates SDLCState.
- State is written to disk.
- The system can complete a fake/mock run end to end.

Stage 2: Real Local Workspace and File Tracking

Replace fake file behavior with real local file operations.

Include:
- write_file(run_id, path, content)
- read_file(run_id, path)
- sha256 hashing for every written file
- FileChange objects containing:
  - path
  - requirement_id
  - rationale
  - hash
- Generated code should live only in volumes/{run_id}/
- SDLCState should store paths and hashes, not code blobs.

Success criteria:
- dev_node writes at least one real source file.
- qa_node writes at least one real test file.
- files_changed and tests_written are populated correctly.
- State JSON stays small because it does not contain raw code.

Stage 3: Deterministic Review Gates Locally

Add local deterministic tooling before adding Docker.

Include:
- ruff
- pytest
- mypy, optional if typing is enabled
- bandit, optional for security checks
- pip-audit, optional if dependency files exist
- complexity check, optional but keep interface ready
- ToolEvidence schema for each tool result

The review_node should run these tools locally using subprocess for now. The release_engineer_node should block done status if required gates fail.

Success criteria:
- review_node produces ToolEvidence records.
- Failed tools update gate_evidence.
- Passing tools allow the flow to continue.
- The system can identify whether a run is pass, fail, or needs another loop.

Stage 4: Real LLM Agents and Diagnostic Utility

Replace stubbed agent behavior with real LiteLLM/DeepSeek calls.

Include:
- BaseLLM wrapper
- config/llm.yaml
- main_llm
- diagnostic_llm
- diagnostic utility that summarizes failing ToolEvidence into actionable instructions
- dev_node should receive prior diagnoses when fixing code
- loop_count should increment on failed review cycles
- hard cap after 3 loops, then current_phase should become human_review

Do not add Temporal yet.

Success criteria:
- tech_lead_node can generate requirements from a goal.
- dev_node can generate or modify code.
- qa_node can generate tests.
- review_node can diagnose failures.
- dev_node can use diagnosis output to attempt a fix.
- The system stops safely after 3 failed loops.

Stage 5: Docker Sandbox

Move tool execution into Docker.

Include:
- SandboxManager
- create_sandbox(run_id)
- exec_in_sandbox(container_ref, cmd)
- destroy_sandbox(container_ref)
- network disabled by default
- workspace mounted safely
- tools run inside ephemeral containers
- no generated code should execute directly on the host

Success criteria:
- pytest, ruff, and other tools execute inside Docker.
- Containers are destroyed after use.
- The host machine is not used to execute generated code.
- The same ToolEvidence interface still works.

Stage 6: Memory Layer

Add memory after the local SDLC loop is stable.

Start simple:
- local lessons_learned.json

Then add:
- Zep / Graphiti adapter
- positive lessons
- negative constraints
- retrieval of relevant prior lessons during tech_lead_node

Success criteria:
- release_engineer_node writes lessons learned.
- tech_lead_node can retrieve lessons from prior runs.
- lessons_learned are injected into SDLCState.
- The system can avoid repeating known failure patterns.

Stage 7: Temporal Orchestration

Only after the local mode works, add Temporal.

Include:
- Temporal workflow
- Temporal activities wrapping existing functions
- workflow ID = goal_id
- activity retry policies
- HITL via Temporal signal and workflow.wait_condition
- local mode must continue to work without Temporal

Success criteria:
- The same goal can run in local mode or Temporal mode.
- Temporal can recover from worker failure.
- Long waits for human review are handled by Temporal, not LangGraph.
- Existing agent logic is reused, not rewritten.

Stage 8: Production Hardening

Add final reliability and governance features.

Include:
- Redis only if needed for fast state lookup
- stronger audit logging
- release notes
- requirement traceability check using # Requirement: {id}
- validation that every requirement appears in code and tests
- config-driven quality thresholds
- provider swapping through config
- stronger error handling
- CLI commands for inspect, resume, and clean

Success criteria:
- The system is auditable.
- Runs are reproducible.
- Failed runs are explainable.
- Local and Temporal modes both remain usable.

Important development rules:

1. Do not build everything at once.
2. Do not add Temporal before the local loop works.
3. Do not add Redis or Zep before the core workflow is stable.
4. Do not store generated code inside SDLCState.
5. Do not execute generated code directly on the host once Docker sandboxing exists.
6. Every stage must include tests or a runnable verification command.
7. Keep interfaces stable so later stages replace implementations instead of rewriting the system.
8. Prefer simple local files before distributed infrastructure.
9. After each stage, provide:
   - what was implemented
   - how to run it
   - how to test it
   - known limitations
   - what the next stage should add

Start by implementing Stage 1 only.