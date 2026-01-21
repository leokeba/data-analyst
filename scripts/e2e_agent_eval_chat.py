#!/usr/bin/env python3
"""Chat eval: two-step interaction with report validation."""
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
    name = f"chat-eval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name})


def _setup_workspace(workspace_path: Path) -> dict[str, float]:
    (workspace_path / "docs").mkdir(parents=True, exist_ok=True)
    (workspace_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (workspace_path / "artifacts" / "agent").mkdir(parents=True, exist_ok=True)

    (workspace_path / "docs" / "context.txt").write_text(
        "Context: revenue dip observed in east.\n", encoding="utf-8"
    )
    (workspace_path / "data" / "raw" / "sales.csv").write_text(
        "id,amount,region\n1,10.0,west\n2,15.0,east\n3,20.0,east\n",
        encoding="utf-8",
    )

    db_path = workspace_path / "data" / "sales.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE sales (id INTEGER, amount REAL, region TEXT)")
        conn.executemany(
            "INSERT INTO sales (id, amount, region) VALUES (?, ?, ?)",
            [(1, 10.0, "west"), (2, 15.0, "east"), (3, 20.0, "east")],
        )
        conn.commit()
    finally:
        conn.close()

    return {"db_total": 45.0}


def _send_agent_chat(api_base: str, project_id: str, content: str) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": False, "auto_run": True},
    )


def _validate_report(workspace_path: Path, expected: dict[str, float]) -> tuple[bool, str]:
    report_path = workspace_path / "artifacts" / "agent" / "chat-report.md"
    if not report_path.exists():
        return False, "Report file missing"
    content = report_path.read_text(encoding="utf-8")
    lowered = content.lower()
    if "# chat report" not in lowered:
        return False, "Missing section: # Chat Report"
    if "## summary" not in lowered:
        return False, "Missing section: ## summary"
    if "## db summary" not in lowered and "## db_summary" not in lowered:
        return False, "Missing section: ## db_summary"
    token_value = None
    for line in content.splitlines():
        if "db_total" not in line:
            continue
        cleaned = line.replace("*", "").replace("`", "")
        parts = cleaned.split("db_total", 1)
        if len(parts) < 2:
            continue
        after = parts[1]
        digits = "".join(ch for ch in after if ch.isdigit() or ch == ".")
        if digits:
            try:
                token_value = float(digits)
            except ValueError:
                continue
    if token_value is None:
        return False, "Missing token: db_total"
    if abs(token_value - expected["db_total"]) > 1e-3:
        return False, f"db_total value mismatch: {token_value}"
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

    prompt1 = "List files in docs/ and summarize docs/context.txt in one sentence."
    _log_section("Chat 1")
    resp1 = _send_agent_chat(api_base, project_id, prompt1)
    if "docs" not in resp1["messages"][1]["content"]:
        steps.append(EvalStep("chat1", False, "Missing docs summary"))
    else:
        steps.append(EvalStep("chat1", True, "Chat 1 responded"))

    prompt2 = (
        "Using the workspace data, query sqlite data/sales.db for total amount, "
        "then write artifacts/agent/chat-report.md with sections # Chat Report, ## summary, ## db_summary. "
        "Include token db_total as key=value."
    )
    _log_section("Chat 2")
    resp2 = _send_agent_chat(api_base, project_id, prompt2)
    if "# Chat Report" not in resp2["messages"][1]["content"]:
        steps.append(EvalStep("chat2", False, "Chat report missing"))
    else:
        steps.append(EvalStep("chat2", True, "Chat 2 responded"))

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
