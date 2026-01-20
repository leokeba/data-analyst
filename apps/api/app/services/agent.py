from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
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
    ActionRecord,
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
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ToolCallPart, ToolReturnPart, TextPart


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
        path_value = str(args.get("path") or "")
        depth = int(args.get("depth", 1))
        project = store.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        base_path = Path(project.workspace_path).resolve()
        if path_value:
            target = validate_path(path_value, policy)
        else:
            target = base_path
        if not target.exists():
            raise ValueError("Path does not exist")
        if target.is_file():
            return ToolResult(output={"path": str(target), "entries": []})
        max_depth = max(depth, 0)
        entries: list[dict[str, Any]] = []
        for root, dirs, files in os.walk(target):
            current_depth = len(Path(root).relative_to(target).parts)
            if current_depth > max_depth:
                dirs[:] = []
                continue
            for name in sorted(dirs):
                entries.append({"path": str(Path(root) / name), "type": "dir"})
            for name in sorted(files):
                entries.append({"path": str(Path(root) / name), "type": "file"})
        return ToolResult(output={"path": str(target), "entries": entries})

    return ToolDefinition(
        name="list_dir",
        description="List files and folders in a workspace directory.",
        handler=_handler,
        destructive=False,
    )


def _tool_read_file_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        path_value = str(args.get("path") or "")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        max_bytes = int(args.get("max_bytes", policy.max_data_bytes))
        if not path_value:
            raise ValueError("Path is required")
        resolved = validate_path(path_value, policy)
        if not resolved.is_file():
            raise ValueError("File not found")
        text = resolved.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if start_line is not None or end_line is not None:
            start_idx = max(int(start_line or 1) - 1, 0)
            end_idx = int(end_line) if end_line is not None else len(lines)
            sliced = lines[start_idx:end_idx]
            content = "\n".join(sliced)
        else:
            content = text
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > max_bytes:
            content = content_bytes[:max_bytes].decode("utf-8", errors="replace")
        return ToolResult(
            output={
                "path": str(resolved),
                "content": content,
                "lines": len(lines),
                "truncated": len(content_bytes) > max_bytes,
            }
        )

    return ToolDefinition(
        name="read_file",
        description="Read a text file from the workspace with optional line ranges.",
        handler=_handler,
        destructive=False,
    )


def _tool_grep_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        pattern = str(args.get("pattern") or "")
        path_value = str(args.get("path") or "")
        regex = bool(args.get("regex", False))
        max_matches = int(args.get("max_matches", 50))
        if not pattern:
            raise ValueError("Pattern is required")
        project = store.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        base_path = Path(project.workspace_path).resolve()
        target = validate_path(path_value, policy) if path_value else base_path
        if not target.exists():
            raise ValueError("Path does not exist")
        compiled = re.compile(pattern) if regex else None
        matches: list[dict[str, Any]] = []
        paths = [target]
        if target.is_dir():
            paths = [path for path in target.rglob("*") if path.is_file()]
        for candidate in paths:
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for idx, line in enumerate(content.splitlines(), start=1):
                found = bool(compiled.search(line)) if compiled else pattern in line
                if found:
                    matches.append({"path": str(candidate), "line": idx, "text": line})
                    if len(matches) >= max_matches:
                        return ToolResult(output={"matches": matches, "truncated": True})
        return ToolResult(output={"matches": matches, "truncated": False})

    return ToolDefinition(
        name="grep",
        description="Search for a pattern in files under a path.",
        handler=_handler,
        destructive=False,
    )


def _tool_append_file_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        path_value = str(args.get("path") or "")
        content = str(args.get("content") or "")
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
        description="Append text content to a file in the project workspace.",
        handler=_handler,
        destructive=True,
    )


def _tool_replace_text_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        path_value = str(args.get("path") or "")
        old_text = str(args.get("old") or "")
        new_text = str(args.get("new") or "")
        count = args.get("count")
        if not path_value:
            raise ValueError("Path is required")
        if not old_text:
            raise ValueError("Old text is required")
        resolved = validate_path(path_value, policy)
        if not resolved.is_file():
            raise ValueError("File not found")
        content = resolved.read_text(encoding="utf-8", errors="replace")
        replace_count = int(count) if count is not None else -1
        updated = content.replace(old_text, new_text, replace_count)
        if updated == content:
            raise ValueError("No matches found for replacement")
        resolved.write_text(updated, encoding="utf-8")
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
        name="replace_text",
        description="Replace text in a file in the project workspace.",
        handler=_handler,
        destructive=True,
    )


