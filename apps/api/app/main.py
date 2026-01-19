from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.artifacts import router as artifacts_router
from app.routes.datasets import router as datasets_router
from app.routes.health import router as health_router
from app.routes.projects import router as projects_router
from app.routes.runs import router as runs_router

app = FastAPI(title="data-analyst API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(projects_router, prefix="/projects", tags=["projects"])
app.include_router(datasets_router, prefix="/projects/{project_id}/datasets", tags=["datasets"])
app.include_router(runs_router, prefix="/projects/{project_id}/runs", tags=["runs"])
app.include_router(
    artifacts_router,
    prefix="/projects/{project_id}/artifacts",
    tags=["artifacts"],
)
