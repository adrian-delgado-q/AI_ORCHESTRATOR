"""State persistence — writes SDLCState to runs/{run_id}/state.json."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.state.schema import SDLCState

logger = logging.getLogger(__name__)

RUNS_DIR = Path("runs")


def save_state(state: SDLCState) -> Path:
    """Serialize state to runs/{run_id}/state.json and return the path."""
    run_dir = RUNS_DIR / state.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "state.json"
    path.write_text(
        json.dumps(state.model_dump(), indent=2, default=str),
        encoding="utf-8",
    )
    logger.debug("State saved → %s", path)
    return path


def load_state(run_id: str) -> SDLCState:
    """Load a previously saved state from disk."""
    path = RUNS_DIR / run_id / "state.json"
    if not path.exists():
        raise FileNotFoundError(f"No state found for run '{run_id}' at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return SDLCState.model_validate(data)
