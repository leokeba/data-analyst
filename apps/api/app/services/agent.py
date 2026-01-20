from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from app.models.db import AgentRollback, AgentRun, AgentSkill, AgentSnapshot
from sqlalchemy import func
from sqlmodel import select
from app.models.schemas import (
    AgentApproval,
    AgentPlanCreate,
    AgentPlanStepCreate,
    AgentRunRead,
    AgentRunStatus,
    AgentToolRead,
    RunCreate,
)
from app.services import store
from app.services.db import get_session
from packages.runtime.agent import (
    ActionJournal,
    AgentPolicy,
    AgentRuntime,
    Approval,
    Plan,
    PlanStep,
    StepStatus,
    ToolDefinition,
    ToolResult,
    ToolRouter,
    SnapshotStore,
)


def _tool_run_factory(project_id: str):
    def _handler(args: dict[str, Any]) -> ToolResult:
        dataset_id = str(args.get("dataset_id", ""))
        run_type = str(args.get("type", ""))
        dataset = store.get_dataset(dataset_id)
        if not dataset or dataset.project_id != project_id:
            raise ValueError("Dataset not found")
        run = store.create_run(project_id, RunCreate(dataset_id=dataset_id, type=run_type))
        return ToolResult(output={"run": run.model_dump()})

    return ToolDefinition(
        name="create_run",
        description="Create a dataset run (ingest/profile/analysis/report).",
        handler=_handler,
        destructive=False,
    )


def _tool_preview_factory(project_id: str):
    def _handler(args: dict[str, Any]) -> ToolResult:
        dataset_id = str(args.get("dataset_id", ""))
        dataset = store.get_dataset(dataset_id)
        if not dataset or dataset.project_id != project_id:
            raise ValueError("Dataset not found")
        preview = store.get_dataset_preview(project_id, dataset_id)
        if not preview:
            raise ValueError("Dataset preview not available")
        return ToolResult(output={"preview": preview})

    return ToolDefinition(
        name="preview_dataset",
        description="Fetch dataset preview (CSV only).",
        handler=_handler,
        destructive=False,
    )


def _tool_list_datasets_factory(project_id: str):
    def _handler(_: dict[str, Any]) -> ToolResult:
        datasets = store.list_datasets(project_id)
        return ToolResult(output={"datasets": [dataset.model_dump() for dataset in datasets]})

    return ToolDefinition(
        name="list_datasets",
        description="List datasets for the project.",
        handler=_handler,
        destructive=False,
    )


def _tool_list_project_runs_factory(project_id: str):
    def _handler(_: dict[str, Any]) -> ToolResult:
        runs = store.list_runs(project_id)
        return ToolResult(output={"runs": [run.model_dump() for run in runs]})

    return ToolDefinition(
        name="list_project_runs",
        description="List data runs for the project.",
        handler=_handler,
        destructive=False,
    )


def _tool_list_artifacts_factory(project_id: str):
    def _handler(args: dict[str, Any]) -> ToolResult:
        run_id = args.get("run_id")
        limit = int(args.get("limit", 100))
        offset = int(args.get("offset", 0))
        artifacts = store.list_project_artifacts(project_id, run_id=run_id, limit=limit, offset=offset)
        return ToolResult(output={"artifacts": [artifact.model_dump() for artifact in artifacts]})

    return ToolDefinition(
        name="list_artifacts",
        description="List artifacts for the project (optional run_id filter).",
        handler=_handler,
        destructive=False,
    )


def _tool_create_snapshot_factory(project_id: str):
    def _handler(args: dict[str, Any]) -> ToolResult:
        kind = str(args.get("kind", ""))
        target_path = str(args.get("path", ""))
        run_id = args.get("run_id")
        details = args.get("metadata") if isinstance(args.get("metadata"), dict) else None
        if not kind or not target_path:
            raise ValueError("Snapshot requires kind and path")
        snapshot = create_snapshot_record(project_id, kind, target_path, run_id, details)
        return ToolResult(
            output={
                "snapshot": {
                    "id": snapshot.id,
                    "project_id": snapshot.project_id,
                    "run_id": snapshot.run_id,
                    "kind": snapshot.kind,
                    "target_path": snapshot.target_path,
                    "created_at": snapshot.created_at.isoformat(),
                    "details": snapshot.details,
                }
            }
        )

    return ToolDefinition(
        name="create_snapshot",
        description="Create a snapshot record for a workspace path.",
        handler=_handler,
        destructive=False,
    )


