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


def _exec(run_id: str, cmd: list[str], timeout: int = TOOL_TIMEOUT) -> tuple[int, str]:
    """Route *cmd* through the sandbox or via subprocess depending on the flag.

    When sandboxed, *cmd* must already use ``/workspace/...`` paths.
    When not sandboxed, *cmd* must already use ``volumes/{run_id}/...`` paths.
    """
    if SANDBOX_ENABLED:
        from src.tools.sandboxed_runner import run_in_sandbox
        return run_in_sandbox(run_id=run_id, cmd=cmd, image=PYTHON_RUNNER_IMAGE, timeout_seconds=timeout)
    return _run_host(cmd)


# ---------------------------------------------------------------------------
# Required tools
# ---------------------------------------------------------------------------

def run_ruff(run_id: str) -> ToolEvidence:
    if SANDBOX_ENABLED:
        cmd = ["ruff", "check", "/workspace/"]
    else:
        cmd = ["ruff", "check", str(_volume(run_id))]
    returncode, output = _exec(run_id, cmd)
    passed = returncode == 0
    findings = output or "No lint issues found."
    logger.info("[ruff] %s", "PASS" if passed else f"FAIL -- {findings[:120]}")
    return ToolEvidence(tool_name="ruff", passed=passed, findings=findings)


def run_pytest(run_id: str, min_coverage: int = 80) -> ToolEvidence:
    if SANDBOX_ENABLED:
        cmd = [
            "pytest", "/workspace/tests/",
            "--cov=/workspace/src/",
            f"--cov-fail-under={min_coverage}",
            "--tb=short", "-q",
        ]
    else:
        volume_dir = _volume(run_id)
        cmd = [
            "pytest", str(volume_dir / "tests"),
            f"--cov={volume_dir / 'src'}",
            f"--cov-fail-under={min_coverage}",
            "--tb=short", "-q",
        ]
    returncode, output = _exec(run_id, cmd)
    passed = returncode == 0
    findings = output or "(no pytest output)"
    logger.info("[pytest] %s (coverage >= %d%%)", "PASS" if passed else "FAIL", min_coverage)
    return ToolEvidence(tool_name="pytest", passed=passed, findings=findings)


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
    logger.info("[mypy] %s", "PASS" if passed else "FAIL")
    return ToolEvidence(tool_name="mypy", passed=passed, findings=findings)


def run_bandit(run_id: str) -> ToolEvidence:
    if SANDBOX_ENABLED:
        cmd = ["bandit", "-r", "/workspace/src/", "-q"]
    else:
        cmd = ["bandit", "-r", str(_volume(run_id) / "src"), "-q"]
    returncode, output = _exec(run_id, cmd)
    passed = returncode == 0
    findings = output or "No security issues found."
    logger.info("[bandit] %s", "PASS" if passed else "FAIL")
    return ToolEvidence(tool_name="bandit", passed=passed, findings=findings)


def run_pip_audit(run_id: str) -> ToolEvidence:
    req_file = _volume(run_id) / "requirements.txt"
    if not req_file.exists():
        return ToolEvidence(tool_name="pip-audit", passed=True, findings="Skipped -- no requirements.txt present.")
    if SANDBOX_ENABLED:
        cmd = ["pip-audit", "-r", "/workspace/requirements.txt"]
    else:
        cmd = ["pip-audit", "-r", str(req_file)]
    returncode, output = _exec(run_id, cmd)
    passed = returncode == 0
    findings = output or "(no pip-audit output)"
    logger.info("[pip-audit] %s", "PASS" if passed else "FAIL")
    return ToolEvidence(tool_name="pip-audit", passed=passed, findings=findings)


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
    logger.info("[complexity] %s (threshold=%d)", "PASS" if passed else "FAIL", max_complexity)
    return ToolEvidence(tool_name="complexity", passed=passed, findings=findings)
