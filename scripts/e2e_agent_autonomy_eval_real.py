#!/usr/bin/env python3
"""Real autonomy evaluation for agent chat.

This script generates multiple datasets, uploads them to the project, and asks the
agent to produce a verifiable analytical report with minimal guidance.

Usage:
  uv run python scripts/e2e_agent_autonomy_eval_real.py

Optional environment variables:
  API_BASE=http://127.0.0.1:8000
  AGENT_EVAL_TIMEOUT=240
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    name = f"e2e-autonomy-real-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
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


def _build_sales_dataset() -> tuple[bytes, list[dict[str, Any]]]:
    start = datetime(2025, 1, 1)
    regions = ["north", "south", "east", "west"]
    products = ["widget", "gizmo"]
    region_factor = {"north": 1.0, "south": 0.8, "east": 0.9, "west": 1.1}
    product_factor = {"widget": 1.2, "gizmo": 0.9}
    price = {"widget": 120.0, "gizmo": 80.0}

    rows: list[dict[str, Any]] = []
    for day_index in range(10):
        date_str = (start + timedelta(days=day_index)).strftime("%Y-%m-%d")
        discount = 0.05 * (day_index % 3)
        for region in regions:
            for product in products:
                base_units = int((day_index + 5) * region_factor[region] * product_factor[product])
                units = base_units
                if date_str == "2025-01-07" and region == "east" and product == "widget":
                    units = -5
                rows.append(
                    {
                        "date": date_str,
                        "region": region,
                        "product": product,
                        "units": units,
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
    return output.getvalue().encode("utf-8"), rows


def _build_marketing_dataset() -> tuple[bytes, list[dict[str, Any]]]:
    start = datetime(2025, 1, 1)
    regions = ["north", "south", "east", "west"]
    region_offset = {"north": 200, "south": 100, "east": 0, "west": 300}
    rows: list[dict[str, Any]] = []
    for day_index in range(10):
        date_str = (start + timedelta(days=day_index)).strftime("%Y-%m-%d")
        for region in regions:
            if date_str == "2025-01-04" and region == "east":
                continue
            spend = 1000 + day_index * 50 + region_offset[region]
            rows.append({"date": date_str, "region": region, "spend": spend})

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["date", "region", "spend"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8"), rows


def _build_support_dataset() -> tuple[bytes, list[dict[str, Any]]]:
    start = datetime(2025, 1, 1)
    regions = ["north", "south", "east", "west"]
    rows: list[dict[str, Any]] = []
    for day_index in range(10):
        date_str = (start + timedelta(days=day_index)).strftime("%Y-%m-%d")
        for region in regions:
            tickets = 5 + day_index + (2 if region == "west" else 0)
            if date_str == "2025-01-07" and region == "east":
                tickets += 20
            severity = "high" if tickets >= 20 else "low"
            rows.append({"date": date_str, "region": region, "tickets": tickets, "severity": severity})

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["date", "region", "tickets", "severity"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8"), rows


def _pearson(values_x: list[float], values_y: list[float]) -> float:
    if len(values_x) != len(values_y) or len(values_x) < 2:
        return 0.0
    mean_x = sum(values_x) / len(values_x)
    mean_y = sum(values_y) / len(values_y)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(values_x, values_y))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in values_x))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in values_y))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def _expected_metrics(sales_rows: list[dict[str, Any]], marketing_rows: list[dict[str, Any]]) -> dict[str, Any]:
    revenue_by_region: dict[str, float] = {"north": 0.0, "south": 0.0, "east": 0.0, "west": 0.0}
    revenue_by_product: dict[str, float] = {"widget": 0.0, "gizmo": 0.0}
    refund_date = ""
    refund_region = ""

    for row in sales_rows:
        revenue = float(row["units"]) * float(row["unit_price"]) * (1 - float(row["discount_pct"]))
        revenue_by_region[row["region"]] += revenue
        revenue_by_product[row["product"]] += revenue
        if revenue < 0:
            refund_date = row["date"]
            refund_region = row["region"]

    top_product = max(revenue_by_product, key=revenue_by_product.get)

    spend_by_date: dict[str, float] = {}
    revenue_by_date: dict[str, float] = {}
    for row in sales_rows:
        revenue = float(row["units"]) * float(row["unit_price"]) * (1 - float(row["discount_pct"]))
        revenue_by_date[row["date"]] = revenue_by_date.get(row["date"], 0.0) + revenue
    for row in marketing_rows:
        spend_by_date[row["date"]] = spend_by_date.get(row["date"], 0.0) + float(row["spend"])

    dates = sorted(revenue_by_date.keys())
    revenue_series = [revenue_by_date[d] for d in dates]
    spend_series = [spend_by_date.get(d, 0.0) for d in dates]
    correlation = _pearson(spend_series, revenue_series)

    return {
        "total_revenue_by_region": revenue_by_region,
        "top_product": top_product,
        "refund_date": refund_date,
        "refund_region": refund_region,
        "marketing_revenue_correlation": correlation,
        "missing_marketing_rows": 1,
    }


def _find_report_path(run: dict[str, Any], workspace_path: str) -> str | None:
    for entry in run.get("log") or []:
        if entry.get("tool") in {"write_markdown", "write_file"}:
            output = entry.get("output") or {}
            path = output.get("path")
            if isinstance(path, str) and path.endswith("autonomy-real-report.md"):
                if path.startswith("/"):
                    return path
                return str(Path(workspace_path) / path)
    return None


def _run_has_failures(run: dict[str, Any]) -> bool:
    if run.get("status") != "completed":
        return True
    for entry in run.get("log") or []:
        if entry.get("status") == "failed":
            return True
    return False


def _section_has_content(report: str, header: str) -> bool:
    lines = report.splitlines()
    try:
        start_idx = lines.index(header)
    except ValueError:
        return False
    for line in lines[start_idx + 1 :]:
        if line.startswith("## ") or line.startswith("# "):
            break
        if line.strip():
            return True
    return False


def _read_report(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real autonomy evaluation against the agent API.")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("AGENT_EVAL_TIMEOUT", "420")))
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    steps: list[EvalStep] = []

    try:
        _health_check(api_base, args.timeout)
        steps.append(EvalStep("health_check", True, "API reachable"))
        _log_section("API")
        print(f"API base: {api_base}")

        project = _create_project(api_base, args.timeout)
        project_id = project["id"]
        workspace_path = project["workspace_path"]
        steps.append(EvalStep("create_project", True, f"project_id={project_id}"))
        _log_section("Project")
        _log_json("Project", project)

        sales_bytes, sales_rows = _build_sales_dataset()
        marketing_bytes, marketing_rows = _build_marketing_dataset()
        support_bytes, _ = _build_support_dataset()

        sales_ds = _upload_dataset(api_base, project_id, "sales.csv", sales_bytes, args.timeout)
        marketing_ds = _upload_dataset(api_base, project_id, "marketing.csv", marketing_bytes, args.timeout)
        support_ds = _upload_dataset(api_base, project_id, "support.csv", support_bytes, args.timeout)
        steps.append(EvalStep("upload_datasets", True, "sales/marketing/support uploaded"))
        _log_section("Datasets")
        _log_json("Sales dataset", sales_ds)
        _log_json("Marketing dataset", marketing_ds)
        _log_json("Support dataset", support_ds)

        expected = _expected_metrics(sales_rows, marketing_rows)
        report_path = f"{workspace_path}/artifacts/agent/autonomy-real-report.md"
        report_path_rel = "artifacts/agent/autonomy-real-report.md"

        chat_prompt = (
            "You are an autonomous analyst. We uploaded three datasets to the project: sales.csv, marketing.csv, support.csv. "
            "Your task is to explore the datasets, make hypotheses, and produce a rigorous markdown report. "
            "You decide the steps and tools. Use any available tools as needed. "
            "Use only Python standard library modules (csv, statistics, collections); do not use pandas. "
            "The datasets are stored under data/raw/ as sales.csv, marketing.csv, support.csv. "
            "Schema hints: sales.csv columns = date, region, product, units, unit_price, discount_pct. "
            "marketing.csv columns = date, region, spend. support.csv columns = date, region, tickets, severity. "
            "Note: discount_pct is a fraction (0.00-0.10), not a percentage, and severity is text (low/high), not numeric. "
            "You must run a Python analysis step to compute metrics from the CSVs before writing the report. "
            "Every report section must contain at least two sentences AND include numeric references derived from the data. "
            "If you use write_markdown, its content must include the full report; do not leave content empty. "
            "Compute marketing_revenue_correlation using DAILY totals (sum across regions per date) for revenue and spend. "
            "If your computed values do not match the required Verification values, adjust the computation method until they match. "
            f"Save the report to {report_path_rel}. "
            "The report must include these sections: # Real Autonomy Report, ## Executive summary, ## Data quality checks, "
            "## Key findings, ## Hypotheses, ## Suggested next actions, ## Appendix. "
            "In the Appendix, include a 'Verification' block with the exact key=value pairs below, using 2 decimals for currency and 3 decimals for the correlation: "
            f"total_revenue_north={expected['total_revenue_by_region']['north']:.2f}, "
            f"total_revenue_south={expected['total_revenue_by_region']['south']:.2f}, "
            f"total_revenue_east={expected['total_revenue_by_region']['east']:.2f}, "
            f"total_revenue_west={expected['total_revenue_by_region']['west']:.2f}, "
            f"top_product={expected['top_product']}, "
            f"refund_anomaly_date={expected['refund_date']}, "
            f"refund_region={expected['refund_region']}, "
            f"marketing_revenue_correlation={expected['marketing_revenue_correlation']:.3f}, "
            f"missing_marketing_rows={expected['missing_marketing_rows']}"
        )

        _log_section("Chat prompt")
        print(chat_prompt)

        chat_response = _send_agent_chat(api_base, project_id, chat_prompt, timeout=args.timeout)
        _log_section("Chat response")
        _log_json("Chat response", chat_response)
        run = chat_response.get("run")
        if not run:
            raise RuntimeError("Chat response missing run payload")
        if _run_has_failures(run):
            raise RuntimeError("Agent run failed or contained failed steps")

        report_on_disk = _find_report_path(run, workspace_path) or report_path
        report_text = _read_report(report_on_disk)

        required_sections = [
            "# Real Autonomy Report",
            "## Executive summary",
            "## Data quality checks",
            "## Key findings",
            "## Hypotheses",
            "## Suggested next actions",
            "## Appendix",
        ]
        for section in required_sections:
            if section not in report_text:
                raise RuntimeError(f"Missing section: {section}")
            if not _section_has_content(report_text, section):
                raise RuntimeError(f"Section has no content: {section}")

        lowered = report_text.lower()
        for keyword in ("refund", "marketing", "support"):
            if keyword not in lowered:
                raise RuntimeError(f"Report missing keyword: {keyword}")

        verification_lines = [
            f"total_revenue_north={expected['total_revenue_by_region']['north']:.2f}",
            f"total_revenue_south={expected['total_revenue_by_region']['south']:.2f}",
            f"total_revenue_east={expected['total_revenue_by_region']['east']:.2f}",
            f"total_revenue_west={expected['total_revenue_by_region']['west']:.2f}",
            f"top_product={expected['top_product']}",
            f"refund_anomaly_date={expected['refund_date']}",
            f"refund_region={expected['refund_region']}",
            f"marketing_revenue_correlation={expected['marketing_revenue_correlation']:.3f}",
            f"missing_marketing_rows={expected['missing_marketing_rows']}",
        ]
        for line in verification_lines:
            if line not in report_text:
                raise RuntimeError(f"Verification missing: {line}")

        steps.append(EvalStep("autonomy_eval_real", True, "report verified"))

    except Exception as exc:  # noqa: BLE001 - keep top-level errors concise
        steps.append(EvalStep("autonomy_eval_real", False, str(exc)))

    print("\nReal autonomy evaluation results")
    print("===============================")
    for step in steps:
        status = "PASS" if step.ok else "FAIL"
        print(f"- {status}: {step.name} ({step.details})")

    failed = [step for step in steps if not step.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
