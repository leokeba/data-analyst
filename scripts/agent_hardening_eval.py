#!/usr/bin/env python3
"""Hardening eval script for the agent harness.

This command runs a stricter, failure-seeking scenario to surface harness
weaknesses. It intentionally enforces tight plan rules (exact tools, paths,
step counts), validates markdown structure, and flags any sandbox escapes. Use
this to iterate: adjust the agent/harness until the script passes, then raise
the bar again.

Usage:
  uv run python scripts/agent_hardening_eval.py [--api-base URL] [--cleanup]

Optional:
  --log-file PATH   Write raw plan/output/steps JSON for post-mortems.
  API_BASE          Environment override for the API base URL.

The script exits non-zero on any violation so CI can catch regressions.
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
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class EvalStep:
    name: str
    ok: bool
    details: str


# ---------- Logging helpers ----------


def _log_section(title: str) -> None:
    print("\n" + title)
    print("-" * len(title))


def _log_json(label: str, payload: object) -> None:
    print(f"\n{label}:")
    print(json.dumps(payload, indent=2, default=str))


def _write_debug_log(path: str, payload: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001 - best-effort logging
        print(f"(Unable to write debug log: {exc})", file=sys.stderr)


# ---------- HTTP helpers ----------


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
    method: str, url: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    status, _, data = _request(method, url, body=body, headers=headers)
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
    boundary = f"----hardening-boundary-{int(time.time() * 1000)}"
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


# ---------- API helpers ----------


def _health_check(api_base: str) -> None:
    status, _, data = _request("GET", f"{api_base}/health")
    if status != 200:
        raise RuntimeError(
            f"Health check failed ({status}): {data.decode('utf-8', errors='replace')}"
        )


def _create_project(api_base: str) -> dict[str, Any]:
    name = f"hardening-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name})


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
    )


def _list_agent_artifacts(api_base: str, project_id: str) -> list[dict[str, Any]]:
    status, _, data = _request(
        "GET", f"{api_base}/projects/{project_id}/agent/artifacts"
    )
    parsed = json.loads(data.decode("utf-8")) if data else []
    if status >= 400:
        raise RuntimeError(f"Artifact list failed ({status}): {parsed}")
    if isinstance(parsed, list):
        return parsed
    return parsed.get("data", [])


def _delete_project(api_base: str, project_id: str) -> None:
    status, _, data = _request("DELETE", f"{api_base}/projects/{project_id}")
    if status not in (200, 204):
        raise RuntimeError(
            f"Project delete failed ({status}): {data.decode('utf-8', errors='replace')}"
        )


# ---------- Dataset helpers ----------


def _build_edge_dataset() -> bytes:
    output = io.StringIO()
    output.write("id,amount,category,region,notes\n")
    rows = [
        (1, 120.5, "hardware", "west", "first"),
        (2, 99.99, "software", "east", ""),
        (3, 240.0, "hardware", "west", "duplicate id"),
        (3, 75.0, "services", "south", "dup check"),
        (5, 180.25, "hardware", "north", None),
        (6, 60.0, "services", "west", ""),
        (7, 130.75, "software", "east", "NaN"),
        (8, 220.1, "hardware", "south", "outlier?"),
        (9, -5.0, "services", "north", "negative"),
        (10, 0.0, "software", "west", "zero"),
    ]
    for row in rows:
        output.write(
            ",".join(
                [
                    str(row[0]),
                    f"{row[1]:.2f}",
                    row[2],
                    row[3],
                    "" if row[4] is None else str(row[4]),
                ]
            )
            + "\n"
        )
    return output.getvalue().encode("utf-8")


# ---------- Validation helpers ----------


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


def _validate_plan(
    plan: dict[str, Any],
    *,
    expected_tools: list[str],
    script_path: str,
    workspace_path: str,
    allowed_tools: set[str],
) -> None:
    steps = plan.get("steps") or []
    if len(steps) != len(expected_tools):
        raise RuntimeError(
            f"Plan must have {len(expected_tools)} steps, got {len(steps)}"
        )
    for idx, (step, expected_tool) in enumerate(
        zip(steps, expected_tools, strict=True)
    ):
        tool = step.get("tool")
        if tool != expected_tool:
            raise RuntimeError(f"Step {idx} expected tool {expected_tool}, got {tool}")
        if tool not in allowed_tools:
            raise RuntimeError(f"Plan uses disallowed tool: {tool}")
        args = step.get("args") or {}
        path = args.get("path")
        if isinstance(path, str) and path:
            if not path.startswith(workspace_path):
                raise RuntimeError(f"Step {idx} path escapes workspace: {path}")
            if expected_tool in {"write_file", "run_python"} and path != script_path:
                raise RuntimeError(
                    f"Step {idx} must target script path {script_path}, got {path}"
                )


def _validate_markdown(report: str) -> None:
    if not report.strip():
        raise RuntimeError("Markdown report missing output")
    required_markers = [
        "# Hardening Report",
        "## data_quality",
        "## anomaly_checks",
        "## sample",
        "DUPLICATE_DETECTED",
    ]
    for marker in required_markers:
        if marker not in report:
            raise RuntimeError(f"Markdown report missing required marker: {marker}")


def _apply_chat_run_steps(
    api_base: str,
    project_id: str,
    run: dict[str, Any],
    allowed_tools: set[str],
) -> dict[str, Any]:
    run_id = run.get("id", "")
    plan = run.get("plan") or {}
    steps = plan.get("steps") or []
    updated_run = run
    for step in steps:
        step_id = step.get("id")
        tool = step.get("tool")
        if tool not in allowed_tools:
            continue
        if not step_id:
            continue
        updated_run = _apply_agent_step(
            api_base, project_id, run_id, step_id, "hardening"
        )
    return updated_run


# ---------- Main flow ----------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run hardening eval against the agent API."
    )
    parser.add_argument(
        "--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000")
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Delete project after run"
    )
    parser.add_argument("--log-file", help="Optional path to write raw debug JSON")
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    steps: list[EvalStep] = []
    project_id = ""
    debug_payload: dict[str, Any] = {"api_base": api_base, "steps": []}

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
        debug_payload["project"] = project

        dataset = _upload_dataset(
            api_base, project_id, _build_edge_dataset(), "edge-data.csv"
        )
        dataset_id = dataset["id"]
        dataset_source = dataset["source"]
        steps.append(EvalStep("upload_dataset", True, f"dataset_id={dataset_id}"))
        _log_section("Dataset")
        _log_json("Dataset", dataset)
        debug_payload["dataset"] = dataset

        dataset_path = dataset_source.replace("file://", "")
        script_path = f"{workspace_path}/scripts/agent/hardening_script.py"
        report_path = f"{workspace_path}/artifacts/agent/hardening-report.md"

        hard_prompt = (
            "Return a JSON plan with exactly two steps: write_file then run_python. "
            "Both steps must target the exact script path provided. Use only standard "
            "library modules (csv, statistics, collections, pathlib). The script must read "
            "the dataset path provided, compute row/column counts, missing values per "
            "column, and detect duplicate ids. If duplicates exist, print the literal token "
            "DUPLICATE_DETECTED in the markdown. Include sections '## data_quality', "
            "'## anomaly_checks', and '## sample'. The markdown must start with '# Hardening Report'. "
            f"Script path: {script_path}. Dataset path: {dataset_path}."
        )
        stricter_prompt = (
            "STRICT MODE: respond only with a plan that has two steps with ids 'hard-write' "
            "and 'hard-run' using tools write_file then run_python, no extra tools, and the "
            "write_file content must be valid Python using only the standard library."
        )
        _log_section("Prompts")
        print(hard_prompt)
        print("\n---\n")
        print(stricter_prompt)

        chat_response = _send_chat_with_retry(
            api_base,
            project_id,
            [hard_prompt, stricter_prompt],
            dataset_id=dataset_id,
            safe_mode=True,
            auto_run=True,
        )
        _log_section("Chat response")
        _log_json("Chat response", chat_response)
        debug_payload["chat_response"] = chat_response

        chat_run = chat_response.get("run")
        if not chat_run:
            raise RuntimeError("Chat response missing run payload")

        plan = chat_run.get("plan") or {}
        _validate_plan(
            plan,
            expected_tools=["write_file", "run_python"],
            script_path=script_path,
            workspace_path=workspace_path,
            allowed_tools={"write_file", "run_python"},
        )
        steps.append(EvalStep("plan_validation", True, "strict plan validated"))

        chat_run = _apply_chat_run_steps(
            api_base,
            project_id,
            chat_run,
            allowed_tools={"write_file", "run_python"},
        )
        _log_section("Chat run log")
        _log_json("Chat run", chat_run)
        debug_payload["chat_run"] = chat_run

        chat_stdout = _extract_stdout(chat_run)
        chat_stderr = _extract_stderr(chat_run)
        _log_section("Chat run output")
        print(chat_stdout)
        if chat_stderr:
            print("\nSTDERR:\n" + chat_stderr)
        _validate_markdown(chat_stdout)
        steps.append(EvalStep("report_validation", True, "markdown checks passed"))

        write_plan = {
            "objective": "Persist the hardening markdown report to the project workspace.",
            "steps": [
                {
                    "id": "write-hardening-report",
                    "title": "Write hardening markdown",
                    "description": "Write the markdown report produced by the hardening scenario.",
                    "tool": "write_markdown",
                    "args": {"path": report_path, "content": chat_stdout},
                    "requires_approval": False,
                }
            ],
        }
        write_approvals = {"write-hardening-report": {"approved_by": "hardening"}}
        _log_section("Write markdown plan")
        _log_json("Write plan", write_plan)
        _log_json("Write approvals", write_approvals)
        write_run = _create_agent_run(api_base, project_id, write_plan, write_approvals)
        debug_payload["write_run"] = write_run

        artifacts = _list_agent_artifacts(api_base, project_id)
        _log_json("Artifacts", artifacts)
        debug_payload["artifacts"] = artifacts
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
        steps.append(EvalStep("artifact_recorded", True, "hardening report saved"))

    except Exception as exc:  # noqa: BLE001 - keep top-level errors concise
        steps.append(EvalStep("hardening_eval", False, str(exc)))

    finally:
        if args.cleanup and project_id:
            try:
                _delete_project(api_base, project_id)
                steps.append(EvalStep("cleanup", True, "project deleted"))
            except Exception as exc:  # noqa: BLE001 - cleanup best effort
                steps.append(EvalStep("cleanup", False, str(exc)))

    failed = [step for step in steps if not step.ok]
    _log_section("Hardening evaluation results")
    print("============================")
    for step in steps:
        status = "PASS" if step.ok else "FAIL"
        print(f"- {status}: {step.name} ({step.details})")

    debug_payload["steps"] = [asdict(step) for step in steps]
    if args.log_file:
        _write_debug_log(args.log_file, debug_payload)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
