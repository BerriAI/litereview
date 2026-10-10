"""Create editable reviewer drafts without saving agents or invoking review tools."""
import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import providers
from .db import Resource, Session
from .schemas import ReviewTarget
from .security import require_user

router = APIRouter(dependencies=[Depends(require_user)])
RESOURCE_ID = "agent-generation-settings"
DEFAULT_MODEL = "anthropic/claude-haiku-5-5"
slots = asyncio.Semaphore(4)


class GenerationSettings(BaseModel):
    connection_id: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=250)

    @field_validator("model")
    @classmethod
    def model_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a model ID.")
        return value.strip()


class Prompt(BaseModel):
    prompt: str = Field(min_length=3, max_length=12000)

    @field_validator("prompt")
    @classmethod
    def meaningful(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("Describe what this reviewer should do.")
        return value.strip()


class Draft(ReviewTarget):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    instructions: str = Field(min_length=1, max_length=20000)
    capabilities: list[Literal["terminal", "browser", "computer"]] = Field(default_factory=lambda: ["terminal"])

    @field_validator("instructions")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Instructions must not be empty.")
        return value.strip()


def model_connection(db, connection_id):
    row = db.get(Resource, connection_id)
    if not row or row.kind != "connections" or row.data.get("type") not in ("gateway", "openai", "anthropic"):
        raise HTTPException(400, "Choose a model connection in Settings → Agent creation.")
    return row


@router.get("/api/settings/agent-generation")
def get_settings():
    with Session() as db:
        saved = db.get(Resource, RESOURCE_ID)
        if saved:
            return saved.data
        connections = db.scalars(select(Resource).where(Resource.kind == "connections").order_by(Resource.id)).all()
        conn = next((r for kind in ("gateway", "anthropic") for r in connections if r.data.get("type") == kind), None)
        return {"connection_id": conn.id if conn else "",
                "model": "claude-haiku-5-5" if conn and conn.data["type"] == "anthropic" else DEFAULT_MODEL}


@router.put("/api/settings/agent-generation")
def save_settings(data: GenerationSettings):
    with Session() as db:
        model_connection(db, data.connection_id)
        row = db.get(Resource, RESOURCE_ID, with_for_update=True)
        if not row:
            row = Resource(id=RESOURCE_ID, kind="system", data={})
            db.add(row)
        row.data = data.model_dump()
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Agent creation settings changed. Reload and try again.") from None
    return data.model_dump()


SYSTEM_PROMPT = """Create a reusable PR review agent from the user's description.
Return ONLY a JSON object with name, instructions, and capabilities.
name: a short command name starting with a letter, using only letters, numbers, hyphens or underscores.
instructions: clear, self-contained instructions that preserve the user's intent, scope and desired output.
For bug reviewers, require concrete reachable failure scenarios, affected code evidence, and verification where practical; allow no findings, reject speculative/style-only complaints, and state actual verification limitations.
The reviewer receives the PR diff and an isolated checkout and runs in Codex. It can inspect code, run commands and tests. It cannot push fixes. Do not invent connected tools, skills, credentials, repository setup, or access to external services. Do not promise comprehensive bug coverage.
capabilities: always include terminal; include browser only for requested browser/app interaction and computer only for requested desktop interaction.
Do not choose models, create resources, run commands, or perform the task yourself. Produce an editable draft. Instructions can be in the user's language. No Markdown fences around the JSON."""


@router.post("/api/agents/generate")
async def generate(data: Prompt):
    config = get_settings()
    with Session() as db:
        model_connection(db, config["connection_id"])
    connection = providers.connection(config["connection_id"])
    if slots.locked():
        raise HTTPException(429, "Agent creation is busy. Try again shortly.")
    async with slots:
        try:
            content = await asyncio.wait_for(providers.draft_completion(connection, config["model"], [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": data.prompt},
            ]), timeout=65)
            text = content.strip()
            if text.startswith("```") and text.endswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            draft = Draft.model_validate(json.loads(text))
        except TimeoutError:
            raise HTTPException(504, "Draft creation timed out. Your prompt is kept; try again or change the model in Settings.") from None
        except (ValidationError, ValueError, TypeError, AttributeError, IndexError):
            raise HTTPException(502, "The model did not return a usable agent draft. Try again or change the model in Settings.") from None
    with Session() as db:
        names = {r.data["name"].lower() for r in db.scalars(select(Resource).where(Resource.kind.in_(("agents", "groups"))))}
    base, suffix = draft.name, 2
    while draft.name in names:
        draft.name = f"{base}-{suffix}"
        suffix += 1
    draft.capabilities = list(dict.fromkeys(["terminal", *draft.capabilities]))
    return draft.model_dump()
