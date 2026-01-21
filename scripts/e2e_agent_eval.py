#!/usr/bin/env python3
"""E2E evaluation script for the agent API.

This script exercises the API directly by:
1) Creating a project
2) Uploading a sample CSV dataset
3) Instructing the agent to write + run a Python analysis script
4) Capturing the markdown report from stdout
5) Writing the markdown report via the agent tool

Usage:
  uv run python scripts/e2e_agent_eval.py

Optional environment variables:
  API_BASE=http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


DEFAULT_TIMEOUT = int(os.environ.get("AGENT_EVAL_TIMEOUT", "240"))


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


def _log_artifact_contents(artifact: dict[str, Any]) -> None:
    path = str(artifact.get("path") or "")
    if not path:
        return
    print(f"\nArtifact contents ({path}):")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            print(handle.read())
    except Exception as exc:  # noqa: BLE001 - best-effort logging
        print(f"(Unable to read artifact: {exc})")


def _request(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
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
    timeout: int = DEFAULT_TIMEOUT,
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


def _encode_multipart(field_name: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----e2e-agent-boundary-{int(time.time() * 1000)}"
    lines: list[bytes] = []
    lines.append(f"--{boundary}".encode())
    lines.append(
        (
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'
        ).encode()
    )
    lines.append(b"Content-Type: text/csv")
    lines.append(b"")
    lines.append(content)
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = b"\r\n".join(lines)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def _health_check(api_base: str) -> None:
    status, _, data = _request("GET", f"{api_base}/health", timeout=DEFAULT_TIMEOUT)
    if status != 200:
        raise RuntimeError(f"Health check failed ({status}): {data.decode('utf-8', errors='replace')}")


def _create_project(api_base: str) -> dict[str, Any]:
    name = f"e2e-eval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name}, timeout=DEFAULT_TIMEOUT)


def _upload_dataset(api_base: str, project_id: str, csv_bytes: bytes) -> dict[str, Any]:
    body, content_type = _encode_multipart("file", "e2e-data.csv", csv_bytes)
    headers = {"Content-Type": content_type}
    status, _, data = _request(
        "POST",
        f"{api_base}/projects/{project_id}/datasets/upload",
        body=body,
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
    )
    parsed = json.loads(data.decode("utf-8")) if data else {}
    if status >= 400:
        raise RuntimeError(f"Upload failed ({status}): {parsed}")
    return parsed


def _create_agent_run(
    api_base: str,
    project_id: str,
    plan: dict[str, Any],
    approvals: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/runs",
        {"plan": plan, "approvals": approvals or {}},
        timeout=DEFAULT_TIMEOUT,
    )


def _send_agent_chat(
    api_base: str,
    project_id: str,
    content: str,
    dataset_id: str | None,
    safe_mode: bool = True,
    auto_run: bool = True,
) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {
            "content": content,
            "dataset_id": dataset_id,
            "safe_mode": safe_mode,
            "auto_run": auto_run,
        },
        timeout=DEFAULT_TIMEOUT,
    )


def _send_chat_with_retry(
    api_base: str,
    project_id: str,
    prompts: list[str],
    dataset_id: str | None,
    safe_mode: bool = True,
    auto_run: bool = True,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for prompt in prompts:
        try:
            return _send_agent_chat(
                api_base,
                project_id,
                prompt,
                dataset_id=dataset_id,
                safe_mode=safe_mode,
                auto_run=auto_run,
            )
        except Exception as exc:  # noqa: BLE001 - retry on any chat error
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("Chat request failed")


def _apply_agent_step(
    api_base: str, project_id: str, run_id: str, step_id: str, approved_by: str
) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/runs/{run_id}/steps/{step_id}/apply",
        {"approved_by": approved_by},
        timeout=DEFAULT_TIMEOUT,
    )


def _list_agent_artifacts(api_base: str, project_id: str) -> list[dict[str, Any]]:
    status, _, data = _request(
        "GET",
        f"{api_base}/projects/{project_id}/agent/artifacts",
        timeout=DEFAULT_TIMEOUT,
    )
    parsed = json.loads(data.decode("utf-8")) if data else []
    if status >= 400:
        raise RuntimeError(f"Artifact list failed ({status}): {parsed}")
    if isinstance(parsed, list):
        return parsed
    return parsed.get("data", [])


def _delete_project(api_base: str, project_id: str) -> None:
    status, _, data = _request(
        "DELETE",
        f"{api_base}/projects/{project_id}",
        timeout=DEFAULT_TIMEOUT,
    )
    if status not in (200, 204):
        raise RuntimeError(
            f"Project delete failed ({status}): {data.decode('utf-8', errors='replace')}"
        )


def _build_dataset() -> bytes:
    output = io.StringIO()
    output.write("id,amount,category,region\n")
    rows = [
        (1, 120.5, "hardware", "west"),
        (2, 99.99, "software", "east"),
        (3, 240.0, "hardware", "west"),
        (4, 75.0, "services", "south"),
        (5, 180.25, "hardware", "north"),
        (6, 60.0, "services", "west"),
        (7, 130.75, "software", "east"),
        (8, 220.1, "hardware", "south"),
        (9, 95.5, "services", "north"),
        (10, 110.0, "software", "west"),
    ]
    for row in rows:
        output.write(",".join([str(row[0]), f"{row[1]:.2f}", row[2], row[3]]) + "\n")
    return output.getvalue().encode("utf-8")


def _build_analysis_script(dataset_source: str) -> str:
    dataset_path = dataset_source
    if dataset_path.startswith("file://"):
        dataset_path = dataset_path[len("file://") :]
    template = """
