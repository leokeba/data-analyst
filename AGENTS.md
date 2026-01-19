# AGENTS.md

This document defines development guidelines for the data‑analyst framework.

## Principles
- Keep data operations deterministic and auditable.
- Treat metadata as first‑class output, not a side effect.
- Prefer composable, testable modules over monolithic flows.
- Isolate projects by default (data, scripts, secrets, environments).
- Keep the agent layer thin and optional.

## Architecture summary
- **apps/api**: FastAPI service (projects, datasets, runs, artifacts).
- **apps/web**: Svelte control plane UI.
- **packages/core**: profiling, analysis, metadata inference.
- **packages/connectors**: ingestion sources (file, DB).
- **packages/runtime**: job execution, logging, environment resolution.
- **packages/reporting**: markdown/HTML/PDF reports.
- **projects/**: per‑project workspaces (gitignored).

## Agent layer guidance
- Use an agent framework (e.g., PydanticAI) only for orchestration and user assistance.
- Agent tools must map to safe, deterministic actions: ingest, profile, analyze, report.
- Avoid “auto‑modify data” tools without explicit user confirmation.
- Store all agent actions as run logs with inputs and outputs.

## Project isolation
- Each project has:
  - data/raw, data/staging, data/processed
  - scripts/
  - artifacts/
  - metadata/
  - secrets/ (encrypted or env‑backed)
  - env/ (definition file, not a shared runtime)

## API conventions
- All writes are idempotent when possible.
- All runs emit: status, logs, artifact list, timestamps.
- Artifacts are immutable once created.
- Use explicit run types: ingest, profile, analyze, report.

## Metadata conventions
- Persist metadata as JSON + markdown in project workspace.
- Always include schema snapshots and column stats.
- Capture candidate keys, duplicates, and join hints.

## Reporting conventions
- Reports are generated from deterministic data + templates.
- Store raw data used for report generation in artifacts.
- Export both Markdown and HTML; PDF is optional in MVP.

## Error handling
- Fail fast with clear error messages and recovery hints.
- Log structured errors with dataset/run IDs.
- Never swallow exceptions in worker/runtime layers.

## Testing
- Unit tests for core analysis + metadata inference.
- Integration tests for ingestion and report generation.
- Snapshot tests for metadata outputs.

## Security
- Secrets never stored in logs.
- Project workspaces must not escape root workspace.
- Validate file uploads and paths.

## Definition of done (MVP)
- One‑click project creation.
- CSV ingest → profiling → analysis → report with artifacts.
- UI shows datasets, run history, and reports.
- Metadata docs generated for each dataset.
