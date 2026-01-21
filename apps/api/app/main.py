from contextlib import asynccontextmanager
from pathlib import Path
import logging
import os
import sys

from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

load_dotenv(dotenv_path=ROOT / ".env")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("uvicorn").setLevel(LOG_LEVEL)
logging.getLogger("uvicorn.error").setLevel(LOG_LEVEL)
logging.getLogger("uvicorn.access").setLevel(LOG_LEVEL)

from app.routes.agent import router as agent_router
from app.routes.artifacts import router as artifacts_router
from app.routes.datasets import router as datasets_router
from app.routes.health import router as health_router
from app.routes.projects import router as projects_router
from app.routes.runs import router as runs_router
from app.services.db import init_db

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="data-analyst API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        payload = {"error": detail}
    else:
        payload = {"error": str(detail)}
    payload["status_code"] = exc.status_code
    return JSONResponse(status_code=exc.status_code, content=payload)


app.include_router(health_router)
app.include_router(projects_router, prefix="/projects", tags=["projects"])
app.include_router(datasets_router, prefix="/projects/{project_id}/datasets", tags=["datasets"])
app.include_router(runs_router, prefix="/projects/{project_id}/runs", tags=["runs"])
app.include_router(agent_router, prefix="/projects/{project_id}/agent", tags=["agent"])
app.include_router(
    artifacts_router,
    prefix="/projects/{project_id}/artifacts",
    tags=["artifacts"],
)