import csv
import statistics
from collections import Counter
from pathlib import Path

DATASET_PATH = r"__DATASET_PATH__"


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False

path = Path(DATASET_PATH)
if not path.is_file():
    raise SystemExit(f"Dataset not found: {DATASET_PATH}")

with path.open(newline="") as handle:
    reader = csv.DictReader(handle)
    rows = list(reader)

if not rows:
    raise SystemExit("Dataset is empty")

columns = list(rows[0].keys())
row_count = len(rows)

missing = {col: 0 for col in columns}
for row in rows:
    for col in columns:
        value = row.get(col, "")
        if value is None or str(value).strip() == "":
            missing[col] += 1

numeric_summary = []
for col in columns:
    values = []
    for row in rows:
        raw = row.get(col, "")
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    if values:
        numeric_summary.append(
            {
                "column": col,
                "min": min(values),
                "max": max(values),
                "mean": statistics.mean(values),
            }
        )

category_summary = []
for col in columns:
    sample = [row.get(col, "") for row in rows]
    if any(item is None for item in sample):
        continue
    if all(_is_number(item) for item in sample if str(item).strip() != ""):
        continue
    counts = Counter(sample)
    top = counts.most_common(3)
    category_summary.append({"column": col, "top": top})

print("# Data Report")
print("")
print(f"- Rows: {row_count}")
print(f"- Columns: {len(columns)}")
print("")
print("## Missing values")
print("| column | missing |")
print("| --- | --- |")
for col in columns:
    print(f"| {col} | {missing[col]} |")

if numeric_summary:
    print("")
    print("## Numeric summary")
    print("| column | min | max | mean |")
    print("| --- | --- | --- | --- |")
    for entry in numeric_summary:
        print(
            "| {column} | {min:.2f} | {max:.2f} | {mean:.2f} |".format(**entry)
        )

if category_summary:
    print("")
    print("## Top categories")
    for entry in category_summary:
        print(f"- **{entry['column']}**: {entry['top']}")

print("")
print("## Sample rows")
print("| " + " | ".join(columns) + " |")
print("| " + " | ".join(["---"] * len(columns)) + " |")
for row in rows[:5]:
    values = [str(row.get(col, "")) for col in columns]
    print("| " + " | ".join(values) + " |")
