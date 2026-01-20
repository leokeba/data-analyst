from __future__ import annotations

from typing import Any

from .journal import ActionJournal
from .models import Approval, Plan, PlanStep, StepStatus, ToolResult
from .router import ToolRouter
from .snapshot import SnapshotStore


class AgentRuntime:
    def __init__(
        self,
        tool_router: ToolRouter,
        journal: ActionJournal,
        snapshot_store: SnapshotStore,
        step_budget: int = 50,
    ) -> None:
        self._tools = tool_router
        self._journal = journal
        self._snapshots = snapshot_store
        self._step_budget = step_budget

    def run_plan(self, plan: Plan, approvals: dict[str, Approval] | None = None) -> list[dict[str, Any]]:
        approvals = approvals or {}
        step_count = 0
        for step in plan.steps:
            if step_count >= self._step_budget:
                break
            approval = approvals.get(step.id)
            self.run_step(plan, step, approval)
            step_count += 1
        return self._journal.to_log()

    def run_step(self, plan: Plan, step: PlanStep, approval: Approval | None = None) -> ToolResult | None:
        record = self._journal.start(plan.id, step)
        if step.requires_approval and approval is None:
            step.status = StepStatus.PENDING
            return None
        if approval:
            self._journal.approve(record, approval)
        if not step.tool:
            step.status = StepStatus.SKIPPED
            return None
        try:
            approved = approval is not None or not step.requires_approval
            result = self._tools.call(step.tool, step.args, approved=approved)
            self._journal.apply(record, result)
            step.status = StepStatus.APPLIED
            return result
        except Exception as exc:  # pragma: no cover - safety net
            self._journal.fail(record, str(exc))
            step.status = StepStatus.FAILED
            return None
