#!/usr/bin/env python3
"""Debug agent tool usage by analyzing run logs.

Usage:
  uv run python scripts/e2e_agent_tool_usage_debug.py

Env:
  API_BASE=http://127.0.0.1:8000
  AGENT_EVAL_TIMEOUT=240
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


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


def _request(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
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
    timeout: int = 120,
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
    boundary = f"----e2e-agent-boundary-{int(datetime.now().timestamp() * 1000)}"
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


def _health_check(api_base: str, timeout: int) -> None:
    status, _, data = _request("GET", f"{api_base}/health", timeout=timeout)
    if status != 200:
        raise RuntimeError(
            f"Health check failed ({status}): {data.decode('utf-8', errors='replace')}"
        )


def _create_project(api_base: str, timeout: int) -> dict[str, Any]:
    name = f"e2e-agent-tool-debug-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return _request_json("POST", f"{api_base}/projects", {"name": name}, timeout=timeout)


def _upload_dataset(api_base: str, project_id: str, filename: str, csv_bytes: bytes, timeout: int) -> dict[str, Any]:
    body, content_type = _encode_multipart("file", filename, csv_bytes)
    headers = {"Content-Type": content_type}
    status, _, data = _request(
        "POST",
        f"{api_base}/projects/{project_id}/datasets/upload",
        body=body,
        headers=headers,
        timeout=timeout,
    )
    parsed = json.loads(data.decode("utf-8")) if data else {}
    if status >= 400:
        raise RuntimeError(f"Upload failed ({status}): {parsed}")
    return parsed


def _send_agent_chat(
    api_base: str,
    project_id: str,
    content: str,
    timeout: int,
) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{api_base}/projects/{project_id}/agent/chat",
        {"content": content, "dataset_id": None, "safe_mode": False, "auto_run": True},
        timeout=timeout,
    )


def _build_sales_dataset() -> bytes:
    start = datetime(2025, 1, 1)
    regions = ["north", "south", "east", "west"]
    products = ["widget", "gizmo"]
    region_factor = {"north": 1.0, "south": 0.8, "east": 0.9, "west": 1.1}
    product_factor = {"widget": 1.2, "gizmo": 0.9}
    price = {"widget": 120.0, "gizmo": 80.0}

    rows: list[dict[str, Any]] = []
    for day_index in range(5):
        date_str = (start + timedelta(days=day_index)).strftime("%Y-%m-%d")
        discount = 0.05 * (day_index % 3)
        for region in regions:
            for product in products:
                base_units = int((day_index + 5) * region_factor[region] * product_factor[product])
                rows.append(
                    {
                        "date": date_str,
                        "region": region,
                        "product": product,
                        "units": base_units,
                        "unit_price": price[product],
                        "discount_pct": discount,
                    }
                )

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["date", "region", "product", "units", "unit_price", "discount_pct"],
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _build_marketing_dataset() -> bytes:
    start = datetime(2025, 1, 1)
    regions = ["north", "south", "east", "west"]
    rows: list[dict[str, Any]] = []
    for day_index in range(5):
        date_str = (start + timedelta(days=day_index)).strftime("%Y-%m-%d")
        for region in regions:
            rows.append({"date": date_str, "region": region, "spend": 1000 + day_index * 50})

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["date", "region", "spend"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _build_support_dataset() -> bytes:
    start = datetime(2025, 1, 1)
    regions = ["north", "south", "east", "west"]
    rows: list[dict[str, Any]] = []
    for day_index in range(5):
        date_str = (start + timedelta(days=day_index)).strftime("%Y-%m-%d")
        for region in regions:
            rows.append({"date": date_str, "region": region, "tickets": 5 + day_index, "severity": "low"})

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["date", "region", "tickets", "severity"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _summarize_tool_usage(run: dict[str, Any]) -> None:
    log = run.get("log") or []
    list_dir_entries = []
    for entry in log:
        if entry.get("tool") != "list_dir":
            continue
        args = entry.get("args") or {}
        output = entry.get("output") or {}
        list_dir_entries.append(
            {
                "args_path": args.get("path"),
                "output_path": output.get("path"),
                "status": entry.get("status"),
            }
        )

    _log_section("Tool usage summary")
    print(f"Total steps: {len(log)}")
    print(f"list_dir calls: {len(list_dir_entries)}")
    unique_outputs = sorted({item.get("output_path") for item in list_dir_entries})
    print(f"list_dir output paths: {unique_outputs}")
    print("\nFirst 8 list_dir calls:")
    for item in list_dir_entries[:8]:
        print(json.dumps(item, indent=2))

    no_path_calls = [item for item in list_dir_entries if not item.get("args_path")]
    if no_path_calls:
        print(f"\nlist_dir calls missing args.path: {len(no_path_calls)}")

    failed = [entry for entry in log if entry.get("status") == "failed"]
    if failed:
        print(f"\nFailed steps: {len(failed)}")
        for entry in failed[:5]:
            print(json.dumps({"tool": entry.get("tool"), "error": entry.get("error")}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug agent tool usage via run logs.")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("AGENT_EVAL_TIMEOUT", "240")))
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    steps: list[EvalStep] = []

    try:
        _health_check(api_base, args.timeout)
        steps.append(EvalStep("health_check", True, "API reachable"))

        project = _create_project(api_base, args.timeout)
        project_id = project["id"]
        workspace_path = project["workspace_path"]
        steps.append(EvalStep("create_project", True, f"project_id={project_id}"))

        sales_bytes = _build_sales_dataset()
        marketing_bytes = _build_marketing_dataset()
        support_bytes = _build_support_dataset()

        _upload_dataset(api_base, project_id, "sales.csv", sales_bytes, args.timeout)
        _upload_dataset(api_base, project_id, "marketing.csv", marketing_bytes, args.timeout)
        _upload_dataset(api_base, project_id, "support.csv", support_bytes, args.timeout)
        steps.append(EvalStep("upload_datasets", True, "sales/marketing/support uploaded"))

        prompt = (
            "You are an autonomous analyst. We uploaded three datasets to the project: sales.csv, marketing.csv, support.csv. "
            "Your task is to explore the datasets, make hypotheses, and produce a rigorous markdown report. "
            "You decide the steps and tools. Use any available tools as needed. "
            "Use only Python standard library modules (csv, statistics, collections); do not use pandas. "
            "The datasets are stored under data/raw/ as sales.csv, marketing.csv, support.csv. "
            "Schema hints: sales.csv columns = date, region, product, units, unit_price, discount_pct. "
            "marketing.csv columns = date, region, spend. support.csv columns = date, region, tickets, severity. "
            "Note: discount_pct is a fraction (0.00-0.10), not a percentage, and severity is text (low/high), not numeric. "
            "You must run a Python analysis step to compute metrics from the CSVs before writing the report. "
            "Write the report with write_markdown to artifacts/agent/tool-usage-report.md and print the full report to stdout in the run_python step."
        )

        _log_section("Chat prompt")
        print(prompt)
        chat_response = _send_agent_chat(api_base, project_id, prompt, timeout=args.timeout)
        _log_section("Chat response")
        _log_json("Chat response", chat_response)
        run = chat_response.get("run")
        if not run:
            raise RuntimeError("Chat response missing run payload")

        _summarize_tool_usage(run)

        if run.get("status") != "completed":
            raise RuntimeError(f"Agent run status: {run.get('status')}")

        steps.append(EvalStep("agent_tool_debug", True, "run completed"))

    except Exception as exc:  # noqa: BLE001 - keep top-level errors concise
        steps.append(EvalStep("agent_tool_debug", False, str(exc)))

    print("\nAgent tool debug results")
    print("========================")
    for step in steps:
        status = "PASS" if step.ok else "FAIL"
        print(f"- {status}: {step.name} ({step.details})")

    failed = [step for step in steps if not step.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
