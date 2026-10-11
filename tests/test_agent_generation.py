import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import agent_generation, providers
from app.db import Resource, Session


def connection(owner, kind="gateway"):
    response = owner.post("/api/resources/connections", json={
        "name": "Models", "type": kind, "base_url": "https://gateway.test/v1", "api_key": "private-key",
    })
    assert response.status_code == 200
    return response.json()["id"]


def test_generation_defaults_and_model_settings_are_persistent_and_private(owner):
    assert owner.get("/api/settings/agent-generation").json()["connection_id"] == ""
    cid = connection(owner)
    data = owner.get("/api/settings/agent-generation").json()
    assert data == {"connection_id": cid, "model": "anthropic/claude-haiku-5-5"}
    data["model"] = "my-cheap-model"
    assert owner.put("/api/settings/agent-generation", json=data).json() == data
    assert owner.get("/api/settings/agent-generation").json() == data
    assert owner.get("/api/resources/agents").json() == []
    assert owner.put("/api/settings/agent-generation", json={**data, "connection_id": "gone"}).status_code == 400
    assert owner.get("/api/settings/agent-generation").json() == data


def test_generate_returns_editable_draft_without_creating_or_modifying_agents(owner, monkeypatch):
    cid = connection(owner)
    with Session.begin() as db:
        db.add(Resource(id="existing", kind="groups", data={"name": "security"}))
    model = AsyncMock(return_value=json.dumps({"name": "Security", "instructions": "Inspect auth boundaries.", "capabilities": ["browser"]}))
    monkeypatch.setattr(providers, "draft_completion", model)
    response = owner.post("/api/agents/generate", json={"prompt": "Review authentication bugs and exercise the browser."})
    assert response.status_code == 200
    assert response.json() == {"name": "security-2", "instructions": "Inspect auth boundaries.", "capabilities": ["terminal", "browser"]}
    conn, selected, messages = model.call_args.args
    assert conn["id"] == cid and selected == agent_generation.DEFAULT_MODEL
    assert "private-key" not in json.dumps(messages)
    assert messages[-1]["content"] == "Review authentication bugs and exercise the browser."
    assert owner.get("/api/resources/agents").json() == []


@pytest.mark.parametrize("output", ["not json", "{}", '{"name":"bad name","instructions":"x"}',
    '{"name":"security","instructions":"x","mcps":["injected"]}',
    '{"name":"security","instructions":"x","capabilities":["root"]}'])
def test_invalid_model_output_never_becomes_an_agent(owner, monkeypatch, output):
    connection(owner)
    monkeypatch.setattr(providers, "draft_completion", AsyncMock(return_value=output))
    response = owner.post("/api/agents/generate", json={"prompt": "Review code"})
    assert response.status_code == 502
    assert owner.get("/api/resources/agents").json() == []


def test_missing_connection_and_invalid_prompt_do_not_call_model(owner, monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(providers, "draft_completion", model)
    assert owner.post("/api/agents/generate", json={"prompt": "Review code"}).status_code == 400
    connection(owner)
    for prompt in ["   ", "a" * 12001]:
        assert owner.post("/api/agents/generate", json={"prompt": prompt}).status_code == 422
    model.assert_not_called()


def test_generation_honors_selected_model_and_handles_timeout(owner, monkeypatch):
    cid = connection(owner)
    owner.put("/api/settings/agent-generation", json={"connection_id": cid, "model": "custom-model"})
    model = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(providers, "draft_completion", model)
    response = owner.post("/api/agents/generate", json={"prompt": "Review code"})
    assert response.status_code == 504
    assert model.call_args.args[1] == "custom-model"


def test_generation_settings_and_requests_require_sign_in(client):
    assert client.get("/api/settings/agent-generation").status_code == 401
    assert client.put("/api/settings/agent-generation", json={"connection_id": "id", "model": "model"}).status_code == 401
    assert client.post("/api/agents/generate", json={"prompt": "Review code"}).status_code == 401


async def test_agent_drafting_leaves_output_capacity_to_provider(monkeypatch):
    content = json.dumps({"name": "regressions", "instructions": "Review regression risks."})
    completion = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    ))
    monkeypatch.setattr(providers.litellm, "acompletion", completion)
    result = await providers.draft_completion(
        {"type": "gateway", "api_key": "test"}, "model", [{"role": "user", "content": "Draft"}],
    )
    assert result == content
    assert not {"max_tokens", "max_output_tokens", "max_completion_tokens"} & completion.call_args.kwargs.keys()
