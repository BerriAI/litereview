import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Connection(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: Literal["gateway", "openai", "anthropic", "mcp"] = "gateway"
    base_url: str = ""
    api_key: str = ""
    tools: list[dict] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("Use an http:// or https:// URL.")
        return value.rstrip("/")


class Skill(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    instructions: str = Field(min_length=1, max_length=100_000)
    files: dict[str, str] = Field(default_factory=dict)


class ReviewTarget(BaseModel):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    repos: list[str] = Field(default_factory=list)
    caller_policy: Literal["collaborators", "allowlist"] = "collaborators"
    callers: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def lowercase(cls, v):
        return v.lower()

    @field_validator("callers")
    @classmethod
    def normalize_callers(cls, values):
        return list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))


class AgentModel(BaseModel):
    connection_id: str
    model: str = Field(min_length=1, max_length=250)
    capabilities: list[Literal["terminal", "browser", "computer"]] = Field(default_factory=list)


class Agent(ReviewTarget, AgentModel):
    instructions: str = Field(min_length=1, max_length=20000)
    skill_ids: list[str] = Field(default_factory=list)
    mcps: list[dict] = Field(default_factory=list)


class Member(BaseModel):
    agent_id: str
    count: int = Field(default=1, ge=1, le=10)


class Group(ReviewTarget):
    description: str = Field(default="", max_length=500)
    members: list[Member] = Field(min_length=1, max_length=20)
    repos: list[str] = Field(min_length=1)


class Repository(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    installation_id: int = 0
    setup_command: str = Field(default="", max_length=10000)
    start_command: str = Field(default="", max_length=10000)
    readiness_url: str = ""
    test_secrets: dict[str, str] = Field(default_factory=dict)

    @field_validator("readiness_url")
    @classmethod
    def local_url(cls, v):
        if v:
            from urllib.parse import urlparse

            p = urlparse(v)
            if p.scheme not in ("http", "https") or p.hostname not in ("localhost", "127.0.0.1"):
                raise ValueError("Readiness URL must point to localhost inside the sandbox.")
        return v

    @field_validator("test_secrets")
    @classmethod
    def secret_names(cls, values):
        for key in values:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key.startswith(("LR_", "MODAL_", "GITHUB_")):
                raise ValueError("Use uppercase test secret names; LR_, MODAL_, and GITHUB_ are reserved.")
        return values


class Finding(BaseModel):
    title: str = Field(max_length=200)
    severity: Literal["critical", "high", "medium", "low"]
    explanation: str = Field(max_length=5000)
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    evidence: list[str] = Field(default_factory=list, max_length=10)


class ReviewResult(BaseModel):
    status: Literal["completed", "failed", "timed_out"]
    summary: str = Field(max_length=5000)
    findings: list[Finding] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)


SCHEMAS = {"agents": Agent, "groups": Group, "connections": Connection, "skills": Skill, "repos": Repository}
