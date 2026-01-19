from fastapi import APIRouter, HTTPException

from app.models.schemas import RunCreate, RunRead
from app.services import store

router = APIRouter()


@router.post("", response_model=RunRead, status_code=201)
def create_run(project_id: str, payload: RunCreate) -> RunRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    dataset = store.get_dataset(payload.dataset_id)
    if not dataset or dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return store.create_run(project_id, payload)


@router.get("", response_model=list[RunRead])
def list_runs(project_id: str, limit: int = 100, offset: int = 0) -> list[RunRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return store.list_runs(project_id, limit=limit, offset=offset)


@router.get("/{run_id}", response_model=RunRead)
def get_run(project_id: str, run_id: str) -> RunRead:
    run = store.get_run(run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.delete("/{run_id}", status_code=204)
def delete_run(project_id: str, run_id: str) -> None:
    run = store.get_run(run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    store.delete_run(project_id, run_id)
