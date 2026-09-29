import fs from "node:fs";
const {
  createAgentSession,
  ModelRuntime,
  SessionManager,
  DefaultResourceLoader,
} =
  await import("/opt/litereview/harness/node_modules/@earendil-works/pi-coding-agent/dist/index.js");
const {
  config: cfg,
  url,
  instructions,
  prompt,
  state,
} = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const modelsPath = state + "/models.json";
fs.writeFileSync(
  modelsPath,
  JSON.stringify({
    providers: {
      litereview: {
        api:
          cfg.connection.type === "anthropic"
            ? "anthropic-messages"
            : "openai-completions",
        baseUrl: cfg.connection.type === "anthropic" ? url : url + "/v1",
        apiKey: "sandbox-scoped",
        models: [
          {
            id: cfg.model,
            name: cfg.model,
            reasoning: false,
            input: ["text", "image"],
            contextWindow: 128000,
            maxTokens: 8192,
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          },
        ],
      },
    },
  }),
);
const modelRuntime = await ModelRuntime.create({
  modelsPath,
  authPath: state + "/auth.json",
  allowModelNetwork: false,
});
const definitions = await (await fetch(url + "/tools")).json();
const customTools = definitions.map((t) => ({
  name: t.name,
  label: t.name,
  description: t.description,
  parameters: t.inputSchema,
  execute: async (_id, args) => {
    const result = await (
      await fetch(url + "/call", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: t.name, arguments: args }),
      })
    ).json();
    return { content: result.content, details: { isError: result.isError } };
  },
}));
const loader = new DefaultResourceLoader({
  cwd: "/workspace/repo",
  agentDir: state + "/pi",
  noExtensions: true,
  noSkills: true,
  noPromptTemplates: true,
  noThemes: true,
  noContextFiles: true,
  appendSystemPrompt: [instructions],
});
await loader.reload();
const { session } = await createAgentSession({
  cwd: "/workspace/repo",
  agentDir: state + "/pi",
  modelRuntime,
  model: modelRuntime.getModel("litereview", cfg.model),
  thinkingLevel: "off",
  tools: [
    ...definitions.map((t) => t.name),
    ...(cfg.capabilities.includes("terminal") ? ["bash"] : []),
  ],
  customTools,
  resourceLoader: loader,
  sessionManager: SessionManager.inMemory(),
});
session.subscribe((e) => {
  if (e.type === "tool_execution_start")
    console.log(JSON.stringify({ lr_event: "Using " + e.toolName }));
});
await session.prompt(prompt);
session.dispose();
