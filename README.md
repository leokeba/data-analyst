# data-analyst

Data‑analyst agent framework for end‑to‑end ingestion, profiling, analysis, metadata, and reporting. This repository will include a FastAPI backend and a Svelte frontend for control and observability.

## MVP spec (v0.1)

### Goals
- Provide a clean UI + API to create isolated data projects.
- Fetch data from common sources and consolidate into a project workspace.
- Auto‑profile and validate datasets.
- Produce reproducible analysis outputs and reports.
- Document schemas, keys, joins, duplicates, and lineage in a consistent format.

### Non‑goals (MVP)
- Full multi‑tenant org management.
- Distributed compute / autoscaling.
- Advanced ML pipelines.
- Real‑time streaming ingestion.

### Core capabilities
1. **Project management (isolated environments)**
	- Create, list, and delete projects.
	- Each project has isolated data, scripts, and secrets.
	- Per‑project Python environment definition.

2. **Ingestion**
	- Local files: CSV, Parquet, JSON.
	- Database connectors (MVP: Postgres + SQLite).
	- Basic normalization into a raw/staging layer.

3. **Profiling & quality**
	- Auto‑generate a profiling report per dataset (HTML + JSON).
	- Basic data checks: missing values, duplicates, schema drift.

4. **Analysis & stats**
	- Standard descriptive stats + correlations.
	- Save analysis artifacts to project storage.

5. **Metadata & documentation**
	- Generate dataset documentation (schema, column stats).
	- Identify candidate keys, joins, duplicates.
	- Persist metadata as JSON + markdown in project.

6. **Reporting**
	- Generate a markdown report with embedded plots.
	- Export HTML/PDF.

7. **Frontend control plane**
	- Project list + detail view.
	- Dataset catalog with profiling status.
	- Run history with logs and artifacts.

8. **Agent layer (thin)**
   - Optional agent orchestration for user guidance and automation.
   - All data operations remain deterministic and auditable.

### MVP user stories
- As a user, I can create a project with isolated data and secrets.
- As a user, I can ingest a CSV and see a profiling report.
- As a user, I can run a basic analysis and download a report.
- As a user, I can view schema documentation and join hints.

### KPIs (MVP)
- Time from project creation to first report < 10 minutes.
- Profiling completion on a 1M‑row CSV < 3 minutes (local).
- 0‑config run on sample dataset.

### MVP functional scope (v0.1)
**Project**
- Create project with isolated workspace + env definition.
- Store secrets as encrypted local files or env vars (MVP). 

**Ingestion**
- CSV/Parquet/JSON upload or local path import.
- DB ingestion: Postgres + SQLite using connection string.
- Record source metadata and schema snapshot.

**Profiling & quality**
- Generate HTML + JSON profile report.
- Basic checks: missing %, duplicates, schema drift warnings.

**Analysis**
- Descriptive stats, correlations, and distribution plots.
- Persist artifacts (tables + plots) with run metadata.

**Metadata**
- Generate schema docs, candidate keys, join hints.
- Write metadata to JSON + markdown in project workspace.

**Reporting**
- Generate Markdown report, export HTML/PDF.

**Jobs & runs**
- Run tracking with status, logs, artifacts, and timestamps.
- Idempotent runs per dataset and stage.

### API surface (v0.1)
- `GET /health`
- `POST /projects`, `GET /projects`, `GET /projects/{id}`, `DELETE /projects/{id}`
- `POST /projects/{id}/datasets` (ingest)
- `GET /projects/{id}/datasets`, `GET /projects/{id}/datasets/{dataset_id}`
- `POST /projects/{id}/runs` (profile|analysis|report)
- `GET /projects/{id}/runs`, `GET /projects/{id}/runs/{run_id}`
- `GET /projects/{id}/artifacts/{artifact_id}`

### Frontend MVP pages
- **Projects**: list + create.
- **Project detail**: datasets, runs, artifacts.
- **Dataset detail**: profiling report + schema docs.
- **Run detail**: logs + artifact links.

