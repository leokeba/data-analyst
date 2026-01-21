#!/usr/bin/env python3
"""Autonomy eval (medium): file scan + sqlite + python report with tool checks."""
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

DEFAULT_TIMEOUT = int(os.environ.get("AGENT_EVAL_TIMEOUT", "200"))


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
    name = f"autonomy-medium-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name})


def _setup_workspace(workspace_path: Path) -> dict[str, float]:
    (workspace_path / "docs").mkdir(parents=True, exist_ok=True)
    (workspace_path / "analysis").mkdir(parents=True, exist_ok=True)
    (workspace_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (workspace_path / "artifacts" / "agent").mkdir(parents=True, exist_ok=True)

    (workspace_path / "docs" / "brief.txt").write_text(
        "Ops note: focus on west region anomalies.\n", encoding="utf-8"
    )
    (workspace_path / "analysis" / "todo.md").write_text(
        "# Tasks\n- Validate duplicate IDs\n", encoding="utf-8"
    )

    csv_path = workspace_path / "data" / "raw" / "sales.csv"
    csv_path.write_text(
        "id,amount,region\n1,10.0,west\n2,20.0,east\n2,20.0,east\n3,30.0,west\n",
        encoding="utf-8",
    )

    db_path = workspace_path / "data" / "sales.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE sales (id INTEGER, amount REAL, region TEXT)")
        conn.executemany(
            "INSERT INTO sales (id, amount, region) VALUES (?, ?, ?)",
            [(1, 10.0, "west"), (2, 20.0, "east"), (2, 20.0, "east"), (3, 30.0, "west")],
        )
        conn.commit()
    finally:
        conn.close()

    total = 80.0
    avg = total / 4.0
    return {"db_total": total, "db_avg": avg}


def _send_agent_chat(api_base: str, project_id: str, content: str) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": False, "auto_run": True},
    )


def _validate_report(workspace_path: Path, expected: dict[str, float]) -> tuple[bool, str]:
    report_path = workspace_path / "artifacts" / "agent" / "autonomy-report.md"
    if not report_path.exists():
        return False, "Report file missing"
    content = report_path.read_text(encoding="utf-8")
    required_sections = ["# Autonomy Report", "## file_scan", "## db_summary", "## data_report", "## notes"]
    for section in required_sections:
        if section not in content:
            return False, f"Missing section: {section}"
    token_values: dict[str, float] = {}
    for line in content.splitlines():
        cleaned = line.strip().lstrip("-").strip()
        for key in expected:
            prefix = f"{key}="
            if cleaned.startswith(prefix):
                try:
                    token_values[key] = float(cleaned.split("=", 1)[1])
                except ValueError:
                    continue
    for key, value in expected.items():
        if key not in token_values:
            return False, f"Missing token: {key}"
        if abs(token_values[key] - value) > 1e-3:
            return False, f"Token {key} value mismatch: {token_values[key]}"
    return True, "Report file valid"


def _validate_tool_usage(run: dict[str, Any]) -> tuple[bool, str]:
    tool_names = {entry.get("tool") for entry in run.get("log") or []}
    required = {"list_dir", "read_file", "query_db", "write_file", "run_python", "write_markdown"}
    missing = required - tool_names
    if missing:
        return False, f"Missing tools: {', '.join(sorted(missing))}"
    return True, "Tool usage valid"


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
        "You are an autonomous agent. Do the following: "
        "1) list files in docs/ and analysis/. "
        "2) read docs/brief.txt and summarize one sentence. "
        "3) query sqlite data/sales.db to compute total and average amount. "
        "4) write a Python script at scripts/agent/autonomy_report.py that reads data/raw/sales.csv "
        "and prints a markdown report with sections: # Autonomy Report and ## data_report. "
        "5) run that script. "
        "6) write artifacts/agent/autonomy-report.md with sections # Autonomy Report, ## file_scan, "
        "## db_summary, ## data_report, ## notes. "
        "In ## db_summary include exact key=value lines using lowercase tokens db_total= and db_avg=. "
        "Return the markdown report in your final response."
    )

    _log_section("Agent")
    response = _send_agent_chat(api_base, project_id, prompt)
    run = response.get("run") or {}
    assistant = response["messages"][1]["content"]
    if "# Autonomy Report" not in assistant:
        steps.append(EvalStep("assistant_report", False, "Assistant did not return report"))
    else:
        steps.append(EvalStep("assistant_report", True, "Assistant returned report"))

    ok_tools, tool_details = _validate_tool_usage(run)
    steps.append(EvalStep("tool_usage", ok_tools, tool_details))

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
