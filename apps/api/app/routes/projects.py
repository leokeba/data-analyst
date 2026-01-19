from fastapi import APIRouter, HTTPException

from app.models.schemas import ProjectCreate, ProjectRead
from app.services import store

router = APIRouter()


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate) -> ProjectRead:
    return store.create_project(payload)


@router.get("", response_model=list[ProjectRead])
def list_projects(limit: int = 100, offset: int = 0) -> list[ProjectRead]:
    return store.list_projects(limit=limit, offset=offset)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str) -> ProjectRead:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    store.delete_project(project_id)
