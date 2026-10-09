# LiteReview

A private workspace for reusable PR reviewers. Configure each agent’s harness, model, instructions, skills, and tools. Call agents directly or use optional groups to bundle them, then comment on a PR:

```text
@litereview frontend pentest security
```

Every instance runs in a separate Modal sandbox. Results appear in one GitHub comment, with findings, **No findings**, or an explicit incomplete result for each agent. LiteReview never pushes fixes.

Add a minimum review duration after each agent or group name:

```text
@litereview frontend t=5 security t=10
```

`t=5` means at least five minutes of review after repository/tool setup. For a group, every instance gets its own five-minute minimum. `t=5m` and `light review frontend t=5` are also accepted. Names without `t=` keep the usual behavior. Durations must be whole minutes from 1 to `MAX_REVIEW_MINUTES` (120 by default); conflicting durations for a repeated name reject the command.

The harness checks a monotonic clock, asks the agent to keep investigating and verifying, and rejects early completion. The API independently checks elapsed time. Codex continues the same thread if it returns without submitting; the other harnesses retain their sessions too. This does not invoke the interactive `/goal` slash command. Time and model-call limits still apply: provider failures, cancellation, and exhausted limits produce an incomplete review, never a successful early completion. Timed jobs add the requested minimum to `JOB_TIMEOUT` and add one `MAX_ROUNDS` budget per started ten minutes. Existing MCP selections, skills, capabilities, models, and instructions are preserved.

