"""pytest configuration — shared fixtures and project root on sys.path."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Always-on stubs
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_load_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace load_llm with StubLLM for every test.

    Tests that need a specific LLM either pass ``llm=MockLLM(...)`` directly
    to the node function, or override this fixture with monkeypatch.setattr.
    """
    from src.core.llm import StubLLM
    import src.agents.nodes as nodes_mod

    monkeypatch.setattr(nodes_mod, "load_llm", lambda *args, **kwargs: StubLLM())
    # Also patch in implementation_planner module when it loads llm
    try:
        import src.agents.implementation_planner as planner_mod  # noqa: F401
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def disable_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable Docker sandbox for all tests unless explicitly re-enabled."""
    import src.tools.runners as runners_mod

    monkeypatch.setattr(runners_mod, "SANDBOX_ENABLED", False)


# ---------------------------------------------------------------------------
# MockLLM — deterministic fake with a reply queue; shared across all test files
# ---------------------------------------------------------------------------


class MockLLM:
    """Returns replies from a fixed queue (last reply repeats when exhausted)."""

    def __init__(self, reply: "str | list[str]" = "mock reply") -> None:
        self._replies = [reply] if isinstance(reply, str) else list(reply)
        self._index = 0
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        idx = min(self._index, len(self._replies) - 1)
        self._index += 1
        return self._replies[idx]


# ---------------------------------------------------------------------------
# Directory patching — replaces the 8+ repeated triple-monkeypatch blocks
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch VOLUMES_DIR and RUNS_DIR to isolated tmp subdirs; return tmp_path."""
    import src.io.workspace as ws
    import src.state.persistence as pers
    import src.tools.runners as runners_mod

    monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")
    monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runners_mod, "VOLUMES_DIR", tmp_path / "volumes")
    return tmp_path


# ---------------------------------------------------------------------------
# State factories — replaces the 4 incompatible _make_state variants
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_goal():
    """Return a callable that builds an OmegaGoal with sensible defaults."""
    from src.core.goal import OmegaGoal

    def _factory(
        goal_id: str = "test-run-001",
        objective: str = "Run a test",
        success_criteria: "list[str] | None" = None,
        **kwargs: Any,
    ):
        return OmegaGoal(
            goal_id=goal_id,
            objective=objective,
            success_criteria=success_criteria or ["Feature A works"],
            **kwargs,
        )

    return _factory


@pytest.fixture()
def make_state():
    """Return a callable that builds an SDLCState with keyword overrides."""
    from src.state.schema import SDLCState, SDLCRequirement

    def _factory(**kwargs: Any):
        defaults: dict[str, Any] = dict(
            run_id="test-run-001",
            objective="Run a test",
            success_criteria=["Feature A works"],
            requirements=[
                SDLCRequirement(
                    id="REQ-001",
                    description="Test requirement",
                    acceptance_criteria=["It works"],
                )
            ],
        )
        defaults.update(kwargs)
        return SDLCState(**defaults)

    return _factory


# ---------------------------------------------------------------------------
# ToolEvidence factories — replaces the 4 duplicate helpers across files
# ---------------------------------------------------------------------------


@pytest.fixture()
def passing_evidence():
    """Return a callable (tool_name) -> passing ToolEvidence."""
    from src.state.schema import ToolEvidence

    def _factory(tool_name: str):
        return ToolEvidence(tool_name=tool_name, passed=True, findings="ok")

    return _factory


@pytest.fixture()
def failing_evidence():
    """Return a callable (tool_name, findings='Error found') -> failing ToolEvidence."""
    from src.state.schema import ToolEvidence

    def _factory(tool_name: str, findings: str = "Error found"):
        return ToolEvidence(tool_name=tool_name, passed=False, findings=findings)

    return _factory


# ---------------------------------------------------------------------------
# Tool runner patcher — replaces the 6-runner patch block repeated 6+ times
# ---------------------------------------------------------------------------


@pytest.fixture()
def patch_all_tools(monkeypatch: pytest.MonkeyPatch, passing_evidence, failing_evidence):
    """Return a helper that patches all 6 quality-gate tool runners.

    Usage::

        def test_foo(patch_all_tools):
            mocks = patch_all_tools()                     # all pass
            mocks = patch_all_tools(all_pass=False)       # all fail
            mocks = patch_all_tools(overrides={           # selective override
                "run_ruff": failing_evidence("ruff"),
            })
    """
    import src.agents.nodes as nodes_mod

    _TOOL_NAMES = {
        "run_ruff": "ruff",
        "run_pytest": "pytest",
        "run_mypy": "mypy",
        "run_bandit": "bandit",
        "run_pip_audit": "pip-audit",
        "run_complexity_check": "complexity",
    }

    def _patch(all_pass: bool = True, overrides: "dict | None" = None) -> "dict[str, MagicMock]":
        overrides = overrides or {}
        mocks: dict[str, MagicMock] = {}
        for attr, tool_name in _TOOL_NAMES.items():
            if attr in overrides:
                ev = overrides[attr]
            else:
                ev = passing_evidence(tool_name) if all_pass else failing_evidence(tool_name)
            mock = MagicMock(return_value=ev)
            monkeypatch.setattr(nodes_mod, attr, mock)
            mocks[attr] = mock
        return mocks

    return _patch


# ---------------------------------------------------------------------------
# Subprocess result factory — replaces _completed_result in test_stage3
# ---------------------------------------------------------------------------


@pytest.fixture()
def completed_process():
    """Return a callable (returncode, stdout, stderr) -> MagicMock."""

    def _factory(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
        mock = MagicMock()
        mock.returncode = returncode
        mock.stdout = stdout
        mock.stderr = stderr
        return mock

    return _factory
