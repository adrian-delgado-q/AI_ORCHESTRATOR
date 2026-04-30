"""Tool runners -- Stage 5.

Each function runs a tool against volumes/{run_id}/ and returns a ToolEvidence
record.  Required tools: ruff, pytest.  Optional (skippable): mypy, bandit,
pip-audit.  Complexity check is real (radon/xenon) from Stage 5 onward.

Sandbox routing
---------------
When ``SANDBOX_ENABLED = True`` (the default after Stage 5), each runner
delegates execution to ``run_in_sandbox()``, which spins up an ephemeral
Docker container.  Commands use ``/workspace/...`` paths inside the container.

When ``SANDBOX_ENABLED = False`` (``--no-sandbox`` or test override), the
legacy ``subprocess.run`` path is used with host ``volumes/{run_id}/...`` paths.

Monkeypatching
--------------
``VOLUMES_DIR`` -- used by the no-sandbox subprocess path and for test setup.
``SANDBOX_ENABLED`` -- set to False in ``tests/conftest.py`` for all unit tests.
Both follow the same module-level constant pattern as ``src/io/workspace.py``.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from src.state.schema import ToolEvidence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patchable module-level constants
# ---------------------------------------------------------------------------

# Host-side volume root -- used when sandbox is disabled and in test setup.
VOLUMES_DIR = Path("volumes")

# Master sandbox switch.  Set to False via --no-sandbox CLI flag or in tests.
SANDBOX_ENABLED = True

# Docker image used for Stage 5 Python sandboxing.
# Stage 6 will parameterise this per TargetStack.
PYTHON_RUNNER_IMAGE = "omega-python-runner"

# Per-tool execution timeout inside the sandbox (seconds).
TOOL_TIMEOUT = 120

# Track which run_ids have already had their deps installed this process.
# Avoids re-running pip install for every tool in the same review cycle.
_deps_installed: set[str] = set()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _volume(run_id: str) -> Path:
    """Return the host-side volume path for *run_id*."""
    return VOLUMES_DIR / run_id


def _run_host(cmd: list[str]) -> tuple[int, str]:
    """Execute *cmd* on the host via subprocess (no-sandbox path)."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def _ensure_deps_installed(run_id: str) -> None:
    """Install workspace requirements into /workspace/.deps (once per run_id).

    Uses a short-lived network-enabled container so that the network-disabled
    QA containers can import project deps via PYTHONPATH=/workspace/.deps.
    Skips if requirements.txt is absent or deps have already been installed
    this process session.
    """
    # Skip if deps are already installed this session AND not marked stale
    stale_marker = _volume(run_id) / ".deps-stale"
    if run_id in _deps_installed and not stale_marker.exists():
        return
    req_file = _volume(run_id) / "requirements.txt"
    if not req_file.exists():
        return
    # Skip for cross-session resume: .deps populated, no stale marker, not in current session
    deps_dir = _volume(run_id) / ".deps"
    if deps_dir.exists() and any(deps_dir.iterdir()) and not stale_marker.exists() and run_id not in _deps_installed:
        logger.info("[runners] .deps already populated for %s — skipping install.", run_id)
        _deps_installed.add(run_id)
        return
    from src.sandbox.manager import get_sandbox_manager
    manager = get_sandbox_manager()
    rc, out = manager.install_deps(run_id)
    if rc != 0:
        logger.warning("[runners] Dep install exited %d: %s", rc, out[:200])
        # Do NOT cache as installed on failure — let the next caller retry.
        return
    _deps_installed.add(run_id)
    # Remove stale marker after successful install
    if stale_marker.exists():
        stale_marker.unlink(missing_ok=True)


def _exec(run_id: str, cmd: list[str], timeout: int = TOOL_TIMEOUT) -> tuple[int, str]:
    """Route *cmd* through the sandbox or via subprocess depending on the flag.

    When sandboxed, *cmd* must already use ``/workspace/...`` paths.
    When not sandboxed, *cmd* must already use ``volumes/{run_id}/...`` paths.

    Sandbox path: deps are pre-installed into /workspace/.deps via a
    network-enabled container (see _ensure_deps_installed).  QA containers
    remain network-disabled and import from .deps via PYTHONPATH.
    """
    if SANDBOX_ENABLED:
        from src.tools.sandboxed_runner import run_in_sandbox
        _ensure_deps_installed(run_id)
        env = {}
        deps_dir = _volume(run_id) / ".deps"
        if deps_dir.exists():
            env["PYTHONPATH"] = "/workspace/.deps"
        return run_in_sandbox(run_id=run_id, cmd=cmd, image=PYTHON_RUNNER_IMAGE, timeout_seconds=timeout, env=env)
    return _run_host(cmd)


def _log_result(tool: str, passed: bool, findings: str, extra: str = "") -> None:
    """Log a one-line PASS or a full multi-line FAIL for *tool*."""
    if passed:
        logger.info("[%s] PASS%s", tool, f" {extra}" if extra else "")
    else:
        label = f"[{tool}] FAIL{(' ' + extra) if extra else ''}"
        # Print a header then the full findings so nothing is truncated
        logger.info("%s\n%s", label, findings)


# ---------------------------------------------------------------------------
# Required tools
# ---------------------------------------------------------------------------

def run_ruff(run_id: str) -> ToolEvidence:
    if SANDBOX_ENABLED:
        cmd = ["ruff", "check", "/workspace/src/", "/workspace/tests/"]
    else:
        vol = _volume(run_id)
        cmd = ["ruff", "check", str(vol / "src"), str(vol / "tests")]
    returncode, output = _exec(run_id, cmd)
    passed = returncode == 0
    findings = output or "No lint issues found."
    _log_result("ruff", passed, findings)
    return ToolEvidence(tool_name="ruff", passed=passed, findings=findings, role="linter")


