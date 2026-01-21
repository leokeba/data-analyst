from __future__ import annotations

import json
import logging
import os
from typing import Any, TypeVar, Union

import httpx

from pydantic import BaseModel, Field, model_validator
from seeds_clients import Message, OpenAIClient
from seeds_clients.core.exceptions import ValidationError as SeedsValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class NoArgs(BaseModel):
    pass


class ListDirArgs(BaseModel):
    path: str
    recursive: bool = False
    include_hidden: bool = False
    max_entries: int = 200


class ReadFileArgs(BaseModel):
    path: str
    start_line: int = 1
    end_line: int | None = None
    max_lines: int = 200
    max_bytes: int | None = None


class SearchTextArgs(BaseModel):
    query: str
    path: str | None = None
    is_regex: bool = False
    include_hidden: bool = False
    max_results: int = 50


class CreateRunArgs(BaseModel):
    dataset_id: str
    type: str


class PreviewDatasetArgs(BaseModel):
    dataset_id: str


class ListArtifactsArgs(BaseModel):
    run_id: str | None = None
    limit: int = 100
    offset: int = 0


class ListDbTablesArgs(BaseModel):
    db_path: str | None = None


class QueryDbArgs(BaseModel):
    sql: str
    db_path: str | None = None
    limit: int = 200


class WriteFileArgs(BaseModel):
    path: str
    content: str


class AppendFileArgs(BaseModel):
    path: str
    content: str


class WriteMarkdownArgs(BaseModel):
    path: str
    content: str


class RunPythonArgs(BaseModel):
    code: str | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _require_code_or_path(self) -> "RunPythonArgs":
        if not (self.code and self.code.strip()) and not (self.path and self.path.strip()):
            raise ValueError("run_python requires code or path")
        return self


class RunShellArgs(BaseModel):
    command: str
    cwd: str | None = None
    timeout: int | None = None
    dry_run: bool = False


class CreateSnapshotArgs(BaseModel):
    kind: str
    path: str
    run_id: str | None = None
    metadata: dict[str, Any] | None = None


class RequestRollbackArgs(BaseModel):
    run_id: str | None = None
    snapshot_id: str | None = None
    note: str | None = None


ToolArgs = Union[
    NoArgs,
    ListDirArgs,
    ReadFileArgs,
    SearchTextArgs,
    CreateRunArgs,
    PreviewDatasetArgs,
    ListArtifactsArgs,
    ListDbTablesArgs,
    QueryDbArgs,
    WriteFileArgs,
    AppendFileArgs,
    WriteMarkdownArgs,
    RunPythonArgs,
    RunShellArgs,
    CreateSnapshotArgs,
    RequestRollbackArgs,
]


TOOL_ARGS_MODELS: dict[str, type[BaseModel]] = {
    "list_dir": ListDirArgs,
    "read_file": ReadFileArgs,
    "search_text": SearchTextArgs,
    "create_run": CreateRunArgs,
    "preview_dataset": PreviewDatasetArgs,
    "list_datasets": NoArgs,
    "list_project_runs": NoArgs,
    "list_artifacts": ListArtifactsArgs,
    "list_project_sqlite": NoArgs,
    "list_db_tables": ListDbTablesArgs,
    "query_db": QueryDbArgs,
    "write_file": WriteFileArgs,
    "append_file": AppendFileArgs,
    "write_markdown": WriteMarkdownArgs,
    "run_python": RunPythonArgs,
    "run_shell": RunShellArgs,
    "create_snapshot": CreateSnapshotArgs,
    "request_rollback": RequestRollbackArgs,
}


class PlanStepPayload(BaseModel):
    title: str
    description: str
    tool: str | None = None
    args: ToolArgs = Field(default_factory=NoArgs)
    requires_approval: bool = True

    @model_validator(mode="after")
    def _validate_args(self) -> "PlanStepPayload":
        if not self.tool:
            return self
        model_cls = TOOL_ARGS_MODELS.get(self.tool)
        if not model_cls:
            raise ValueError(f"Unknown tool: {self.tool}")
        if isinstance(self.args, model_cls):
            return self
        if isinstance(self.args, BaseModel):
            parsed = model_cls(**self.args.model_dump(mode="json"))
        else:
            parsed = model_cls(**(self.args or {}))
        self.args = parsed
        return self


class PlanPayload(BaseModel):
    objective: str
    steps: list[PlanStepPayload]


class NextActionPayload(BaseModel):
    objective: str | None = None
    finish: bool = False
    reasoning: str | None = None
    step: PlanStepPayload | None = None


