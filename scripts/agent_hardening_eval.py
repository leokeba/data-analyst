#!/usr/bin/env python3
"""Hardening eval: enforce exact script path and report content."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
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


def _health_check(api_base: str) -> None:
    status, _, data = _request("GET", f"{api_base}/health")
    if status != 200:
        raise RuntimeError(
            f"Health check failed ({status}): {data.decode('utf-8', errors='replace')}"
        )


def _create_project(api_base: str) -> dict[str, Any]:
    name = f"hardening-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name})


def _setup_workspace(workspace_path: Path) -> dict[str, Any]:
    (workspace_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (workspace_path / "artifacts" / "agent").mkdir(parents=True, exist_ok=True)
    (workspace_path / "scripts" / "agent").mkdir(parents=True, exist_ok=True)

    csv_path = workspace_path / "data" / "raw" / "edge-data.csv"
    csv_path.write_text(
        "id,amount,region,notes\n"
        "1,10.0,west,ok\n"
        "2,20.0,east,\n"
        "2,20.0,east,dup\n"
        "3,,west,\n"
        "4,40.0,north,ok\n",
        encoding="utf-8",
    )
    return {
        "row_count": 5,
        "column_count": 4,
        "missing_amount": 1,
        "missing_notes": 2,
        "duplicate_ids": 1,
    }


def _send_agent_chat(api_base: str, project_id: str, content: str) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": False, "auto_run": True},
    )


def _tool_paths(run: dict[str, Any], tool: str) -> list[str]:
    paths = []
    for entry in run.get("log") or []:
        if entry.get("tool") != tool:
            continue
        output = entry.get("output") or {}
        path = output.get("path")
        if isinstance(path, str):
            paths.append(path)
    return paths


def _validate_report(workspace_path: Path, expected: dict[str, Any]) -> tuple[bool, str]:
    report_path = workspace_path / "artifacts" / "agent" / "hardening-report.md"
    if not report_path.exists():
        return False, "Report file missing"
    content = report_path.read_text(encoding="utf-8")
    required_sections = ["# Hardening Report", "## data_quality", "## anomaly_checks", "## sample"]
    for section in required_sections:
        if section not in content:
            return False, f"Missing section: {section}"
    token_values: dict[str, str] = {}
    for line in content.splitlines():
        cleaned = line.strip().lstrip("-").strip()
        for key in expected:
            prefix = f"{key}="
            if cleaned.startswith(prefix):
                token_values[key] = cleaned.split("=", 1)[1].strip()
    for key, value in expected.items():
        if key not in token_values:
            return False, f"Missing token: {key}={value}"
        raw_value = token_values[key]
        if key in {"row_count", "column_count"}:
            try:
                if int(raw_value) != value:
                    return False, f"{key} mismatch: {raw_value}"
            except ValueError:
                return False, f"{key} value invalid: {raw_value}"
        elif key in {"missing_amount", "missing_notes"}:
            if raw_value.isdigit():
                if int(raw_value) != value:
                    return False, f"{key} mismatch: {raw_value}"
            else:
                field = "amount" if key == "missing_amount" else "notes"
                if field not in raw_value or str(value) not in raw_value:
                    return False, f"{key} value invalid: {raw_value}"
        elif key == "duplicate_ids":
            if raw_value.startswith("["):
                if "2" not in raw_value:
                    return False, f"duplicate_ids list mismatch: {raw_value}"
            else:
                try:
                    if int(raw_value) != value:
                        return False, f"duplicate_ids count mismatch: {raw_value}"
                except ValueError:
                    return False, f"duplicate_ids value invalid: {raw_value}"
    if "DUPLICATE_DETECTED" not in content:
        return False, "Missing DUPLICATE_DETECTED token"
    return True, "Report file valid"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    steps: list[EvalStep] = []
    api_base = args.api_base

    _log_section("Health")
    _health_check(api_base)
    steps.append(EvalStep("health_check", True, "API reachable"))

    _log_section("Project")
    project = _create_project(api_base)
    project_id = project["id"]
    workspace_path = Path(project["workspace_path"])
    expected = _setup_workspace(workspace_path)
    steps.append(EvalStep("create_project", True, f"project_id={project_id}"))

    prompt = (
        "Return a report by writing scripts/agent/hardening_script.py and running it. "
        "The script must read data/raw/edge-data.csv and compute row/column counts, missing values per column, "
        "and duplicate ids. If duplicates exist, print literal token DUPLICATE_DETECTED in the markdown. "
        "Write artifacts/agent/hardening-report.md with sections # Hardening Report, ## data_quality, "
        "## anomaly_checks, ## sample. Include tokens row_count, column_count, missing_amount, missing_notes, "
        "duplicate_ids as key=value lines."
    )

    _log_section("Agent")
    response = _send_agent_chat(api_base, project_id, prompt)
    run = response.get("run") or {}
    assistant = response["messages"][1]["content"]
    if "# Hardening Report" not in assistant:
        steps.append(EvalStep("assistant_report", False, "Assistant did not return report"))
    else:
        steps.append(EvalStep("assistant_report", True, "Assistant returned report"))

    write_paths = _tool_paths(run, "write_file")
    run_paths = _tool_paths(run, "run_python")
    report_paths = _tool_paths(run, "write_markdown")
    if "scripts/agent/hardening_script.py" not in write_paths:
        steps.append(EvalStep("script_path", False, "Missing write_file to scripts/agent/hardening_script.py"))
    else:
        steps.append(EvalStep("script_path", True, "Script written"))
    if "scripts/agent/hardening_script.py" not in run_paths:
        steps.append(EvalStep("run_path", False, "Missing run_python for scripts/agent/hardening_script.py"))
    else:
        steps.append(EvalStep("run_path", True, "Script executed"))
    if "artifacts/agent/hardening-report.md" not in report_paths:
        steps.append(EvalStep("report_path", False, "Missing write_markdown to artifacts/agent/hardening-report.md"))
    else:
        steps.append(EvalStep("report_path", True, "Report written"))

    ok_report, report_details = _validate_report(workspace_path, expected)
    steps.append(EvalStep("report_file", ok_report, report_details))

    _log_section("Results")
    failures = [step for step in steps if not step.ok]
    for step in steps:
        status = "PASS" if step.ok else "FAIL"
        print(f"- {status}: {step.name} ({step.details})")

    if args.cleanup:
        _request("DELETE", f"{api_base}/projects/{project_id}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
