"""pytest configuration — adds project root to sys.path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def stub_load_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace load_llm with StubLLM for every test by default.

    This prevents accidental real API calls when DEEPSEEK_API_KEY is set in
    the environment.  Tests that need a specific LLM either:
      - Pass ``llm=MockLLM(...)`` directly to the node function (never calls
        load_llm at all), OR
      - Call ``monkeypatch.setattr(nodes_mod, "load_llm", ...)`` in the test
        body, which overrides this autouse fixture.
    """
    from src.core.llm import StubLLM
    import src.agents.nodes as nodes_mod

    monkeypatch.setattr(nodes_mod, "load_llm", lambda *args, **kwargs: StubLLM())


@pytest.fixture(autouse=True)
def disable_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable Docker sandbox for all tests.

    Stage 5 tests that need the real sandbox path mock SandboxManager directly.
    This fixture prevents any test from accidentally spawning Docker containers.
    """
    import src.tools.runners as runners_mod
    monkeypatch.setattr(runners_mod, "SANDBOX_ENABLED", False)