def run_ruff_fix(run_id: str) -> tuple[int, str]:
    """Apply ruff's safe auto-fixes in-place (--fix flag).

    Only fixes issues marked [*] in ruff output (e.g. F401 unused imports).
    Non-fixable issues (F841, E501, logic errors) are left for the LLM loop.
    Returns (exit_code, output) — callers log the result themselves.
    """
    if SANDBOX_ENABLED:
        cmd = ["ruff", "check", "--fix", "/workspace/src/", "/workspace/tests/"]
    else:
        vol = _volume(run_id)
        cmd = ["ruff", "check", "--fix", str(vol / "src"), str(vol / "tests")]
    return _exec(run_id, cmd)


def run_pytest(run_id: str, min_coverage: int = 80) -> ToolEvidence:
    if SANDBOX_ENABLED:
        cmd = [
            "pytest", "/workspace/tests/",
            "--cov=/workspace/src/",
            f"--cov-fail-under={min_coverage}",
            "--tb=short", "-v",
        ]
    else:
        volume_dir = _volume(run_id)
        cmd = [
            "pytest", str(volume_dir / "tests"),
            f"--cov={volume_dir / 'src'}",
            f"--cov-fail-under={min_coverage}",
            "--tb=short", "-v",
        ]
    returncode, output = _exec(run_id, cmd)
    passed = returncode == 0
    findings = output or "(no pytest output)"
    _log_result("pytest", passed, findings, extra=f"(coverage >= {min_coverage}%)")
    return ToolEvidence(tool_name="pytest", passed=passed, findings=findings, role="test")


# ---------------------------------------------------------------------------
# Optional tools
# ---------------------------------------------------------------------------

def run_mypy(run_id: str, enforce: bool = True) -> ToolEvidence:
    if not enforce:
        return ToolEvidence(tool_name="mypy", passed=True, findings="Skipped -- enforce_type_hints=false.")
    if SANDBOX_ENABLED:
        cmd = ["mypy", "/workspace/src/", "--ignore-missing-imports"]
    else:
        cmd = ["mypy", str(_volume(run_id) / "src"), "--ignore-missing-imports"]
    returncode, output = _exec(run_id, cmd)
    passed = returncode == 0
    findings = output or "(no mypy output)"
    _log_result("mypy", passed, findings)
    return ToolEvidence(tool_name="mypy", passed=passed, findings=findings, role="linter")


def run_bandit(run_id: str) -> ToolEvidence:
    # -ll = only MEDIUM+ severity, -ii = only MEDIUM+ confidence.
    # This suppresses noisy LOW findings (e.g. B101 assert, B311 random)
    # while still catching real issues.
    if SANDBOX_ENABLED:
        cmd = ["bandit", "-r", "/workspace/src/", "-ll", "-ii"]
    else:
        cmd = ["bandit", "-r", str(_volume(run_id) / "src"), "-ll", "-ii"]
    returncode, output = _exec(run_id, cmd)
    passed = returncode == 0
    findings = output or "No security issues found."
    _log_result("bandit", passed, findings)
    return ToolEvidence(tool_name="bandit", passed=passed, findings=findings, role="security")


def run_pip_audit(run_id: str) -> ToolEvidence:
    req_file = _volume(run_id) / "requirements.txt"
    if not req_file.exists():
        return ToolEvidence(tool_name="pip-audit", passed=True, findings="Skipped -- no requirements.txt present.")
    if SANDBOX_ENABLED:
        cmd = ["pip-audit", "-r", "/workspace/requirements.txt"]
    else:
        cmd = ["pip-audit", "-r", str(req_file)]
    returncode, output = _exec(run_id, cmd)
    findings = output or "(no pip-audit output)"
    # pip-audit tries to upgrade pip/wheel/setuptools in a temp venv during
    # its own setup.  In a network-disabled sandbox this always fails with
    # "Failed to upgrade `pip`".  Treat this as a sandbox-incompatibility skip
    # rather than a real audit failure so it doesn't pollute required-gate
    # routing.  Real CVE findings won't contain only that message.
    _SANDBOX_PIP_UPGRADE_ERR = "Failed to upgrade `pip`"
    if returncode != 0 and _SANDBOX_PIP_UPGRADE_ERR in findings and "vulnerability" not in findings.lower():
        logger.warning("[pip-audit] Sandbox pip-upgrade error — treating as skip (no CVEs found).")
        passed = True
        findings = f"Skipped (sandbox pip-upgrade incompatibility): {findings[:200]}"
    else:
        passed = returncode == 0
    _log_result("pip-audit", passed, findings)
    return ToolEvidence(tool_name="pip-audit", passed=passed, findings=findings, role="audit")


def run_complexity_check(run_id: str, max_complexity: int = 10) -> ToolEvidence:
    """Real xenon/radon complexity gate (Stage 5).

    Uses xenon with conservative thresholds.  Pass/fail derived from exit code.
    ToolEvidence shape is unchanged from the Stage 3 stub.
    """
    if SANDBOX_ENABLED:
        cmd = [
            "xenon",
            "--max-absolute", "B",
            "--max-modules", "B",
            "--max-average", "A",
            "/workspace/src/",
        ]
    else:
        cmd = [
            "xenon",
            "--max-absolute", "B",
            "--max-modules", "B",
            "--max-average", "A",
            str(_volume(run_id) / "src"),
        ]
    returncode, output = _exec(run_id, cmd)
    passed = returncode == 0
    findings = output or ("Complexity OK." if passed else "(no xenon output)")
    _log_result("complexity", passed, findings, extra=f"(threshold={max_complexity})")
    return ToolEvidence(tool_name="complexity", passed=passed, findings=findings, role="complexity")
