"""BaseLLM protocol and LiteLLM backend — Stage 4.

Usage
-----
    from src.core.llm import load_llm

    llm = load_llm("main")          # uses config/llm.yaml [main] section
    reply = llm.chat([{"role": "user", "content": "Hello"}])

When ``DEEPSEEK_API_KEY`` (or the configured ``api_key_env``) is absent,
``load_llm`` returns a ``StubLLM`` so that Stages 1-3 tests continue to pass
without a real API key.  Set the key to get real LiteLLM calls.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "llm.yaml"


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class BaseLLM(Protocol):
    """Minimal synchronous LLM interface used by all agent nodes."""

    def chat(self, messages: list[dict]) -> str:
        """Send *messages* to the model and return the text reply."""
        ...


# ---------------------------------------------------------------------------
# Stub backend (used when no API key is configured)
# ---------------------------------------------------------------------------

class StubLLM:
    """Deterministic stub that replicates the hardcoded Stage 1-3 node output.

    Identifies the calling node from the system prompt and produces minimal
    but structurally valid output so that Stage 1-3 tests never need a key.
    """

    def chat(self, messages: list[dict]) -> str:  # noqa: D401
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in messages if m["role"] == "user"), "")

        # --- tech_lead_node ---
        if "principal engineer" in system or ("JSON" in system and "requirements" in system):
            # Extract success criteria to build requirements list
            req_id = "REQ-001"
            description = "Implement the stated objective"
            acceptance = ["System meets the stated objective"]
            return json.dumps({
                "requirements": [
                    {"id": req_id, "description": description, "acceptance_criteria": acceptance}
                ],
                "architecture_doc": "# Architecture\n\nStub architecture (no API key set).",
            })

        # --- dev_node ---
        if "Python developer" in system or "production-ready Python code" in system:
            req_id_match = re.search(r"Requirement ID:\s*(\S+)", user)
            req_id = req_id_match.group(1) if req_id_match else "REQ-001"
            fn_name = req_id.lower().replace("-", "_")
            return (
                f"# Requirement: {req_id}\n"
                f"# Stub implementation (no API key set)\n"
                f"\n"
                f"def stub_{fn_name}():\n"
                f'    """Stub implementation."""\n'
                f"    pass\n"
            )

        # --- qa_node ---
        if "QA engineer" in system or "pytest test files" in system:
            req_id_match = re.search(r"Requirement ID:\s*(\S+)", user)
            req_id = req_id_match.group(1) if req_id_match else "REQ-001"
            fn_name = req_id.lower().replace("-", "_")
            module_name = fn_name + "_impl"
            return (
                f"# Requirement: {req_id}\n"
                f"import sys\n"
                f"from pathlib import Path\n"
                f"\n"
                f"sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))\n"
                f"\n"
                f"from {module_name} import stub_{fn_name}  # noqa: E402\n"
                f"\n"
                f"\n"
                f"def test_{fn_name}() -> None:\n"
                f'    """Stub test (no API key set)."""\n'
                f"    result = stub_{fn_name}()\n"
                f"    assert result is None\n"
            )

        # --- release_engineer_node ---
        if "release engineer" in system:
            run_id_match = re.search(r"Run ID:\s*(\S+)", user)
            run_id = run_id_match.group(1) if run_id_match else "unknown"
            return (
                f"# Release Notes — {run_id}\n\n"
                "Stub release notes (no API key set).\n"
            )

        # --- supervisor_node ---
        if "engineering manager" in system:
            return "Stub supervisor summary (no API key set). Run complete."

        # --- diagnostic ---
        if "quality-gate" in system or "findings" in system:
            return "Stub diagnosis (no API key set): review the raw findings and fix the identified issue."

        # Fallback
        return "Stub LLM response (no API key set)."


# ---------------------------------------------------------------------------
# LiteLLM backend
# ---------------------------------------------------------------------------

class LiteLLMBackend:
    """Thin wrapper around litellm.completion."""

    def __init__(self, model: str, api_key: str, temperature: float = 0.2, max_tokens: int = 4096) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat(self, messages: list[dict]) -> str:
        import litellm  # imported lazily so tests can run without it installed

        logger.debug("[LLM] chat → model=%s messages=%d", self.model, len(messages))
        response = litellm.completion(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        content: str = response.choices[0].message.content or ""
        logger.debug("[LLM] reply length=%d chars", len(content))
        return content


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def load_llm(section: str = "main", config_path: Path | None = None) -> BaseLLM:
    """Load LLM backend from *config/llm.yaml* using the named *section*.

    If the API key env-var is absent or empty, returns a :class:`StubLLM` so
    that Stages 1-3 tests pass without a real key.

    Parameters
    ----------
    section:
        Key in ``llm.yaml`` — ``"main"`` or ``"diagnostic"``.
    config_path:
        Override for the yaml file path (used in tests).
    """
    path = config_path or _CONFIG_PATH

    # If config file is absent, fall back to stub
    if not path.exists():
        logger.warning("[LLM] config not found at %s — using StubLLM.", path)
        return StubLLM()

    with open(path) as fh:
        cfg = yaml.safe_load(fh)

    if section not in cfg:
        raise KeyError(f"llm.yaml has no section '{section}'. Available: {list(cfg)}")

    sec = cfg[section]
    api_key_env = sec.get("api_key_env", "DEEPSEEK_API_KEY")
    api_key = os.environ.get(api_key_env, "")

    if not api_key:
        logger.warning(
            "[LLM] %s is not set — using StubLLM (set the key for real LLM calls).",
            api_key_env,
        )
        return StubLLM()

    return LiteLLMBackend(
        model=sec["model"],
        api_key=api_key,
        temperature=sec.get("temperature", 0.2),
        max_tokens=sec.get("max_tokens", 4096),
    )
