from fastapi import APIRouter, HTTPException

from app.models.schemas import ArtifactRead
from app.services import store

router = APIRouter()


@router.get("/{artifact_id}", response_model=ArtifactRead)
def get_artifact(project_id: str, artifact_id: str) -> ArtifactRead:
    artifact = store.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    run = store.get_run(artifact.run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact
