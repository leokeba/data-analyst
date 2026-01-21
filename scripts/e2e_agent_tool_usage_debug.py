#!/usr/bin/env python3
"""Tool usage eval: verify required tool calls and artifacts."""
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
    name = f"tool-eval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name})


def _setup_workspace(workspace_path: Path) -> dict[str, float]:
    (workspace_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (workspace_path / "artifacts" / "agent").mkdir(parents=True, exist_ok=True)
    (workspace_path / "scripts").mkdir(parents=True, exist_ok=True)

    raw_path = workspace_path / "data" / "raw" / "inventory.csv"
    raw_path.write_text(
        "sku,qty\nA,5\nB,3\nC,7\n",
        encoding="utf-8",
    )

    db_path = workspace_path / "data" / "inventory.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE inventory (sku TEXT, qty INTEGER)")
        conn.executemany(
            "INSERT INTO inventory (sku, qty) VALUES (?, ?)",
            [("A", 5), ("B", 3), ("C", 7)],
        )
        conn.commit()
    finally:
        conn.close()

    return {"total_qty": 15}


def _send_agent_chat(api_base: str, project_id: str, content: str) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": False, "auto_run": True},
    )


def _fetch_agent_runs(api_base: str, project_id: str) -> list[dict[str, Any]]:
    return _request_json("GET", f"{api_base}/projects/{project_id}/agent/runs").get(
        "data", []
    )


def _extract_tool_names(run: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for entry in run.get("log") or []:
        name = entry.get("tool") or ""
        if name:
            names.append(name)
    for entry in run.get("tool_runs", []):
        name = entry.get("name") or ""
        if name:
            names.append(name)
    return names


def _validate_artifacts(workspace_path: Path, expected: dict[str, float]) -> tuple[bool, str]:
    report_path = workspace_path / "artifacts" / "agent" / "tool-usage-report.md"
    script_path = workspace_path / "scripts" / "inventory_summary.py"
    if not report_path.exists():
        return False, "Report missing"
    if not script_path.exists():
        return False, "Script missing"
    report = report_path.read_text(encoding="utf-8")
    token = f"total_qty={expected['total_qty']}"
    if token not in report:
        return False, f"Missing token: {token}"
    if "## Tool Usage" not in report:
        return False, "Missing Tool Usage section"
    return True, "Artifacts valid"


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
        "You must use list_dir, read_file, list_db_tables, query_db, run_python, and write_markdown. "
        "1) Inspect data/raw/inventory.csv. "
        "2) Query data/inventory.db for total qty. "
        "3) Create scripts/inventory_summary.py (exact path) to read the CSV and print total qty. "
        "4) Execute scripts/inventory_summary.py. "
        "5) Write artifacts/agent/tool-usage-report.md with sections # Tool Usage Report and ## Tool Usage. "
        "Include token total_qty=<value>."
    )
    _log_section("Chat")
    response = _send_agent_chat(api_base, project_id, prompt)
    run = response.get("run") or {}
    tool_names = set(_extract_tool_names(run))
    runs = _fetch_agent_runs(api_base, project_id)
    for item in runs:
        tool_names.update(_extract_tool_names(item))
    required_tools = {
        "list_dir",
        "read_file",
        "list_db_tables",
        "query_db",
        "run_python",
        "write_markdown",
    }
    missing = required_tools - set(tool_names)
    if missing:
        steps.append(EvalStep("tool_usage", False, f"Missing tools: {sorted(missing)}"))
    else:
        steps.append(EvalStep("tool_usage", True, "Required tools used"))

    ok_artifacts, artifact_details = _validate_artifacts(workspace_path, expected)
    steps.append(EvalStep("artifacts", ok_artifacts, artifact_details))

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
