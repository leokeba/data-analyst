from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    PLANNER = "planner"
    OPERATOR = "operator"
    EXPLAINER = "explainer"


class StepStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    SKIPPED = "skipped"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Approval(BaseModel):
    approved_by: str
    approved_at: datetime = Field(default_factory=_now)
    note: str | None = None


class PlanStep(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    title: str
    description: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = True
    status: StepStatus = StepStatus.PENDING


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    objective: str
    steps: list[PlanStep]
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ToolResult(BaseModel):
    output: dict[str, Any] | None = None
    artifacts: list[str] = Field(default_factory=list)
    diff: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class ActionRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    step_id: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    approvals: list[Approval] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    diff: str | None = None


class SnapshotRef(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    kind: str
    target_path: str
    created_at: datetime = Field(default_factory=_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
