from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
from uuid import uuid4
import shutil

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


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "projects").exists():
            return parent
    return Path.cwd()


def _ensure_project_workspace(project_id: str) -> Path:
    root = _repo_root() / "projects" / project_id
    (root / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "data" / "staging").mkdir(parents=True, exist_ok=True)
    (root / "data" / "processed").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "metadata").mkdir(parents=True, exist_ok=True)
    (root / "secrets").mkdir(parents=True, exist_ok=True)
    (root / "env").mkdir(parents=True, exist_ok=True)
    return root


def _is_probable_file_source(source: str) -> bool:
    if source.startswith("file://"):
        return True
    if "://" in source:
        return False
    return True


def _maybe_copy_source(project_id: str, source: str) -> None:
    if not _is_probable_file_source(source):
        return
    source_path = Path(source.replace("file://", "")).expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    dest_dir = _repo_root() / "projects" / project_id / "data" / "raw"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, dest_dir / source_path.name)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_project(payload: ProjectCreate) -> ProjectRead:
    project_id = uuid4().hex
    workspace_root = _ensure_project_workspace(project_id)
    project = ProjectRead(
        id=project_id,
        name=payload.name,
        created_at=_now(),
        workspace_path=str(workspace_root),
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
    _maybe_copy_source(project_id, payload.source)
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


def list_project_artifacts(project_id: str, run_id: str | None = None) -> list[ArtifactRead]:
    project_run_ids = {r.id for r in _runs.values() if r.project_id == project_id}
    if run_id:
        if run_id not in project_run_ids:
            return []
        project_run_ids = {run_id}
    return [a for a in _artifacts.values() if a.run_id in project_run_ids]


def get_artifact(artifact_id: str) -> ArtifactRead | None:
    return _artifacts.get(artifact_id)
