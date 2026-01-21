#!/usr/bin/env python3
"""E2E autonomy evaluation for agent chat.

This script checks that the agent can:
- Navigate the workspace (list_dir)
- Write + read files (write_file, read_file)
- Write + execute a Python script (run_python)

Usage:
  uv run python scripts/e2e_agent_autonomy_eval.py

Optional environment variables:
    API_BASE=http://127.0.0.1:8000
    AGENT_EVAL_TIMEOUT=240
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class EvalStep:
    name: str
    ok: bool
    details: str


def _log_section(title: str) -> None:
    print("\n" + title)
    print("-" * len(title))


def _log_json(label: str, payload: object) -> None:
    print(f"\n{label}:")
    print(json.dumps(payload, indent=2, default=str))


def _request(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        payload = exc.read() or b""
        return exc.code, dict(exc.headers), payload
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc


def _request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    status, _, data = _request(method, url, body=body, headers=headers, timeout=timeout)
    try:
        parsed = json.loads(data.decode("utf-8")) if data else {}
    except json.JSONDecodeError:
        parsed = {"raw": data.decode("utf-8", errors="replace")}
    if status >= 400:
        raise RuntimeError(f"{method} {url} failed ({status}): {parsed}")
    if not isinstance(parsed, dict):
        return {"data": parsed}
    return parsed


def _health_check(api_base: str, timeout: int) -> None:
    status, _, data = _request("GET", f"{api_base}/health", timeout=timeout)
    if status != 200:
        raise RuntimeError(
            f"Health check failed ({status}): {data.decode('utf-8', errors='replace')}"
        )


def _create_project(api_base: str, timeout: int) -> dict[str, Any]:
    name = f"e2e-autonomy-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name}, timeout=timeout)


def _send_agent_chat(
    api_base: str,
    project_id: str,
    content: str,
    safe_mode: bool = False,
    auto_run: bool = True,
    timeout: int = 30,
) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": safe_mode, "auto_run": auto_run},
        timeout=timeout,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run autonomy evaluation against the agent API.")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("AGENT_EVAL_TIMEOUT", "240")),
    )
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    steps: list[EvalStep] = []

    try:
        _health_check(api_base, args.timeout)
        steps.append(EvalStep("health_check", True, "API reachable"))
        _log_section("API")
        print(f"API base: {api_base}")

        project = _create_project(api_base, args.timeout)
        project_id = project["id"]
        workspace_path = project["workspace_path"]
        steps.append(EvalStep("create_project", True, f"project_id={project_id}"))
        _log_section("Project")
        _log_json("Project", project)

        script_path = "scripts/agent/autonomy_check.py"
        note_path = "notes/agent-autonomy.txt"
        chat_prompt = (
            "You are operating inside a project workspace. "
            "Return a plan that uses ONLY these tools: list_dir, write_file, read_file, run_python. "
            "Steps: (1) use list_dir with path set exactly to '.' to confirm structure; "
            f"(2) use write_file to create a note at {note_path} with content 'autonomy ok'; "
            f"(3) use read_file to read back {note_path}; "
            f"(4) use write_file to create a Python script at {script_path} that prints 'autonomy ok' "
            "and the note content; (5) use run_python to execute that script."
        )
        _log_section("Chat prompt")
        print(chat_prompt)

        chat_response = _send_agent_chat(
            api_base,
            project_id,
            chat_prompt,
            safe_mode=False,
            auto_run=True,
            timeout=args.timeout,
        )
        _log_section("Chat response")
        _log_json("Chat response", chat_response)
        run = chat_response.get("run")
        if not run:
            raise RuntimeError("Chat response missing run payload")

        tools_used = {entry.get("tool") for entry in (run.get("log") or [])}
        required_tools = {"list_dir", "write_file", "read_file", "run_python"}
        missing_tools = required_tools.difference(tools_used)
        if missing_tools:
            raise RuntimeError(f"Missing tool usage: {sorted(missing_tools)}")

        stdout = ""
        for entry in run.get("log") or []:
            if entry.get("tool") == "run_python":
                output = entry.get("output") or {}
                stdout = str(output.get("stdout") or "")
                break
        if "autonomy ok" not in stdout.lower():
            raise RuntimeError("run_python stdout missing 'autonomy ok'")

        steps.append(EvalStep("autonomy_eval", True, "tools exercised and output validated"))

    except Exception as exc:  # noqa: BLE001 - keep top-level errors concise
        steps.append(EvalStep("autonomy_eval", False, str(exc)))

    print("\nAutonomy evaluation results")
    print("==========================")
    for step in steps:
        status = "PASS" if step.ok else "FAIL"
        print(f"- {status}: {step.name} ({step.details})")

    failed = [step for step in steps if not step.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
