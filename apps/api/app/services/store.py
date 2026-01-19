from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict
from uuid import uuid4

from app.models.schemas import (
    ArtifactRead,
    DatasetCreate,
    DatasetRead,
    ProjectCreate,
    ProjectRead,
    RunCreate,
    RunRead,
)


_projects: Dict[str, ProjectRead] = {}
_datasets: Dict[str, DatasetRead] = {}
_runs: Dict[str, RunRead] = {}
_artifacts: Dict[str, ArtifactRead] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_project(payload: ProjectCreate) -> ProjectRead:
    project_id = uuid4().hex
    project = ProjectRead(
        id=project_id,
        name=payload.name,
        created_at=_now(),
        workspace_path=f"projects/{project_id}",
    )
    _projects[project_id] = project
    return project


def list_projects() -> list[ProjectRead]:
    return list(_projects.values())


def get_project(project_id: str) -> ProjectRead | None:
    return _projects.get(project_id)


def delete_project(project_id: str) -> None:
    _projects.pop(project_id, None)
    dataset_ids = [d.id for d in _datasets.values() if d.project_id == project_id]
    for dataset_id in dataset_ids:
        delete_dataset(project_id, dataset_id)


def create_dataset(project_id: str, payload: DatasetCreate) -> DatasetRead:
    dataset_id = uuid4().hex
    dataset = DatasetRead(
        id=dataset_id,
        project_id=project_id,
        name=payload.name,
        source=payload.source,
        created_at=_now(),
        schema_snapshot=None,
        stats=None,
    )
    _datasets[dataset_id] = dataset
    return dataset


def list_datasets(project_id: str) -> list[DatasetRead]:
    return [d for d in _datasets.values() if d.project_id == project_id]


def get_dataset(dataset_id: str) -> DatasetRead | None:
    return _datasets.get(dataset_id)


def delete_dataset(project_id: str, dataset_id: str) -> None:
    dataset = _datasets.get(dataset_id)
    if dataset and dataset.project_id != project_id:
        return
    _datasets.pop(dataset_id, None)
    run_ids = [r.id for r in _runs.values() if r.dataset_id == dataset_id]
    for run_id in run_ids:
        delete_run(project_id, run_id)


def create_run(project_id: str, payload: RunCreate) -> RunRead:
    run_id = uuid4().hex
    run = RunRead(
        id=run_id,
        project_id=project_id,
        dataset_id=payload.dataset_id,
        type=payload.type,
        status="queued",
        started_at=_now(),
        finished_at=None,
    )
    _runs[run_id] = run
    return run


def list_runs(project_id: str) -> list[RunRead]:
    return [r for r in _runs.values() if r.project_id == project_id]


def get_run(run_id: str) -> RunRead | None:
    return _runs.get(run_id)


def delete_run(project_id: str, run_id: str) -> None:
    run = _runs.get(run_id)
    if run and run.project_id != project_id:
        return
    _runs.pop(run_id, None)
    artifact_ids = [a.id for a in _artifacts.values() if a.run_id == run_id]
    for artifact_id in artifact_ids:
        _artifacts.pop(artifact_id, None)


def list_artifacts(run_id: str) -> list[ArtifactRead]:
    return [a for a in _artifacts.values() if a.run_id == run_id]


def get_artifact(artifact_id: str) -> ArtifactRead | None:
    return _artifacts.get(artifact_id)
