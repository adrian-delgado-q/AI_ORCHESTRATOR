"""Critical coverage for previously untested modules.

Covers:
- src/core/timing.py (zero previous coverage)
- src/state/persistence.py missing-file error path
- src/agents/nodes._infer_requirements_from_imports (pure function)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# src/core/timing.py
# ---------------------------------------------------------------------------


class TestTiming:
    def test_record_timing_creates_json_file(self, tmp_path, monkeypatch):
        import src.state.persistence as pers
        import src.core.timing as timing_mod

        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")

        timing_mod.record_timing("my-run", "node", "dev_node", 1.23)

        timing_path = tmp_path / "runs" / "my-run" / "timings.json"
        assert timing_path.exists()
        data = json.loads(timing_path.read_text())
        assert isinstance(data, list)
        assert data[0]["name"] == "dev_node"
        assert data[0]["elapsed_seconds"] == pytest.approx(1.23)

    def test_record_timing_appends_multiple_entries(self, tmp_path, monkeypatch):
        import src.state.persistence as pers
        import src.core.timing as timing_mod

        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")

        timing_mod.record_timing("my-run", "node", "node_a", 0.1)
        timing_mod.record_timing("my-run", "node", "node_b", 0.2)

        data = json.loads((tmp_path / "runs" / "my-run" / "timings.json").read_text())
        assert len(data) == 2
        assert data[1]["name"] == "node_b"

    def test_timed_context_manager_records_duration(self, tmp_path, monkeypatch):
        import src.state.persistence as pers
        import src.core.timing as timing_mod

        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")

        with timing_mod.timed("my-run", "node", "qa_node"):
            time.sleep(0.01)

        data = json.loads((tmp_path / "runs" / "my-run" / "timings.json").read_text())
        assert data[0]["name"] == "qa_node"
        assert data[0]["elapsed_seconds"] >= 0.005

    def test_record_timing_suppresses_write_errors(self, tmp_path, monkeypatch):
        """Timing failures must never propagate and break orchestration."""
        import src.state.persistence as pers
        import src.core.timing as timing_mod

        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")

        # Make the runs dir non-writable so write fails
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_dir = runs_dir / "my-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        timing_path = run_dir / "timings.json"
        timing_path.write_text("[]")
        timing_path.chmod(0o444)  # read-only
        try:
            # Must not raise
            timing_mod.record_timing("my-run", "node", "any_node", 0.5)
        finally:
            timing_path.chmod(0o644)  # restore for cleanup


# ---------------------------------------------------------------------------
# src/state/persistence.py — missing-file error path
# ---------------------------------------------------------------------------


class TestPersistenceMissingFile:
    def test_load_state_missing_file_raises_file_not_found(self, tmp_dirs):
        import src.state.persistence as pers
        with pytest.raises(FileNotFoundError):
            pers.load_state("does-not-exist-run")


# ---------------------------------------------------------------------------
# src/agents/nodes._infer_requirements_from_imports — pure function
# ---------------------------------------------------------------------------


class TestInferRequirementsFromImports:
    """Unit tests for the import-walking helper."""

    def _call(self, src_files: dict[str, str], run_id: str = "test-run") -> str:
        """Write *src_files* to a temp dir, build FileChange list, call the helper."""
        import tempfile
        import pathlib
        from unittest.mock import patch
        from src.agents.nodes import _infer_requirements_from_imports
        from src.state.schema import FileChange
        import hashlib

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            src_dir = root / run_id / "src"
            src_dir.mkdir(parents=True)
            changes = []
            for name, code in src_files.items():
                rel_path = f"src/{name}"
                (root / run_id / rel_path).parent.mkdir(parents=True, exist_ok=True)
                (root / run_id / rel_path).write_text(code)
                changes.append(FileChange(
                    path=rel_path,
                    hash=hashlib.sha256(code.encode()).hexdigest(),
                    requirement_id="REQ-001",
                    rationale="stub",
                ))
            import src.agents.nodes as nodes_mod
            with patch.object(nodes_mod, "VOLUMES_DIR", root):
                return _infer_requirements_from_imports(run_id, changes)

    def test_maps_known_third_party_package(self):
        req_txt = self._call({"impl.py": "import fastapi\n"})
        assert "fastapi" in req_txt

    def test_excludes_stdlib_modules(self):
        req_txt = self._call({"impl.py": "import os\nimport sys\nimport json\n"})
        assert "os" not in req_txt
        assert "sys" not in req_txt
        assert "json" not in req_txt

    def test_returns_empty_string_for_no_imports(self):
        req_txt = self._call({"impl.py": "x = 1\n"})
        assert req_txt.strip() == ""

    def test_deduplicates_repeated_imports(self):
        req_txt = self._call({
            "a.py": "import fastapi\n",
            "b.py": "import fastapi\n",
        })
        lines = [l for l in req_txt.splitlines() if l.strip()]
        assert lines.count("fastapi") == 1