def _tool_run_shell_factory(project_id: str, policy: AgentPolicy):
    def _handler(args: dict[str, Any]) -> ToolResult:
        command = str(args.get("command") or "")
        cwd_value = str(args.get("cwd") or "")
        timeout = int(args.get("timeout", 30))
        if not command:
            raise ValueError("Command is required")
        project = store.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        if cwd_value:
            cwd = validate_path(cwd_value, policy)
        else:
            cwd = Path(project.workspace_path).resolve()
        argv = shlex.split(command)
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
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
        out_path = artifacts_dir / f"agent-shell-{uuid4().hex}.txt"
        out_path.write_text(output_text)
        artifact = _upsert_agent_artifact(
            project_id,
            None,
            None,
            "shell_output",
            out_path,
            "text/plain",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Command failed ({result.returncode}): {result.stderr}")
        return ToolResult(
            output={
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            artifacts=[artifact.id],
        )

    return ToolDefinition(
        name="run_shell",
        description="Run a shell command inside the project workspace.",
        handler=_handler,
        destructive=True,
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
        "list_dir": {"path": "string (optional)", "depth": "int"},
        "read_file": {
            "path": "string",
            "start_line": "int (optional)",
            "end_line": "int (optional)",
            "max_bytes": "int (optional)",
        },
        "grep": {
            "pattern": "string",
            "path": "string (optional)",
            "regex": "bool (optional)",
            "max_matches": "int (optional)",
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
        "replace_text": {
            "path": "string",
            "old": "string",
            "new": "string",
            "count": "int (optional)",
        },
        "write_markdown": {"path": "string", "content": "string"},
        "run_python": {"code": "string (optional)", "path": "string (optional)"},
        "run_shell": {"command": "string", "cwd": "string (optional)", "timeout": "int (optional)"},
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
    policy = AgentPolicy(allowed_paths=allowed_paths)
    router = ToolRouter(policy)
    router.register(_tool_list_dir_factory(project_id, policy))
    router.register(_tool_read_file_factory(project_id, policy))
    router.register(_tool_grep_factory(project_id, policy))
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
    router.register(_tool_replace_text_factory(project_id, policy))
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


@dataclass
class PydanticAgentDeps:
    project_id: str
    safe_mode: bool
    router: ToolRouter


def _pydantic_ai_model_name() -> str:
    model_name = os.getenv("AGENT_MODEL") or os.getenv("OPENAI_MODEL") or "openai:gpt-4o-mini"
    if ":" in model_name:
        return model_name
    return f"openai:{model_name}"


def _extract_text_response(response: Any) -> str:
    if response is None:
        return ""
    parts = getattr(response, "parts", []) or []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, TextPart):
            chunks.append(getattr(part, "text", ""))
    return "".join(chunks).strip()


def _call_router_tool(ctx: RunContext[PydanticAgentDeps], name: str, args: dict[str, Any]) -> dict[str, Any]:
    tools = {tool.name: tool for tool in ctx.deps.router.list_tools()}
    tool = tools.get(name)
    if not tool:
        raise ValueError(f"Tool not registered: {name}")
    if ctx.deps.safe_mode and tool.destructive:
        raise ValueError(f"Tool not allowed in safe mode: {name}")
    result = ctx.deps.router.call(name, args, approved=not ctx.deps.safe_mode)
    return result.output or {}


def _build_pydantic_agent(project_id: str, safe_mode: bool) -> tuple[Agent, PydanticAgentDeps]:
    router, _ = _build_router(project_id)
    instructions = (
        "You are a project automation agent. Use the available tools to navigate the workspace, "
        "inspect files, write code, run scripts, and report results. "
        "If a tool fails, inspect outputs and retry with a fix. "
        "Prefer deterministic, auditable actions. "
        f"Safe mode is {'enabled' if safe_mode else 'disabled'}; if enabled, avoid destructive tools."
    )
    agent = Agent(
        _pydantic_ai_model_name(),
        deps_type=PydanticAgentDeps,
        instructions=instructions,
    )

    @agent.tool(name="list_dir", description="List files and folders under a path.")
    def _list_dir(ctx: RunContext[PydanticAgentDeps], path: str | None = None, depth: int = 1) -> dict[str, Any]:
        args: dict[str, Any] = {"depth": depth}
        if path:
            args["path"] = path
        return _call_router_tool(ctx, "list_dir", args)

    @agent.tool(name="read_file", description="Read a file from the workspace.")
    def _read_file(
        ctx: RunContext[PydanticAgentDeps],
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path}
        if start_line is not None:
            args["start_line"] = start_line
        if end_line is not None:
            args["end_line"] = end_line
        if max_bytes is not None:
            args["max_bytes"] = max_bytes
        return _call_router_tool(ctx, "read_file", args)

    @agent.tool(name="grep", description="Search for a pattern in files.")
    def _grep(
        ctx: RunContext[PydanticAgentDeps],
        pattern: str,
        path: str | None = None,
        regex: bool = False,
        max_matches: int = 50,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "pattern": pattern,
            "regex": regex,
            "max_matches": max_matches,
        }
        if path:
            args["path"] = path
        return _call_router_tool(ctx, "grep", args)

    @agent.tool(name="list_datasets", description="List datasets in the project.")
    def _list_datasets(ctx: RunContext[PydanticAgentDeps]) -> dict[str, Any]:
        return _call_router_tool(ctx, "list_datasets", {})

    @agent.tool(name="preview_dataset", description="Preview a dataset by id.")
    def _preview_dataset(ctx: RunContext[PydanticAgentDeps], dataset_id: str) -> dict[str, Any]:
        return _call_router_tool(ctx, "preview_dataset", {"dataset_id": dataset_id})

    @agent.tool(name="list_project_runs", description="List data runs for the project.")
    def _list_project_runs(ctx: RunContext[PydanticAgentDeps]) -> dict[str, Any]:
        return _call_router_tool(ctx, "list_project_runs", {})

    @agent.tool(name="list_artifacts", description="List artifacts for the project.")
    def _list_artifacts(
        ctx: RunContext[PydanticAgentDeps],
        run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"limit": limit, "offset": offset}
        if run_id:
            args["run_id"] = run_id
        return _call_router_tool(ctx, "list_artifacts", args)

    @agent.tool(name="write_file", description="Write text content to a file.")
    def _write_file(ctx: RunContext[PydanticAgentDeps], path: str, content: str) -> dict[str, Any]:
        return _call_router_tool(ctx, "write_file", {"path": path, "content": content})

    @agent.tool(name="append_file", description="Append text content to a file.")
    def _append_file(ctx: RunContext[PydanticAgentDeps], path: str, content: str) -> dict[str, Any]:
        return _call_router_tool(ctx, "append_file", {"path": path, "content": content})

    @agent.tool(name="replace_text", description="Replace text in a file.")
    def _replace_text(
        ctx: RunContext[PydanticAgentDeps],
        path: str,
        old: str,
        new: str,
        count: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path, "old": old, "new": new}
        if count is not None:
            args["count"] = count
        return _call_router_tool(ctx, "replace_text", args)

    @agent.tool(name="write_markdown", description="Write a markdown report file.")
    def _write_markdown(ctx: RunContext[PydanticAgentDeps], path: str, content: str) -> dict[str, Any]:
        return _call_router_tool(ctx, "write_markdown", {"path": path, "content": content})

    @agent.tool(name="run_python", description="Run a Python script.")
    def _run_python(
        ctx: RunContext[PydanticAgentDeps],
        path: str | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if path:
            args["path"] = path
        if code:
            args["code"] = code
        return _call_router_tool(ctx, "run_python", args)

    @agent.tool(name="run_shell", description="Run a shell command.")
    def _run_shell(
        ctx: RunContext[PydanticAgentDeps],
        command: str,
        cwd: str | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"command": command, "timeout": timeout}
        if cwd:
            args["cwd"] = cwd
        return _call_router_tool(ctx, "run_shell", args)

    @agent.tool(name="create_run", description="Create a dataset run.")
    def _create_run(ctx: RunContext[PydanticAgentDeps], dataset_id: str, type: str) -> dict[str, Any]:
        return _call_router_tool(ctx, "create_run", {"dataset_id": dataset_id, "type": type})

    @agent.tool(name="create_snapshot", description="Create a snapshot record for a file.")
    def _create_snapshot(
        ctx: RunContext[PydanticAgentDeps],
        kind: str,
        path: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"kind": kind, "path": path}
        if run_id:
            args["run_id"] = run_id
        if metadata:
            args["metadata"] = metadata
        return _call_router_tool(ctx, "create_snapshot", args)

    @agent.tool(name="request_rollback", description="Request a rollback for a run or snapshot.")
    def _request_rollback(
        ctx: RunContext[PydanticAgentDeps],
        run_id: str | None = None,
        snapshot_id: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if run_id:
            args["run_id"] = run_id
        if snapshot_id:
            args["snapshot_id"] = snapshot_id
        if note:
            args["note"] = note
        return _call_router_tool(ctx, "request_rollback", args)

    deps = PydanticAgentDeps(project_id=project_id, safe_mode=safe_mode, router=router)
    return agent, deps


def _build_pydantic_ai_run(
    project_id: str,
    prompt: str,
    dataset_id: str | None,
    safe_mode: bool,
) -> tuple[AgentRunRead, str]:
    agent, deps = _build_pydantic_agent(project_id, safe_mode)
    enriched_prompt = prompt
    if dataset_id:
        enriched_prompt += f"\n\nDataset ID: {dataset_id}"
    result = agent.run_sync(enriched_prompt, deps=deps)

    tool_calls: list[tuple[str, dict[str, Any], str | None]] = []
    outputs: dict[str, Any] = {}
    for message in result.all_messages():
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                tool_calls.append((part.tool_name, dict(part.args or {}), part.tool_call_id))
            elif isinstance(part, ToolReturnPart):
                if part.tool_call_id:
                    outputs[part.tool_call_id] = part.content

    steps: list[PlanStep] = []
    log_entries: list[dict[str, Any]] = []
    for tool_name, args, call_id in tool_calls:
        step_id = uuid4().hex
        output = outputs.get(call_id) if call_id else None
        status = StepStatus.APPLIED if output is not None else StepStatus.FAILED
        steps.append(
            PlanStep(
                id=step_id,
                title=f"Run {tool_name}",
                description=f"Invoke tool {tool_name}",
                tool=tool_name,
                args=args,
                requires_approval=False,
                status=status,
            )
        )
        record = ActionRecord(
            run_id="",
            step_id=step_id,
            tool=tool_name,
            args=args,
            status=status,
            output=output if isinstance(output, dict) else {"result": output},
            error=None if output is not None else "Missing tool return",
        )
        log_entries.append(record.model_dump(mode="json"))

    plan = Plan(objective=prompt, steps=steps)
    status = AgentRunStatus.COMPLETED
    if any(step.status == StepStatus.FAILED for step in steps):
        status = AgentRunStatus.FAILED

    agent_run = AgentRun(
        project_id=project_id,
        status=status.value,
        plan=plan.model_dump(mode="json"),
        log=log_entries,
    )
    with get_session() as session:
        session.add(agent_run)
        session.commit()
        session.refresh(agent_run)
    for entry in log_entries:
        entry["run_id"] = agent_run.id
    _write_run_artifacts(project_id, agent_run.id, plan.model_dump(mode="json"), log_entries)
    plan_payload = _plan_to_payload(plan)
    response_text = _extract_text_response(result.response)
    return AgentRunRead(
        id=agent_run.id,
        project_id=agent_run.project_id,
        status=AgentRunStatus(agent_run.status),
        plan=plan_payload,
        log=log_entries,
    ), response_text


def send_chat_message(
    project_id: str,
    content: str,
    dataset_id: str | None,
    safe_mode: bool = True,
    auto_run: bool = True,
) -> tuple[AgentChatMessage, AgentChatMessage, AgentRunRead | None]:
    user_message = create_chat_message(project_id, "user", content)
    backend = os.getenv("AGENT_BACKEND", "legacy").lower()
    run: AgentRunRead | None = None
    assistant_content = "Saved your note."

    if backend == "pydantic_ai" and auto_run:
        run, response_text = _build_pydantic_ai_run(project_id, content, dataset_id, safe_mode)
        assistant_content = response_text or f"Created agent run {run.id} with {len(run.plan.steps)} step(s)."
    else:
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
