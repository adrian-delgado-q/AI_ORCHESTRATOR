"""Stage 5 tests — Docker Sandbox, sandbox runners, and performance opts."""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.state.schema import ToolEvidence


# ---------------------------------------------------------------------------
# Shared volume scaffold (only used by TestComplexityCheckReal)
# ---------------------------------------------------------------------------


def _make_stub_volume(tmp_path: Path) -> str:
    """Write a minimal src/tests volume under *tmp_path*; return run_id."""
    run_id = "stage5-test-run"
    src = tmp_path / run_id / "src"
    tests = tmp_path / run_id / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    (src / "req_001_impl.py").write_text(textwrap.dedent("""\
        # Requirement: REQ-001
        def add(a: int, b: int) -> int:
            return a + b
    """))
    (tests / "test_req_001.py").write_text(textwrap.dedent(f"""\
        # Requirement: REQ-001
        import sys
        sys.path.insert(0, "{src.parent}")
        from req_001_impl import add

        def test_add():
            assert add(1, 2) == 3
    """))
    return run_id


# ---------------------------------------------------------------------------
# TestSandboxManager
# ---------------------------------------------------------------------------


class TestSandboxManager:
    def _mock_docker(self):
        fake_container = MagicMock()
        fake_container.id = "abc123def456" * 4
        fake_docker = MagicMock()
        fake_docker.containers.run.return_value = fake_container
        fake_docker.containers.get.return_value = fake_container
        return fake_docker, fake_container

    def test_create_sandbox_returns_container_ref(self):
        from src.sandbox.manager import SandboxManager
        fake_docker, fake_container = self._mock_docker()
        with patch("docker.from_env", return_value=fake_docker):
            ref = SandboxManager().create_sandbox(run_id="test-run-001")
        assert ref.run_id == "test-run-001"
        assert ref.container_id == fake_container.id

    def test_create_sandbox_network_disabled(self):
        from src.sandbox.manager import SandboxManager
        fake_docker, _ = self._mock_docker()
        with patch("docker.from_env", return_value=fake_docker):
            SandboxManager().create_sandbox(run_id="test-run-002")
        _, kwargs = fake_docker.containers.run.call_args
        assert kwargs.get("network_disabled") is True

    def test_create_sandbox_mounts_workspace_volume(self, tmp_path, monkeypatch):
        from src.sandbox import manager as mgr_mod
        from src.sandbox.manager import SandboxManager
        monkeypatch.setattr(mgr_mod, "VOLUMES_DIR", tmp_path)
        fake_docker, _ = self._mock_docker()
        with patch("docker.from_env", return_value=fake_docker):
            SandboxManager().create_sandbox(run_id="my-run")
        _, kwargs = fake_docker.containers.run.call_args
        assert any(v.get("bind") == "/workspace" for v in kwargs.get("volumes", {}).values())

    def test_create_sandbox_memory_limited(self):
        from src.sandbox.manager import SandboxManager
        fake_docker, _ = self._mock_docker()
        with patch("docker.from_env", return_value=fake_docker):
            SandboxManager().create_sandbox(run_id="mem-test")
        _, kwargs = fake_docker.containers.run.call_args
        assert "mem_limit" in kwargs

    def test_exec_in_sandbox_returns_exit_code_and_output(self):
        from src.sandbox.manager import SandboxManager, ContainerRef
        fake_docker, fake_container = self._mock_docker()
        fake_container.exec_run.return_value = (0, b"all good")
        with patch("docker.from_env", return_value=fake_docker):
            code, output = SandboxManager().exec_in_sandbox(
                ContainerRef(container_id="abc", run_id="r", image="omega-python-runner"),
                ["ruff", "check", "/workspace/"],
            )
        assert code == 0
        assert output == "all good"

    def test_exec_in_sandbox_nonzero_exit_code(self):
        from src.sandbox.manager import SandboxManager, ContainerRef
        fake_docker, fake_container = self._mock_docker()
        fake_container.exec_run.return_value = (1, b"E501 line too long")
        with patch("docker.from_env", return_value=fake_docker):
            code, output = SandboxManager().exec_in_sandbox(
                ContainerRef(container_id="abc", run_id="r", image="omega-python-runner"),
                ["ruff", "check", "/workspace/"],
            )
        assert code == 1
        assert "line too long" in output

    def test_destroy_sandbox_stops_and_removes_container(self):
        from src.sandbox.manager import SandboxManager, ContainerRef
        fake_docker, fake_container = self._mock_docker()
        with patch("docker.from_env", return_value=fake_docker):
            SandboxManager().destroy_sandbox(ContainerRef(container_id="abc", run_id="r", image="img"))
        fake_container.stop.assert_called_once()
        fake_container.remove.assert_called_once()

    def test_destroy_sandbox_handles_exception_gracefully(self):
        from src.sandbox.manager import SandboxManager, ContainerRef
        fake_docker = MagicMock()
        fake_docker.containers.get.side_effect = RuntimeError("container gone")
        with patch("docker.from_env", return_value=fake_docker):
            SandboxManager().destroy_sandbox(ContainerRef(container_id="abc", run_id="r", image="img"))


