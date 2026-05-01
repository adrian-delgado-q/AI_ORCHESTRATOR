# Setup

## Prerequisites

| Requirement | Minimum version | Notes |
|-------------|-----------------|-------|
| Python | 3.10 | 3.11 or 3.12 recommended |
| Docker | 20.x | Required for sandbox mode |
| Git | any | |
| LLM API key | — | DeepSeek by default; any [LiteLLM](https://docs.litellm.ai/docs/providers) provider works |

---

## 1. Clone the repository

```bash
git clone git@github.com:adrian-delgado-q/AI_ORCHESTRATOR.git
cd AI_ORCHESTRATOR
```

---

## 2. Run the one-shot setup script

```bash
bash scripts/setup_env.sh
```

This will:

- Create a `.venv` virtual environment
- Upgrade pip / setuptools / wheel
- Install the project in editable mode with `dev` extras
- Run `direnv allow` if `direnv` is available
- Create a `.env.local` template

---

## 3. Add your LLM API key

Open `.env.local` (created by the setup script) and uncomment the key for your provider:

```bash
# .env.local
DEEPSEEK_API_KEY=your_key_here
```

Then export it in your shell (or let `direnv` handle it automatically):

```bash
export DEEPSEEK_API_KEY=your_key_here
```

---

## 4. Build the Docker sandbox image

```bash
make docker-build
```

This builds `omega-python-runner` from `docker/Dockerfile.python-runner`. The image is used to run `ruff`, `pytest`, `mypy`, `bandit`, and `pip-audit` inside an isolated container.

Verify the image is ready:

```bash
make docker-check
```

---

## 5. Install all optional extras (recommended for full functionality)

```bash
make install-all
```

This installs `dev`, `stage3`, `stage4`, and `stage5` extras (ruff, pytest, mypy, bandit, pip-audit, litellm, docker SDK).

---

## 6. Run the example goal

```bash
make run-example
```

Or using the CLI directly:

```bash
python main.py --goal config/goals/example_goal.yaml --mode local
```

Expected output ends with:

```
Run complete.
  Phase     : done
  Loop count: 1
  State     : runs/example-goal-001/state.json
```

---

## Troubleshooting

??? question "Permission error on `.deps` directory"
    Docker writes `.deps` as root. The orchestrator detects this and marks the directory stale so it reinstalls on the next run. No action needed.

??? question "`omega-python-runner` image not found"
    Run `make docker-build` first. The sandbox requires the local image; it does not pull from a registry.

??? question "Missing API key at runtime"
    Ensure `DEEPSEEK_API_KEY` (or your chosen provider's key) is exported before running. Check `config/llm.yaml` for the `api_key_env` field to confirm the variable name.
