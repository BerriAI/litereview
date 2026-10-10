# LiteReview v1

Status: beta · 10 October 2026. This document records product behavior and design constraints. See [README.md](README.md) for setup and [validation](docs/VALIDATION.md) for the scope of test evidence.

## 1. What we are building

A small web app for configuring reusable PR review agents and calling them directly or in optional groups from GitHub comments. Each agent instance reviews the same PR commit in its own Modal sandbox. LiteReview acknowledges accepted requests with 👀 and posts one final comment with each agent’s findings, “No issues found,” or a clear failure status.

The purpose of v1 is to run real reviews and judge whether the experience is useful. Ship the complete loop before adding more orchestration.

## 2. The core objects

| Object | Owns |
| --- | --- |
| **PR agent** | Unique command name, instructions, model, enabled tools, attached MCP connections, attached skills |
| **Agent group** | Command name, references to PR agents, instance count for each reference |
| **Connection** | A reusable model/provider connection or MCP server connection |
| **Skill** | Name, description, `SKILL.md`, optional supporting files |
| **Review run** | PR and commit, configuration snapshot, individual agent jobs, results and artifacts |

An agent is configured once and can belong to several groups. Groups reference agents; they do not copy them. Instance counts belong to group membership. Updating a shared agent affects future runs in all its groups. An accepted run uses an immutable snapshot of the agent, skill contents, and group configuration. Store secret references in snapshots, not credential values.

GitHub commands accept **agents and groups** together. Their command names share one case-insensitive unique namespace. Access is managed centrally for People and Repositories. Each entry grants all current and future agents/groups or specific target IDs; both the person and repository must permit each requested target. Group access includes its members when invoked through that group. Groups are optional convenience bundles.

## 3. The user flow

1. A workspace member connects GitHub, selects repositories, and adds a model connection.
2. They optionally add reusable MCP connections and skills.
3. They create PR agents, choosing a model, instructions, capabilities, and attachments for each.
4. They optionally create groups by selecting existing agents and instance counts, then use People and each repository's Review permissions to configure invocation grants.
5. An allowed user comments on a PR:

   ```text
   @litereview frontend pentest security
   ```

6. LiteReview adds 👀 to the request and a separate progress comment for each selected instance. Each comment switches from 👀 to ✅ and publishes that agent’s response as it finishes. Findings become linked inline threads where possible. Runs shows collapsed agent reports by default.

If `frontend` and `pentest` are individual agents and `security` contains five instances, this runs seven independent jobs.

Names are case-insensitive. Ignore repeated agent or group names within one command. A directly selected agent runs one instance; if it is also included through a group, both selections run independently. Expand different groups independently: an agent referenced in two selected groups runs once for each membership, including its configured instance count. Label results with the group, agent, and instance so this is visible.

V1 accepts a command as the entire content of a new top-level PR comment. Unknown names or denied access reject the whole invocation with a short explanation. Only authorized callers receive configuration details. Ignore bot comments and ordinary issue comments. Edited comments and automatic reviews on pushes are outside v1.

## 4. Agent capabilities

**Every agent instance runs in a Modal sandbox. There is no “code only versus sandbox” selector.** Models run at the selected provider or gateway; Modal runs the pinned Codex runtime and its tools. The model endpoint must support the Responses API. Direct Anthropic connections require a compatible gateway.

Repository and terminal access are included for every reviewer. The agent editor offers browser and computer use as additional capabilities:

| Capability | Behavior |
| --- | --- |
| **Repository — always included** | Read the PR diff, list/read files, and search the checked-out repository |
| **Terminal — always included** | Run commands, install dependencies, execute tests, and start the app inside the sandbox |
| **Browser** | Use Playwright to navigate, inspect page structure, click, fill forms, and see screenshots of the rendered page |
| **Computer use** | See the virtual desktop through screenshots and operate it through mouse clicks, dragging, typing, keyboard shortcuts, and scrolling |

Computer use is a first-class built-in capability, not something the user must assemble through MCP. It controls a private Linux desktop in Modal, never the user’s computer. The initial desktop image contains a lightweight window manager, virtual display, and Chromium. V1 supports this Linux environment; macOS and Windows desktops are outside scope.

Both capabilities provide visual feedback to the model. Screenshot tools return actual image content in the model conversation, not just a file path, textual description, or saved artifact. The model must be able to inspect what the app rendered.