# ---------------------------------------------------------------------------
# TestSandboxedRunner
# ---------------------------------------------------------------------------


class TestSandboxedRunner:
    def test_run_in_sandbox_success(self, monkeypatch):
        from src.sandbox import manager as mgr_mod
        mock_mgr = MagicMock()
        mock_ref = MagicMock()
        mock_mgr.create_sandbox.return_value = mock_ref
        mock_mgr.exec_in_sandbox.return_value = (0, "ok")
        monkeypatch.setattr(mgr_mod, "_manager", mock_mgr)
        from src.tools.sandboxed_runner import run_in_sandbox
        code, out = run_in_sandbox("test-run", ["ruff", "check", "/workspace/"])
        assert code == 0
        assert out == "ok"
        mock_mgr.destroy_sandbox.assert_called_once_with(mock_ref)

    def test_run_in_sandbox_destroys_even_on_exec_exception(self, monkeypatch):
        from src.sandbox import manager as mgr_mod
        mock_mgr = MagicMock()
        mock_ref = MagicMock()
        mock_mgr.create_sandbox.return_value = mock_ref
        mock_mgr.exec_in_sandbox.side_effect = RuntimeError("container crashed")
        monkeypatch.setattr(mgr_mod, "_manager", mock_mgr)
        from src.tools.sandboxed_runner import run_in_sandbox
        with pytest.raises(RuntimeError):
            run_in_sandbox("test-run", ["pytest", "/workspace/tests/"])
        mock_mgr.destroy_sandbox.assert_called_once_with(mock_ref)


# ---------------------------------------------------------------------------
# TestRunnersWithSandbox — shape and path assertions
# ---------------------------------------------------------------------------


class TestRunnersWithSandbox:
    def test_run_ruff_uses_workspace_path(self, monkeypatch):
        import src.tools.runners as runners_mod
        monkeypatch.setattr(runners_mod, "SANDBOX_ENABLED", True)
        captured_cmd = []

        def patched_exec(run_id, cmd, timeout=120):
            captured_cmd.extend(cmd)
            return (0, "No lint issues found.")

        monkeypatch.setattr(runners_mod, "_exec", patched_exec)
        ev = runners_mod.run_ruff("my-run")
        assert ev.passed is True
        assert "/workspace/" in " ".join(captured_cmd)

    def test_run_pytest_uses_workspace_paths(self, monkeypatch):
        import src.tools.runners as runners_mod
        monkeypatch.setattr(runners_mod, "SANDBOX_ENABLED", True)
        captured_cmd = []

        def patched_exec(run_id, cmd, timeout=120):
            captured_cmd.extend(cmd)
            return (0, "1 passed")

        monkeypatch.setattr(runners_mod, "_exec", patched_exec)
        ev = runners_mod.run_pytest("my-run", min_coverage=80)
        assert ev.passed is True
        cmd_str = " ".join(captured_cmd)
        assert "/workspace/tests/" in cmd_str
        assert "/workspace/src/" in cmd_str

    def test_run_complexity_check_uses_workspace_path(self, monkeypatch):
        import src.tools.runners as runners_mod
        monkeypatch.setattr(runners_mod, "SANDBOX_ENABLED", True)
        captured_cmd = []

        def patched_exec(run_id, cmd, timeout=120):
            captured_cmd.extend(cmd)
            return (0, "")

        monkeypatch.setattr(runners_mod, "_exec", patched_exec)
        ev = runners_mod.run_complexity_check("my-run")
        assert ev.passed is True
        assert "/workspace/src/" in " ".join(captured_cmd)

    def test_sandbox_enabled_tools_return_tool_evidence(self, monkeypatch):
        import src.tools.runners as runners_mod
        monkeypatch.setattr(runners_mod, "SANDBOX_ENABLED", True)

        def patched_exec(run_id, cmd, timeout=120):
            return (0, "ok")

        monkeypatch.setattr(runners_mod, "_exec", patched_exec)
        for fn in (runners_mod.run_ruff, runners_mod.run_bandit):
            ev = fn("my-run")
            assert isinstance(ev, ToolEvidence)
            assert isinstance(ev.passed, bool)


