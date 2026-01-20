from __future__ import annotations

import json
import os
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
    max_steps: int = 8,
) -> PlanPayload:
    system = (
        "You are the Planner. Produce a JSON plan with objective and steps. "
        "Use only tools from the catalog. Keep steps minimal and deterministic. "
        "Return JSON only with keys: objective, steps[].title, steps[].description, "
        "steps[].tool, steps[].args, steps[].requires_approval."
    )
    user_payload = {
        "prompt": prompt,
        "dataset_id": dataset_id,
        "safe_mode": safe_mode,
        "max_steps": max_steps,
        "tools": tool_catalog,
    }
    client = _client()
    response = client.chat.completions.create(
        model=_model_name(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
        temperature=0.2,
    )
    content = response.choices[0].message.content or ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Invalid JSON from LLM: {exc}") from exc
    try:
        return PlanPayload(**payload)
    except ValidationError as exc:
        raise LLMError(f"Invalid plan payload from LLM: {exc}") from exc
