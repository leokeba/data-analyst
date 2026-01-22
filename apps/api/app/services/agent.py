from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import os
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


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "projects").exists():
            return parent
    return Path.cwd()


def _skill_dirs(workspace_root: Path) -> list[Path]:
    return [
        _repo_root() / "skills",
        workspace_root / "skills",
    ]


def _load_skill_files(directory: Path, max_chars: int = 4000) -> list[str]:
    if not directory.exists() or not directory.is_dir():
        return []
    entries: list[str] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not content:
            continue
        if len(content) > max_chars:
            content = content[:max_chars].rstrip() + "\n\n...(truncated)"
        entries.append(f"## {path.name}\n{content}")
    return entries


def _build_skills_context(workspace_root: Path) -> str:
    sections: list[str] = []
    for directory in _skill_dirs(workspace_root):
        sections.extend(_load_skill_files(directory))
    return "\n\n".join(sections).strip()


@dataclass
class ToolLogEntry:
    tool: str
    args: dict[str, Any]
    output: dict[str, Any]
    status: str = "applied"
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ProjectToolRuntime:
    project_id: str
    workspace_root: Path
    run_id: str | None = None
    log: list[ToolLogEntry] = field(default_factory=list)

    def _resolve(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Path is required")
        resolved = (self.workspace_root / path).resolve()
        if not resolved.is_relative_to(self.workspace_root):
            raise ValueError("Path escapes project workspace")
        return resolved

    def _is_probably_binary(self, path: Path) -> bool:
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".parquet", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
            return True
        try:
            with path.open("rb") as handle:
                chunk = handle.read(1024)
            return b"\x00" in chunk
        except OSError:
            return False

    def _find_default_db(self) -> Path:
        candidates = list(self.workspace_root.rglob("*.db"))
        if not candidates:
            raise ValueError("No sqlite database found; provide db_path")
        return candidates[0]

    def _record_artifact(self, path: Path, artifact_type: str, mime_type: str) -> None:
        size = path.stat().st_size if path.exists() else 0
        artifact = AgentArtifact(
            project_id=self.project_id,
            run_id=self.run_id,
            snapshot_id=None,
            type=artifact_type,
            path=str(path),
            mime_type=mime_type,
            size=size,
        )
        with get_session() as session:
            session.add(artifact)
            session.commit()

    def _serialize_log_entry(self, entry: ToolLogEntry) -> dict[str, Any]:
        return {
            "tool": entry.tool,
            "args": entry.args,
            "output": entry.output,
            "status": entry.status,
            "error": entry.error,
            "created_at": entry.created_at.isoformat(),
        }

    def _append_run_log(self, entry: ToolLogEntry) -> None:
        if not self.run_id:
            return
        payload = self._serialize_log_entry(entry)
        with get_session() as session:
            run = session.get(AgentRun, self.run_id)
            if not run or run.project_id != self.project_id:
                return
            existing = run.log or []
            run.log = [*existing, payload]
            session.add(run)
            session.commit()

    def _log(
        self,
        tool: str,
        args: dict[str, Any],
        output: dict[str, Any],
        status: str = "applied",
        error: str | None = None,
    ) -> dict[str, Any]:
        entry = ToolLogEntry(tool=tool, args=args, output=output, status=status, error=error)
        self.log.append(entry)
        self._append_run_log(entry)
        return output

    def list_dir(
        self,
        path: str,
        recursive: bool = False,
        include_hidden: bool = False,
        max_entries: int = 200,
    ) -> dict[str, Any]:
        safe_path = path.strip() if isinstance(path, str) else ""
        if not safe_path:
            safe_path = "."
        if max_entries < 1:
            max_entries = 200
        args = {
            "path": safe_path,
            "recursive": recursive,
            "include_hidden": include_hidden,
            "max_entries": max_entries,
        }
        try:
            root = self._resolve(safe_path)
            if not root.exists() or not root.is_dir():
                raise ValueError(f"Directory not found: {safe_path}")
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
            return self._log("list_dir", args, {"entries": entries})
        except Exception as exc:
            fallback: list[str] = []
            try:
                fallback = [
                    entry.name
                    for entry in self.workspace_root.iterdir()
                    if entry.is_dir() and not entry.name.startswith(".")
                ]
            except OSError:
                fallback = []
            return self._log(
                "list_dir",
                args,
                {
                    "error": str(exc),
                    "fallback": f"Valid directories: {', '.join(sorted(fallback))}" if fallback else "Valid directories: (unavailable)",
                },
                status="failed",
                error=str(exc),
            )

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_lines: int = 200,
    ) -> dict[str, Any]:
        args = {
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "max_lines": max_lines,
        }
        try:
            target = self._resolve(path)
            if not target.exists():
                raise ValueError(f"File not found: {path}")
            if self._is_probably_binary(target):
                return self._log(
                    "read_file",
                    args,
                    {
                        "error": "Binary file detected; use a data-specific tool instead of read_file.",
                        "path": path,
                        "hint": "For sqlite use list_db_tables/query_db; for images or binaries use run_python to read/plot.",
                    },
                    status="failed",
                    error="Binary file detected",
                )
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
                {"path": path, "start_line": start_line, "end_line": end, "max_lines": max_lines},
                {"lines": lines, "start_line": start_line, "end_line": end},
            )
        except Exception as exc:
            return self._log(
                "read_file",
                args,
                {"error": str(exc)},
                status="failed",
                error=str(exc),
            )

    def search_text(
        self,
        query: str,
        path: str | None = None,
        is_regex: bool = False,
        include_hidden: bool = False,
        max_results: int = 50,
    ) -> dict[str, Any]:
        args = {
            "query": query,
            "path": path,
            "is_regex": is_regex,
            "include_hidden": include_hidden,
            "max_results": max_results,
        }
        try:
            base = self._resolve(path) if path else self.workspace_root
            results: list[dict[str, Any]] = []
            pattern = re.compile(query) if is_regex else None
            skipped_binary = 0
            for file_path in base.rglob("*"):
                if file_path.is_dir():
                    continue
                if not include_hidden and any(part.startswith(".") for part in file_path.parts):
                    continue
                if self._is_probably_binary(file_path):
                    skipped_binary += 1
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
            output: dict[str, Any] = {"results": results}
            if skipped_binary:
                output["skipped_binary"] = skipped_binary
                output["hint"] = "Binary files were skipped (e.g., sqlite db). Use list_db_tables/query_db instead."
            return self._log("search_text", args, output)
        except Exception as exc:
            return self._log(
                "search_text",
                args,
                {"error": str(exc)},
                status="failed",
                error=str(exc),
            )

    def list_db_tables(self, db_path: str | None = None) -> dict[str, Any]:
        args = {"db_path": db_path}
        try:
            target = self._resolve(db_path) if db_path else self._find_default_db()
            conn = sqlite3.connect(target)
            try:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                tables = [row[0] for row in cursor.fetchall()]
            finally:
                conn.close()
            return self._log(
                "list_db_tables",
                {"db_path": str(target)},
                {
                    "tables": tables,
                    "hint": "Use query_db and PRAGMA table_info(<table>) to inspect columns.",
                },
            )
        except Exception as exc:
            return self._log(
                "list_db_tables",
                args,
                {"error": str(exc)},
                status="failed",
                error=str(exc),
            )

    def query_db(self, sql: str, db_path: str | None = None, limit: int = 200) -> dict[str, Any]:
        args = {"sql": sql, "db_path": db_path, "limit": limit}
        try:
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
        except Exception as exc:
            error_text = str(exc)
            hint: str | None = None
            extra: dict[str, Any] = {}
            missing_table = re.search(r"no such table: ([\w_]+)", error_text)
            missing_column = re.search(r"no such column: ([\w_]+)", error_text)
            if missing_table:
                table_name = missing_table.group(1)
                hint = (
                    "Table not found. Use list_db_tables to see available tables. "
                    "If the data is a CSV/JSON file, load it via run_python instead of query_db."
                )
                for ext in (".csv", ".json"):
                    candidate = self.workspace_root / "data" / "raw" / f"{table_name}{ext}"
                    if candidate.exists():
                        extra["hint_file"] = str(candidate.relative_to(self.workspace_root))
                        break
                try:
                    conn = sqlite3.connect(self._resolve(db_path) if db_path else self._find_default_db())
                    cursor = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                    extra["available_tables"] = [row[0] for row in cursor.fetchall()]
                    conn.close()
                except Exception:
                    pass
            if missing_column:
                column_name = missing_column.group(1)
                table_match = re.search(r"from\s+([\w_]+)", sql, flags=re.IGNORECASE)
                hint = (
                    "Column not found. Use PRAGMA table_info(<table>) to inspect columns "
                    "and update the query."
                )
                if table_match:
                    table_name = table_match.group(1)
                    try:
                        conn = sqlite3.connect(self._resolve(db_path) if db_path else self._find_default_db())
                        cursor = conn.execute(f"PRAGMA table_info({table_name})")
                        extra["available_columns"] = [row[1] for row in cursor.fetchall()]
                        conn.close()
                    except Exception:
                        pass
                extra["missing_column"] = column_name
            payload = {"error": error_text}
            if hint:
                payload["hint"] = hint
            payload.update(extra)
            return self._log(
                "query_db",
                args,
                payload,
                status="failed",
                error=error_text,
            )

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        args = {"path": path}
        try:
            target = self._resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self._record_artifact(target, "text_file", "text/plain")
            return self._log("write_file", args, {"path": path, "bytes": len(content)})
        except Exception as exc:
            return self._log(
                "write_file",
                args,
                {"error": str(exc)},
                status="failed",
                error=str(exc),
            )

    def write_markdown(self, path: str, content: str) -> dict[str, Any]:
        args = {"path": path}
        try:
            target = self._resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self._record_artifact(target, "markdown", "text/markdown")
            return self._log(
                "write_markdown",
                args,
                {"path": path, "bytes": len(content)},
            )
        except Exception as exc:
            return self._log(
                "write_markdown",
                args,
                {"error": str(exc)},
                status="failed",
                error=str(exc),
            )

    def run_python(self, code: str | None = None, path: str | None = None) -> dict[str, Any]:
        args = {"code": code, "path": path}
        try:
            if not code and not path:
                raise ValueError("run_python requires code or path")
            if path:
                target = self._resolve(path)
                if target.suffix.lower() != ".py":
                    return self._log(
                        "run_python",
                        args,
                        {
                            "error": "run_python only accepts .py scripts when using path.",
                            "path": str(target.relative_to(self.workspace_root)),
                            "hint": "Use run_python with inline code to read CSV/JSON files.",
                        },
                        status="failed",
                        error="Invalid run_python path",
                    )
                source = target.read_text(encoding="utf-8", errors="replace")
            else:
                run_id = uuid4().hex
                target = self._resolve(f"artifacts/agent/run_python_{run_id}.py")
                target.parent.mkdir(parents=True, exist_ok=True)
                prefix = (
                    "import os\n"
                    "_PROJECT_ROOT = os.environ.get('PROJECT_ROOT')\n"
                    "if _PROJECT_ROOT:\n"
                    "    os.chdir(_PROJECT_ROOT)\n\n"
                )
                source = code or ""
                target.write_text(prefix + source, encoding="utf-8")
                self._record_artifact(target, "text_file", "text/plain")
            missing_inputs = _script_missing_inputs(source, self.workspace_root)
            if missing_inputs:
                return self._log(
                    "run_python",
                    args,
                    {
                        "error": "Script references input files that do not exist in the workspace.",
                        "missing_inputs": missing_inputs,
                        "hint": "Use list_dir/read_file to locate data files before running scripts.",
                        "path": str(target.relative_to(self.workspace_root)),
                    },
                    status="failed",
                    error="Missing input files for run_python",
                )
            if not _script_reads_data(source) and _script_looks_hardcoded(source):
                return self._log(
                    "run_python",
                    args,
                    {
                        "error": "Script appears to hard-code data without reading files or databases.",
                        "path": str(target.relative_to(self.workspace_root)),
                    },
                    status="failed",
                    error="Script must load workspace data; hard-coded arrays are not allowed.",
                )
            cmd = ["uv", "run", "python", str(target.relative_to(self.workspace_root))]
            env = os.environ.copy()
            env["PROJECT_ROOT"] = str(self.workspace_root)
            proc = subprocess.run(
                cmd,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                env=env,
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
        except Exception as exc:
            return self._log(
                "run_python",
                args,
                {"error": str(exc)},
                status="failed",
                error=str(exc),
            )


def _script_reads_data(source: str) -> bool:
    patterns = [
        r"\bopen\(",
        r"pandas\.read_",
        r"pd\.read_",
        r"pandas\.read_sql",
        r"pd\.read_sql",
        r"pandas\.read_sql_query",
        r"pd\.read_sql_query",
        r"sqlite3\.connect\(",
        r"json\.load\(",
        r"csv\.reader\(",
    ]
    return any(re.search(pat, source) for pat in patterns)


def _script_looks_hardcoded(source: str) -> bool:
    if re.search(r"=\s*\[[^\]]{80,}\]", source, flags=re.DOTALL):
        return True
    numbers = re.findall(r"\b\d+(?:\.\d+)?\b", source)
    return len(numbers) >= 12


def _script_missing_inputs(source: str, workspace_root: Path) -> list[str]:
    missing: list[str] = []
    patterns = [
        r"(?:pandas|pd)\.read_csv\(\s*['\"]([^'\"]+)['\"]",
        r"(?:pandas|pd)\.read_json\(\s*['\"]([^'\"]+)['\"]",
        r"(?:pandas|pd)\.read_parquet\(\s*['\"]([^'\"]+)['\"]",
        r"(?:pandas|pd)\.read_excel\(\s*['\"]([^'\"]+)['\"]",
        r"(?:pandas|pd)\.read_table\(\s*['\"]([^'\"]+)['\"]",
        r"\bopen\(\s*['\"]([^'\"]+)['\"]",
        r"sqlite3\.connect\(\s*['\"]([^'\"]+)['\"]",
    ]
    candidates: list[str] = []
    for pattern in patterns:
        candidates.extend(re.findall(pattern, source))
    for raw_path in candidates:
        if not raw_path or "://" in raw_path:
            continue
        try:
            resolved = (workspace_root / raw_path).resolve()
            if not resolved.is_relative_to(workspace_root):
                missing.append(raw_path)
                continue
            if not resolved.exists():
                missing.append(raw_path)
        except Exception:
            missing.append(raw_path)
    return sorted(set(missing))


_TOOL_SPECS: list[AgentToolRead] = [
    AgentToolRead(
        name="list_dir",
        description="List files and folders. Start from project root '.' when unsure.",
        destructive=False,
    ),
    AgentToolRead(
        name="read_file",
        description="Read a text file (not for binaries like .db/.png).",
        destructive=False,
    ),
    AgentToolRead(
        name="search_text",
        description="Search text in files (skips binaries like sqlite .db).",
        destructive=False,
    ),
    AgentToolRead(
        name="list_db_tables",
        description="List tables in a sqlite database (default: first .db in project).",
        destructive=False,
    ),
    AgentToolRead(
        name="query_db",
        description="Query a sqlite database. Use list_db_tables/PRAGMA table_info first.",
        destructive=False,
    ),
    AgentToolRead(name="write_file", description="Write a text file.", destructive=True),
    AgentToolRead(name="write_markdown", description="Write a markdown file.", destructive=True),
    AgentToolRead(
        name="run_python",
        description=(
            "Run a python script (cwd=project root; PROJECT_ROOT env provided). "
            "Scripts must load workspace data (no hard-coded arrays)."
        ),
        destructive=True,
    ),
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


def _summarize_tool_log(log: list[ToolLogEntry], max_items: int = 6) -> str:
    lines: list[str] = []
    for entry in log[-max_items:]:
        error = entry.error or (entry.output or {}).get("error")
        lines.append(
            f"{entry.tool} status={entry.status} error={error or 'none'}"
        )
    return "\n".join(lines)


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
        plan = AgentPlanCreate(objective="PydanticAI autonomous run", steps=[])
        run = AgentRun(
            project_id=project_id,
            status=AgentRunStatus.PENDING.value,
            plan=plan.model_dump(mode="json"),
            log=[],
        )
        with get_session() as session:
            session.add(run)
            session.commit()
            session.refresh(run)
        tools = ProjectToolRuntime(
            project_id=project_id,
            workspace_root=Path(project.workspace_path),
            run_id=run.id,
        )
        skills_context = _build_skills_context(tools.workspace_root)
        agent = build_agent(extra_instructions=skills_context or None)
        root_entries: list[str] = []
        try:
            for entry in tools.workspace_root.iterdir():
                name = entry.name + "/" if entry.is_dir() else entry.name
                if name.startswith("."):
                    continue
                root_entries.append(name)
        except OSError:
            root_entries = []
        filesystem_map = ", ".join(sorted(root_entries)) or "(unavailable)"
        augmented_content = (
            f"Project root contents: {filesystem_map}. "
            f"{content}"
        )
        result = None
        retry_context = ""
        max_attempts = 3
        status = AgentRunStatus.COMPLETED
        try:
            for attempt in range(max_attempts):
                try:
                    result = agent.run_sync(
                        augmented_content + retry_context, deps=AgentDeps(tools=tools)
                    )
                    break
                except Exception as exc:
                    error_text = str(exc)
                    if attempt >= max_attempts - 1:
                        raise LLMError(error_text) from exc
                    summary = _summarize_tool_log(tools.log)
                    retry_context = (
                        "\n\nPrevious attempt failed with tool errors. "
                        "Review the tool log summary and retry with corrected tool usage.\n"
                        f"Error: {error_text}\n"
                        f"Recent tool log:\n{summary}\n"
                    )
            if result is None:
                raise LLMError("Agent failed without producing a result")
            assistant_content = result.output
        except LLMError:
            status = AgentRunStatus.FAILED
            raise
        finally:
            log = [tools._serialize_log_entry(entry) for entry in tools.log]
            with get_session() as session:
                run_db = session.get(AgentRun, run.id)
                if run_db:
                    run_db.status = status.value
                    run_db.plan = plan.model_dump(mode="json")
                    run_db.log = log
                    session.add(run_db)
                    session.commit()
                    session.refresh(run_db)

        run_read = AgentRunRead(
            id=run.id,
            project_id=run.project_id,
            status=status,
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
    run = AgentRun(
        project_id=project_id,
        status=AgentRunStatus.PENDING.value,
        plan=plan.model_dump(mode="json"),
        log=[],
    )
    with get_session() as session:
        session.add(run)
        session.commit()
        session.refresh(run)
    tools = ProjectToolRuntime(
        project_id=project_id,
        workspace_root=Path(project.workspace_path),
        run_id=run.id,
    )
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
        if any(entry.status == "failed" for entry in tools.log):
            status = AgentRunStatus.FAILED
            break

    log = [tools._serialize_log_entry(entry) for entry in tools.log]
    with get_session() as session:
        run_db = session.get(AgentRun, run.id)
        if run_db:
            run_db.status = status.value
            run_db.plan = plan.model_dump(mode="json")
            run_db.log = log
            session.add(run_db)
            session.commit()
            session.refresh(run_db)
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
