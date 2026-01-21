from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import re
import sqlite3
import subprocess

from sqlalchemy import func
from sqlmodel import select

from app.models.db import AgentArtifact, AgentChatMessage, AgentRun, AgentRollback, AgentSnapshot, AgentSkill
from app.models.schemas import (
    AgentApproval,
    AgentPlanCreate,
    AgentRunRead,
    AgentRunStatus,
    AgentToolRead,
)
from app.services import store
from app.services.db import get_session
from packages.runtime.agent.llm import AgentDeps, LLMError, build_agent


@dataclass
class ToolLogEntry:
    tool: str
    args: dict[str, Any]
    output: dict[str, Any]
    status: str = "applied"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ProjectToolRuntime:
    project_id: str
    workspace_root: Path
    log: list[ToolLogEntry] = field(default_factory=list)

    def _resolve(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Path is required")
        resolved = (self.workspace_root / path).resolve()
        if not resolved.is_relative_to(self.workspace_root):
            raise ValueError("Path escapes project workspace")
        return resolved

    def _record_artifact(self, path: Path, artifact_type: str, mime_type: str) -> None:
        size = path.stat().st_size if path.exists() else 0
        artifact = AgentArtifact(
            project_id=self.project_id,
            run_id=None,
            snapshot_id=None,
            type=artifact_type,
            path=str(path),
            mime_type=mime_type,
            size=size,
        )
        with get_session() as session:
            session.add(artifact)
            session.commit()

    def _log(self, tool: str, args: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
        entry = ToolLogEntry(tool=tool, args=args, output=output)
        self.log.append(entry)
        return output

    def list_dir(
        self,
        path: str,
        recursive: bool = False,
        include_hidden: bool = False,
        max_entries: int = 200,
    ) -> dict[str, Any]:
        root = self._resolve(path)
        entries: list[dict[str, Any]] = []
        iterator = root.rglob("*") if recursive else root.iterdir()
        for entry in iterator:
            name = entry.name
            if not include_hidden and name.startswith("."):
                continue
            entries.append(
                {
                    "path": str(entry.relative_to(self.workspace_root)),
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None,
                }
            )
            if len(entries) >= max_entries:
                break
        return self._log(
            "list_dir",
            {
                "path": path,
                "recursive": recursive,
                "include_hidden": include_hidden,
                "max_entries": max_entries,
            },
            {"entries": entries},
        )

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_lines: int = 200,
    ) -> dict[str, Any]:
        target = self._resolve(path)
        if not target.exists():
            raise ValueError(f"File not found: {path}")
        if start_line < 1:
            start_line = 1
        end = end_line or (start_line + max_lines - 1)
        lines: list[str] = []
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            for idx, line in enumerate(handle, start=1):
                if idx < start_line:
                    continue
                if idx > end:
                    break
                lines.append(line.rstrip("\n"))
        return self._log(
            "read_file",
            {
                "path": path,
                "start_line": start_line,
                "end_line": end,
                "max_lines": max_lines,
            },
            {"lines": lines, "start_line": start_line, "end_line": end},
        )

    def search_text(
        self,
        query: str,
        path: str | None = None,
        is_regex: bool = False,
        include_hidden: bool = False,
        max_results: int = 50,
    ) -> dict[str, Any]:
        base = self._resolve(path) if path else self.workspace_root
        results: list[dict[str, Any]] = []
        pattern = re.compile(query) if is_regex else None
        for file_path in base.rglob("*"):
            if file_path.is_dir():
                continue
            if not include_hidden and any(part.startswith(".") for part in file_path.parts):
                continue
            try:
                with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                    for idx, line in enumerate(handle, start=1):
                        if is_regex:
                            if pattern is None or not pattern.search(line):
                                continue
                        else:
                            if query not in line:
                                continue
                        results.append(
                            {
                                "path": str(file_path.relative_to(self.workspace_root)),
                                "line": idx,
                                "text": line.rstrip("\n"),
                            }
                        )
                        if len(results) >= max_results:
                            raise StopIteration
            except StopIteration:
                break
        return self._log(
            "search_text",
            {
                "query": query,
                "path": path,
                "is_regex": is_regex,
                "include_hidden": include_hidden,
                "max_results": max_results,
            },
            {"results": results},
        )

    def list_db_tables(self, db_path: str | None = None) -> dict[str, Any]:
        target = self._resolve(db_path) if db_path else self._find_default_db()
        conn = sqlite3.connect(target)
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()
        return self._log("list_db_tables", {"db_path": str(target)}, {"tables": tables})

    def query_db(self, sql: str, db_path: str | None = None, limit: int = 200) -> dict[str, Any]:
        target = self._resolve(db_path) if db_path else self._find_default_db()
        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(sql)
            rows = cursor.fetchmany(limit)
            output = [dict(row) for row in rows]
        finally:
            conn.close()
        return self._log(
            "query_db",
            {"sql": sql, "db_path": str(target), "limit": limit},
            {"rows": output},
        )

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._record_artifact(target, "text_file", "text/plain")
        return self._log("write_file", {"path": path}, {"path": path, "bytes": len(content)})

    def write_markdown(self, path: str, content: str) -> dict[str, Any]:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._record_artifact(target, "markdown", "text/markdown")
        return self._log(
            "write_markdown",
            {"path": path},
            {"path": path, "bytes": len(content)},
        )

    def run_python(self, code: str | None = None, path: str | None = None) -> dict[str, Any]:
        if not code and not path:
            raise ValueError("run_python requires code or path")
        if path:
            target = self._resolve(path)
        else:
            run_id = uuid4().hex
            target = self._resolve(f"artifacts/agent/run_python_{run_id}.py")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code or "", encoding="utf-8")
            self._record_artifact(target, "text_file", "text/plain")
        cmd = ["uv", "run", "python", str(target.relative_to(self.workspace_root))]
        proc = subprocess.run(
            cmd,
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
        )
        output_path = self._resolve(f"artifacts/agent/agent-python-{uuid4().hex}.txt")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")
        self._record_artifact(output_path, "python_run_output", "text/plain")
        return self._log(
            "run_python",
            {"path": str(target.relative_to(self.workspace_root))},
            {
                "path": str(target.relative_to(self.workspace_root)),
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            },
        )

    def _find_default_db(self) -> Path:
        candidates = list(self.workspace_root.rglob("*.db"))
        if not candidates:
            raise ValueError("No sqlite database found; provide db_path")
        return candidates[0]