# ---------------------------------------------------------------------------
# TestRunnersNoSandboxRegress — non-regression against Stage 3
# ---------------------------------------------------------------------------


class TestRunnersNoSandboxRegress:
    def test_ruff_passes_on_valid_code(self, tmp_dirs):
        import src.tools.runners as runners_mod
        run_id = "regress-ruff"
        src_dir = tmp_dirs / "volumes" / run_id / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "ok.py").write_text("x = 1\n")
        ev = runners_mod.run_ruff(run_id)
        assert ev.tool_name == "ruff"
        assert ev.passed is True

    def test_ruff_fails_on_bad_code(self, tmp_dirs):
        import src.tools.runners as runners_mod
        run_id = "regress-ruff-fail"
        src_dir = tmp_dirs / "volumes" / run_id / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "bad.py").write_text("import os,sys\n")
        ev = runners_mod.run_ruff(run_id)
        assert ev.tool_name == "ruff"
        assert ev.passed is False

    def test_mypy_skipped_when_not_enforced(self):
        import src.tools.runners as runners_mod
        ev = runners_mod.run_mypy("any-run", enforce=False)
        assert ev.passed is True
        assert "Skipped" in ev.findings

    def test_pip_audit_skipped_when_no_requirements(self, tmp_dirs):
        import src.tools.runners as runners_mod
        run_id = "no-req"
        (tmp_dirs / "volumes" / run_id).mkdir(parents=True)
        ev = runners_mod.run_pip_audit(run_id)
        assert ev.passed is True
        assert "Skipped" in ev.findings

    def test_all_runners_return_tool_evidence(self, tmp_dirs):
        import src.tools.runners as runners_mod
        # Create a minimal volume under tmp_dirs/volumes
        run_id = "stage5-regress-run"
        src_dir = tmp_dirs / "volumes" / run_id / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "req_001_impl.py").write_text("def add(a, b): return a + b\n")
        for ev in [
            runners_mod.run_ruff(run_id),
            runners_mod.run_mypy(run_id, enforce=False),
            runners_mod.run_pip_audit(run_id),
        ]:
            assert isinstance(ev, ToolEvidence)
            assert isinstance(ev.passed, bool)


# ---------------------------------------------------------------------------
# TestComplexityCheckReal — real xenon on a stub volume
# ---------------------------------------------------------------------------


class TestComplexityCheckReal:
    def test_complexity_check_is_not_stub(self, tmp_path, monkeypatch):
        import src.tools.runners as runners_mod
        monkeypatch.setattr(runners_mod, "VOLUMES_DIR", tmp_path)
        run_id = _make_stub_volume(tmp_path)
        ev = runners_mod.run_complexity_check(run_id)
        assert "Stub" not in ev.findings
        assert ev.tool_name == "complexity"

    def test_complexity_check_passes_on_simple_code(self, tmp_path, monkeypatch):
        import src.tools.runners as runners_mod
        monkeypatch.setattr(runners_mod, "VOLUMES_DIR", tmp_path)
        run_id = _make_stub_volume(tmp_path)
        ev = runners_mod.run_complexity_check(run_id)
        assert ev.passed is True, f"Expected xenon to pass on simple code, got: {ev.findings}"

    def test_complexity_check_tool_evidence_shape(self, tmp_path, monkeypatch):
        import src.tools.runners as runners_mod
        monkeypatch.setattr(runners_mod, "VOLUMES_DIR", tmp_path)
        run_id = _make_stub_volume(tmp_path)
        ev = runners_mod.run_complexity_check(run_id)
        assert isinstance(ev, ToolEvidence)
        assert ev.tool_name == "complexity"
        assert isinstance(ev.passed, bool)
        assert ev.diagnosis is None


