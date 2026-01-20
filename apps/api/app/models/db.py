from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=_now)
    workspace_path: str


class Dataset(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    project_id: str = Field(index=True)
    name: str
    source: str
    created_at: datetime = Field(default_factory=_now)
    schema_snapshot: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    stats: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class Run(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    project_id: str = Field(index=True)
    dataset_id: str = Field(index=True)
    type: str
    status: str
    started_at: datetime = Field(default_factory=_now)
    finished_at: Optional[datetime] = None


class Artifact(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    run_id: str = Field(index=True)
    type: str
    path: str
    mime_type: str
    size: int


class AgentRun(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    project_id: str = Field(index=True)
    status: str
    created_at: datetime = Field(default_factory=_now)
    plan: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    log: Optional[list[dict]] = Field(default=None, sa_column=Column(JSON))


class AgentSnapshot(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    project_id: str = Field(index=True)
    run_id: Optional[str] = Field(default=None, index=True)
    kind: str
    target_path: str
    created_at: datetime = Field(default_factory=_now)
    details: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class AgentRollback(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    project_id: str = Field(index=True)
    run_id: Optional[str] = Field(default=None, index=True)
    snapshot_id: Optional[str] = Field(default=None, index=True)
    status: str
    created_at: datetime = Field(default_factory=_now)
    note: Optional[str] = None


class AgentSkill(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    project_id: str = Field(index=True)
    name: str
    description: str
    prompt_template: Optional[str] = None
    toolchain: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    enabled: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