_TOOL_SPECS: list[AgentToolRead] = [
    AgentToolRead(name="list_dir", description="List files and folders.", destructive=False),
    AgentToolRead(name="read_file", description="Read a text file.", destructive=False),
    AgentToolRead(name="search_text", description="Search text in files.", destructive=False),
    AgentToolRead(name="list_db_tables", description="List tables in a sqlite database.", destructive=False),
    AgentToolRead(name="query_db", description="Query a sqlite database.", destructive=False),
    AgentToolRead(name="write_file", description="Write a text file.", destructive=True),
    AgentToolRead(name="write_markdown", description="Write a markdown file.", destructive=True),
    AgentToolRead(name="run_python", description="Run a python script.", destructive=True),
]


def list_tools(project_id: str) -> list[AgentToolRead]:
    return _TOOL_SPECS


def _create_chat_message(project_id: str, role: str, content: str, run_id: str | None = None) -> AgentChatMessage:
    message = AgentChatMessage(project_id=project_id, role=role, content=content, run_id=run_id)
    with get_session() as session:
        session.add(message)
        session.commit()
        session.refresh(message)
    return message


def send_chat_message(
    project_id: str,
    content: str,
    dataset_id: str | None,
    safe_mode: bool,
    auto_run: bool,
) -> tuple[AgentChatMessage, AgentChatMessage, AgentRunRead | None]:
    user_message = _create_chat_message(project_id, "user", content)
    assistant_content = "Saved your note."
    run_read: AgentRunRead | None = None

    if auto_run:
        project = store.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        tools = ProjectToolRuntime(project_id=project_id, workspace_root=Path(project.workspace_path))
        agent = build_agent()
        try:
            result = agent.run_sync(content, deps=AgentDeps(tools=tools))
        except Exception as exc:
            raise LLMError(str(exc)) from exc
        assistant_content = result.output
        plan = AgentPlanCreate(objective="PydanticAI autonomous run", steps=[])
        log = [
            {
                "tool": entry.tool,
                "args": entry.args,
                "output": entry.output,
                "status": entry.status,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in tools.log
        ]
        run = AgentRun(
            project_id=project_id,
            status=AgentRunStatus.COMPLETED.value,
            plan=plan.model_dump(mode="json"),
            log=log,
        )
        with get_session() as session:
            session.add(run)
            session.commit()
            session.refresh(run)
        run_read = AgentRunRead(
            id=run.id,
            project_id=run.project_id,
            status=AgentRunStatus.COMPLETED,
            plan=plan,
            log=log,
        )
        assistant_message = _create_chat_message(
            project_id, "assistant", assistant_content, run_id=run.id
        )
        return user_message, assistant_message, run_read

    assistant_message = _create_chat_message(project_id, "assistant", assistant_content)
    return user_message, assistant_message, run_read


def run_plan(
    project_id: str,
    plan: AgentPlanCreate,
    approvals: dict[str, AgentApproval] | None,
) -> AgentRunRead:
    project = store.get_project(project_id)
    if not project:
        raise ValueError("Project not found")
    tools = ProjectToolRuntime(project_id=project_id, workspace_root=Path(project.workspace_path))
    status = AgentRunStatus.COMPLETED
    for step in plan.steps:
        tool_name = step.tool
        args = step.args or {}
        try:
            if tool_name == "list_dir":
                tools.list_dir(**args)
            elif tool_name == "read_file":
                tools.read_file(**args)
            elif tool_name == "search_text":
                tools.search_text(**args)
            elif tool_name == "list_db_tables":
                tools.list_db_tables(**args)
            elif tool_name == "query_db":
                tools.query_db(**args)
            elif tool_name == "write_file":
                tools.write_file(**args)
            elif tool_name == "write_markdown":
                tools.write_markdown(**args)
            elif tool_name == "run_python":
                tools.run_python(**args)
            else:
                raise ValueError(f"Unknown tool: {tool_name}")
        except Exception:
            status = AgentRunStatus.FAILED
            break

    log = [
        {
            "tool": entry.tool,
            "args": entry.args,
            "output": entry.output,
            "status": entry.status,
            "created_at": entry.created_at.isoformat(),
        }
        for entry in tools.log
    ]
    run = AgentRun(
        project_id=project_id,
        status=status.value,
        plan=plan.model_dump(mode="json"),
        log=log,
    )
    with get_session() as session:
        session.add(run)
        session.commit()
        session.refresh(run)
    return AgentRunRead(
        id=run.id,
        project_id=run.project_id,
        status=status,
        plan=plan,
        log=log,
    )


def apply_run_step(
    project_id: str, run_id: str, step_id: str, payload: AgentApproval
) -> AgentRunRead | None:
    raise ValueError("Step approvals are not supported in the new agent")


def list_runs(project_id: str, limit: int = 100, offset: int = 0) -> list[AgentRunRead]:
    with get_session() as session:
        results = list(session.exec(
            select(AgentRun)
            .where(AgentRun.project_id == project_id)
            .limit(limit)
            .offset(offset)
        ))
    return [
        AgentRunRead(
            id=item.id,
            project_id=item.project_id,
            status=AgentRunStatus(item.status),
            plan=_normalize_plan(item.plan),
            log=item.log or [],
        )
        for item in results
    ]


def count_runs(project_id: str) -> int:
    with get_session() as session:
        result = session.exec(
            select(func.count()).select_from(AgentRun).where(AgentRun.project_id == project_id)
        ).one()
    return int(result or 0)


def get_run(project_id: str, run_id: str) -> AgentRunRead | None:
    with get_session() as session:
        run = session.get(AgentRun, run_id)
    if not run or run.project_id != project_id:
        return None
    return AgentRunRead(
        id=run.id,
        project_id=run.project_id,
        status=AgentRunStatus(run.status),
        plan=_normalize_plan(run.plan),
        log=run.log or [],
    )


def list_chat_messages(project_id: str, limit: int = 100, offset: int = 0) -> list[AgentChatMessage]:
    with get_session() as session:
        return list(session.exec(
            select(AgentChatMessage)
            .where(AgentChatMessage.project_id == project_id)
            .limit(limit)
            .offset(offset)
        ))


def count_chat_messages(project_id: str) -> int:
    with get_session() as session:
        result = session.exec(
            select(func.count())
            .select_from(AgentChatMessage)
            .where(AgentChatMessage.project_id == project_id)
        ).one()
    return int(result or 0)


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
        return list(session.exec(query.limit(limit).offset(offset)))


def count_agent_artifacts(
    project_id: str, run_id: str | None, snapshot_id: str | None
) -> int:
    with get_session() as session:
        query = select(func.count()).select_from(AgentArtifact).where(
            AgentArtifact.project_id == project_id
        )
        if run_id:
            query = query.where(AgentArtifact.run_id == run_id)
        if snapshot_id:
            query = query.where(AgentArtifact.snapshot_id == snapshot_id)
        result = session.exec(query).one()
    return int(result or 0)


def get_agent_artifact(project_id: str, artifact_id: str) -> AgentArtifact | None:
    with get_session() as session:
        artifact = session.get(AgentArtifact, artifact_id)
    if not artifact or artifact.project_id != project_id:
        return None
    return artifact


def list_snapshots(project_id: str, limit: int = 100, offset: int = 0) -> list[AgentSnapshot]:
    with get_session() as session:
        return list(session.exec(
            select(AgentSnapshot)
            .where(AgentSnapshot.project_id == project_id)
            .limit(limit)
            .offset(offset)
        ))


def count_snapshots(project_id: str) -> int:
    with get_session() as session:
        result = session.exec(
            select(func.count())
            .select_from(AgentSnapshot)
            .where(AgentSnapshot.project_id == project_id)
        ).one()
    return int(result or 0)


def create_snapshot(
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
        details=details or {"snapshot_path": target_path},
    )
    with get_session() as session:
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
    return snapshot


def restore_snapshot(project_id: str, snapshot_id: str) -> AgentRollback | None:
    with get_session() as session:
        snapshot = session.get(AgentSnapshot, snapshot_id)
        if not snapshot or snapshot.project_id != project_id:
            return None
        rollback = AgentRollback(
            project_id=project_id,
            run_id=snapshot.run_id,
            snapshot_id=snapshot.id,
            status="applied",
            note="snapshot restore",
        )
        session.add(rollback)
        session.commit()
        session.refresh(rollback)
    return rollback


def create_rollback(
    project_id: str,
    run_id: str | None,
    snapshot_id: str | None,
    note: str | None,
) -> AgentRollback:
    rollback = AgentRollback(
        project_id=project_id,
        run_id=run_id,
        snapshot_id=snapshot_id,
        status="pending",
        note=note,
    )
    with get_session() as session:
        session.add(rollback)
        session.commit()
        session.refresh(rollback)
    return rollback


def list_rollbacks(project_id: str, limit: int = 100, offset: int = 0) -> list[AgentRollback]:
    with get_session() as session:
        return list(session.exec(
            select(AgentRollback)
            .where(AgentRollback.project_id == project_id)
            .limit(limit)
            .offset(offset)
        ))


def count_rollbacks(project_id: str) -> int:
    with get_session() as session:
        result = session.exec(
            select(func.count())
            .select_from(AgentRollback)
            .where(AgentRollback.project_id == project_id)
        ).one()
    return int(result or 0)


def apply_rollback(project_id: str, rollback_id: str) -> AgentRollback | None:
    with get_session() as session:
        rollback = session.get(AgentRollback, rollback_id)
        if not rollback or rollback.project_id != project_id:
            return None
        rollback.status = "applied"
        session.add(rollback)
        session.commit()
        session.refresh(rollback)
    return rollback


def cancel_rollback(project_id: str, rollback_id: str) -> AgentRollback | None:
    with get_session() as session:
        rollback = session.get(AgentRollback, rollback_id)
        if not rollback or rollback.project_id != project_id:
            return None
        rollback.status = "cancelled"
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
        return list(session.exec(
            select(AgentSkill)
            .where(AgentSkill.project_id == project_id)
            .limit(limit)
            .offset(offset)
        ))


def _normalize_plan(payload: Any | None) -> AgentPlanCreate:
    if isinstance(payload, dict):
        return AgentPlanCreate.model_validate(payload)
    return AgentPlanCreate(objective="", steps=[])


def count_skills(project_id: str) -> int:
    with get_session() as session:
        result = session.exec(
            select(func.count())
            .select_from(AgentSkill)
            .where(AgentSkill.project_id == project_id)
        ).one()
    return int(result or 0)


def get_skill(project_id: str, skill_id: str) -> AgentSkill | None:
    with get_session() as session:
        skill = session.get(AgentSkill, skill_id)
    if not skill or skill.project_id != project_id:
        return None
    return skill


def update_skill(project_id: str, skill_id: str, updates: dict[str, Any]) -> AgentSkill | None:
    with get_session() as session:
        skill = session.get(AgentSkill, skill_id)
        if not skill or skill.project_id != project_id:
            return None
        for key, value in updates.items():
            setattr(skill, key, value)
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
