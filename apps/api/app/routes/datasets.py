from fastapi import APIRouter, HTTPException, File, UploadFile

from app.models.schemas import DatasetCreate, DatasetRead
from app.services import store

router = APIRouter()


@router.post("", response_model=DatasetRead, status_code=201)
def create_dataset(project_id: str, payload: DatasetCreate) -> DatasetRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return store.create_dataset(project_id, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@router.post("/upload", response_model=DatasetRead, status_code=201)
async def upload_dataset(project_id: str, file: UploadFile = File(...)) -> DatasetRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    payload = await file.read()
    return store.create_dataset_from_upload(project_id, file.filename, payload)
