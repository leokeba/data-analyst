from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.models.schemas import ArtifactRead
from app.services import store

router = APIRouter()


@router.get("", response_model=list[ArtifactRead])
def list_artifacts(project_id: str, run_id: str | None = None) -> list[ArtifactRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return store.list_project_artifacts(project_id, run_id)


@router.get("/{artifact_id}", response_model=ArtifactRead)
def get_artifact(project_id: str, artifact_id: str) -> ArtifactRead:
    artifact = store.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    run = store.get_run(artifact.run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.delete("/{artifact_id}", status_code=204)
def delete_artifact(project_id: str, artifact_id: str) -> None:
    artifact = store.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    run = store.get_run(artifact.run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    store.delete_artifact(project_id, artifact_id)


@router.get("/{artifact_id}/download")
def download_artifact(project_id: str, artifact_id: str) -> FileResponse:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    artifact = store.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    run = store.get_run(artifact.run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    artifact_path = Path(artifact.path).resolve()
    workspace_root = Path(project.workspace_path).resolve()
    if not artifact_path.is_file() or not artifact_path.is_relative_to(workspace_root):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(
        path=str(artifact_path),
        media_type=artifact.mime_type,
        filename=artifact_path.name,
    )
