from fastapi import APIRouter, HTTPException

from app.models.schemas import DatasetCreate, DatasetRead
from app.services import store

router = APIRouter()


@router.post("", response_model=DatasetRead, status_code=201)
def create_dataset(project_id: str, payload: DatasetCreate) -> DatasetRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return store.create_dataset(project_id, payload)


@router.get("", response_model=list[DatasetRead])
def list_datasets(project_id: str) -> list[DatasetRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return store.list_datasets(project_id)


@router.get("/{dataset_id}", response_model=DatasetRead)
def get_dataset(project_id: str, dataset_id: str) -> DatasetRead:
    dataset = store.get_dataset(dataset_id)
    if not dataset or dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset
