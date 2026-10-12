import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class Connection(BaseModel):
    name: str = Field(min_length=1)
    type: Literal["gateway", "openai", "anthropic", "mcp"] = "gateway"
    base_url: str = ""
    api_key: str = ""
    tools: list[dict] = Field(default_factory=list)
    auth_type: Literal["none", "bearer", "oauth"] = "bearer"
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_scope: str = ""

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("Use an http:// or https:// URL.")
        return value.rstrip("/")


class Skill(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    instructions: str = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict, description="Relative supporting-file paths mapped to base64-encoded content. The instructions field contains SKILL.md text.")


class ReviewTarget(BaseModel):
    name: str = Field(min_length=1, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")

    @field_validator("name")
    @classmethod
    def lowercase(cls, v):
        return v.lower()


class DefaultReviewer(BaseModel):
    agent_id: str = Field(min_length=1)


class ModelSelection(BaseModel):
    connection_id: str
    model: str = Field(min_length=1)
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None


class Fusion(BaseModel):
    branches: list[ModelSelection] = Field(min_length=1, max_length=16)


class AgentModel(ModelSelection):
    harness: Literal["codex"] = "codex"
    fusion: Fusion | None = None
    capabilities: list[Literal["terminal", "browser", "computer"]] = Field(
        default_factory=lambda: ["terminal"]
    )

    @field_validator("capabilities")
    @classmethod
    def included_tools(cls, values):
        from runner.catalog import review_capabilities

        return review_capabilities(values)


class ReviewRuleInstruction(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    enabled: bool = True
    required: bool = True


class Agent(ReviewTarget, AgentModel):
    instructions: str = Field(min_length=1)
    trigger_access: Literal["workspace", "anyone"] = Field(default="workspace", description="workspace requires approved callers; anyone allows external human PR commenters. Repository grants still apply. Does not grant dashboard or MCP access.")
    rules: list[ReviewRuleInstruction] = Field(default_factory=list, description="Named requirements supplementing base instructions and skills. Use rule tools to edit one without replacing the array.")
    review_mode: Literal["full", "incremental", "compare"] = Field(default="full", description="full: whole PR each time. incremental: new changes since the last compatible successful review, otherwise full. compare: incremental plus a private full review; both cost credits.")
    minimum_minutes: int = Field(default=0, ge=0, strict=True)
    maximum_minutes: int | None = Field(default=None, ge=1, strict=True)
    max_model_calls: int | None = Field(default=None, ge=1, strict=True)
    skill_ids: list[str] = Field(default_factory=list)
    mcps: list[dict] = Field(default_factory=list, description='Tool selections: [{"connection_id": "saved-mcp-connection-id", "tools": ["discovered_tool_name"]}]. Test the connection to discover valid names.')

    @model_validator(mode="after")
    def compatible_times(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("Review rules must have unique IDs.")
        if self.maximum_minutes is not None and self.maximum_minutes <= self.minimum_minutes:
            raise ValueError("Maximum run time must exceed minimum review time to allow for setup.")
        return self


class Member(BaseModel):
    agent_id: str
    count: int = Field(default=1, ge=1)


class Group(ReviewTarget):
    description: str = ""
    members: list[Member] = Field(min_length=1)


class Access(BaseModel):
    subject: Literal["user", "repo"] = "user"
    name: str = Field(min_length=1)
    scope: Literal["all", "selected"] = "all"
    target_ids: list[str] = Field(default_factory=list)
    identity_sub: str = ""

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        return value.strip().removeprefix("@").lower()

    @model_validator(mode="after")
    def validate_grant(self):
        pattern = (
            r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?"
            if self.subject == "user"
            else r"[a-z0-9_.-]+/[a-z0-9_.-]+"
        )
        if not re.fullmatch(pattern, self.name):
            raise ValueError(
                "Enter a GitHub username." if self.subject == "user" else "Enter a repository as owner/repo."
            )
        self.target_ids = [] if self.scope == "all" else list(dict.fromkeys(self.target_ids))
        return self


class ReviewRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=40)
    target_id: str = Field(min_length=1)
    enabled: bool = True
    events: list[Literal["opened", "ready_for_review", "synchronize", "reopened", "labeled"]] = Field(
        default_factory=lambda: ["opened", "ready_for_review"], min_length=1
    )
    authors: list[str] = Field(default_factory=list)
    label: str = ""

    @field_validator("authors")
    @classmethod
    def usernames(cls, values):
        names = [v.strip().removeprefix("@").lower() for v in values]
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?(?:\[bot\])?", v) for v in names):
            raise ValueError("Enter GitHub usernames, including [bot] for bot accounts.")
        return list(dict.fromkeys(names))

    @model_validator(mode="after")
    def trigger_settings(self):
        self.events = list(dict.fromkeys(self.events))
        self.label = self.label.strip()
        if "labeled" in self.events and not self.label:
            raise ValueError("Choose the label that triggers this review.")
        return self


class Repository(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    installation_id: int = 0
    setup_command: str = ""
    start_command: str = ""
    readiness_url: str = ""
    test_secrets: dict[str, str] = Field(default_factory=dict)
    review_rules: list[ReviewRule] = Field(default_factory=list)

    @field_validator("review_rules")
    @classmethod
    def unique_rules(cls, rules):
        if len({r.id for r in rules}) != len(rules):
            raise ValueError("Automatic review rules must have unique IDs.")
        return rules

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
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    explanation: str
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    evidence: list[str] = Field(default_factory=list)


class ReviewCheck(BaseModel):
    rule_id: str | None = None
    name: str = Field(min_length=1)
    status: Literal["passed", "failed", "not_run"]
    required: bool
    details: str


class RebuttalDecision(BaseModel):
    verdict: Literal["withdraw", "uphold", "uncertain"]
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def withdrawal_requires_evidence(self):
        if self.verdict == "withdraw" and not any(item.strip() for item in self.evidence):
            raise ValueError("Withdrawing a finding requires verified evidence.")
        return self


class ReviewResult(BaseModel):
    status: Literal["completed", "failed", "timed_out"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    checks: list[ReviewCheck] = Field(default_factory=list)
    rebuttal: RebuttalDecision | None = None

    @model_validator(mode="after")
    def required_checks_are_blockers(self):
        for check in self.checks:
            if check.required and check.status == "not_run":
                blocker = f"{check.name}: {check.details or 'Required check was not run.'}"
                if blocker not in self.limitations:
                    self.limitations.append(blocker)
        return self


SCHEMAS = {
    "agents": Agent,
    "groups": Group,
    "connections": Connection,
    "skills": Skill,
    "repos": Repository,
    "access": Access,
}
