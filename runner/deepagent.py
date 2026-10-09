"""Deep Agents runs locally inside this job's Modal sandbox."""

import asyncio
import json
import math
import sys
from pathlib import Path

import httpx
from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import StructuredTool


async def main():
    data = json.loads(Path(sys.argv[1]).read_text())
    cfg, url = data["config"], data["url"]
    async with httpx.AsyncClient(timeout=180) as client:
        definitions = (await client.get(url + "/tools")).json()
        tools = []
        for item in definitions:

            def make_call(name):
                async def call(**arguments):
                    result = (
                        await client.post(url + "/call", json={"name": name, "arguments": arguments})
                    ).json()
                    return [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:" + block["mimeType"] + ";base64," + block["data"]},
                        }
                        if block["type"] == "image"
                        else block
                        for block in result["content"]
                    ]

                return call

            tools.append(
                StructuredTool(
                    name=item["name"],
                    description=item["description"],
                    args_schema=item["inputSchema"],
                    coroutine=make_call(item["name"]),
                )
            )
        allowed = {item["name"] for item in definitions} | {
            "task",
            "ls",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "write_todos",
        }
        if "terminal" in cfg["capabilities"]:
            allowed.add("execute")

        class ReviewTools(AgentMiddleware):
            async def awrap_model_call(self, request, handler):
                return await handler(
                    request.override(
                        tools=[
                            t
                            for t in request.tools
                            if (t.name if hasattr(t, "name") else t["name"]) in allowed
                        ]
                    )
                )

            async def awrap_tool_call(self, request, handler):
                name = request.tool_call["name"]
                if name not in allowed:
                    raise ValueError("This tool is disabled for PR reviews.")
                print(json.dumps({"lr_event": "Using " + name}), flush=True)
                return await handler(request)

        if cfg["connection"]["type"] == "anthropic":
            from langchain_anthropic import ChatAnthropic

            model = ChatAnthropic(model=cfg["model"], api_key="sandbox-scoped", base_url=url, max_tokens=8192)
        else:
            from langchain_openai import ChatOpenAI

            model = ChatOpenAI(
                model=cfg["model"], api_key="sandbox-scoped", base_url=url + "/v1", max_tokens=8192
            )
        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=data["instructions"],
            backend=LocalShellBackend(root_dir="/workspace/repo"),
            middleware=[ReviewTools()],
        )
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": data["prompt"]}]}, {"recursion_limit": math.inf}
        )
        while (
            cfg.get("minimum_review_seconds") and not (await client.get(url + "/status")).json()["finished"]
        ):
            result = await agent.ainvoke(
                {
                    "messages": [
                        *result["messages"],
                        {
                            "role": "user",
                            "content": "Continue reviewing. Check lr_review_clock, investigate and verify "
                            "until the minimum time elapses, then call lr_finish_review. Do not sleep or busy-wait.",
                        },
                    ]
                },
                {"recursion_limit": math.inf},
            )


if __name__ == "__main__":
    asyncio.run(main())
