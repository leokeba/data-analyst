#!/usr/bin/env python3
"""Custom iteration command to harden the agent harness.

This runner executes the available eval scripts (baseline, chat-only, hardening)
with strict failure detection, logs all outputs for post-mortems, and summarizes
results. Use it to iterate: make the agent pass, then tighten the evals.

Usage:
  uv run python scripts/agent_iterate_command.py [--api-base URL] [--cleanup]
                                               [--out-dir PATH] [--skip SKIP...]

Examples:
  uv run python scripts/agent_iterate_command.py --cleanup
  uv run python scripts/agent_iterate_command.py --skip hardening
  uv run python scripts/agent_iterate_command.py --out-dir /tmp/agent-evals

Notes:
- Uses `uv run python <script>` for each eval to comply with tooling guidance.
- Writes stdout/stderr per eval into the out directory for quick diffing.
- Exits non-zero on the first failure to surface weaknesses quickly.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Eval scripts to run in order (name -> path)
EVAL_SCRIPTS = {
    "baseline": "scripts/e2e_agent_eval.py",
    "chat": "scripts/e2e_agent_eval_chat.py",
    "hardening": "scripts/agent_hardening_eval.py",
}


@dataclass
class EvalResult:
    name: str
    status: str  # PASS/FAIL/SKIP
    details: str
    log_path: Path | None = None


def _run_eval(
    name: str, script: str, *, api_base: str, cleanup: bool, out_dir: Path
) -> EvalResult:
    if not Path(script).is_file():
        return EvalResult(name, "SKIP", f"missing script: {script}")

    log_path = out_dir / f"{name}.log"
    cmd = [
        "uv",
        "run",
        "python",
        script,
        "--api-base",
        api_base,
    ]
    if cleanup:
        cmd.append("--cleanup")

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, "API_BASE": api_base},
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(proc.stdout + "\n\n[STDERR]\n" + proc.stderr)

    if proc.returncode != 0:
        return EvalResult(
            name, "FAIL", f"rc={proc.returncode}; see {log_path}", log_path
        )
    return EvalResult(name, "PASS", f"rc=0; see {log_path}", log_path)


def _filter_evals(skip: Iterable[str]) -> dict[str, str]:
    skip_set = {item.strip().lower() for item in skip}
    return {k: v for k, v in EVAL_SCRIPTS.items() if k.lower() not in skip_set}


def main() -> int:
    parser = argparse.ArgumentParser(description="Iterative agent eval runner")
    parser.add_argument(
        "--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000")
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Pass --cleanup to sub-evals"
    )
    parser.add_argument("--out-dir", default=None, help="Directory to store eval logs")
    parser.add_argument(
        "--skip",
        nargs="*",
        default=(),
        help="Eval names to skip (baseline, chat, hardening)",
    )
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir or f".agent-evals/{timestamp}")

    selected = _filter_evals(args.skip)
    if not selected:
        print("No evals selected (all skipped)")
        return 0

    print(f"Running evals: {', '.join(selected.keys())}")
    print(f"API base: {api_base}")
    print(f"Logs: {out_dir}")

    results: list[EvalResult] = []
    for name, script in selected.items():
        print(f"\n=== {name} ===")
        result = _run_eval(
            name, script, api_base=api_base, cleanup=args.cleanup, out_dir=out_dir
        )
        results.append(result)
        print(f"{result.status}: {result.details}")
        if result.status == "FAIL":
            break

    print("\nSummary")
    print("-------")
    exit_code = 0
    for res in results:
        print(f"- {res.status}: {res.name} ({res.details})")
        if res.status == "FAIL":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
