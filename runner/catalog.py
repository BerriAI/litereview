"""The pinned Codex runtime installed in every review sandbox."""

HARNESS_VERSIONS = {"codex": "0.158.0"}
HARNESS_LABELS = {"codex": "Codex"}
NPM_PACKAGES = {"codex": "@openai/codex"}


def validate_connection(harness, connection_type):
    if harness != "codex":
        raise ValueError("LiteReview uses Codex. Start a new review with the saved reviewer.")
    if connection_type not in ("openai", "gateway"):
        raise ValueError(
            "Codex requires an OpenAI or gateway connection that supports the Responses API. "
            "For Anthropic models, add a Responses-compatible gateway in Models and select it in PR agents."
        )


def review_capabilities(values):
    """Every reviewer can investigate and run tests in its disposable checkout."""
    return list(dict.fromkeys(["terminal", *values]))
