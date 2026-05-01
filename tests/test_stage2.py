"""Stage 2 tests — real file I/O, sha256 hashing, node disk writes."""
from __future__ import annotations

import hashlib
import json

import pytest

from src.core.goal import OmegaGoal
from src.state.schema import SDLCState


# ---------------------------------------------------------------------------
# workspace.py — write_file / read_file
# ---------------------------------------------------------------------------


class TestWorkspaceIO:
    def test_write_file_creates_file(self, tmp_dirs):
        import src.io.workspace as ws
        fc = ws.write_file(
            run_id="test-run",
            path="src/hello.py",
            content="print('hello')\n",
            requirement_id="REQ-001",
            rationale="Test write",
        )
        target = tmp_dirs / "volumes" / "test-run" / "src" / "hello.py"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "print('hello')\n"

    def test_write_file_returns_file_change_with_hash(self, tmp_dirs):
        import src.io.workspace as ws
        fc = ws.write_file(
            run_id="test-run",
            path="src/hello.py",
            content="print('hello')\n",
            requirement_id="REQ-001",
            rationale="Test hash",
        )
        assert fc.requirement_id == "REQ-001"
        assert fc.path == "src/hello.py"
        assert len(fc.hash) == 64

    def test_write_file_hash_is_correct_sha256(self, tmp_dirs):
        import src.io.workspace as ws
        content = "def foo(): pass\n"
        fc = ws.write_file(run_id="test-run", path="src/foo.py", content=content,
                           requirement_id="REQ-002", rationale="Hash check")
        expected = hashlib.sha256(content.encode()).hexdigest()
        assert fc.hash == expected

    def test_read_file_round_trips_content(self, tmp_dirs):
        import src.io.workspace as ws
        content = "x = 42\n"
        ws.write_file(run_id="test-run", path="src/x.py", content=content,
                      requirement_id="REQ-003", rationale="Round trip")
        assert ws.read_file("test-run", "src/x.py") == content

    def test_read_file_missing_raises(self, tmp_dirs):
        import src.io.workspace as ws
        with pytest.raises(FileNotFoundError):
            ws.read_file("test-run", "nonexistent.py")

    def test_write_file_creates_parent_dirs(self, tmp_dirs):
        import src.io.workspace as ws
        ws.write_file(run_id="test-run", path="deep/nested/file.py", content="pass\n",
                      requirement_id="REQ-004", rationale="Nested dirs")
        assert (tmp_dirs / "volumes" / "test-run" / "deep" / "nested" / "file.py").exists()

    def test_write_file_overwrite(self, tmp_dirs):
        import src.io.workspace as ws
        ws.write_file(run_id="test-run", path="src/f.py", content="a = 1\n",
                      requirement_id="REQ-001", rationale="first")
        fc2 = ws.write_file(run_id="test-run", path="src/f.py", content="a = 2\n",
                            requirement_id="REQ-001", rationale="overwrite")
        assert ws.read_file("test-run", "src/f.py") == "a = 2\n"
        assert fc2.hash == hashlib.sha256(b"a = 2\n").hexdigest()


# ---------------------------------------------------------------------------
# dev_node and qa_node write real files
# ---------------------------------------------------------------------------


class TestNodeFileWrites:
    def _run_through_tech_lead(self, tmp_dirs):
        import src.io.workspace as ws  # already patched by tmp_dirs
        goal = OmegaGoal(
            goal_id="stage2-test-001",
            objective="Test real file writes",
            success_criteria=["Feature A works", "Feature B works"],
        )
        from src.agents.nodes import tech_lead_node
        state = SDLCState.from_goal(goal)
        return tech_lead_node(state)

    def test_dev_node_populates_files_changed_with_hashes(self, tmp_dirs):
        from src.agents.nodes import dev_node
        state = dev_node(self._run_through_tech_lead(tmp_dirs))
        assert len(state.files_changed) >= 1
        for fc in state.files_changed:
            assert len(fc.hash) == 64, f"{fc.path} has bad hash length"

    def test_dev_node_writes_python_files_to_volume(self, tmp_dirs):
        from src.agents.nodes import dev_node
        state = dev_node(self._run_through_tech_lead(tmp_dirs))
        py_files = list((tmp_dirs / "volumes" / state.run_id).rglob("*.py"))
        assert len(py_files) >= 1

    def test_qa_node_populates_tests_written_with_hashes(self, tmp_dirs):
        from src.agents.nodes import dev_node, qa_node
        state = qa_node(dev_node(self._run_through_tech_lead(tmp_dirs)))
        assert len(state.tests_written) >= 1
        for fc in state.tests_written:
            assert len(fc.hash) == 64

    def test_qa_node_writes_test_files_to_volume(self, tmp_dirs):
        from src.agents.nodes import dev_node, qa_node
        state = qa_node(dev_node(self._run_through_tech_lead(tmp_dirs)))
        test_files = list((tmp_dirs / "volumes" / state.run_id / "tests").glob("test_*.py"))
        assert len(test_files) >= 1


# ---------------------------------------------------------------------------
# End-to-end graph run
# ---------------------------------------------------------------------------


class TestStage2EndToEnd:
    def test_graph_run_writes_real_files_and_no_code_blobs(self, tmp_dirs):
        import src.state.persistence as pers
        from src.agents.graph import omega_graph

        goal = OmegaGoal(
            goal_id="e2e-stage2-001",
            objective="End-to-end Stage 2 run",
            success_criteria=["Thing A", "Thing B"],
        )
        initial = SDLCState.from_goal(goal)
        final_dict = omega_graph.invoke(initial.model_dump())
        final = SDLCState.model_validate(final_dict)

        assert final.current_phase == "done"

        volume = tmp_dirs / "volumes" / goal.goal_id
        assert len(list(volume.rglob("*.py"))) >= 2

        # FileChange objects carry hashes, never raw code
        for fc in final.files_changed + final.tests_written:
            assert len(fc.hash) == 64
            assert not hasattr(fc, "content") or not fc.__dict__.get("content")

        # state.json must not contain a "content" key
        pers.save_state(final)
        state_path = tmp_dirs / "runs" / goal.goal_id / "state.json"
        data = json.loads(state_path.read_text())
        for fc in data["files_changed"] + data["tests_written"]:
            assert "content" not in fc
