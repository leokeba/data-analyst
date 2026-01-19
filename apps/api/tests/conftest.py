from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(API_ROOT))

from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _clean_db() -> None:
    db_path = API_ROOT / "app.db"
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as client:
        yield client