class ReflectionPayload(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    critique: str
    next_action_needed: bool = False


def _validate_step_payload(step: PlanStepPayload) -> None:
    if not step.tool:
        raise LLMError("Step tool is required")
    args = step.args.model_dump(mode="json", exclude_none=True) if isinstance(step.args, BaseModel) else step.args or {}
    if step.tool == "list_dir":
        path_value = args.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise LLMError("list_dir requires args.path (use '.' for root)")
    if step.tool == "read_file":
        path_value = args.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise LLMError("read_file requires args.path")
    if step.tool == "run_python":
        code_value = args.get("code")
        path_value = args.get("path")
        has_code = isinstance(code_value, str) and code_value.strip()
        has_path = isinstance(path_value, str) and path_value.strip()
        if not has_code and not has_path:
            raise LLMError("run_python requires args.code or args.path")


def _client_timeout() -> float:
    raw = os.getenv("LLM_TIMEOUT_SECONDS") or os.getenv("OPENAI_TIMEOUT_SECONDS")
    if not raw:
        return 120.0
    try:
        return float(raw)
    except ValueError:
        return 120.0


def _client() -> OpenAIClient:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMError("OPENAI_API_KEY is not set")

    base_url = os.getenv("OPENAI_BASE_URL")
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": _model_name(),
        "enable_tracking": False,
    }
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAIClient(**client_kwargs)
    timeout_seconds = _client_timeout()
    client._http_client = httpx.Client(
        base_url=client.base_url,
        headers={
            "Authorization": f"Bearer {client.api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout_seconds,
    )
    return client


def _model_name() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


logger = logging.getLogger(__name__)


def _llm_debug_enabled() -> bool:
    value = os.getenv("LLM_DEBUG", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _configure_llm_logger() -> None:
    if not _llm_debug_enabled():
        return
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


def _log_payload_debug(stage: str, content: str) -> None:
    """Emit a debug log with truncated model content for troubleshooting."""
    if not _llm_debug_enabled():
        return
    _configure_llm_logger()
    preview = content.replace("\n", " ").strip()
    truncated = preview if len(preview) <= 800 else preview[:800] + "..."
    logger.debug("llm.parse.%s", stage, extra={"preview": truncated, "length": len(content)})


def _log_request_debug(system: str, user_payload: dict[str, Any]) -> None:
    if not _llm_debug_enabled():
        return
    _configure_llm_logger()
    payload = json.dumps(
        {"system": system, "payload": user_payload},
        ensure_ascii=False,
    )
    logger.debug("llm.request %s", payload)


def _log_response_debug(parsed: BaseModel | None) -> None:
    if not _llm_debug_enabled():
        return
    _configure_llm_logger()
    if parsed is None:
        logger.debug("llm.response null")
        return
    payload = json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False)
    logger.debug("llm.response %s", payload)


def _build_messages(system: str, user_payload: dict[str, Any]) -> list[Message]:
    return [
        Message(role="system", content=system),
        Message(role="user", content=json.dumps(user_payload)),
    ]


def _request_structured_response(
    system: str,
    user_payload: dict[str, Any],
    response_model: type[T],
) -> T:
    messages = _build_messages(system, user_payload)
    _log_request_debug(system, user_payload)

    try:
        with _client() as client:
            response = client.generate(
                messages=messages,
                response_format=response_model,
            )
    except SeedsValidationError as exc:
        raise LLMError(f"Invalid structured output from LLM: {exc}") from exc
    except Exception as exc:  # pragma: no cover - network issues and provider errors
        raise LLMError(f"LLM request failed: {exc}") from exc

    _log_payload_debug("raw_content", response.content)

    if response.parsed is None:
        preview = response.content.replace("\n", " ").strip()
        if len(preview) > 500:
            preview = preview[:500] + "..."
        raise LLMError(
            f"LLM did not return structured output. Raw content preview: {preview}"
        )

    _log_response_debug(response.parsed)

    return response.parsed


def generate_plan(
    prompt: str,
    tool_catalog: list[dict[str, Any]],
    dataset_id: str | None,
    safe_mode: bool,
    context: dict[str, Any] | None = None,
    max_steps: int = 8,
) -> PlanPayload:
    base_system = (
        "You are the Planner. Produce a JSON plan with objective and steps. "
        "Treat the user's prompt as the single source of truth; do not invent or substitute unrelated objectives. "
        "If the prompt specifies exact tools, paths, outputs, or content, follow those requirements literally. "
        "Use only tools from the catalog. Keep steps minimal and deterministic. "
        "If context is provided, incorporate tool outputs and failures to refine the next plan. "
        "Use project-relative paths only (root is '.'); avoid absolute paths. "
        "If the prompt specifies an exact path, use it verbatim. "
        "After listing '.', if you see data/, list data/raw next. "
        "For list_dir you MUST include args.path (use '.' for root). "
        "Example list_dir step: tool='list_dir', args={'path': 'data/raw'}. "
        "Example read_file step: tool='read_file', args={'path': 'data/raw/sales.csv', 'start_line': 1, 'end_line': 50}. "
        "Example run_python step: tool='run_python', args={'code': 'print(\"hello\")'}. "
        "When analyzing data, do not assume schemas or types. "
        "Prefer listing datasets and previewing or reading sample rows before writing analysis code. "
        "Handle non-numeric fields defensively and avoid casting without checks. "
        "For read_file, you MUST include args.path. "
        "For run_python, you MUST include args.code or args.path. "
        "When writing run_python code, avoid Markdown code fences (```), and avoid multi-line string literals; "
        "use plain text lines and explicit \\n sequences for newlines. "
        "Avoid repeating a tool call that already succeeded unless explicitly required. "
        "If the prompt requires an exact number of steps, produce exactly that count and no extras. "
        "When asked to produce a report, ensure run_python prints the full report to stdout. "
        "Every required section header (including # Real Autonomy Report) must be followed by at least two sentences before the next header. "
        "In the Appendix Verification block, use the exact key=value pairs provided in the prompt; do not recompute or alter them. "
        "Return JSON only with keys: objective, steps[].title, steps[].description, "
        "steps[].tool, steps[].args, steps[].requires_approval."
    )
    user_payload: dict[str, Any] = {
        "prompt": prompt,
        "dataset_id": dataset_id,
        "safe_mode": safe_mode,
        "max_steps": max_steps,
        "tools": tool_catalog,
        "context": context or {},
    }
    last_error: str | None = None
    for attempt in range(2):
        system = base_system
        if last_error:
            system = (
                base_system
                + " Previous plan was invalid: "
                + last_error
                + ". You MUST correct the plan by supplying required args; do not repeat the same plan."
            )
        plan = _request_structured_response(system, user_payload, PlanPayload)
        try:
            for step in plan.steps:
                _validate_step_payload(step)
            return plan
        except LLMError as exc:
            last_error = str(exc)
            user_payload = {
                **user_payload,
                "context": {
                    **(context or {}),
                    "validation_error": last_error,
                    "previous_plan": plan.model_dump(mode="json"),
                },
            }
            continue
    raise LLMError(last_error or "Planner returned invalid plan")


def generate_next_action(
    prompt: str,
    tool_catalog: list[dict[str, Any]],
    dataset_id: str | None,
    safe_mode: bool,
    context: dict[str, Any] | None = None,
) -> NextActionPayload:
    system = (
        "You are the Planner producing the next SINGLE action. "
        "Return exactly one step or set finish=true when the goal is achieved. "
        "Use only tools from the catalog. Keep actions small and observable. "
        "Incorporate context observations and failures before picking the next action. "
        "Use project-relative paths only (root is '.'); avoid absolute paths. "
        "If the prompt specifies an exact path, use it verbatim. "
        "After listing '.', if you see data/, list data/raw next. "
        "For list_dir you MUST include args.path (use '.' for root). "
        "Example list_dir step: tool='list_dir', args={'path': 'data/raw'}. "
        "Prefer inspecting data (list/read/preview) before heavy analysis; avoid assumptions. "
        "Do not batch multiple tools into one step; each call chooses one tool. "
        "For read_file, you MUST include args.path. "
        "For run_python, you MUST include args.code or args.path. "
        "When writing run_python code, avoid Markdown code fences (```), and avoid multi-line string literals; "
        "use plain text lines and explicit \\n sequences for newlines. "
        "Every required section header (including # Real Autonomy Report) must be followed by at least two sentences before the next header. "
        "In the Appendix Verification block, use the exact key=value pairs provided in the prompt; do not recompute or alter them. "
        "When ready to produce the final report, choose the write_markdown tool with full content. "
        "Avoid repeating a tool call that already succeeded unless explicitly required. "
        "If the prompt requires an exact number of steps, do not add more steps; instead finish when satisfied. "
        "When asked to produce a report, ensure run_python prints the full report to stdout. "
        "Respond with JSON ONLY that matches the provided schema. "
        "If finish is false, you MUST provide step with a valid tool and args; do not return null step when finish=false."
    )
    user_payload = {
        "prompt": prompt,
        "dataset_id": dataset_id,
        "safe_mode": safe_mode,
        "tools": tool_catalog,
        "context": context or {},
    }
    next_action = _request_structured_response(system, user_payload, NextActionPayload)

    if not next_action.finish and not next_action.step:
        preview = json.dumps(user_payload, ensure_ascii=False)
        raise LLMError(
            "Planner returned finish=false without a step. "
            f"Payload context: {preview}"
        )

    if not next_action.finish and next_action.step:
        _validate_step_payload(next_action.step)

    return next_action


def generate_reflection(
    prompt: str,
    tool_catalog: list[dict[str, Any]],
    dataset_id: str | None,
    safe_mode: bool,
    context: dict[str, Any] | None = None,
) -> ReflectionPayload:
    system = (
        "You are the Evaluator. Review the latest step execution and overall progress. "
        "Return JSON with score (0-1), passed (bool), critique (string), and "
        "next_action_needed (bool). "
        "Be strict and concise; if the objective is not yet met, set next_action_needed=true. "
        "Use only the provided context; do not assume unseen files or outputs. "
        "Respond with JSON ONLY that matches the provided schema."
    )
    user_payload = {
        "prompt": prompt,
        "dataset_id": dataset_id,
        "safe_mode": safe_mode,
        "tools": tool_catalog,
        "context": context or {},
    }
    return _request_structured_response(system, user_payload, ReflectionPayload)


