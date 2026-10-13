import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from runner.finding_assessment import FindingAssessment
from runner.preview import PreviewDocument


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


class ReviewRuleInstruction(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    enabled: bool = True
    required: bool = True


class BranchCondition(BaseModel):
    connection_id: str = Field(min_length=1, description="Saved gateway or OpenAI connection serving /v1/decisions.")
    model: str = Field(min_length=1, description="An available decision model ID, such as the gateway's Jev deployment.")
    instructions: str = Field(min_length=1, description="Yes/no criterion evaluated against the PR title and body. True runs this branch; false skips it.")
    threshold: float = Field(default=0.5, ge=0, le=1, description="Run when the probability of true is at least this value. Decision failures run the branch, rather than silently dropping coverage.")


class FusionBranch(ModelSelection):
    name: str = Field(default="", description="Optional role label, e.g. Drift and reuse.")
    instructions: str | None = Field(default=None, min_length=1, description="Replace this branch's instructions. Omitted or null inherits the agent instructions; the aggregator keeps its own instructions.")
    skill_ids: list[str] | None = Field(default=None, description="Replace branch skills. Null inherits the agent skills; [] attaches none.")
    mcps: list[dict] | None = Field(default=None, description="Replace branch MCP selections: [{connection_id, tools}]. Null inherits; [] disables MCP tools.")
    capabilities: list[Literal["terminal", "browser", "computer"]] | None = Field(default=None, description="Replace branch capabilities. Null inherits; terminal is always included.")
    rules: list[ReviewRuleInstruction] | None = Field(default=None, description="Replace branch review rules. Null inherits; [] applies none. The aggregator keeps the agent rules.")
    paths: list[str] | None = Field(default=None, description="Optional changed-path gate over the full captured PR diff, including both names of renames. Any matching path qualifies. Repository-relative, case-sensitive: exact files, directories ending in /, * and ? within a segment, ** across directories. Null or [] disables this gate. Evaluated before condition; when both gates are set, both must match.")
    condition: BranchCondition | None = Field(default=None, description="Optional decision-model gate. Null disables this gate. Evaluated once per accepted branch against the snapshotted PR title/body, after any path gate passes and before starting its sandbox.")

    @field_validator("paths")
    @classmethod
    def relative_paths(cls, values):
        if not values:
            return None
        paths = list(dict.fromkeys(p.strip().removeprefix("./") for p in values if p.strip()))
        if not paths or any(not p or p.startswith(("/", "!")) or "\\" in p
                            or any(s in ("", ".", "..") for s in p.rstrip("/").split("/"))
                            or any("**" in s and s != "**" for s in p.split("/")) for p in paths):
            raise ValueError("Use repository-relative files or directories ending in /; ** must be a full path segment. Exclusions are not supported.")
        return paths

    @field_validator("capabilities")
    @classmethod
    def included_tools(cls, values):
        from runner.catalog import review_capabilities

        return None if values is None else review_capabilities(values)

    @field_validator("rules")
    @classmethod
    def unique_rules(cls, values):
        if values is not None and len({rule.id for rule in values}) != len(values):
            raise ValueError("Review rules must have unique IDs within each branch.")
        return values


class Fusion(BaseModel):
    branches: list[FusionBranch] = Field(min_length=1, max_length=16, description="Independent parallel reviewers. The agent's top-level model, instructions, skills, rules and tools configure the final aggregator and supply defaults for branches.")


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


class Agent(ReviewTarget, AgentModel):
    instructions: str = Field(min_length=1)
    output_type: Literal["review", "preview"] = Field(default="review", description="review publishes findings; preview builds a hosted, signed-in review companion from agent-authored HTML and saved media.")
    show_score: bool = Field(default=False, description="Show a server-calculated 1–5 readiness score. Completed reviews with no actionable findings or material gaps get 5/5. Previews are unscored.")
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
    preview: PreviewDocument | None = None
    finding_assessments: list[FindingAssessment] = Field(default_factory=list)

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
