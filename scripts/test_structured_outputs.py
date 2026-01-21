"""Quick probe for OpenAI structured outputs via the Responses API.

Run with uv:
  uv run python scripts/test_structured_outputs.py --prompt "Describe one action" --model gpt-4o-mini
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI


def _extract_output_text(response: Any) -> str:
    """Return the best-effort text payload from a Responses API call."""
    content = getattr(response, "output_text", None) or ""
    if content:
        return content
    output = getattr(response, "output", []) or []
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        message_content = getattr(item, "content", []) or []
        for part in message_content:
            if getattr(part, "type", None) == "output_text":
                text = getattr(part, "text", "") or ""
                if text:
                    return text
    return ""


def _build_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "finish": {"type": "boolean"},
            "reasoning": {"type": "string"},
            "step": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "tool": {"type": "string"},
                    "args": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                        "required": [],
                    },
                },
                "required": ["title", "tool", "args"],
            },
        },
        "required": ["finish", "reasoning", "step"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe OpenAI structured outputs")
    parser.add_argument(
        "--prompt",
        default="List one safe action to inspect the dataset directory.",
        help="User prompt to send",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        help="Model name (must support Responses text format json_schema)",
    )
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.stderr.write("OPENAI_API_KEY is required\n")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"))

    system = (
        "You MUST respond with JSON that conforms exactly to the provided schema. "
        "Do not add prose. If finish is false, include a step with title, tool, and args."
    )

    response = client.responses.create(
        model=args.model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": args.prompt},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "next_action_probe",
                "schema": _build_schema(),
                "strict": True,
            }
        },
    )

    text = _extract_output_text(response)
    print("=== Raw output ===")
    print(text)

    if not text:
        sys.stderr.write("No text returned from response.\n")
        sys.exit(2)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"JSON decode error: {exc}\n")
        sys.exit(3)

    print("\n=== Parsed JSON ===")
    print(json.dumps(parsed, indent=2))


if __name__ == "__main__":
    main()
