# Configure reviewers

Choose what your reviewers investigate and which tools they can use. For your first review, use the default instructions and a single model in the [deployment guide](DEPLOYMENT.md#get-your-first-review). Customize the reviewer after that works.

- [Review instructions](#review-instructions)
- [Models and runtime](#codex-runtime)
- [Capabilities and groups](#capabilities)
- [MCP tools](#connect-mcp-tools)
- [Read review results](#read-review-results)

## Review instructions

To create a reviewer from a description, open **PR agents → Create agent → Create from prompt**. Describe its focus and choose **Generate draft**. Review the generated name, instructions, and capabilities, select the review model, then **Save agent** and **Test agent**. Nothing is saved or run on a PR until you take those actions. The draft keeps your prompt if generation fails.

Draft generation defaults to Haiku 5.5 through a saved gateway or Anthropic connection. Change its connection and model under **Settings → Agent creation**. This is separate from the agent’s review model. Generation uses one model request and provider credits; its cost is separate from the PR review totals on Spend.

Comment `@litereview` alone to run the workspace's default reviewer. The first reviewer becomes the default automatically. Under **PR agents**, choose **Set default** on an agent’s row at any time; the choice applies to future runs. Existing workspaces with one reviewer can use the bare command immediately. Workspaces with several reviewers choose a default once. To select specific agents or groups, name them in the comment, such as `@litereview frontend security`. Caller and repository permissions apply to both forms.

You can include ordinary wording, such as `@litereview code-review review this pls`. LiteReview matches exact saved agent and group names after the mention in the same paragraph, ignoring capitalization and other words. Generated footers in separate paragraphs do not affect the command. Commands inside blockquotes, code blocks, inline code, or links do not start reviews. Extra wording does not change the reviewer's saved instructions. If no name matches, LiteReview explains that the command is unavailable; it does not fall back to the default reviewer.

The agent’s **Review instructions** define its task, scope, workflow, language, and voice. Attached skills support those instructions; the agent instructions take precedence when they conflict. Codex supplies the agent runtime, tools, and result delivery. An agent can produce a narrative response without bug findings. Credentials, enabled tools, and configured time limits still apply.

Agents attempt relevant dependency setup using the project’s manifests and documented commands before reporting an environment blocker. Build tools, Python, Node, and uv are available; project-specific packages and toolchains can be installed in the disposable sandbox. Optional tests that were not run appear under **Checks** without making the review incomplete. A required check that could not run, or other unfinished work required by the configured task, is reported as incomplete. An executed test that fails is evidence, not automatically an incomplete review.

Set **Minimum review time (minutes)** when creating a reviewer or under **PR agents → reviewer → Review instructions**. For example, save `5` for `frontend` and `10` for `security`, then comment:

```text
@litereview frontend security
```

Every instance uses its reviewer's saved minimum, including when called through a group. The clock starts after repository/tool setup. `0` means no minimum and preserves the normal behavior for existing reviewers. Use whole minutes; LiteReview does not impose a workspace ceiling. Changing the setting affects future reviews; accepted jobs keep their saved duration. Comment time options are rejected with instructions to use the UI setting. Private harness checks do not run for this minimum.

The harness checks a monotonic clock, asks the agent to keep investigating and verifying, and rejects early completion. The API independently checks elapsed time. Codex continues the same thread if it returns before the minimum without submitting. This does not invoke the interactive `/goal` slash command. Runs have no LiteReview time or model-call limit by default. To set one, expand **Optional limits** in the agent editor and enter **Maximum run time (minutes)**, **Maximum model calls**, or both. Leave a field blank to remove its limit. Maximum run time includes setup and must exceed the minimum; a minimum never creates a maximum. Groups use each agent’s saved limits, and accepted jobs keep their snapshots. Provider failures and reached limits produce an incomplete review, never a successful early completion. [Modal imposes a 24-hour sandbox lifetime](https://modal.com/docs/guide/sandboxes#timeouts); LiteReview uses that full lifetime when no maximum is selected. Runs do not yet resume across sandbox replacements. Existing MCP selections, skills, visual tools, models, and instructions are preserved.

## Codex runtime

Every reviewer runs Codex 0.158.0 in its own Modal sandbox. Choose the model, instructions, skills, and tools; LiteReview installs and configures Codex automatically.

Use an OpenAI connection or a gateway that supports the Responses API. A gateway can route compatible requests to other providers, including Anthropic. Direct Anthropic connections cannot run reviewers. Browser and desktop checks require image-capable models. Under **PR agents**, select your reviewer and choose **Test agent** to check Codex, the model, sample skill loading, and enabled terminal/browser/desktop tools on a sample project. This private check consumes Modal and model credits; it does not access a GitHub repository, post comments, execute your review instructions, or call attached MCP tools.

Every reviewer includes terminal access to inspect files, install dependencies, run tests, and create reproducers in its disposable checkout. Instructions and skills can request Codex subagents; the main reviewer combines their evidence into one result. Browser, computer use, attached skills, and selected MCP tools remain configurable. The checkout contains the supplied PR commit's source archive, without Git history. LiteReview never pushes changes.

Under **PR agents → reviewer → Model**, expand **Reasoning effort** to use the model default or request Low, Medium, High, or Extra high. Supported levels depend on your model and gateway. Higher effort can increase review time and cost. Run **Test agent** after changing it; that check uses the selected effort. Existing reviewers keep their model default until you choose a level.

### Combine independent reviews with Fusion

Open **PR agents → reviewer → Model** and choose **Add model**. Select another model to enable fusion automatically, with up to 16 models. Every model uses the same review instructions, skills, and enabled tools. The question mark beside **Add model** explains how they work together. Remove extra models to return to a single reviewer.

Choose the **Final review model** below the selected models, then save and run **Test agent**. The test exercises every stage on a private sample project. For a PR review, branches run in separate checkouts; the aggregator gets a fresh checkout and their reports, verifies the claims, and publishes one final review. In **Runs**, expand **Fusion** to inspect each branch's result and activity. Branch results start collapsed. If a branch fails, its incomplete coverage remains in the final result. If every branch fails, aggregation does not run.

A fusion review costs the sum of every branch and the aggregator. Maximum run time and model-call limits apply separately to each stage; minimum review time applies to each branch. The workspace concurrency setting can queue branches when capacity is full. Choose **Set default** on this reviewer’s row to invoke the whole fusion with `@litereview`.

For an independent bug review, copy the [example review instructions](../examples/reviewers/independent-bugs.md) into the reviewer's instructions. The example traces callers and consumers, checks boundary cases and repeated operations, and verifies whether tests exercise their intended behavior. Leave external review-service MCP tools unselected when evaluating it against another reviewer. Measure quality and cost on your own PRs before making it a required check.

The full PR context is stored in a sandbox file, and the diff remains available through review tools. Large diffs do not travel as command-line arguments or oversized initial prompts. Follow-up turns and timed continuations keep the same Codex session and cache key. Cache hits and savings depend on the selected gateway and provider; independent reviewers have separate sessions.

The runtime version is pinned in `runner/catalog.py` and recorded with every job. See [runtime validation](VALIDATION.md#codex-runtime-validation) for the evidence and [third-party notices](../THIRD_PARTY_NOTICES.md#agent-runtime-and-services) for Codex's Apache-2.0 license.

### Existing reviewers

On startup, LiteReview moves saved reviewers to Codex while preserving models, instructions, skills, tools, and limits. If a reviewer uses a direct Anthropic connection, add a Responses-compatible gateway in **Models**, select it in **PR agents → reviewer → Model**, then run **Test agent**. Keep the model ID if your gateway exposes it; otherwise use its equivalent model ID. Run a private check before requesting your next PR review.

Running sandboxes and review history retain their original configuration. A queued job using a retired harness ends with instructions to start a new review; LiteReview does not reinterpret an accepted job's settings.

## Capabilities

- **Repository**: always available. Read/search the immutable checkout and PR diff.
- **Terminal**: always available. Run tests and commands in the disposable checkout.
- **Browser**: Playwright actions, accessibility structure, and real screenshots sent to the model.
- **Computer use**: screenshots of a private 1280×900 Linux desktop, mouse and keyboard input. It shares Chromium with Browser when both are enabled. Every action returns visual feedback.
- **MCP**: reusable remote Streamable HTTP connections with per-agent tool selection.
- **Skills**: Markdown or ZIP with `SKILL.md` and supporting files. Attach the same skill to several agents. Skills do not grant tools.

Agent and group command names share one case-insensitive unique namespace. **People** and each repository’s **Review permissions** control invocation centrally. With SSO enabled, membership comes from approved Google sign-ins and verified GitHub links. People defaults to all current/future agents and groups. **Edit access** can limit a person to specific selections; an empty selection blocks reviews. **Reset access** restores the default. Repository permissions and any person restriction must allow every name in the command, and the caller must also have GitHub repository access. Without SSO, People remains a manual allowlist. Group grants permit running the group’s members through that group. Connected repositories start with All access. Restrictions follow the Google identity across GitHub renames and relinks, and persist across restarts. Linked users remain connected in People after their browser sessions expire. People shows when Google sign-in or GitHub linking is still needed; a saved grant never establishes membership by itself. Groups are optional references to shared agents. Instance counts belong to group membership. Selecting the same name twice deduplicates it; selecting two groups that share an agent runs each membership independently. Calling an agent directly and through a group runs both selections independently. Accepted runs snapshot their configuration and commit SHA.

## Connect MCP tools

Open **MCP servers** in the left sidebar and choose **Add MCP server**. Enter a name and the Streamable HTTP endpoint URL, keep **OAuth**, and choose **Save & connect**. Approve access with the provider. LiteReview then runs a real connection test and displays the discovered tools. Open a PR agent and select which tools it can use. The account’s selected tools are shared with agents using that connection.

For token-based or public servers, choose **Bearer token** or **No authentication**, then **Save & test**. **Test connection** repeats discovery and shows any failure with a retry path. A saved server or OAuth approval alone does not count as a successful connection test.

OAuth supports discovery, PKCE, automatic client registration where offered, and refresh tokens. If a provider requires a pre-registered app, open **Advanced OAuth settings**, copy the displayed redirect URI into that app, and enter its client ID and optional secret. Authorization and refresh tokens are encrypted at rest and stay in the review service. **Disconnect** removes LiteReview’s local authorization; revoke the upstream grant in the provider’s account settings if needed. **Reconnect** renews it. Cancelling sign-in preserves the server and any previous working authorization.

**MCP servers**, **Skills**, and **Models** are one click away in the sidebar while editing agents. An agent draft survives resource navigation, OAuth redirects, and reloads in the same browser tab. Choose **Resume editing** to return with the newly available tools or skills, or discard the draft. Connection credentials are never saved in browser draft storage.

## Read review results

LiteReview acknowledges an accepted command with 👀 and creates a separate progress comment for each agent instance. Every instance runs in its own Modal sandbox. As each agent finishes, its comment shows ✅ and its full response, with findings linked to inline threads where you can reply. Findings without a usable diff line stay in the agent comment. Each agent’s 👀 reaction is removed when it finishes; the request’s 👀 is removed when all agents finish. ✅ means the agent finished, not that the review is issue-free: incomplete work and skipped checks remain labeled. Reviewers stay in the order named in the command, with group members and instances in their saved order. In **Runs**, agent reports, findings, checks, screenshots, and activity start collapsed. LiteReview never pushes fixes.
