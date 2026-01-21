from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .models import ActionRecord, Approval, PlanStep, StepStatus, ToolResult, _now


class ActionJournal(BaseModel):
    records: list[ActionRecord] = Field(default_factory=list)

    def start(self, run_id: str, step: PlanStep) -> ActionRecord:
        record = ActionRecord(
            run_id=run_id,
            step_id=step.id,
            tool=step.tool,
            args=step.args,
            status=StepStatus.PENDING,
        )
        self.records.append(record)
        return record

    def approve(self, record: ActionRecord, approval: Approval) -> ActionRecord:
        record.approvals.append(approval)
        record.status = StepStatus.APPROVED
        return record

    def apply(self, record: ActionRecord, result: ToolResult) -> ActionRecord:
        record.status = StepStatus.APPLIED
        record.finished_at = _now()
        record.output = result.output
        record.artifacts = result.artifacts
        record.diff = result.diff
        return record

    def fail(self, record: ActionRecord, error: str) -> ActionRecord:
        record.status = StepStatus.FAILED
        record.finished_at = _now()
        record.error = error
        return record

    def record_feedback(
        self,
        run_id: str,
        step_id: str,
        tool: str,
        output: dict[str, Any],
    ) -> ActionRecord:
        record = ActionRecord(
            run_id=run_id,
            step_id=step_id,
            tool=tool,
            status=StepStatus.APPLIED,
            output=output,
            finished_at=_now(),
        )
        self.records.append(record)
        return record

    def to_log(self) -> list[dict[str, Any]]:
        return [record.model_dump(mode="json") for record in self.records]