def _tool_request_rollback_factory(project_id: str):
    def _handler(args: dict[str, Any]) -> ToolResult:
        run_id = args.get("run_id")
        snapshot_id = args.get("snapshot_id")
        note = args.get("note") if isinstance(args.get("note"), str) else None
        rollback = AgentRollback(
            project_id=project_id,
            run_id=run_id,
            snapshot_id=snapshot_id,
            status="requested",
            note=note,
        )
        with get_session() as session:
            session.add(rollback)
            session.commit()
            session.refresh(rollback)
        return ToolResult(
            output={
                "rollback": {
                    "id": rollback.id,
                    "project_id": rollback.project_id,
                    "run_id": rollback.run_id,
                    "snapshot_id": rollback.snapshot_id,
                    "status": rollback.status,
                    "created_at": rollback.created_at.isoformat(),
                    "note": rollback.note,
                }
            }
        )

    return ToolDefinition(
        name="request_rollback",
        description="Request a rollback for a snapshot or run.",
        handler=_handler,
        destructive=False,
    )


def _build_plan(payload: AgentPlanCreate) -> Plan:
    steps: list[PlanStep] = []
    for step in payload.steps:
        steps.append(
            PlanStep(
                id=step.id or uuid4().hex,
                title=step.title,
                description=step.description,
                tool=step.tool,
                args=step.args or {},
                requires_approval=step.requires_approval,
            )
        )
    return Plan(objective=payload.objective, steps=steps)


def _plan_to_payload(plan: Plan) -> AgentPlanCreate:
    return AgentPlanCreate(
        objective=plan.objective,
        steps=[
            AgentPlanStepCreate(
                id=step.id,
                title=step.title,
                description=step.description,
                tool=step.tool,
                args=step.args,
                requires_approval=step.requires_approval,
            )
            for step in plan.steps
        ],
    )


def _approve_map(approvals: dict[str, AgentApproval] | None) -> dict[str, Approval]:
    if not approvals:
        return {}
    return {key: Approval(**value.model_dump()) for key, value in approvals.items()}


def _compute_status(plan: Plan) -> AgentRunStatus:
    statuses = {step.status for step in plan.steps}
    if StepStatus.FAILED in statuses:
        return AgentRunStatus.FAILED
    if StepStatus.PENDING in statuses or StepStatus.APPROVED in statuses:
        return AgentRunStatus.PENDING
    return AgentRunStatus.COMPLETED


def _build_router(project_id: str) -> tuple[ToolRouter, AgentPolicy]:
    policy = AgentPolicy()
    router = ToolRouter(policy)
    router.register(_tool_run_factory(project_id))
    router.register(_tool_preview_factory(project_id))
    router.register(_tool_list_datasets_factory(project_id))
    router.register(_tool_list_project_runs_factory(project_id))
    router.register(_tool_list_artifacts_factory(project_id))
    router.register(_tool_create_snapshot_factory(project_id))
    router.register(_tool_request_rollback_factory(project_id))
    return router, policy


def list_tools(project_id: str) -> list[AgentToolRead]:
    router, _ = _build_router(project_id)
    return [
        AgentToolRead(
            name=tool.name,
            description=tool.description,
            destructive=tool.destructive,
        )
        for tool in router.list_tools()
    ]


