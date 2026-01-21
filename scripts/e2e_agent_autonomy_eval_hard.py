#!/usr/bin/env python3
"""Harder autonomy evaluation for agent chat.

This script checks that the agent can handle:
- Workspace navigation
- Writing a data generator script
- Running scripts to create datasets
- Reading and appending files
- Writing and executing a more complex analysis script

Usage:
  uv run python scripts/e2e_agent_autonomy_eval_hard.py

Optional environment variables:
  API_BASE=http://127.0.0.1:8000
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
    timeout: int = 120,
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
    timeout: int = 120,
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
    name = f"e2e-autonomy-hard-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name}, timeout=timeout)


def _send_agent_chat(
    api_base: str,
    project_id: str,
    content: str,
    safe_mode: bool = False,
    auto_run: bool = True,
    timeout: int = 120,
) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": safe_mode, "auto_run": auto_run},
        timeout=timeout,
    )


def _extract_stdout(run: dict[str, Any]) -> str:
    stdout = ""
    for entry in run.get("log") or []:
        if entry.get("tool") == "run_python":
            output = entry.get("output") or {}
            stdout = str(output.get("stdout") or "")
    return stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Run hard autonomy evaluation against the agent API.")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("AGENT_EVAL_TIMEOUT", "180")))
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

        generator_path = "scripts/agent/generate_sales.py"
        dataset_path = "data/raw/sales.csv"
        note_path = "metadata/notes.txt"
        analysis_path = "scripts/agent/analyze_sales.py"
        report_path = "artifacts/agent/hard-report.md"

        chat_prompt = (
            "You are operating inside a project workspace. "
            "Return a plan with EXACTLY 7 steps in the order below and no extra steps. "
            "Use ONLY these tools: list_dir, write_file, append_file, read_file, run_python. "
            "Set requires_approval to false for every step. "
            "Use project-relative paths only and do not use absolute paths. "
            "Step 1: use list_dir with path set exactly to '.'. "
            f"Step 2: use write_file to create a Python generator script at {generator_path} that writes "
            f"a deterministic CSV to {dataset_path} with 200 rows and columns: id, amount, category, region, day. "
            "Use random.seed(42). Categories: hardware, software, services. Regions: north, south, east, west. "
            "Amounts should be floats between 10 and 500. Days should be integers 1-30. "
            "Step 3: use run_python to execute the generator script. "
            f"Step 4: use read_file to read the first 6 lines of {dataset_path}. "
            f"Step 5: use append_file to add a line 'autonomy-hard ok' to {note_path}. "
            f"Step 6: use write_file to create an analysis script at {analysis_path} that reads the CSV and writes "
            f"a markdown report to {report_path} AND prints the FULL report to stdout. The report must include sections: "
            "# Hard Autonomy Report, ## Summary, ## Missing values, ## Top categories, ## Region totals, ## Sample rows. "
            "Compute row count, column count, missing values per column, top 3 categories, and total amount per region. "
            "Step 7: use run_python to execute the analysis script."
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
        required_tools = {"list_dir", "write_file", "append_file", "read_file", "run_python"}
        missing_tools = required_tools.difference(tools_used)
        if missing_tools:
            raise RuntimeError(f"Missing tool usage: {sorted(missing_tools)}")

        stdout = _extract_stdout(run)
        if "# Hard Autonomy Report" not in stdout:
            raise RuntimeError("run_python stdout missing report header")
        if "## Region totals" not in stdout:
            raise RuntimeError("run_python stdout missing region totals section")

        steps.append(EvalStep("autonomy_eval_hard", True, "complex toolchain validated"))

    except Exception as exc:  # noqa: BLE001 - keep top-level errors concise
        steps.append(EvalStep("autonomy_eval_hard", False, str(exc)))

    print("\nHard autonomy evaluation results")
    print("===============================")
    for step in steps:
        status = "PASS" if step.ok else "FAIL"
        print(f"- {status}: {step.name} ({step.details})")

    failed = [step for step in steps if not step.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
