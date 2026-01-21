from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from .models import ToolResult
from .policy import AgentPolicy, validate_path

ToolHandler = Callable[[dict[str, Any]], ToolResult]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: ToolHandler
    destructive: bool = False
    args_model: type[BaseModel] | None = None


class ToolRouter:
    def __init__(self, policy: AgentPolicy) -> None:
        self._policy = policy
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def call(self, name: str, args: dict[str, Any], approved: bool = False) -> ToolResult:
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        tool = self._tools[name]
        if tool.destructive and self._policy.require_approval_for_destructive and not approved:
            raise PermissionError(f"Approval required for tool: {name}")
        if tool.args_model:
            parsed = tool.args_model(**(args or {}))
            args = parsed.model_dump(mode="json", exclude_none=True)
        for key, value in args.items():
            if value is None:
                continue
            if key == "path" or key.endswith("_path"):
                try:
                    if isinstance(value, str) and not Path(value).is_absolute():
                        continue
                except Exception:
                    pass
                validate_path(value, self._policy)
        return tool.handler(args)