def run_plan(project_id: str, payload: AgentPlanCreate, approvals: dict[str, AgentApproval] | None) -> AgentRunRead:
    plan = _build_plan(payload)
    router, policy = _build_router(project_id)
    journal = ActionJournal()
    snapshots = SnapshotStore(policy=policy)
    runtime = AgentRuntime(router, journal, snapshots)
    runtime.run_plan(plan, _approve_map(approvals))
    status = _compute_status(plan)
    plan_payload = _plan_to_payload(plan)
    log = journal.to_log()
    agent_run = AgentRun(
        project_id=project_id,
        status=status.value,
        plan=plan.model_dump(mode="json"),
        log=log,
    )
    with get_session() as session:
        session.add(agent_run)
        session.commit()
        session.refresh(agent_run)
    return AgentRunRead(
        id=agent_run.id,
        project_id=agent_run.project_id,
        status=AgentRunStatus(agent_run.status),
        plan=plan_payload,
        log=log,
    )


def get_run(project_id: str, run_id: str) -> AgentRunRead | None:
    with get_session() as session:
        record = session.get(AgentRun, run_id)
    if not record or record.project_id != project_id:
        return None
    plan_data = record.plan or {"objective": "", "steps": []}
    plan_payload = _plan_to_payload(Plan(**plan_data))
    return AgentRunRead(
        id=record.id,
        project_id=record.project_id,
        status=AgentRunStatus(record.status),
        plan=plan_payload,
        log=record.log or [],
    )


def list_runs(project_id: str, limit: int = 100, offset: int = 0) -> list[AgentRunRead]:
    with get_session() as session:
        runs = session.exec(
            select(AgentRun)
            .where(AgentRun.project_id == project_id)
            .order_by(AgentRun.created_at)
            .offset(offset)
            .limit(limit)
        ).all()
    results: list[AgentRunRead] = []
    for run in runs:
        plan_data = run.plan or {"objective": "", "steps": []}
        plan_payload = _plan_to_payload(Plan(**plan_data))
        results.append(
            AgentRunRead(
                id=run.id,
                project_id=run.project_id,
                status=AgentRunStatus(run.status),
                plan=plan_payload,
                log=run.log or [],
            )
        )
    return results


def count_runs(project_id: str) -> int:
    with get_session() as session:
        total = session.exec(
            select(func.count()).select_from(AgentRun).where(AgentRun.project_id == project_id)
        ).one()
    return int(total)


def list_snapshots(project_id: str, limit: int = 100, offset: int = 0) -> list[AgentSnapshot]:
    with get_session() as session:
        snapshots = session.exec(
            select(AgentSnapshot)
            .where(AgentSnapshot.project_id == project_id)
            .order_by(AgentSnapshot.created_at)
            .offset(offset)
            .limit(limit)
        ).all()
    return snapshots


def count_snapshots(project_id: str) -> int:
    with get_session() as session:
        total = session.exec(
            select(func.count()).select_from(AgentSnapshot).where(AgentSnapshot.project_id == project_id)
        ).one()
    return int(total)


def get_snapshot(project_id: str, snapshot_id: str) -> AgentSnapshot | None:
    with get_session() as session:
        snapshot = session.get(AgentSnapshot, snapshot_id)
    if not snapshot or snapshot.project_id != project_id:
        return None
    return snapshot


def create_snapshot_record(
    project_id: str,
    kind: str,
    target_path: str,
    run_id: str | None,
    details: dict | None,
) -> AgentSnapshot:
    snapshot = AgentSnapshot(
        project_id=project_id,
        run_id=run_id,
        kind=kind,
        target_path=target_path,
        details=details,
    )
    with get_session() as session:
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
    project = store.get_project(project_id)
    if project:
        source = target_path.replace("file://", "")
        path = Path(source).expanduser().resolve()
        workspace_root = Path(project.workspace_path).resolve()
        if path.is_file() and path.is_relative_to(workspace_root):
            snapshots_dir = workspace_root / "artifacts" / "snapshots"
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            dest_path = snapshots_dir / f"{snapshot.id}-{path.name}"
            shutil.copy2(path, dest_path)
            snapshot.details = {**(snapshot.details or {}), "snapshot_path": str(dest_path)}
            with get_session() as session:
                session.add(snapshot)
                session.commit()
                session.refresh(snapshot)
    return snapshot


