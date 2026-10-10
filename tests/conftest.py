import os
import tempfile

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + tempfile.mktemp(
    suffix=".db"
)
os.environ["SECRET_KEY"] = "test-secret-key-" * 4
os.environ["ADMIN_TOKEN"] = "test-owner-token-" * 3
os.environ["PUBLIC_URL"] = "http://testserver"
os.environ["DISPATCHER_ENABLED"] = "false"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["GITHUB_TOKEN"] = ""
os.environ["GATEWAY_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def offline_pricing(monkeypatch):
    from unittest.mock import AsyncMock

    from app import spend
    monkeypatch.setattr(spend, "lookup_rates", AsyncMock(return_value=None))


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def owner(client):
    response = client.post("/api/login", json={"token": os.environ["ADMIN_TOKEN"]})
    assert response.status_code == 200
    return client
