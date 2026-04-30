"""OmegaGoal schema and YAML loader — Stage 1."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class QualityThresholds(BaseModel):
    max_cyclomatic_complexity: int = 10
    min_test_coverage: int = 80
    enforce_type_hints: bool = True


class OmegaGoal(BaseModel):
    goal_id: str
    objective: str
    context: Optional[str] = None
    technical_requirements: list[str] = Field(default_factory=list)
    quality_thresholds: QualityThresholds = Field(default_factory=QualityThresholds)
    success_criteria: list[str] = Field(default_factory=list)

    @field_validator("goal_id")
    @classmethod
    def goal_id_no_spaces(cls, v: str) -> str:
        if " " in v:
            raise ValueError("goal_id must not contain spaces")
        return v

    @field_validator("objective")
    @classmethod
    def objective_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("objective must not be empty")
        return v


def load_goal(path: str | Path) -> OmegaGoal:
    """Load and validate an OmegaGoal from a YAML file."""
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return OmegaGoal.model_validate(data)
