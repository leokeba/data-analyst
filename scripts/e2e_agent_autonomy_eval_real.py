#!/usr/bin/env python3
"""Streamlined autonomy eval: files + sqlite + python report.

Usage:
  uv run python scripts/e2e_agent_autonomy_eval_real.py [--api-base URL] [--cleanup]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = int(os.environ.get("AGENT_EVAL_TIMEOUT", "180"))


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
    name = f"autonomy-real-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name})


def _cleanup_project(api_base: str, project_id: str) -> None:
    _request("DELETE", f"{api_base}/projects/{project_id}")


def _setup_workspace(workspace_path: Path) -> None:
    (workspace_path / "docs").mkdir(parents=True, exist_ok=True)
    (workspace_path / "analysis").mkdir(parents=True, exist_ok=True)
    (workspace_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (workspace_path / "artifacts" / "agent").mkdir(parents=True, exist_ok=True)

    (workspace_path / "docs" / "sample.txt").write_text(
        "Customer notes for Q1. Region focus: west.\n", encoding="utf-8"
    )
    (workspace_path / "analysis" / "notes.md").write_text(
        "# Analyst Notes\n- Prioritize duplicate IDs\n", encoding="utf-8"
    )
    (workspace_path / "data" / "raw" / "sales.csv").write_text(
        "id,amount,region\n1,120.5,west\n2,99.9,east\n3,240.0,west\n3,75.0,south\n",
        encoding="utf-8",
    )

    db_path = workspace_path / "data" / "sales.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE sales (id INTEGER, amount REAL, region TEXT)")
        conn.executemany(
            "INSERT INTO sales (id, amount, region) VALUES (?, ?, ?)",
            [(1, 120.5, "west"), (2, 99.9, "east"), (3, 240.0, "west"), (3, 75.0, "south")],
        )
        conn.commit()
    finally:
        conn.close()


def _send_agent_chat(api_base: str, project_id: str, content: str) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": False, "auto_run": True},
    )


def _validate_report(workspace_path: Path) -> tuple[bool, str]:
    report_path = workspace_path / "artifacts" / "agent" / "autonomy-report.md"
    if not report_path.exists():
        return False, "Report file missing"
    content = report_path.read_text(encoding="utf-8")
    required_sections = ["# Autonomy Report", "## file_scan", "## db_summary", "## data_report"]
    for section in required_sections:
        if section not in content:
            return False, f"Missing section: {section}"
    return True, "Report file valid"


def _validate_python_runs(workspace_path: Path) -> tuple[bool, str]:
    artifacts = list((workspace_path / "artifacts" / "agent").glob("agent-python-*.txt"))
    if not artifacts:
        return False, "No python run output artifacts found"
    return True, f"Found {len(artifacts)} python run artifacts"


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
    _setup_workspace(workspace_path)
    steps.append(EvalStep("create_project", True, f"project_id={project_id}"))

    prompt = (
        "You are an autonomous agent. Do the following: "
        "1) list files in docs/ and analysis/. "
        "2) read docs/sample.txt and summarize one sentence. "
        "3) query the sqlite database at data/sales.db to compute total and average amount. "
        "4) write a Python script at scripts/agent/autonomy_report.py that reads data/raw/sales.csv "
        "and prints a markdown report with sections: # Autonomy Report, ## data_report. "
        "5) run that script. "
        "6) write artifacts/agent/autonomy-report.md with sections # Autonomy Report, ## file_scan, "
        "## db_summary, ## data_report, including the file summary and db totals. "
        "Return the markdown report in your final response."
    )

    _log_section("Agent")
    response = _send_agent_chat(api_base, project_id, prompt)
    assistant = response["messages"][1]["content"]
    if "# Autonomy Report" not in assistant:
        steps.append(EvalStep("assistant_report", False, "Assistant did not return report"))
    else:
        steps.append(EvalStep("assistant_report", True, "Assistant returned report"))

    ok_report, report_details = _validate_report(workspace_path)
    steps.append(EvalStep("report_file", ok_report, report_details))

    ok_python, python_details = _validate_python_runs(workspace_path)
    steps.append(EvalStep("python_runs", ok_python, python_details))

    _log_section("Results")
    failures = [step for step in steps if not step.ok]
    for step in steps:
        status = "PASS" if step.ok else "FAIL"
        print(f"- {status}: {step.name} ({step.details})")

    if args.cleanup:
        _cleanup_project(api_base, project_id)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
