from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from app.models.schemas import (
    AgentApproval,
    AgentArtifactRead,
    AgentChatMessageRead,
    AgentChatSend,
    AgentChatSendResponse,
    AgentSkillCreate,
    AgentSkillRead,
    AgentSkillUpdate,
    AgentRollbackCreate,
    AgentRollbackRead,
    AgentRunCreate,
    AgentRunRead,
    AgentSnapshotCreate,
    AgentSnapshotRead,
    AgentToolRead,
    AgentPlanCreate,
    AgentPlanStepCreate,
)
from app.services import agent as agent_service
from packages.runtime.agent import LLMError
from app.services import store

router = APIRouter()


def _validate_toolchain(project_id: str, toolchain: list[str] | None) -> None:
    if not toolchain:
        return
    available = {tool.name for tool in agent_service.list_tools(project_id)}
    unknown = [tool for tool in toolchain if tool not in available]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tools in toolchain: {', '.join(unknown)}",
        )


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


@router.post("/runs/{run_id}/steps/{step_id}/apply", response_model=AgentRunRead)
def apply_agent_run_step(
    project_id: str, run_id: str, step_id: str, payload: AgentApproval
) -> AgentRunRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        run = agent_service.apply_run_step(project_id, run_id, step_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@router.get("/tools", response_model=list[AgentToolRead])
def list_agent_tools(project_id: str) -> list[AgentToolRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return agent_service.list_tools(project_id)


@router.get("/chat/messages", response_model=list[AgentChatMessageRead])
def list_agent_chat_messages(
    project_id: str, response: Response, limit: int = 100, offset: int = 0
) -> list[AgentChatMessageRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    response.headers["X-Total-Count"] = str(agent_service.count_chat_messages(project_id))
    messages = agent_service.list_chat_messages(project_id, limit=limit, offset=offset)
    return [
        AgentChatMessageRead(
            id=message.id,
            project_id=message.project_id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            run_id=message.run_id,
            attachments=message.attachments,
        )
        for message in messages
    ]


@router.post("/chat", response_model=AgentChatSendResponse, status_code=201)
def send_agent_chat_message(project_id: str, payload: AgentChatSend) -> AgentChatSendResponse:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        user_message, assistant_message, run = agent_service.send_chat_message(
            project_id,
            payload.content,
            payload.dataset_id,
            payload.safe_mode,
            payload.auto_run,
        )
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AgentChatSendResponse(
        messages=[
            AgentChatMessageRead(
                id=user_message.id,
                project_id=user_message.project_id,
                role=user_message.role,
                content=user_message.content,
                created_at=user_message.created_at,
                run_id=user_message.run_id,
                attachments=user_message.attachments,
            ),
            AgentChatMessageRead(
                id=assistant_message.id,
                project_id=assistant_message.project_id,
                role=assistant_message.role,
                content=assistant_message.content,
                created_at=assistant_message.created_at,
                run_id=assistant_message.run_id,
                attachments=assistant_message.attachments,
            ),
        ],
        run=run,
    )


@router.get("/artifacts", response_model=list[AgentArtifactRead])
def list_agent_artifacts(
    project_id: str,
    response: Response,
    run_id: str | None = None,
    snapshot_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AgentArtifactRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    response.headers["X-Total-Count"] = str(
        agent_service.count_agent_artifacts(project_id, run_id, snapshot_id)
    )
    artifacts = agent_service.list_agent_artifacts(project_id, run_id, snapshot_id, limit, offset)
    return [
        AgentArtifactRead(
            id=artifact.id,
            project_id=artifact.project_id,
            run_id=artifact.run_id,
            snapshot_id=artifact.snapshot_id,
            type=artifact.type,
            path=artifact.path,
            mime_type=artifact.mime_type,
            size=artifact.size,
            created_at=artifact.created_at,
        )
        for artifact in artifacts
    ]


@router.get("/artifacts/{artifact_id}", response_model=AgentArtifactRead)
def get_agent_artifact(project_id: str, artifact_id: str) -> AgentArtifactRead:
    artifact = agent_service.get_agent_artifact(project_id, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Agent artifact not found")
    return AgentArtifactRead(
        id=artifact.id,
        project_id=artifact.project_id,
        run_id=artifact.run_id,
        snapshot_id=artifact.snapshot_id,
        type=artifact.type,
        path=artifact.path,
        mime_type=artifact.mime_type,
        size=artifact.size,
        created_at=artifact.created_at,
    )


@router.get("/artifacts/{artifact_id}/download")
def download_agent_artifact(project_id: str, artifact_id: str) -> FileResponse:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    artifact = agent_service.get_agent_artifact(project_id, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Agent artifact not found")
    artifact_path = Path(artifact.path).resolve()
    workspace_root = Path(project.workspace_path).resolve()
    if not artifact_path.is_file() or not artifact_path.is_relative_to(workspace_root):
        raise HTTPException(status_code=404, detail="Agent artifact not found")
    return FileResponse(
        path=str(artifact_path),
        media_type=artifact.mime_type,
        filename=artifact_path.name,
    )


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


@router.post("/snapshots", response_model=AgentSnapshotRead, status_code=201)
def create_agent_snapshot(
    project_id: str, payload: AgentSnapshotCreate
) -> AgentSnapshotRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    snapshot = agent_service.create_snapshot_record(
        project_id,
        payload.kind,
        payload.target_path,
        payload.run_id,
        payload.details,
    )
    return AgentSnapshotRead(
        id=snapshot.id,
        project_id=snapshot.project_id,
        run_id=snapshot.run_id,
        kind=snapshot.kind,
        target_path=snapshot.target_path,
        created_at=snapshot.created_at,
        details=snapshot.details,
    )


@router.post("/snapshots/{snapshot_id}/restore", response_model=AgentRollbackRead)
def restore_agent_snapshot(project_id: str, snapshot_id: str) -> AgentRollbackRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    rollback = agent_service.restore_snapshot(project_id, snapshot_id)
    if not rollback:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return AgentRollbackRead(
        id=rollback.id,
        project_id=rollback.project_id,
        run_id=rollback.run_id,
        snapshot_id=rollback.snapshot_id,
        status=rollback.status,
        created_at=rollback.created_at,
        note=rollback.note,
    )


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


@router.post("/skills", response_model=AgentSkillRead, status_code=201)
def create_agent_skill(project_id: str, payload: AgentSkillCreate) -> AgentSkillRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    _validate_toolchain(project_id, payload.toolchain)
    skill = agent_service.create_skill(
        project_id,
        payload.name,
        payload.description,
        payload.prompt_template,
        payload.toolchain,
        payload.enabled,
    )
    return AgentSkillRead(
        id=skill.id,
        project_id=skill.project_id,
        name=skill.name,
        description=skill.description,
        prompt_template=skill.prompt_template,
        toolchain=skill.toolchain,
        enabled=skill.enabled,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


@router.get("/skills", response_model=list[AgentSkillRead])
def list_agent_skills(
    project_id: str, response: Response, limit: int = 100, offset: int = 0
) -> list[AgentSkillRead]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    response.headers["X-Total-Count"] = str(agent_service.count_skills(project_id))
    skills = agent_service.list_skills(project_id, limit=limit, offset=offset)
    return [
        AgentSkillRead(
            id=skill.id,
            project_id=skill.project_id,
            name=skill.name,
            description=skill.description,
            prompt_template=skill.prompt_template,
            toolchain=skill.toolchain,
            enabled=skill.enabled,
            created_at=skill.created_at,
            updated_at=skill.updated_at,
        )
        for skill in skills
    ]


@router.get("/skills/{skill_id}", response_model=AgentSkillRead)
def get_agent_skill(project_id: str, skill_id: str) -> AgentSkillRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    skill = agent_service.get_skill(project_id, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return AgentSkillRead(
        id=skill.id,
        project_id=skill.project_id,
        name=skill.name,
        description=skill.description,
        prompt_template=skill.prompt_template,
        toolchain=skill.toolchain,
        enabled=skill.enabled,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


@router.get("/skills/{skill_id}/plan", response_model=AgentPlanCreate)
def get_agent_skill_plan(project_id: str, skill_id: str) -> AgentPlanCreate:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    skill = agent_service.get_skill(project_id, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    steps = []
    for tool in skill.toolchain or []:
        steps.append(
            AgentPlanStepCreate(
                title=f"Run {tool}",
                description=skill.description,
                tool=tool,
                args={},
                requires_approval=True,
            )
        )
    return AgentPlanCreate(objective=skill.name, steps=steps)


@router.patch("/skills/{skill_id}", response_model=AgentSkillRead)
def update_agent_skill(
    project_id: str, skill_id: str, payload: AgentSkillUpdate
) -> AgentSkillRead:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    updates = payload.model_dump(exclude_unset=True)
    if "toolchain" in updates:
        _validate_toolchain(project_id, updates["toolchain"])
    skill = agent_service.update_skill(project_id, skill_id, updates)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return AgentSkillRead(
        id=skill.id,
        project_id=skill.project_id,
        name=skill.name,
        description=skill.description,
        prompt_template=skill.prompt_template,
        toolchain=skill.toolchain,
        enabled=skill.enabled,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


@router.delete("/skills/{skill_id}", status_code=204)
def delete_agent_skill(project_id: str, skill_id: str) -> None:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    deleted = agent_service.delete_skill(project_id, skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Skill not found")
