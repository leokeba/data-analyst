# LLM agent integration roadmap (v0.3)

## Progress (current)
- Phase 1: Core orchestration — **in progress**
	- ✅ Agent runtime skeleton (plan/steps, tool router, journal, snapshots)
	- ✅ Agent API endpoints: create run, get run, list runs, list tools
	- ✅ Initial agent tools: create run, preview dataset, list datasets, list project runs, list artifacts, create snapshot, request rollback
	- ✅ Agent runs UI: tool inventory, run list, per-step status, completion %, replay action
	- ✅ Replay flow captures approver name for required-approval steps
	- ✅ Snapshot listing endpoint (metadata only)
	- ✅ Snapshot records persisted in DB schema
	- ✅ Snapshot list surfaced in agent UI (read-only)
	- ✅ Rollback request endpoint (record-only)
	- ✅ Rollback request action wired in UI (record-only)
	- ✅ Rollback request list surfaced in agent UI
	- ✅ Persisted run logs wired to UI (expandable log viewer)
	- ⏳ Tool approval UI + apply/rollback controls (not started)
	- ⏳ Snapshot/rollback implementation beyond metadata (not started)

## Goals
- Safe, auditable agent-assisted workflows for ingest → profile → analyze → correlate → consolidate → report.
- Deterministic execution with explicit user approvals, reversible actions, and full run logs.
- Copilot-grade conversational UX: chat-first, inline diffs, step approvals, and rollbacks.
- Track progress end-to-end: plan completion %, per-step status, and run timeline updates surfaced in UI.

## Phase 0 — Design (1–2 weeks)
- Define agent roles: `Planner` (plan/branch), `Operator` (tool calls), `Explainer` (summaries, rationale).
- Expand tool contract: data navigation (list/preview/query sources), ingestion, profiling, correlation discovery, plotting, scripted transforms, consolidation/joins, report generation, artifact inspection, rollback (snapshot/undo), and safe file edits.
- Define policy model: path allowlists, size/time/resource caps, network default-off, destructive action confirmations, sandbox tiers (read-only vs apply).
- Define state model: action journal with deterministic args, snapshot strategy (workspace, dataset, file), inverse operations for undo/redo.
- Define run log schema: inputs, tool calls, outputs, timestamps, approvals, applied/rolled-back status, diffs/artifacts references.

## Phase 1 — Core orchestration (2–3 weeks)
- Implement agent runtime (planner/operator/explainer loop) with plan checkpointing and step budgets.
- Add tool router with strict allowlist, parameter validation, and staged execution (dry-run first, apply after approval).
- Add action journal + snapshot store for deterministic replay and rollback (undo last, restore snapshot).
- Enforce sandboxed execution for scripted transforms (resource/time limits, temp workspace copies, diff-before-apply).
- Add correlation and consolidation primitives (key inference, join type selection) as first-class tools.

## Phase 2 — UX integration (2–3 weeks)
- Add Copilot-style chat sidebar with inline previews (data slices, plots, code diffs).
- Show structured plan with per-step approve/apply/rollback and status chips.
- Progress tracking UI: live step status, completion %, and timeline view tied to run logs.
- Inline diffs for file/script changes; show artifact thumbnails for plots/reports.
- Live run log streaming (inputs/outputs/errors) with jump-to-artifact and jump-to-diff.
- Expose undo/redo controls (restore snapshot, undo last action) and safe-mode toggle (read-only until explicitly approved).
- Persist run logs and snapshots as artifacts and link them in the UI.

## Phase 3 — Prompting + templates (2 weeks)

## Phase 3 — Prompting + templates (2 weeks)
- Task templates for ingest → profile → correlate → consolidate → report, including multi-source joins/unions with key detection.
- Guardrails: refusal for unsafe paths, path sandboxing, file size/row limits, schema compatibility checks before consolidation.
- Smart defaults: suggested runs based on dataset stats, sampling for previews/plots, safe plotting defaults.
- Recipe catalog with preflight checks (data size, schema match, key coverage) and explicit approvals.
- **User-managed SKILLS list (Claude-style)**
	- Add a Skills registry with CRUD and list endpoints for user-defined skills.
	- Expose a Skills management UI (enable/disable, edit metadata, versioning).
	- Allow plans to reference Skills by name; resolve to toolchains + prompt templates.
	- Source inspiration: Anthropic Claude Skills API (create/list skills) and a user-maintained skill catalog concept.

## Phase 4 — Quality + evaluation (2–3 weeks)
- Add eval suite: deterministic prompt tests, tool call traces, rollback/redo correctness, correlation/merge accuracy fixtures.
- Regression tests for authorization/policy enforcement and sandbox escape attempts.
- UX regressions for chat, diff, and revert flows.
- Metrics: completion rate, approval latency, error rate, undo success rate, revert latency, guardrail false positives.

## Deliverables
- Agent runtime module + expanded tool contracts (navigation, transforms, plotting, correlations, consolidation, rollback).
- Action journal + snapshot/rollback service with deterministic replay.
- Copilot-style chat UI with plan/approve/apply/rollback, inline diffs, previews, and artifact gallery for plots/reports.
- Run logs as artifacts + audit view showing applied/rolled-back steps and diffs.
- Template prompts + eval harness covering consolidation and rollback scenarios.

## Risks to manage
- Non-deterministic outputs → enforce tool-driven determinism, sampling caps, and approvals.
- Data leakage → strict path allowlists, redaction in logs, network-off by default.
- UX overload → guided templates, minimal-step plans, and inline diffs instead of wall-of-text logs.
- Rollback complexity → snapshot granularity and cost; ensure fast undo paths.
- Script execution risk → sandboxing, resource/time limits, diff-before-apply.

## Dependencies
- Stable tool APIs for ingestion, profiling, analysis, correlations, plotting, consolidation, reporting, navigation, rollback.
- Clear artifact metadata, preview endpoints, and snapshot storage.
- Security model for access control, sandboxing, and policy enforcement.
