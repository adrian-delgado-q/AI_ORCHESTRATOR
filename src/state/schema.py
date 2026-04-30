"""SDLCState and supporting schemas — grows with each stage."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.core.goal import OmegaGoal, QualityThresholds


class SDLCRequirement(BaseModel):
    id: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)


class FileChange(BaseModel):
    """Tracks a generated file by path + hash. No code stored in state."""

    path: str
    requirement_id: str
    rationale: str
    hash: str  # sha256 — populated in Stage 2; empty string in Stage 1


class ToolEvidence(BaseModel):
    tool_name: str
    passed: bool
    findings: str
    diagnosis: Optional[str] = None  # populated by DiagnosticUtility in Stage 4
    # Semantic category — language/tool agnostic. Nodes filter by role, never
    # by tool name, so swapping ruff→eslint or pytest→jest requires no node changes.
    # Values: linter | test | security | audit | complexity
    role: str = ""


class SDLCState(BaseModel):
    # Identity
    run_id: str
    objective: str
    context: Optional[str] = None

    # Goal fields (carried from OmegaGoal)
    technical_requirements: list[str] = Field(default_factory=list)
    quality_thresholds: QualityThresholds = Field(default_factory=QualityThresholds)
    success_criteria: list[str] = Field(default_factory=list)

    # Planning
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    requirements: list[SDLCRequirement] = Field(default_factory=list)
    architecture_doc: Optional[str] = None

    # Execution — paths + hashes only, no code blobs
    files_changed: list[FileChange] = Field(default_factory=list)
    tests_written: list[FileChange] = Field(default_factory=list)

    # Review
    gate_evidence: list[ToolEvidence] = Field(default_factory=list)

    # Memory (Stage 6)
    lessons_learned: list[str] = Field(default_factory=list)

    # Release
    release_notes: Optional[str] = None

    # Supervisor (Stage 4)
    supervisor_notes: Optional[str] = None

    # Graph control
    current_phase: Literal[
        "planning",
        "implementation",
        "testing",
        "review",
        "release",
        "done",
        "human_review",
    ] = "planning"
    loop_count: int = 0

    @classmethod
    def from_goal(cls, goal: OmegaGoal) -> "SDLCState":
        return cls(
            run_id=goal.goal_id,
            objective=goal.objective,
            context=goal.context,
            technical_requirements=goal.technical_requirements,
            quality_thresholds=goal.quality_thresholds,
            success_criteria=goal.success_criteria,
        )
