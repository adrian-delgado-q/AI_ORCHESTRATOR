"""Lean Omega — CLI entrypoint.

Usage:
    python main.py --goal config/goals/example_goal.yaml --mode local
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.agents.graph import omega_graph
from src.core.goal import load_goal
from src.state.persistence import save_state
from src.state.schema import SDLCState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_local(goal_path: str, resume: bool = False) -> SDLCState:
    """Load a goal file and run the Omega graph in local mode.

    When *resume* is True and a prior state exists for the run_id, the saved
    state is loaded and the graph continues from where it left off.
    """
    from src.state.persistence import load_state

    logger.info("Loading goal from: %s", goal_path)
    goal = load_goal(goal_path)
    logger.info("Goal validated: %s — %s", goal.goal_id, goal.objective)

    # Provision per-run volume directory
    volume_dir = Path("volumes") / goal.goal_id
    volume_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Volume directory provisioned: %s", volume_dir)

    # Load or initialise state
    if resume:
        try:
            initial_state = load_state(goal.goal_id)
            logger.info(
                "Resuming run '%s' from phase='%s' (loop=%d).",
                goal.goal_id, initial_state.current_phase, initial_state.loop_count,
            )
        except FileNotFoundError:
            logger.warning("No saved state for '%s' — starting fresh.", goal.goal_id)
            initial_state = SDLCState.from_goal(goal)
            save_state(initial_state)
    else:
        initial_state = SDLCState.from_goal(goal)
        save_state(initial_state)
        logger.info("Initial state saved.")
        # Clear cached deps so the new run re-installs from scratch
        deps_dir = volume_dir / ".deps"
        stale = volume_dir / ".deps-stale"
        if deps_dir.exists():
            import shutil
            try:
                shutil.rmtree(deps_dir)
                stale.unlink(missing_ok=True)  # clean state
                logger.info("Cleared stale .deps cache for fresh run.")
            except PermissionError:
                # Docker wrote .deps as root; mark stale to force reinstall
                stale.touch()
                logger.info(".deps owned by root — marked stale, will reinstall on next review.")
        else:
            stale.unlink(missing_ok=True)  # nothing to install, no stale marker needed

    # Run the graph (LangGraph returns the final dict state)
    logger.info("─" * 60)
    logger.info("Starting Omega graph for run_id=%s", goal.goal_id)
    logger.info("─" * 60)

    final_dict = omega_graph.invoke(initial_state.model_dump())
    final_state = SDLCState.model_validate(final_dict)

    # Persist final state
    path = save_state(final_state)
    logger.info("─" * 60)
    logger.info("Run complete.")
    logger.info("  Phase     : %s", final_state.current_phase)
    logger.info("  Loop count: %d", final_state.loop_count)
    logger.info("  State     : %s", path)
    logger.info("─" * 60)

    return final_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Lean Omega SDLC Orchestrator")
    parser.add_argument("--goal", required=True, help="Path to a goal YAML file")
    parser.add_argument(
        "--mode",
        choices=["local", "temporal"],
        default="local",
        help="Execution mode (temporal added in Stage 7)",
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        default=False,
        help="Disable Docker sandboxing (trusted dev mode only; default: sandbox enabled)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume from the last saved checkpoint instead of starting fresh.",
    )
    args = parser.parse_args()

    if args.mode == "temporal":
        logger.error("Temporal mode is not available until Stage 7.")
        return 1

    if args.no_sandbox:
        import src.tools.runners as _runners
        _runners.SANDBOX_ENABLED = False
        logger.warning("Sandbox DISABLED (--no-sandbox flag set). Running tools on host.")
    else:
        logger.info("Sandbox ENABLED (default). Tools will execute inside Docker.")

    state = run_local(args.goal, resume=args.resume)
    return 0 if state.current_phase == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
