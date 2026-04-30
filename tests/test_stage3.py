"""Stage 3 tests — deterministic review gates via subprocess tool runners."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.goal import OmegaGoal
from src.state.schema import SDLCState, ToolEvidence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(goal_id: str = "stage3-test-001") -> SDLCState:
    goal = OmegaGoal(
        goal_id=goal_id,
        objective="Stage 3 test run",
        success_criteria=["Feature A", "Feature B"],
    )
    return SDLCState.from_goal(goal)


def _completed_result(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    """Return a mock CompletedProcess with the given returncode and output."""
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


# ---------------------------------------------------------------------------
# Unit tests — tool runner functions (subprocess mocked)
# ---------------------------------------------------------------------------

class TestToolRunnersUnit:
    """Each runner returns a ToolEvidence without hitting the filesystem."""

    def test_run_ruff_pass(self, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(0, stdout="All checks passed."),
        )
        ev = runners.run_ruff("dummy-run")
        assert isinstance(ev, ToolEvidence)
        assert ev.tool_name == "ruff"
        assert ev.passed is True

    def test_run_ruff_fail(self, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(1, stderr="E501 line too long"),
        )
        ev = runners.run_ruff("dummy-run")
        assert ev.tool_name == "ruff"
        assert ev.passed is False
        assert "E501" in ev.findings

    def test_run_pytest_pass(self, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(0, stdout="2 passed"),
        )
        ev = runners.run_pytest("dummy-run", min_coverage=80)
        assert ev.tool_name == "pytest"
        assert ev.passed is True

    def test_run_pytest_fail_on_coverage(self, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(1, stdout="FAILED (coverage: 42%)"),
        )
        ev = runners.run_pytest("dummy-run", min_coverage=80)
        assert ev.tool_name == "pytest"
        assert ev.passed is False

    def test_run_mypy_skipped_when_not_enforced(self):
        import src.tools.runners as runners
        ev = runners.run_mypy("dummy-run", enforce=False)
        assert ev.tool_name == "mypy"
        assert ev.passed is True
        assert "Skipped" in ev.findings

    def test_run_mypy_enforced_pass(self, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(0, stdout="Success: no issues found"),
        )
        ev = runners.run_mypy("dummy-run", enforce=True)
        assert ev.tool_name == "mypy"
        assert ev.passed is True

    def test_run_mypy_enforced_fail(self, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(1, stderr="error: Missing return type"),
        )
        ev = runners.run_mypy("dummy-run", enforce=True)
        assert ev.tool_name == "mypy"
        assert ev.passed is False

    def test_run_bandit_pass(self, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(0, stdout="No issues identified."),
        )
        ev = runners.run_bandit("dummy-run")
        assert ev.tool_name == "bandit"
        assert ev.passed is True

    def test_run_pip_audit_skipped_no_requirements(self, tmp_path, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(runners, "VOLUMES_DIR", tmp_path / "volumes")
        # No requirements.txt in tmp volume → skip
        ev = runners.run_pip_audit("dummy-run")
        assert ev.tool_name == "pip-audit"
        assert ev.passed is True
        assert "Skipped" in ev.findings

    def test_run_pip_audit_runs_when_requirements_present(self, tmp_path, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(runners, "VOLUMES_DIR", tmp_path / "volumes")
        req_file = tmp_path / "volumes" / "dummy-run" / "requirements.txt"
        req_file.parent.mkdir(parents=True)
        req_file.write_text("requests==2.28.0\n")

        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(0, stdout="No known vulnerabilities found."),
        )
        ev = runners.run_pip_audit("dummy-run")
        assert ev.tool_name == "pip-audit"
        assert ev.passed is True

    def test_run_complexity_check_returns_tool_evidence(self, monkeypatch):
        # Stage 5: run_complexity_check is real (xenon), not a stub.
        # Mock subprocess so the test doesn't depend on the volume existing.
        import src.tools.runners as runners
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(0, stdout=""),
        )
        ev = runners.run_complexity_check("dummy-run", max_complexity=10)
        assert ev.tool_name == "complexity"
        assert ev.passed is True
        assert "Stub" not in ev.findings  # real implementation, not a stub

    def test_all_runners_return_tool_evidence(self, monkeypatch):
        """Smoke: every runner returns a properly typed ToolEvidence."""
        import src.tools.runners as runners
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _completed_result(0, stdout="ok"),
        )
        monkeypatch.setattr(runners, "VOLUMES_DIR", Path("/tmp"))

        results = [
            runners.run_ruff("x"),
            runners.run_pytest("x"),
            runners.run_mypy("x", enforce=False),
            runners.run_bandit("x"),
            runners.run_pip_audit("x"),  # no requirements.txt → skip
            runners.run_complexity_check("x"),
        ]
        for ev in results:
            assert isinstance(ev, ToolEvidence), f"{ev!r} is not ToolEvidence"
            assert isinstance(ev.passed, bool)
            assert isinstance(ev.findings, str)


# ---------------------------------------------------------------------------
# review_node — routing logic
# ---------------------------------------------------------------------------

class TestReviewNodeGating:
    """Patch the imported tool functions inside nodes to control pass/fail."""

    def _make_passing_evidence(self, name: str) -> ToolEvidence:
        return ToolEvidence(tool_name=name, passed=True, findings="ok")

    def _make_failing_evidence(self, name: str) -> ToolEvidence:
        return ToolEvidence(tool_name=name, passed=False, findings="fail")

    def _run_review(self, monkeypatch, ruff_pass: bool, pytest_pass: bool) -> SDLCState:
        """Patch tool runners and invoke review_node."""
        import src.agents.nodes as nodes

        monkeypatch.setattr(nodes, "run_ruff", lambda run_id: self._make_passing_evidence("ruff") if ruff_pass else self._make_failing_evidence("ruff"))
        monkeypatch.setattr(nodes, "run_pytest", lambda run_id, min_coverage=80: self._make_passing_evidence("pytest") if pytest_pass else self._make_failing_evidence("pytest"))
        monkeypatch.setattr(nodes, "run_mypy", lambda run_id, enforce=True: self._make_passing_evidence("mypy"))
        monkeypatch.setattr(nodes, "run_bandit", lambda run_id: self._make_passing_evidence("bandit"))
        monkeypatch.setattr(nodes, "run_pip_audit", lambda run_id: self._make_passing_evidence("pip-audit"))
        monkeypatch.setattr(nodes, "run_complexity_check", lambda run_id, max_complexity=10: self._make_passing_evidence("complexity"))

        state = _make_state()
        return nodes.review_node(state)

    def test_review_all_required_pass_advances_to_release(self, monkeypatch):
        state = self._run_review(monkeypatch, ruff_pass=True, pytest_pass=True)
        assert state.current_phase == "release"
        assert state.loop_count == 0

    def test_review_ruff_fail_routes_to_implementation(self, monkeypatch):
        state = self._run_review(monkeypatch, ruff_pass=False, pytest_pass=True)
        assert state.current_phase == "implementation"

    def test_review_pytest_fail_routes_to_implementation(self, monkeypatch):
        state = self._run_review(monkeypatch, ruff_pass=True, pytest_pass=False)
        assert state.current_phase == "implementation"

    def test_review_required_fail_increments_loop_count(self, monkeypatch):
        state = self._run_review(monkeypatch, ruff_pass=False, pytest_pass=False)
        assert state.loop_count == 1

    def test_review_populates_gate_evidence(self, monkeypatch):
        state = self._run_review(monkeypatch, ruff_pass=True, pytest_pass=True)
        tool_names = {e.tool_name for e in state.gate_evidence}
        assert "ruff" in tool_names
        assert "pytest" in tool_names

    def test_review_successive_fails_accumulate_loop_count(self, monkeypatch):
        """Three consecutive review failures → loop_count == 3."""
        import src.agents.nodes as nodes

        def _failing_ruff(run_id):
            return self._make_failing_evidence("ruff")

        monkeypatch.setattr(nodes, "run_ruff", _failing_ruff)
        monkeypatch.setattr(nodes, "run_pytest", lambda run_id, min_coverage=80: self._make_passing_evidence("pytest"))
        monkeypatch.setattr(nodes, "run_mypy", lambda run_id, enforce=True: self._make_passing_evidence("mypy"))
        monkeypatch.setattr(nodes, "run_bandit", lambda run_id: self._make_passing_evidence("bandit"))
        monkeypatch.setattr(nodes, "run_pip_audit", lambda run_id: self._make_passing_evidence("pip-audit"))
        monkeypatch.setattr(nodes, "run_complexity_check", lambda run_id, max_complexity=10: self._make_passing_evidence("complexity"))

        state = _make_state()
        for _ in range(3):
            state = nodes.review_node(state)
        assert state.loop_count == 3

    def test_optional_tool_failure_does_not_block_release(self, monkeypatch):
        """mypy/bandit failures on their own do not block required gates."""
        import src.agents.nodes as nodes

        monkeypatch.setattr(nodes, "run_ruff", lambda run_id: self._make_passing_evidence("ruff"))
        monkeypatch.setattr(nodes, "run_pytest", lambda run_id, min_coverage=80: self._make_passing_evidence("pytest"))
        monkeypatch.setattr(nodes, "run_mypy", lambda run_id, enforce=True: self._make_failing_evidence("mypy"))
        monkeypatch.setattr(nodes, "run_bandit", lambda run_id: self._make_failing_evidence("bandit"))
        monkeypatch.setattr(nodes, "run_pip_audit", lambda run_id: self._make_passing_evidence("pip-audit"))
        monkeypatch.setattr(nodes, "run_complexity_check", lambda run_id, max_complexity=10: self._make_passing_evidence("complexity"))

        state = _make_state()
        state = nodes.review_node(state)
        # Required gates pass → still advances to release despite optional failures
        assert state.current_phase == "release"
        assert state.loop_count == 0


# ---------------------------------------------------------------------------
# release_engineer_node — safety-net block
# ---------------------------------------------------------------------------

class TestReleaseEngineerBlock:
    def test_re_blocks_when_required_gate_failed(self):
        from src.agents.nodes import release_engineer_node
        state = _make_state("re-block-001")
        state.gate_evidence = [
            ToolEvidence(tool_name="ruff", passed=False, findings="lint errors"),
            ToolEvidence(tool_name="pytest", passed=True, findings="ok"),
        ]
        state.current_phase = "release"

        result = release_engineer_node(state)
        assert result.current_phase == "implementation"
        assert result.loop_count == 0  # RE does not increment; review_node owns loop_count
        assert result.release_notes is None

    def test_re_proceeds_when_all_required_pass(self):
        from src.agents.nodes import release_engineer_node
        state = _make_state("re-pass-001")
        state.gate_evidence = [
            ToolEvidence(tool_name="ruff", passed=True, findings="ok"),
            ToolEvidence(tool_name="pytest", passed=True, findings="ok"),
        ]
        state.current_phase = "release"

        result = release_engineer_node(state)
        assert result.current_phase == "done"
        assert result.release_notes is not None

    def test_re_passes_when_no_gate_evidence(self):
        """No evidence means no required failures — RE proceeds normally."""
        from src.agents.nodes import release_engineer_node
        state = _make_state("re-empty-001")
        state.gate_evidence = []
        state.current_phase = "release"

        result = release_engineer_node(state)
        assert result.current_phase == "done"


# ---------------------------------------------------------------------------
# Graph routing — loop and escalation (tools mocked)
# ---------------------------------------------------------------------------

class TestGraphLoopRouting:
    """Full graph runs with patched tools to exercise loop and escalation paths."""

    def _patch_tools_all_pass(self, monkeypatch):
        import src.agents.nodes as nodes
        ok = lambda name: (lambda *a, **kw: ToolEvidence(tool_name=name, passed=True, findings="ok"))
        monkeypatch.setattr(nodes, "run_ruff", ok("ruff"))
        monkeypatch.setattr(nodes, "run_pytest", ok("pytest"))
        monkeypatch.setattr(nodes, "run_mypy", ok("mypy"))
        monkeypatch.setattr(nodes, "run_bandit", ok("bandit"))
        monkeypatch.setattr(nodes, "run_pip_audit", ok("pip-audit"))
        monkeypatch.setattr(nodes, "run_complexity_check", ok("complexity"))

    def _patch_tools_always_fail(self, monkeypatch):
        import src.agents.nodes as nodes
        fail = lambda name: (lambda *a, **kw: ToolEvidence(tool_name=name, passed=False, findings="fail"))
        monkeypatch.setattr(nodes, "run_ruff", fail("ruff"))
        monkeypatch.setattr(nodes, "run_pytest", fail("pytest"))
        monkeypatch.setattr(nodes, "run_mypy", fail("mypy"))
        monkeypatch.setattr(nodes, "run_bandit", fail("bandit"))
        monkeypatch.setattr(nodes, "run_pip_audit", fail("pip-audit"))
        monkeypatch.setattr(nodes, "run_complexity_check", fail("complexity"))

    def test_graph_all_gates_pass_reaches_done(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        import src.state.persistence as pers

        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")
        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")
        self._patch_tools_all_pass(monkeypatch)

        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="loop-pass-001",
            objective="Loop pass test",
            success_criteria=["A", "B"],
        )
        final = SDLCState.model_validate(omega_graph.invoke(SDLCState.from_goal(goal).model_dump()))
        assert final.current_phase == "done"
        assert final.loop_count == 0
        assert any(e.tool_name == "ruff" for e in final.gate_evidence)
        assert any(e.tool_name == "pytest" for e in final.gate_evidence)

    def test_graph_gates_fail_escalates_to_human_review(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        import src.state.persistence as pers

        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")
        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")
        self._patch_tools_always_fail(monkeypatch)

        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="loop-fail-001",
            objective="Loop escalation test",
            success_criteria=["A"],
        )
        final = SDLCState.model_validate(omega_graph.invoke(SDLCState.from_goal(goal).model_dump()))
        assert final.current_phase == "human_review"
        assert final.loop_count >= 3

    def test_graph_loop_count_increments_per_failed_cycle(self, tmp_path, monkeypatch):
        """loop_count must be exactly 3 after three failed review cycles."""
        import src.io.workspace as ws
        import src.state.persistence as pers

        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")
        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")
        self._patch_tools_always_fail(monkeypatch)

        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="loop-count-001",
            objective="Loop count test",
            success_criteria=["A"],
        )
        final = SDLCState.model_validate(omega_graph.invoke(SDLCState.from_goal(goal).model_dump()))
        assert final.loop_count == 3


# ---------------------------------------------------------------------------
# End-to-end — real subprocess tools on real stub files
# ---------------------------------------------------------------------------

class TestStage3EndToEndReal:
    """Run the full graph against real ruff/pytest on the stub files generated by
    dev_node/qa_node.  This verifies the stub code is clean enough to pass all
    required gates without manual intervention.

    Requires: ruff, pytest, pytest-cov installed in the active venv.
    """

    def test_valid_stub_files_pass_all_required_gates(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        import src.state.persistence as pers
        import src.tools.runners as runners

        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")
        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(runners, "VOLUMES_DIR", tmp_path / "volumes")

        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="real-tools-001",
            objective="Validate real tool gates on stub files",
            success_criteria=["Feature A", "Feature B"],
            quality_thresholds={"min_test_coverage": 80, "max_cyclomatic_complexity": 10, "enforce_type_hints": False},
        )
        initial = SDLCState.from_goal(goal)
        final = SDLCState.model_validate(omega_graph.invoke(initial.model_dump()))

        assert final.current_phase == "done", (
            f"Expected done but got {final.current_phase}. "
            f"Gate evidence:\n"
            + "\n".join(f"  {e.tool_name}: {'PASS' if e.passed else 'FAIL'} — {e.findings[:200]}" for e in final.gate_evidence)
        )
        assert final.loop_count == 0

    def test_gate_evidence_contains_real_tool_output(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        import src.state.persistence as pers
        import src.tools.runners as runners

        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")
        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(runners, "VOLUMES_DIR", tmp_path / "volumes")

        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="evidence-001",
            objective="Prove gate evidence comes from real tools",
            success_criteria=["Only one requirement"],
            quality_thresholds={"min_test_coverage": 80, "max_cyclomatic_complexity": 10, "enforce_type_hints": False},
        )
        final = SDLCState.model_validate(omega_graph.invoke(SDLCState.from_goal(goal).model_dump()))

        tool_names = {e.tool_name for e in final.gate_evidence}
        assert "ruff" in tool_names
        assert "pytest" in tool_names
        # findings come from real tool output — not the old stub strings
        ruff_ev = next(e for e in final.gate_evidence if e.tool_name == "ruff")
        assert "stub" not in ruff_ev.findings.lower()
