from __future__ import annotations

from datetime import datetime
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
