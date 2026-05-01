# Lean Omega — AI SDLC Orchestrator

> A multi-agent, LangGraph-powered orchestrator that drives an entire software development lifecycle — from goal definition to tested, sandboxed code — with deterministic review gates and Docker-isolated tool execution.

[![CI](https://github.com/adrian-delgado-q/AI_ORCHESTRATOR/actions/workflows/ci.yml/badge.svg)](https://github.com/adrian-delgado-q/AI_ORCHESTRATOR/actions/workflows/ci.yml)
[![Docs](https://github.com/adrian-delgado-q/AI_ORCHESTRATOR/actions/workflows/docs.yml/badge.svg)](https://adrian-delgado-q.github.io/AI_ORCHESTRATOR/)

## What it does

You write a **goal YAML** — the system plans, implements, tests, reviews, and iterates autonomously until the code passes all quality gates or escalates to human review.

```yaml
# config/goals/example_goal.yaml
goal_id: "example-goal-001"
objective: "Build a simple REST API for a todo list."
technical_requirements:
  - "Use FastAPI with Pydantic models."
  - "Endpoints must be async."
```

```bash
python main.py --goal config/goals/example_goal.yaml --mode local
```

## Quick start

```bash
# 1. Clone and set up the environment
bash scripts/setup_env.sh

# 2. Add your LLM API key
echo "DEEPSEEK_API_KEY=your_key_here" >> .env.local

# 3. Build the Docker sandbox image
make docker-build

# 4. Run
make run-example
```

> Full setup instructions, architecture deep-dive, and configuration reference live in the [documentation](https://adrian-delgado-q.github.io/AI_ORCHESTRATOR/).

## Documentation

| Page | Description |
|------|-------------|
| [Setup](https://adrian-delgado-q.github.io/AI_ORCHESTRATOR/setup/) | Environment setup & prerequisites |
| [Architecture](https://adrian-delgado-q.github.io/AI_ORCHESTRATOR/architecture/) | Agent graph, stages, and design decisions |
| [Goal YAML reference](https://adrian-delgado-q.github.io/AI_ORCHESTRATOR/goal-yaml/) | All goal fields explained |
| [Configuration](https://adrian-delgado-q.github.io/AI_ORCHESTRATOR/configuration/) | LLM config, env vars, sandbox options |
| [Running](https://adrian-delgado-q.github.io/AI_ORCHESTRATOR/running/) | CLI flags, `make` targets, resume & sandbox |
| [Stages](https://adrian-delgado-q.github.io/AI_ORCHESTRATOR/stages/) | Roadmap — stages 1–9 |
| [Contributing](https://adrian-delgado-q.github.io/AI_ORCHESTRATOR/contributing/) | Dev workflow, tests, and conventions |

## Requirements

- Python ≥ 3.10
- Docker (for sandbox isolation)
- A [DeepSeek](https://platform.deepseek.com/) API key (or any LiteLLM-compatible provider)

## License

[MIT](LICENSE)