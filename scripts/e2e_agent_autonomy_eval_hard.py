#!/usr/bin/env python3
"""Autonomy eval (hard): missing values, duplicates, tool usage enforcement."""
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
    name = f"autonomy-hard-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name})


def _setup_workspace(workspace_path: Path) -> dict[str, Any]:
    (workspace_path / "docs").mkdir(parents=True, exist_ok=True)
    (workspace_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (workspace_path / "artifacts" / "agent").mkdir(parents=True, exist_ok=True)

    (workspace_path / "docs" / "audit.txt").write_text(
        "Audit note: inspect duplicate ids and missing amounts.\n", encoding="utf-8"
    )

    csv_path = workspace_path / "data" / "raw" / "sales.csv"
    csv_path.write_text(
        "id,amount,region,notes\n"
        "1,10.0,west,ok\n"
        "2,,east,\n"
        "2,20.0,east,dup\n"
        "3,30.0,west,\n"
        "4,40.0,north,ok\n",
        encoding="utf-8",
    )

    db_path = workspace_path / "data" / "sales.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE sales (id INTEGER, amount REAL, region TEXT)")
        conn.executemany(
            "INSERT INTO sales (id, amount, region) VALUES (?, ?, ?)",
            [(1, 10.0, "west"), (2, 20.0, "east"), (2, 20.0, "east"), (3, 30.0, "west"), (4, 40.0, "north")],
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "missing_amount": 1,
        "missing_notes": 2,
        "duplicate_ids": 1,
        "db_total": 120.0,
    }


def _send_agent_chat(api_base: str, project_id: str, content: str) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": False, "auto_run": True},
    )


def _validate_report(workspace_path: Path, expected: dict[str, Any]) -> tuple[bool, str]:
    report_path = workspace_path / "artifacts" / "agent" / "autonomy-hard-report.md"
    if not report_path.exists():
        return False, "Report file missing"
    content = report_path.read_text(encoding="utf-8")
    required_sections = ["# Autonomy Hard Report", "## data_quality", "## anomaly_checks", "## db_summary"]
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
            return False, f"Missing token: {key}"
        raw_value = token_values[key]
        if key == "duplicate_ids":
            if raw_value.startswith("["):
                if "2" not in raw_value:
                    return False, f"duplicate_ids list mismatch: {raw_value}"
            else:
                try:
                    if int(raw_value) != value:
                        return False, f"duplicate_ids count mismatch: {raw_value}"
                except ValueError:
                    return False, f"duplicate_ids value invalid: {raw_value}"
        elif key == "db_total":
            try:
                if abs(float(raw_value) - float(value)) > 1e-3:
                    return False, f"db_total mismatch: {raw_value}"
            except ValueError:
                return False, f"db_total value invalid: {raw_value}"
        else:
            try:
                if int(raw_value) != value:
                    return False, f"{key} mismatch: {raw_value}"
            except ValueError:
                return False, f"{key} value invalid: {raw_value}"
    if "DUPLICATE_DETECTED" not in content:
        return False, "Missing DUPLICATE_DETECTED token"
    return True, "Report file valid"


def _validate_tool_usage(run: dict[str, Any]) -> tuple[bool, str]:
    tool_names = {entry.get("tool") for entry in run.get("log") or []}
    required = {"list_dir", "read_file", "search_text", "query_db", "write_file", "run_python", "write_markdown"}
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
        "You are an autonomous agent. Requirements: "
        "1) list files in docs/ and data/raw/. "
        "2) search docs/audit.txt for the word 'duplicate'. "
        "3) read data/raw/sales.csv and identify missing amounts and missing notes. "
        "4) query sqlite data/sales.db for total amount. "
        "5) write scripts/agent/autonomy_hard.py that computes duplicates and prints a markdown report. "
        "6) run that script. "
        "7) write artifacts/agent/autonomy-hard-report.md with sections # Autonomy Hard Report, ## data_quality, ## anomaly_checks, ## db_summary. "
        "Include tokens missing_amount, missing_notes, duplicate_ids, db_total as key=value lines. "
        "If duplicates exist, include literal token DUPLICATE_DETECTED."
    )

    _log_section("Agent")
    response = _send_agent_chat(api_base, project_id, prompt)
    run = response.get("run") or {}
    assistant = response["messages"][1]["content"]
    if "# Autonomy Hard Report" not in assistant:
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
