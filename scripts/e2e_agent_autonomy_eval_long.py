#!/usr/bin/env python3
"""Long-horizon autonomy eval: multi-source joins + plotting.

Usage:
  uv run python scripts/e2e_agent_autonomy_eval_long.py [--api-base URL] [--cleanup]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = int(os.environ.get("AGENT_EVAL_TIMEOUT", "300"))


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
    name = f"autonomy-long-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name})


def _cleanup_project(api_base: str, project_id: str) -> None:
    _request("DELETE", f"{api_base}/projects/{project_id}")


def _setup_workspace(workspace_path: Path) -> dict[str, Any]:
    (workspace_path / "docs").mkdir(parents=True, exist_ok=True)
    (workspace_path / "analysis").mkdir(parents=True, exist_ok=True)
    (workspace_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (workspace_path / "artifacts" / "agent").mkdir(parents=True, exist_ok=True)

    (workspace_path / "docs" / "brief.txt").write_text(
        "Ops note: compare machines, include downtime and energy usage.\n",
        encoding="utf-8",
    )
    (workspace_path / "analysis" / "context.md").write_text(
        "# Context\n- Use all data sources.\n- Include a plot in the report.\n",
        encoding="utf-8",
    )

    (workspace_path / "data" / "raw" / "machine_meta.json").write_text(
        json.dumps(
            [
                {"machine_id": "M-1", "line": "A", "location": "north"},
                {"machine_id": "M-2", "line": "B", "location": "south"},
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    downtime_rows = [
        {"machine_id": "M-2", "start_ts": "2026-01-01T01:30:00", "end_ts": "2026-01-01T02:30:00", "reason": "overheat"},
        {"machine_id": "M-1", "start_ts": "2026-01-02T00:15:00", "end_ts": "2026-01-02T00:45:00", "reason": "inspection"},
    ]
    downtime_path = workspace_path / "data" / "raw" / "downtime.csv"
    with downtime_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=downtime_rows[0].keys())
        writer.writeheader()
        writer.writerows(downtime_rows)

    db_path = workspace_path / "data" / "plant.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE sensors (ts TEXT, machine_id TEXT, temp REAL, pressure REAL)"
        )
        conn.execute(
            "CREATE TABLE energy (ts TEXT, machine_id TEXT, kwh REAL)"
        )
        conn.execute(
            "CREATE TABLE maintenance (machine_id TEXT, maint_ts TEXT, event TEXT)"
        )
        conn.execute(
            "CREATE TABLE shifts (shift_id TEXT, start_ts TEXT, end_ts TEXT, supervisor TEXT)"
        )
        sensor_rows = [
            ("2026-01-01T00:00:00", "M-1", 70.0, 30.1),
            ("2026-01-01T01:00:00", "M-1", 72.0, 30.4),
            ("2026-01-01T02:00:00", "M-1", 71.0, 30.2),
            ("2026-01-02T00:00:00", "M-1", 68.0, 29.9),
            ("2026-01-02T01:00:00", "M-1", 69.0, 30.0),
            ("2026-01-02T02:00:00", "M-1", 70.0, 30.1),
            ("2026-01-01T00:00:00", "M-2", 80.0, 31.1),
            ("2026-01-01T01:00:00", "M-2", 82.0, 31.4),
            ("2026-01-01T02:00:00", "M-2", 85.0, 31.6),
            ("2026-01-02T00:00:00", "M-2", 78.0, 30.8),
            ("2026-01-02T01:00:00", "M-2", 79.0, 30.9),
            ("2026-01-02T02:00:00", "M-2", 81.0, 31.0),
        ]
        energy_rows = [
            ("2026-01-01T00:00:00", "M-1", 10.0),
            ("2026-01-01T01:00:00", "M-1", 11.0),
            ("2026-01-01T02:00:00", "M-1", 12.0),
            ("2026-01-02T00:00:00", "M-1", 9.0),
            ("2026-01-02T01:00:00", "M-1", 10.0),
            ("2026-01-02T02:00:00", "M-1", 11.0),
            ("2026-01-01T00:00:00", "M-2", 14.0),
            ("2026-01-01T01:00:00", "M-2", 15.0),
            ("2026-01-01T02:00:00", "M-2", 16.0),
            ("2026-01-02T00:00:00", "M-2", 13.0),
            ("2026-01-02T01:00:00", "M-2", 14.0),
            ("2026-01-02T02:00:00", "M-2", 15.0),
        ]
        maintenance_rows = [
            ("M-1", "2026-01-01T03:00:00", "calibration"),
            ("M-2", "2026-01-02T03:00:00", "cooling_flush"),
        ]
        shift_rows = [
            ("A", "2026-01-01T00:00:00", "2026-01-01T12:00:00", "Lopez"),
            ("B", "2026-01-01T12:00:00", "2026-01-02T00:00:00", "Singh"),
        ]
        conn.executemany("INSERT INTO sensors VALUES (?, ?, ?, ?)", sensor_rows)
        conn.executemany("INSERT INTO energy VALUES (?, ?, ?)", energy_rows)
        conn.executemany("INSERT INTO maintenance VALUES (?, ?, ?)", maintenance_rows)
        conn.executemany("INSERT INTO shifts VALUES (?, ?, ?, ?)", shift_rows)
        conn.commit()
    finally:
        conn.close()

    total_sensor_rows = len(sensor_rows)
    joined_rows = len(sensor_rows)
    peak_temp_machine = "M-2"
    downtime_minutes = 90
    avg_kwh_m2 = sum(row[2] for row in energy_rows if row[1] == "M-2") / 6.0
    return {
        "total_sensor_rows": total_sensor_rows,
        "joined_rows": joined_rows,
        "peak_temp_machine": peak_temp_machine,
        "downtime_minutes": downtime_minutes,
        "avg_kwh_m2": avg_kwh_m2,
    }


def _send_agent_chat(api_base: str, project_id: str, content: str) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": False, "auto_run": True},
    )


def _extract_tokens(content: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    normalized = content.replace("\\n", "\n")
    for line in normalized.splitlines():
        cleaned = line.strip().lstrip("-").strip()
        if "=" not in cleaned:
            continue
        key, value = cleaned.split("=", 1)
        tokens[key.strip()] = value.strip()
    for line in normalized.splitlines():
        cleaned = line.strip().lstrip("-").strip()
        lower = cleaned.lower()
        if "total sensor rows" in lower:
            tokens["total_sensor_rows"] = cleaned.split(":", 1)[-1].strip()
        if "rows joined" in lower or "joined rows" in lower:
            if ":" in cleaned:
                tokens["joined_rows"] = cleaned.split(":", 1)[-1].strip()
            else:
                match = re.search(r"(\d+)", cleaned)
                if match:
                    tokens["joined_rows"] = match.group(1)
        if "peak temperature machine" in lower:
            tokens["peak_temp_machine"] = cleaned.split(":", 1)[-1].strip()
        if "total downtime minutes" in lower:
            tokens["downtime_minutes"] = cleaned.split(":", 1)[-1].strip()
        if "average kwh" in lower and "m-2" in lower:
            tokens["avg_kwh_m2"] = cleaned.split(":", 1)[-1].strip()
    return tokens


def _validate_report(workspace_path: Path, expected: dict[str, Any]) -> tuple[bool, str]:
    report_path = workspace_path / "artifacts" / "agent" / "long-horizon-report.md"
    if not report_path.exists():
        return False, "Report file missing"
    content = report_path.read_text(encoding="utf-8")
    content_lower = content.lower()
    required_sections = [
        "# long horizon report",
        "## data_sources",
        "## joins",
        "## metrics",
        "## plot",
        "## findings",
    ]
    for section in required_sections:
        if section not in content_lower:
            return False, f"Missing section: {section}"
    if "![temperature_trend](temperature_trend.png)" not in content_lower:
        return False, "Missing plot image reference"
    tokens = _extract_tokens(content)
    if tokens.get("total_sensor_rows") != str(expected["total_sensor_rows"]):
        return False, "total_sensor_rows mismatch"
    if tokens.get("joined_rows") != str(expected["joined_rows"]):
        return False, "joined_rows mismatch"
    if tokens.get("peak_temp_machine") != expected["peak_temp_machine"]:
        return False, "peak_temp_machine mismatch"
    if tokens.get("downtime_minutes") != str(expected["downtime_minutes"]):
        return False, "downtime_minutes mismatch"
    avg = tokens.get("avg_kwh_m2")
    try:
        if avg is None or abs(float(avg) - expected["avg_kwh_m2"]) > 1e-3:
            return False, "avg_kwh_m2 mismatch"
    except ValueError:
        return False, "avg_kwh_m2 invalid"
    plot_path = workspace_path / "artifacts" / "agent" / "temperature_trend.png"
    if not plot_path.exists():
        return False, "Plot image missing"
    return True, "Report file valid"


def _validate_tool_usage(run: dict[str, Any]) -> tuple[bool, str]:
    tool_names = {entry.get("tool") for entry in run.get("log") or []}
    required = {
        "list_dir",
        "read_file",
        "list_db_tables",
        "query_db",
        "run_python",
        "write_markdown",
    }
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
        "You are an autonomous agent performing a long-horizon industrial analysis. "
        "Use these data sources: sqlite data/plant.db (tables sensors, energy, maintenance, shifts), "
        "CSV data/raw/downtime.csv, JSON data/raw/machine_meta.json, docs/brief.txt. "
        "Steps: 1) list files in docs/, analysis/, data/raw/. "
        "2) list sqlite tables in data/plant.db using list_db_tables. "
        "3) read docs/brief.txt and data/raw/machine_meta.json. "
        "4) inspect downtime.csv and compute total downtime minutes (sum of end-start in minutes). "
        "Use the two events: 01:30-02:30 (60 min) and 00:15-00:45 (30 min). "
        "5) query sqlite: SELECT COUNT(*) AS total_sensor_rows FROM sensors. "
        "6) query sqlite join: SELECT s.ts, s.machine_id, s.temp, e.kwh FROM sensors s "
        "JOIN energy e ON s.ts = e.ts AND s.machine_id = e.machine_id. "
        "7) query sqlite for peak temp machine: SELECT machine_id FROM sensors ORDER BY temp DESC LIMIT 1. "
        "8) query sqlite for avg kwh M-2: SELECT AVG(kwh) AS avg_kwh_m2 FROM energy WHERE machine_id = 'M-2'. "
        "9) write scripts/agent/long_horizon_analysis.py using write_file (not write_markdown) to load csv/json/db, perform joins, "
        "and generate a matplotlib plot saved to artifacts/agent/temperature_trend.png. "
        "10) run that script. "
        "11) write_markdown artifacts/agent/long-horizon-report.md with sections # Long Horizon Report, "
        "## data_sources, ## joins, ## metrics, ## plot, ## findings. "
        "Include tokens total_sensor_rows, joined_rows, peak_temp_machine, downtime_minutes, avg_kwh_m2 as key=value lines. "
        "Use exact headers: # Long Horizon Report, ## data_sources, ## joins, ## metrics, ## plot, ## findings. "
        "joined_rows should equal the join row count from sensors+energy (12). "
        "In ## plot include ![temperature_trend](temperature_trend.png). "
        "Return the markdown report in your final response."
    )

    _log_section("Agent")
    response = _send_agent_chat(api_base, project_id, prompt)
    run = response.get("run") or {}
    assistant = response["messages"][1]["content"]
    if "# Long Horizon Report" not in assistant:
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
        _cleanup_project(api_base, project_id)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
