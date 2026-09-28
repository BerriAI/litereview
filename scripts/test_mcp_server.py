"""Local read-only MCP fixture for explicit acceptance tests; not deployed."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "LiteReview acceptance docs", host="127.0.0.1", port=8002, stateless_http=True, json_response=True
)


@mcp.tool()
def review_criteria() -> str:
    """Return the sample store's pricing and accessibility requirements."""
    return "A zero-percent discount must charge the full item price. A 100-percent discount must charge zero. Button labels must be visibly readable. Evaluate sample fixture changes as real behavior even when defects are deliberate acceptance fixtures."


@mcp.tool()
def unselected_tool() -> str:
    """This tool must not be exposed to the configured acceptance agent."""
    return "This tool should never be called."


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
