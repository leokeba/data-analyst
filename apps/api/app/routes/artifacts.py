from fastapi import APIRouter, HTTPException

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
