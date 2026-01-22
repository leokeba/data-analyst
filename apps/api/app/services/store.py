from __future__ import annotations

from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import shutil

from sqlalchemy import func
from sqlmodel import select

from app.models.db import Artifact, Dataset, Project, Run
from app.models.schemas import (
    ArtifactRead,
    DatasetCreate,
    DatasetRead,
    ProjectCreate,
    ProjectRead,
    RunCreate,
    RunRead,
)
from app.services.db import get_session


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "projects").exists():
            return parent
    return Path.cwd()


def _ensure_project_workspace(project_id: str) -> Path:
    root = _repo_root() / "projects" / project_id
    (root / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "data" / "staging").mkdir(parents=True, exist_ok=True)
    (root / "data" / "processed").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "metadata").mkdir(parents=True, exist_ok=True)
    (root / "secrets").mkdir(parents=True, exist_ok=True)
    (root / "env").mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(parents=True, exist_ok=True)
    return root


def _is_probable_file_source(source: str) -> bool:
    if source.startswith("file://"):
        return True
    if "://" in source:
        return False
    return True


def _resolve_source_path(source: str) -> Path | None:
    if not _is_probable_file_source(source):
        return None
    source_path = Path(source.replace("file://", "")).expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        return None
    return source_path


def _maybe_copy_source(project_id: str, source: str) -> Path | None:
    if not _is_probable_file_source(source):
        return None
    source_path = Path(source.replace("file://", "")).expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    dest_dir = _repo_root() / "projects" / project_id / "data" / "raw"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / source_path.name
    shutil.copy2(source_path, dest_path)
    return dest_path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _project_read(project: Project) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        name=project.name,
        created_at=project.created_at,
        workspace_path=project.workspace_path,
    )


def _dataset_read(dataset: Dataset) -> DatasetRead:
    return DatasetRead(
        id=dataset.id,
        project_id=dataset.project_id,
        name=dataset.name,
        source=dataset.source,
        created_at=dataset.created_at,
        schema_snapshot=dataset.schema_snapshot,
        stats=dataset.stats,
    )


