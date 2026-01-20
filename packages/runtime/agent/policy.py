from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class AgentPolicy(BaseModel):
    allowed_paths: list[str] = Field(default_factory=list)
    max_data_bytes: int = 50_000_000
    allow_network: bool = False
    require_approval_for_destructive: bool = True


def repo_root(start: Path | None = None) -> Path:
    current = start or Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "projects").exists():
            return parent
    return Path.cwd()


def validate_path(path: str | Path, policy: AgentPolicy) -> Path:
    resolved = Path(path).expanduser().resolve()
    root = repo_root(resolved)
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path not allowed outside repo: {resolved}")
    if policy.allowed_paths:
        allowed = [Path(p).expanduser().resolve() for p in policy.allowed_paths]
        if not any(resolved.is_relative_to(p) for p in allowed):
            raise ValueError(f"Path not in allowlist: {resolved}")
    return resolved