def create_rollback(project_id: str, run_id: str | None, snapshot_id: str | None, note: str | None) -> AgentRollback:
    rollback = AgentRollback(
        project_id=project_id,
        run_id=run_id,
        snapshot_id=snapshot_id,
        status="requested",
        note=note,
    )
    with get_session() as session:
        session.add(rollback)
        session.commit()
        session.refresh(rollback)
    return rollback


def restore_snapshot(project_id: str, snapshot_id: str) -> AgentRollback | None:
    snapshot = get_snapshot(project_id, snapshot_id)
    if not snapshot:
        return None
    rollback = AgentRollback(
        project_id=project_id,
        run_id=snapshot.run_id,
        snapshot_id=snapshot.id,
        status="applied",
        note="restore snapshot",
    )
    with get_session() as session:
        session.add(rollback)
        session.commit()
        session.refresh(rollback)
    return rollback


def list_rollbacks(project_id: str, limit: int = 100, offset: int = 0) -> list[AgentRollback]:
    with get_session() as session:
        rollbacks = session.exec(
            select(AgentRollback)
            .where(AgentRollback.project_id == project_id)
            .order_by(AgentRollback.created_at)
            .offset(offset)
            .limit(limit)
        ).all()
    return rollbacks


def count_rollbacks(project_id: str) -> int:
    with get_session() as session:
        total = session.exec(
            select(func.count()).select_from(AgentRollback).where(AgentRollback.project_id == project_id)
        ).one()
    return int(total)


def get_rollback(project_id: str, rollback_id: str) -> AgentRollback | None:
    with get_session() as session:
        rollback = session.get(AgentRollback, rollback_id)
    if not rollback or rollback.project_id != project_id:
        return None
    return rollback


def set_rollback_status(project_id: str, rollback_id: str, status: str) -> AgentRollback | None:
    with get_session() as session:
        rollback = session.get(AgentRollback, rollback_id)
        if not rollback or rollback.project_id != project_id:
            return None
        rollback.status = status
        session.add(rollback)
        session.commit()
        session.refresh(rollback)
        return rollback


def create_skill(
    project_id: str,
    name: str,
    description: str,
    prompt_template: str | None,
    toolchain: list[str] | None,
    enabled: bool,
) -> AgentSkill:
    skill = AgentSkill(
        project_id=project_id,
        name=name,
        description=description,
        prompt_template=prompt_template,
        toolchain=toolchain,
        enabled=enabled,
    )
    with get_session() as session:
        session.add(skill)
        session.commit()
        session.refresh(skill)
    return skill


def list_skills(project_id: str, limit: int = 100, offset: int = 0) -> list[AgentSkill]:
    with get_session() as session:
        skills = session.exec(
            select(AgentSkill)
            .where(AgentSkill.project_id == project_id)
            .order_by(AgentSkill.created_at)
            .offset(offset)
            .limit(limit)
        ).all()
    return skills


def count_skills(project_id: str) -> int:
    with get_session() as session:
        total = session.exec(
            select(func.count()).select_from(AgentSkill).where(AgentSkill.project_id == project_id)
        ).one()
    return int(total)


def get_skill(project_id: str, skill_id: str) -> AgentSkill | None:
    with get_session() as session:
        skill = session.get(AgentSkill, skill_id)
    if not skill or skill.project_id != project_id:
        return None
    return skill


def update_skill(project_id: str, skill_id: str, payload: dict[str, Any]) -> AgentSkill | None:
    with get_session() as session:
        skill = session.get(AgentSkill, skill_id)
        if not skill or skill.project_id != project_id:
            return None
        for key, value in payload.items():
            setattr(skill, key, value)
        skill.updated_at = datetime.now(timezone.utc)
        session.add(skill)
        session.commit()
        session.refresh(skill)
        return skill


def delete_skill(project_id: str, skill_id: str) -> bool:
    with get_session() as session:
        skill = session.get(AgentSkill, skill_id)
        if not skill or skill.project_id != project_id:
            return False
        session.delete(skill)
        session.commit()
    return True
