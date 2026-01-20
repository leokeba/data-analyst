from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .models import ToolResult
from .policy import AgentPolicy, validate_path

ToolHandler = Callable[[dict[str, Any]], ToolResult]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: ToolHandler
    destructive: bool = False


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
        if "path" in args:
            validate_path(args["path"], self._policy)
        return tool.handler(args)
