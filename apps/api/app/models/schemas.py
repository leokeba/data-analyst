from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class ProjectRead(BaseModel):
    id: str
    name: str
    created_at: datetime
    workspace_path: str


class DatasetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    source: str = Field(..., min_length=1, max_length=500)


class DatasetRead(BaseModel):
    id: str
    project_id: str
    name: str
    source: str
    created_at: datetime
    schema_snapshot: dict | None = None
    stats: dict | None = None


RunType = Literal["ingest", "profile", "analysis", "report"]


class RunCreate(BaseModel):
    dataset_id: str
    type: RunType


class RunRead(BaseModel):
    id: str
    project_id: str
    dataset_id: str
    type: RunType
    status: str
    started_at: datetime
    finished_at: datetime | None = None


class ArtifactRead(BaseModel):
    id: str
    run_id: str
    type: str
    path: str
    mime_type: str
    size: int


class AgentPlanStepCreate(BaseModel):
    id: str | None = None
    title: str
    description: str
    tool: str | None = None
    args: dict | None = None
    requires_approval: bool = True


class AgentPlanCreate(BaseModel):
    objective: str
    steps: list[AgentPlanStepCreate]


class AgentApproval(BaseModel):
    approved_by: str
    note: str | None = None


class AgentRunStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunCreate(BaseModel):
    plan: AgentPlanCreate
    approvals: dict[str, AgentApproval] | None = None


class AgentRunRead(BaseModel):
    id: str
    project_id: str
    status: AgentRunStatus
    plan: AgentPlanCreate
    log: list[dict[str, object]]


class AgentToolRead(BaseModel):
    name: str
    description: str
    destructive: bool


class AgentSnapshotRead(BaseModel):
    id: str
    project_id: str
    run_id: str | None = None
    kind: str
    target_path: str
    created_at: datetime
    details: dict | None = None


class AgentSnapshotCreate(BaseModel):
    kind: str
    target_path: str
    run_id: str | None = None
    details: dict | None = None


class AgentRollbackCreate(BaseModel):
    run_id: str | None = None
    snapshot_id: str | None = None
    note: str | None = None


class AgentRollbackRead(BaseModel):
    id: str
    project_id: str
    run_id: str | None = None
    snapshot_id: str | None = None
    status: str
    created_at: datetime
    note: str | None = None


class AgentSkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=500)
    prompt_template: str | None = None
    toolchain: list[str] | None = None
    enabled: bool = True


class AgentSkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    prompt_template: str | None = None
    toolchain: list[str] | None = None
    enabled: bool | None = None


class AgentSkillRead(BaseModel):
    id: str
    project_id: str
    name: str
    description: str
    prompt_template: str | None = None
    toolchain: list[str] | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AgentToolRead(BaseModel):
    name: str
    description: str
    destructive: bool