The private workspace is deployed at [litereview.onrender.com](https://litereview.onrender.com). See [deployment status and GitHub setup](docs/DEPLOYMENT.md) for the remaining organization setup.

## Deploy to Render

1. Create a **Blueprint** in Render from this repository. `render.yaml` provisions one Docker web service and Postgres. If Render cannot see the BerriAI repo, an organization owner may need to approve Render’s GitHub access.
2. Set `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`. The app automatically uses Render’s assigned HTTPS URL; set `PUBLIC_URL` explicitly if using a custom domain. Keep the generated `SECRET_KEY` stable across deploys. Configure Google sign-in as described below, with at least one administrator email.
3. Open the app, choose **Sign in with Google**, then **Open workspace**.
4. Under **Connections → GitHub**, reuse the App if it already belongs to the right account. Otherwise choose **Connect GitHub organization** (or **Connect an organization instead** for an existing personal App). Choose the account or organization that owns the target repositories. GitHub registers a private App there, returns its credentials to LiteReview, and opens installation. A private App can only be installed on its owning account. For BerriAI repositories, an organization owner may need to create/install the App; they can also create it manually and provide the App credentials. You can instead use an existing App or environment variables; see below.
5. Return to **Connections → GitHub**. Access is checked automatically; choose **Connect** beside a discovered repository. No installation ID is needed. Use **Check again** if installation is still awaiting approval. For browser/desktop reviews, configure the setup command, start command, and local readiness URL.
6. Under **Connections → Models**, add your gateway or a direct OpenAI/Anthropic key. Gateway base URLs normally end in `/v1`. Test the connection to discover models. No LiteLLM Gateway is required. For an environment-only first setup, optionally provide `GATEWAY_URL` and `GATEWAY_API_KEY` (or `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) before the first start. These seed an encrypted connection; manage subsequent model connection changes in the UI.
7. Create a PR agent, select a harness and model, and use **Test agent** to check them in a disposable Modal sandbox. The editor defaults new agents to Codex. In **Access**, add the GitHub accounts allowed to call it and choose which agents/groups each person and repository can use. Each caller signs in with Google and chooses **Link GitHub account** on **Your LiteReview account**, then comments `@litereview your-agent t=5` on an open PR. Optionally bundle agents into groups, as reusable shortcuts. Mix agent and group names in the same command.


## Resume GitHub setup

To start over, open **Connections → GitHub** and choose **Reset**. GitHub reviews pause, and setup returns to the first step. Your agents, model and MCP connections, skills, repository settings and secrets, access rules, and review history stay saved. The App and its installations remain on GitHub. Choose **Undo reset** to restore the previous connection, even after refreshing the page. Saving a new App replaces that backup. Active PR reviews must finish before you reset.

**Connections → GitHub** verifies the saved App, shows its owning account, and lists the repositories it can currently access. “Setup needed” means the App has no connected repositories; saving App credentials alone is not a working integration. Returning from GitHub refreshes the check automatically, and **Check again** retries without discarding saved work.

A private App owned by a personal account cannot be installed in BerriAI. Choose **Connect an organization instead** and have an organization owner complete App creation/installation if your account lacks permission. GitHub opens in a separate tab, so an unsuccessful attempt leaves your workspace available. Failed or expired callbacks return to setup with recovery instructions.

Choose **Connect** beside an available repository. LiteReview verifies access and fills in its installation ID. If you replace the App, **Reconnect** updates the installation while retaining repository commands, secrets, and access restrictions. Connecting a new repository grants its existing default All access; narrow it in **Access** when needed. Connection retries never restore revoked grants.

## Google Workspace SSO

Use an **Internal** Google OAuth web client for your organization. You can reuse an existing client by adding `https://litereview.onrender.com/api/auth/google/callback` to its authorized redirect URIs; keep its other redirect URIs. A new client uses the same exact URI. For another deployment, replace the origin with `PUBLIC_URL`.

In Render’s LiteReview environment settings, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ALLOWED_DOMAINS=berri.ai`, `GOOGLE_ADMIN_EMAILS` to the comma-separated administrator emails, and `GOOGLE_SSO_REQUIRED=true`, then deploy. Register the callback before enabling SSO on an existing workspace: `redirect_uri_mismatch` means the URI is missing from that Google client. Never commit the client secret.

Verify that a workspace account can sign in, an outside account is denied, and the old workspace access key no longer works. Enabling SSO also invalidates existing access-key/GitHub-only sessions. Members can link their own GitHub account; administrators can manage configuration. Verified linked GitHub administrators in `ADMIN_USERS` also retain management access. Google sign-in does not add or broaden existing People/Repository grants.

The server verifies the Google token signature, issuer, audience, expiry, nonce, verified email, and hosted-domain claim. PKCE and single-use, browser-bound state protect login. Linking verifies GitHub’s stable user ID through OAuth; a typed GitHub username cannot establish membership. GitHub comments require a linked identity with current Google authorization in addition to the existing grants and repository permission. Sessions expire after `SSO_SESSION_SECONDS` (12 hours by default); sign in again to renew commenting authorization. Signing out revokes it immediately. Google account suspension is recognized at the next sign-in or session expiry; there is no Directory sync. Signed GitHub webhooks and expiring per-job worker tokens remain the machine entry points.

Local development can use `ADMIN_TOKEN` only when no Google client is configured and `GOOGLE_SSO_REQUIRED=false`. A required but incomplete SSO configuration denies access rather than falling back to a workspace key.

Modal images and sandboxes are created on demand using your credentials. The first review builds the image and can take several minutes; subsequent reviews reuse it. No separate Modal app deployment, workflow service, Redis, or storage bucket is required.

## Harnesses

Choose **Built-in**, **Pi**, **OpenCode V1**, **OpenCode V2**, **Codex**, **Claude Code**, or **Deep Agents** alongside the model in each agent's editor. Existing agents keep Built-in. Each sandbox installs and launches only the selected harness; there is no pool of idle harnesses.

Terminal uses the selected harness's native command tool. Browser, computer use, repository tools, attached skills, and selected MCP tools are connected to that harness by LiteReview. Code-only agents are not offered a command tool. Native file editing and delegation tools are disabled; commands may create disposable test/build artifacts. Nothing pushes changes to GitHub.

Codex needs a Responses-compatible endpoint. Claude Code needs a Messages-compatible endpoint. A gateway can translate these protocols to compatible models, including Claude with Codex. Pi, OpenCode, and Deep Agents use Chat Completions for gateway/OpenAI connections and Messages for direct Anthropic connections. Browser and desktop checks require image-capable models. Model discovery alone does not establish compatibility: **Test agent** runs the actual selected harness against a sample project, without accessing a GitHub repo or posting comments. It checks sample skill loading and the enabled terminal/browser/desktop tools; it does not execute your review instructions or attached MCP tools. You can stop a test from the editor.

Versions are pinned in `runner/catalog.py` and captured with every job. Runtime updates are application releases. See [harness validation](docs/VALIDATION.md#selectable-harness-validation) for the tested versions and combinations.

## Capabilities

- **Repository**: always available. Read/search the immutable checkout and PR diff.
- **Terminal**: run tests and commands in the disposable checkout.
- **Browser**: Playwright actions, accessibility structure, and real screenshots sent to the model.
- **Computer use**: screenshots of a private 1280×900 Linux desktop, mouse and keyboard input. It shares Chromium with Browser when both are enabled. Every action returns visual feedback.
- **MCP**: reusable remote Streamable HTTP connections with per-agent tool selection.
- **Skills**: Markdown or ZIP with `SKILL.md` and supporting files. Attach the same skill to several agents. Skills do not grant tools.

Agent and group command names share one case-insensitive unique namespace. **Access → People / Repositories** controls invocation centrally. Each entry grants all current/future agents and groups or specific selections. Both grants must allow every name in the command, and the caller must also have GitHub repository access. Group grants permit running the group’s members through that group. Connected repositories start with All access; workspace administrators are added to People on first setup. Revoking access persists across restarts. Groups are optional references to shared agents. Instance counts belong to group membership. Selecting the same name twice deduplicates it; selecting two groups that share an agent runs each membership independently. Calling an agent directly and through a group runs both selections independently. Accepted runs snapshot their configuration and commit SHA.

## Local development

Install Python 3.12, Node 22, and uv. Copy `.env.example` to `.env`, generate `SECRET_KEY` and `ADMIN_TOKEN`, then:

```sh
uv sync --frozen
npm ci --prefix frontend
npm run build --prefix frontend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

SQLite is supported for local development. Use Postgres in deployment. `docker compose up --build` runs both services locally. Use one API worker and one service instance in v1.

Modal must reach the API over HTTPS. For local live testing, expose port 8000 through a temporary HTTPS tunnel and set `PUBLIC_URL` to its address before starting the app. All configuration, results, and screenshots require sign-in; job relay endpoints require expiring per-job tokens. `PUBLIC_URL` must also match the origin you use in your browser.

For UI iteration, `npm run dev --prefix frontend` proxies API calls to port 8000. Set `PUBLIC_URL=http://localhost:5173` while doing local UI-only work.

Run checks:

```sh
uv run ruff check app runner tests
uv run pytest -q
npm run build --prefix frontend
```

`uv run python -m scripts.modal_smoke` exercises browser and desktop tools in a real Modal sandbox and consumes Modal credits. Live review tests additionally consume model credits.

For the full LiteReview app check, run `uv run python -m scripts.modal_app_smoke --repo BerriAI/litereview --pr 1` against the trusted test PR. This downloads its pinned source, verifies every extracted file, installs dependencies, builds and starts the application, exercises browser/desktop tools and internet access, and asks the configured gateway model to read a screenshot. It requires authenticated `gh` and local Modal/gateway credentials; evidence stays in ignored `.test-artifacts/` files. It executes the selected PR's code and consumes Modal/model credits.

## Existing GitHub App

Configure **Connections → GitHub → Use existing App** (or **App settings and recovery → Update App credentials** if an App is saved), or set:

- `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY` (PEM, escaped newlines supported), and `GITHUB_WEBHOOK_SECRET`.
- `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` for GitHub sign-in.
- Callback URL: `PUBLIC_URL/api/auth/github/callback`.
- Webhook URL: `PUBLIC_URL/api/webhook/github`; subscribe to **Issue comment** events.
- Repository permissions: **Contents read**, **Metadata read**, **Pull requests write**, **Issues write**. Issues write permits top-level PR conversation comments.

With Google SSO enabled, GitHub OAuth links a caller’s account after Google sign-in and cannot sign in on its own. In local access-key mode, only `ADMIN_USERS` can sign in with GitHub, and `ADMIN_TOKEN` remains available.

## Runtime and boundaries

The FastAPI service serves the built React UI and runs a durable Postgres-backed dispatcher. Each job runs its selected harness in a separate Modal sandbox with Python 3.12, Node 22, Chromium, Playwright, and a Linux desktop. LLM inference happens at the configured provider.

The checkout is a GitHub source archive at the PR's immutable head SHA, rather than a full Git clone. It does not include `.git` history, materialize submodules or Git LFS objects, or preserve symlinks. Repositories that depend on those features need additional support. Executable reviews use the repository's configured setup command, start command, and readiness URL; external services, private package registries, and test accounts still need their own configuration. Modal sandboxes have outbound internet access by default, including package installation and browser navigation. This does not grant access to private networks or credentials.

Long-lived model and MCP keys are encrypted in the database using `SECRET_KEY`. They stay in the API service. Sandboxes use bounded, expiring job tokens to call the configured model/MCP relay. GitHub credentials never enter the checkout. Optional repository test secrets are deliberately made available to the sandbox; use test credentials only.

For a small v1, screenshots are private, bounded Postgres artifacts rather than a separate storage service. Each job stores at most 20 PNGs, 2 MB each. Logs are bounded to 200 events. Repository archives are limited to 50 MB compressed / 250 MB expanded; PRs to 300 changed files / a 2 MB diff. Sandboxes launch on demand across reviews by default. Set `CONCURRENCY` to a positive number for an optional workspace-wide cap; `0` leaves concurrency to Modal account quotas and provider rate limits. Each sandbox requests 2 CPU cores and 4 GiB of memory. Defaults are ten minutes per agent and 60 model calls, configurable through environment variables. Keep Postgres backups enabled; deleting the database loses settings and run history.

GitHub retries are deduplicated by delivery ID. Pending jobs survive API restarts; completed jobs are never rerun automatically. Sandboxes have hard timeouts and are terminated on normal completion. A lost worker callback becomes an incomplete result. Publication retries update the original marked comment. Results always name the reviewed SHA; later pushes are called out.

This v1 posts summary comments, not resolvable inline review threads. It has no automatic fixes, push triggers, fusion/judge agent, persistent chat, local stdio MCP servers, or plugin marketplace. See [SPEC.md](SPEC.md) for the product decisions and [docs/VALIDATION.md](docs/VALIDATION.md) for actual test evidence and external setup boundaries.