## Proposed architecture

### Services
- **API service (FastAPI)**
  - Project CRUD
  - Job orchestration
  - Dataset registry
  - Artifact storage API
  - Auth (token‑based for MVP)

- **Worker runtime**
  - Executes ingestion, profiling, analysis, report generation
  - Uses Python scripts + library modules
  - Writes artifacts to project storage

- **Frontend (Svelte)**
  - Controls projects, datasets, and runs
  - Shows profiling outputs and reports

### Core packages
- `packages/core`: profiling, analysis, metadata, schemas
- `packages/connectors`: file + DB connectors
- `packages/runtime`: job execution + logging + environment resolution
- `packages/reporting`: markdown/HTML/PDF generation

### Data model (MVP)
- **Project**: id, name, created_at, workspace_path
- **Dataset**: id, project_id, name, source, schema_snapshot, stats
- **Run**: id, project_id, dataset_id, type, status, started_at, finished_at
- **Artifact**: id, run_id, type, path, mime_type, size

### Data flow (simplified)
1. Project created → workspace provisioned
2. Ingestion job pulls data → raw/staging
3. Profiling job → HTML/JSON report + metadata
4. Analysis job → stats, plots, summary tables
5. Report job → markdown + HTML/PDF

## Tech stack (MVP)

**Backend**
- FastAPI (API)
- Pydantic (models)
- SQLModel or SQLAlchemy (metadata DB)
- Celery or RQ (job queue) — optional in MVP

**Analysis runtime**
- pandas + Polars
- DuckDB (local SQL)
- ydata‑profiling (EDA reports)
- Great Expectations or Soda Core (quality checks)
- matplotlib / seaborn / plotly (plots)

**Frontend**
- SvelteKit
- Tailwind CSS
- TanStack Query (or equivalent) for API data

**Storage**
- Local filesystem for MVP
- Pluggable storage interface for later (S3/GCS)

**Agent layer (optional)**
- PydanticAI or similar for conversational orchestration.
- Tools restricted to safe, deterministic pipeline actions.

## Initial repo layout

```
data-analyst/
├─ apps/
│  ├─ api/                    # FastAPI backend
│  │  ├─ app/
│  │  │  ├─ main.py
│  │  │  ├─ routes/
│  │  │  ├─ services/
│  │  │  ├─ models/
│  │  │  └─ config/
│  │  └─ tests/
│  └─ web/                    # Svelte frontend
│     ├─ src/
│     └─ tests/
├─ packages/
│  ├─ core/                   # analysis + profiling + metadata
│  ├─ connectors/             # ingestion connectors
│  ├─ runtime/                # job execution helpers
│  └─ reporting/              # report generation
├─ projects/                  # per‑project workspaces (gitignored)
├─ docs/                      # architecture + specs
├─ scripts/                   # dev tooling
└─ README.md
```

## Next steps
1. Create the directory structure and base project configs.
2. Implement FastAPI skeleton (health, projects, runs).
3. Implement Svelte UI shell (projects list + detail).
4. Add ingestion + profiling pipeline for CSV.
5. Add report generator for HTML/Markdown.

## Roadmap
**v0.1 (MVP)**
- Local filesystem storage.
- Single‑node execution.
- CSV/Parquet/JSON + Postgres/SQLite ingestion.
- Profiling + basic quality checks.
- Basic analysis + report generation.
- Svelte control plane for projects/datasets/runs.

**v0.2**
- Pluggable storage (S3/GCS).
- Improved metadata (lineage, glossary, owners).
- Incremental ingestion + dataset versioning.
- AuthN/AuthZ, multi‑user project access.

**v0.3**
- Job queue + workers (Celery/RQ).
- Observability dashboards + alerts.
- Connector marketplace + custom connectors.
- Optional metadata catalog integration (DataHub/OpenMetadata).