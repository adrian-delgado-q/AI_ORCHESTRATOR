"""Stage 3 tests — deterministic review gates via subprocess tool runners."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.goal import OmegaGoal
from src.state.schema import SDLCState, ToolEvidence


# ---------------------------------------------------------------------------
# Unit tests — tool runner functions (subprocess mocked)
# ---------------------------------------------------------------------------


class TestToolRunnersUnit:
    """Each runner returns a ToolEvidence without hitting the filesystem."""

    def test_run_ruff_pass(self, monkeypatch, completed_process):
        import src.tools.runners as runners
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed_process(0, stdout="All checks passed."))
        ev = runners.run_ruff("dummy-run")
        assert isinstance(ev, ToolEvidence)
        assert ev.tool_name == "ruff"
        assert ev.passed is True

    def test_run_ruff_fail(self, monkeypatch, completed_process):
        import src.tools.runners as runners
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed_process(1, stderr="E501 line too long"))
        ev = runners.run_ruff("dummy-run")
        assert ev.tool_name == "ruff"
        assert ev.passed is False
        assert "E501" in ev.findings

    def test_run_pytest_pass(self, monkeypatch, completed_process):
        import src.tools.runners as runners
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed_process(0, stdout="2 passed"))
        ev = runners.run_pytest("dummy-run", min_coverage=80)
        assert ev.tool_name == "pytest"
        assert ev.passed is True

    def test_run_pytest_fail_on_coverage(self, monkeypatch, completed_process):
        import src.tools.runners as runners
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed_process(1, stdout="FAILED (coverage: 42%)"))
        ev = runners.run_pytest("dummy-run", min_coverage=80)
        assert ev.tool_name == "pytest"
        assert ev.passed is False

    def test_run_mypy_skipped_when_not_enforced(self):
        import src.tools.runners as runners
        ev = runners.run_mypy("dummy-run", enforce=False)
        assert ev.tool_name == "mypy"
        assert ev.passed is True
        assert "Skipped" in ev.findings

    def test_run_mypy_enforced_pass(self, monkeypatch, completed_process):
        import src.tools.runners as runners
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed_process(0, stdout="Success: no issues found"))
        ev = runners.run_mypy("dummy-run", enforce=True)
        assert ev.tool_name == "mypy"
        assert ev.passed is True

    def test_run_mypy_enforced_fail(self, monkeypatch, completed_process):
        import src.tools.runners as runners
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed_process(1, stderr="error: Missing return type"))
        ev = runners.run_mypy("dummy-run", enforce=True)
        assert ev.tool_name == "mypy"
        assert ev.passed is False

    def test_run_bandit_pass(self, monkeypatch, completed_process):
        import src.tools.runners as runners
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed_process(0, stdout="No issues identified."))
        ev = runners.run_bandit("dummy-run")
        assert ev.tool_name == "bandit"
        assert ev.passed is True

    def test_run_pip_audit_skipped_no_requirements(self, tmp_path, monkeypatch):
        import src.tools.runners as runners
        monkeypatch.setattr(runners, "VOLUMES_DIR", tmp_path / "volumes")
        ev = runners.run_pip_audit("dummy-run")
        assert ev.tool_name == "pip-audit"
        assert ev.passed is True
        assert "Skipped" in ev.findings

    def test_run_pip_audit_runs_when_requirements_present(self, tmp_path, monkeypatch, completed_process):
        import src.tools.runners as runners
        monkeypatch.setattr(runners, "VOLUMES_DIR", tmp_path / "volumes")
        req_file = tmp_path / "volumes" / "dummy-run" / "requirements.txt"
        req_file.parent.mkdir(parents=True)
        req_file.write_text("requests==2.28.0\n")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed_process(0, stdout="No known vulnerabilities found."))
        ev = runners.run_pip_audit("dummy-run")
        assert ev.tool_name == "pip-audit"
        assert ev.passed is True

    def test_run_complexity_check_is_real_not_stub(self, monkeypatch, completed_process):
        import src.tools.runners as runners
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed_process(0, stdout=""))
        ev = runners.run_complexity_check("dummy-run", max_complexity=10)
        assert ev.tool_name == "complexity"
        assert ev.passed is True
        assert "Stub" not in ev.findings


# ---------------------------------------------------------------------------
# review_node — routing logic
# ---------------------------------------------------------------------------


class TestReviewNodeGating:
    """Use patch_all_tools to control pass/fail for each runner."""

    def test_review_all_required_pass_advances_to_release(self, make_state, patch_all_tools):
        from src.agents.nodes import review_node
        patch_all_tools()
        state = review_node(make_state())
        assert state.current_phase == "release"
        assert state.loop_count == 0

    def test_review_ruff_fail_routes_back_to_implementation(self, make_state, patch_all_tools, failing_evidence):
        from src.agents.nodes import review_node
        patch_all_tools(overrides={"run_ruff": failing_evidence("ruff")})
        state = review_node(make_state())
        # Source routes gate failures to "implementation_planning" (re-plan before dev)
        assert state.current_phase == "implementation_planning"

    def test_review_pytest_fail_routes_back_to_implementation(self, make_state, patch_all_tools, failing_evidence):
        from src.agents.nodes import review_node
        patch_all_tools(overrides={"run_pytest": failing_evidence("pytest")})
        state = review_node(make_state())
        assert state.current_phase == "implementation_planning"

    def test_review_required_fail_increments_loop_count(self, make_state, patch_all_tools):
        from src.agents.nodes import review_node
        patch_all_tools(all_pass=False)
        state = review_node(make_state())
        assert state.loop_count == 1

    def test_review_populates_gate_evidence(self, make_state, patch_all_tools):
        from src.agents.nodes import review_node
        patch_all_tools()
        state = review_node(make_state())
        tool_names = {e.tool_name for e in state.gate_evidence}
        assert "ruff" in tool_names
        assert "pytest" in tool_names

    def test_successive_fails_accumulate_loop_count(self, make_state, patch_all_tools):
        """Three consecutive review failures → loop_count == 3."""
        from src.agents.nodes import review_node
        patch_all_tools(all_pass=False)
        state = make_state()
        for _ in range(3):
            state = review_node(state)
        assert state.loop_count == 3

    def test_optional_tool_failure_does_not_block_release(self, make_state, patch_all_tools, failing_evidence):
        """mypy/bandit failures alone do not block required gates."""
        from src.agents.nodes import review_node
        patch_all_tools(overrides={
            "run_mypy": failing_evidence("mypy"),
            "run_bandit": failing_evidence("bandit"),
        })
        state = review_node(make_state())
        assert state.current_phase == "release"
        assert state.loop_count == 0

    def test_optional_tools_not_called_when_required_gate_fails(self, make_state, patch_all_tools, failing_evidence):
        """Optional tools are skipped when required gates already fail."""
        from src.agents.nodes import review_node
        mocks = patch_all_tools(overrides={"run_ruff": failing_evidence("ruff")})
        review_node(make_state())
        mocks["run_mypy"].assert_not_called()


# ---------------------------------------------------------------------------
# release_engineer_node — safety-net block
# ---------------------------------------------------------------------------


class TestReleaseEngineerBlock:
    def test_re_blocks_when_required_gate_failed(self, make_state, failing_evidence, passing_evidence):
        from src.agents.nodes import release_engineer_node
        state = make_state(
            gate_evidence=[failing_evidence("ruff", "lint errors"), passing_evidence("pytest")],
            current_phase="release",
        )
        result = release_engineer_node(state)
        assert result.current_phase == "implementation_planning"
        assert result.release_notes is None

    def test_re_proceeds_when_all_required_pass(self, make_state, passing_evidence):
        from src.agents.nodes import release_engineer_node
        state = make_state(
            gate_evidence=[passing_evidence("ruff"), passing_evidence("pytest")],
            current_phase="release",
        )
        result = release_engineer_node(state)
        assert result.current_phase == "done"
        assert result.release_notes is not None

    def test_re_passes_when_no_gate_evidence(self, make_state):
        from src.agents.nodes import release_engineer_node
        state = make_state(gate_evidence=[], current_phase="release")
        result = release_engineer_node(state)
        assert result.current_phase == "done"


# ---------------------------------------------------------------------------
# Graph routing — loop and escalation (tools mocked)
# ---------------------------------------------------------------------------


class TestGraphLoopRouting:
    def test_graph_all_gates_pass_reaches_done(self, tmp_dirs, make_goal, patch_all_tools):
        from src.agents.graph import omega_graph
        patch_all_tools()
        goal = make_goal(goal_id="loop-pass-001", objective="Loop pass test")
        final = SDLCState.model_validate(
            omega_graph.invoke(SDLCState.from_goal(goal).model_dump())
        )
        assert final.current_phase == "done"
        assert final.loop_count == 0
        assert any(e.tool_name == "ruff" for e in final.gate_evidence)

    def test_graph_gates_fail_escalates_to_human_review(self, tmp_dirs, make_goal, patch_all_tools):
        from src.agents.graph import omega_graph
        patch_all_tools(all_pass=False)
        goal = make_goal(goal_id="loop-fail-001", objective="Loop escalation test")
        final = SDLCState.model_validate(
            omega_graph.invoke(SDLCState.from_goal(goal).model_dump())
        )
        assert final.current_phase == "human_review"
        assert final.loop_count >= 3

    def test_graph_loop_count_is_exactly_3_after_cap(self, tmp_dirs, make_goal, patch_all_tools):
        from src.agents.graph import omega_graph
        patch_all_tools(all_pass=False)
        goal = make_goal(goal_id="loop-count-001", objective="Loop count test")
        final = SDLCState.model_validate(
            omega_graph.invoke(SDLCState.from_goal(goal).model_dump())
        )
        assert final.loop_count == 3


# ---------------------------------------------------------------------------
# End-to-end — real subprocess tools on real stub files
# ---------------------------------------------------------------------------


class TestStage3EndToEndReal:
    """Run the full graph against real ruff/pytest on the stub files generated by
    dev_node/qa_node.

    Requires: ruff, pytest, pytest-cov installed in the active venv.
    """

    def test_valid_stub_files_pass_all_required_gates(self, tmp_dirs):
        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="real-tools-001",
            objective="Validate real tool gates on stub files",
            success_criteria=["Feature A", "Feature B"],
            quality_thresholds={"min_test_coverage": 80, "max_cyclomatic_complexity": 10, "enforce_type_hints": False},
        )
        final = SDLCState.model_validate(
            omega_graph.invoke(SDLCState.from_goal(goal).model_dump())
        )
        assert final.current_phase == "done", (
            f"Expected done but got {final.current_phase}. Gate evidence:\n"
            + "\n".join(
                f"  {e.tool_name}: {'PASS' if e.passed else 'FAIL'} — {e.findings[:200]}"
                for e in final.gate_evidence
            )
        )
        assert final.loop_count == 0

    def test_gate_evidence_contains_real_tool_output(self, tmp_dirs):
        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="evidence-001",
            objective="Prove gate evidence comes from real tools",
            success_criteria=["Only one requirement"],
            quality_thresholds={"min_test_coverage": 80, "max_cyclomatic_complexity": 10, "enforce_type_hints": False},
        )
        final = SDLCState.model_validate(
            omega_graph.invoke(SDLCState.from_goal(goal).model_dump())
        )
        tool_names = {e.tool_name for e in final.gate_evidence}
        assert "ruff" in tool_names
        assert "pytest" in tool_names
        ruff_ev = next(e for e in final.gate_evidence if e.tool_name == "ruff")
        assert "stub" not in ruff_ev.findings.lower()
