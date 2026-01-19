from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

from sqlmodel import select

from app.models.db import Artifact, Dataset, Project, Run
from app.models.schemas import (
    ArtifactRead,
    DatasetCreate,
    DatasetRead,
    ProjectCreate,
    ProjectRead,
    RunCreate,
    RunRead,
)
from app.services.db import get_session


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


def _project_read(project: Project) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        name=project.name,
        created_at=project.created_at,
        workspace_path=project.workspace_path,
    )


def _dataset_read(dataset: Dataset) -> DatasetRead:
    return DatasetRead(
        id=dataset.id,
        project_id=dataset.project_id,
        name=dataset.name,
        source=dataset.source,
        created_at=dataset.created_at,
        schema_snapshot=dataset.schema_snapshot,
        stats=dataset.stats,
    )


def _run_read(run: Run) -> RunRead:
    return RunRead(
        id=run.id,
        project_id=run.project_id,
        dataset_id=run.dataset_id,
        type=run.type,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _artifact_read(artifact: Artifact) -> ArtifactRead:
    return ArtifactRead(
        id=artifact.id,
        run_id=artifact.run_id,
        type=artifact.type,
        path=artifact.path,
        mime_type=artifact.mime_type,
        size=artifact.size,
    )


def create_project(payload: ProjectCreate) -> ProjectRead:
    project_id = Project().id
    workspace_root = _ensure_project_workspace(project_id)
    project = Project(
        id=project_id,
        name=payload.name,
        created_at=_now(),
        workspace_path=str(workspace_root),
    )
    with get_session() as session:
        session.add(project)
        session.commit()
        session.refresh(project)
    return _project_read(project)


def list_projects() -> list[ProjectRead]:
    with get_session() as session:
        projects = session.exec(select(Project).order_by(Project.created_at)).all()
    return [_project_read(project) for project in projects]


def get_project(project_id: str) -> ProjectRead | None:
    with get_session() as session:
        project = session.get(Project, project_id)
    return _project_read(project) if project else None


def delete_project(project_id: str) -> None:
    with get_session() as session:
        datasets = session.exec(
            select(Dataset).where(Dataset.project_id == project_id)
        ).all()
        for dataset in datasets:
            runs = session.exec(select(Run).where(Run.dataset_id == dataset.id)).all()
            for run in runs:
                artifacts = session.exec(
                    select(Artifact).where(Artifact.run_id == run.id)
                ).all()
                for artifact in artifacts:
                    session.delete(artifact)
                session.delete(run)
            session.delete(dataset)
        project = session.get(Project, project_id)
        if project:
            session.delete(project)
        session.commit()


def _create_dataset_record(project_id: str, payload: DatasetCreate) -> DatasetRead:
    dataset = Dataset(
        project_id=project_id,
        name=payload.name,
        source=payload.source,
        created_at=_now(),
        schema_snapshot=None,
        stats=None,
    )
    with get_session() as session:
        session.add(dataset)
        session.commit()
        session.refresh(dataset)
    return _dataset_read(dataset)


def create_dataset(project_id: str, payload: DatasetCreate) -> DatasetRead:
    _maybe_copy_source(project_id, payload.source)
    return _create_dataset_record(project_id, payload)


def create_dataset_from_upload(project_id: str, filename: str, data: bytes) -> DatasetRead:
    workspace = _ensure_project_workspace(project_id)
    dest_path = workspace / "data" / "raw" / filename
    dest_path.write_bytes(data)
    payload = DatasetCreate(name=filename, source=f"file://{dest_path}")
    return _create_dataset_record(project_id, payload)


def list_datasets(project_id: str) -> list[DatasetRead]:
    with get_session() as session:
        datasets = session.exec(
            select(Dataset)
            .where(Dataset.project_id == project_id)
            .order_by(Dataset.created_at)
        ).all()
    return [_dataset_read(dataset) for dataset in datasets]


def get_dataset(dataset_id: str) -> DatasetRead | None:
    with get_session() as session:
        dataset = session.get(Dataset, dataset_id)
    return _dataset_read(dataset) if dataset else None


def delete_dataset(project_id: str, dataset_id: str) -> None:
    with get_session() as session:
        dataset = session.get(Dataset, dataset_id)
        if not dataset or dataset.project_id != project_id:
            return
        runs = session.exec(select(Run).where(Run.dataset_id == dataset_id)).all()
        for run in runs:
            artifacts = session.exec(select(Artifact).where(Artifact.run_id == run.id)).all()
            for artifact in artifacts:
                session.delete(artifact)
            session.delete(run)
        session.delete(dataset)
        session.commit()


def create_run(project_id: str, payload: RunCreate) -> RunRead:
    run = Run(
        project_id=project_id,
        dataset_id=payload.dataset_id,
        type=payload.type,
        status="queued",
        started_at=_now(),
        finished_at=None,
    )
    with get_session() as session:
        session.add(run)
        session.commit()
        session.refresh(run)
    return _run_read(run)


def list_runs(project_id: str) -> list[RunRead]:
    with get_session() as session:
        runs = session.exec(
            select(Run).where(Run.project_id == project_id).order_by(Run.started_at)
        ).all()
    return [_run_read(run) for run in runs]


def get_run(run_id: str) -> RunRead | None:
    with get_session() as session:
        run = session.get(Run, run_id)
    return _run_read(run) if run else None


def delete_run(project_id: str, run_id: str) -> None:
    with get_session() as session:
        run = session.get(Run, run_id)
        if not run or run.project_id != project_id:
            return
        artifacts = session.exec(select(Artifact).where(Artifact.run_id == run_id)).all()
        for artifact in artifacts:
            session.delete(artifact)
        session.delete(run)
        session.commit()


def list_artifacts(run_id: str) -> list[ArtifactRead]:
    with get_session() as session:
        artifacts = session.exec(select(Artifact).where(Artifact.run_id == run_id)).all()
    return [_artifact_read(artifact) for artifact in artifacts]


def list_project_artifacts(project_id: str, run_id: str | None = None) -> list[ArtifactRead]:
    with get_session() as session:
        runs = session.exec(select(Run).where(Run.project_id == project_id)).all()
        run_ids = {run.id for run in runs}
        if run_id:
            if run_id not in run_ids:
                return []
            run_ids = {run_id}
        if not run_ids:
            return []
        artifacts = session.exec(select(Artifact).where(Artifact.run_id.in_(run_ids))).all()
    return [_artifact_read(artifact) for artifact in artifacts]


def get_artifact(artifact_id: str) -> ArtifactRead | None:
    with get_session() as session:
        artifact = session.get(Artifact, artifact_id)
    return _artifact_read(artifact) if artifact else None
