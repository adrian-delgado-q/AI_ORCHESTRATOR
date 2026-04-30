"""Tool runners — Stage 3.

Each function runs a subprocess tool against volumes/{run_id}/ and returns a
ToolEvidence record.  Required tools: ruff, pytest.  Optional (skippable):
mypy, bandit, pip-audit.  Complexity check is a stub until Stage 5.

The module-level VOLUMES_DIR constant is monkeypatchable in tests — mirror the
same pattern used in src/io/workspace.py.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from src.state.schema import ToolEvidence

logger = logging.getLogger(__name__)

# Patchable in tests via monkeypatch
VOLUMES_DIR = Path("volumes")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str]) -> tuple[int, str]:
    """Run *cmd* via subprocess and return (returncode, combined stdout+stderr)."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def _volume(run_id: str) -> Path:
    return VOLUMES_DIR / run_id


# ---------------------------------------------------------------------------
# Required tools
# ---------------------------------------------------------------------------

def run_ruff(run_id: str) -> ToolEvidence:
    """Lint-check volumes/{run_id}/ with ruff.  Required gate."""
    volume_dir = _volume(run_id)
    returncode, output = _run(["ruff", "check", str(volume_dir)])
    passed = returncode == 0
    findings = output or "No lint issues found."
    logger.info("[ruff] %s", "PASS" if passed else f"FAIL — {findings[:120]}")
    return ToolEvidence(tool_name="ruff", passed=passed, findings=findings)


def run_pytest(run_id: str, min_coverage: int = 80) -> ToolEvidence:
    """Run pytest with coverage on volumes/{run_id}/.  Required gate.

    Discovers tests under volumes/{run_id}/tests/ and measures coverage of
    volumes/{run_id}/src/.  Fails if coverage is below *min_coverage*.
    """
    volume_dir = _volume(run_id)
    src_dir = volume_dir / "src"
    tests_dir = volume_dir / "tests"

    cmd = [
        "pytest",
        str(tests_dir),
        f"--cov={src_dir}",
        f"--cov-fail-under={min_coverage}",
        "--tb=short",
        "-q",
    ]
    returncode, output = _run(cmd)
    passed = returncode == 0
    findings = output or "(no pytest output)"
    logger.info("[pytest] %s (coverage >= %d%%)", "PASS" if passed else "FAIL", min_coverage)
    return ToolEvidence(tool_name="pytest", passed=passed, findings=findings)


# ---------------------------------------------------------------------------
# Optional tools
# ---------------------------------------------------------------------------

def run_mypy(run_id: str, enforce: bool = True) -> ToolEvidence:
    """Type-check volumes/{run_id}/src/ with mypy.

    Skipped (returns passing stub) when *enforce* is False — controlled by
    OmegaGoal.quality_thresholds.enforce_type_hints.
    """
    if not enforce:
        return ToolEvidence(
            tool_name="mypy",
            passed=True,
            findings="Skipped — enforce_type_hints=false.",
        )

    src_dir = _volume(run_id) / "src"
    returncode, output = _run(["mypy", str(src_dir), "--ignore-missing-imports"])
    passed = returncode == 0
    findings = output or "(no mypy output)"
    logger.info("[mypy] %s", "PASS" if passed else "FAIL")
    return ToolEvidence(tool_name="mypy", passed=passed, findings=findings)


def run_bandit(run_id: str) -> ToolEvidence:
    """Security-scan volumes/{run_id}/src/ with bandit."""
    src_dir = _volume(run_id) / "src"
    returncode, output = _run(["bandit", "-r", str(src_dir), "-q"])
    passed = returncode == 0
    findings = output or "No security issues found."
    logger.info("[bandit] %s", "PASS" if passed else "FAIL")
    return ToolEvidence(tool_name="bandit", passed=passed, findings=findings)


def run_pip_audit(run_id: str) -> ToolEvidence:
    """Audit volumes/{run_id}/requirements.txt with pip-audit.

    Skipped (returns passing stub) when requirements.txt is absent.
    """
    req_file = _volume(run_id) / "requirements.txt"
    if not req_file.exists():
        return ToolEvidence(
            tool_name="pip-audit",
            passed=True,
            findings="Skipped — no requirements.txt present.",
        )

    returncode, output = _run(["pip-audit", "-r", str(req_file)])
    passed = returncode == 0
    findings = output or "(no pip-audit output)"
    logger.info("[pip-audit] %s", "PASS" if passed else "FAIL")
    return ToolEvidence(tool_name="pip-audit", passed=passed, findings=findings)


def run_complexity_check(run_id: str, max_complexity: int = 10) -> ToolEvidence:
    """Cyclomatic-complexity check — stub implementation.

    Interface is frozen here; real radon/xenon integration arrives in Stage 5.
    Always returns passing evidence in Stage 3.
    """
    logger.info("[complexity] Stub — always passes in Stage 3 (threshold=%d).", max_complexity)
    return ToolEvidence(
        tool_name="complexity",
        passed=True,
        findings=f"Stub — real check in Stage 5 (max_complexity={max_complexity}).",
    )
