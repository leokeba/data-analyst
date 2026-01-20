from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError


class LLMError(RuntimeError):
    pass


class PlanStepPayload(BaseModel):
    title: str
    description: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = True


class PlanPayload(BaseModel):
    objective: str
    steps: list[PlanStepPayload]


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMError("OPENAI_API_KEY is not set")
    base_url = os.getenv("OPENAI_BASE_URL")
    return OpenAI(api_key=api_key, base_url=base_url)


def _model_name() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def generate_plan(
    prompt: str,
    tool_catalog: list[dict[str, Any]],
    dataset_id: str | None,
    safe_mode: bool,
    context: dict[str, Any] | None = None,
    max_steps: int = 8,
) -> PlanPayload:
    system = (
        "You are the Planner. Produce a JSON plan with objective and steps. "
        "Use only tools from the catalog. Keep steps minimal and deterministic. "
        "If context is provided, incorporate tool outputs and failures to refine the next plan. "
        "Return JSON only with keys: objective, steps[].title, steps[].description, "
        "steps[].tool, steps[].args, steps[].requires_approval."
    )
    user_payload = {
        "prompt": prompt,
        "dataset_id": dataset_id,
        "safe_mode": safe_mode,
        "max_steps": max_steps,
        "tools": tool_catalog,
        "context": context or {},
    }
    client = _client()
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "agent_plan",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "objective": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "tool": {"type": ["string", "null"]},
                                "args": {"type": "object"},
                                "requires_approval": {"type": "boolean"},
                            },
                            "required": [
                                "title",
                                "description",
                                "tool",
                                "args",
                                "requires_approval",
                            ],
                        },
                    },
                },
                "required": ["objective", "steps"],
            },
        },
    }
    try:
        response = client.responses.create(
            model=_model_name(),
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            response_format=response_format,
        )
    except Exception:
        response = client.responses.create(
            model=_model_name(),
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
        )
    content = getattr(response, "output_text", None) or ""
    if not content:
        output = getattr(response, "output", []) or []
        for item in output:
            if getattr(item, "type", None) != "message":
                continue
            message_content = getattr(item, "content", []) or []
            for part in message_content:
                if getattr(part, "type", None) == "output_text":
                    content = getattr(part, "text", "") or ""
                    if content:
                        break
            if content:
                break
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        payload = _extract_json_payload(content)
        if payload is None:
            preview = content.replace("\n", " ").strip()
            if len(preview) > 500:
                preview = preview[:500] + "..."
            raise LLMError(
                f"Invalid JSON from LLM: {exc}. Raw content preview: {preview}"
            ) from exc
    try:
        return PlanPayload(**payload)
    except ValidationError as exc:
        preview = content.replace("\n", " ").strip()
        if len(preview) > 500:
            preview = preview[:500] + "..."
        raise LLMError(
            f"Invalid plan payload from LLM: {exc}. Raw content preview: {preview}"
        ) from exc


def _extract_json_payload(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`")
        text = re.sub(r"^json\s*", "", text)
        text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
