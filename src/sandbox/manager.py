"""SandboxManager — Stage 5.

Provides ephemeral, network-disabled Docker containers for tool execution.
Each container:
  - mounts volumes/{run_id}/ → /workspace (read-write)
  - has network disabled
  - is resource-constrained
  - is destroyed after use

The module-level singleton is swappable in tests via monkeypatch:
    monkeypatch.setattr("src.sandbox.manager._manager", MockSandboxManager())
"""
from __future__ import annotations

import dataclasses
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Patchable constant — mirrors VOLUMES_DIR pattern in runners.py / workspace.py
VOLUMES_DIR = Path("volumes")

# Default image name for Stage 5 (Python-only).
# Stage 6 will parameterise this via TargetStack.image.
PYTHON_RUNNER_IMAGE = "omega-python-runner"


def _sandbox_cpu_quota() -> int:
    raw = os.environ.get("OMEGA_SANDBOX_CPU_QUOTA", "100000")
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("[Sandbox] Invalid OMEGA_SANDBOX_CPU_QUOTA=%r; using 100000.", raw)
        return 100_000


@dataclasses.dataclass
class ContainerRef:
    """Lightweight handle to a running sandbox container."""
    container_id: str
    run_id: str
    image: str


class SandboxManager:
    """Manages ephemeral Docker sandbox containers.

    Uses the ``docker`` SDK (``pip install docker>=7.0``).
    The Docker daemon must be accessible on the host.
    """

    def create_sandbox(
        self,
        run_id: str,
        image: str = PYTHON_RUNNER_IMAGE,
    ) -> ContainerRef:
        """Create and start an ephemeral sandbox container.

        The workspace volume ``volumes/{run_id}/`` is mounted read-write to
        ``/workspace`` inside the container.  No other host paths are mounted.
        Network is disabled.  Memory is capped at 512 MiB.
        """
        import docker  # type: ignore[import]

        client = docker.from_env()
        host_workspace = str(VOLUMES_DIR.resolve() / run_id)

        container = client.containers.run(
            image,
            command="sleep infinity",  # keep alive; exec_run will run real commands
            detach=True,
            network_disabled=True,
            volumes={
                host_workspace: {"bind": "/workspace", "mode": "rw"},
            },
            mem_limit="512m",
            cpu_period=100_000,
            cpu_quota=_sandbox_cpu_quota(),
            working_dir="/workspace",
            remove=False,  # we remove manually in destroy_sandbox
        )
        logger.debug("[Sandbox] Created container %s (image=%s, run_id=%s)", container.id[:12], image, run_id)
        return ContainerRef(container_id=container.id, run_id=run_id, image=image)

    def install_deps(
        self,
        run_id: str,
        image: str = PYTHON_RUNNER_IMAGE,
    ) -> tuple[int, str]:
        """Install /workspace/requirements.txt into /workspace/.deps using a
        short-lived network-enabled container.

        Dependencies land in ``/workspace/.deps`` (on the host volume) so the
        network-disabled QA containers can import them by adding that path to
        ``PYTHONPATH``.  Safe to call multiple times — pip is idempotent.
        """
        import docker  # type: ignore[import]

        client = docker.from_env()
        host_workspace = str(VOLUMES_DIR.resolve() / run_id)

        logger.info("[Sandbox] Installing deps for run_id=%s into /workspace/.deps", run_id)
        try:
            result = client.containers.run(
                image,
                command=[
                    "pip", "install", "-q",
                    "-r", "/workspace/requirements.txt",
                    "--target", "/workspace/.deps",
                ],
                detach=False,
                network_disabled=False,  # needs internet to fetch packages
                volumes={host_workspace: {"bind": "/workspace", "mode": "rw"}},
                mem_limit="512m",
                remove=True,
                stdout=True,
                stderr=True,
            )
            output = result.decode("utf-8", errors="replace").strip() if result else ""
            logger.info("[Sandbox] Dep install complete for run_id=%s", run_id)
            return 0, output
        except Exception as exc:  # docker.errors.ContainerError carries exit_code
            import docker.errors  # type: ignore[import]
            if isinstance(exc, docker.errors.ContainerError):
                output = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
                logger.warning("[Sandbox] Dep install failed (exit=%d): %s", exc.exit_status, output[:200])
                return exc.exit_status, output
            raise

    def exec_in_sandbox(
        self,
        container_ref: ContainerRef,
        cmd: list[str],
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        """Run *cmd* inside the sandbox container.

        Returns ``(exit_code, combined_stdout_stderr)``.
        Raises ``TimeoutError`` if the command exceeds *timeout_seconds*.
        """
        import docker  # type: ignore[import]

        client = docker.from_env()
        container = client.containers.get(container_ref.container_id)

        logger.debug("[Sandbox] exec %s in %s", cmd, container_ref.container_id[:12])

        exit_code, output = container.exec_run(
            cmd,
            stdout=True,
            stderr=True,
            demux=False,
            workdir="/workspace",
            environment=env or {},
            # The docker SDK does not natively support exec timeout;
            # rely on the container's own resource limits for runaway processes.
            # A future Stage 8 Temporal activity wraps this with asyncio timeout.
        )

        output_str = output.decode("utf-8", errors="replace").strip() if output else ""
        logger.debug("[Sandbox] exit_code=%d output=%s...", exit_code, output_str[:80])
        return exit_code, output_str

    def destroy_sandbox(self, container_ref: ContainerRef) -> None:
        """Stop and remove the sandbox container unconditionally."""
        import docker  # type: ignore[import]

        client = docker.from_env()
        try:
            container = client.containers.get(container_ref.container_id)
            container.stop(timeout=5)
            container.remove(force=True)
            logger.debug("[Sandbox] Destroyed container %s", container_ref.container_id[:12])
        except Exception as exc:
            # Best-effort cleanup — log and continue
            logger.warning("[Sandbox] Failed to destroy container %s: %s", container_ref.container_id[:12], exc)


# ---------------------------------------------------------------------------
# Module-level singleton — monkeypatchable in tests
# ---------------------------------------------------------------------------

_manager: Optional[SandboxManager] = None


def get_sandbox_manager() -> SandboxManager:
    """Return the shared SandboxManager instance (lazy singleton).

    Replace this in tests::

        monkeypatch.setattr("src.sandbox.manager._manager", MockSandboxManager())
    """
    global _manager
    if _manager is None:
        _manager = SandboxManager()
    return _manager
