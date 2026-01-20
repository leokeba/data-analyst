from fastapi import APIRouter, HTTPException, Response

from app.models.schemas import (
    AgentRollbackCreate,
    AgentRollbackRead,
    AgentRunCreate,
    AgentRunRead,
    AgentSnapshotRead,
    AgentToolRead,
)
from app.services import agent as agent_service
from app.services import store

router = APIRouter()


@router.post("/runs", response_model=AgentRunRead, status_code=201)
def create_agent_run(project_id: str, payload: AgentRunCreate) -> AgentRunRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return agent_service.run_plan(project_id, payload.plan, payload.approvals)


@router.get("/runs", response_model=list[AgentRunRead])
def list_agent_runs(
    project_id: str, response: Response, limit: int = 100, offset: int = 0
) -> list[AgentRunRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    response.headers["X-Total-Count"] = str(agent_service.count_runs(project_id))
    return agent_service.list_runs(project_id, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=AgentRunRead)
def get_agent_run(project_id: str, run_id: str) -> AgentRunRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    run = agent_service.get_run(project_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@router.get("/tools", response_model=list[AgentToolRead])
def list_agent_tools(project_id: str) -> list[AgentToolRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return agent_service.list_tools(project_id)


@router.get("/snapshots", response_model=list[AgentSnapshotRead])
def list_agent_snapshots(
    project_id: str, response: Response, limit: int = 100, offset: int = 0
) -> list[AgentSnapshotRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    response.headers["X-Total-Count"] = str(agent_service.count_snapshots(project_id))
    snapshots = agent_service.list_snapshots(project_id, limit=limit, offset=offset)
    return [
        AgentSnapshotRead(
            id=snapshot.id,
            project_id=snapshot.project_id,
            run_id=snapshot.run_id,
            kind=snapshot.kind,
            target_path=snapshot.target_path,
            created_at=snapshot.created_at,
            details=snapshot.details,
        )
        for snapshot in snapshots
    ]


@router.post("/rollbacks", response_model=AgentRollbackRead, status_code=201)
def create_agent_rollback(project_id: str, payload: AgentRollbackCreate) -> AgentRollbackRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    rollback = agent_service.create_rollback(project_id, payload.run_id, payload.snapshot_id, payload.note)
    return AgentRollbackRead(
        id=rollback.id,
        project_id=rollback.project_id,
        run_id=rollback.run_id,
        snapshot_id=rollback.snapshot_id,
        status=rollback.status,
        created_at=rollback.created_at,
        note=rollback.note,
    )


@router.get("/rollbacks", response_model=list[AgentRollbackRead])
def list_agent_rollbacks(
    project_id: str, response: Response, limit: int = 100, offset: int = 0
) -> list[AgentRollbackRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    response.headers["X-Total-Count"] = str(agent_service.count_rollbacks(project_id))
    rollbacks = agent_service.list_rollbacks(project_id, limit=limit, offset=offset)
    return [
        AgentRollbackRead(
            id=rollback.id,
            project_id=rollback.project_id,
            run_id=rollback.run_id,
            snapshot_id=rollback.snapshot_id,
            status=rollback.status,
            created_at=rollback.created_at,
            note=rollback.note,
        )
        for rollback in rollbacks
    ]


@router.post("/rollbacks/{rollback_id}/apply", response_model=AgentRollbackRead)
def apply_agent_rollback(project_id: str, rollback_id: str) -> AgentRollbackRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    rollback = agent_service.set_rollback_status(project_id, rollback_id, "applied")
    if not rollback:
        raise HTTPException(status_code=404, detail="Rollback not found")
    return AgentRollbackRead(
        id=rollback.id,
        project_id=rollback.project_id,
        run_id=rollback.run_id,
        snapshot_id=rollback.snapshot_id,
        status=rollback.status,
        created_at=rollback.created_at,
        note=rollback.note,
    )


@router.post("/rollbacks/{rollback_id}/cancel", response_model=AgentRollbackRead)
def cancel_agent_rollback(project_id: str, rollback_id: str) -> AgentRollbackRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    rollback = agent_service.set_rollback_status(project_id, rollback_id, "cancelled")
    if not rollback:
        raise HTTPException(status_code=404, detail="Rollback not found")
    return AgentRollbackRead(
        id=rollback.id,
        project_id=rollback.project_id,
        run_id=rollback.run_id,
        snapshot_id=rollback.snapshot_id,
        status=rollback.status,
        created_at=rollback.created_at,
        note=rollback.note,
    )
