# LiteReview

Self-hosted plane for configuring PR review agents. Host a shared workspace, configure each agent’s model, instructions, skills, and tools, then call reviewers from a GitHub PR comment:

```text
@litereview
```

LiteReview acknowledges an accepted command with 👀 and creates a separate progress comment for each agent instance. Every instance runs in its own Modal sandbox. As each agent finishes, its comment shows ✅ and its full response, with findings linked to inline threads where you can reply. Findings without a usable diff line stay in the agent comment. Each agent’s 👀 reaction is removed when it finishes; the request’s 👀 is removed when all agents finish. ✅ means the agent finished, not that the review is issue-free: incomplete work and skipped checks remain labeled. Reviewers stay in the order named in the command, with group members and instances in their saved order. In **Runs**, agent reports, findings, checks, screenshots, and activity start collapsed. LiteReview never pushes fixes.

## Get started

- [Deploy a team workspace](docs/DEPLOYMENT.md): Render, Postgres, Google Workspace sign-in, and your GitHub App.
- [Develop locally](#local-development): run the UI and API with SQLite and a local access key.
- [Contribute](CONTRIBUTING.md) · [Security](SECURITY.md) · [MIT license](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

Reviews require a **Modal account**, a **model provider or gateway**, and a **publicly reachable HTTPS API URL**. GitHub PR integration requires a GitHub App that you create and install. Modal and model usage incur charges; there is no local sandbox backend. Render is the documented deployment path; Lens and BerriAI-hosted services are optional.

This beta runs **one trusted workspace per deployment**. Every admitted member can manage the workspace and use its shared connections. Run time, model-call, and concurrency limits are optional; configure them and your providers' budgets for your deployment. See the [security boundary](SECURITY.md#intended-deployment-boundary) before connecting private repositories or privileged tools.

## Configure reviewers

Comment `@litereview` alone to run the workspace's default reviewer. The first reviewer becomes the default automatically. Under **PR agents → Default reviewer**, choose a different agent at any time; the choice applies to future runs. Existing workspaces with one reviewer can use the bare command immediately. Workspaces with several reviewers choose a default once. To select specific agents or groups, name them in the comment, such as `@litereview frontend security`. Caller and repository permissions apply to both forms.

The agent’s **Review instructions** define its task, scope, workflow, language, and voice. Attached skills support those instructions; the agent instructions take precedence when they conflict. Codex supplies the agent runtime, tools, and result delivery. An agent can produce a narrative response without bug findings. Credentials, enabled tools, and configured time limits still apply.

Agents attempt relevant dependency setup using the project’s manifests and documented commands before reporting an environment blocker. Build tools, Python, Node, and uv are available; project-specific packages and toolchains can be installed in the disposable sandbox. Optional tests that were not run appear under **Checks** without making the review incomplete. A required check that could not run, or other unfinished work required by the configured task, is reported as incomplete. An executed test that fails is evidence, not automatically an incomplete review.

Set **Minimum review time (minutes)** when creating a reviewer or under **PR agents → reviewer → Review instructions**. For example, save `5` for `frontend` and `10` for `security`, then comment:

```text
@litereview frontend security
```

Every instance uses its reviewer's saved minimum, including when called through a group. The clock starts after repository/tool setup. `0` means no minimum and preserves the normal behavior for existing reviewers. Use whole minutes; LiteReview does not impose a workspace ceiling. Changing the setting affects future reviews; accepted jobs keep their saved duration. Comment time options are rejected with instructions to use the UI setting. Private harness checks do not run for this minimum.

The harness checks a monotonic clock, asks the agent to keep investigating and verifying, and rejects early completion. The API independently checks elapsed time. Codex continues the same thread if it returns before the minimum without submitting. This does not invoke the interactive `/goal` slash command. Runs have no LiteReview time or model-call limit by default. To set one, expand **Optional limits** in the agent editor and enter **Maximum run time (minutes)**, **Maximum model calls**, or both. Leave a field blank to remove its limit. Maximum run time includes setup and must exceed the minimum; a minimum never creates a maximum. Groups use each agent’s saved limits, and accepted jobs keep their snapshots. Provider failures and reached limits produce an incomplete review, never a successful early completion. [Modal imposes a 24-hour sandbox lifetime](https://modal.com/docs/guide/sandboxes#timeouts); LiteReview uses that full lifetime when no maximum is selected. Runs do not yet resume across sandbox replacements. Existing MCP selections, skills, visual tools, models, and instructions are preserved.

## Deploy to Render

Follow the [deployment guide](docs/DEPLOYMENT.md) to obtain credentials, deploy, and verify sign-in. The complete path to your first review is:

1. Fork this repository, then create a **Blueprint** in Render from your fork. `render.yaml` provisions one paid Docker web service and Postgres. An organization owner may need to approve Render’s access to your repository.
2. Set `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`, your Google OAuth client, and `GOOGLE_ALLOWED_DOMAINS` to domains you control. The Blueprint requires Google SSO. The app uses Render’s assigned HTTPS URL; set `PUBLIC_URL` for a custom domain. Keep the generated `SECRET_KEY` stable. Set `GITHUB_ORGANIZATION` to your GitHub organization, or leave it empty for account selection during setup.
3. Sign in with Google. Link GitHub to request reviews, or choose **Workspace** to configure agents and connections first. Every member has the same interface.
4. Open **Repositories → Connect GitHub** (the button names your organization when configured). Use an App owned by the account that owns your repositories. Continue to GitHub in the same tab; the guided flow registers the App, stores its credentials encrypted, and opens installation. An organization owner or organization-wide App Manager can create the App; installation may need an owner’s approval. A private personal App cannot be installed in an organization.
5. Select repositories on GitHub and return to **Repositories**. LiteReview imports the selection automatically; installation IDs and a second repository picker are unnecessary. Public and private repositories are supported. New selections allow existing approved People to call their permitted reviewers. Open a repository’s **Review permissions** to narrow that access.
6. If no model connection exists, add one under **Models**. Reuse an existing Responses-compatible gateway or OpenAI connection where possible. **Configure reviewer** asks for its name and model, then **Save and check reviewer** runs a private harness check using Modal and model credits. Every reviewer uses Codex; Anthropic models require a Responses-compatible gateway. Full instructions, capabilities, skills, and MCP tools remain in the agent editor.
7. Each teammate chooses **Sign in with Google → Connect GitHub**. They appear in **People** automatically and can use all reviewers by default. Use **Edit access** for optional restrictions; no duplicate allowlist is required. The start page shows commands only for permitted repositories and checked reviewers, after verifying current GitHub access. Post the command in a PR; successful setup ends with an actual review result. Provider consent can require additional clicks.

## Resume GitHub setup

**Repositories** checks the App and syncs repository selection when opened or when you return from GitHub. Signed installation webhooks also sync selection. Use **GitHub connection options → Sync repositories** to retry. Failed checks preserve configuration. If GitHub approval is pending, copy the setup request for an organization owner and resume afterward.

A linked GitHub account lets LiteReview recognize your PR comments. It does not establish repository access. **Account** and **Repositories** show identity and repository access separately. When no repositories are connected, workspace pages show **Connect GitHub**, and the start page links directly to setup.

A saved App also does not establish repository access. If it belongs to the wrong account, Repositories identifies that owner and offers the appropriate organization setup. **Copy setup request** includes instructions for an organization owner to assign the organization-wide App Manager role on supported GitHub plans, or create the App themselves. App management and installation are separate permissions. The target organization comes from saved setup or `GITHUB_ORGANIZATION`; it is never inferred from the Google email domain.

To install LiteReview yourself, you need Admin access on the target repositories and the organization’s **Settings → Member privileges → GitHub Apps → Allow repository admins to install GitHub Apps for their repositories** setting enabled. This policy applies to all repository admins. An owner can instead keep installations restricted and select repositories for you. Once installed, use **Add repositories** to update the existing App’s access; no new App is needed. LiteReview’s People and repository grants still restrict who can request reviews.

**Settings → GitHub App** contains credential recovery and **Reset setup**. A reset pauses GitHub reviews while preserving agents, models, MCP connections, skills, repository environments and secrets, access rules, and review history. The App remains on GitHub. **Undo reset** restores the prior connection; saving a new App replaces that backup. Active reviews must finish before resetting.

Sync preserves existing environments and permissions. Removing a repository in LiteReview excludes it from future automatic imports; restore it explicitly under **Previously removed repositories**. Removing or restricting an access grant is never undone by sync. Removing GitHub access leaves the saved environment visible as unavailable. After an App replacement, sync updates verified installation IDs without changing grants.

Each browser stays signed in independently. Signing in or out in another browser does not close your session. When your session expires, **Sign in to continue** returns you to the page you opened, including a specific run. The GitHub identity remains linked. A failed GitHub check offers retry instead of asking you to rebuild setup.

## Google Workspace SSO

Use an **Internal** Google OAuth web client for your Google Workspace organization. Register `https://YOUR-LITEREVIEW-HOST/api/auth/google/callback` as an authorized redirect URI, replacing the origin with your deployment's actual `PUBLIC_URL`. If reusing a client, keep its other redirect URIs. See the [credential setup steps](docs/DEPLOYMENT.md#prepare-your-accounts).

In Render’s LiteReview environment settings, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ALLOWED_DOMAINS` to your domain (for example, `example.com`), and `GOOGLE_SSO_REQUIRED=true`, then deploy. Register the callback before enabling SSO on an existing workspace: `redirect_uri_mismatch` means the URI is missing from that Google client. Never commit the client secret.

Verify that a workspace account can sign in, an outside account is denied, and the old workspace access key no longer works. Enabling SSO also invalidates existing access-key/GitHub-only sessions. Every admitted Google member can manage agents, groups, skills, shared model/MCP connections, repositories, review access settings, and run history. There is no separate LiteReview administrator role. Shared connections use their connected accounts’ permissions and shared model credits. `GOOGLE_ADMIN_EMAILS` is no longer used; `ADMIN_USERS` only controls legacy GitHub-only sign-in when SSO is disabled. GitHub still controls App installation permissions. Approved Google users appear in **People** automatically. Linked users can call all reviewers unless an explicit person restriction or repository permission disallows them. Existing restrictions are preserved.

The server verifies the Google token signature, issuer, audience, expiry, nonce, verified email, and hosted-domain claim. PKCE and single-use, browser-bound state protect login. Linking verifies GitHub’s stable user ID through OAuth; a typed GitHub username cannot establish membership. Once linked, that GitHub account can request reviews without signing in again. Browser sessions expire after `SSO_SESSION_SECONDS` (12 hours by default); expiry and signing out do not revoke the GitHub link. Every request still checks the linked identity against the configured Google client and allowed domains, GitHub repository permission, and any explicit per-person restrictions. To stop a person from requesting reviews, use **People → Edit access** and leave the selection empty. Unlinking GitHub also stops requests from that account. There is no Google Directory sync: suspending a Google account prevents future Google sign-ins but does not automatically revoke its existing GitHub review access. Signed GitHub webhooks and expiring per-job worker tokens remain the machine entry points.

Local development can use `ADMIN_TOKEN` only when no Google client is configured and `GOOGLE_SSO_REQUIRED=false`. A required but incomplete SSO configuration denies access rather than falling back to a workspace key.

Modal images and sandboxes are created on demand using your credentials. The first review builds the image and can take several minutes; subsequent reviews reuse it. No separate Modal app deployment, workflow service, Redis, or storage bucket is required.

## Codex runtime

Every reviewer runs Codex 0.158.0 in its own Modal sandbox. Choose the model, instructions, skills, and tools; LiteReview installs and configures Codex automatically.

Use an OpenAI connection or a gateway that supports the Responses API. A gateway can route compatible requests to other providers, including Anthropic. Direct Anthropic connections cannot run reviewers. Browser and desktop checks require image-capable models. Under **PR agents**, select your reviewer and choose **Test agent** to check Codex, the model, sample skill loading, and enabled terminal/browser/desktop tools on a sample project. This private check consumes Modal and model credits; it does not access a GitHub repository, post comments, execute your review instructions, or call attached MCP tools.

Every reviewer includes terminal access to inspect files, install dependencies, run tests, and create reproducers in its disposable checkout. Instructions and skills can request Codex subagents; the main reviewer combines their evidence into one result. Browser, computer use, attached skills, and selected MCP tools remain configurable. The checkout contains the supplied PR commit's source archive, without Git history. LiteReview never pushes changes.

Under **PR agents → reviewer → Model**, **Reasoning effort** can use the model default or request Low, Medium, High, or Extra high. Supported levels depend on your model and gateway. Higher effort can increase review time and cost. Run **Test agent** after changing it; that check uses the selected effort. Existing reviewers keep their model default until you choose a level.

### Combine independent reviews with Fusion

Open **PR agents → reviewer** and change **Review mode** to **Fusion**. Select each branch's connection, model, and optional reasoning effort. Use **Add branch** for more independent reviews, up to 16. An optional focus gives a branch a perspective such as configuration contracts or state and lifecycle bugs. All branches use the reviewer's instructions, skills, and enabled tools.

Choose the **Aggregator model** below the branches, then save and run **Test agent**. The test exercises every stage on a private sample project. For a PR review, branches run in separate checkouts; the aggregator gets a fresh checkout and their reports, verifies the claims, and publishes one final review. In **Runs**, expand **Fusion** to inspect each branch's result and activity. Branch results start collapsed. If a branch fails, its incomplete coverage remains in the final result. If every branch fails, aggregation does not run.

A fusion review costs the sum of every branch and the aggregator. Maximum run time and model-call limits apply separately to each stage; minimum review time applies to each branch. The workspace concurrency setting can queue branches when capacity is full. Choose this reviewer as **Default reviewer** to invoke the whole fusion with `@litereview`.

For an independent bug review, copy the [example review instructions](examples/reviewers/independent-bugs.md) into the reviewer's instructions. The example traces callers and consumers, checks boundary cases and repeated operations, and verifies whether tests exercise their intended behavior. Leave external review-service MCP tools unselected when evaluating it against another reviewer. Measure quality and cost on your own PRs before making it a required check.

The full PR context is stored in a sandbox file, and the diff remains available through review tools. Large diffs do not travel as command-line arguments or oversized initial prompts. Follow-up turns and timed continuations keep the same Codex session and cache key. Cache hits and savings depend on the selected gateway and provider; independent reviewers have separate sessions.

The runtime version is pinned in `runner/catalog.py` and recorded with every job. See [runtime validation](docs/VALIDATION.md#codex-runtime-validation) for the evidence and [third-party notices](THIRD_PARTY_NOTICES.md#agent-runtime-and-services) for Codex's Apache-2.0 license.

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

## Local development

Install [Python 3.12](https://www.python.org/downloads/), [Node 22](https://nodejs.org/), and [uv 0.10.9](https://docs.astral.sh/uv/getting-started/installation/). Clone the repository and install dependencies:

```sh
git clone https://github.com/BerriAI/litereview.git
cd litereview
uv sync --frozen --python 3.12
npm ci --prefix frontend
npm run build --prefix frontend
```

In a new checkout, create a local configuration with independent random keys. This command refuses to overwrite an existing `.env`:

```sh
uv run python - <<'PY'
from pathlib import Path
import secrets

text = Path('.env.example').read_text()
for name in ('SECRET_KEY', 'ADMIN_TOKEN'):
    text = text.replace(f'{name}=\n', f'{name}={secrets.token_urlsafe(32)}\n')
with Path('.env').open('x') as output:
    output.write(text)
Path('.env').chmod(0o600)
PY
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open `http://localhost:8000` and sign in with the generated `ADMIN_TOKEN` from `.env` in **Workspace access key**. In another terminal, `curl --fail http://localhost:8000/health` should return `{"status":"ok"}`. This starts the workspace; live reviews still need the external accounts listed above.

SQLite is supported for local development. Use Postgres in deployment. After creating `.env`, `docker compose up --build` runs both services locally. Use one API worker and one service instance in v1.

Modal must reach the API over HTTPS. For local live testing, expose port 8000 through a temporary HTTPS tunnel and set `PUBLIC_URL` to its address before starting the app. All configuration, results, and screenshots require sign-in; job relay endpoints require expiring per-job tokens. `PUBLIC_URL` must also match the origin you use in your browser.

For UI iteration, `npm run dev --prefix frontend` proxies API calls to port 8000. Set `PUBLIC_URL=http://localhost:5173` while doing local UI-only work.

Run checks:

```sh
uv run ruff check app runner tests
uv run pytest -q
npm run build --prefix frontend
```

`uv run python -m scripts.modal_smoke` exercises browser and desktop tools in a real Modal sandbox and consumes Modal credits. Live review tests additionally consume model credits.

For the full LiteReview app check, run `uv run python -m scripts.modal_app_smoke --repo YOUR-OWNER/YOUR-LITEREVIEW-FORK --pr YOUR-PR-NUMBER` against a PR in your LiteReview fork. Replace both placeholders with a repository and trusted PR you control. This downloads its pinned source, verifies every extracted file, installs dependencies, builds and starts the application, exercises browser/desktop tools and internet access, and asks the configured gateway model to read a screenshot. It requires authenticated `gh` and local Modal/gateway credentials; evidence stays in ignored `.test-artifacts/` files. It executes the selected PR's code and consumes Modal/model credits.

## Existing GitHub App

Configure **Repositories → Use existing App** (or **Settings → GitHub App → App credentials** if an App is saved), or set:

- `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY` (PEM, escaped newlines supported), and `GITHUB_WEBHOOK_SECRET`.
- `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` for GitHub sign-in.
- Callback URL: `PUBLIC_URL/api/auth/github/callback`.
- Webhook URL: `PUBLIC_URL/api/webhook/github`; subscribe to **Issue comment** events.
- Repository permissions: **Contents read**, **Metadata read**, **Pull requests write**, **Issues write**. Issues write permits top-level PR conversation comments.

With Google SSO enabled, GitHub OAuth links a caller’s account after Google sign-in and cannot sign in on its own. In local access-key mode, only `ADMIN_USERS` can sign in with GitHub, and `ADMIN_TOKEN` remains available.

## Runtime and boundaries

### Optional tracing with Lens

LiteReview can send review traces to [Lens](https://github.com/BerriAI/lens) or an OTLP/HTTP collector.
Tracing is off by default. You can run LiteReview without Lens or any Berri-hosted service.

1. Deploy Lens and sign in. Open **Tracing key** and generate a key for LiteReview.
2. Set these variables in the LiteReview API environment, then restart or redeploy it:

   ```dotenv
   LITEREVIEW_TRACE_ENDPOINT=https://YOUR-LENS-HOST/v1/traces
   LITEREVIEW_TRACE_API_KEY=YOUR-LENS-TRACING-KEY
   LITEREVIEW_TRACE_CONTENT=false
   ```

   Use the full OTLP/HTTP endpoint, including `/v1/traces`. The API server must be able to reach it.
   Use a tracing key, not the Lens admin token. For a collector without authentication, leave the key empty.
3. Run a review or use **Test agent** in the agent editor. In Lens, open **Traces** for that reviewer.
   Each agent's review appears as one run. Open it and select **Thread** to read its messages,
   tool calls, tool results, and final review when content capture is enabled. **Steps** shows
   the individual model calls under the review. Agents in a group have separate runs.

Traces include the reviewer, harness, model, job and run IDs, duration, status, and token counts when the
provider supplies them. To capture the conversation, set `LITEREVIEW_TRACE_CONTENT=true`.
This exports full prompts, responses, tool arguments, tool results sent back to the model, and the
submitted review. It can include private source code and command output. Images, encrypted reasoning,
request headers, and stored credentials are not captured. Secrets inside conversation text or tool
arguments and results are not redacted.

Codex model requests export through the API. The collector key never enters a Modal sandbox.
Exports run in the background without a queue-size cap. Failed exports are logged; reviews continue.
An API restart can lose pending exports; later calls still join the same review. The parent span and
final review appear when the run finishes. Unfinished required checks mark the review incomplete.
Previously recorded traces keep their original structure; content omitted from old exports cannot be recovered.

If traces do not appear, check the endpoint and tracing key, then check the API logs for export errors.
Keep `/v1/traces` in the endpoint. To turn tracing off, clear `LITEREVIEW_TRACE_ENDPOINT` and redeploy.

### Review execution

The FastAPI service serves the built React UI and runs a durable Postgres-backed dispatcher. Each job runs Codex in a separate Modal sandbox with Python 3.12, Node 22, Chromium, Playwright, and a Linux desktop. LLM inference happens at the configured provider.

The checkout is a GitHub source archive at the PR's immutable head SHA, rather than a full Git clone. It does not include `.git` history, materialize submodules or Git LFS objects, or preserve symlinks. Repositories that depend on those features need additional support. Executable reviews use the repository's configured setup command, start command, and readiness URL; external services, private package registries, and test accounts still need their own configuration. Modal sandboxes have outbound internet access by default, including package installation and browser navigation. This does not grant access to private networks or credentials.

Long-lived model and MCP keys are encrypted in the database using `SECRET_KEY`. They stay in the API service. Sandboxes use job-scoped tokens to call the configured model/MCP relay. Tokens remain valid only while the job is active; an explicitly selected timeout also expires access after a short result-reporting grace period. GitHub credentials never enter the checkout. Optional repository test secrets are deliberately made available to the sandbox; use test credentials only.

Repository downloads and extraction have no LiteReview size or file-count limit. Downloads stream through the API into a temporary file in the sandbox, and extraction copies file contents in chunks. Available sandbox disk space and upstream service limits still apply. LiteReview also accepts PRs without a changed-file or diff-size cap; the full diff is stored and available to review tools, with optional ranges chosen by the agent.

Screenshots and activity logs are private Postgres records with no LiteReview size or count cap. Skill uploads, instructions, tool results, review findings, and summaries are also retained without application size caps or truncation.

File, search, shell, browser, and MCP tools return their full results unless the agent requests a range. Model, MCP, download, setup, and tool requests have no LiteReview deadline unless one is explicitly selected for the run or command. Codex, models, and upstream services still enforce their own context capacities.

Sandboxes launch on demand across reviews by default. Set `CONCURRENCY` to a positive number for an optional workspace-wide cap; `0` leaves concurrency to Modal account quotas and provider rate limits. Each sandbox requests 2 CPU cores and 4 GiB of memory. Time and model-call budgets are optional per-agent settings. Legacy `JOB_TIMEOUT`, `MAX_ROUNDS`, and `MAX_REVIEW_MINUTES` environment values no longer impose defaults. Existing reviewers have no minimum by default. Keep Postgres backups enabled; deleting the database loses settings and run history.

GitHub retries are deduplicated by delivery ID. Pending jobs survive API restarts; completed jobs are never rerun automatically. Sandboxes are terminated on completion. The dispatcher reattaches to recorded active sandboxes after API restarts and reports an incomplete result if a sandbox exits without a result. Modal’s 24-hour lifetime still applies. Publication retries recover each agent’s marked comments and finding threads after a lost response. A shared lock allows only one publisher per run during deployment overlap. Existing aggregate progress comments link to the agent results when their runs finish; already published runs remain unchanged. Results always name the reviewed SHA; later pushes are called out.

Reviews longer than GitHub's 65,536-character comment limit are published across multiple comments without truncating the review. Retries recover every already-published part. Each agent comment links to its inline finding threads where a usable diff line exists. It has no automatic fixes, push triggers, fusion/judge agent, persistent chat, local stdio MCP servers, or plugin marketplace. See [SPEC.md](SPEC.md) for the product decisions and [docs/VALIDATION.md](docs/VALIDATION.md) for actual test evidence and external setup boundaries.
