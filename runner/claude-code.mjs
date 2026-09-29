import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire("/opt/litereview/harness/package.json");
const { query } = await import(
  require.resolve("@anthropic-ai/claude-agent-sdk")
);
const {
  config: cfg,
  url,
  instructions,
  prompt,
} = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const builtin = [
  "Read",
  "Glob",
  "Grep",
  ...(cfg.capabilities.includes("terminal") ? ["Bash"] : []),
];
try {
  for await (const message of query({
    prompt,
    options: {
      cwd: "/workspace/repo",
      model: cfg.model,
      maxTurns: 60,
      systemPrompt: {
        type: "preset",
        preset: "claude_code",
        append: instructions,
      },
      tools: builtin,
      allowedTools: [...builtin, "mcp__litereview__*"],
      disallowedTools: [
        "Write",
        "Edit",
        "NotebookEdit",
        "Agent",
        "Task",
        "AskUserQuestion",
      ],
      permissionMode: "dontAsk",
      settingSources: [],
      strictMcpConfig: true,
      skills: [],
      mcpServers: { litereview: { type: "http", url: url + "/mcp" } },
      env: {
        ...process.env,
        ANTHROPIC_BASE_URL: url,
        ANTHROPIC_AUTH_TOKEN: "sandbox-scoped",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1",
        CLAUDE_AGENT_SDK_CLIENT_APP: "litereview/0.1.0",
      },
    },
  })) {
    if (message.type === "assistant")
      for (const block of message.message.content)
        if (block.type === "tool_use")
          console.log(JSON.stringify({ lr_event: "Using " + block.name }));
    if (message.type === "result")
      console.log(JSON.stringify({ result: message }));
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
