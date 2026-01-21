#!/usr/bin/env python3
"""E2E evaluation script for the agent API (chat-driven only).

This script exercises the API directly by:
1) Creating a project
2) Uploading multiple CSV datasets
3) Instructing the LLM agent (via chat) to write + run analysis scripts
4) Capturing markdown reports from stdout
5) Writing markdown reports via the agent tool

Usage:
  uv run python scripts/e2e_agent_eval_chat.py

Optional environment variables:
  API_BASE=http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
    lower = path.lower()
    if not any(lower.endswith(ext) for ext in (".md", ".json", ".txt", ".py", ".csv")):
        print("(Binary or unsupported artifact; skipping content preview)")
        return
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


def _encode_multipart(
    field_name: str, filename: str, content: bytes
) -> tuple[bytes, str]:
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
        raise RuntimeError(
            f"Health check failed ({status}): {data.decode('utf-8', errors='replace')}"
        )


def _create_project(api_base: str) -> dict[str, Any]:
    name = f"e2e-eval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name}, timeout=DEFAULT_TIMEOUT)


def _upload_dataset(
    api_base: str, project_id: str, csv_bytes: bytes, filename: str
) -> dict[str, Any]:
    body, content_type = _encode_multipart("file", filename, csv_bytes)
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


def _build_base_dataset() -> bytes:
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


def _build_customers_dataset() -> bytes:
    output = io.StringIO()
    output.write("customer_id,segment,region\n")
    rows = [
        ("C001", "enterprise", "west"),
        ("C002", "smb", "east"),
        ("C003", "smb", "west"),
        ("C004", "mid", "south"),
        ("C005", "enterprise", "north"),
        ("C006", "smb", "west"),
        ("C007", "mid", "east"),
        ("C008", "enterprise", "south"),
    ]
    for row in rows:
        output.write(",".join(row) + "\n")
    return output.getvalue().encode("utf-8")


def _build_orders_dataset() -> bytes:
    output = io.StringIO()
    output.write("order_id,customer_id,amount,channel,region\n")
    rows = [
        ("O100", "C001", 120.50, "web", "west"),
        ("O101", "C002", 99.99, "partner", "east"),
        ("O102", "C003", 240.00, "web", "west"),
        ("O103", "C004", 75.00, "direct", "south"),
        ("O104", "C005", 180.25, "web", "north"),
        ("O105", "C006", 60.00, "direct", "west"),
        ("O106", "C007", 130.75, "partner", "east"),
        ("O107", "C008", 220.10, "web", "south"),
        ("O108", "C001", 95.50, "direct", "north"),
        ("O109", "C002", 110.00, "web", "west"),
        ("O110", "C009", 88.00, "web", "west"),
        ("O111", "C003", 240.00, "web", "west"),
    ]
    for row in rows:
        output.write(",".join([row[0], row[1], f"{row[2]:.2f}", row[3], row[4]]) + "\n")
    return output.getvalue().encode("utf-8")


def _validate_markdown(report: str, required_headers: list[str]) -> None:
    if not report.strip():
        raise RuntimeError("Markdown report missing output")
    for header in required_headers:
        if header not in report:
            raise RuntimeError(f"Markdown report missing section: {header}")


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


def _collect_invalid_paths(plan: dict[str, Any], workspace_path: str) -> list[str]:
    invalid: list[str] = []
    steps = plan.get("steps") or []
    for step in steps:
        args = step.get("args") or {}
        path = args.get("path")
        if isinstance(path, str) and path:
            if Path(path).is_absolute() and not path.startswith(workspace_path):
                invalid.append(path)
    return invalid


def _run_chat_task(
    *,
    api_base: str,
    project_id: str,
    workspace_path: str,
    dataset_id: str | None,
    title: str,
    prompts: list[str],
    report_path: str,
    required_headers: list[str],
    steps: list[EvalStep],
) -> None:
    _log_section(title)
    for prompt in prompts:
        print(prompt)
        print("\n---\n")
    chat_response = _send_chat_with_retry(
        api_base,
        project_id,
        prompts,
        dataset_id=dataset_id,
        safe_mode=True,
        auto_run=True,
    )
    _log_section(f"{title} response")
    _log_json("Chat response", chat_response)
    chat_run = chat_response.get("run")
    if not chat_run:
        raise RuntimeError("Chat response missing run payload")
    invalid_paths = _collect_invalid_paths(chat_run.get("plan") or {}, workspace_path)
    if invalid_paths:
        raise RuntimeError(f"Chat plan uses paths outside workspace: {invalid_paths}")
    chat_run = _apply_chat_run_steps(
        api_base,
        project_id,
        chat_run,
        allowed_tools={"write_file", "run_python", "write_markdown"},
    )
    _log_section(f"{title} run log")
    _log_json("Chat run", chat_run)
    chat_report_md = _extract_stdout(chat_run)
    chat_report_err = _extract_stderr(chat_run)
    _log_section(f"{title} run output")
    print(chat_report_md)
    if chat_report_err:
        print("\nSTDERR:\n" + chat_report_err)
    _validate_markdown(chat_report_md, required_headers)
    steps.append(EvalStep(f"{title}_run", True, "chat markdown emitted"))

    write_plan = {
        "objective": f"Persist the {title} markdown report to the project workspace.",
        "steps": [
            {
                "id": f"write-{title}-report",
                "title": f"Write {title} markdown report",
                "description": "Write the markdown report produced by the chat task.",
                "tool": "write_markdown",
                "args": {"path": report_path, "content": chat_report_md},
                "requires_approval": False,
            }
        ],
    }
    write_approvals = {f"write-{title}-report": {"approved_by": "e2e"}}
    _log_section(f"{title} write markdown")
    _log_json("Write plan", write_plan)
    _log_json("Write approvals", write_approvals)
    _create_agent_run(api_base, project_id, write_plan, write_approvals)
    artifacts = _list_agent_artifacts(api_base, project_id)
    _log_json("Artifacts", artifacts)
    for artifact in artifacts:
        _log_artifact_contents(artifact)
    report_artifact = next(
        (
            artifact
            for artifact in artifacts
            if artifact.get("type") == "markdown"
            and str(artifact.get("path", "")).endswith(report_path)
        ),
        None,
    )
    if not report_artifact:
        raise RuntimeError("Markdown artifact not found after write_markdown")
    steps.append(EvalStep(f"{title}_write", True, "artifact recorded"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run e2e evaluation against the agent API."
    )
    parser.add_argument(
        "--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000")
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Delete project after run"
    )
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

        base_dataset = _upload_dataset(
            api_base, project_id, _build_base_dataset(), "base-data.csv"
        )
        base_id = base_dataset["id"]
        base_source = base_dataset["source"]
        steps.append(EvalStep("upload_base", True, f"dataset_id={base_id}"))
        _log_section("Base dataset")
        _log_json("Base", base_dataset)

        customers_dataset = _upload_dataset(
            api_base, project_id, _build_customers_dataset(), "customers.csv"
        )
        customers_id = customers_dataset["id"]
        customers_source = customers_dataset["source"]
        steps.append(EvalStep("upload_customers", True, f"dataset_id={customers_id}"))
        _log_section("Customers dataset")
        _log_json("Customers", customers_dataset)

        orders_dataset = _upload_dataset(
            api_base, project_id, _build_orders_dataset(), "orders.csv"
        )
        orders_id = orders_dataset["id"]
        orders_source = orders_dataset["source"]
        steps.append(EvalStep("upload_orders", True, f"dataset_id={orders_id}"))
        _log_section("Orders dataset")
        _log_json("Orders", orders_dataset)

        base_path = base_source.replace("file://", "")
        base_path_rel = "data/raw/base-data.csv"
        if base_path.startswith(workspace_path + "/"):
            base_path_rel = base_path[len(workspace_path) + 1 :]
        basic_prompt = (
            "Write a Python script that reads the CSV dataset at the path below and produces a markdown report "
            "with row/column counts, missing values by column, and the top 3 categories for each categorical column. "
            "You may use pandas/polars/matplotlib if available, otherwise use the standard library. "
            "Use write_file to save the script under the provided script path, then run_python to execute it. "
            "The script MUST print the full markdown report to stdout (not just a file path). "
            "Script path: scripts/agent/chat_basic_analysis.py. "
            f"Dataset path: {base_path_rel}."
        )
        basic_prompt_strict = (
            "Return a JSON plan with exactly two steps: write_file then run_python. "
            "Use only tools from the catalog. "
            "The script MUST print the full markdown report to stdout. "
            "Script path: scripts/agent/chat_basic_analysis.py. "
            f"Dataset path: {base_path_rel}."
        )
        _run_chat_task(
            api_base=api_base,
            project_id=project_id,
            workspace_path=workspace_path,
            dataset_id=base_id,
            title="chat_basic_analysis",
            prompts=[basic_prompt, basic_prompt_strict],
            report_path="artifacts/agent/chat-basic-report.md",
            required_headers=["#", "##"],
            steps=steps,
        )

        customers_path = customers_source.replace("file://", "")
        orders_path = orders_source.replace("file://", "")
        customers_path_rel = "data/raw/customers.csv"
        orders_path_rel = "data/raw/orders.csv"
        if customers_path.startswith(workspace_path + "/"):
            customers_path_rel = customers_path[len(workspace_path) + 1 :]
        if orders_path.startswith(workspace_path + "/"):
            orders_path_rel = orders_path[len(workspace_path) + 1 :]
        join_prompt = (
            "Write a Python script that joins orders.csv with customers.csv on customer_id, "
            "then generates a markdown report with missing customers, duplicate order_ids, "
            "average order amount by segment and by channel, plus a small sample table. "
            "You may use pandas/polars/matplotlib if available; otherwise standard library is fine. "
            "Use write_file to save the script under the provided script path, then run_python to execute it. "
            "The script MUST print the full markdown report to stdout (not just a file path). "
            "Script path: scripts/agent/chat_join_analysis.py. "
            f"Customers path: {customers_path_rel}. "
            f"Orders path: {orders_path_rel}."
        )
        join_prompt_strict = (
            "Return a JSON plan with exactly two steps: write_file then run_python. "
            "Use only tools from the catalog. "
            "The script MUST print the full markdown report to stdout. "
            "Script path: scripts/agent/chat_join_analysis.py. "
            f"Customers path: {customers_path_rel}. "
            f"Orders path: {orders_path_rel}."
        )
        _run_chat_task(
            api_base=api_base,
            project_id=project_id,
            workspace_path=workspace_path,
            dataset_id=orders_id,
            title="chat_join_analysis",
            prompts=[join_prompt, join_prompt_strict],
            report_path="artifacts/agent/chat-join-report.md",
            required_headers=["#", "## Missing", "## Duplicate", "##"],
            steps=steps,
        )

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
