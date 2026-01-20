from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .models import SnapshotRef
from .policy import AgentPolicy, validate_path


class SnapshotStore(BaseModel):
    policy: AgentPolicy
    snapshots: list[SnapshotRef] = Field(default_factory=list)

    def create_snapshot(self, kind: str, target_path: str, metadata: dict[str, Any] | None = None) -> SnapshotRef:
        validate_path(target_path, self.policy)
        snapshot = SnapshotRef(kind=kind, target_path=target_path, metadata=metadata or {})
        self.snapshots.append(snapshot)
        return snapshot

    def restore_snapshot(self, snapshot_id: str) -> SnapshotRef | None:
        for snap in self.snapshots:
            if snap.id == snapshot_id:
                return snap
        return None
