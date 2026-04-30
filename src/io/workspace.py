"""Workspace I/O — Stage 2.

All generated code is written to volumes/{run_id}/ on disk.
State only stores FileChange (path + hash), never raw code.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from src.state.schema import FileChange

logger = logging.getLogger(__name__)

# Patchable in tests via monkeypatch
VOLUMES_DIR = Path("volumes")


def write_file(
    run_id: str,
    path: str,
    content: str,
    requirement_id: str,
    rationale: str,
) -> FileChange:
    """Write content to volumes/{run_id}/{path} and return a FileChange.

    Parent directories are created automatically.
    The hash is sha256 of the UTF-8 encoded content.
    """
    target = VOLUMES_DIR / run_id / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    logger.debug("Wrote %s → %s (sha256=%s…)", path, target, content_hash[:12])

    return FileChange(
        path=path,
        requirement_id=requirement_id,
        rationale=rationale,
        hash=content_hash,
    )


def read_file(run_id: str, path: str) -> str:
    """Read content from volumes/{run_id}/{path}.

    Raises FileNotFoundError if the file does not exist.
    """
    target = VOLUMES_DIR / run_id / path
    if not target.exists():
        raise FileNotFoundError(f"File not found in volume: {target}")
    return target.read_text(encoding="utf-8")