Computer use starts with a desktop screenshot and follows a see → act → see loop, returning a fresh screenshot after an action or short action batch. Browser use provides page structure and screenshots; take screenshots when opening the page and at the states being reviewed, then inspect the visible result after meaningful interactions. A review can therefore catch layout issues, clipped text, overlays, and other failures that page structure alone would miss.

When Browser and Computer use are both enabled, they operate the same Chromium session. Both require a model that accepts images and tool calls. Validate this before a review starts and report an actionable configuration error if it is unsupported. Do not silently substitute a text-only model. Pass screenshots through the provider/gateway image-input format and retain useful screenshots as review evidence. Continuous video or a live desktop viewer is not required in v1.

A reviewer can inspect code without running the project, but terminal access remains available. Adding skills does not grant extra tools. Enforce configured browser, computer, and MCP tool access in the runner.

The agent’s job is to review. It can create temporary test files and build artifacts inside its disposable sandbox when execution is enabled. It does not commit, push, open fix PRs, or modify the source PR. GitHub publishing credentials stay outside the agent sandbox.

## 5. Models, MCPs, and skills

### Models

Support direct OpenAI and Anthropic credentials and an OpenAI-compatible gateway connection, including LiteLLM Gateway. A gateway is optional.

Each connection has a name, provider/type, optional base URL, and secret credential. Discover models where supported; allow manual model IDs. Include a connection test. A listed model is not assumed to support images or tool calls merely because it appears in a model list.

Agents select a connection and model. Use the LiteLLM Python SDK for model adaptation; do not build a model gateway product or depend on the agent control plane.

### MCP connections

Keep a workspace library of named remote HTTP MCP servers. Each connection has its URL, authentication, and exposed tool selection. Agents explicitly select connections and permitted tools from that library.

V1 supports remote Streamable HTTP MCP servers. Arbitrary local stdio servers, plugin marketplaces, and OAuth integration catalogs are deferred. Built-in Terminal, Browser, and Computer use cover the local capabilities needed for the initial version.

### Skills

A workspace member can create a skill by pasting Markdown or uploading a skill folder as a ZIP containing `SKILL.md` and optional supporting files. Unpack archives without application size or file-count caps; reject paths outside the skill directory.

The agent editor has a multi-select list of workspace skills. At run start, materialize only the attached skills in that sandbox. Give the agent their names, descriptions, paths, and `SKILL.md` instructions; supporting files are available through repository/file-reading tools. Saved agent instructions define the task, scope, workflow, language, and voice; attached skills support them and yield when they conflict. Skills cannot expand tool permissions.

No skill marketplace, dependency installer, or separate version-management UI is needed. Runs retain the skill contents they used.

## 6. Web app and UI standard

