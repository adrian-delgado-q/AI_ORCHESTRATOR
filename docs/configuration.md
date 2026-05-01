# Configuration

## LLM configuration — `config/llm.yaml`

```yaml
# config/llm.yaml
main:
  model: deepseek/deepseek-v4-flash  # any LiteLLM model string
  api_key_env: DEEPSEEK_API_KEY       # env var that holds the key
  temperature: 0.2
  max_tokens: 4096

diagnostic:
  model: deepseek/deepseek-v4-flash
  api_key_env: DEEPSEEK_API_KEY
  temperature: 0.0
  max_tokens: 1024
```

The `main` profile is used by all agent nodes. The `diagnostic` profile is used by `DiagnosticUtility` when analysing tool failures. Both are independently configurable.

### Switching LLM providers

Because the backend is [LiteLLM](https://docs.litellm.ai/docs/providers), you can swap the provider by changing the `model` string without touching any code.

=== "OpenAI"
    ```yaml
    main:
      model: gpt-4o
      api_key_env: OPENAI_API_KEY
    ```

=== "Anthropic"
    ```yaml
    main:
      model: claude-3-5-sonnet-20241022
      api_key_env: ANTHROPIC_API_KEY
    ```

=== "Local / Ollama"
    ```yaml
    main:
      model: ollama/llama3.2
      api_key_env: ""   # no key needed for local
    ```

---

## Environment variables

Managed in `.env.local` (never committed to git). Template created automatically by `scripts/setup_env.sh`.

| Variable | Required | Stage | Description |
|----------|----------|-------|-------------|
| `DEEPSEEK_API_KEY` | Yes* | 4 | API key for the default LLM provider |
| `ZEP_API_KEY` | No | 6 | Zep / Graphiti memory store |
| `ZEP_API_URL` | No | 6 | Zep API endpoint |
| `TEMPORAL_HOST` | No | 7 | Temporal service address (`host:port`) |
| `TEMPORAL_NAMESPACE` | No | 7 | Temporal namespace (default: `default`) |
| `REDIS_URL` | No | 8 | Redis connection URL |

\* Required only when running with the real LLM backend. Tests use `StubLLM` and do not need a key.

---

## Sandbox configuration

The Docker sandbox is **enabled by default**. It requires the `omega-python-runner` image to be built locally:

```bash
make docker-build
```

### Disabling the sandbox

```bash
# Via CLI flag
python main.py --goal ... --no-sandbox

# Via make target
make run-no-sandbox
```

!!! warning "No-sandbox mode"
    When `--no-sandbox` is set, tool commands (`ruff`, `pytest`, `mypy`, `bandit`, `pip-audit`) run directly on the host Python environment. Only use this for local development with a trusted codebase.

### Docker image

| Setting | Value |
|---------|-------|
| Image name | `omega-python-runner` |
| Dockerfile | `docker/Dockerfile.python-runner` |
| Registry | `ghcr.io/adrian-delgado-q/AI_ORCHESTRATOR/omega-python-runner` (published by CI on `main`) |

---

## Python dependency extras

```bash
pip install -e ".[dev]"          # ruff, pytest, pytest-cov
pip install -e ".[stage3]"       # + mypy, bandit, pip-audit
pip install -e ".[stage4]"       # + litellm
pip install -e ".[stage5]"       # + docker SDK
pip install -e ".[dev,stage3,stage4,stage5]"  # everything (make install-all)
```
