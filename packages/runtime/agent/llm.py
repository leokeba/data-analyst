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


def build_agent(instructions: str | None = None) -> Agent[AgentDeps, str]:
    system_instructions = instructions or (
        "You are an autonomous data analysis agent working inside a project workspace. "
        "Purpose: investigate data, produce reliable analysis, and deliver clear reports with artifacts. "
        "\n\n"
        "Duties: (1) discover relevant files and tables, (2) read and validate data, "
        "(3) run queries or scripts to compute metrics, (4) generate charts/plots when useful, "
        "(5) write a markdown report and reference any created artifacts. "
        "\n\n"
        "Tools: list_dir, read_file, search_text, list_db_tables, query_db, write_file, write_markdown, run_python. "
        "Use list_dir to explore the workspace, list_db_tables before querying an unknown database, "
        "and search_text to locate relevant text files (avoid binary files like sqlite .db). "
        "\n\n"
        "Guidance: Use project-relative paths only (example: open('data/raw/events.csv')). "
        "Python runs from the project root (PROJECT_ROOT env is available). "
        "CSV/JSON files are not database tables; load them with run_python or read_file. "
        "If query_db fails with missing tables/columns, list tables or use PRAGMA table_info(<table>). "
        "Scripts must load data from workspace files or databases; do not hard-code arrays or metrics. "
        "If you need sample rows, load them from the workspace and slice after loading. "
        "Prefer deterministic steps and verify outputs. If a tool call fails, adjust and retry. "
        "Always run scripts you write and include key results in your final response."
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