def _run_read(run: Run) -> RunRead:
    return RunRead(
        id=run.id,
        project_id=run.project_id,
        dataset_id=run.dataset_id,
        type=run.type,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _artifact_read(artifact: Artifact) -> ArtifactRead:
    return ArtifactRead(
        id=artifact.id,
        run_id=artifact.run_id,
        type=artifact.type,
        path=artifact.path,
        mime_type=artifact.mime_type,
        size=artifact.size,
    )


def create_project(payload: ProjectCreate) -> ProjectRead:
    project_id = Project().id
    workspace_root = _ensure_project_workspace(project_id)
    project = Project(
        id=project_id,
        name=payload.name,
        created_at=_now(),
        workspace_path=str(workspace_root),
    )
    with get_session() as session:
        session.add(project)
        session.commit()
        session.refresh(project)
    return _project_read(project)


def list_projects(limit: int = 100, offset: int = 0) -> list[ProjectRead]:
    with get_session() as session:
        projects = session.exec(
            select(Project)
            .order_by(Project.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    return [_project_read(project) for project in projects]


def count_projects() -> int:
    with get_session() as session:
        total = session.exec(select(func.count()).select_from(Project)).one()
    return int(total)


def get_project(project_id: str) -> ProjectRead | None:
    with get_session() as session:
        project = session.get(Project, project_id)
    return _project_read(project) if project else None


def delete_project(project_id: str) -> None:
    workspace_root: Path | None = None
    with get_session() as session:
        project = session.get(Project, project_id)
        if project:
            workspace_root = Path(project.workspace_path)
        datasets = session.exec(
            select(Dataset).where(Dataset.project_id == project_id)
        ).all()
        for dataset in datasets:
            runs = session.exec(select(Run).where(Run.dataset_id == dataset.id)).all()
            for run in runs:
                artifacts = session.exec(
                    select(Artifact).where(Artifact.run_id == run.id)
                ).all()
                for artifact in artifacts:
                    session.delete(artifact)
                session.delete(run)
            session.delete(dataset)
        if project:
            session.delete(project)
        session.commit()
    if workspace_root:
        repo_root = _repo_root().resolve()
        workspace_root = workspace_root.resolve()
        if workspace_root.is_dir() and workspace_root.is_relative_to(repo_root):
            shutil.rmtree(workspace_root, ignore_errors=True)


def _create_dataset_record(project_id: str, payload: DatasetCreate) -> DatasetRead:
    dataset = Dataset(
        project_id=project_id,
        name=payload.name,
        source=payload.source,
        created_at=_now(),
        schema_snapshot=None,
        stats=None,
    )
    with get_session() as session:
        session.add(dataset)
        session.commit()
        session.refresh(dataset)
    return _dataset_read(dataset)


def create_dataset(project_id: str, payload: DatasetCreate) -> DatasetRead:
    copied_path = _maybe_copy_source(project_id, payload.source)
    if copied_path:
        payload = DatasetCreate(name=payload.name, source=f"file://{copied_path}")
    return _create_dataset_record(project_id, payload)


def create_dataset_from_upload(project_id: str, filename: str, data: bytes) -> DatasetRead:
    workspace = _ensure_project_workspace(project_id)
    dest_path = workspace / "data" / "raw" / filename
    dest_path.write_bytes(data)
    payload = DatasetCreate(name=filename, source=f"file://{dest_path}")
    return _create_dataset_record(project_id, payload)


def list_datasets(project_id: str, limit: int = 100, offset: int = 0) -> list[DatasetRead]:
    with get_session() as session:
        datasets = session.exec(
            select(Dataset)
            .where(Dataset.project_id == project_id)
            .order_by(Dataset.created_at)
            .offset(offset)
            .limit(limit)
        ).all()
    return [_dataset_read(dataset) for dataset in datasets]


def count_datasets(project_id: str) -> int:
    with get_session() as session:
        total = session.exec(
            select(func.count()).select_from(Dataset).where(Dataset.project_id == project_id)
        ).one()
    return int(total)


def get_dataset(dataset_id: str) -> DatasetRead | None:
    with get_session() as session:
        dataset = session.get(Dataset, dataset_id)
    return _dataset_read(dataset) if dataset else None


def get_dataset_file_path(project_id: str, dataset_id: str) -> Path | None:
    with get_session() as session:
        dataset = session.get(Dataset, dataset_id)
        project = session.get(Project, project_id)
        if not dataset or not project or dataset.project_id != project_id:
            return None
    source_path = _resolve_source_path(dataset.source)
    if not source_path:
        return None
    workspace_root = Path(project.workspace_path).resolve()
    source_path = source_path.resolve()
    if not source_path.is_relative_to(workspace_root):
        return None
    return source_path


def get_dataset_preview(project_id: str, dataset_id: str) -> dict[str, object] | None:
    dataset_path = get_dataset_file_path(project_id, dataset_id)
    if not dataset_path:
        return None
    if dataset_path.suffix.lower() != ".csv":
        return None
    header: list[str] = []
    rows: list[list[str]] = []
    with dataset_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        for idx, row in enumerate(reader):
            rows.append(row)
            if idx >= 19:
                break
    return {"columns": header, "rows": rows, "row_count": len(rows)}


def delete_dataset(project_id: str, dataset_id: str) -> None:
    workspace_root: Path | None = None
    source_path: Path | None = None
    with get_session() as session:
        dataset = session.get(Dataset, dataset_id)
        if not dataset or dataset.project_id != project_id:
            return
        project = session.get(Project, project_id)
        if project:
            workspace_root = Path(project.workspace_path)
        source_path = _resolve_source_path(dataset.source)
        runs = session.exec(select(Run).where(Run.dataset_id == dataset_id)).all()
        for run in runs:
            artifacts = session.exec(select(Artifact).where(Artifact.run_id == run.id)).all()
            for artifact in artifacts:
                session.delete(artifact)
            session.delete(run)
        session.delete(dataset)
        session.commit()
    if workspace_root and source_path:
        repo_root = _repo_root().resolve()
        workspace_root = workspace_root.resolve()
        source_path = source_path.resolve()
        if source_path.is_file() and source_path.is_relative_to(workspace_root) and source_path.is_relative_to(repo_root):
            source_path.unlink(missing_ok=True)


def create_run(project_id: str, payload: RunCreate) -> RunRead:
    run = Run(
        project_id=project_id,
        dataset_id=payload.dataset_id,
        type=payload.type,
        status="queued",
        started_at=_now(),
        finished_at=None,
    )
    with get_session() as session:
        session.add(run)
        session.commit()
        session.refresh(run)
    _execute_run_stub(project_id, run.id, payload.dataset_id, payload.type)
    return get_run(run.id) or _run_read(run)


def _execute_run_stub(project_id: str, run_id: str, dataset_id: str, run_type: str) -> None:
    workspace = _ensure_project_workspace(project_id)
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    with get_session() as session:
        run = session.get(Run, run_id)
        if run:
            run.status = "running"
            session.add(run)
            session.commit()
    artifacts_to_create: list[tuple[str, Path, str]] = []
    artifact_path = artifacts_dir / f"{run_type}-{run_id}.json"
    log_path = artifacts_dir / f"{run_type}-{run_id}.log"
    summary: dict[str, object] = {
        "run_id": run_id,
        "project_id": project_id,
        "dataset_id": dataset_id,
        "type": run_type,
        "status": "completed",
        "generated_at": _now().isoformat(),
    }
    if run_type == "profile":
        profile_summary = _profile_dataset(dataset_id)
        if profile_summary:
            summary["profile"] = profile_summary
    if run_type == "analysis":
        analysis = _analyze_dataset(dataset_id)
        if analysis:
            summary["analysis"] = analysis
            analysis_path = artifacts_dir / f"analysis-{run_id}.json"
            analysis_path.write_text(json.dumps(analysis, indent=2))
            artifacts_to_create.append(("analysis_summary", analysis_path, "application/json"))
    if run_type == "report":
        report = _build_report(dataset_id)
        if report:
            summary["report"] = {"markdown": str(report["markdown"]), "html": str(report["html"])}
            artifacts_to_create.append(("report_markdown", report["markdown"], "text/markdown"))
            artifacts_to_create.append(("report_html", report["html"], "text/html"))
    artifact_path.write_text(json.dumps(summary, indent=2))
    log_path.write_text(
        "\n".join(
            [
                f"[{summary['generated_at']}] Run started",
                f"Run type: {run_type}",
                f"Dataset id: {dataset_id}",
                "Status: completed",
            ]
        )
    )
    artifacts_to_create.append((f"{run_type}_summary", artifact_path, "application/json"))
    artifacts_to_create.append(("run_log", log_path, "text/plain"))
    finished_at = _now()
    with get_session() as session:
        run = session.get(Run, run_id)
        if run:
            run.status = "completed"
            run.finished_at = finished_at
            session.add(run)
        for artifact_type, path, mime_type in artifacts_to_create:
            artifact = Artifact(
                run_id=run_id,
                type=artifact_type,
                path=str(path),
                mime_type=mime_type,
                size=path.stat().st_size,
            )
            session.add(artifact)
        session.commit()


def _profile_dataset(dataset_id: str) -> dict[str, object] | None:
    with get_session() as session:
        dataset = session.get(Dataset, dataset_id)
        if not dataset:
            return None
        source_path = _resolve_source_path(dataset.source)
        if not source_path:
            return None
        row_count = 0
        header: list[str] = []
        missing_by_column: dict[str, int] = {}
        duplicate_rows = 0
        seen_rows: set[tuple[str, ...]] = set()
        with source_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            missing_by_column = {name: 0 for name in header}
            for row in reader:
                row_count += 1
                row_key = tuple(row)
                if row_key in seen_rows:
                    duplicate_rows += 1
                else:
                    seen_rows.add(row_key)
                for idx, name in enumerate(header):
                    value = row[idx] if idx < len(row) else ""
                    if value == "":
                        missing_by_column[name] += 1
        column_count = len(header)
        stats = {
            "row_count": row_count,
            "column_count": column_count,
            "file_size_bytes": source_path.stat().st_size,
            "missing_by_column": missing_by_column,
            "duplicate_row_count": duplicate_rows,
        }
        schema_snapshot = {
            "columns": [{"name": name, "index": idx} for idx, name in enumerate(header)]
        }
        dataset.stats = stats
        dataset.schema_snapshot = schema_snapshot
        session.add(dataset)
        session.commit()
        return {"stats": stats, "schema": schema_snapshot, "source": str(source_path)}


def _build_report(dataset_id: str) -> dict[str, Path] | None:
    with get_session() as session:
        dataset = session.get(Dataset, dataset_id)
        if not dataset:
            return None
        stats = dataset.stats or {}
        schema = dataset.schema_snapshot or {}
    analysis = _analyze_dataset(dataset_id) or {}
    workspace = _repo_root() / "projects" / dataset.project_id
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_md = artifacts_dir / f"report-{dataset_id}.md"
    report_html = artifacts_dir / f"report-{dataset_id}.html"
    columns = schema.get("columns", []) if isinstance(schema, dict) else []
    sample_rows = analysis.get("sample_rows", []) if isinstance(analysis, dict) else []
    sample_columns = analysis.get("columns", []) if isinstance(analysis, dict) else []
    sample_table_rows = sample_rows[:5]
    md_table = "\n"
    if sample_columns:
        header_row = "| " + " | ".join(sample_columns) + " |"
        separator_row = "| " + " | ".join(["---" for _ in sample_columns]) + " |"
        body_rows = ["| " + " | ".join(row) + " |" for row in sample_table_rows]
        md_table = "\n".join([header_row, separator_row, *body_rows]) + "\n"
    md = "".join(
        [
            f"# Dataset report\n\n",
            f"**Dataset:** {dataset.name}\n\n",
            f"**Source:** {dataset.source}\n\n",
            f"## Summary\n",
            f"- Rows: {stats.get('row_count', '—')}\n",
            f"- Columns: {stats.get('column_count', '—')}\n",
            f"- File size: {stats.get('file_size_bytes', '—')} bytes\n\n",
            "## Columns\n",
            "\n".join([f"- {col.get('name', '')}" for col in columns]) or "- (none)",
            "\n",
            "\n## Sample rows\n",
            md_table or "(no sample rows available)\n",
        ]
    )
    report_md.write_text(md)
    sample_table_html = ""
    if sample_columns:
        header_html = "".join([f"<th>{col}</th>" for col in sample_columns])
        body_html = "".join(
            [
                "<tr>" + "".join([f"<td>{cell}</td>" for cell in row]) + "</tr>"
                for row in sample_table_rows
            ]
        )
        sample_table_html = f"<table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"
    html = "".join(
        [
            "<html><head><meta charset='utf-8'><title>Dataset report</title>",
            "<style>",
            "body{font-family:system-ui,-apple-system,sans-serif;margin:32px;color:#18181b;background:#fafafa;}",
            "h1,h2{margin:0 0 12px 0;} h2{margin-top:24px;}",
            "p,li{font-size:14px;line-height:1.5;color:#3f3f46;}",
            "ul{padding-left:20px;}",
            "table{width:100%;border-collapse:collapse;margin-top:8px;background:white;}",
            "th,td{border:1px solid #e4e4e7;padding:6px 8px;text-align:left;font-size:12px;}",
            "th{background:#f4f4f5;font-weight:600;}",
            "</style></head><body>",
            f"<h1>Dataset report</h1>",
            f"<p><strong>Dataset:</strong> {dataset.name}</p>",
            f"<p><strong>Source:</strong> {dataset.source}</p>",
            "<h2>Summary</h2>",
            "<ul>",
            f"<li>Rows: {stats.get('row_count', '—')}</li>",
            f"<li>Columns: {stats.get('column_count', '—')}</li>",
            f"<li>File size: {stats.get('file_size_bytes', '—')} bytes</li>",
            "</ul>",
            "<h2>Columns</h2>",
            "<ul>",
            "".join([f"<li>{col.get('name', '')}</li>" for col in columns]) or "<li>(none)</li>",
            "</ul>",
            "<h2>Sample rows</h2>",
            sample_table_html or "<p>(no sample rows available)</p>",
            "</body></html>",
        ]
    )
    report_html.write_text(html)
    return {"markdown": report_md, "html": report_html}


def _analyze_dataset(dataset_id: str) -> dict[str, object] | None:
    with get_session() as session:
        dataset = session.get(Dataset, dataset_id)
        if not dataset:
            return None
        source_path = _resolve_source_path(dataset.source)
        if not source_path:
            return None
    sample_rows: list[list[str]] = []
    header: list[str] = []
    with source_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        for idx, row in enumerate(reader):
            sample_rows.append(row)
            if idx >= 4:
                break
    return {
        "source": str(source_path),
        "columns": header,
        "sample_rows": sample_rows,
        "sample_row_count": len(sample_rows),
    }


def list_runs(project_id: str, limit: int = 100, offset: int = 0) -> list[RunRead]:
    with get_session() as session:
        runs = session.exec(
            select(Run)
            .where(Run.project_id == project_id)
            .order_by(Run.started_at)
            .offset(offset)
            .limit(limit)
        ).all()
    return [_run_read(run) for run in runs]


def count_runs(project_id: str) -> int:
    with get_session() as session:
        total = session.exec(
            select(func.count()).select_from(Run).where(Run.project_id == project_id)
        ).one()
    return int(total)


def get_run(run_id: str) -> RunRead | None:
    with get_session() as session:
        run = session.get(Run, run_id)
    return _run_read(run) if run else None


def delete_run(project_id: str, run_id: str) -> None:
    workspace_root: Path | None = None
    with get_session() as session:
        run = session.get(Run, run_id)
        if not run or run.project_id != project_id:
            return
        project = session.get(Project, project_id)
        if project:
            workspace_root = Path(project.workspace_path)
        artifacts = session.exec(select(Artifact).where(Artifact.run_id == run_id)).all()
        for artifact in artifacts:
            session.delete(artifact)
        session.delete(run)
        session.commit()
    if workspace_root:
        repo_root = _repo_root().resolve()
        workspace_root = workspace_root.resolve()
        for artifact in artifacts:
            artifact_path = Path(artifact.path).resolve()
            if artifact_path.is_file() and artifact_path.is_relative_to(workspace_root) and artifact_path.is_relative_to(repo_root):
                artifact_path.unlink(missing_ok=True)


def list_artifacts(run_id: str, limit: int = 100, offset: int = 0) -> list[ArtifactRead]:
    with get_session() as session:
        artifacts = session.exec(
            select(Artifact)
            .where(Artifact.run_id == run_id)
            .offset(offset)
            .limit(limit)
        ).all()
    return [_artifact_read(artifact) for artifact in artifacts]


def list_project_artifacts(
    project_id: str, run_id: str | None = None, limit: int = 100, offset: int = 0
) -> list[ArtifactRead]:
    with get_session() as session:
        runs = session.exec(select(Run).where(Run.project_id == project_id)).all()
        run_ids = {run.id for run in runs}
        if run_id:
            if run_id not in run_ids:
                return []
            run_ids = {run_id}
        if not run_ids:
            return []
        artifacts = session.exec(
            select(Artifact)
            .where(Artifact.run_id.in_(run_ids))
            .offset(offset)
            .limit(limit)
        ).all()
    return [_artifact_read(artifact) for artifact in artifacts]


def count_project_artifacts(project_id: str, run_id: str | None = None) -> int:
    with get_session() as session:
        query = (
            select(func.count())
            .select_from(Artifact)
            .join(Run, Artifact.run_id == Run.id)
            .where(Run.project_id == project_id)
        )
        if run_id:
            query = query.where(Artifact.run_id == run_id)
        total = session.exec(query).one()
    return int(total)


def get_artifact(artifact_id: str) -> ArtifactRead | None:
    with get_session() as session:
        artifact = session.get(Artifact, artifact_id)
    return _artifact_read(artifact) if artifact else None


def delete_artifact(project_id: str, artifact_id: str) -> None:
    workspace_root: Path | None = None
    artifact_path: Path | None = None
    with get_session() as session:
        artifact = session.get(Artifact, artifact_id)
        run = session.get(Run, artifact.run_id) if artifact else None
        project = session.get(Project, project_id)
        if not artifact or not run or not project or run.project_id != project_id:
            return
        artifact_path = Path(artifact.path)
        workspace_root = Path(project.workspace_path)
        session.delete(artifact)
        session.commit()
    if workspace_root and artifact_path:
        repo_root = _repo_root().resolve()
        workspace_root = workspace_root.resolve()
        artifact_path = artifact_path.resolve()
        if artifact_path.is_file() and artifact_path.is_relative_to(workspace_root) and artifact_path.is_relative_to(repo_root):
            artifact_path.unlink(missing_ok=True)