Use the shared patterns from the [LiteLLM dashboard](https://github.com/BerriAI/litellm/tree/main/ui/litellm-dashboard) and the self-contained [UI guide](docs/UI.md).

- Compact left sidebar, clear page titles, simple tables, and ordinary labeled forms.
- Reuse the LiteLLM dashboard’s shared component patterns and primitives; no new component system.
- Use Geist/Geist Mono typography, ink `#272B37`, restrained violet `#5B3FD1`, neutral surfaces, and thin borders. The current interface is light-only.
- No overview dashboard, command composer, oversized metric cards, or diagram editor.

The workspace navigation is:

| Page | Main interaction |
| --- | --- |
| **PR agents** | List saved agents; create/edit command name, instructions, model, capabilities, MCPs, and skills; show which groups use an agent |
| **Agent groups** | Create/edit a group; choose existing agents and instance counts; show its GitHub command |
| **Models** | Manage shared model-provider and gateway connections |
| **MCP servers** | Connect remote tools with OAuth, bearer tokens, or no authentication |
| **Skills** | Create, upload, inspect, and edit reusable skills; show which agents use them |
| **Repositories** | Connect GitHub, configure review environments, and manage repository Review permissions |
| **People** | Inspect linked identities and restrict review invocation |
| **Settings** | Recover GitHub credentials or reset/undo setup |
| **Runs** | List PR reviews; open per-agent status, findings, logs, and screenshots |

Linked members open **PR agents**; unlinked Google members start with GitHub linking and can still open the workspace. Selecting an agent opens its editor. Selecting a group opens its member list and settings. Group membership controls selection and count; editing a shared agent clearly states which groups will be affected.

Creation and editing must save real configuration, show validation errors, and support cancellation. Empty states lead directly to the next useful action. GitHub remains the place where users trigger reviews.

Initial setup is one trusted workspace per deployment. Hosted teams sign in with Google Workspace SSO, then link GitHub for review invocation. Every admitted member has the same management access. Local development can use an access key and a legacy GitHub sign-in allowlist when SSO is disabled. An access key cannot bypass required SSO. There is no separate administrator role, tenant isolation, billing, or organization-management UI.

## 7. Repository and caller setup

Use a GitHub App installed on selected repositories, with repository contents read, metadata read, pull requests write, and issues write permissions. A private GitHub App must be owned by the account or organization where it is installed; organization-owner involvement may be required for setup. Subscribe to issue-comment events. The API uses installation credentials for PR inspection and comment publishing; workers receive repository data without those credentials. Verify the exact comment and collaborator-access endpoints against these permissions during the first integration step.

With SSO, **People** derives membership from approved Google sign-ins and verified GitHub links. Members can invoke all reviewers unless a person restriction or repository permission disallows it. Local access-key mode uses a manual People allowlist. Each connected repository's **Review permissions** allows all agents/groups or selected targets. Both the person and repository grants must permit every requested name. GitHub repository access is checked independently. Newly connected repositories start with All access; explicit restrictions survive syncing and relinking. Review-trigger restrictions do not remove workspace management access.

Check installation, repository, and actor permissions on every trigger. Run pages and artifacts require workspace sign-in, and all admitted members can see them. A linked GitHub identity can request reviews after the browser session expires. Google account suspension does not automatically revoke that link; offboarding must explicitly restrict the person or unlink GitHub. No Google Directory synchronization is implemented.

Each repository can optionally configure setup commands, an app start command, readiness URL, and references to test-only secrets. These are workspace settings, not executable instructions taken automatically from a PR. They support test, browser, and desktop reviews. Agents attempt relevant dependency setup using documented commands, manifests, and pinned versions before reporting environment blockers. Missing dependencies make a review incomplete only when they block work required by the configured task.

## 8. Implementation shape

Use a small standalone implementation:

- **Web:** React, TypeScript, Vite, and the shared LiteLLM UI patterns.
- **API:** Python/FastAPI serving the built web app and handling configuration, GitHub events, and results.
- **Database:** Postgres for configuration, runs, jobs, and artifact metadata.
- **Execution:** one durable API dispatcher backed by Postgres, and one configurable review runner used by every Modal sandbox.
- **Artifacts:** private Postgres records for full screenshots and logs, served through authenticated app routes. This avoids a separate storage service in v1.

Package the web/API as one application deployment. Provide Docker Compose for the application and Postgres during setup, plus guided GitHub App setup. Modal images and the named Modal app are created on demand; there is no separate runtime deployment command. No Redis, Celery, workflow engine, or managed-agent platform is required.

The [agent control plane](https://github.com/LiteLLM-Labs/litellm-agent-control-plane) is a reference for agent editing, skill attachments, MCP settings, and credentials. Selectively reuse useful components or patterns. Do not make its runtime/session platform a required service.

### Execution sequence

1. Validate the GitHub webhook signature; deduplicate delivery; parse the command and check access.
2. Resolve the PR’s current base/head SHAs and snapshot selected group memberships, agents, models, capabilities, and skills. Create the run and jobs in Postgres.
3. Acknowledge the request comment with 👀 and dispatch from the durable job queue. Reaction retries are idempotent. A stored pending run must be recoverable if dispatch is interrupted.
4. For every job, create a fresh sandbox, stage the repository and diff, mount attached skills, and initialize enabled tools. Run configured setup only when the job needs the runnable application environment.
5. Run Codex: request a model response, execute permitted tool calls, return tool results, and continue until it returns a structured review or reaches its limits.
6. Persist results and artifacts. Terminate the sandbox in cleanup, with Modal’s required sandbox lifetime as a backstop.
7. As each job reaches a terminal state, update its own GitHub comment with its response and links to inline finding threads on valid right-side lines of the captured diff. Keep unanchored or oversized findings in the agent comment. Demote model headings below the agent title. Recover marked comments and threads after lost responses. Replace the agent title’s 👀 with ✅ and remove its eyes reaction; remove the request’s eyes when every result is published. GitHub has no check-mark reaction. Keep incomplete status explicit. One failed agent does not discard other agents’ results.

Jobs run concurrently subject to Modal capacity and a deployment concurrency limit. Show queued work honestly. Use atomic job claims and idempotent publication so retries do not duplicate reviews. Do not automatically rerun completed agents.

Keep long-lived model and remote MCP credentials in the API service. Workers receive active-job-scoped access to small internal model/MCP relay endpoints, restricted to their configured model/tools and active-run limits. These are internal application endpoints, not a separately deployed gateway. Do not leave GitHub credentials in the staged checkout or worker environment.

Do not impose default time or model-call budgets. Owners may select a maximum run time and/or maximum model calls per agent, independently of its minimum review time. Blank fields mean no LiteReview limit; reaching a selected cap produces an incomplete result. Modal still requires a sandbox lifetime of at most 24 hours. Do not impose application size or count caps on repository archives, PR diffs, skills, screenshots, logs, tool output, trace content, reviewer instructions, or review results. Preserve full content. Stream archives and extraction to keep memory use independent of repository size. Split long published results only to satisfy GitHub's per-comment limit. Model and MCP requests have no application deadline; user-selected run/command budgets and upstream platform capacities still apply.

## 9. Results and basic failure behavior

Each job returns:

```text
status: completed | failed | timed_out
findings: [{ title, severity, explanation, file?, line?, evidence[] }]
summary: the agent's primary response in its configured voice
checks: [{ name, status: passed | failed | not_run, required, details }]
limitations: blockers to completing the configured task
```

Show the agent's response directly. Use **No issues found** only as a fallback for a completed review with no response, findings, or blockers. Optional unrun checks remain visible without making the review incomplete. Required unrun checks become blockers. An executed failing test is evidence, not automatically unfinished work. Preserve useful partial findings from failed or timed-out jobs and label them incomplete.

Post one GitHub summary with a section for each reviewer. Include a group label when called through a group and instance numbers only when multiple instances need distinguishing. Show each agent's response, findings, and blockers directly; collapse verification results and timing metadata under Checks and execution details. Put the reviewed commit and full-run link in a compact footer. Put detailed logs and screenshots in the authenticated run page. Preserve the agent's wording and Markdown without a judge agent or automatic rewriting stage.

If the PR advances during a review, label the result with the reviewed SHA and indicate that newer commits exist. Never imply it covers the new head. Users rerun by posting another command.

For v1, the output is a top-level summary comment. Resolvable inline GitHub review threads are a follow-up, not part of this first build.

## 10. What “working v1” means

The build is ready to try when all of these work against a real test repository:

1. Connect a direct model provider and, separately, a gateway; run a real agent through each path.
2. Create an agent with skills and tools; reuse it in two groups without copying it. Confirm edits affect future runs in both groups.
3. Trigger three groups totaling seven instances from one PR comment. Confirm separate sandboxes, one shared commit snapshot, and separate attributed agent comments, per-agent completion indicators, and linked finding threads.
4. Complete a repository-only review, a test/Playwright review, and a **computer-use review that actually observes screenshots and operates the virtual desktop**. Verify that browser and desktop screenshots reach the model as image inputs and that it checks the visible result after acting. Use a controlled PR with an observable UI failure for the last two, including a visual issue that cannot be established from page structure alone.
5. Confirm selected skills/supporting files are available and unattached skills/tools are unavailable. Verify a configured remote MCP tool call.
6. Show real findings, a clean completed review, and an incomplete review caused by a setup/model failure accurately.
7. Reject an unauthorized caller, handle a duplicated webhook once, and clean up sandboxes after completion and timeout.
8. Verify the create/edit/reuse flows and run results on desktop and narrow screens using the specified UI standard.

No auto-fixes, pushes, scheduled reviews, persistent chat sessions, custom agent runtimes, TOML import/export, or enterprise administration in this version. Codex subagents are available; the main reviewer submits the combined result.

## 11. Build order

1. **One real review:** GitHub trigger → one Modal agent → structured result → one comment.
2. **Configuration UI:** reusable agents, groups, connections, skills, and run history; persist the real objects.
3. **Capabilities:** Terminal, Playwright, virtual desktop computer use, and remote MCPs; verify model compatibility and isolation.
4. **Parallel review:** group expansion, instance counts, snapshotting, failure handling, and the acceptance pass above.

All four steps are part of v1. Deliver a running test deployment and a reviewed example PR so the first evaluation is based on actual use.
