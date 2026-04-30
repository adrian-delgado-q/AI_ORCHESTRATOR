"""Stage 2 tests — real file I/O, sha256 hashing, node disk writes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.goal import OmegaGoal
from src.state.schema import SDLCState


# ---------------------------------------------------------------------------
# workspace.py — write_file / read_file
# ---------------------------------------------------------------------------

class TestWorkspaceIO:
    def test_write_file_creates_file(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")

        fc = ws.write_file(
            run_id="test-run",
            path="src/hello.py",
            content="print('hello')\n",
            requirement_id="REQ-001",
            rationale="Test write",
        )

        target = tmp_path / "volumes" / "test-run" / "src" / "hello.py"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "print('hello')\n"

    def test_write_file_returns_file_change_with_hash(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")

        fc = ws.write_file(
            run_id="test-run",
            path="src/hello.py",
            content="print('hello')\n",
            requirement_id="REQ-001",
            rationale="Test hash",
        )

        assert fc.requirement_id == "REQ-001"
        assert fc.path == "src/hello.py"
        assert len(fc.hash) == 64  # sha256 hex digest
        assert fc.hash != ""

    def test_write_file_hash_is_correct_sha256(self, tmp_path, monkeypatch):
        import hashlib
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")

        content = "def foo(): pass\n"
        fc = ws.write_file(
            run_id="test-run",
            path="src/foo.py",
            content=content,
            requirement_id="REQ-002",
            rationale="Hash check",
        )

        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert fc.hash == expected

    def test_read_file_round_trips_content(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")

        content = "x = 42\n"
        ws.write_file(
            run_id="test-run",
            path="src/x.py",
            content=content,
            requirement_id="REQ-003",
            rationale="Round trip",
        )

        result = ws.read_file("test-run", "src/x.py")
        assert result == content

    def test_read_file_missing_raises(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")

        with pytest.raises(FileNotFoundError):
            ws.read_file("test-run", "nonexistent.py")

    def test_write_file_creates_parent_dirs(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")

        ws.write_file(
            run_id="test-run",
            path="deeply/nested/dir/file.py",
            content="pass\n",
            requirement_id="REQ-004",
            rationale="Nested dirs",
        )

        assert (tmp_path / "volumes" / "test-run" / "deeply" / "nested" / "dir" / "file.py").exists()


# ---------------------------------------------------------------------------
# dev_node and qa_node write real files
# ---------------------------------------------------------------------------

class TestNodeFileWrites:
    def _make_state(self, tmp_path, monkeypatch) -> SDLCState:
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")

        goal = OmegaGoal(
            goal_id="stage2-test-001",
            objective="Test real file writes",
            success_criteria=["Feature A works", "Feature B works"],
        )
        from src.agents.nodes import tech_lead_node
        state = SDLCState.from_goal(goal)
        state = tech_lead_node(state)
        return state

    def test_dev_node_populates_files_changed_with_hashes(self, tmp_path, monkeypatch):
        from src.agents.nodes import dev_node
        state = self._make_state(tmp_path, monkeypatch)
        state = dev_node(state)

        assert len(state.files_changed) >= 1
        for fc in state.files_changed:
            assert fc.hash != "", f"FileChange {fc.path} has empty hash"
            assert len(fc.hash) == 64

    def test_dev_node_writes_files_to_volume(self, tmp_path, monkeypatch):
        from src.agents.nodes import dev_node
        state = self._make_state(tmp_path, monkeypatch)
        state = dev_node(state)

        volume = tmp_path / "volumes" / state.run_id
        py_files = list(volume.rglob("*.py"))
        assert len(py_files) >= 1, "No .py files written by dev_node"

    def test_qa_node_populates_tests_written_with_hashes(self, tmp_path, monkeypatch):
        from src.agents.nodes import dev_node, qa_node
        state = self._make_state(tmp_path, monkeypatch)
        state = dev_node(state)
        state = qa_node(state)

        assert len(state.tests_written) >= 1
        for fc in state.tests_written:
            assert fc.hash != "", f"FileChange {fc.path} has empty hash"
            assert len(fc.hash) == 64

    def test_qa_node_writes_test_files_to_volume(self, tmp_path, monkeypatch):
        from src.agents.nodes import dev_node, qa_node
        state = self._make_state(tmp_path, monkeypatch)
        state = dev_node(state)
        state = qa_node(state)

        volume = tmp_path / "volumes" / state.run_id
        test_files = list((volume / "tests").glob("test_*.py"))
        assert len(test_files) >= 1, "No test files written by qa_node"


# ---------------------------------------------------------------------------
# End-to-end graph run
# ---------------------------------------------------------------------------

class TestStage2EndToEnd:
    def test_graph_run_writes_real_files(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        import src.state.persistence as pers
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")
        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")

        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="e2e-stage2-001",
            objective="End-to-end Stage 2 run",
            success_criteria=["Thing A", "Thing B"],
        )
        initial = SDLCState.from_goal(goal)
        final_dict = omega_graph.invoke(initial.model_dump())
        final = SDLCState.model_validate(final_dict)

        # Graph reaches done
        assert final.current_phase == "done"

        # Real .py files exist on volume
        volume = tmp_path / "volumes" / goal.goal_id
        all_py = list(volume.rglob("*.py"))
        assert len(all_py) >= 2, "Expected at least one source + one test file"

        # files_changed has hashes, no raw code key
        assert all(len(fc.hash) == 64 for fc in final.files_changed)
        assert all(len(fc.hash) == 64 for fc in final.tests_written)

    def test_state_json_has_no_code_blobs(self, tmp_path, monkeypatch):
        import src.io.workspace as ws
        import src.state.persistence as pers
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path / "volumes")
        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")

        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="no-blob-001",
            objective="Assert no code in state",
            success_criteria=["No blobs"],
        )
        initial = SDLCState.from_goal(goal)
        final_dict = omega_graph.invoke(initial.model_dump())

        pers.save_state(SDLCState.model_validate(final_dict))
        state_path = tmp_path / "runs" / goal.goal_id / "state.json"
        data = json.loads(state_path.read_text())

        # FileChange objects must not carry a 'content' key
        for fc in data["files_changed"] + data["tests_written"]:
            assert "content" not in fc, f"Found 'content' key in FileChange: {fc}"
            assert fc["hash"] != ""
