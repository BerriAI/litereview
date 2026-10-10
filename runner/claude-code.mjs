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
  "Write",
  "Edit",
  "Agent",
  "Task",
  ...(cfg.capabilities.includes("terminal") ? ["Bash"] : []),
];
try {
  let sessionId;
  let nextPrompt = prompt;
  do {
    for await (const message of query({
      prompt: nextPrompt,
      options: {
        cwd: "/workspace/repo",
        model: cfg.model,
        ...(sessionId ? { resume: sessionId } : {}),
        systemPrompt: instructions,
        tools: builtin,
        allowedTools: [...builtin, "mcp__litereview__*"],
        disallowedTools: ["AskUserQuestion"],
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
      if (message.session_id) sessionId = message.session_id;
      if (message.type === "result" && message.is_error)
        throw new Error("Claude Code could not complete this review.");
      if (message.type === "assistant")
        for (const block of message.message.content)
          if (block.type === "tool_use")
            console.log(JSON.stringify({ lr_event: "Using " + block.name }));
      if (message.type === "result")
        console.log(JSON.stringify({ result: message }));
    }
    if (
      !cfg.minimum_review_seconds ||
      !sessionId ||
      (await (await fetch(url + "/status")).json()).finished
    )
      break;
    nextPrompt =
      "Continue reviewing in this session. Check lr_review_clock, investigate and verify until the minimum duration elapses, then call lr_finish_review. Do not sleep or busy-wait.";
  } while (true);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
