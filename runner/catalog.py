"""Pinned runtimes. Only the selected entry is installed in a job's image."""

HARNESS_VERSIONS = {
    "builtin": "1",
    "pi": "0.87.1",
    "opencode-v1": "1.18.33",
    "opencode-v2": "2.0.18",
    "codex": "0.158.0",
    "claude-code": "0.3.284",
    "deepagents": "0.7.19",
}
HARNESS_LABELS = {
    "builtin": "Built-in",
    "pi": "Pi",
    "opencode-v1": "OpenCode V1",
    "opencode-v2": "OpenCode V2",
    "codex": "Codex",
    "claude-code": "Claude Code",
    "deepagents": "Deep Agents",
}
NPM_PACKAGES = {
    "pi": "@earendil-works/pi-coding-agent",
    "opencode-v1": "opencode-ai",
    "opencode-v2": "@opencode/cli",
    "codex": "@openai/codex",
    "claude-code": "@anthropic-ai/claude-agent-sdk",
}


def validate_connection(harness, connection_type):
    if harness not in HARNESS_VERSIONS:
        raise ValueError("Unknown harness.")
    if connection_type == "mcp":
        raise ValueError("Choose a model connection.")
    if harness == "codex" and connection_type == "anthropic":
        raise ValueError("Codex needs an OpenAI or gateway connection that supports the Responses API.")
    if harness == "claude-code" and connection_type == "openai":
        raise ValueError(
            "Claude Code needs an Anthropic or gateway connection that supports the Messages API."
        )


def review_capabilities(values):
    """Every reviewer can investigate and run tests in its disposable checkout."""
    return list(dict.fromkeys(["terminal", *values]))
