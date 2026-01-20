from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from typing import Any
from uuid import uuid4

from app.models.db import (
    AgentArtifact,
    AgentChatMessage,
    AgentRollback,
    AgentRun,
    AgentSkill,
    AgentSnapshot,
)
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
from app.services.db import get_engine, get_session
from packages.runtime.agent import (
    ActionJournal,
    AgentPolicy,
    AgentRuntime,
    Approval,
    LLMError,
    Plan,
    PlanStep,
    StepStatus,
    ToolDefinition,
    ToolResult,
    ToolRouter,
    SnapshotStore,
    generate_plan,
    validate_path,
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


def _tool_list_dir_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        project = store.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        path_value = args.get("path") or project.workspace_path
        resolved = validate_path(str(path_value), policy)
        include_hidden = bool(args.get("include_hidden", False))
        recursive = bool(args.get("recursive", False))
        max_entries = int(args.get("max_entries", 200))
        entries: list[dict[str, Any]] = []
        iterator = resolved.rglob("*") if recursive else resolved.iterdir()
        for entry in iterator:
            name = entry.name
            if not include_hidden and name.startswith("."):
                continue
            entries.append(
                {
                    "path": str(entry),
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None,
                }
            )
            if len(entries) >= max_entries:
                break
        return ToolResult(output={"path": str(resolved), "entries": entries})

    return ToolDefinition(
        name="list_dir",
        description="List files and directories in the project workspace.",
        handler=_handler,
        destructive=False,
    )


def _tool_read_file_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        path_value = str(args.get("path", ""))
        if not path_value:
            raise ValueError("Path is required")
        resolved = validate_path(path_value, policy)
        if not resolved.is_file():
            raise ValueError("File not found")
        start_line = int(args.get("start_line", 1))
        end_line = args.get("end_line")
        max_lines = int(args.get("max_lines", 200))
        if start_line < 1:
            start_line = 1
        if end_line is None:
            end_line = start_line + max_lines - 1
        if end_line < start_line:
            end_line = start_line
        lines: list[str] = []
        truncated = False
        byte_budget = int(args.get("max_bytes", policy.max_data_bytes))
        bytes_read = 0
        with resolved.open("r", encoding="utf-8", errors="replace") as handle:
            for idx, line in enumerate(handle, start=1):
                if idx < start_line:
                    continue
                if idx > end_line:
                    truncated = True
                    break
                bytes_read += len(line.encode("utf-8", errors="ignore"))
                if bytes_read > byte_budget:
                    truncated = True
                    break
                lines.append(line.rstrip("\n"))
        return ToolResult(
            output={
                "path": str(resolved),
                "start_line": start_line,
                "end_line": start_line + len(lines) - 1 if lines else start_line,
                "lines": lines,
                "truncated": truncated,
            }
        )

    return ToolDefinition(
        name="read_file",
        description="Read a file from the project workspace (line range).",
        handler=_handler,
        destructive=False,
    )


def _tool_search_text_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        project = store.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("Query is required")
        path_value = args.get("path") or project.workspace_path
        resolved = validate_path(str(path_value), policy)
        is_regex = bool(args.get("is_regex", False))
        include_hidden = bool(args.get("include_hidden", False))
        max_results = int(args.get("max_results", 50))
        pattern = re.compile(query) if is_regex else None
        results: list[dict[str, Any]] = []
        for file_path in resolved.rglob("*"):
            if file_path.is_dir():
                continue
            if not include_hidden and file_path.name.startswith("."):
                continue
            try:
                with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line_no, line in enumerate(handle, start=1):
                        haystack = line
                        matched = bool(pattern.search(haystack)) if pattern else query in haystack
                        if matched:
                            results.append(
                                {
                                    "path": str(file_path),
                                    "line": line_no,
                                    "text": line.rstrip("\n"),
                                }
                            )
                            if len(results) >= max_results:
                                return ToolResult(output={"results": results, "truncated": True})
            except (UnicodeDecodeError, OSError):
                continue
        return ToolResult(output={"results": results, "truncated": False})

    return ToolDefinition(
        name="search_text",
        description="Search text in workspace files (string or regex).",
        handler=_handler,
        destructive=False,
    )


def _tool_list_project_sqlite_factory(project_id: str, policy: AgentPolicy):
    def _handler(_: dict[str, Any]) -> ToolResult:
        project = store.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        workspace_root = Path(project.workspace_path).resolve()
        candidates: list[str] = []
        for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
            for match in workspace_root.rglob(pattern):
                if match.is_file():
                    candidates.append(str(match))
                if len(candidates) >= 50:
                    break
        return ToolResult(output={"sqlite_files": candidates})

    return ToolDefinition(
        name="list_project_sqlite",
        description="List SQLite files in the project workspace.",
        handler=_handler,
        destructive=False,
    )


def _tool_list_db_tables_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        db_path = args.get("db_path")
        if db_path:
            resolved = validate_path(str(db_path), policy)
        else:
            resolved = Path(get_engine().url.database or "").resolve()
        if not resolved.is_file():
            raise ValueError("Database file not found")
        tables: list[dict[str, Any]] = []
        with sqlite3.connect(str(resolved)) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            for (name,) in cursor.fetchall():
                columns = conn.execute(f"PRAGMA table_info({name})").fetchall()
                tables.append(
                    {
                        "name": name,
                        "columns": [
                            {"name": col[1], "type": col[2], "nullable": col[3] == 0}
                            for col in columns
                        ],
                    }
                )
        return ToolResult(output={"db_path": str(resolved), "tables": tables})

    return ToolDefinition(
        name="list_db_tables",
        description="List tables and columns from a SQLite database.",
        handler=_handler,
        destructive=False,
    )


def _is_readonly_sql(sql: str) -> bool:
    lowered = sql.strip().lower()
    if not lowered:
        return False
    if lowered.startswith("select") or lowered.startswith("with"):
        blocked = ("insert", "update", "delete", "drop", "alter", "create", "pragma")
        return not any(token in lowered for token in blocked)
    return False


def _tool_query_db_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        sql = str(args.get("sql", "")).strip()
        if not sql:
            raise ValueError("SQL is required")
        cleaned = sql.rstrip("; ")
        if ";" in cleaned:
            raise ValueError("Only single SQL statements are allowed")
        if not _is_readonly_sql(cleaned):
            raise ValueError("Only read-only SELECT queries are allowed")
        limit = int(args.get("limit", 200))
        if limit <= 0:
            limit = 200
        db_path = args.get("db_path")
        if db_path:
            resolved = validate_path(str(db_path), policy)
        else:
            resolved = Path(get_engine().url.database or "").resolve()
        if not resolved.is_file():
            raise ValueError("Database file not found")
        with sqlite3.connect(str(resolved)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(cleaned)
            rows = cursor.fetchmany(limit)
            columns = [col[0] for col in cursor.description or []]
            payload = [dict(row) for row in rows]
        return ToolResult(output={"db_path": str(resolved), "columns": columns, "rows": payload})

    return ToolDefinition(
        name="query_db",
        description="Run a read-only SQL query against a SQLite database.",
        handler=_handler,
        destructive=False,
    )


def _tool_write_file_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        path_value = str(args.get("path", ""))
        content = str(args.get("content", ""))
        if not path_value:
            raise ValueError("Path is required")
        resolved = validate_path(path_value, policy)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
        artifact = _upsert_agent_artifact(
            project_id,
            None,
            None,
            "text_file",
            resolved,
            "text/plain",
        )
        return ToolResult(
            output={"path": str(resolved), "bytes": resolved.stat().st_size},
            artifacts=[artifact.id],
        )

    return ToolDefinition(
        name="write_file",
        description="Write a text file to the project workspace.",
        handler=_handler,
        destructive=True,
    )


def _tool_append_file_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        path_value = str(args.get("path", ""))
        content = str(args.get("content", ""))
        if not path_value:
            raise ValueError("Path is required")
        resolved = validate_path(path_value, policy)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with resolved.open("a", encoding="utf-8") as handle:
            handle.write(content)
        artifact = _upsert_agent_artifact(
            project_id,
            None,
            None,
            "text_file",
            resolved,
            "text/plain",
        )
        return ToolResult(
            output={"path": str(resolved), "bytes": resolved.stat().st_size},
            artifacts=[artifact.id],
        )

    return ToolDefinition(
        name="append_file",
        description="Append text to a file in the project workspace.",
        handler=_handler,
        destructive=True,
    )


def _tool_write_markdown_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        path_value = str(args.get("path", ""))
        content = str(args.get("content", ""))
        if not path_value:
            raise ValueError("Path is required")
        resolved = validate_path(path_value, policy)
        if resolved.suffix.lower() != ".md":
            raise ValueError("Markdown files must end with .md")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
        artifact = _upsert_agent_artifact(
            project_id,
            None,
            None,
            "markdown",
            resolved,
            "text/markdown",
        )
        return ToolResult(
            output={"path": str(resolved), "bytes": resolved.stat().st_size},
            artifacts=[artifact.id],
        )

    return ToolDefinition(
        name="write_markdown",
        description="Write a markdown report in the project workspace.",
        handler=_handler,
        destructive=True,
    )


def _tool_run_python_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        project = store.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        workspace_root = Path(project.workspace_path).resolve()
        code = args.get("code")
        path_value = args.get("path")
        if not code and not path_value:
            raise ValueError("Provide code or a path to a script")
        if path_value:
            script_path = validate_path(str(path_value), policy)
        else:
            scripts_dir = workspace_root / "scripts" / "agent"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            script_path = scripts_dir / f"agent-script-{uuid4().hex}.py"
            script_path.write_text(str(code))
        result = subprocess.run(
            ["uv", "run", "python", str(script_path)],
            cwd=str(Path(__file__).resolve().parents[4]),
            capture_output=True,
            text=True,
            timeout=30,
        )
        output_text = "".join(
            [
                "STDOUT:\n",
                result.stdout or "",
                "\nSTDERR:\n",
                result.stderr or "",
            ]
        )
        artifacts_dir = _agent_artifacts_dir(project_id)
        if not artifacts_dir:
            raise ValueError("Artifacts directory unavailable")
        out_path = artifacts_dir / f"agent-python-{uuid4().hex}.txt"
        out_path.write_text(output_text)
        artifact = _upsert_agent_artifact(
            project_id,
            None,
            None,
            "python_run_output",
            out_path,
            "text/plain",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Python script failed: {result.stderr}")
        return ToolResult(
            output={
                "path": str(script_path),
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            artifacts=[artifact.id],
        )

    return ToolDefinition(
        name="run_python",
        description="Execute a Python script inside the project workspace.",
        handler=_handler,
        destructive=True,
    )


def _tool_run_shell_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        if not policy.allow_shell:
            raise PermissionError("Shell execution is disabled by policy")
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("Command is required")
        if policy.allowed_shell_commands:
            allowed = tuple(policy.allowed_shell_commands)
            if not command.startswith(allowed):
                raise PermissionError("Command not in allowlist")
        cwd_value = args.get("cwd")
        if cwd_value:
            cwd = validate_path(str(cwd_value), policy)
        else:
            project = store.get_project(project_id)
            if not project:
                raise ValueError("Project not found")
            cwd = validate_path(str(project.workspace_path), policy)
        timeout = int(args.get("timeout", policy.max_shell_seconds))
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=True,
        )
        return ToolResult(
            output={
                "command": command,
                "cwd": str(cwd),
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )

    return ToolDefinition(
        name="run_shell",
        description="Run a shell command inside the project workspace.",
        handler=_handler,
        destructive=True,
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
        artifacts = [artifact.id for artifact in list_agent_artifacts(project_id, snapshot_id=snapshot.id)]
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
            },
            artifacts=artifacts,
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


def _tool_catalog(router: ToolRouter) -> list[dict[str, Any]]:
    arg_hints = {
        "list_dir": {"path": "string (optional)", "recursive": "bool", "include_hidden": "bool", "max_entries": "int"},
        "read_file": {
            "path": "string",
            "start_line": "int",
            "end_line": "int (optional)",
            "max_lines": "int",
            "max_bytes": "int",
        },
        "search_text": {
            "query": "string",
            "path": "string (optional)",
            "is_regex": "bool",
            "include_hidden": "bool",
            "max_results": "int",
        },
        "list_datasets": {},
        "preview_dataset": {"dataset_id": "string"},
        "list_project_runs": {},
        "list_artifacts": {"run_id": "string", "limit": "int", "offset": "int"},
        "list_project_sqlite": {},
        "list_db_tables": {"db_path": "string (optional)"},
        "query_db": {"sql": "string", "db_path": "string (optional)", "limit": "int"},
        "write_file": {"path": "string", "content": "string"},
        "append_file": {"path": "string", "content": "string"},
        "write_markdown": {"path": "string", "content": "string"},
        "run_python": {"code": "string (optional)", "path": "string (optional)"},
        "run_shell": {"command": "string", "cwd": "string (optional)", "timeout": "int"},
        "create_run": {"dataset_id": "string", "type": "ingest|profile|analysis|report"},
        "create_snapshot": {"kind": "string", "path": "string", "run_id": "string (optional)"},
        "request_rollback": {"run_id": "string (optional)", "snapshot_id": "string (optional)"},
    }
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "args": arg_hints.get(tool.name, {}),
            "destructive": tool.destructive,
        }
        for tool in router.list_tools()
    ]


def _apply_safe_mode(plan: AgentPlanCreate, router: ToolRouter, safe_mode: bool) -> AgentPlanCreate:
    if not safe_mode:
        return plan
    tools = {tool.name: tool for tool in router.list_tools()}
    for step in plan.steps:
        if step.tool and step.tool in tools and tools[step.tool].destructive:
            step.requires_approval = True
    return plan


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
    project = store.get_project(project_id)
    allowed_paths = [project.workspace_path] if project else []
    policy = AgentPolicy(allowed_paths=allowed_paths, allow_shell=True)
    router = ToolRouter(policy)
    router.register(_tool_list_dir_factory(project_id, policy))
    router.register(_tool_read_file_factory(project_id, policy))
    router.register(_tool_search_text_factory(project_id, policy))
    router.register(_tool_run_factory(project_id))
    router.register(_tool_preview_factory(project_id))
    router.register(_tool_list_datasets_factory(project_id))
    router.register(_tool_list_project_runs_factory(project_id))
    router.register(_tool_list_artifacts_factory(project_id))
    router.register(_tool_list_project_sqlite_factory(project_id, policy))
    router.register(_tool_list_db_tables_factory(project_id, policy))
    router.register(_tool_query_db_factory(project_id, policy))
    router.register(_tool_write_file_factory(project_id, policy))
    router.register(_tool_append_file_factory(project_id, policy))
    router.register(_tool_write_markdown_factory(project_id, policy))
    router.register(_tool_run_python_factory(project_id, policy))
    router.register(_tool_run_shell_factory(project_id, policy))
    router.register(_tool_create_snapshot_factory(project_id))
    router.register(_tool_request_rollback_factory(project_id))
    return router, policy


def _agent_artifacts_dir(project_id: str) -> Path | None:
    project = store.get_project(project_id)
    if not project:
        return None
    workspace_root = Path(project.workspace_path).resolve()
    artifacts_dir = workspace_root / "artifacts" / "agent"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir


def _upsert_agent_artifact(
    project_id: str,
    run_id: str | None,
    snapshot_id: str | None,
    artifact_type: str,
    path: Path,
    mime_type: str,
) -> AgentArtifact:
    with get_session() as session:
        existing = session.exec(
            select(AgentArtifact)
            .where(AgentArtifact.project_id == project_id)
            .where(AgentArtifact.run_id == run_id)
            .where(AgentArtifact.snapshot_id == snapshot_id)
            .where(AgentArtifact.type == artifact_type)
        ).first()
        if existing:
            existing.path = str(path)
            existing.mime_type = mime_type
            existing.size = path.stat().st_size
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing
        artifact = AgentArtifact(
            project_id=project_id,
            run_id=run_id,
            snapshot_id=snapshot_id,
            type=artifact_type,
            path=str(path),
            mime_type=mime_type,
            size=path.stat().st_size,
        )
        session.add(artifact)
        session.commit()
        session.refresh(artifact)
        return artifact


def _write_agent_json_artifact(
    project_id: str,
    run_id: str | None,
    snapshot_id: str | None,
    artifact_type: str,
    filename: str,
    payload: dict[str, Any],
) -> AgentArtifact | None:
    artifacts_dir = _agent_artifacts_dir(project_id)
    if not artifacts_dir:
        return None
    path = artifacts_dir / filename
    path.write_text(json.dumps(payload, indent=2))
    return _upsert_agent_artifact(project_id, run_id, snapshot_id, artifact_type, path, "application/json")


def _write_run_artifacts(project_id: str, run_id: str, plan: dict[str, Any], log: list[dict[str, Any]]) -> None:
    _write_agent_json_artifact(
        project_id,
        run_id,
        None,
        "agent_run_plan",
        f"agent-plan-{run_id}.json",
        plan,
    )
    _write_agent_json_artifact(
        project_id,
        run_id,
        None,
        "agent_run_log",
        f"agent-log-{run_id}.json",
        {"run_id": run_id, "log": log},
    )


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
    _write_run_artifacts(project_id, agent_run.id, plan.model_dump(mode="json"), log)
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


def apply_run_step(
    project_id: str,
    run_id: str,
    step_id: str,
    approval: AgentApproval | None,
) -> AgentRunRead | None:
    with get_session() as session:
        record = session.get(AgentRun, run_id)
    if not record or record.project_id != project_id:
        return None
    plan_data = record.plan or {"objective": "", "steps": []}
    plan = Plan(**plan_data)
    step = next((item for item in plan.steps if item.id == step_id), None)
    if not step:
        return None
    if step.requires_approval and approval is None:
        raise ValueError("Approval required for this step")
    approval_payload = Approval(**approval.model_dump()) if approval else None
    router, policy = _build_router(project_id)
    journal = ActionJournal()
    snapshots = SnapshotStore(policy=policy)
    runtime = AgentRuntime(router, journal, snapshots)
    runtime.run_step(plan, step, approval_payload)
    log = list(record.log or [])
    log_entry = journal.to_log()[0] if journal.records else None
    if log_entry:
        replaced = False
        for idx in range(len(log) - 1, -1, -1):
            if log[idx].get("step_id") == step_id:
                log[idx] = log_entry
                replaced = True
                break
        if not replaced:
            log.append(log_entry)
    status = _compute_status(plan)
    record.plan = plan.model_dump(mode="json")
    record.log = log
    record.status = status.value
    with get_session() as session:
        session.add(record)
        session.commit()
        session.refresh(record)
    _write_run_artifacts(project_id, record.id, record.plan or {}, record.log or [])
    plan_payload = _plan_to_payload(plan)
    return AgentRunRead(
        id=record.id,
        project_id=record.project_id,
        status=AgentRunStatus(record.status),
        plan=plan_payload,
        log=record.log or [],
    )


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


def list_agent_artifacts(
    project_id: str,
    run_id: str | None = None,
    snapshot_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AgentArtifact]:
    with get_session() as session:
        query = select(AgentArtifact).where(AgentArtifact.project_id == project_id)
        if run_id:
            query = query.where(AgentArtifact.run_id == run_id)
        if snapshot_id:
            query = query.where(AgentArtifact.snapshot_id == snapshot_id)
        artifacts = session.exec(
            query.order_by(AgentArtifact.created_at).offset(offset).limit(limit)
        ).all()
    return artifacts


def count_agent_artifacts(
    project_id: str,
    run_id: str | None = None,
    snapshot_id: str | None = None,
) -> int:
    with get_session() as session:
        query = select(func.count()).select_from(AgentArtifact).where(
            AgentArtifact.project_id == project_id
        )
        if run_id:
            query = query.where(AgentArtifact.run_id == run_id)
        if snapshot_id:
            query = query.where(AgentArtifact.snapshot_id == snapshot_id)
        total = session.exec(query).one()
    return int(total)


def get_agent_artifact(project_id: str, artifact_id: str) -> AgentArtifact | None:
    with get_session() as session:
        artifact = session.get(AgentArtifact, artifact_id)
    if not artifact or artifact.project_id != project_id:
        return None
    return artifact


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
    snapshot_payload = {
        "id": snapshot.id,
        "project_id": snapshot.project_id,
        "run_id": snapshot.run_id,
        "kind": snapshot.kind,
        "target_path": snapshot.target_path,
        "created_at": snapshot.created_at.isoformat(),
        "details": snapshot.details,
    }
    _write_agent_json_artifact(
        project_id,
        snapshot.run_id,
        snapshot.id,
        "snapshot_metadata",
        f"snapshot-{snapshot.id}.json",
        snapshot_payload,
    )
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
            _upsert_agent_artifact(
                project_id,
                snapshot.run_id,
                snapshot.id,
                "snapshot_file",
                dest_path,
                "application/octet-stream",
            )
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
    project = store.get_project(project_id)
    if project:
        workspace_root = Path(project.workspace_path).resolve()
        target_path = Path(snapshot.target_path.replace("file://", "")).resolve()
        snapshot_path = None
        if isinstance(snapshot.details, dict):
            snapshot_path_value = snapshot.details.get("snapshot_path")
            if isinstance(snapshot_path_value, str):
                snapshot_path = Path(snapshot_path_value).resolve()
        if (
            snapshot_path
            and snapshot_path.is_file()
            and snapshot_path.is_relative_to(workspace_root)
            and target_path.is_relative_to(workspace_root)
        ):
            try:
                shutil.copy2(snapshot_path, target_path)
            except Exception as exc:  # pragma: no cover - safety net
                with get_session() as session:
                    rollback = session.get(AgentRollback, rollback.id)
                    if rollback:
                        rollback.status = "failed"
                        rollback.note = f"restore failed: {exc}"
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


def create_chat_message(
    project_id: str,
    role: str,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
) -> AgentChatMessage:
    message = AgentChatMessage(
        project_id=project_id,
        role=role,
        content=content,
        attachments=attachments,
        run_id=run_id,
    )
    with get_session() as session:
        session.add(message)
        session.commit()
        session.refresh(message)
    return message


def list_chat_messages(project_id: str, limit: int = 100, offset: int = 0) -> list[AgentChatMessage]:
    with get_session() as session:
        messages = session.exec(
            select(AgentChatMessage)
            .where(AgentChatMessage.project_id == project_id)
            .order_by(AgentChatMessage.created_at)
            .offset(offset)
            .limit(limit)
        ).all()
    return messages


def count_chat_messages(project_id: str) -> int:
    with get_session() as session:
        total = session.exec(
            select(func.count()).select_from(AgentChatMessage).where(
                AgentChatMessage.project_id == project_id
            )
        ).one()
    return int(total)


def send_chat_message(
    project_id: str,
    content: str,
    dataset_id: str | None,
    safe_mode: bool = True,
    auto_run: bool = True,
) -> tuple[AgentChatMessage, AgentChatMessage, AgentRunRead | None]:
    user_message = create_chat_message(project_id, "user", content)
    router, _ = _build_router(project_id)
    plan_payload = generate_plan(
        prompt=content,
        tool_catalog=_tool_catalog(router),
        dataset_id=dataset_id,
        safe_mode=safe_mode,
    )
    plan = AgentPlanCreate(
        objective=plan_payload.objective,
        steps=[
            AgentPlanStepCreate(
                title=step.title,
                description=step.description,
                tool=step.tool,
                args=step.args,
                requires_approval=step.requires_approval,
            )
            for step in plan_payload.steps
        ],
    )
    plan = _apply_safe_mode(plan, router, safe_mode)
    run: AgentRunRead | None = None
    assistant_content = "Saved your note."

    if plan.steps:
        if auto_run:
            run = run_plan(project_id, plan, approvals=None)
            step_list = ", ".join(step.tool or "step" for step in run.plan.steps)
            assistant_content = (
                f"Created agent run {run.id} with {len(run.plan.steps)} step(s): {step_list}."
            )
        else:
            step_list = ", ".join(step.tool or "step" for step in plan.steps)
            assistant_content = (
                f"Prepared a plan with {len(plan.steps)} step(s): {step_list}."
            )

    assistant_message = create_chat_message(
        project_id,
        "assistant",
        assistant_content,
        run_id=run.id if run else None,
    )
    return user_message, assistant_message, run