# ---------------------------------------------------------------------------
# TestGetSandboxManager — only the testability contract matters
# ---------------------------------------------------------------------------


class TestGetSandboxManager:
    def test_singleton_is_replaceable_via_monkeypatch(self, monkeypatch):
        """The _manager singleton can be replaced for testing."""
        import src.sandbox.manager as mgr_mod
        mock = MagicMock()
        monkeypatch.setattr(mgr_mod, "_manager", mock)
        from src.sandbox.manager import get_sandbox_manager
        assert get_sandbox_manager() is mock


# ---------------------------------------------------------------------------
# TestSandboxPerformanceOptimizations
# ---------------------------------------------------------------------------


class TestSandboxPerformanceOptimizations:
    def test_deps_install_skips_when_requirements_hash_unchanged(self, tmp_path, monkeypatch):
        import src.tools.runners as runners_mod
        run_id = "deps-cache"
        run_dir = tmp_path / run_id
        deps_dir = run_dir / ".deps"
        deps_dir.mkdir(parents=True)
        (deps_dir / "installed.txt").write_text("ok")
        req_file = run_dir / "requirements.txt"
        req_file.write_text("fastapi\n")
        monkeypatch.setattr(runners_mod, "VOLUMES_DIR", tmp_path)
        runners_mod._write_deps_hash(run_id, runners_mod._requirements_hash(req_file))
        runners_mod._deps_installed.discard(run_id)

        mock_mgr = MagicMock()
        monkeypatch.setattr("src.sandbox.manager._manager", mock_mgr)
        runners_mod._ensure_deps_installed(run_id)
        mock_mgr.install_deps.assert_not_called()
        assert run_id in runners_mod._deps_installed

    def test_deps_install_runs_when_requirements_hash_changes(self, tmp_path, monkeypatch):
        import src.tools.runners as runners_mod
        run_id = "deps-cache-changed"
        run_dir = tmp_path / run_id
        deps_dir = run_dir / ".deps"
        deps_dir.mkdir(parents=True)
        (deps_dir / "installed.txt").write_text("ok")
        req_file = run_dir / "requirements.txt"
        req_file.write_text("fastapi\n")
        monkeypatch.setattr(runners_mod, "VOLUMES_DIR", tmp_path)
        runners_mod._write_deps_hash(run_id, "old-hash")
        runners_mod._deps_installed.discard(run_id)

        mock_mgr = MagicMock()
        mock_mgr.install_deps.return_value = (0, "installed")
        monkeypatch.setattr("src.sandbox.manager._manager", mock_mgr)
        runners_mod._ensure_deps_installed(run_id)
        mock_mgr.install_deps.assert_called_once_with(run_id)
        assert runners_mod._deps_hash_matches(run_id, runners_mod._requirements_hash(req_file))

    def test_shared_review_sandbox_reuses_one_container_and_cleans_up(self, monkeypatch):
        import src.tools.runners as runners_mod
        monkeypatch.setattr(runners_mod, "SANDBOX_ENABLED", True)
        monkeypatch.setattr(runners_mod, "_ensure_deps_installed", MagicMock())

        mock_ref = MagicMock()
        mock_ref.run_id = "review-run"
        mock_mgr = MagicMock()
        mock_mgr.create_sandbox.return_value = mock_ref
        mock_mgr.exec_in_sandbox.side_effect = [(0, "ruff ok"), (0, "pytest ok")]
        monkeypatch.setattr("src.sandbox.manager._manager", mock_mgr)

        with runners_mod.shared_review_sandbox("review-run"):
            runners_mod._exec("review-run", ["ruff", "check", "/workspace/src/"])
            runners_mod._exec("review-run", ["pytest", "/workspace/tests/"])

        mock_mgr.create_sandbox.assert_called_once()
        assert mock_mgr.exec_in_sandbox.call_count == 2
        mock_mgr.destroy_sandbox.assert_called_once_with(mock_ref)