"""
    return template.replace("__DATASET_PATH__", dataset_path)


def _extract_stdout(run: dict[str, Any]) -> str:
    log = run.get("log") or []
    for entry in log:
        if entry.get("tool") == "run_python":
            output = entry.get("output") or {}
            return str(output.get("stdout") or "")
    return ""


def _extract_stderr(run: dict[str, Any]) -> str:
    log = run.get("log") or []
    for entry in log:
        if entry.get("tool") == "run_python":
            output = entry.get("output") or {}
            return str(output.get("stderr") or "")
    return ""


def _apply_chat_run_steps(
    api_base: str,
    project_id: str,
    run: dict[str, Any],
    allowed_tools: set[str] | None = None,
) -> dict[str, Any]:
    run_id = run.get("id", "")
    plan = run.get("plan") or {}
    steps = plan.get("steps") or []
    updated_run = run
    for step in steps:
        step_id = step.get("id")
        tool = step.get("tool")
        if allowed_tools is not None and tool not in allowed_tools:
            continue
        if not step_id:
            continue
        updated_run = _apply_agent_step(api_base, project_id, run_id, step_id, "e2e")
    return updated_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Run e2e evaluation against the agent API.")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--cleanup", action="store_true", help="Delete project after run")
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    steps: list[EvalStep] = []
    project_id = ""

    try:
        _health_check(api_base)
        steps.append(EvalStep("health_check", True, "API reachable"))
        _log_section("API")
        print(f"API base: {api_base}")

        project = _create_project(api_base)
        project_id = project["id"]
        workspace_path = project["workspace_path"]
        steps.append(EvalStep("create_project", True, f"project_id={project_id}"))
        _log_section("Project")
        _log_json("Project", project)

        dataset = _upload_dataset(api_base, project_id, _build_dataset())
        dataset_id = dataset["id"]
        dataset_source = dataset["source"]
        steps.append(EvalStep("upload_dataset", True, f"dataset_id={dataset_id}"))
        _log_section("Dataset")
        _log_json("Dataset", dataset)

        dataset_fs_path = dataset_source
        if dataset_fs_path.startswith("file://"):
            dataset_fs_path = dataset_fs_path[len("file://") :]
        dataset_path_rel = "data/raw/e2e-data.csv"
        if dataset_fs_path.startswith(workspace_path + "/"):
            dataset_path_rel = dataset_fs_path[len(workspace_path) + 1 :]

        analysis_script = _build_analysis_script(dataset_path_rel)
        _log_section("Analysis script")
        print(analysis_script)
        script_path = "scripts/agent/e2e_eval_analysis.py"
        plan = {
            "objective": "Create and run a Python analysis script that emits a markdown report.",
            "steps": [
                {
                    "id": "write-script",
                    "title": "Write analysis script",
                    "description": "Write a Python script that analyzes the dataset and prints markdown.",
                    "tool": "write_file",
                    "args": {
                        "path": script_path,
                        "content": analysis_script,
                    },
                    "requires_approval": False,
                },
                {
                    "id": "run-script",
                    "title": "Run analysis script",
                    "description": "Execute the analysis script to emit markdown.",
                    "tool": "run_python",
                    "args": {
                        "path": script_path,
                    },
                    "requires_approval": False,
                },
            ],
        }
        approvals = {
            "write-script": {"approved_by": "e2e"},
            "run-script": {"approved_by": "e2e"},
        }
        _log_section("Agent plan")
        _log_json("Plan", plan)
        _log_json("Approvals", approvals)
        run = _create_agent_run(api_base, project_id, plan, approvals)
        _log_section("Agent run log")
        _log_json("Run", run)
        report_md = _extract_stdout(run)
        report_err = _extract_stderr(run)
        _log_section("Agent run output")
        print(report_md)
        if report_err:
            print("\nSTDERR:\n" + report_err)
        if "# Data Report" not in report_md:
            raise RuntimeError("Markdown report missing expected header")
        if "## Missing values" not in report_md:
            raise RuntimeError("Markdown report missing missing-values section")
        steps.append(EvalStep("agent_run_python", True, "markdown emitted"))

        report_path = "artifacts/agent/e2e-report.md"
        write_plan = {
            "objective": "Persist the markdown report to the project workspace.",
            "steps": [
                {
                    "id": "write-report",
                    "title": "Write markdown report",
                    "description": "Write the markdown report produced by the Python analysis.",
                    "tool": "write_markdown",
                    "args": {"path": report_path, "content": report_md},
                    "requires_approval": False,
                }
            ],
        }
        write_approvals = {"write-report": {"approved_by": "e2e"}}
        _log_section("Write markdown report")
        _log_json("Write plan", write_plan)
        _log_json("Write approvals", write_approvals)
        _create_agent_run(api_base, project_id, write_plan, write_approvals)
        artifacts = _list_agent_artifacts(api_base, project_id)
        _log_json("Artifacts", artifacts)
        for artifact in artifacts:
            _log_artifact_contents(artifact)
        markdown_artifact = next(
            (
                artifact
                for artifact in artifacts
                if artifact.get("type") == "markdown"
                and str(artifact.get("path", "")).endswith(report_path)
            ),
            None,
        )
        if not markdown_artifact:
            raise RuntimeError("Markdown artifact not found after write_markdown")
        steps.append(EvalStep("agent_write_markdown", True, "artifact recorded"))
        _log_section("Markdown artifact")
        _log_json("Markdown artifact", markdown_artifact)

        chat_script_path = "scripts/agent/chat_eval_script.py"
        chat_prompt = (
            "Create a simple Python script that reads the CSV dataset at the path below, "
            "computes basic stats (row/column counts, missing values), and prints a markdown report. "
            "Use only Python standard library modules (csv, statistics, collections); do not use pandas. "
            "Use write_file with the exact path provided, then run_python with the same path. "
            "Only use the write_file and run_python tools. The script must print the markdown report to stdout. "
            f"Script path: {chat_script_path}. "
            f"Dataset path: {dataset_path_rel}."
        )
        chat_prompt_strict = (
            "Return a plan with exactly two steps. "
            "Step 1 uses tool write_file to write a Python script at the provided path. "
            "Step 2 uses tool run_python to execute that same path. "
            "Use only standard library modules (csv, statistics, collections). "
            "The script must print a markdown report to stdout. "
            f"Script path: {chat_script_path}. "
            f"Dataset path: {dataset_path_rel}."
        )
        _log_section("Chat prompts")
        print(chat_prompt)
        print("\n---\n")
        print(chat_prompt_strict)
        chat_response = _send_chat_with_retry(
            api_base,
            project_id,
            [chat_prompt, chat_prompt_strict],
            dataset_id=dataset_id,
            safe_mode=True,
            auto_run=True,
        )
        _log_section("Chat response")
        _log_json("Chat response", chat_response)
        chat_run = chat_response.get("run")
        if not chat_run:
            raise RuntimeError("Chat response missing run payload")
        chat_run = _apply_chat_run_steps(
            api_base,
            project_id,
            chat_run,
            allowed_tools={"write_file", "run_python"},
        )
        _log_section("Chat run log")
        _log_json("Chat run", chat_run)
        chat_report_md = _extract_stdout(chat_run)
        chat_report_err = _extract_stderr(chat_run)
        _log_section("Chat run output")
        print(chat_report_md)
        if chat_report_err:
            print("\nSTDERR:\n" + chat_report_err)
        if not chat_report_md.strip():
            raise RuntimeError("Chat-run markdown report missing output")
        if "#" not in chat_report_md:
            raise RuntimeError("Chat-run markdown report missing markdown header")
        steps.append(EvalStep("chat_run_python", True, "chat-driven markdown emitted"))

        chat_report_path = "artifacts/agent/e2e-chat-report.md"
        chat_write_plan = {
            "objective": "Persist the chat-driven markdown report to the project workspace.",
            "steps": [
                {
                    "id": "write-chat-report",
                    "title": "Write chat markdown report",
                    "description": "Write the markdown report produced by the chat-driven analysis.",
                    "tool": "write_markdown",
                    "args": {"path": chat_report_path, "content": chat_report_md},
                    "requires_approval": False,
                }
            ],
        }
        chat_write_approvals = {"write-chat-report": {"approved_by": "e2e"}}
        _log_section("Chat write markdown")
        _log_json("Chat write plan", chat_write_plan)
        _log_json("Chat write approvals", chat_write_approvals)
        _create_agent_run(api_base, project_id, chat_write_plan, chat_write_approvals)
        chat_artifacts = _list_agent_artifacts(api_base, project_id)
        _log_json("Chat artifacts", chat_artifacts)
        for artifact in chat_artifacts:
            _log_artifact_contents(artifact)
        chat_markdown_artifact = next(
            (
                artifact
                for artifact in chat_artifacts
                if artifact.get("type") == "markdown"
                and str(artifact.get("path", "")).endswith(chat_report_path)
            ),
            None,
        )
        if not chat_markdown_artifact:
            raise RuntimeError("Chat markdown artifact not found after write_markdown")
        steps.append(EvalStep("chat_write_markdown", True, "chat artifact recorded"))
        _log_section("Chat markdown artifact")
        _log_json("Chat markdown artifact", chat_markdown_artifact)

    except Exception as exc:  # noqa: BLE001 - keep top-level errors concise
        steps.append(EvalStep("evaluation", False, str(exc)))

    finally:
        if args.cleanup and project_id:
            try:
                _delete_project(api_base, project_id)
                steps.append(EvalStep("cleanup", True, "project deleted"))
            except Exception as exc:  # noqa: BLE001 - cleanup best effort
                steps.append(EvalStep("cleanup", False, str(exc)))

    failed = [step for step in steps if not step.ok]
    print("\nE2E evaluation results")
    print("======================")
    for step in steps:
        status = "PASS" if step.ok else "FAIL"
        print(f"- {status}: {step.name} ({step.details})")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
