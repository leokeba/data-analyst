from __future__ import annotations

from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from app.models.db import (
    AgentChatMessage,
    AgentArtifact,
    AgentRollback,
    AgentRun,
    AgentSkill,
    AgentSnapshot,
    Artifact,
    Dataset,
    Project,
    Run,
)


def _db_path() -> Path:
    base_dir = Path(__file__).resolve().parents[2]
    return base_dir / "app.db"


def get_engine():
    return create_engine(
        f"sqlite:///{_db_path()}", connect_args={"check_same_thread": False}
    )


def init_db() -> None:
    engine = get_engine()
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(get_engine())
