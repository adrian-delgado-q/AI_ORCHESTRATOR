"""Sandboxed runner — Stage 5.

Thin wrapper that creates an ephemeral sandbox container, runs a single
command, then destroys the container unconditionally.

Usage inside tool runners::

    from src.tools.sandboxed_runner import run_in_sandbox

    exit_code, output = run_in_sandbox(
        run_id="example-goal-001",
        cmd=["ruff", "check", "/workspace/"],
        image="omega-python-runner",
        timeout_seconds=60,
    )

The ``destroy_sandbox`` call is always made in a ``finally`` block so leaking
containers is not possible even on exceptions.
"""
from __future__ import annotations

import logging

from src.sandbox.manager import get_sandbox_manager

logger = logging.getLogger(__name__)


def run_in_sandbox(
    run_id: str,
    cmd: list[str],
    image: str = "omega-python-runner",
    timeout_seconds: int = 120,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Execute *cmd* inside a fresh ephemeral sandbox and return ``(exit_code, output)``.

    The sandbox container is always destroyed after execution, even on error.
    *env* is forwarded to the container as extra environment variables.
    """
    manager = get_sandbox_manager()
    container_ref = manager.create_sandbox(run_id=run_id, image=image)
    try:
        return manager.exec_in_sandbox(container_ref, cmd, timeout_seconds=timeout_seconds, env=env)
    finally:
        manager.destroy_sandbox(container_ref)
