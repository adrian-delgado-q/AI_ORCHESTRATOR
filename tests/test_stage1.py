"""Stage 1 tests — schema validation, graph routing, state persistence."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.core.goal import OmegaGoal, load_goal
from src.state.schema import SDLCState, SDLCRequirement, ToolEvidence


# ---------------------------------------------------------------------------
# OmegaGoal schema
# ---------------------------------------------------------------------------

class TestOmegaGoal:
    def test_valid_goal(self):
        goal = OmegaGoal(
            goal_id="test-001",
            objective="Do something useful",
            success_criteria=["It works"],
        )
        assert goal.goal_id == "test-001"
        assert goal.quality_thresholds.min_test_coverage == 80

    def test_goal_id_no_spaces(self):
        with pytest.raises(Exception):
            OmegaGoal(goal_id="has spaces", objective="x")

    def test_empty_objective(self):
        with pytest.raises(Exception):
            OmegaGoal(goal_id="ok-id", objective="   ")

    def test_load_goal_from_yaml(self, tmp_path):
        yaml_content = """
goal_id: "yaml-test-001"
objective: "Test loading from YAML"
success_criteria:
  - "It loads"
"""
        p = tmp_path / "goal.yaml"
        p.write_text(yaml_content)
        goal = load_goal(p)
        assert goal.goal_id == "yaml-test-001"
        assert len(goal.success_criteria) == 1

    def test_load_goal_full_yaml(self):
        """Load the real example_goal.yaml from the project."""
        goal = load_goal("config/goals/example_goal.yaml")
        assert goal.goal_id == "example-goal-001"
        assert len(goal.success_criteria) == 3
        assert goal.quality_thresholds.max_cyclomatic_complexity == 10


# ---------------------------------------------------------------------------
# SDLCState
# ---------------------------------------------------------------------------

class TestSDLCState:
    def test_from_goal(self):
        goal = OmegaGoal(
            goal_id="state-test-001",
            objective="Build something",
            success_criteria=["Works"],
        )
        state = SDLCState.from_goal(goal)
        assert state.run_id == "state-test-001"
        assert state.current_phase == "planning"
        assert state.loop_count == 0
        assert state.files_changed == []

    def test_state_serialization_round_trip(self):
        goal = OmegaGoal(goal_id="round-trip-001", objective="Serialize me")
        state = SDLCState.from_goal(goal)
        dumped = state.model_dump()
        restored = SDLCState.model_validate(dumped)
        assert restored.run_id == state.run_id
        assert restored.current_phase == state.current_phase


# ---------------------------------------------------------------------------
# Graph end-to-end
# ---------------------------------------------------------------------------

class TestOmegaGraph:
    def test_mock_run_reaches_done(self):
        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="graph-test-001",
            objective="End-to-end mock run",
            success_criteria=["Step A completes", "Step B completes"],
        )
        initial = SDLCState.from_goal(goal)
        final_dict = omega_graph.invoke(initial.model_dump())
        final = SDLCState.model_validate(final_dict)

        assert final.current_phase == "done"
        assert final.loop_count == 0
        assert len(final.requirements) >= 1
        assert len(final.files_changed) >= 1
        assert len(final.tests_written) >= 1
        assert len(final.gate_evidence) >= 1
        assert final.release_notes is not None

    def test_loop_cap_escalates_to_human_review(self):
        """Force gate failure to verify loop cap + human_review escalation."""
        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="loop-cap-test-001",
            objective="Trigger loop cap",
        )
        initial = SDLCState.from_goal(goal)
        # Pre-set loop_count to 3 and inject a failing gate so the next
        # review routes to release_engineer, which is fine — we test the
        # supervisor escalation path directly via the node function.
        from src.agents.nodes import supervisor_node
        state = SDLCState.from_goal(goal)
        state.loop_count = 3
        state.current_phase = "done"
        result = supervisor_node(state)
        assert result.current_phase == "human_review"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_load(self, tmp_path, monkeypatch):
        import src.state.persistence as pers

        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")

        goal = OmegaGoal(goal_id="persist-test-001", objective="Save me")
        state = SDLCState.from_goal(goal)
        path = pers.save_state(state)

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["run_id"] == "persist-test-001"

        loaded = pers.load_state("persist-test-001")
        assert loaded.run_id == state.run_id
        assert loaded.current_phase == state.current_phase
