from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
import os

from pydantic_ai import Agent, RunContext


class LLMError(RuntimeError):
    pass


class ToolRuntime(Protocol):
    def list_dir(
        self,
        path: str,
        recursive: bool = False,
        include_hidden: bool = False,
        max_entries: int = 200,
    ) -> dict[str, Any]:
        ...

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_lines: int = 200,
    ) -> dict[str, Any]:
        ...

    def search_text(
        self,
        query: str,
        path: str | None = None,
        is_regex: bool = False,
        include_hidden: bool = False,
        max_results: int = 50,
    ) -> dict[str, Any]:
        ...

    def list_db_tables(self, db_path: str | None = None) -> dict[str, Any]:
        ...

    def query_db(self, sql: str, db_path: str | None = None, limit: int = 200) -> dict[str, Any]:
        ...

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        ...

    def write_markdown(self, path: str, content: str) -> dict[str, Any]:
        ...

    def run_python(self, code: str | None = None, path: str | None = None) -> dict[str, Any]:
        ...


@dataclass
class AgentDeps:
    tools: ToolRuntime


def _model_name() -> str:
    return os.getenv("AGENT_MODEL", "openai:gpt-4o-mini")


def build_agent(
    instructions: str | None = None,
    extra_instructions: str | None = None,
) -> Agent[AgentDeps, str]:
    system_instructions = instructions or (
        "You are an autonomous data analysis agent working inside a project workspace. "
        "Goal: investigate data, compute reliable metrics, and deliver a clear markdown report with artifacts. "
        "\n\n"
        "Workflow: "
        "(1) discover files and tables, "
        "(2) inspect schemas and read raw files, "
        "(3) compute metrics with queries/scripts, "
        "(4) create plots if needed, "
        "(5) write the report and reference artifacts. "
        "\n\n"
        "Tools: list_dir, read_file, search_text, list_db_tables, query_db, write_file, write_markdown, run_python. "
        "Use list_dir to explore, list_db_tables + PRAGMA table_info before SQL, and search_text only when needed. "
        "\n\n"
        "Rules: "
        "Use project-relative paths only (e.g., open('data/raw/events.csv')). "
        "Python runs from project root (PROJECT_ROOT is set). "
        "CSV/JSON are not DB tables; load them with run_python/read_file. "
        "Never assume tables/columns—inspect schema first. "
        "Use COUNT(*) for row counts; do not invent columns. "
        "Qualify columns in SQL joins to avoid ambiguity. "
        "For time-series joins, use ts + machine_id. "
        "Avoid /tmp; write intermediates under artifacts/agent/. "
        "Do not pass .csv/.json/.db paths to run_python; use inline code. "
        "Do not reference non-existent script paths. "
        "Scripts must load workspace data; no hard-coded arrays or fabricated metrics. "
        "Never synthesize datasets in code (no inlined tables or 'simulated' values). "
        "If a value is reported, it must be derived from workspace files or database queries in the same run. "
        "Persist computed metrics to artifacts/agent/results.json. "
        "Write reports with write_markdown using results_path and {{key}} placeholders. "
        "Do not use write_file for reports. "
        "No placeholders in final report; all key=value lines must match computed outputs. "
        "If a tool fails, fix the query/code and retry before writing the report. "
        "Always run scripts you write and include key results in the final response."
    )
    if extra_instructions:
        system_instructions = (
            f"{system_instructions}\n\nSkills:\n{extra_instructions}"
        )
    agent: Agent[AgentDeps, str] = Agent(
        _model_name(),
        deps_type=AgentDeps,
        instructions=system_instructions,
    )

    @agent.tool
    def list_dir(
        ctx: RunContext[AgentDeps],
        path: str,
        recursive: bool = False,
        include_hidden: bool = False,
        max_entries: int = 200,
    ) -> dict[str, Any]:
        return ctx.deps.tools.list_dir(
            path=path,
            recursive=recursive,
            include_hidden=include_hidden,
            max_entries=max_entries,
        )

    @agent.tool
    def read_file(
        ctx: RunContext[AgentDeps],
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_lines: int = 200,
    ) -> dict[str, Any]:
        return ctx.deps.tools.read_file(
            path=path,
            start_line=start_line,
            end_line=end_line,
            max_lines=max_lines,
        )

    @agent.tool
    def search_text(
        ctx: RunContext[AgentDeps],
        query: str,
        path: str | None = None,
        is_regex: bool = False,
        include_hidden: bool = False,
        max_results: int = 50,
    ) -> dict[str, Any]:
        return ctx.deps.tools.search_text(
            query=query,
            path=path,
            is_regex=is_regex,
            include_hidden=include_hidden,
            max_results=max_results,
        )

    @agent.tool
    def list_db_tables(ctx: RunContext[AgentDeps], db_path: str | None = None) -> dict[str, Any]:
        return ctx.deps.tools.list_db_tables(db_path=db_path)

    @agent.tool
    def query_db(
        ctx: RunContext[AgentDeps],
        sql: str,
        db_path: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        return ctx.deps.tools.query_db(sql=sql, db_path=db_path, limit=limit)

    @agent.tool
    def write_file(ctx: RunContext[AgentDeps], path: str, content: str) -> dict[str, Any]:
        return ctx.deps.tools.write_file(path=path, content=content)

    @agent.tool
    def write_markdown(ctx: RunContext[AgentDeps], path: str, content: str) -> dict[str, Any]:
        return ctx.deps.tools.write_markdown(path=path, content=content)

    @agent.tool
    def run_python(
        ctx: RunContext[AgentDeps],
        code: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        return ctx.deps.tools.run_python(code=code, path=path)

    return agent


